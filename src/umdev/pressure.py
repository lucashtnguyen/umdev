"""Compile traceable pressure-connection settings without inventing node profiles.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained; canonical engineering lengths are metres.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Mapping, Sequence

from hymo import SWMMDocument, interpret_fields
from shmm import Change

from .units import from_metres, to_metres


def pressure_contract_reason(pipe: Mapping[str, Any], document: SWMMDocument) -> str | None:
    """Require explicit force-main friction parameters compatible with model options.

    Notes
    -----
    H-W C is dimensionless; D-W absolute roughness is stored in metres until
    conversion into SWMM rain-depth units (inches US, millimetres SI).
    The CONDUITS Manning field remains separately positive; SWMM determines
    the force-main friction calculation internally. Options are never rewritten.
    """
    assignment = pipe.get("pressure_assignment", {})
    options = {row.tokens[0].upper(): row.tokens[1].upper() for row in document.records("OPTIONS")}
    equation = assignment.get("equation")
    coefficient = assignment.get("value")
    if (pipe.get("shape") != "FORCE_MAIN" or equation not in {"H-W", "D-W"}
            or not isinstance(coefficient, (int, float)) or isinstance(coefficient, bool)
            or not math.isfinite(coefficient) or coefficient <= 0
            or assignment.get("unit") != ("dimensionless" if equation == "H-W" else "m")
            or not assignment.get("basis")):
        return "unsupported_pressure_or_parameter_contract"
    if options.get("FLOW_ROUTING", "DYNWAVE") != "DYNWAVE":
        return "force_main_requires_dynamic_wave"
    if options.get("FORCE_MAIN_EQUATION", "H-W") != equation:
        return "force_main_equation_mismatch"
    return None


def baseline_pressure_plan(document: SWMMDocument, config: Mapping[str, Any], *,
                           added_connections: Sequence[Mapping[str, str]] = ()) -> tuple[list[Change], dict[str, Any], list[dict[str, Any]]]:
    """Apply an enabled internal-pressure policy to existing force-main junctions.

    Returns
    -------
    changes, evidence, review : tuple
        SHMM instructions with old-value preconditions, source-keyed evidence,
        and all force-main endpoint classifications. Wet wells and receiving
        gravity junctions remain unchanged. A terminal with no established
        receiving connection remains unresolved.

    Notes
    -----
    Automatic sealing requires an incoming force main or pump, an outgoing
    force main, and no gravity/regulator connections. This avoids treating a
    discharge manhole or storage wet well as a sealed intermediate fitting.
    The large configured surcharge allowance is a test assumption, not a
    pressure rating or evidence that the cover is physically bolted.
    """
    policy = config.get("parameters", {}).get("nodes", {}).get("pressure_connections", {})
    if not policy:
        return [], {}, []
    setting = policy["surcharge_depth"]
    requested_m = to_metres(setting["value"], setting["unit"])
    unit = config["model"]["target"]["length_unit"]
    shapes = {row.tokens[0].upper(): row.tokens[1].upper() for row in document.records("XSECTIONS")}
    junctions = {row.tokens[0].upper(): row for row in document.records("JUNCTIONS")}
    storage = {row.tokens[0].upper() for row in document.records("STORAGE")}
    incident: dict[str, list[dict[str, str]]] = defaultdict(list)
    pressure_nodes = set()
    for section in ("CONDUITS", "PUMPS", "WEIRS", "ORIFICES", "OUTLETS"):
        for row in document.records(section):
            kind = "pump" if section == "PUMPS" else "force_main" if section == "CONDUITS" and shapes.get(row.tokens[0].upper()) == "FORCE_MAIN" else "gravity" if section == "CONDUITS" else "other"
            for role, identity in (("outgoing", row.tokens[1]), ("incoming", row.tokens[2])):
                incident[identity.upper()].append({"link": row.tokens[0], "kind": kind, "end": role})
                if kind == "force_main":
                    pressure_nodes.add(identity.upper())
    # Classify the final planned network, including accepted additions. A new
    # gravity connection may invalidate an otherwise internal pressure-only node.
    for connection in added_connections:
        kind = "force_main" if connection["pipe_kind"] == "force_main" else "gravity"
        for role, identity in (("outgoing", connection["from_node"]), ("incoming", connection["to_node"])):
            incident[identity.upper()].append({"link": connection["model_id"], "kind": kind, "end": role})
    overrides = {key.upper(): role for key, role in policy.get("model_node_overrides", {}).items()}
    if len(overrides) != len(policy.get("model_node_overrides", {})):
        raise ValueError("Case-insensitive pressure model-node override collision")
    namespace = {row.tokens[0].upper() for section in ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS") for row in document.records(section)}
    if set(overrides) - namespace:
        raise ValueError("Pressure role override names an absent model node")
    if any(role == "sealed" and identity not in junctions or role == "wet_well" and identity not in storage for identity, role in overrides.items()):
        raise ValueError("Pressure model-node role conflicts with the baseline node section")
    changes, evidence, review = [], {}, []
    for identity in sorted(pressure_nodes | set(overrides)):
        connections = incident[identity]
        kinds = {row["kind"] for row in connections}
        incoming = any(row["end"] == "incoming" and row["kind"] in {"force_main", "pump"} for row in connections)
        outgoing = any(row["end"] == "outgoing" and row["kind"] == "force_main" for row in connections)
        role = "wet_well" if identity in storage else "sealed" if incoming and outgoing and kinds <= {"force_main", "pump"} else "open_discharge" if not outgoing and any(row["end"] == "incoming" and row["kind"] == "force_main" for row in connections) and any(row["end"] == "outgoing" and row["kind"] == "gravity" for row in connections) else "unresolved"
        method = "baseline_internal_pressure_connectivity"
        if policy.get("classification") != "internal_force_main_only" and role == "sealed":
            role = "unresolved"
        if identity in overrides:
            role, method = overrides[identity], "explicit_model_node_role"
        row = junctions.get(identity)
        current = float(interpret_fields("JUNCTIONS", row)["surcharge_depth"]) if row else None
        expected = from_metres(requested_m, unit) if role == "sealed" and row else current
        detail = {"model_id": row.tokens[0] if row else identity, "role": role,
                  "method": method, "incident_links": connections,
                  "previous_surcharge_depth": current, "expected_surcharge_depth": expected,
                  "unit": unit, "basis": policy["basis"], "changed": current != expected}
        review.append(detail)
        if row and role == "sealed" and current != expected:
            source_key = "pressure/baseline/" + row.tokens[0]
            changes.append(Change("update", "JUNCTIONS", row.tokens[0], {"surcharge_depth": expected}, source_key, {"surcharge_depth": current}))
            evidence[source_key] = detail
    allowed = config.get("qa", {}).get("allow_categories")
    if changes and allowed is not None and "assumed" not in allowed:
        raise ValueError("Configured QA categories prohibit assumed pressure surcharge changes")
    return changes, evidence, review
