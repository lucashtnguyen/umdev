"""Diagnose utility network islands and nominal directed connectivity.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. Pure graph review: no spatial matching or model editing.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from hymo import SWMMDocument


@dataclass
class _Graph:
    """Internal indexed graph retaining declared and referenced-only nodes."""

    declared: dict[str, Mapping[str, Any]]
    display: dict[str, str]
    links: dict[str, Mapping[str, Any]]
    endpoints: dict[str, tuple[str, str]]
    weak: dict[str, set[str]]
    reverse: dict[str, list[tuple[str, str]]]


def _identifier(value: Any, label: str) -> str:
    """Require an explicit nonempty ID without coercing or trimming identity."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _comparison(value: str, mode: str) -> str:
    """Use declared identity semantics; Unicode case folding is never implicit."""
    return value.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")) if mode == "ascii_case_insensitive" else value


def _graph(nodes: Sequence[Mapping[str, Any]], links: Sequence[Mapping[str, Any]], mode: str) -> _Graph:
    """Index all explicitly supplied graph rows, including isolated nodes."""
    declared: dict[str, Mapping[str, Any]] = {}
    display: dict[str, str] = {}
    for row in nodes:
        identity = _identifier(row.get("asset_id"), "Node asset_id")
        key = _comparison(identity, mode)
        if key in declared:
            raise ValueError(f"Duplicate node identity under {mode}: {identity!r}")
        declared[key] = row
        display[key] = identity
    indexed_links: dict[str, Mapping[str, Any]] = {}
    endpoints = {}
    references: dict[str, set[str]] = defaultdict(set)
    weak = {key: set() for key in declared}
    reverse: dict[str, list[tuple[str, str]]] = {key: [] for key in declared}
    for row in links:
        identity = _identifier(row.get("asset_id"), "Link asset_id")
        key = _comparison(identity, mode)
        if key in indexed_links:
            raise ValueError(f"Duplicate link identity under {mode}: {identity!r}")
        start_raw = _identifier(row.get("from_node"), "Link from_node")
        end_raw = _identifier(row.get("to_node"), "Link to_node")
        start, end = _comparison(start_raw, mode), _comparison(end_raw, mode)
        indexed_links[key] = row
        endpoints[key] = start, end
        references[start].add(start_raw)
        references[end].add(end_raw)
        weak.setdefault(start, set()).add(end)
        weak.setdefault(end, set()).add(start)
        reverse.setdefault(end, []).append((start, key))
        reverse.setdefault(start, [])
    for key, spellings in references.items():
        display.setdefault(key, min(spellings))
    return _Graph(declared, display, indexed_links, endpoints, weak, reverse)


