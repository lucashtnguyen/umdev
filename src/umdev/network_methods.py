"""Audited elevation inference and review-gated simplification of a finished network.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained numerical preparation helpers; no model files are written.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


Network = Mapping[str, Mapping[str, Any]]


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    """Validate a finite real engineering quantity, excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{label} must be finite" + (" and positive" if positive else ""))
    return result


def _kind(record: Mapping[str, Any]) -> str:
    return str(record.get("kind", "unknown")).casefold()


def _protected(record: Mapping[str, Any]) -> bool:
    return any(bool(record.get(field)) for field in (
        "protected", "explicit_drop", "has_inflow", "inflows", "inflow", "controls",
        "control", "boundary", "keep_explicit", "has_control", "has_loading",
        "pressure", "sealed", "wet_well", "is_pump_station",
    ))


def _validate(nodes: Network, links: Network) -> tuple[dict, dict, dict]:
    """Copy and validate endpoints, node floors and gravity hydraulic lengths."""
    node_copy, link_copy = deepcopy(dict(nodes)), deepcopy(dict(links))
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for node_id, node in node_copy.items():
        if not isinstance(node_id, str) or not isinstance(node, Mapping):
            raise ValueError("Node IDs must be strings and records must be mappings")
        if node.get("invert") is not None:
            _number(node["invert"], f"Node {node_id} invert")
    for link_id, link in link_copy.items():
        if not isinstance(link_id, str) or not isinstance(link, Mapping):
            raise ValueError("Link IDs must be strings and records must be mappings")
        start, end = link.get("from_node"), link.get("to_node")
        if start not in node_copy or end not in node_copy:
            raise ValueError(f"Link {link_id} has an undeclared endpoint")
        if start == end:
            raise ValueError(f"Link {link_id} is a self loop")
        incoming[end].append(link_id)
        outgoing[start].append(link_id)
        if _kind(link) == "gravity":
            _number(link.get("length"), f"Link {link_id} length", positive=True)
            if link.get("diameter") is not None:
                _number(link["diameter"], f"Link {link_id} diameter", positive=True)
        for field in ("inlet_offset", "outlet_offset"):
            offset = _number(link.get(field, 0.0), f"Link {link_id} {field}")
            if offset < 0:
                raise ValueError(f"Link {link_id} {field} must be nonnegative depth")
    adjacency = {key: (sorted(incoming[key]), sorted(outgoing[key])) for key in node_copy}
    return node_copy, link_copy, adjacency


def _trace_anchor(
    node_id: str, direction: str, nodes: Network, links: Network, adjacency: dict,
    tolerance: float,
) -> dict[str, Any]:
    """Follow a serial gravity reach to a known endpoint without crossing a drop."""
    current, distance, path = node_id, 0.0, []
    visited = {current}
    while True:
        incoming, outgoing = adjacency[current]
        candidates = incoming if direction == "upstream" else outgoing
        if len(incoming) > 1 or len(outgoing) > 1:
            return {"status": "branch", "at_node": current, "path": path}
        if not candidates:
            return {"status": "dead_end", "distance": distance, "path": path}
        # All links participate in degree checks, including pumps/unknown families.
        if len(candidates) != 1 or len(incoming) > 1 or len(outgoing) > 1:
            return {"status": "branch", "at_node": current, "path": path}
        link_id = candidates[0]
        link = links[link_id]
        if _kind(link) != "gravity":
            return {"status": "non_gravity_boundary", "at_link": link_id, "path": path}
        if _protected(link):
            return {"status": "protected_link", "at_link": link_id, "path": path}
        neighbor = link["from_node"] if direction == "upstream" else link["to_node"]
        distance += float(link["length"])
        path.append(link_id)
        if neighbor in visited:
            return {"status": "cycle", "at_node": neighbor, "path": path}
        visited.add(neighbor)
        node = nodes[neighbor]
        if node.get("invert") is not None:
            offset_field = "inlet_offset" if direction == "upstream" else "outlet_offset"
            return {"status": "anchor", "node_id": neighbor,
                    "elevation": float(node["invert"]) + float(link.get(offset_field, 0.0)),
                    "floor_elevation": float(node["invert"]),
                    "anchor_offset": float(link.get(offset_field, 0.0)),
                    "distance": distance, "path": path}
        if _protected(node) or _kind(node) not in {"junction", "unknown"}:
            return {"status": "protected_node", "at_node": neighbor, "path": path}
        local_in, local_out = adjacency[neighbor]
        if any(float(links[key].get("outlet_offset", 0.0)) > tolerance for key in local_in) or any(
            float(links[key].get("inlet_offset", 0.0)) > tolerance for key in local_out
        ):
            return {"status": "explicit_offset", "at_node": neighbor, "path": path}
        current = neighbor


