"""Plan explicitly reviewed retirements without editing source or SWMM records.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Manually maintained. Model publication and final graph validation belong to SHMM.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence

from hymo import SWMMDocument

from .models import _key
from .reporting import stable_hash

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NODES = ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS")
_LINKS = ("CONDUITS", "PUMPS", "WEIRS", "ORIFICES", "OUTLETS")
_LOADS = {"DWF", "INFLOWS", "RDII"}
_LINK_METADATA = {"XSECTIONS", "LOSSES", "VERTICES"}
_NO_NETWORK_REFS = {"TITLE", "OPTIONS", "FILES", "EVAPORATION", "TEMPERATURE", "RAINGAGES",
    "TIMESERIES", "CURVES", "PATTERNS", "HYDROGRAPHS", "SNOWPACKS", "AQUIFERS",
    "POLLUTANTS", "LANDUSES", "BUILDUP", "WASHOFF", "LID_CONTROLS", "ADJUSTMENTS",
    "MAP", "BACKDROP", "SYMBOLS"}


def _text(value: Any, label: str) -> str:
    """Require uncoerced review identities and rationale."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " requires nonempty text")
    return value


def _hash(value: Any, label: str) -> str:
    """Require a lowercase SHA256 fingerprint."""
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(label + " requires lowercase SHA256")
    return value


def _review(row: Mapping[str, Any]) -> None:
    """Require independent review metadata; proposed decisions never execute."""
    for field in ("basis", "reviewer", "date"):
        _text(row.get(field), field)
    if date.fromisoformat(row["date"]).isoformat() != row["date"]:
        raise ValueError("Review date must be YYYY-MM-DD")
    if row.get("decision") not in {"approved", "proposed"}:
        raise ValueError("Review decision must be approved or proposed")


def lifecycle_record_fingerprint(section: str, tokens: Sequence[str]) -> str:
    """Fingerprint the exact parsed native record and its section.

    Parameters
    ----------
    section : str
        Native SWMM section name, case insensitive.
    tokens : sequence of str
        Exact unmodified tokens from Hymo, preserving spelling and numerics.

    Returns
    -------
    str
        Canonical JSON SHA256. The plan separately binds complete model bytes.
    """
    _text(section, "section")
    if not isinstance(tokens, (list, tuple)) or not tokens or any(not isinstance(x, str) for x in tokens):
        raise ValueError("Native record tokens must be a nonempty string sequence")
    return stable_hash({"section": section.upper(), "tokens": list(tokens)})


def lifecycle_source_fingerprint(pipe: Mapping[str, Any]) -> str:
    """Bind one complete canonical pipe, including raw evidence and lifecycle.

    Parameters
    ----------
    pipe : mapping
        Canonical source pipe with its source-qualified key and raw-row SHA256.

    Returns
    -------
    str
        Full canonical-record SHA256; changing normalized status is detectable.
    """
    if not isinstance(pipe, Mapping):
        raise ValueError("Canonical pipe must be a mapping")
    _text(pipe.get("key"), "source key")
    _hash(pipe.get("source_record_sha256"), "raw source fingerprint")
    if "source_attributes" in pipe and stable_hash({"source_key": pipe["key"], "record": pipe["source_attributes"]}) != pipe["source_record_sha256"]:
        raise ValueError("Canonical pipe raw source fingerprint is inconsistent")
    return stable_hash(pipe)