def _components(graph: _Graph, prefix: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Compute weak components in deterministic ID order, without recursion."""
    unseen = set(graph.weak)
    components = []
    membership = {}
    for seed in sorted(graph.weak):
        if seed not in unseen:
            continue
        pending, found = [seed], set()
        while pending:
            node = pending.pop()
            if node in found:
                continue
            found.add(node)
            unseen.discard(node)
            pending.extend(graph.weak[node] - found)
        identity = f"{prefix}{len(components) + 1:05d}"
        members = sorted(found)
        components.append({"component_id": identity, "comparison_node_ids": members,
                           "node_ids": [graph.display[node] for node in members]})
        membership.update({node: identity for node in members})
    by_id = {row["component_id"]: row for row in components}
    for component in components:
        component["link_ids"] = []
        component["link_families"] = {}
    for identity, endpoints in sorted(graph.endpoints.items()):
        component = by_id[membership[endpoints[0]]]
        row = graph.links[identity]
        component["link_ids"].append(row["asset_id"])
        family = str(row.get("section") or row.get("family") or "unspecified")
        component["link_families"][family] = component["link_families"].get(family, 0) + 1
    for component in components:
        component["node_count"] = len(component["node_ids"])
        component["link_count"] = len(component["link_ids"])
        component["isolated_node"] = component["link_count"] == 0
        component["link_families"] = dict(sorted(component["link_families"].items()))
    return components, membership


def _reachability(graph: _Graph, anchors: set[str]) -> dict[str, dict[str, Any]]:
    """Reverse-BFS nominal directed routes with deterministic shortest witnesses.

    Notes
    -----
    Storing one next-hop pointer avoids quadratic full-path output for long
    sewers. Routes use every supplied directed link regardless of family. They
    do not evaluate pump status, controls, valve physics, grade or capacity.
    """
    seen = {node: {"anchor": node, "next_node": None, "next_link": None, "edge_count": 0}
            for node in sorted(anchors & graph.weak.keys())}
    pending = deque(seen)
    while pending:
        node = pending.popleft()
        for predecessor, link in sorted(graph.reverse[node]):
            if predecessor in seen:
                continue
            seen[predecessor] = {"anchor": seen[node]["anchor"], "next_node": node,
                                 "next_link": link, "edge_count": seen[node]["edge_count"] + 1}
            pending.append(predecessor)
    return seen


def _route_value(value: Mapping[str, Any] | None, graph: _Graph) -> dict[str, Any]:
    """Serialize a reachability witness using retained model display IDs."""
    if value is None:
        return {"reachable": False, "anchor_id": None, "next_node_id": None,
                "next_link_id": None, "edge_count": None}
    return {"reachable": True, "anchor_id": graph.display[value["anchor"]],
            "next_node_id": graph.display[value["next_node"]] if value["next_node"] is not None else None,
            "next_link_id": graph.links[value["next_link"]]["asset_id"] if value["next_link"] is not None else None,
            "edge_count": value["edge_count"]}


def diagnose_topology(nodes: Sequence[Mapping[str, Any]], links: Sequence[Mapping[str, Any]], *,
                      baseline_nodes: Sequence[Mapping[str, Any]] = (),
                      baseline_links: Sequence[Mapping[str, Any]] = (),
                      source_connectors: Sequence[Mapping[str, Any]] = (),
                      terminal_node_ids: Sequence[str] = (),
                      identity_mode: str = "exact") -> dict[str, Any]:
    """Distinguish inherited independent networks from new unsupported islands.

    Parameters
    ----------
    nodes, links : sequences of mappings
        Complete candidate graph. Every node requires a string ``asset_id``;
        every link requires unique ``asset_id``, ``from_node`` and ``to_node``.
        ``section`` or ``family`` retains link type. All supplied links are
        actual graph edges: do not mix held source requests into this sequence.
        Nodes with no edges are explicitly included. Outfalls are declared with
        ``is_terminal=True`` or ``section='OUTFALLS'``.
    baseline_nodes, baseline_links : sequences of mappings, optional
        Complete baseline graph in the same identity namespace. Existing separate
        components are recorded independently; their separation is not itself
        evidence that a new model update is defective.
    source_connectors : sequence of mappings, optional
        Held/excluded source links, already crosswalked to model endpoint IDs.
        Their disposition, reasons, source key, status and family are retained
        for exact-ID review. They never become actual graph edges. Only rows
        touching a candidate node are returned as boundary/bridge candidates.
    terminal_node_ids : sequence of str, optional
        Additional explicit terminal IDs. A missing terminal declaration is
        reported, never silently synthesized as an outfall.
    identity_mode : {'exact', 'ascii_case_insensitive'}, optional
        Exact identity by default; SWMM callers should use ASCII case comparison.
        Leading zeros and original display spelling remain intact.

    Returns
    -------
    dict
        JSON-serializable components, per-node directed reachability witnesses,
        baseline splits/merges, exact-ID source connector candidates and findings.
        Baseline reachability is separate from terminal/outfall reachability.

    Raises
    ------
    ValueError
        IDs are missing/duplicated, identity semantics are unsupported, or an
        actual link lacks an explicit endpoint. Incomplete source connector IDs
        become review findings rather than guessed connections.

    Notes
    -----
    This is topology QA, not hydraulic acceptance. A nominal route through a pump
    or regulator says nothing about its operating status, head, capacity, loading
    or controls. Existing baseline nodes are zero-edge baseline anchors; this
    cannot prove that their network reaches an outfall. Component IDs are stable
    for identical graphs regardless of row order, not persistent cross-run IDs.
    No coordinates, nearest-neighbor matching, clipping, scope expansion, link
    insertion or direction reversal occurs.
    """
    if identity_mode not in {"exact", "ascii_case_insensitive"}:
        raise ValueError("Unsupported topology identity_mode")
    candidate = _graph(nodes, links, identity_mode)
    baseline = _graph(baseline_nodes, baseline_links, identity_mode)
    components, membership = _components(candidate, "C")
    baseline_components, baseline_membership = _components(baseline, "B")
    findings = []
    for label, graph in (("candidate", candidate), ("baseline", baseline)):
        for key in sorted(graph.weak.keys() - graph.declared.keys()):
            findings.append({"rule": "missing_node_reference", "graph": label,
                             "node_id": graph.display[key], "severity": "warning"})
    terminals = {_comparison(_identifier(value, "Terminal node ID"), identity_mode) for value in terminal_node_ids}
    terminals.update(key for key, row in candidate.declared.items()
                     if row.get("is_terminal") is True or str(row.get("section", "")).upper() == "OUTFALLS")
    missing_terminals = sorted(terminals - candidate.declared.keys())
    for key in missing_terminals:
        findings.append({"rule": "terminal_node_not_declared", "node_id": candidate.display.get(key, key), "severity": "warning"})
    # Undeclared endpoint ghosts are diagnostic graph vertices, not accepted
    # baseline anchors or declared terminal objects.
    terminals &= candidate.declared.keys()
    baseline_anchors = set(baseline.declared) & candidate.declared.keys()
    toward_baseline = _reachability(candidate, baseline_anchors)
    toward_terminal = _reachability(candidate, terminals)
    component_rows = {row["component_id"]: row for row in components}
    for component in components:
        members = set(component["comparison_node_ids"])
        old_members = members & baseline.declared.keys()
        old_components = sorted({baseline_membership[key] for key in old_members})
        new_members = (members & candidate.declared.keys()) - baseline.declared.keys()
        if old_components:
            classification = "baseline_component_merge" if len(old_components) > 1 else (
                "baseline_connected_extension" if new_members or any(_comparison(v, identity_mode) not in baseline.links for v in component["link_ids"])
                else "baseline_component")
        elif members & terminals:
            classification = "new_network_with_terminal"
        elif component["isolated_node"]:
            classification = "new_isolated_node"
        else:
            classification = "new_unsupported_island"
        component.update(classification=classification, baseline_component_ids=old_components,
                         baseline_node_ids=[candidate.display[key] for key in sorted(old_members)],
                         new_node_ids=[candidate.display[key] for key in sorted(new_members)],
                         referenced_only_node_ids=[candidate.display[key] for key in sorted(members - candidate.declared.keys())],
                         terminal_node_ids=[candidate.display[key] for key in sorted(members & terminals)],
                         nodes_without_directed_baseline_route=[candidate.display[key] for key in sorted(members - toward_baseline.keys())],
                         nodes_without_directed_terminal_route=[candidate.display[key] for key in sorted(members - toward_terminal.keys())],
                         connector_candidate_keys=[])
        if classification in {"new_isolated_node", "new_unsupported_island"}:
            findings.append({"rule": classification, "component_id": component["component_id"],
                             "node_ids": component["node_ids"], "severity": "warning"})
        unanchored_new = (members & candidate.declared.keys()) - baseline.declared.keys() - toward_baseline.keys() - toward_terminal.keys()
        if old_components and unanchored_new:
            findings.append({"rule": "new_nodes_without_nominal_downstream_anchor", "component_id": component["component_id"],
                             "node_ids": [candidate.display[key] for key in sorted(unanchored_new)], "severity": "warning"})
    baseline_splits = []
    for component in baseline_components:
        present = set(component["comparison_node_ids"]) & candidate.weak.keys()
        candidate_ids = sorted({membership[key] for key in present})
        component["candidate_component_ids"] = candidate_ids
        component["missing_candidate_node_ids"] = [baseline.display[key] for key in component["comparison_node_ids"] if key not in candidate.declared]
        if len(candidate_ids) > 1:
            split = {"baseline_component_id": component["component_id"], "candidate_component_ids": candidate_ids}
            baseline_splits.append(split)
            findings.append({"rule": "baseline_component_split", **split, "severity": "warning"})
    connector_rows = []
    seen_source_keys = set()
    already_present = 0
    unrelated = 0
    for row in sorted(source_connectors, key=lambda value: str(value.get("key") or value.get("asset_id") or "")):
        source_key = row.get("key") or row.get("asset_id")
        try:
            source_key = _identifier(source_key, "Source connector key")
            if source_key in seen_source_keys:
                raise ValueError("Duplicate source connector key")
            seen_source_keys.add(source_key)
            raw_start = _identifier(row.get("from_node"), "Source connector from_node")
            raw_end = _identifier(row.get("to_node"), "Source connector to_node")
        except ValueError as error:
            findings.append({"rule": "unusable_source_connector_identity", "source_key": source_key,
                             "reason": str(error), "identity_issues": deepcopy(row.get("identity_issues", {})),
                             "source_from_node": row.get("source_from_node", row.get("from_node")),
                             "source_to_node": row.get("source_to_node", row.get("to_node")),
                             "disposition": row.get("disposition"), "reasons": deepcopy(row.get("reasons", [])),
                             "severity": "warning"})
            continue
        start, end = _comparison(raw_start, identity_mode), _comparison(raw_end, identity_mode)
        model_link = row.get("model_id")
        if model_link is None and row.get("model_identity_resolved") is not False:
            model_link = row.get("asset_id")
        endpoint_conflict = False
        existing = None
        if isinstance(model_link, str) and _comparison(model_link, identity_mode) in candidate.links:
            existing = candidate.endpoints[_comparison(model_link, identity_mode)]
            if existing == (start, end):
                already_present += 1
                continue
            endpoint_conflict = True
        touched = sorted({membership[key] for key in (start, end) if key in membership})
        if not touched:
            unrelated += 1
            continue
        missing = [raw for raw, key in ((raw_start, start), (raw_end, end)) if key not in candidate.declared]
        classification = "bridges_candidate_components" if len(touched) > 1 else (
            "touches_unmodeled_endpoint" if missing else "within_candidate_component")
        record = {"source_key": source_key, "asset_id": row.get("asset_id"), "model_id": row.get("model_id"),
                  "from_node": candidate.display.get(start, raw_start), "to_node": candidate.display.get(end, raw_end),
                  "source_from_node": row.get("source_from_node"), "source_to_node": row.get("source_to_node"),
                  "model_identity_resolved": row.get("model_identity_resolved", model_link is not None),
                  "identity_issues": deepcopy(row.get("identity_issues", {})),
                  "node_model_association": deepcopy(row.get("node_model_association", {})),
                  "pipe_model_association": deepcopy(row.get("pipe_model_association", {})),
                  "component_ids": touched, "classification": classification,
                  "unmodeled_node_ids": missing, "section": row.get("section"), "family": row.get("family"),
                  "status": row.get("status"), "disposition": row.get("disposition"),
                  "existing_model_link_endpoint_conflict": endpoint_conflict,
                  "existing_model_endpoints": [candidate.display[key] for key in existing] if endpoint_conflict else None,
                  "reasons": deepcopy(row.get("reasons", [])),
                  "could_supply_directed_terminal_route": not endpoint_conflict and start in candidate.declared and end in toward_terminal and start not in toward_terminal,
                  "could_supply_directed_baseline_route": not endpoint_conflict and start in candidate.declared and end in toward_baseline and start not in toward_baseline,
                  "applied_to_graph": False,
                  "review_requirement": "Confirm identity, lifecycle, scope, hydraulic parameters and load/control effects before any model edit."}
        connector_rows.append(record)
        for component_id in touched:
            component_rows[component_id]["connector_candidate_keys"].append(source_key)
    node_rows = [{"node_id": candidate.display[key], "comparison_id": key, "component_id": membership[key],
                  "declared": key in candidate.declared, "in_baseline": key in baseline.declared,
                  "is_terminal": key in terminals, "isolated": not candidate.weak[key],
                  "toward_baseline": _route_value(toward_baseline.get(key), candidate),
                  "toward_terminal": _route_value(toward_terminal.get(key), candidate)}
                 for key in sorted(candidate.weak)]
    removed_nodes = sorted(baseline.declared.keys() - candidate.declared.keys())
    removed_links = sorted(baseline.links.keys() - candidate.links.keys())
    changed_endpoints = [baseline.links[key]["asset_id"] for key in sorted(baseline.links.keys() & candidate.links.keys())
                         if baseline.endpoints[key] != candidate.endpoints[key]]
    return {"schema_version": 1, "identity_mode": identity_mode,
            "interpretation": "Nominal directed topology only; baseline attachment and outfall reachability are separate from hydraulic acceptance.",
            "summary": {"declared_nodes": len(candidate.declared), "referenced_only_nodes": len(candidate.weak) - len(candidate.declared),
                        "links": len(candidate.links), "weak_components": len(components), "baseline_weak_components": len(baseline_components),
                        "isolated_declared_nodes": sum(not candidate.weak[key] for key in candidate.declared),
                        "new_unsupported_islands": sum(row["classification"] == "new_unsupported_island" for row in components),
                        "new_isolated_nodes": sum(row["classification"] == "new_isolated_node" for row in components),
                        "declared_nodes_without_terminal_route": sum(key not in toward_terminal for key in candidate.declared),
                        "new_declared_nodes_without_baseline_or_terminal_route": sum(key not in baseline_anchors and key not in toward_baseline and key not in toward_terminal for key in candidate.declared),
                        "source_connectors_supplied": len(source_connectors), "connector_candidates_touching_model": len(connector_rows),
                        "source_connectors_already_present": already_present, "source_connectors_without_model_endpoint": unrelated},
            "components": components, "baseline_components": baseline_components, "nodes": node_rows,
            "source_connector_candidates": connector_rows, "baseline_component_splits": baseline_splits,
            "missing_baseline_node_ids": [baseline.display[key] for key in removed_nodes],
            "missing_baseline_link_ids": [baseline.links[key]["asset_id"] for key in removed_links],
            "changed_baseline_link_endpoints": changed_endpoints, "findings": findings}



def diagnose_swmm_topology(document: "SWMMDocument", baseline: "SWMMDocument | None" = None, *,
                            source_connectors: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Diagnose complete immutable SWMM documents without changing their bytes.

    Parameters
    ----------
    document : hymo.SWMMDocument
        Candidate readback containing all declared node and link families.
    baseline : hymo.SWMMDocument, optional
        Original model defining inherited component membership and node anchors.
    source_connectors : sequence of mappings, optional
        Held/excluded source links with explicitly crosswalked model endpoints.
        No source association or spatial inference is performed by this wrapper.

    Returns
    -------
    dict
        Generic topology diagnosis plus document hashes. All JUNCTIONS, STORAGE,
        DIVIDERS and OUTFALLS participate, including isolated node objects. Pumps,
        conduits, weirs, orifices and outlets are nominal directed graph edges.

    Notes
    -----
    SWMM identity comparison uses ASCII case semantics. A route through a pump
    does not establish pump operation, capacity or hydraulic model acceptance.
    """
    from hashlib import sha256

    def rows(source: "SWMMDocument | None") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if source is None:
            return [], []
        nodes = [{"asset_id": record.tokens[0], "section": section}
                 for section in ("JUNCTIONS", "STORAGE", "DIVIDERS", "OUTFALLS")
                 for record in source.records(section)]
        links = []
        for section in ("CONDUITS", "PUMPS", "WEIRS", "ORIFICES", "OUTLETS"):
            for record in source.records(section):
                if len(record.tokens) < 3:
                    raise ValueError(f"Missing topology endpoints in SWMM {section} record")
                links.append({"asset_id": record.tokens[0], "from_node": record.tokens[1],
                              "to_node": record.tokens[2], "section": section})
        return nodes, links

    candidate_nodes, candidate_links = rows(document)
    original_nodes, original_links = rows(baseline)
    report = diagnose_topology(candidate_nodes, candidate_links, baseline_nodes=original_nodes,
                               baseline_links=original_links, source_connectors=source_connectors,
                               identity_mode="ascii_case_insensitive")
    report["candidate_sha256"] = sha256(document.data).hexdigest()
    report["baseline_sha256"] = sha256(baseline.data).hexdigest() if baseline is not None else None
    return report
