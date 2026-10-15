"""Prepare coherent utility node and link parameters from best available evidence.

Author: Lucas HT Nguyen
Last modified by: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained. SI engineering values; no model file writing occurs here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from .evidence import apply_reviewed_overrides, collect_structure_ports, normalize_elevation, resolve_ports, tolerance_metres
from .placeholders import resolve_missing_rim
from .qa import connectivity_findings, finding, profile_findings
from .records import Assignment, Network, PreparedNetwork, finite_number, network_from_dict
from .units import to_metres


def _material_key(value: Any) -> str:
    """Normalize exact material aliases without fuzzy or substring matching."""
    return " ".join(str(value or "").strip().upper().split())


def resolve_pressure_parameters(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve an explicit pressure equation without borrowing gravity roughness.

    Parameters
    ----------
    policy : mapping
        Roughness policy containing an optional explicit pressure_parameters
        contract with equation, coefficient, conduit_manning_n and basis.

    Returns
    -------
    dict
        Canonical coefficient, SI unit and traceable assumption, or an unresolved
        assignment when no pressure contract has been authorized.

    Raises
    ------
    ValueError
        An explicit coefficient, equation, unit, Manning n or basis is invalid.

    Notes
    -----
    H-W coefficients are dimensionless. D-W absolute roughness is converted to
    metres; it is independent of the separately declared partial-flow Manning n.
    Missing contracts remain held. No empirical coefficient is supplied here.
    """
    pressure = policy.get("pressure_parameters", {})
    if pressure.get("policy") != "explicit_assumption":
        return {"value": None, "category": "unresolved", "flags": ["unsupported_pressure_or_parameter_contract"]}
    equation = pressure.get("equation")
    raw = pressure.get("coefficient")
    if equation == "H-W":
        coefficient = finite_number(raw)
        unit = "dimensionless"
    elif equation == "D-W" and isinstance(raw, Mapping):
        coefficient = to_metres(raw.get("value"), raw.get("unit"))
        unit = "m"
    else:
        raise ValueError("Pressure equation must be H-W or D-W with explicit coefficient units")
    manning = finite_number(pressure.get("conduit_manning_n"))
    if coefficient is None or coefficient <= 0 or manning is None or manning <= 0 or not pressure.get("basis"):
        raise ValueError("Pressure contract requires positive finite coefficient, separate Manning n and basis")
    return {"value": coefficient, "unit": unit, "equation": equation,
            "category": "assumed", "policy_id": policy.get("policy_id"),
            "basis": pressure["basis"], "conduit_manning_n": manning,
            "flags": ["pressure_parameter_assumption"]}


