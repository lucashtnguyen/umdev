"""Compile finished utility parameters into SHMM operations and diff reports.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. SHMM exclusively writes model files; no raw GIS inference.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from hymo import SWMMDocument, interpret_fields, read_document
from shmm import Change, VertexChange

from .reporting import stable_hash
from .units import from_metres, to_metres

_NODE_SECTIONS = ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS")
_LINK_SECTIONS = ("CONDUITS", "PUMPS", "WEIRS", "ORIFICES", "OUTLETS")


def _key(value: str) -> str:
    """Match ASCII model identifiers without changing their original spelling."""
    return value.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def validate_crosswalk(value: Any) -> dict[str, dict[str, str]]:
    """Validate explicit asset-to-model identities without coercing IDs."""
    if not isinstance(value, Mapping) or set(value) - {"nodes", "pipes"}:
        raise ValueError("Crosswalk must contain nodes/pipes asset-ID mappings")
    result = {"nodes": {}, "pipes": {}}
    for entity in result:
        rows = value.get(entity, {})
        if not isinstance(rows, Mapping):
            raise ValueError(f"Crosswalk {entity} must be an asset-ID mapping")
        for identity, target in rows.items():
            if not isinstance(identity, str) or not identity.strip() or not isinstance(target, str) or not target.strip():
                raise ValueError("Crosswalk source and target identities must be nonempty strings")
            result[entity][identity] = target
    return result


def resolve_model_identity(row: Mapping[str, Any], entity: str, config: Mapping[str, Any],
                           crosswalk: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve one target identity using only enabled methods in configured order.

    Parameters
    ----------
    row : mapping
        Canonical source row. Previously bound identities retain their original
        mapped model ID under ``model_association.mapped_model_id``.
    entity : str
        ``nodes`` or ``pipes`` crosswalk namespace.
    config : mapping
        Effective configuration. An absent methods list retains the documented
        compatibility order: mapped model ID, explicit crosswalk, then asset ID.
        An explicitly empty list enables no method and cannot infer an ID.
    crosswalk : mapping
        Validated, frozen crosswalk snapshot; this function reads no files.

    Returns
    -------
    model_id, method : tuple of str
        Selected identity and association method for the decision audit.

    Raises
    ------
    ValueError
        Every enabled method is missing, a method is unknown or an ID malformed.
    """
    if entity not in {"nodes", "pipes"}:
        raise ValueError("Unknown model identity entity")
    association = config.get("identity", {}).get("model_association", {})
    methods = association.get("methods", ["mapped_model_id", "explicit_crosswalk", "asset_id"])
    if not isinstance(methods, Sequence) or isinstance(methods, (str, bytes)):
        raise ValueError("Model association methods must be an ordered list")
    provenance = row.get("model_association", {})
    mapped = provenance.get("mapped_model_id", row.get("model_id"))
    for method in methods:
        if method == "mapped_model_id":
            candidate = mapped
        elif method == "explicit_crosswalk":
            candidate = crosswalk.get(entity, {}).get(row["asset_id"])
        elif method == "asset_id":
            candidate = row.get("asset_id")
        else:
            raise ValueError(f"Unsupported model association method: {method!r}")
        if candidate is None or candidate == "":
            continue
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError("Model identity must be a nonempty string")
        return candidate, method
    raise ValueError(f"Unresolved {entity} model identity for source {row['key']!r}")


