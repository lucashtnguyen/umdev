"""Compile finished utility parameters into SHMM operations and diff reports.

Author: Lucas HT Nguyen
Last modified by: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
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

from .pressure import baseline_pressure_plan, pressure_contract_reason
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


def baseline_node_parameters(document: SWMMDocument, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Read existing junction/storage profiles in SI without modifying their cards.

    Notes
    -----
    STORAGE elevation and maximum depth are the first two numeric fields for
    both functional and tabular storage. Their sum is the preserved model top,
    not a newly surveyed ground rim. Storage curves and every remaining token
    remain opaque and untouched. Outfalls/dividers supply no junction rim.
    """
    unit = config["model"]["target"]["length_unit"]
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for section in _NODE_SECTIONS:
        for row in document.records(section):
            identity = _key(row.tokens[0])
            if identity in seen:
                raise ValueError("Case-insensitive baseline node identity collision")
            seen.add(identity)
            if section not in {"JUNCTIONS", "STORAGE"}:
                continue
            if section == "JUNCTIONS":
                fields = interpret_fields("JUNCTIONS", row)
                floor = to_metres(fields["invert_elevation"], unit)
                depth = to_metres(fields["max_depth"], unit)
            else:
                if len(row.tokens) < 3:
                    raise ValueError("Existing STORAGE requires finite elevation and maximum depth")
                floor, depth = to_metres(row.tokens[1], unit), to_metres(row.tokens[2], unit)
            result[row.tokens[0]] = {"floor_m": floor, "rim_m": floor + depth,
                "max_depth_m": depth, "model_section": section, "model_id": row.tokens[0]}
    return result