def resolve_roughness(material: Any, pipe_kind: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve gravity Manning n from an explicit, traceable project policy.

    Notes
    -----
    There is no blanket n default. Force mains use a separately declared
    partial-flow Manning n, never their pressure coefficient. Unknown materials
    may proceed only under an explicit configured assumption.
    """
    if pipe_kind == "force_main":
        pressure = resolve_pressure_parameters(policy)
        if pressure["value"] is None:
            return pressure
        return {"value": pressure["conduit_manning_n"], "unit": "manning_n",
                "category": "assumed", "policy_id": policy.get("policy_id"),
                "basis": pressure["basis"], "flags": ["pressure_partial_flow_manning_assumption"]}
    if pipe_kind != "gravity" or policy.get("parameter") != "manning_n":
        return {"value": None, "category": "unresolved", "flags": ["unsupported_pressure_or_parameter_contract"]}
    normalized = _material_key(material)
    matched = []
    for identity, rule in policy.get("rules", {}).items():
        aliases = {_material_key(identity), *(_material_key(alias) for alias in rule.get("aliases", []))}
        if normalized and normalized in aliases:
            matched.append((identity, rule))
    if len(matched) > 1:
        raise ValueError(f"Ambiguous material aliases for {material!r}")
    if matched:
        identity, rule = matched[0]
        coefficient = finite_number(rule.get("value"))
        limits = rule.get("reference_range")
        if coefficient is None or coefficient <= 0:
            raise ValueError("Configured Manning n must be positive and finite")
        if limits is not None:
            if not isinstance(limits, Sequence) or isinstance(limits, (str, bytes)) or len(limits) != 2:
                raise ValueError("Manning reference range must contain two finite positive bounds")
            lower, upper = (finite_number(value) for value in limits)
            if lower is None or upper is None or lower <= 0 or upper < lower:
                raise ValueError("Manning reference range must contain two finite positive ordered bounds")
            if not lower <= coefficient <= upper:
                raise ValueError("Configured Manning n lies outside its declared reference range")
        return {"value": coefficient, "category": "assumed", "policy_id": policy.get("policy_id"),
                "material_class": identity, "material_raw": material, "rule": deepcopy(rule),
                "flags": ["material_roughness_assumption"], "unit": "manning_n"}
    fallback = policy.get("unknown_material", {})
    if fallback.get("policy") != "explicit_assumption":
        return {"value": None, "category": "unresolved", "material_raw": material,
                "flags": ["unknown_material_roughness"]}
    coefficient = finite_number(fallback.get("value"))
    if coefficient is None or coefficient <= 0 or not fallback.get("basis"):
        raise ValueError("Unknown-material fallback requires a positive finite value and explicit basis")
    return {"value": coefficient, "category": "assumed", "material_raw": material,
            "policy_id": policy.get("policy_id"), "rule": deepcopy(fallback), "unit": "manning_n",
            "flags": [fallback.get("qa_flag", "assumed_roughness_unknown_material")]}


def _node_assignment(node: Mapping[str, Any], field: str, config: Mapping[str, Any]) -> Assignment:
    """Normalize a declared structure measurement retaining its source identity."""
    normalized = normalize_elevation(node.get(field), node.get(f"{field}_datum", node.get("vertical_datum")),
                                     config, source_key=node["key"], asset_id=node["asset_id"])
    if normalized.value is None:
        return normalized
    return Assignment(normalized.value, normalized.category, f"structure_{field}",
                      evidence=normalized.evidence, flags=normalized.flags)



# This is the preferred reusable policy for new configurations. Explicit legacy
# methods remain available so older projects do not silently change behavior.
DEFAULT_FLOOR_METHODS = ("reviewed_override", "lowest_structure_port", "lowest_applied_pipe_end")


def _structure_floor_observations(network: Network, config: Mapping[str, Any]) -> dict[str, list[Assignment]]:
    """Normalize usable structure invert records independently of pipe matching.

    Notes
    -----
    Pipe association/diameter uncertainty does not invalidate a clear structure
    elevation. Ambiguous syntax, unit conflicts and unresolved vertical bases do.
    Explicitly inactive records/pipe references follow the project context policy.
    """
    nodes = {row["asset_id"]: row for row in network.nodes}
    pipes = {row["key"]: row for row in network.pipes}
    pipes.update({row["asset_id"]: row for row in network.pipes})
    excluded = set(config.get("ports", {}).get("assignment", {}).get("context_exclude_statuses", []))
    scenario = config.get("scenarios", {}).get(config.get("selection", {}).get("scenario"), {})
    excluded.update(scenario.get("exclude_statuses", []))
    observations: dict[str, list[Assignment]] = defaultdict(list)
    for port in collect_structure_ports(network, config):
        node_id = str(port.get("structure_id", port.get("node_id", "")))
        node = nodes.get(node_id)
        if node is None or port.get("status") in excluded:
            continue
        if port.get("family", "normalized_port") not in {"normalized_port", "inline_port"}:
            continue
        direct = port.get("pipe_key") or port.get("pipe_id")
        if direct in pipes and pipes[direct].get("status") in excluded:
            continue
        # A nonsensical diameter is an association warning, not a replacement
        # elevation. Qualified/ambiguous text or conflicting units stay unusable.
        parsing_flags = set(port.get("flags", []))
        if parsing_flags - {"invalid_port_diameter"}:
            continue
        normalized = normalize_elevation(port.get("invert", port.get("elevation_m")),
            port.get("vertical_datum", node.get("vertical_datum")), config,
            source_key=port["key"], asset_id=node_id)
        if normalized.value is None:
            continue
        flags = set(normalized.flags) | parsing_flags
        observations[node_id].append(Assignment(normalized.value,
            "provisional" if flags else "interpreted", "structure_port_observation",
            evidence=({"port_key": port["key"], "structure_id": node_id,
                       "port": deepcopy(port), "normalization": normalized.to_dict()},),
            flags=tuple(sorted(flags))))
    return observations


def _minimum_floor(candidates: Sequence[Assignment], method: str) -> Assignment | None:
    """Return the local minimum with all contributing records retained."""
    usable = [row for row in candidates if row.value is not None]
    if not usable:
        return None
    selected = min(usable, key=lambda row: row.value)
    return Assignment(selected.value, selected.category, method,
        evidence=tuple({"assignment": row.to_dict(), "selected_minimum": row.value == selected.value}
                       for row in usable), flags=selected.flags)


def _applied_end_floor_observations(node: Mapping[str, Any],
        incident: Sequence[tuple[Mapping[str, Any], str]],
        ends: Mapping[tuple[str, str], Assignment], donors: set[str],
        model_pipe_ends: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[Assignment]:
    """Collect only local pipe ends from the permitted model-plan donor set.

    Notes
    -----
    Incoming pipes contribute their downstream end; outgoing pipes their upstream
    end. The remote end of the same conduit is never a floor observation here.
    Baseline context contains actual CONDUITS, never inferred pump/regulator levels.
    prepare_project prunes speculative new donors to a stable accepted handoff.
    """
    result = []
    for pipe, end in incident:
        if pipe["key"] not in donors:
            continue
        assignment = ends[(pipe["key"], end)]
        if assignment.value is not None:
            result.append(Assignment(assignment.value, assignment.category, "applied_source_pipe_end",
                evidence=({"pipe_key": pipe["key"], "end": end, "assignment": assignment.to_dict(),
                           "basis": "planned_model_pipe_end"},), flags=assignment.flags))
    name = node.get("model_id") or node["asset_id"]
    fold = lambda value: value.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    matches = [rows for key, rows in model_pipe_ends.items() if fold(key) == fold(name)]
    if len(matches) > 1:
        raise ValueError("Case-insensitive modeled pipe-end node identity collision")
    for record in matches[0] if matches else []:
        value = finite_number(record.get("invert_m"))
        fingerprint = record.get("baseline_sha256")
        if (record.get("basis") != "baseline_model" or not isinstance(fingerprint, str) or
                len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint)):
            raise ValueError("Modeled pipe-end context requires a fingerprinted baseline_model basis")
        if (value is None or record.get("end") not in {"upstream", "downstream"} or
                not isinstance(record.get("node_id"), str) or fold(record["node_id"]) != fold(name) or
                not isinstance(record.get("model_id"), str) or not record["model_id"].strip()):
            raise ValueError("Modeled pipe-end context requires a finite local invert and explicit identities/end")
        if record.get("disposition") in {"hold", "not_selected", "rejected"}:
            continue
        result.append(Assignment(value, "observed", "existing_model_pipe_end",
                                 evidence=(deepcopy(dict(record)),)))
    return result


def _floor_assignment(node: Mapping[str, Any], methods: Sequence[str],
                      incident: Sequence[tuple[Mapping[str, Any], str]],
                      ends: Mapping[tuple[str, str], Assignment],
                      config: Mapping[str, Any], *,
                      structure_observations: Sequence[Assignment] = (),
                      applied_end_observations: Sequence[Assignment] = ()) -> Assignment:
    """Apply declared structure-first floor policy with a local applied-end fallback."""
    if "floor" in node.get("_reviewed_override_holds", []):
        return Assignment(None, "unresolved", "stale_override_hold", flags=("stale_reviewed_override",))
    for method in methods:
        if method in {"lowest_structure_port", "lowest_applied_pipe_end"}:
            observations = structure_observations if method == "lowest_structure_port" else applied_end_observations
            candidate = _minimum_floor(observations, method)
            if candidate is not None:
                return candidate
        elif method == "structure_floor":
            candidate = _node_assignment(node, "floor", config)
            if candidate.value is not None:
                return candidate
        elif method == "reviewed_override":
            reviewed = node.get("_reviewed_overrides", {}).get("floor")
            if reviewed is not None:
                candidate = normalize_elevation(reviewed["value_m"], reviewed["vertical_datum"], config,
                                                source_key=node["key"], asset_id=node["asset_id"])
                if candidate.value is not None:
                    return Assignment(candidate.value, candidate.category, "reviewed_override",
                                      evidence=({"review": reviewed, "normalization": candidate.to_dict()},), flags=candidate.flags)
        elif method in ("unique_active_outgoing_port", "lowest_active_port", "minimum_active_port"):
            relevant = [(pipe, end) for pipe, end in incident
                        if method != "unique_active_outgoing_port" or end == "upstream"]
            observations = [ends[(pipe["key"], end)] for pipe, end in relevant
                            if ends[(pipe["key"], end)].value is not None]
            if method == "unique_active_outgoing_port" and len(relevant) != 1:
                continue
            if not observations:
                continue
            if method == "unique_active_outgoing_port" and len(observations) != 1:
                continue
            selected = min(observations, key=lambda assignment: assignment.value)
            return Assignment(selected.value, "provisional" if selected.flags else "interpreted", method,
                evidence=tuple({"pipe_key": pipe["key"], "end": end, "assignment": ends[(pipe["key"], end)].to_dict()}
                               for pipe, end in relevant if ends[(pipe["key"], end)].value is not None),
                flags=tuple(sorted({flag for assignment in observations for flag in assignment.flags})))
        else:
            raise ValueError(f"Unsupported floor method: {method!r}")
    return Assignment(None, "unresolved", "missing_floor", flags=("missing_required_floor",))


def _rim_assignment(node: Mapping[str, Any], methods: Sequence[str], config: Mapping[str, Any]) -> Assignment:
    """Select a structure rim independently from the floor and port measurements."""
    if "rim" in node.get("_reviewed_override_holds", []):
        return Assignment(None, "unresolved", "stale_override_hold", flags=("stale_reviewed_override",))
    for method in methods:
        if method == "reviewed_override":
            reviewed = node.get("_reviewed_overrides", {}).get("rim")
            if reviewed is not None:
                candidate = normalize_elevation(reviewed["value_m"], reviewed["vertical_datum"], config,
                                                source_key=node["key"], asset_id=node["asset_id"])
                if candidate.value is not None:
                    return Assignment(candidate.value, candidate.category, "reviewed_override",
                                      evidence=({"review": reviewed, "normalization": candidate.to_dict()},), flags=candidate.flags)
        elif method == "structure_rim":
            candidate = _node_assignment(node, "rim", config)
            if candidate.value is not None:
                return candidate
        elif method != "reviewed_override":
            raise ValueError(f"Unsupported rim method: {method!r}")
    return Assignment(None, "unresolved", "missing_rim", flags=("missing_required_rim",))


def _geometry_length(coordinates: Any) -> float | None:
    """Measure complete projected XY geometry in canonical metres."""
    if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)) or len(coordinates) < 2:
        return None
    if any(not isinstance(point, Sequence) or len(point) < 2 or
           any(finite_number(value) is None for value in point[:2]) for point in coordinates):
        return None
    return math.fsum(math.dist(first[:2], second[:2]) for first, second in zip(coordinates, coordinates[1:]))


def _pipe_measurement(pipe: Mapping[str, Any], name: str, methods: Sequence[str]) -> tuple[float | None, str]:
    """Resolve a positive pipe measurement using explicit source precedence."""
    if name in pipe.get("_reviewed_override_holds", []):
        return None, "stale_reviewed_override"
    for method in methods:
        if method == f"attribute_{name}":
            value = finite_number(pipe.get(name))
        elif method == "geometry_length" and name == "length":
            value = _geometry_length(pipe.get("coordinates"))
        elif method == "reviewed_override":
            reviewed = pipe.get("_reviewed_overrides", {}).get(name)
            value = reviewed["value_m"] if reviewed is not None else None
        else:
            raise ValueError(f"Unsupported {name} method: {method!r}")
        if value is not None and value > 0:
            return value, method
    return None, f"missing_required_{name}"


def _pressure_connection(node: Mapping[str, Any], baseline: Mapping[str, Any] | None,
                         incident: Sequence[tuple[Mapping[str, Any], str]], selected: set[str],
                         policy: Mapping[str, Any]) -> dict[str, Any] | None:
    """Classify selected pressure endpoints without sealing terminal outlets.

    Notes
    -----
    Only internal, exclusively force-main connections may be inferred sealed.
    Terminal or mixed gravity connections require an explicit reviewed role.
    Existing STORAGE profiles remain wet wells and are never sealed junctions.
    """
    pressure = [(pipe, end) for pipe, end in incident
                if pipe["key"] in selected and pipe.get("pipe_kind") == "force_main"]
    if not pressure or not policy:
        return None
    role = policy.get("node_overrides", {}).get(node["asset_id"])
    method = "explicit_role" if role else "unresolved_pressure_role"
    if baseline is not None and baseline.get("model_section") == "STORAGE":
        if role not in {None, "wet_well"}:
            return {"role": "unresolved", "method": "conflicting_storage_pressure_role",
                    "basis": policy.get("basis"), "incident_force_main_keys": sorted(p["key"] for p, _ in pressure)}
        role, method = "wet_well", "preserved_existing_storage"
    elif role is None and policy.get("classification", "explicit_roles") == "internal_force_main_only":
        incoming = any(end == "downstream" for _, end in pressure)
        outgoing = any(end == "upstream" for _, end in pressure)
        # An unselected active gravity tributary is still evidence that sealing
        # this connection would need an explicit engineering decision.
        exclusively_pressure = all(pipe.get("pipe_kind") == "force_main" for pipe, _ in incident)
        if incoming and outgoing and exclusively_pressure:
            role, method = "sealed", "internal_force_main_only"
    if role == "wet_well" and (baseline is None or baseline.get("model_section") != "STORAGE"):
        role, method = "unresolved", "unsupported_new_wet_well_contract"
    return {"role": role or "unresolved", "method": method, "basis": policy.get("basis"),
            "incident_force_main_keys": sorted(p["key"] for p, _ in pressure)}


def prepare_network(network: Network, config: Mapping[str, Any], *,
                    baseline_nodes: Mapping[str, Mapping[str, Any]] | None = None,
                    selected_pipe_keys: Sequence[str] | None = None,
                    floor_donor_pipe_keys: Sequence[str] | None = None,
                    model_pipe_ends: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> PreparedNetwork:
    """Prepare complete parameters while keeping every requested hold traceable.

    Parameters
    ----------
    network : Network
        Full canonical source network; all incident pipes participate in port
        attribution before the selected export subset is prepared.
    config : mapping
        Effective project configuration defining port, datum, floor/rim,
        dimensional and roughness policies. No municipality defaults exist.
    baseline_nodes : mapping, optional
        Existing model node IDs to SI ``floor_m``/``rim_m`` and optional
        ``model_section``/``model_id`` provenance. Existing floors are preserved;
        existing STORAGE tops are also preserved regardless of junction-rim
        policy. New pipe offsets are recomputed from absolute ends. Updating
        existing floors and all dependencies requires another contract.
    selected_pipe_keys : sequence of str, optional
        Explicit requested source keys. Omission prepares every source pipe;
        it never implies deletion of any baseline model object.
    floor_donor_pipe_keys : sequence of str, optional
        Selected pipe keys eligible to donate local resolved ends to the floor
        fallback. Omission initially permits the selected set. prepare_project
        prunes this set to compiler-accepted instructions before returning a plan.
    model_pipe_ends : mapping, optional
        Model-node IDs to finite, fingerprinted baseline CONDUITS end records.
        Incoming ends use their downstream elevation, outgoing their upstream.
        No pump/regulator elevation is inferred.

    Returns
    -------
    PreparedNetwork
        Independent prepared rows, decisions, findings and held dispositions.

    Notes
    -----
    SWMM DEPTH offset = absolute pipe end minus node floor. ELEVATION offset
    serializes the absolute pipe end instead. Maximum depth = rim minus floor.
    Missing mandatory evidence remains absent; hydraulic zero is never a fill.
    A configured missing-rim placeholder is a separately flagged assumption above
    a known floor, never a replacement source observation.
    Default new-project floors use structure port minima before local modeled
    pipe ends. Explicit legacy floor methods retain their configured semantics.
    Standalone preparation is provisional until the caller or prepare_project
    confirms which candidate pipes actually survive the writer capability checks.
    """
    network, override_findings = apply_reviewed_overrides(network, config)
    node_policy = config.get("parameters", {}).get("nodes", {})
    pipe_policy = config.get("parameters", {}).get("pipes", {})
    floor_methods = node_policy.get("floor_methods", DEFAULT_FLOOR_METHODS)
    if not floor_methods or not node_policy.get("rim_methods"):
        raise ValueError("Nonempty floor_methods and explicit rim_methods are required")
    if not pipe_policy.get("length_methods") or not pipe_policy.get("diameter_methods"):
        raise ValueError("Explicit pipe length_methods and diameter_methods are required")
    convention = config.get("model", {}).get("target", {}).get("link_offsets")
    if convention not in ("DEPTH", "ELEVATION"):
        raise ValueError("Target model must explicitly declare DEPTH or ELEVATION offsets")
    allowed_categories = config.get("qa", {}).get("allow_categories")
    selected = set(selected_pipe_keys) if selected_pipe_keys is not None else {pipe["key"] for pipe in network.pipes}
    pipes_by_key = {pipe["key"]: pipe for pipe in network.pipes}
    if selected - pipes_by_key.keys():
        raise ValueError(f"Selected source keys not found: {sorted(selected - pipes_by_key.keys())}")
    donors = set(floor_donor_pipe_keys) if floor_donor_pipe_keys is not None else set(selected)
    if donors - selected:
        raise ValueError("Floor donor pipes must belong to the selected model-plan scope")
    modeled_ends = model_pipe_ends or {}
    structure_observations = _structure_floor_observations(network, config) if "lowest_structure_port" in floor_methods else {}
    ends, port_findings = resolve_ports(network, config)
    findings = list(network.findings) + override_findings + port_findings
    nodes_by_id = {node["asset_id"]: node for node in network.nodes}
    incidents: dict[str, list[tuple[Mapping[str, Any], str]]] = defaultdict(list)
    context_exclude = set(config.get("ports", {}).get("assignment", {}).get("context_exclude_statuses", []))
    scenario = config.get("scenarios", {}).get(config.get("selection", {}).get("scenario"), {})
    context_exclude.update(scenario.get("exclude_statuses", []))
    for pipe in network.pipes:
        if pipe.get("status") in context_exclude:
            continue
        incidents[str(pipe.get("from_node"))].append((pipe, "upstream"))
        incidents[str(pipe.get("to_node"))].append((pipe, "downstream"))
    needed = {str(pipes_by_key[key].get(field)) for key in selected for field in ("from_node", "to_node")}
    baseline_index = {}
    ascii_upper = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for model_id, values in (baseline_nodes or {}).items():
        folded = model_id.translate(ascii_upper)
        if folded in baseline_index:
            raise ValueError("Case-insensitive baseline node identity collision")
        baseline_index[folded] = values
    prepared_nodes = []
    decisions = []
    for identity in sorted(needed):
        node = nodes_by_id.get(identity)
        if node is None:
            prepared_nodes.append({"key": f"unresolved_node/{identity}", "asset_id": identity,
                                   "disposition": "hold", "reasons": ["missing_structure_identity"]})
            continue
        baseline = baseline_index.get((node.get("model_id") or identity).translate(ascii_upper))
        applied_end_observations = _applied_end_floor_observations(node, incidents[identity], ends, donors, modeled_ends) if "lowest_applied_pipe_end" in floor_methods else []
        floor = _floor_assignment(node, floor_methods, incidents[identity], ends, config,
            structure_observations=structure_observations.get(identity, []), applied_end_observations=applied_end_observations)
        proposed_floor = floor.to_dict()
        rim = _rim_assignment(node, node_policy["rim_methods"], config)
        if baseline is not None:
            baseline_floor = finite_number(baseline.get("floor_m"))
            baseline_rim = finite_number(baseline.get("rim_m"))
            if baseline_floor is None or baseline_rim is None:
                raise ValueError(f"Existing model node {identity} lacks a finite floor/rim")
            model_section = baseline.get("model_section", "JUNCTIONS")
            if model_section not in {"JUNCTIONS", "STORAGE"}:
                raise ValueError("Existing endpoint profile supports only JUNCTIONS/STORAGE")
            if floor.value is not None and any(method in floor_methods for method in ("lowest_structure_port", "lowest_applied_pipe_end")):
                tolerance = tolerance_metres(config.get("ports", {}).get("assignment", {}).get("elevation_conflict_tolerance"), 1e-9)
                if abs(floor.value - baseline_floor) > tolerance:
                    findings.append(finding("existing_floor_differs_from_policy", node["key"], config,
                        baseline_floor_m=baseline_floor, proposed_floor_m=floor.value,
                        residual_m=floor.value - baseline_floor, proposed_assignment=proposed_floor,
                        action="review_dependency_reconciliation; baseline_floor_preserved"))
            reference = {"model_id": baseline.get("model_id", node.get("model_id") or identity),
                         "model_section": model_section}
            floor = Assignment(baseline_floor, "observed", "preserved_baseline_floor", evidence=(reference,))
            # An existing storage's top and stage/area relationship are one
            # preserved hydraulic object; generic junction-rim policy cannot edit it.
            if model_section == "STORAGE" or not node_policy.get("update_existing_rims", False):
                rim = Assignment(baseline_rim, "observed", "preserved_baseline_rim", evidence=(reference,))
        rim = resolve_missing_rim(node, floor, rim, config, is_existing=baseline is not None)
        reasons = []
        pressure_connection = _pressure_connection(node, baseline, incidents[identity], selected,
                                                  node_policy.get("pressure_connections", {}))
        surcharge_depth = None
        if pressure_connection is not None:
            # Pressure classification is independent of physical floor/rim QA.
            # An unresolved or rejected force main must not remove valid gravity
            # conduits sharing an otherwise complete manhole profile.
            pressure_connection["reasons"] = []
            if pressure_connection["role"] == "unresolved":
                pressure_connection["reasons"].append("unresolved_pressure_connection_role")
            elif pressure_connection["role"] == "sealed":
                declared = node_policy["pressure_connections"].get("surcharge_depth", {})
                surcharge_depth = to_metres(declared.get("value"), declared.get("unit"))
                if surcharge_depth < 0 or not pressure_connection.get("basis"):
                    raise ValueError("Sealed pressure connections require nonnegative surcharge depth and basis")
                if allowed_categories is not None and "assumed" not in allowed_categories:
                    pressure_connection["reasons"].append("surcharge_depth:assignment_category_not_allowed")
        coordinates = node.get("coordinates")
        if baseline is None and (not isinstance(coordinates, Sequence) or len(coordinates) < 2 or
                                 any(finite_number(value) is None for value in coordinates[:2])):
            reasons.append("unusable_point_geometry")
        if floor.value is None:
            reasons.append("missing_required_floor")
        if rim.value is None:
            reasons.append("missing_required_rim")
        for field, assignment in (("floor", floor), ("rim", rim)):
            if allowed_categories is not None and assignment.value is not None and assignment.category not in allowed_categories:
                reasons.append(f"{field}:assignment_category_not_allowed")
        depth = rim.value - floor.value if floor.value is not None and rim.value is not None else None
        if depth is not None and depth <= 0:
            reasons.append("rim_not_above_floor")
        row = {**deepcopy(node), "floor_m": floor.value, "rim_m": rim.value, "max_depth_m": depth,
               "is_existing": baseline is not None, "proposed_floor_assignment": proposed_floor,
               "has_placeholder_rim": rim.method == "missing_rim_placeholder",
               "model_section": baseline.get("model_section", "JUNCTIONS") if baseline is not None else "JUNCTIONS",
               "disposition": "hold" if reasons else "include",
               "reasons": reasons, "assignments": {"floor": floor.to_dict(), "rim": rim.to_dict()}}
        if pressure_connection is not None:
            row["pressure_connection"] = pressure_connection
            row["surcharge_depth_m"] = surcharge_depth
            if surcharge_depth is not None:
                assignment = Assignment(surcharge_depth, "assumed", "pressure_connection_policy",
                                        evidence=(deepcopy(pressure_connection),),
                                        flags=("sealed_pressure_connection_assumption",))
                row["assignments"]["surcharge_depth"] = assignment.to_dict()
                decisions.append({"object_key": node["key"], "asset_id": identity,
                                  "field": "surcharge_depth", **assignment.to_dict()})
        prepared_nodes.append(row)
        for field, assignment in (("floor", floor), ("rim", rim)):
            decisions.append({"object_key": node["key"], "asset_id": identity, "field": field, **assignment.to_dict()})
            findings.extend(finding(flag, node["key"], config, field=field) for flag in assignment.flags)
    prepared_node_map = {node["asset_id"]: node for node in prepared_nodes}
    prepared_pipes = []
    for key in sorted(selected):
        pipe = pipes_by_key[key]
        reasons = []
        diameter, diameter_method = _pipe_measurement(pipe, "diameter", pipe_policy["diameter_methods"])
        length, length_method = _pipe_measurement(pipe, "length", pipe_policy["length_methods"])
        if diameter is None:
            reasons.append("missing_required_diameter")
        if length is None:
            reasons.append("missing_required_length")
        if _geometry_length(pipe.get("coordinates")) in (None, 0):
            reasons.append("unusable_line_geometry")
        if pipe.get("geometry_issues"):
            reasons.extend(pipe["geometry_issues"])
        roughness = resolve_roughness(pipe.get("material"), pipe.get("pipe_kind", "unknown"), config.get("roughness", {}))
        if roughness["value"] is None:
            reasons.extend(roughness["flags"])
        elif allowed_categories is not None and roughness["category"] not in allowed_categories:
            reasons.append("roughness:assignment_category_not_allowed")
        if pipe.get("pipe_kind") == "force_main" and pipe.get("shape") not in {None, "CIRCULAR", "FORCE_MAIN"}:
            reasons.append("unsupported_pipe_shape")
        pressure_assignment = (resolve_pressure_parameters(config.get("roughness", {}))
                               if pipe.get("pipe_kind") == "force_main" else None)
        shape = ("FORCE_MAIN" if pressure_assignment is not None and pressure_assignment["value"] is not None
                 else pipe.get("shape") or pipe_policy.get("shape"))
        if shape is None:
            reasons.append("missing_required_shape")
        elif shape != "CIRCULAR" and not (shape == "FORCE_MAIN" and pressure_assignment is not None
                                          and pressure_assignment["value"] is not None):
            reasons.append("unsupported_pipe_shape")
        assignments = {}
        values: dict[str, Any] = {}
        for end, node_field, offset_field in (("upstream", "from_node", "inlet_offset_m"),
                                               ("downstream", "to_node", "outlet_offset_m")):
            assignment = ends[(key, end)]
            assignments[end] = assignment.to_dict()
            node = prepared_node_map[str(pipe.get(node_field))]
            values[f"{end}_invert_m"] = assignment.value
            values[offset_field] = None
            if assignment.value is None:
                reasons.append(f"{end}:unresolved_port_invert")
            elif allowed_categories is not None and assignment.category not in allowed_categories:
                reasons.append(f"{end}:assignment_category_not_allowed")
            if pipe.get("pipe_kind") == "force_main":
                connection = node.get("pressure_connection")
                if connection is None:
                    reasons.append(f"{end}:unresolved_pressure_connection_role")
                else:
                    reasons.extend(f"{end}:{reason}" for reason in connection.get("reasons", []))
            if node["disposition"] == "hold":
                reasons.append(f"{end}:incomplete_node_profile")
            floor = node.get("floor_m")
            if floor is not None and assignment.value is not None:
                offset = assignment.value - floor if convention == "DEPTH" else assignment.value
                values[offset_field] = offset
                if convention == "DEPTH" and offset < 0:
                    reasons.append(f"{end}:negative_depth_offset")
            decisions.append({"object_key": key, "asset_id": pipe["asset_id"], "field": f"{end}_invert", **assignment.to_dict()})
            findings.extend(finding(flag, key, config, endpoint=end) for flag in assignment.flags)
        grade = ((values["upstream_invert_m"] - values["downstream_invert_m"]) / length
                 if length and values["upstream_invert_m"] is not None and values["downstream_invert_m"] is not None else None)
        row = {**deepcopy(pipe), **values, "diameter_m": diameter, "length_m": length,
               "roughness": roughness["value"], "roughness_assignment": roughness,
               "diameter_method": diameter_method, "length_method": length_method,
               "shape": shape, "grade": grade,
               "assignments": assignments, "disposition": "hold" if reasons else "include",
               "reasons": sorted(set(reasons))}
        if pressure_assignment is not None:
            row["pressure_assignment"] = pressure_assignment
            decisions.append({"object_key": key, "asset_id": pipe["asset_id"],
                              "field": "pressure_coefficient", **pressure_assignment})
        prepared_pipes.append(row)
        decisions.append({"object_key": key, "asset_id": pipe["asset_id"], "field": "roughness", **roughness})
        findings.extend(finding(flag, key, config, field="roughness") for flag in roughness["flags"])
    engineering_findings = profile_findings(prepared_nodes, prepared_pipes, config)
    findings.extend(engineering_findings)
    hold_keys = {row["object_key"] for row in engineering_findings if row.get("disposition") == "hold"}
    for row in prepared_pipes:
        if row["key"] in hold_keys:
            row["disposition"] = "hold"
            row["reasons"].extend(sorted({item["rule"] for item in engineering_findings
                                          if item["object_key"] == row["key"] and item["disposition"] == "hold"}))
    findings.extend(connectivity_findings(prepared_pipes, config))
    held = [deepcopy(row) for row in prepared_nodes + prepared_pipes if row["disposition"] == "hold"]
    return PreparedNetwork(tuple(prepared_nodes), tuple(prepared_pipes), tuple(decisions), tuple(findings),
                           tuple(held), deepcopy(network.manifest))


def prepare_network_from_dict(canonical: Mapping[str, Any], config: Mapping[str, Any], *,
                             baseline_nodes: Mapping[str, Mapping[str, Any]] | None = None,
                             selected_pipe_keys: Sequence[str] | None = None,
                             floor_donor_pipe_keys: Sequence[str] | None = None,
                             model_pipe_ends: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> PreparedNetwork:
    """Validate/copy canonical dictionaries and prepare an auditable SI network."""
    return prepare_network(network_from_dict(canonical), config, baseline_nodes=baseline_nodes,
                           selected_pipe_keys=selected_pipe_keys, floor_donor_pipe_keys=floor_donor_pipe_keys,
                           model_pipe_ends=model_pipe_ends)