def _native(document: SWMMDocument, sections: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Reject collisions across native namespaces before producing any intent."""
    result = {}
    for section in sections:
        for record in document.records(section):
            key = _key(record.tokens[0])
            if key in result:
                raise ValueError("Case-insensitive native identity collision: " + record.tokens[0])
            result[key] = {"section": section, "tokens": list(record.tokens),
                           "sha256": lifecycle_record_fingerprint(section, record.tokens)}
    return result


def _references(document: SWMMDocument, nodes: set[str], links: set[str]) -> list[dict[str, Any]]:
    """Inventory structural, loading, metadata and conservative opaque references."""
    result = []
    for section in document.sections:
        if section in _NO_NETWORK_REFS:
            continue
        for index, record in enumerate(document.records(section)):
            tokens = record.tokens
            pairs: list[tuple[int, str, str]] = []
            if section in _NODES:
                if section == "DIVIDERS" and len(tokens) > 2:
                    pairs.append((2, "link", "unsupported_dependency"))
            elif section in _LINKS:
                pairs.extend((i, "node", "structural") for i in (1, 2) if len(tokens) > i)
            elif section in _LOADS:
                pairs.append((0, "node", "load"))
            elif section == "SUBCATCHMENTS":
                pairs.append((2, "node", "unsupported_dependency"))
            elif section == "TREATMENT":
                pairs.append((0, "node", "unsupported_dependency"))
            elif section == "COORDINATES":
                pairs.append((0, "node", "metadata"))
            elif section in _LINK_METADATA:
                pairs.append((0, "link", "metadata"))
            elif section == "TAGS" and len(tokens) > 1:
                family = "node" if tokens[0].upper() == "NODE" else "link" if tokens[0].upper() == "LINK" else ""
                if family:
                    pairs.append((1, family, "metadata"))
            elif section == "REPORT" and tokens[0].upper() in {"NODES", "LINKS"}:
                family = "node" if tokens[0].upper() == "NODES" else "link"
                pairs.extend((i, family, "metadata") for i in range(1, len(tokens)))
            else:
                # Unknown grammar is not guessed. A literal identifier match is
                # a conservative hold, not proof that a numeric token is a ref.
                pairs.extend((i, family, "opaque_token_occurrence")
                             for i, token in enumerate(tokens) for family, ids in (("node", nodes), ("link", links))
                             if _key(token) in ids)
            for column, family, kind in pairs:
                if column >= len(tokens) or _key(tokens[column]) not in (nodes if family == "node" else links):
                    continue
                result.append({"section": section, "record_index": index, "column": column,
                    "family": family, "asset_id": tokens[column], "kind": kind,
                    "tokens": list(tokens), "record_sha256": lifecycle_record_fingerprint(section, tokens)})
    return result


def _number(token: str) -> Decimal:
    """Keep finite native load sums exact in their original model units."""
    try:
        number = Decimal(token)
    except InvalidOperation as error:
        raise ValueError("Native load quantity is not numeric") from error
    if not number.is_finite() or number < 0:
        raise ValueError("Native load quantity must be finite and nonnegative")
    return number


def _load_groups(document: SWMMDocument, transfers: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Group only identical DWF/pattern, constant INFLOW or RDII definitions."""
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for section in sorted(_LOADS):
        for record in document.records(section):
            tokens = list(record.tokens)
            source = _key(tokens[0])
            target = transfers.get(source)
            if target is None and _key(tokens[0]) not in {_key(v) for v in transfers.values()}:
                continue
            target = target or tokens[0]
            column = 6 if section == "INFLOWS" else 2
            if len(tokens) <= column or section == "INFLOWS" and (tokens[2] != "" or tokens[3].upper() != "FLOW"):
                return [], ["unsupported_load_transfer:" + section + ":" + tokens[0]]
            # Pollutant concentrations cannot be added as though they were
            # flows. Preserve such records as unresolved dependencies instead.
            if section != "RDII" and tokens[1].upper() != "FLOW":
                return [], ["unsupported_pollutant_load_transfer:" + section + ":" + tokens[0]]
            _number(tokens[column])
            signature = tuple(tokens[1:column] + tokens[column+1:])
            # Pollutant distinguishes DWF/INFLOW rows; RDII has one UH per node.
            identity = tokens[1].upper() if section != "RDII" else "RDII"
            slot = (section, source, identity)
            if slot in seen:
                return [], ["duplicate_native_load_slot:" + section + ":" + tokens[0]]
            seen.add(slot)
            candidates[section, _key(target), identity].append({"source_node": tokens[0],
                "target_node": target, "tokens": tokens, "signature": signature,
                "value_column": column, "record_sha256": lifecycle_record_fingerprint(section, tokens)})
    result, held = [], []
    for (section, target, _), rows in sorted(candidates.items()):
        if not any(_key(r["source_node"]) in transfers for r in rows):
            continue
        if len({r["signature"] for r in rows}) != 1:
            held.append("incompatible_load_merge:" + section + ":" + target)
            continue
        template = list(rows[0]["tokens"])
        template[0] = rows[0]["target_node"]
        total = sum((_number(r["tokens"][r["value_column"]]) for r in rows), Decimal(0))
        template[rows[0]["value_column"]] = format(total, "f")
        result.append({"section": section, "target_node": template[0], "tokens": template,
                       "total_native": format(total, "f"), "source_records": [{k: v for k, v in r.items() if k != "signature"} for r in rows]})
    return result, held


def plan_lifecycle_retirements(
    canonical: Mapping[str, Any], baseline: SWMMDocument, *,
    representations: Sequence[Mapping[str, Any]],
    node_dispositions: Sequence[Mapping[str, Any]] = (),
    context_sha256: str | None = None,
) -> dict[str, Any]:
    """Prepare an atomic source/model-bound retirement and dependency plan.

    Parameters
    ----------
    canonical : mapping
        Complete canonical pipes/nodes with immutable raw-row source fingerprints.
    baseline : SWMMDocument
        Exact current reviewed model, including any already modeled replacements.
    representations : sequence of mapping
        Explicit reviewed complete source membership per obsolete model conduit.
        Required keys are id/model_link_id/model_record_sha256/source_pipes/
        complete_source_membership/preserved_representations/basis/reviewer/date/
        decision. Source bindings require source_key/source_sha256/
        expected_fingerprint/intent(retire|preserve). Preserved representations
        require source_key/model_link_id/mode/from_node/to_node/basis/reviewer/
        date/decision; mode is existing, rebuild or exclude_scope.
    node_dispositions : sequence of mapping, optional
        node_id/node_record_sha256/action(retain|remove)/basis/reviewer/date/
        decision and optional transfer_to_node_id for exact newly orphaned nodes.
    context_sha256 : str, optional
        Caller-bound private match/review receipt digest, retained without I/O.

    Returns
    -------
    dict
        status ready|held, per-representation proposals, orphan/reference audit,
        and executable_intent only when the ENTIRE reviewed plan is ready.
        Compatible loads retain native units, definitions and exact decimal sums.

    Raises
    ------
    ValueError
        Malformed review contracts, stale fingerprints, absent identities or
        conflicting duplicate bindings. Engineering ambiguity produces holds.

    Notes
    -----
    Status never selects a model identity. Proximity, Pending register rows and
    incomplete aggregate membership cannot authorize deletion. Preserved active
    members must be explicitly retained, rebuilt or independently excluded from
    scope. exclude_scope does not change their source lifecycle; a loaded or
    otherwise connected native upstream node prevents this narrow branch policy.
    No new elevation, hydraulic parameter, load destination or survey fact is
    inferred. Final connection/directed-route and written-model checks remain
    the caller/SHMM responsibility; a ready plan is not engineering acceptance.
    """
    if context_sha256 is not None:
        _hash(context_sha256, "context fingerprint")
    if not isinstance(representations, (list, tuple)) or not isinstance(node_dispositions, (list, tuple)):
        raise ValueError("Reviews must be explicit sequences")
    if not isinstance(canonical, Mapping) or not isinstance(canonical.get("pipes", []), (list, tuple)):
        raise ValueError("Canonical pipes must be an explicit sequence")
    pipes = {}; assets = set()
    for pipe in canonical.get("pipes", []):
        if not isinstance(pipe, Mapping):
            raise ValueError("Canonical pipe must be a mapping")
        key = _text(pipe.get("key"), "canonical pipe key")
        asset = _text(pipe.get("asset_id"), "canonical pipe asset id")
        if key in pipes:
            raise ValueError("Duplicate canonical pipe key")
        if asset in assets:
            raise ValueError("Duplicate canonical pipe asset id")
        assets.add(asset)
        pipes[key] = pipe
    nodes, links = _native(baseline, _NODES), _native(baseline, _LINKS)
    for link in links.values():
        if len(link["tokens"]) < 3 or any(_key(t) not in nodes for t in link["tokens"][1:3]):
            raise ValueError("Native link has missing or undeclared endpoints")
    refs = _references(baseline, set(nodes), set(links))
    ids, used_links, used_sources, proposals = set(), set(), set(), []
    required = set("id model_link_id model_record_sha256 source_pipes complete_source_membership preserved_representations basis reviewer date decision".split())
    for raw in representations:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("Retirement representation requires exact reviewed schema keys")
        row = deepcopy(dict(raw)); _review(row)
        identity = _text(row["id"], "representation id")
        target = _key(_text(row["model_link_id"], "model link id"))
        if identity in ids or target in used_links:
            raise ValueError("Duplicate retirement id or model link")
        ids.add(identity); used_links.add(target)
        if target not in links or links[target]["section"] != "CONDUITS":
            raise ValueError("Retirement requires an existing conduit")
        if _hash(row["model_record_sha256"], "model record") != links[target]["sha256"]:
            raise ValueError("Stale retirement model record")
        if not isinstance(row["source_pipes"], list) or not row["source_pipes"]:
            raise ValueError("Retirement requires complete explicit source membership")
        held = []
        if row["decision"] != "approved": held.append("review_not_approved")
        if row["complete_source_membership"] is not True: held.append("incomplete_aggregate_membership")
        preserved, retired, seen = set(), set(), set()
        for binding in row["source_pipes"]:
            if not isinstance(binding, Mapping) or set(binding) != {"source_key", "source_sha256", "expected_fingerprint", "intent"}:
                raise ValueError("Source membership requires exact key/hash/fingerprint/intent")
            key = _text(binding["source_key"], "source key")
            if key not in pipes or key in seen or key in used_sources:
                raise ValueError("Missing or duplicate retirement source binding")
            seen.add(key); used_sources.add(key)
            pipe = pipes[key]
            if _hash(binding["source_sha256"], "source fingerprint") != pipe.get("source_record_sha256") or _hash(binding["expected_fingerprint"], "canonical fingerprint") != lifecycle_source_fingerprint(pipe):
                raise ValueError("Stale retirement source fingerprint")
            if binding["intent"] == "retire":
                retired.add(key)
                if pipe.get("status") not in {"abandoned", "retired", "removed", "decommissioned"}:
                    held.append("retirement_status_not_confirmed:" + key)
            elif binding["intent"] == "preserve":
                preserved.add(key)
                if pipe.get("status") != "active": held.append("preserved_source_not_confirmed_active:" + key)
            else:
                raise ValueError("Source intent must be retire or preserve")
        if not retired: held.append("no_confirmed_retired_member")
        replacement = row["preserved_representations"]
        if not isinstance(replacement, list): raise ValueError("Preserved representations must be a list")
        mapped, verification = set(), []
        for entry in replacement:
            if not isinstance(entry, Mapping) or set(entry) != {"source_key", "model_link_id", "mode", "from_node", "to_node", "basis", "reviewer", "date", "decision"}:
                raise ValueError("Preservation requires exact reviewed schema keys")
            _review(entry)
            key = entry["source_key"]
            if key not in preserved or key in mapped: raise ValueError("Preserved source binding is missing, extra or duplicated")
            mapped.add(key)
            pipe = pipes[key]
            u, v = (_text(entry[name], name) for name in ("from_node", "to_node"))
            if (u, v) != (pipe.get("from_node"), pipe.get("to_node")):
                raise ValueError("Preservation endpoints differ from canonical source orientation")
            if entry["decision"] != "approved": held.append("preservation_review_not_approved:" + key)
            mode = entry["mode"]
            if mode == "existing":
                match = _key(_text(entry["model_link_id"], "preserved model link"))
                if match not in links or links[match]["section"] != "CONDUITS" or tuple(_key(t) for t in links[match]["tokens"][1:3]) != (_key(u), _key(v)):
                    held.append("preserved_existing_link_not_verified:" + key)
                else:
                    verification.append({"source_key": key, "mode": mode,
                        "native": deepcopy(links[match]),
                        "xsections": [{"tokens": list(record.tokens), "sha256": lifecycle_record_fingerprint("XSECTIONS", record.tokens)}
                            for record in baseline.records("XSECTIONS") if _key(record.tokens[0]) == match],
                        "interpretation": "Existing conduit identity and orientation verified; compare dimensions and lineage explicitly, not as a spatial equivalence proof."})
            elif mode == "rebuild":
                match = _key(_text(entry["model_link_id"], "replacement model link"))
                if match in links and match != target: held.append("replacement_model_identity_collision:" + key)
            elif mode == "exclude_scope":
                if entry["model_link_id"] is not None: raise ValueError("Excluded source has no model link id")
                upstream = _key(u)
                if any(_key(r["asset_id"]) == upstream and r["family"] == "node" and r["kind"] in {"load", "unsupported_dependency", "opaque_token_occurrence"} for r in refs):
                    held.append("excluded_active_source_upstream_has_dependencies:" + key)
                if any(_key(t) == upstream for link_id, link in links.items() if link_id != target for t in link["tokens"][1:3]):
                    held.append("excluded_active_source_upstream_has_native_connections:" + key)
                if any(p.get("key") != key and p.get("status") == "active" and u in (p.get("from_node"), p.get("to_node")) for p in pipes.values()):
                    held.append("excluded_active_source_upstream_has_active_source_connections:" + key)
            else: raise ValueError("Unknown preservation mode")
        if mapped != preserved: held.append("active_aggregate_member_without_disposition")
        proposals.append({"id": identity, "model_link_id": links[target]["tokens"][0],
            "native": links[target], "review": row, "retired_source_keys": sorted(retired),
            "preserved_source_keys": sorted(preserved), "reasons": held,
            "preservation_verification": verification,
            "action": "replace_aggregate" if preserved else "remove_link"})
    rebuilding = set()
    for proposal in proposals:
        for entry in proposal["review"]["preserved_representations"]:
            if entry["mode"] == "existing" and _key(entry["model_link_id"]) in used_links:
                proposal["reasons"].append("preserved_link_also_retired")
            if entry["mode"] == "rebuild":
                key = _key(entry["model_link_id"])
                if key in rebuilding:
                    raise ValueError("Duplicate replacement model identity")
                rebuilding.add(key)
    retiring_refs = [r for r in refs if r["family"] == "link" and _key(r["asset_id"]) in used_links]
    blockers = ["retired_link_dependency:" + r["section"] + ":" + r["asset_id"] for r in retiring_refs if r["kind"] != "metadata"]
    affected = {_key(t) for key in used_links for t in links[key]["tokens"][1:3]}
    remaining = {_key(t) for key, link in links.items() if key not in used_links for t in link["tokens"][1:3]}
    # Rebuild intents are topology declarations only; their real rows and every
    # native value must still be supplied and independently verified by SHMM.
    remaining.update(_key(entry[name]) for proposal in proposals for entry in proposal["review"]["preserved_representations"]
                     if entry["mode"] == "rebuild" for name in ("from_node", "to_node"))
    orphaned = affected - remaining
    dispositions = {}
    for raw in node_dispositions:
        required_node = {"node_id", "node_record_sha256", "action", "basis", "reviewer", "date", "decision"}
        if not isinstance(raw, Mapping) or set(raw) - (required_node | {"transfer_to_node_id"}) or not required_node <= set(raw):
            raise ValueError("Node disposition requires exact reviewed schema keys")
        _review(raw); key = _key(_text(raw["node_id"], "node id"))
        if key in dispositions or key not in orphaned: raise ValueError("Node disposition must identify one exact newly orphaned node")
        if _hash(raw["node_record_sha256"], "node record") != nodes[key]["sha256"]: raise ValueError("Stale node disposition fingerprint")
        if raw["action"] not in {"retain", "remove"}: raise ValueError("Node action must be retain or remove")
        dispositions[key] = deepcopy(dict(raw))
    orphans, transfers = [], {}
    for key in sorted(orphaned):
        node_refs = [r for r in refs if r["family"] == "node" and _key(r["asset_id"]) == key and not (r["section"] in _LINKS and _key(r["tokens"][0]) in used_links)]
        disposition = dispositions.get(key)
        reasons = []
        if disposition is None: reasons.append("orphan_requires_explicit_disposition")
        elif disposition["decision"] != "approved": reasons.append("node_review_not_approved")
        elif disposition["action"] == "remove":
            if nodes[key]["section"] != "JUNCTIONS": reasons.append("protected_native_node_type")
            if any(r["kind"] not in {"metadata", "load"} for r in node_refs): reasons.append("unsupported_node_dependency")
            target = disposition.get("transfer_to_node_id")
            if any(r["kind"] == "load" for r in node_refs) and target is None: reasons.append("loaded_node_requires_explicit_transfer")
            if target is not None:
                target_key = _key(_text(target, "transfer destination"))
                if target_key not in nodes or target_key in orphaned: reasons.append("transfer_destination_not_surviving_native_node")
                else: transfers[key] = nodes[target_key]["tokens"][0]
        elif "transfer_to_node_id" in disposition: raise ValueError("Retained node cannot transfer its loading")
        orphans.append({"node_id": nodes[key]["tokens"][0], "native": nodes[key],
                        "references": node_refs, "disposition": disposition, "reasons": reasons})
    load_groups, load_held = _load_groups(baseline, transfers)
    blockers.extend(load_held)
    blockers.extend("representation:" + p["id"] + ":" + reason for p in proposals for reason in p["reasons"])
    blockers.extend("node:" + n["node_id"] + ":" + reason for n in orphans for reason in n["reasons"])
    intent = None
    if not blockers:
        intent = {"delete_link_ids": sorted(p["model_link_id"] for p in proposals),
            "delete_node_ids": sorted(n["node_id"] for n in orphans if n["disposition"]["action"] == "remove"),
            "preserved_representations": [deepcopy(entry) for p in proposals for entry in p["review"]["preserved_representations"]],
            "node_dispositions": list(dispositions.values()), "load_transfers": load_groups,
            "remove_link_metadata": retiring_refs,
            "remove_node_metadata": [r for n in orphans if n["disposition"]["action"] == "remove" for r in n["references"] if r["kind"] == "metadata"]}
    return {"schema_version": 1, "status": "held" if blockers else "ready", "baseline_sha256": sha256(baseline.data).hexdigest(),
            "context_sha256": context_sha256, "review_sha256": stable_hash({"representations": representations, "node_dispositions": node_dispositions}),
            "representations": proposals, "orphan_nodes": orphans, "reference_inventory": refs,
            "held": sorted(set(blockers)), "executable_intent": intent,
            "limitations": ["Complete membership is an explicit reviewed assertion, not a spatial match.",
                "Native units and source lifecycle are unchanged; active scope exclusions are not retirements.",
                "No model writing, load catchment inference, final topology or engineering acceptance occurs."]}