def infer_missing_inverts(
    nodes: Network, links: Network, *, extrapolation_slope: float | None = None,
    anchor_tolerance: float = 1e-8, length_unit: str = "model",
) -> dict[str, Any]:
    """Infer only missing floors along unambiguous directed gravity chains.

    Parameters
    ----------
    nodes : mapping
        Node ID to records with ``invert`` (number or None), optional ``kind``
        (junction by default), and protection fields described in the README.
    links : mapping
        Link ID to records with ``from_node``, ``to_node``, ``kind`` and positive
        gravity ``length``. Optional offsets are nonnegative DEPTH offsets.
    extrapolation_slope : float, optional
        Explicit positive fall/length assumption for a chain with one known
        anchor and a genuine dead end. No default extrapolation is performed.
    anchor_tolerance : float
        Elevation tolerance in the caller's common length unit.
    length_unit : str
        Traceability label only (for example ft or m); no unit conversion occurs.

    Returns
    -------
    dict
        Deep-copied ``nodes``, per-node ``assignments`` with anchor/path lineage,
        and ``unresolved`` diagnostics. Input records are never modified.

    Notes
    -----
    All lengths, offsets and elevations must share one unit and vertical datum.
    Interpolation is z = z_up + (z_down - z_up) L_up / (L_up + L_down), using
    hydraulic lengths rather than straight-line geometry. Anchor elevations
    include the relevant pipe-end offset. Missing intermediate floors imply
    zero offsets; branches, explicit drops, pumps, cycles and adverse known
    anchor grades are held. Flat anchors are permitted but explicitly flagged.
    This function performs geometric preparation, not hydraulic validation.
    """
    if not isinstance(length_unit, str) or not length_unit.strip():
        raise ValueError("length_unit must be a nonempty unit label")
    tolerance = _number(anchor_tolerance, "anchor_tolerance")
    if tolerance < 0:
        raise ValueError("anchor_tolerance must be nonnegative")
    slope = None if extrapolation_slope is None else _number(
        extrapolation_slope, "extrapolation_slope", positive=True
    )
    original, copied_links, adjacency = _validate(nodes, links)
    result = deepcopy(original)
    assignments, unresolved = [], []
    # Use only original known anchors, never an inferred value as fresh evidence.
    for node_id in sorted(original):
        node = original[node_id]
        if node.get("invert") is not None:
            continue
        incoming, outgoing = adjacency[node_id]
        reason = None
        if _protected(node) or _kind(node) not in {"junction", "unknown"}:
            reason = "protected_node"
        elif not incoming and not outgoing:
            reason = "isolated_node"
        elif len(incoming) > 1 or len(outgoing) > 1:
            reason = "branch"
        elif any(float(copied_links[key].get("outlet_offset", 0.0)) > tolerance for key in incoming) or any(
            float(copied_links[key].get("inlet_offset", 0.0)) > tolerance for key in outgoing
        ):
            reason = "explicit_offset"
        if reason:
            unresolved.append({"node_id": node_id, "reason": reason})
            continue
        upstream = _trace_anchor(node_id, "upstream", original, copied_links, adjacency, tolerance)
        downstream = _trace_anchor(node_id, "downstream", original, copied_links, adjacency, tolerance)
        base = {"node_id": node_id, "upstream": upstream, "downstream": downstream}
        flags = ["assumed_invert"]
        if upstream["status"] == downstream["status"] == "anchor":
            total_length = upstream["distance"] + downstream["distance"]
            fall = upstream["elevation"] - downstream["elevation"]
            if fall < -tolerance:
                unresolved.append({**base, "reason": "adverse_anchor_grade"})
                continue
            # Do not flatten or otherwise change measured/reviewed anchors.
            elevation = upstream["elevation"] - fall * upstream["distance"] / total_length
            method = "length_weighted_interpolation"
            if abs(fall) <= tolerance:
                flags.append("flat_anchor_grade")
        elif slope is not None and {upstream["status"], downstream["status"]} == {"anchor", "dead_end"}:
            anchor = upstream if upstream["status"] == "anchor" else downstream
            sign = -1 if upstream["status"] == "anchor" else 1
            elevation = anchor["elevation"] + sign * slope * anchor["distance"]
            method = "configured_slope_extrapolation"
            flags.append("provisional_extrapolation")
        else:
            unresolved.append({**base, "reason": "no_unambiguous_anchor_pair"})
            continue
        if not math.isfinite(elevation):
            unresolved.append({**base, "reason": "nonfinite_inference"})
            continue
        assignment = {**base, "value": elevation, "method": method, "flags": flags,
                      "length_unit": length_unit,
                      "extrapolation_slope": slope if method == "configured_slope_extrapolation" else None}
        result[node_id]["invert"] = elevation
        result[node_id]["assumptions"] = [*result[node_id].get("assumptions", []), deepcopy(assignment)]
        assignments.append(assignment)
    known = {key: node["invert"] for key, node in original.items() if node.get("invert") is not None}
    return {"nodes": result, "assignments": assignments, "unresolved": unresolved,
            "length_unit": length_unit,
            "preservation": {"known_node_inverts": known, "known_values_preserved": all(result[key]["invert"] == value for key, value in known.items())}}


