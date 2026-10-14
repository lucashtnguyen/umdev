"""Prepare coherent utility node and link parameters from best available evidence.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. SI engineering values; no model file writing occurs here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from .evidence import apply_reviewed_overrides, normalize_elevation, resolve_ports, tolerance_metres
from .qa import connectivity_findings, finding, profile_findings
from .records import Assignment, Network, PreparedNetwork, finite_number, network_from_dict


def _material_key(value: Any) -> str:
    """Normalize exact material aliases without fuzzy or substring matching."""
    return " ".join(str(value or "").strip().upper().split())


def resolve_roughness(material: Any, pipe_kind: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve gravity Manning n from an explicit, traceable project policy.

    Notes
    -----
    There is no blanket n default. Force mains require a distinct parameter
    contract and cannot inherit gravity Manning, Hazen-Williams or Darcy values.
    Unknown materials may proceed only under an explicit configured assumption.
    """
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


def _floor_assignment(node: Mapping[str, Any], methods: Sequence[str],
                      incident: Sequence[tuple[Mapping[str, Any], str]],
                      ends: Mapping[tuple[str, str], Assignment],
                      config: Mapping[str, Any]) -> Assignment:
    """Apply only explicitly configured floor methods in declared order."""
    if "floor" in node.get("_reviewed_override_holds", []):
        return Assignment(None, "unresolved", "stale_override_hold", flags=("stale_reviewed_override",))
    for method in methods:
        if method == "structure_floor":
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


def prepare_network(network: Network, config: Mapping[str, Any], *,
                    baseline_nodes: Mapping[str, Mapping[str, Any]] | None = None,
                    selected_pipe_keys: Sequence[str] | None = None) -> PreparedNetwork:
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

    Returns
    -------
    PreparedNetwork
        Independent prepared rows, decisions, findings and held dispositions.

    Notes
    -----
    SWMM DEPTH offset = absolute pipe end minus node floor. ELEVATION offset
    serializes the absolute pipe end instead. Maximum depth = rim minus floor.
    Missing mandatory evidence remains absent; hydraulic zero is never a fill.
    """
    network, override_findings = apply_reviewed_overrides(network, config)
    node_policy = config.get("parameters", {}).get("nodes", {})
    pipe_policy = config.get("parameters", {}).get("pipes", {})
    if not node_policy.get("floor_methods") or not node_policy.get("rim_methods"):
        raise ValueError("Explicit node floor_methods and rim_methods are required")
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
        floor = _floor_assignment(node, node_policy["floor_methods"], incidents[identity], ends, config)
        rim = _rim_assignment(node, node_policy["rim_methods"], config)
        if baseline is not None:
            baseline_floor = finite_number(baseline.get("floor_m"))
            baseline_rim = finite_number(baseline.get("rim_m"))
            if baseline_floor is None or baseline_rim is None:
                raise ValueError(f"Existing model node {identity} lacks a finite floor/rim")
            model_section = baseline.get("model_section", "JUNCTIONS")
            if model_section not in {"JUNCTIONS", "STORAGE"}:
                raise ValueError("Existing endpoint profile supports only JUNCTIONS/STORAGE")
            reference = {"model_id": baseline.get("model_id", node.get("model_id") or identity),
                         "model_section": model_section}
            floor = Assignment(baseline_floor, "observed", "preserved_baseline_floor", evidence=(reference,))
            # An existing storage's top and stage/area relationship are one
            # preserved hydraulic object; generic junction-rim policy cannot edit it.
            if model_section == "STORAGE" or not node_policy.get("update_existing_rims", False):
                rim = Assignment(baseline_rim, "observed", "preserved_baseline_rim", evidence=(reference,))
        reasons = []
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
               "is_existing": baseline is not None,
               "model_section": baseline.get("model_section", "JUNCTIONS") if baseline is not None else "JUNCTIONS",
               "disposition": "hold" if reasons else "include",
               "reasons": reasons, "assignments": {"floor": floor.to_dict(), "rim": rim.to_dict()}}
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
        shape = pipe.get("shape") or pipe_policy.get("shape")
        if shape is None:
            reasons.append("missing_required_shape")
        elif shape != "CIRCULAR":
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
                             selected_pipe_keys: Sequence[str] | None = None) -> PreparedNetwork:
    """Validate/copy canonical dictionaries and prepare an auditable SI network."""
    return prepare_network(network_from_dict(canonical), config, baseline_nodes=baseline_nodes,
                           selected_pipe_keys=selected_pipe_keys)