def baseline_pipe_end_parameters(document: SWMMDocument, config: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Read local absolute conduit-end elevations from the immutable baseline.

    Parameters
    ----------
    document : SWMMDocument
        Existing model; only CONDUITS provide pipe-end elevations.
    config : mapping
        Validated target model units, datum, horizontal basis and offset convention.

    Returns
    -------
    dict
        Original model-node IDs to deterministic conduit-end evidence records in
        metres, including conduit ID, local end and baseline byte fingerprint.

    Raises
    ------
    ValueError
        Node/link identities collide, a node reference is missing, or an elevation
        or offset is missing, nonfinite or unsupported (including ``*`` tokens).

    Notes
    -----
    DEPTH absolute end = local node invert + that end's offset. ELEVATION uses
    the explicit absolute end value. Node invert positions are common to
    JUNCTIONS, STORAGE, OUTFALLS and DIVIDERS; no rim or storage curve is inferred.
    Pumps, weirs, outlets and orifices supply no conduit-end evidence.
    """
    _validate_model_basis(document, config)
    unit = config["model"]["target"]["length_unit"]
    convention = config["model"]["target"]["link_offsets"]
    fingerprint = sha256(document.data).hexdigest()
    nodes: dict[str, tuple[str, float]] = {}
    for section in _NODE_SECTIONS:
        for record in document.records(section):
            identity = _key(record.tokens[0])
            if identity in nodes:
                raise ValueError("Case-insensitive baseline node identity collision")
            if len(record.tokens) < 2:
                raise ValueError("Baseline pipe context requires an explicit node invert")
            nodes[identity] = (record.tokens[0], to_metres(record.tokens[1], unit))
    context: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for record in document.records("CONDUITS"):
        identity = _key(record.tokens[0])
        if identity in seen:
            raise ValueError("Case-insensitive baseline conduit identity collision")
        seen.add(identity)
        if len(record.tokens) < 7:
            raise ValueError("Baseline pipe context requires explicit local conduit offsets")
        for end, node_id, offset_token in zip(("upstream", "downstream"), record.tokens[1:3], record.tokens[5:7], strict=True):
            if _key(node_id) not in nodes:
                raise ValueError("Baseline conduit endpoint node is absent")
            canonical_id, floor_m = nodes[_key(node_id)]
            offset_m = to_metres(offset_token, unit)
            invert_m = floor_m + offset_m if convention == "DEPTH" else offset_m
            if not math.isfinite(invert_m):
                raise ValueError("Baseline absolute pipe-end elevation is nonfinite")
            context.setdefault(canonical_id, []).append({"model_id": record.tokens[0],
                "node_id": canonical_id, "end": end, "invert_m": invert_m,
                "basis": "baseline_model", "baseline_sha256": fingerprint})
    return {identity: sorted(rows, key=lambda row: (_key(row["model_id"]), row["end"]))
            for identity, rows in sorted(context.items(), key=lambda item: _key(item[0]))}


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


def _guard_legacy_applied_floor_context(extras: Sequence[Change], config: Mapping[str, Any]) -> None:
    """Reject legacy operations that invalidate the frozen local-end floor basis.

    Notes
    -----
    A separate legacy instruction packet is not part of monotone source-pipe
    acceptance. Its topology/node-invert/offset changes therefore cannot be used
    safely with applied-end floor inference. Dimension-only updates leave the
    local absolute end elevations intact and remain supported.
    """
    from .parameters import DEFAULT_FLOOR_METHODS
    methods = config.get("parameters", {}).get("nodes", {}).get("floor_methods", DEFAULT_FLOOR_METHODS)
    if "lowest_applied_pipe_end" not in methods:
        return
    for change in extras:
        section = change.section.upper()
        affects_ends = (section == "CONDUITS" and
            (change.action != "update" or bool(set(change.values) &
             {"inlet_node", "outlet_node", "inlet_offset", "outlet_offset"})))
        affects_node_invert = (section in _NODE_SECTIONS and
            (change.action != "update" or "invert_elevation" in change.values))
        if affects_ends or affects_node_invert:
            raise ValueError("Legacy finished changes affecting hydraulic pipe ends are unsupported "
                             "with lowest_applied_pipe_end floor inference")


def compile_handoff(document: SWMMDocument, prepared: Mapping[str, Any],
                    config: Mapping[str, Any], *, config_sha256: str,
                    source_manifest: Any = None,
                    crosswalk_snapshot: Mapping[str, Any] | None = None,
                    decision_instructions: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
    crosswalk_snapshot : mapping, optional
        Frozen target-identity crosswalk already read by orchestration.
    decision_instructions : mapping, optional
        Frozen consolidated-ledger changes, evidence and side-input hashes.
        Cannot be combined with the legacy model.changes_path input.

    Returns
    -------
    dict
        Versioned JSON handoff with serialized SHMM changes/vertices, source-keyed
        evidence and explicit writer-capability holds. Omission never requests deletion.

    Notes
    -----
    Existing junction floors and entire existing storage profiles are preserved.
    Gravity and explicitly parameterized force-main links may reference existing
    storage without writing its card or curve. New storage and outfall endpoints
    remain unsupported. Pressure connections require explicit roles or an enabled
    internal-connection policy; open discharge and wet-well cards are preserved.
    Changing a floor and all its dependencies is intentionally held by this
    adapter. Existing hydraulic pipe updates require exact directed endpoint
    identity and circular shape; geometry replacement
    remains a downstream capability gap.
    """
    _validate_model_basis(document, config)
    target = config["model"]["target"]
    length_unit, map_unit = target["length_unit"], target["map_unit"]
    junctions = {_key(r.tokens[0]): r for r in document.records("JUNCTIONS")}
    storages = {_key(r.tokens[0]): r for r in document.records("STORAGE")}
    baseline_profiles = {_key(identity): values for identity, values in baseline_node_parameters(document, config).items()}
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
    pump_ends = {}
    for pump in document.records("PUMPS"):
        pump_ends.setdefault(tuple(map(_key, pump.tokens[1:3])), []).append(pump.tokens[0])
        pump_ends.setdefault(tuple(map(_key, reversed(pump.tokens[1:3]))), []).append(pump.tokens[0])
    seen_targets = set()
    for pipe in source_pipes:
        name = resolve_model_identity(pipe, "pipes", config, crosswalk)[0]
        upstream, downstream = nodes[pipe["from_node"]], nodes[pipe["to_node"]]
        ends = (target_node(upstream), target_node(downstream))
        reason = None
        if _key(name) in seen_targets:
            raise ValueError("Two prepared source pipes target the same model identity")
        seen_targets.add(_key(name))
        if pipe.get("pipe_kind") == "force_main":
            reason = pressure_contract_reason(pipe, document)
            if tuple(map(_key, ends)) in pump_ends:
                reason = "force_main_matches_existing_pump_abstraction"
            if reason is None and any(row.get("pressure_connection", {}).get("role") not in {"sealed", "open_discharge", "wet_well"} for row in (upstream, downstream)):
                reason = "unresolved_pressure_connection_role"
        if reason is not None:
            pass
        elif any(_key(node) in node_namespace and _key(node) not in junctions and _key(node) not in storages for node in ends):
            reason = "existing_nonjunction_endpoint_requires_supported_parameter_contract"
        elif any(row.get("model_section") == "STORAGE" and _key(node) not in storages for row, node in zip((upstream, downstream), ends)):
            reason = "new_storage_requires_supported_parameter_contract"
        elif any(_key(node) in storages and baseline_profiles[_key(node)]["max_depth_m"] <= 0 for node in ends):
            reason = "existing_storage_profile_requires_positive_depth"
        elif any(_key(node) in storages and _key(node) not in coords for node in ends):
            reason = "existing_storage_endpoint_requires_baseline_coordinates"
        elif _key(name) in link_namespace and _key(name) not in links:
            reason = "existing_nonconduit_target_requires_supported_operation"
        elif pipe.get("shape") not in {"CIRCULAR", "FORCE_MAIN"}:
            reason = "prepared_shape_requires_additional_model_adapter"
        elif _key(name) in links:
            baseline = links[_key(name)]
            if tuple(map(_key, baseline.tokens[1:3])) != tuple(map(_key, ends)):
                reason = "directed_rewire_is_not_supported"
            elif _key(name) not in sections or sections[_key(name)].tokens[1].upper() != pipe.get("shape"):
                reason = "existing_shape_conversion_is_not_supported"
        if reason:
            holds.append({"source_key": pipe["key"], "asset_id": pipe["asset_id"], "reason": reason})
        else:
            accepted.append((name, pipe, upstream, downstream))
    # Source GIS and the baseline can each omit a gravity/station connection
    # present in the other. Infer sealing only against their final combined
    # connection context; an explicit reviewed sealed role remains authoritative.
    nonpressure_nodes = set()
    for record in document.records("CONDUITS"):
        shape = sections.get(_key(record.tokens[0]))
        if shape is None or shape.tokens[1].upper() != "FORCE_MAIN":
            nonpressure_nodes.update(map(_key, record.tokens[1:3]))
    for section in ("WEIRS", "ORIFICES", "OUTLETS"):
        for record in document.records(section):
            nonpressure_nodes.update(map(_key, record.tokens[1:3]))
    for record in document.records("PUMPS"):
        nonpressure_nodes.add(_key(record.tokens[1]))
    for _, pipe, upstream, downstream in accepted:
        if pipe.get("pipe_kind") != "force_main":
            nonpressure_nodes.update((_key(target_node(upstream)), _key(target_node(downstream))))
    filtered = []
    for item in accepted:
        name, pipe, upstream, downstream = item
        conflict = pipe.get("pipe_kind") == "force_main" and any(
            row.get("pressure_connection", {}).get("method") == "internal_force_main_only"
            and _key(target_node(row)) in nonpressure_nodes for row in (upstream, downstream))
        if conflict:
            holds.append({"source_key": pipe["key"], "asset_id": pipe["asset_id"],
                          "reason": "inferred_sealed_connection_has_nonpressure_model_context"})
        else:
            filtered.append(item)
    accepted = filtered
    needed = {n["asset_id"] for _, _, upstream, downstream in accepted for n in (upstream, downstream)}
    seen_node_targets = {}
    for identity in sorted(needed):
        target_identity = _key(target_node(nodes[identity]))
        if target_identity in seen_node_targets and seen_node_targets[target_identity] != identity:
            raise ValueError("Multiple source nodes target the same model identity")
        seen_node_targets[target_identity] = identity
    pressure_changes, pressure_evidence, pressure_review = baseline_pressure_plan(document, config,
        added_connections=[{"model_id": name, "pipe_kind": pipe.get("pipe_kind", "gravity"),
            "from_node": target_node(upstream), "to_node": target_node(downstream)}
            for name, pipe, upstream, downstream in accepted if _key(name) not in links])
    changes, vertices, evidence = list(pressure_changes), [], dict(pressure_evidence)
    pressure_needed = {node["asset_id"] for _, pipe, upstream, downstream in accepted if pipe.get("pipe_kind") == "force_main" for node in (upstream, downstream)}
    fields_units = {"invert_elevation": length_unit, "max_depth": length_unit, "length": length_unit,
                    "inlet_offset": length_unit, "outlet_offset": length_unit, "geom1": length_unit,
                    "roughness": "Manning n", "surcharge_depth": length_unit, "x": map_unit, "y": map_unit, "vertices": map_unit}
    for identity in sorted(needed):
        row = nodes[identity]
        name, source_key = target_node(row), row["key"]
        floor = from_metres(row["floor_m"], length_unit)
        depth = from_metres(row["max_depth_m"], length_unit)
        placeholder = row.get("assignments", {}).get("rim", {}).get("method") == "missing_rim_placeholder"
        comment = None
        if placeholder:
            declared = config["parameters"]["nodes"]["missing_rim"]["placeholder"]
            # Preserve the literal native sentinel. Subtracting rim - invert and
            # converting it back can otherwise serialize 12.344999999999999.
            depth = declared["value"] if declared["unit"] in {"model", length_unit} else from_metres(to_metres(declared["value"], declared["unit"]), length_unit)
            comment = f"UMDEV_PLACEHOLDER_RIM MaxDepth={depth} {length_unit}; synthetic rim, review required"
        surcharge_m = row.get("surcharge_depth_m") if identity in pressure_needed and row.get("pressure_connection", {}).get("role") == "sealed" else None
        evidence[source_key] = {"asset_id": identity, "assignments": row.get("assignments", {}),
            "units": fields_units, "pressure_connection": row.get("pressure_connection"), "surcharge_depth_m": surcharge_m if identity in pressure_needed else None, "qa_flags": [f for f in prepared.get("findings", []) if f.get("object_key") == source_key]}
        if identity in pressure_needed:
            model_roles = config.get("parameters", {}).get("nodes", {}).get("pressure_connections", {}).get("model_node_overrides", {})
            configured_role = next((role for key, role in model_roles.items() if _key(key) == _key(name)), None)
            if configured_role is not None and configured_role != row["pressure_connection"]["role"]:
                raise ValueError("Source and model pressure-connection roles conflict")
            # Accepted, explicitly prepared node roles take precedence over an
            # automatic baseline classification. Open/wet-well roles never
            # inherit a sealed-node change merely because force mains touch it.
            changes = [change for change in changes if not (change.source_key.startswith("pressure/baseline/") and _key(change.object_id) == _key(name))]
            evidence.pop("pressure/baseline/" + name, None)
            pressure_review = [item for item in pressure_review if _key(item["model_id"]) != _key(name)]
            existing_fields = interpret_fields("JUNCTIONS", junctions[_key(name)]) if _key(name) in junctions else None
            original_surcharge = float(existing_fields["surcharge_depth"]) if existing_fields else 0.0 if _key(name) not in storages else None
            expected_surcharge = from_metres(surcharge_m, length_unit) if surcharge_m is not None else original_surcharge
            pressure_review.append({"model_id": name, **row["pressure_connection"],
                "previous_surcharge_depth": original_surcharge, "expected_surcharge_depth": expected_surcharge,
                "unit": length_unit, "changed": expected_surcharge != original_surcharge})
        if _key(name) in storages:
            # Existing storage is only an endpoint reference. Never turn it into
            # a junction or alter its stage/area curve, depth, losses or map point.
            old = baseline_profiles[_key(name)]
            if not math.isclose(row["floor_m"], old["floor_m"], rel_tol=0, abs_tol=1e-9) or not math.isclose(row["rim_m"], old["rim_m"], rel_tol=0, abs_tol=1e-9):
                raise NotImplementedError("Existing STORAGE floor/rim edits are unsupported; preserve its profile")
        elif _key(name) in junctions:
            old = interpret_fields("JUNCTIONS", junctions[_key(name)])
            if not math.isclose(floor, float(old["invert_elevation"]), rel_tol=0, abs_tol=1e-9):
                raise NotImplementedError("Existing floor edits require complete dependent-end preservation")
            if not math.isclose(depth, float(old["max_depth"]), rel_tol=0, abs_tol=1e-9):
                changes.append(Change("update", "JUNCTIONS", name, {"max_depth": depth}, source_key,
                                      {"max_depth": float(old["max_depth"])}))
            if surcharge_m is not None:
                requested = from_metres(surcharge_m, length_unit)
                if not math.isclose(requested, float(old["surcharge_depth"]), rel_tol=0, abs_tol=1e-9):
                    changes = [change for change in changes if not (change.section == "JUNCTIONS" and _key(change.object_id) == _key(name) and "surcharge_depth" in change.values)]
                    changes.append(Change("update", "JUNCTIONS", name, {"surcharge_depth": requested}, source_key, {"surcharge_depth": float(old["surcharge_depth"])}))
        else:
            new_values = {"invert_elevation": floor, "max_depth": depth}
            if surcharge_m is not None:
                new_values["surcharge_depth"] = from_metres(surcharge_m, length_unit)
            changes.append(Change("add", "JUNCTIONS", name, new_values, source_key, comment=comment))
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
            "roughness_assignment": pipe.get("roughness_assignment"), "pressure_assignment": pipe.get("pressure_assignment"), "length_method": pipe.get("length_method"),
            "diameter_method": pipe.get("diameter_method"), "units": fields_units,
            "qa_flags": [f for f in prepared.get("findings", []) if f.get("object_key") == source_key]}
        if pipe.get("pipe_kind") == "force_main":
            evidence[source_key]["units"] = {**fields_units, "geom2": "dimensionless H-W C" if pipe["pressure_assignment"]["equation"] == "H-W" else "in" if length_unit == "ft" else "mm"}
        if _key(name) in links:
            old = interpret_fields("CONDUITS", links[_key(name)])
            editable = {field: value for field, value in values.items() if field not in {"inlet_node", "outlet_node"}}
            changes.append(Change("update", "CONDUITS", name, editable, source_key,
                                  {field: float(old[field]) for field in editable}))
            xvalues = {"geom1": diameter}
            if pipe.get("shape") == "FORCE_MAIN":
                assignment = pipe["pressure_assignment"]
                xvalues["geom2"] = assignment["value"] if assignment["equation"] == "H-W" else from_metres(assignment["value"], "in" if length_unit == "ft" else "mm")
            changes.append(Change("update", "XSECTIONS", name, xvalues, source_key,
                                  {field: float(sections[_key(name)].tokens[2 if field == "geom1" else 3]) for field in xvalues}))
        else:
            changes.append(Change("add", "CONDUITS", name, values, source_key))
            pressure = pipe.get("pressure_assignment", {})
            coefficient = pressure.get("value", 0)
            if pressure.get("equation") == "D-W":
                coefficient = from_metres(coefficient, "in" if length_unit == "ft" else "mm")
            changes.append(Change("add", "XSECTIONS", name,
                {"shape": pipe["shape"], "geom1": diameter, "geom2": coefficient, "geom3": 0, "geom4": 0}, source_key))
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
    if decision_instructions is not None:
        if config["model"].get("changes_path"):
            raise ValueError("Consolidated decision instructions cannot be combined with legacy model.changes_path")
        from copy import deepcopy
        rows = decision_instructions.get("changes", [])
        extra_evidence = decision_instructions.get("evidence", {})
        extra_inputs = decision_instructions.get("side_input_hashes", {})
        if not isinstance(rows, list) or not isinstance(extra_evidence, Mapping) or not isinstance(extra_inputs, Mapping):
            raise ValueError("Decision instructions require changes, evidence and side-input hash mappings")
        extras = [Change(**deepcopy(row)) for row in rows]
        # A reviewed instruction cannot silently override an automatically
        # prepared instruction targeting the same model object field.
        prepared_targets = {(change.section.upper(), _key(change.object_id), field)
                            for change in changes for field in change.values}
        for change in extras:
            if any((change.section.upper(), _key(change.object_id), field) in prepared_targets for field in change.values):
                raise ValueError("Decision instruction conflicts with a prepared model field")
    else:
        extras, extra_evidence, extra_inputs = _extra_instructions(config)
        _guard_legacy_applied_floor_context(extras, config)
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
            "pressure_nodes": pressure_review,
            "force_main_accounting": [{"source_key": pipe["key"], "asset_id": pipe["asset_id"],
                "represented_pumps": pump_ends.get(tuple(_key(target_node(nodes[pipe[field]])) for field in ("from_node", "to_node") if pipe.get(field) in nodes), []),
                "disposition": "applied" if pipe["key"] in {p["key"] for _, p, _, _ in accepted} else "hold",
                "preparation_reasons": pipe.get("reasons", [])}
                for pipe in prepared.get("pipes", []) if pipe.get("pipe_kind") == "force_main"],
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
    profile_nodes = {_key(r.tokens[0]): r for section in ("JUNCTIONS", "STORAGE") for r in candidate.records(section)}
    conduits = {_key(r.tokens[0]): r for r in candidate.records("CONDUITS")}
    xsections = {_key(r.tokens[0]): r for r in candidate.records("XSECTIONS")}
    nodes = {r["asset_id"]: r for r in prepared["nodes"]}
    applied = set(handoff["applied_source_keys"])
    checked_nodes, rows = set(), []
    checked_placeholders = set()
    new_junctions = {_key(change["object_id"]): change for change in handoff["changes"] if change["section"] == "JUNCTIONS" and change["action"] == "add"}
    for pipe in prepared["pipes"]:
        if pipe["key"] not in applied:
            continue
        name = handoff["model_ids"]["pipes"][pipe["key"]]
        observed = conduits[_key(name)]
        node_values = []
        for end, field, column in (("upstream", "from_node", 5), ("downstream", "to_node", 6)):
            node = nodes[pipe[field]]
            model_id = handoff["model_ids"]["nodes"][node["asset_id"]]
            raw = profile_nodes[_key(model_id)]
            floor = to_metres(float(raw.tokens[1]), unit)
            depth = to_metres(float(raw.tokens[2]) if len(raw.tokens) > 2 else 0, unit)
            port = floor + to_metres(float(observed.tokens[column]), unit) if config["model"]["target"]["link_offsets"] == "DEPTH" else to_metres(float(observed.tokens[column]), unit)
            for value, expected, label in ((floor, node["floor_m"], "floor"),
                    (floor + depth, node["rim_m"], "rim"), (port, pipe[end + "_invert_m"], "pipe-end invert")):
                if not math.isclose(value, expected, rel_tol=0, abs_tol=tolerance):
                    raise ValueError(f"Written {label} differs from prepared evidence")
            if node.get("assignments", {}).get("rim", {}).get("method") == "missing_rim_placeholder":
                expected_depth = str(new_junctions[_key(model_id)]["values"]["max_depth"])
                if raw.tokens[2] != expected_depth:
                    raise ValueError("Written placeholder MaxDepth lost its literal sentinel")
                line = candidate.lines[raw.line_index]
                if "UMDEV_PLACEHOLDER_RIM" not in line or "MaxDepth=" + expected_depth not in line:
                    raise ValueError("Written placeholder rim marker is missing")
                checked_placeholders.add(model_id)
            checked_nodes.add(model_id)
            node_values.append(port)
        cross_section = xsections[_key(name)]
        if cross_section.tokens[1].upper() != pipe["shape"]:
            raise ValueError("Written pipe shape differs from prepared evidence")
        if pipe.get("pipe_kind") == "force_main":
            reason = pressure_contract_reason(pipe, candidate)
            if reason:
                raise ValueError("Written force-main option/parameter mismatch: " + reason)
            coefficient = float(cross_section.tokens[3])
            assignment = pipe["pressure_assignment"]
            if assignment["equation"] == "D-W":
                coefficient = to_metres(coefficient, "in" if unit == "ft" else "mm")
            if not math.isclose(coefficient, assignment["value"], rel_tol=0, abs_tol=tolerance):
                raise ValueError("Written force-main coefficient differs from prepared evidence")
            if not math.isclose(float(observed.tokens[4]), pipe["roughness"], rel_tol=0, abs_tol=1e-12):
                raise ValueError("Written conduit Manning differs from prepared evidence")
        diameter = to_metres(float(xsections[_key(name)].tokens[2]), unit)
        length = to_metres(float(observed.tokens[3]), unit)
        if not math.isclose(diameter, pipe["diameter_m"], rel_tol=0, abs_tol=tolerance) or not math.isclose(length, pipe["length_m"], rel_tol=0, abs_tol=tolerance):
            raise ValueError("Written pipe dimensions differ from prepared evidence")
        grade = (node_values[0] - node_values[1]) / length
        if not math.isclose(grade, pipe["grade"], rel_tol=0, abs_tol=2 * tolerance / length):
            raise ValueError("Written pipe grade differs from prepared evidence")
        rows.append({"source_key": pipe["key"], "model_id": name, "inlet_invert_m": node_values[0],
                     "outlet_invert_m": node_values[1], "grade": grade})
    for pressure_node in handoff.get("pressure_nodes", []):
        expected = pressure_node.get("expected_surcharge_depth")
        if expected is None:
            continue
        actual = interpret_fields("JUNCTIONS", profile_nodes[_key(pressure_node["model_id"])])
        if not math.isclose(float(actual["surcharge_depth"]), expected, rel_tol=0, abs_tol=from_metres(tolerance, unit)):
            raise ValueError("Written surcharge depth differs from pressure-connection policy")
    for change in handoff["changes"]:
        if change["section"] == "JUNCTIONS" and "surcharge_depth" in change["values"]:
            observed = interpret_fields("JUNCTIONS", profile_nodes[_key(change["object_id"])])
            if not math.isclose(float(observed["surcharge_depth"]), change["values"]["surcharge_depth"], rel_tol=0, abs_tol=from_metres(tolerance, unit)):
                raise ValueError("Written surcharge depth differs from pressure-connection policy")
    return {"checked_nodes": len(checked_nodes), "checked_pipes": len(rows), "profiles": rows, "checked_placeholder_nodes": len(checked_placeholders), "checked_surcharge_instructions": sum(change["section"] == "JUNCTIONS" and "surcharge_depth" in change["values"] for change in handoff["changes"])}


