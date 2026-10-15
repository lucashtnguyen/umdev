"""Apply explicit source scope decisions and optional single-pipe branch rules.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained. These scope decisions never delete baseline model assets.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from hymo import SWMMDocument

from .models import _key, resolve_model_identity


def _model_id(row: Mapping[str, Any], entity: str, config: Mapping[str, Any],
              crosswalk: Mapping[str, Any]) -> str | None:
    """Resolve an identity through the configured order and frozen crosswalk."""
    try:
        return resolve_model_identity(row, entity, config, crosswalk)[0]
    except ValueError:
        # Missing model identity must retain a pipe for normal preparation QA,
        # never silently make an unknown existing asset eligible for pruning.
        return None


def _branch_candidates(canonical: Mapping[str, Any], config: Mapping[str, Any],
                       baseline: SWMMDocument, crosswalk: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Find leaf additions using complete source context frozen before exclusions.

    Notes
    -----
    All scenario-active source pipes contribute to node degree, including pipes
    outside the selected region or diameter filter. Unknown lifecycle records
    also block leaf status. Only known active gravity pipes may qualify. Existing
    model links of every family protect their endpoint pairs in either direction.
    This is a one-pass rule; removing one pipe never makes another pipe a leaf.
    """
    selection = config.get("selection", {})
    scenario = config.get("scenarios", {}).get(selection.get("scenario"), {})
    included = set(scenario.get("include_statuses", []))
    inactive = set(scenario.get("exclude_statuses", []))
    nodes = {row["asset_id"]: row for row in canonical.get("nodes", [])}
    model_nodes = {_key(row.tokens[0]) for section in ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS") for row in baseline.records(section)}
    junctions = {_key(row.tokens[0]) for row in baseline.records("JUNCTIONS")}
    links = [row for section in ("CONDUITS", "PUMPS", "WEIRS", "ORIFICES", "OUTLETS") for row in baseline.records(section)]
    link_ids = {_key(row.tokens[0]) for row in links}
    represented_pairs = {frozenset((_key(row.tokens[1]), _key(row.tokens[2]))) for row in links}
    incident: dict[str, set[str]] = defaultdict(set)
    for pipe in canonical.get("pipes", []):
        if pipe.get("status", "unknown") in inactive:
            continue
        for field in ("from_node", "to_node"):
            identity = pipe.get(field)
            if isinstance(identity, str) and identity:
                incident[identity].add(pipe["key"])
    candidates = []
    for pipe in sorted(canonical.get("pipes", []), key=lambda row: row["key"]):
        if pipe.get("status") not in included or pipe.get("pipe_kind") != "gravity":
            continue
        first, second = pipe.get("from_node"), pipe.get("to_node")
        if not isinstance(first, str) or not isinstance(second, str) or not first or not second or first == second or first not in nodes or second not in nodes:
            continue
        first_id, second_id = _model_id(nodes[first], "nodes", config, crosswalk), _model_id(nodes[second], "nodes", config, crosswalk)
        pipe_id = _model_id(pipe, "pipes", config, crosswalk)
        if first_id is None or second_id is None or pipe_id is None:
            continue
        first_model, second_model = _key(first_id), _key(second_id)
        if first_model == second_model or _key(pipe_id) in link_ids or frozenset((first_model, second_model)) in represented_pairs:
            continue
        for leaf, anchor, leaf_model, anchor_model in ((first, second, first_model, second_model), (second, first, second_model, first_model)):
            if leaf_model not in model_nodes and anchor_model in junctions and len(incident[leaf]) == 1:
                candidates.append({"source_key": pipe["key"], "asset_id": pipe["asset_id"],
                                   "source_sha256": pipe.get("source_record_sha256"),
                                   "leaf_node": leaf, "anchor_node": anchor,
                                   "leaf_model_id": first_id if leaf == first else second_id,
                                   "anchor_model_id": second_id if anchor == second else first_id,
                                   "leaf_active_source_degree": 1,
                                   "graph_context": "complete_source_excluding_known_inactive"})
    return candidates


def apply_scope_exclusions(canonical: Mapping[str, Any], config: Mapping[str, Any],
                           baseline: SWMMDocument, selected: Sequence[str],
                           membership: Sequence[Mapping[str, Any]], *,
                           crosswalk_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return scope dispositions and branch evidence without mutating inputs.

    Parameters
    ----------
    canonical : mapping
        Complete source network retained for graph context and attribution.
    config : mapping
        Validated selection policy with optional explicit and branch exclusions.
    baseline : SWMMDocument
        Immutable baseline, used only to protect existing model representation.
    selected : sequence of str
        Source-qualified pipe keys initially selected for parameter preparation.
    membership : sequence of mapping
        Initial disposition for every source pipe, including selection holds.
    crosswalk_snapshot : mapping, optional
        Validated frozen identity crosswalk; no crosswalk files are reread.

    Returns
    -------
    dict
        ``selected``, independent ``membership``, audit ``exclusions``, and
        report-only or applied ``branch_candidates``. A scope exclusion prevents
        preparation and does not delete an already represented baseline asset.

    Raises
    ------
    ValueError
        A configured source key is absent/duplicate, evidence is stale, or initial
        membership/selection is inconsistent with the canonical source packet.
    """
    pipes = {row["key"]: row for row in canonical.get("pipes", [])}
    if len(pipes) != len(canonical.get("pipes", [])):
        raise ValueError("Duplicate canonical pipe source key")
    rows = deepcopy(list(membership))
    indexed = {row["source_key"]: row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != set(pipes):
        raise ValueError("Membership must identify every canonical pipe exactly once")
    chosen = set(selected)
    if len(chosen) != len(selected) or chosen != {key for key, row in indexed.items() if row["disposition"] == "selected_for_preparation"}:
        raise ValueError("Selected keys disagree with initial membership")
    selection = config.get("selection", {})
    explicit = selection.get("pipe_exclusions", [])
    explicit_by_key = {row["source_key"]: row for row in explicit}
    if len(explicit_by_key) != len(explicit) or set(explicit_by_key) - set(pipes):
        raise ValueError("Explicit pipe exclusion source key is absent or duplicated")
    branch = selection.get("single_pipe_branches", {})
    preserved = set(branch.get("preserve_pipe_keys", []))
    if preserved - set(pipes):
        raise ValueError("Preserved branch source key is absent")
    audit = []

    def exclude(key: str, reason_code: str, detail: Mapping[str, Any]) -> bool:
        row = indexed[key]
        applied = row["disposition"] in {"selected_for_preparation", "hold"}
        evidence = {"source_key": key, "asset_id": pipes[key]["asset_id"],
                    "source_sha256": pipes[key].get("source_record_sha256"),
                    "reason_code": reason_code, **deepcopy(dict(detail)),
                    "previous_disposition": row["disposition"],
                    "previous_reasons": deepcopy(row.get("reasons", [])),
                    "applied": applied,
                    "status": "excluded" if applied else "deferred_outside_selection"}
        audit.append(evidence)
        if applied:
            row.update(disposition="excluded", reasons=[reason_code], scope_exclusion=deepcopy(evidence))
            chosen.discard(key)
        return applied

    for key, entry in sorted(explicit_by_key.items()):
        if "source_sha256" in entry and entry["source_sha256"] != pipes[key].get("source_record_sha256"):
            raise ValueError(f"Stale pipe exclusion source fingerprint: {key}")
        exclude(key, "explicit_scope_exclusion", entry)
    candidates = _branch_candidates(canonical, config, baseline, crosswalk_snapshot or {})
    for candidate in candidates:
        key = candidate["source_key"]
        candidate["mode"] = branch.get("mode", "review")
        if "decision_id" in branch:
            candidate["decision_id"] = branch["decision_id"]
        candidate["applied"] = False
        candidate["preserved"] = key in preserved
        candidate["explicit_exclusion"] = key in explicit_by_key
        candidate["selection_disposition"] = indexed[key]["disposition"]
        if key in explicit_by_key:
            candidate["status"] = "explicit_exclusion_takes_precedence"
        elif key in preserved:
            candidate["status"] = "preserved_by_exception"
        elif branch.get("mode", "review") == "exclude":
            topology_evidence = {field: candidate[field] for field in (
                "source_key", "asset_id", "source_sha256", "leaf_node", "anchor_node",
                "leaf_model_id", "anchor_model_id", "leaf_active_source_degree", "graph_context")}
            candidate["applied"] = exclude(key, "single_pipe_branch_scope_exclusion", {
                "reason": branch["reason"],
                **({"decision_id": branch["decision_id"]} if "decision_id" in branch else {}),
                "branch_evidence": topology_evidence})
            candidate["status"] = "excluded" if candidate["applied"] else "deferred_outside_selection"
        else:
            candidate["status"] = "review_only"
    return {"selected": sorted(chosen), "membership": rows,
            "exclusions": audit, "branch_candidates": candidates}