def bind_model_identities(canonical: dict[str, Any], config: Mapping[str, Any],
                          crosswalk: Mapping[str, Any], selected_pipe_keys: Sequence[str]) -> None:
    """Bind only requested source rows before baseline-floor preparation.

    Notes
    -----
    Original mapped IDs remain explicit provenance and raw source attributes
    remain untouched. Unselected pipes still participate in port attribution;
    a partial project crosswalk does not require identities for those assets.
    Multiple selected source nodes targeting one model node require an explicit
    aggregation contract, which this adapter does not implement.
    """
    selected = set(selected_pipe_keys)
    pipes = [row for row in canonical["pipes"] if row["key"] in selected]
    needed = {row[field] for row in pipes for field in ("from_node", "to_node")}
    for entity, rows in (("nodes", [row for row in canonical["nodes"] if row["asset_id"] in needed]),
                         ("pipes", pipes)):
        seen = {}
        for row in rows:
            target, method = resolve_model_identity(row, entity, config, crosswalk)
            folded = _key(target)
            if folded in seen and seen[folded] != row["key"]:
                raise ValueError(f"Multiple source {entity} target the same model identity")
            seen[folded] = row["key"]
            mapped = row.get("model_association", {}).get("mapped_model_id", row.get("model_id"))
            row["model_association"] = {"mapped_model_id": mapped, "resolved_model_id": target, "method": method}
            row["model_id"] = target