def candidate_engineering_qa(document: SWMMDocument, config: Mapping[str, Any], *,
                             placeholder_node_ids: Sequence[str] = ()) -> list[dict[str, Any]]:
    """QA actual written node/pipe profiles and complete modeled connectivity.

    Notes
    -----
    Structured profile checks cover standard node invert/depth positions and
    CIRCULAR/FORCE_MAIN conduit heights. Other link types participate in the
    directed connectivity graph without invented hydraulic parameters.
    """
    from .qa import finding, profile_findings
    from .topology import diagnose_swmm_topology
    unit = config["model"]["target"]["length_unit"]
    convention = config["model"]["target"]["link_offsets"]
    nodes, floors = [], {}
    placeholder_ids = {_key(identity) for identity in placeholder_node_ids}
    for section in _NODE_SECTIONS:
        for record in document.records(section):
            floor = to_metres(float(record.tokens[1]), unit)
            floors[_key(record.tokens[0])] = floor
            row = {"key": f"model/{section}/{record.tokens[0]}", "asset_id": _key(record.tokens[0]), "display_id": record.tokens[0],
                   "floor_m": floor, "rim_m": None, "max_depth_m": None,
                   "has_placeholder_rim": section == "JUNCTIONS" and _key(record.tokens[0]) in placeholder_ids}
            if section in {"JUNCTIONS", "STORAGE"}:
                depth = to_metres(float(record.tokens[2]) if len(record.tokens) > 2 else 0, unit)
                row.update(rim_m=floor + depth, max_depth_m=depth)
            nodes.append(row)
    shapes = {_key(r.tokens[0]): r for r in document.records("XSECTIONS")}
    profiles = []
    for section in _LINK_SECTIONS:
        for record in document.records(section):
            base = {"key": f"model/{section}/{record.tokens[0]}", "asset_id": record.tokens[0],
                    "from_node": _key(record.tokens[1]), "to_node": _key(record.tokens[2]), "disposition": "include"}
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
    # Separate legitimate networks with outfalls from components lacking an
    # outlet route. Weak connectivity alone cannot establish hydraulic support.
    graph = diagnose_swmm_topology(document, document)
    connectivity = []
    for component in graph["components"]:
        missing = component["nodes_without_directed_terminal_route"]
        if not component["terminal_node_ids"]:
            rule = "isolated_model_node" if component["isolated_node"] else "component_without_outfall"
            connectivity.append(finding(rule, component["node_ids"][0], config,
                node_ids=component["node_ids"], link_ids=component["link_ids"],
                nominal_directed_topology=True))
        elif missing:
            connectivity.append(finding("nodes_without_directed_outfall_route",
                missing[0], config, node_ids=missing, component_node_ids=component["node_ids"],
                nominal_directed_topology=True))
        elif len(graph["components"]) > 1:
            connectivity.append({"rule": "independent_outfall_component", "rule_version": 1,
                "object_key": component["node_ids"][0], "severity": "info",
                "disposition": "informational", "review_status": "not_required",
                "node_ids": component["node_ids"], "outfall_ids": component["terminal_node_ids"],
                "nominal_directed_topology": True})
    return profile_findings(nodes, profiles, config) + connectivity