def _merge_pair(
    short_id: str, neighbor_id: str, middle: str, nodes: Network, links: Network,
    protected_nodes: set[str], keep_ids: set[str], tolerance: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """Check a serial gravity pair and build a traceable review proposal."""
    short, neighbor = links[short_id], links[neighbor_id]
    first_id, second_id = (short_id, neighbor_id) if short["to_node"] == middle else (neighbor_id, short_id)
    first, second = links[first_id], links[second_id]
    node = nodes[middle]
    if middle in protected_nodes or _protected(node) or _kind(node) not in {"junction", "unknown"}:
        return None, "protected_node"
    if neighbor_id in keep_ids or _protected(short) or _protected(neighbor):
        return None, "protected_link"
    if _kind(first) != "gravity" or _kind(second) != "gravity":
        return None, "non_gravity_pair"
    if first.get("diameter") is None or second.get("diameter") is None or not first.get("material") or not second.get("material"):
        return None, "unknown_diameter_or_material"
    for field in ("diameter", "material", "shape", "roughness", "manning_n", "barrels", "cross_section"):
        if first.get(field) != second.get(field):
            return None, "changed_" + field
    if ("vertices" in first or "vertices" in second) and node.get("coordinates") is None:
        return None, "missing_middle_geometry"
    if any(abs(float(record.get(field, 0.0))) > tolerance for record, field in (
        (first, "outlet_offset"), (second, "inlet_offset")
    )):
        return None, "intermediate_drop"
    if any(record.get(field) not in (None, 0, 0.0, False, "NO") for record in (first, second) for field in (
        "initial_flow", "flow_limit", "entry_loss", "exit_loss", "average_loss", "seepage", "flap_gate"
    )):
        return None, "protected_hydraulic_feature"
    if first.get("to_node") != second.get("from_node") or first["from_node"] == second["to_node"]:
        return None, "non_serial_pair"
    outer_start, outer_end = first["from_node"], second["to_node"]
    if any(nodes[key].get("invert") is None for key in (outer_start, middle, outer_end)):
        return None, "unknown_profile"
    start_invert = float(nodes[outer_start]["invert"]) + float(first.get("inlet_offset", 0.0))
    middle_invert = float(node["invert"]) + float(first.get("outlet_offset", 0.0))
    end_invert = float(nodes[outer_end]["invert"]) + float(second.get("outlet_offset", 0.0))
    if middle_invert > start_invert + tolerance or middle_invert < end_invert - tolerance:
        return None, "nonmonotone_profile"
    lengths = [float(first["length"]), float(second["length"])]
    if not math.isfinite(sum(lengths)):
        return None, "nonfinite_total_length"
    source_ids = [*first.get("source_link_ids", [first_id]), *second.get("source_link_ids", [second_id])]
    merge_id = "merge_" + hashlib.sha256((first_id + "\0" + second_id + "\0" + middle).encode()).hexdigest()[:16]
    return {"merge_id": merge_id, "status": "proposed", "absorbed_link_id": short_id,
            "retained_link_id": neighbor_id, "ordered_link_ids": [first_id, second_id],
            "removed_node_id": middle, "from_node": outer_start, "to_node": outer_end,
            "source_link_ids": source_ids, "source_lengths": lengths, "length": sum(lengths),
            "inlet_offset": float(first.get("inlet_offset", 0.0)),
            "outlet_offset": float(second.get("outlet_offset", 0.0)),
            "upstream_invert": start_invert, "downstream_invert": end_invert,
            "removed_floor": node["invert"], "flags": ["short_pipe_simplification"],
            "source_records": {key: deepcopy(links[key]) for key in (first_id, second_id)},
            "removed_node_record": deepcopy(node),
            "model_note": "UMDEV_SHORT_PIPE sources=" + ",".join(source_ids) + "; length=" + format(sum(lengths), ".12g")}, None


def simplify_short_pipes(
    nodes: Network, links: Network, *, mode: str = "explicit_all", threshold: float = 20.0,
    keep_link_ids: Iterable[str] = (), selected_link_ids: Iterable[str] = (),
    protected_node_ids: Iterable[str] = (), approved_merge_ids: Iterable[str] = (),
    drop_tolerance: float = 1e-6, length_unit: str = "model",
) -> dict[str, Any]:
    """Propose protected short-pipe merges and apply only individually approved pairs.

    Parameters
    ----------
    nodes, links : mapping
        Finished-network records described by ``infer_missing_inverts``. Gravity
        links also require known equal diameter/material and compatible sections.
    mode : {'explicit_all', 'selective', 'simplify_eligible'}
        Keep all pipes, consider selected IDs, or consider every eligible short pipe.
    threshold : float
        Strict upper hydraulic length threshold in the same unit as all lengths.
        Default 20 means 20 caller units, not an implicit feet conversion.
    keep_link_ids, selected_link_ids, protected_node_ids : iterable of str
        Explicit protection/selection. A kept pipe cannot absorb another pipe.
    approved_merge_ids : iterable of str
        Exact IDs from an earlier proposal pass. Unapproved proposals do not change
        the returned network. Changed source data invalidates prior approval IDs.
    drop_tolerance : float
        Maximum insignificant middle offset difference in the common length unit.
    length_unit : str
        Traceability label only; the threshold must already use this common unit.

    Returns
    -------
    dict
        Copied ``nodes``/``links``, disjoint ``merges`` (proposed/applied), and
        ``withheld`` candidate diagnostics. Applied records retain source snapshots,
        total hydraulic length, outer offsets/inverts and all source IDs.

    Notes
    -----
    Only serial gravity pairs with an unprotected degree-two middle junction can
    merge. Pumps, pressure mains, inflows, controls, boundaries, branches, explicit
    drops, section/material/roughness changes and unknown/adverse profiles prevent
    a merge. Downstream absorption is preferred deterministically. Each pass uses
    disjoint pairs; review and repeat for longer chains. Total hydraulic length is
    preserved; local profile curvature/storage are simplified and require review.
    SHMM remains responsible for INP edits/comments and QA export.
    """
    if not isinstance(length_unit, str) or not length_unit.strip():
        raise ValueError("length_unit must be a nonempty unit label")
    if mode not in {"explicit_all", "selective", "simplify_eligible"}:
        raise ValueError("Unknown simplification mode")
    limit = _number(threshold, "threshold", positive=True)
    tolerance = _number(drop_tolerance, "drop_tolerance")
    if tolerance < 0:
        raise ValueError("drop_tolerance must be nonnegative")
    copied_nodes, copied_links, adjacency = _validate(nodes, links)
    keep, selected, protected, approved = map(set, (keep_link_ids, selected_link_ids, protected_node_ids, approved_merge_ids))
    for supplied, valid, label in ((keep, copied_links, "keep_link_ids"), (selected, copied_links, "selected_link_ids"), (protected, copied_nodes, "protected_node_ids")):
        if supplied - valid.keys():
            raise ValueError(f"Unknown IDs in {label}: {sorted(supplied - valid.keys())}")
    proposals, withheld, reserved = [], [], set()
    for short_id in sorted(copied_links):
        short = copied_links[short_id]
        if mode == "explicit_all" or (mode == "selective" and short_id not in selected):
            continue
        if _kind(short) != "gravity" or float(short["length"]) >= limit:
            continue
        if short_id in keep or short_id in reserved:
            continue
        reasons = []
        # A degree-two node means exactly one incoming and one outgoing across
        # ALL families, so filtering force mains/pumps cannot hide a branch.
        for middle in (short["to_node"], short["from_node"]):
            incoming, outgoing = adjacency[middle]
            if len(incoming) != 1 or len(outgoing) != 1:
                reasons.append("not_serial_degree_two")
                continue
            neighbor_id = outgoing[0] if middle == short["to_node"] else incoming[0]
            if neighbor_id in reserved:
                reasons.append("reserved_pair")
                continue
            proposal, reason = _merge_pair(short_id, neighbor_id, middle, copied_nodes, copied_links, protected, keep, tolerance)
            if proposal is None:
                reasons.append(reason)
                continue
            # Bind approval to every source record, not just IDs or lengths.
            fingerprint = hashlib.sha256(json.dumps({"nodes": {key: copied_nodes[key] for key in (proposal["from_node"], middle, proposal["to_node"])}, "links": proposal["source_records"]}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            proposal["length_unit"] = length_unit
            proposal["source_sha256"] = fingerprint
            proposal["merge_id"] += "_" + fingerprint[:12]
            proposals.append(proposal)
            reserved.update(proposal["ordered_link_ids"])
            break
        else:
            withheld.append({"link_id": short_id, "reasons": sorted(set(reasons))})
    actual_ids = {row["merge_id"] for row in proposals}
    if approved - actual_ids:
        raise ValueError("Unknown or stale merge approvals: " + ", ".join(sorted(approved - actual_ids)))
    result_nodes, result_links = deepcopy(copied_nodes), deepcopy(copied_links)
    for proposal in proposals:
        if proposal["merge_id"] not in approved:
            continue
        proposal["status"] = "applied"
        retained, absorbed = proposal["retained_link_id"], proposal["absorbed_link_id"]
        merged = deepcopy(result_links[retained])
        first_id, second_id = proposal["ordered_link_ids"]
        first, second = copied_links[first_id], copied_links[second_id]
        merged.update({key: proposal[key] for key in ("from_node", "to_node", "length", "inlet_offset", "outlet_offset", "source_link_ids")})
        merged["removed_node_ids"] = [*first.get("removed_node_ids", []), proposal["removed_node_id"], *second.get("removed_node_ids", [])]
        merged["path_node_ids"] = [*first.get("path_node_ids", [first["from_node"], first["to_node"]]), *second.get("path_node_ids", [second["from_node"], second["to_node"]])[1:]]
        merged["assumptions"] = [*deepcopy(first.get("assumptions", [])), *deepcopy(second.get("assumptions", [])), deepcopy(proposal)]
        # Retain the eliminated map vertex when the caller supplies node XY.
        if "vertices" in first or "vertices" in second:
            middle_xy = copied_nodes[proposal["removed_node_id"]].get("coordinates")
            if middle_xy is None:
                raise ValueError("A merge with vertices requires middle-node coordinates")
            merged["vertices"] = [*deepcopy(first.get("vertices", [])), deepcopy(middle_xy), *deepcopy(second.get("vertices", []))]
        result_links[retained] = merged
        del result_links[absorbed]
        del result_nodes[proposal["removed_node_id"]]
    return {"nodes": result_nodes, "links": result_links, "merges": proposals, "withheld": withheld, "length_unit": length_unit}