def baseline_node_parameters(document: SWMMDocument, config: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Read baseline junction floors/rims into canonical metres for preparation."""
    unit = config["model"]["target"]["length_unit"]
    result = {}
    for row in document.records("JUNCTIONS"):
        fields = interpret_fields("JUNCTIONS", row)
        floor = to_metres(float(fields["invert_elevation"]), unit)
        depth = to_metres(float(fields["max_depth"]), unit)
        result[row.tokens[0]] = {"floor_m": floor, "rim_m": floor + depth}
    return result


def _validate_model_basis(document: SWMMDocument, config: Mapping[str, Any]) -> None:
    """Require explicit target units/options compatible with the supplied baseline."""
    target = config["model"]["target"]
    options = {r.tokens[0].upper(): r.tokens[1].upper() for r in document.records("OPTIONS")}
    flow = options.get("FLOW_UNITS")
    if flow != target["flow_units"].upper():
        raise ValueError("Baseline FLOW_UNITS differs from the configured target; implicit model conversion is unsupported")
    required_length = "ft" if flow in {"CFS", "GPM", "MGD"} else "m"
    if not math.isclose(to_metres(1.0, target["length_unit"]), to_metres(1.0, required_length)):
        raise ValueError("Target hydraulic length unit is incompatible with SWMM flow units")
    if options.get("LINK_OFFSETS", "DEPTH") != target["link_offsets"]:
        raise ValueError("Baseline LINK_OFFSETS differs from configured target")
    metadata = config["model"].get("baseline_metadata", {})
    if metadata.get("horizontal_crs") != target.get("map_crs"):
        raise NotImplementedError("Map reprojection requires an explicit supported adapter")
    if metadata.get("vertical_datum") != target.get("vertical_datum"):
        raise ValueError("Baseline and target vertical bases differ")
    if metadata.get("map_unit") != target.get("map_unit"):
        raise ValueError("Baseline map unit differs from target; global map conversion is unsupported")


def _extra_instructions(config: Mapping[str, Any]) -> tuple[list[Change], dict[str, Any], dict[str, str]]:
    """Read explicit finished instructions, preserving source file provenance."""
    name = config["model"].get("changes_path")
    if not name:
        return [], {}, {}
    path = Path(name)
    data = path.read_bytes()
    value = json.loads(data)
    rows = value if isinstance(value, list) else value.get("changes", [])
    evidence = {} if isinstance(value, list) else value.get("evidence", {})
    if not isinstance(rows, list) or not isinstance(evidence, dict):
        raise ValueError("Finished changes require a changes list and evidence mapping")
    return [Change(**row) for row in rows], evidence, {str(path): sha256(data).hexdigest()}


def compile_handoff(document: SWMMDocument, prepared: Mapping[str, Any],
                    config: Mapping[str, Any], *, config_sha256: str,
                    source_manifest: Any = None,
                    crosswalk_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compile supported complete objects and explicit existing-model instructions.

    Parameters
    ----------
    document : SWMMDocument
        Immutable baseline; unit and offset conventions must match configuration.
    prepared : mapping
        umdev prepared SI node/link dictionaries with per-field assignment evidence.
    config : mapping
        Resolved project configuration; no municipality constants.
    config_sha256 : str
        Effective preparation configuration hash.
    source_manifest : object, optional
        Read-only input identities and hashes.

    Returns
    -------
    dict
        Versioned JSON handoff with serialized SHMM changes/vertices, source-keyed
        evidence and explicit writer-capability holds. Omission never requests deletion.

    Notes
    -----
    Existing floors are preserved. Changing a floor and all its dependencies is
    intentionally held by this adapter. Existing hydraulic pipe updates require
    exact directed endpoint identity and circular shape; geometry replacement
    remains a downstream capability gap.
    """
    _validate_model_basis(document, config)
    target = config["model"]["target"]
    length_unit, map_unit = target["length_unit"], target["map_unit"]
    junctions = {_key(r.tokens[0]): r for r in document.records("JUNCTIONS")}
    node_namespace = {_key(r.tokens[0]): section for section in _NODE_SECTIONS for r in document.records(section)}
    links = {_key(r.tokens[0]): r for r in document.records("CONDUITS")}
    link_namespace = {_key(r.tokens[0]): section for section in _LINK_SECTIONS for r in document.records(section)}
    sections = {_key(r.tokens[0]): r for r in document.records("XSECTIONS")}
    coords = {_key(r.tokens[0]): tuple(map(float, r.tokens[1:3])) for r in document.records("COORDINATES")}
    nodes = {r["asset_id"]: r for r in prepared.get("nodes", [])}
    source_pipes = [r for r in prepared.get("pipes", []) if r.get("disposition") == "include"]
    crosswalk = {"nodes": {}, "pipes": {}}
    path = config.get("identity", {}).get("model_association", {}).get("crosswalk_path")
    side_inputs = {}
    if crosswalk_snapshot is not None:
        crosswalk = validate_crosswalk(crosswalk_snapshot)
    elif path:
        raw = Path(path).read_bytes()
        crosswalk = validate_crosswalk(json.loads(raw))
        side_inputs[str(path)] = sha256(raw).hexdigest()
    def target_node(row: Mapping[str, Any]) -> str:
        return resolve_model_identity(row, "nodes", config, crosswalk)[0]
    holds = []
    accepted = []
    seen_targets = set()
    for pipe in source_pipes:
        name = resolve_model_identity(pipe, "pipes", config, crosswalk)[0]
        upstream, downstream = nodes[pipe["from_node"]], nodes[pipe["to_node"]]
        ends = (target_node(upstream), target_node(downstream))
        reason = None
        if _key(name) in seen_targets:
            raise ValueError("Two prepared source pipes target the same model identity")
        seen_targets.add(_key(name))
        if any(_key(node) in node_namespace and _key(node) not in junctions for node in ends):
            reason = "existing_nonjunction_endpoint_requires_supported_parameter_contract"
        elif _key(name) in link_namespace and _key(name) not in links:
            reason = "existing_nonconduit_target_requires_supported_operation"
        elif pipe.get("shape") != "CIRCULAR":
            reason = "prepared_shape_requires_additional_model_adapter"
        elif _key(name) in links:
            baseline = links[_key(name)]
            if tuple(map(_key, baseline.tokens[1:3])) != tuple(map(_key, ends)):
                reason = "directed_rewire_is_not_supported"
            elif _key(name) not in sections or sections[_key(name)].tokens[1].upper() != "CIRCULAR":
                reason = "existing_shape_conversion_is_not_supported"
        if reason:
            holds.append({"source_key": pipe["key"], "asset_id": pipe["asset_id"], "reason": reason})
        else:
            accepted.append((name, pipe, upstream, downstream))
    needed = {n["asset_id"] for _, _, upstream, downstream in accepted for n in (upstream, downstream)}
    seen_node_targets = {}
    for identity in sorted(needed):
        target_identity = _key(target_node(nodes[identity]))
        if target_identity in seen_node_targets and seen_node_targets[target_identity] != identity:
            raise ValueError("Multiple source nodes target the same model identity")
        seen_node_targets[target_identity] = identity
    changes, vertices, evidence = [], [], {}
    fields_units = {"invert_elevation": length_unit, "max_depth": length_unit, "length": length_unit,
                    "inlet_offset": length_unit, "outlet_offset": length_unit, "geom1": length_unit,
                    "roughness": "Manning n", "x": map_unit, "y": map_unit, "vertices": map_unit}
    for identity in sorted(needed):
        row = nodes[identity]
        name, source_key = target_node(row), row["key"]
        floor = from_metres(row["floor_m"], length_unit)
        depth = from_metres(row["max_depth_m"], length_unit)
        evidence[source_key] = {"asset_id": identity, "assignments": row.get("assignments", {}),
            "units": fields_units, "qa_flags": [f for f in prepared.get("findings", []) if f.get("object_key") == source_key]}
        if _key(name) in junctions:
            old = interpret_fields("JUNCTIONS", junctions[_key(name)])
            if not math.isclose(floor, float(old["invert_elevation"]), rel_tol=0, abs_tol=1e-9):
                raise NotImplementedError("Existing floor edits require complete dependent-end preservation")
            if not math.isclose(depth, float(old["max_depth"]), rel_tol=0, abs_tol=1e-9):
                changes.append(Change("update", "JUNCTIONS", name, {"max_depth": depth}, source_key,
                                      {"max_depth": float(old["max_depth"])}))
        else:
            changes.append(Change("add", "JUNCTIONS", name, {"invert_elevation": floor, "max_depth": depth}, source_key))
        if _key(name) not in coords:
            point = row.get("coordinates") or []
            if len(point) < 2 or any(not math.isfinite(float(v)) for v in point[:2]):
                raise ValueError("Prepared node has no finite map point")
            mapped = tuple(from_metres(float(v), map_unit) for v in point[:2])
            changes.append(Change("add", "COORDINATES", name, {"x": mapped[0], "y": mapped[1]}, source_key))
            coords[_key(name)] = mapped
    for name, pipe, upstream, downstream in accepted:
        source_key = pipe["key"]
        values = {"inlet_node": target_node(upstream), "outlet_node": target_node(downstream),
                  "length": from_metres(pipe["length_m"], length_unit), "roughness": pipe["roughness"],
                  "inlet_offset": from_metres(pipe["inlet_offset_m"], length_unit),
                  "outlet_offset": from_metres(pipe["outlet_offset_m"], length_unit)}
        diameter = from_metres(pipe["diameter_m"], length_unit)
        evidence[source_key] = {"asset_id": pipe["asset_id"], "assignments": pipe.get("assignments", {}),
            "roughness_assignment": pipe.get("roughness_assignment"), "length_method": pipe.get("length_method"),
            "diameter_method": pipe.get("diameter_method"), "units": fields_units,
            "qa_flags": [f for f in prepared.get("findings", []) if f.get("object_key") == source_key]}
        if _key(name) in links:
            old = interpret_fields("CONDUITS", links[_key(name)])
            editable = {field: value for field, value in values.items() if field not in {"inlet_node", "outlet_node"}}
            changes.append(Change("update", "CONDUITS", name, editable, source_key,
                                  {field: float(old[field]) for field in editable}))
            changes.append(Change("update", "XSECTIONS", name, {"geom1": diameter}, source_key,
                                  {"geom1": float(sections[_key(name)].tokens[2])}))
        else:
            changes.append(Change("add", "CONDUITS", name, values, source_key))
            changes.append(Change("add", "XSECTIONS", name,
                {"shape": "CIRCULAR", "geom1": diameter, "geom2": 0, "geom3": 0, "geom4": 0}, source_key))
            points = [tuple(float(v) for v in point[:2]) for point in pipe["coordinates"]]
            source_start, source_end = upstream["coordinates"][:2], downstream["coordinates"][:2]
            tolerance = config.get("ports", {}).get("assignment", {}).get("endpoint_tolerance", {"value": 0, "unit": "m"})
            tolerance_m = to_metres(tolerance["value"], tolerance["unit"])
            forward = math.dist(points[0], source_start) <= tolerance_m and math.dist(points[-1], source_end) <= tolerance_m
            reverse = math.dist(points[-1], source_start) <= tolerance_m and math.dist(points[0], source_end) <= tolerance_m
            if forward == reverse:
                raise ValueError("Line endpoints do not establish a unique orientation")
            if reverse:
                points.reverse()
            mapped = [tuple(from_metres(v, map_unit) for v in point) for point in points]
            gaps = (math.dist(mapped[0], coords[_key(values["inlet_node"])]),
                    math.dist(mapped[-1], coords[_key(values["outlet_node"])]))
            if gaps[1] <= 1e-8:
                mapped.pop()
            if gaps[0] <= 1e-8:
                mapped.pop(0)
            vertices.append(VertexChange(name, mapped, source_key))
            evidence[source_key]["geometry"] = {"reversed_digitization": reverse,
                "endpoint_display_connectors": list(gaps), "unit": map_unit, "existing_points_preserved": True}
    extras, extra_evidence, extra_inputs = _extra_instructions(config)
    changes.extend(extras)
    for key, value in extra_evidence.items():
        if key in evidence:
            raise ValueError("Finished instruction evidence key conflicts with prepared source")
        evidence[key] = value
    side_inputs.update(extra_inputs)
    return {"schema_version": "1", "baseline_sha256": sha256(document.data).hexdigest(),
            "scenario": config["selection"]["scenario"], "config_sha256": config_sha256,
            "prepared_sha256": stable_hash(prepared), "source_manifest": source_manifest,
            "changes": [asdict(c) for c in changes], "vertex_changes": [asdict(v) for v in vertices],
            "evidence": evidence, "application_holds": holds, "side_input_hashes": side_inputs,
            "applied_source_keys": [p["key"] for _, p, _, _ in accepted],
            "model_ids": {"nodes": {identity: target_node(nodes[identity]) for identity in needed},
                          "pipes": {p["key"]: name for name, p, _, _ in accepted}}}


def instructions_from_handoff(handoff: Mapping[str, Any]) -> tuple[list[Change], list[VertexChange]]:
    """Read a versioned data handoff without evaluating executable expressions."""
    if handoff.get("schema_version") != "1":
        raise ValueError("Unsupported SHMM handoff schema")
    return [Change(**row) for row in handoff["changes"]], [VertexChange(**row) for row in handoff["vertex_changes"]]


def verify_prepared_profiles(candidate: SWMMDocument, prepared: Mapping[str, Any],
                             handoff: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """Independently reconstruct actual written floors, rims, pipe ends and grades."""
    unit = config["model"]["target"]["length_unit"]
    setting = config.get("qa", {}).get("readback_absolute_tolerance", {"value": 1e-9, "unit": "m"})
    tolerance = to_metres(setting["value"], setting["unit"])
    junctions = {_key(r.tokens[0]): r for r in candidate.records("JUNCTIONS")}
    conduits = {_key(r.tokens[0]): r for r in candidate.records("CONDUITS")}
    xsections = {_key(r.tokens[0]): r for r in candidate.records("XSECTIONS")}
    nodes = {r["asset_id"]: r for r in prepared["nodes"]}
    applied = set(handoff["applied_source_keys"])
    checked_nodes, rows = set(), []
    for pipe in prepared["pipes"]:
        if pipe["key"] not in applied:
            continue
        name = handoff["model_ids"]["pipes"][pipe["key"]]
        observed = conduits[_key(name)]
        node_values = []
        for end, field, column in (("upstream", "from_node", 5), ("downstream", "to_node", 6)):
            node = nodes[pipe[field]]
            model_id = handoff["model_ids"]["nodes"][node["asset_id"]]
            raw = junctions[_key(model_id)]
            floor = to_metres(float(raw.tokens[1]), unit)
            depth = to_metres(float(raw.tokens[2]) if len(raw.tokens) > 2 else 0, unit)
            port = floor + to_metres(float(observed.tokens[column]), unit) if config["model"]["target"]["link_offsets"] == "DEPTH" else to_metres(float(observed.tokens[column]), unit)
            for value, expected, label in ((floor, node["floor_m"], "floor"),
                    (floor + depth, node["rim_m"], "rim"), (port, pipe[end + "_invert_m"], "pipe-end invert")):
                if not math.isclose(value, expected, rel_tol=0, abs_tol=tolerance):
                    raise ValueError(f"Written {label} differs from prepared evidence")
            checked_nodes.add(model_id)
            node_values.append(port)
        diameter = to_metres(float(xsections[_key(name)].tokens[2]), unit)
        length = to_metres(float(observed.tokens[3]), unit)
        if not math.isclose(diameter, pipe["diameter_m"], rel_tol=0, abs_tol=tolerance) or not math.isclose(length, pipe["length_m"], rel_tol=0, abs_tol=tolerance):
            raise ValueError("Written pipe dimensions differ from prepared evidence")
        grade = (node_values[0] - node_values[1]) / length
        if not math.isclose(grade, pipe["grade"], rel_tol=0, abs_tol=2 * tolerance / length):
            raise ValueError("Written pipe grade differs from prepared evidence")
        rows.append({"source_key": pipe["key"], "model_id": name, "inlet_invert_m": node_values[0],
                     "outlet_invert_m": node_values[1], "grade": grade})
    return {"checked_nodes": len(checked_nodes), "checked_pipes": len(rows), "profiles": rows}


def candidate_engineering_qa(document: SWMMDocument, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """QA actual written node/pipe profiles and complete modeled connectivity.

    Notes
    -----
    Structured profile checks cover standard node invert/depth positions and
    CIRCULAR/FORCE_MAIN conduit heights. Other link types participate in the
    directed connectivity graph without invented hydraulic parameters.
    """
    from .qa import connectivity_findings, profile_findings
    unit = config["model"]["target"]["length_unit"]
    convention = config["model"]["target"]["link_offsets"]
    nodes, floors = [], {}
    for section in _NODE_SECTIONS:
        for record in document.records(section):
            floor = to_metres(float(record.tokens[1]), unit)
            floors[_key(record.tokens[0])] = floor
            row = {"key": f"model/{section}/{record.tokens[0]}", "asset_id": _key(record.tokens[0]), "display_id": record.tokens[0],
                   "floor_m": floor, "rim_m": None, "max_depth_m": None}
            if section in {"JUNCTIONS", "STORAGE"}:
                depth = to_metres(float(record.tokens[2]) if len(record.tokens) > 2 else 0, unit)
                row.update(rim_m=floor + depth, max_depth_m=depth)
            nodes.append(row)
    shapes = {_key(r.tokens[0]): r for r in document.records("XSECTIONS")}
    profiles, topology = [], []
    for section in _LINK_SECTIONS:
        for record in document.records(section):
            base = {"key": f"model/{section}/{record.tokens[0]}", "asset_id": record.tokens[0],
                    "from_node": _key(record.tokens[1]), "to_node": _key(record.tokens[2]), "disposition": "include"}
            topology.append(base)
            if section != "CONDUITS":
                continue
            shape = shapes.get(_key(record.tokens[0]))
            if shape is None or shape.tokens[1].upper() not in {"CIRCULAR", "FORCE_MAIN"}:
                continue
            ends = []
            fields = interpret_fields("CONDUITS", record)
            for node, token in zip(record.tokens[1:3], (fields["inlet_offset"], fields["outlet_offset"]), strict=True):
                node_floor = floors[_key(node)]
                offset = to_metres(float(token), unit) if token != "*" else node_floor
                ends.append(node_floor + offset if convention == "DEPTH" else offset)
            profiles.append({**base, "pipe_kind": "force_main" if shape.tokens[1].upper() == "FORCE_MAIN" else "gravity", "upstream_invert_m": ends[0],
                "downstream_invert_m": ends[1], "length_m": to_metres(float(record.tokens[3]), unit),
                "diameter_m": to_metres(float(shape.tokens[2]), unit)})
    return profile_findings(nodes, profiles, config) + connectivity_findings(topology, config)