def placeholder_node_register(prepared: Mapping[str, Any], handoff: Mapping[str, Any],
                              config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List applied and held synthetic rims with their native SWMM sentinel.

    Notes
    -----
    SWMM JUNCTIONS always writes maximum depth above invert, independent of the
    link offset convention. The original sentinel is kept in native model units
    so a reviewer can recognize it directly in INP text. Source rims remain absent.
    """
    unit = config["model"]["target"]["length_unit"]
    declaration = config.get("parameters", {}).get("nodes", {}).get("missing_rim", {})
    applied = handoff.get("model_ids", {}).get("nodes", {})
    result = []
    for node in prepared.get("nodes", []):
        assignment = node.get("assignments", {}).get("rim", {})
        if assignment.get("method") != "missing_rim_placeholder":
            continue
        quantity = declaration["placeholder"]
        depth = quantity["value"] if quantity["unit"] in {"model", unit} else from_metres(to_metres(quantity["value"], quantity["unit"]), unit)
        result.append({"source_key": node["key"], "asset_id": node["asset_id"],
            "model_id": applied.get(node["asset_id"], node.get("model_id")),
            "disposition": "applied" if node["asset_id"] in applied else "held",
            "field": "max_depth", "placeholder_kind": quantity["kind"],
            "placeholder_value": quantity["value"], "configured_unit": quantity["unit"],
            "model_max_depth": depth, "unit": unit,
            "floor_m": node.get("floor_m"), "rim_m": node.get("rim_m"),
            "max_depth_m": node.get("max_depth_m"), "basis": declaration["basis"],
            "assignment": assignment, "source_rim_value": node.get("rim"), "source_rim_unchanged": True})
    return result
