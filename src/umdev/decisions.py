"""Resolve private manual decisions without writing models or source data.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained. Observations and decision history are retained separately.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from hymo import SWMMDocument, interpret_fields
from .units import to_metres

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_KEYS = set("id status kind scenario recorded_by recorded_date authorization rationale evidence baseline_sha256 supersedes context override change units record".split())


def _object(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    """Require an object and reject unknown contract keys."""
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{label}: invalid object or unknown keys")
    return value


def _text(value: Any, label: str) -> str:
    """Require explicit text instead of coercing an identity."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: nonempty text required")
    return value


def _hash(value: Any, label: str) -> str:
    """Require a lowercase SHA-256 fingerprint."""
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{label}: lowercase SHA-256 required")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of replacing a recorded decision."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _fold(value: str) -> str:
    """Match SWMM ASCII identities without Unicode case folding."""
    return value.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def _validate_entry(entry: Any) -> tuple[str, tuple[Any, ...]]:
    """Validate one decision and return its logical target for conflict checks."""
    row = _object(entry, _ENTRY_KEYS, "Decision")
    identity = _text(row.get("id"), "Decision id")
    for key in ("scenario", "recorded_by", "rationale"):
        _text(row.get(key), identity + "." + key)
    date.fromisoformat(_text(row.get("recorded_date"), identity + ".recorded_date"))
    if row.get("status") not in {"approved", "proposed", "rejected", "archived"}:
        raise ValueError(f"{identity}: unsupported status")
    kind = row.get("kind")
    if kind not in {"parameter_override", "model_update", "review_record"}:
        raise ValueError(f"{identity}: unsupported kind")
    authority = _object(row.get("authorization"), {"basis", "reference", "reviewer", "date"}, identity + ".authorization")
    for key in ("basis", "reference"):
        _text(authority.get(key), identity + ".authorization." + key)
    if "reviewer" in authority:
        _text(authority["reviewer"], identity + ".authorization.reviewer")
    if "date" in authority:
        date.fromisoformat(_text(authority["date"], identity + ".authorization.date"))
    if "context" in row and not isinstance(row["context"], dict):
        raise ValueError(f"{identity}: context must be an object")
    if "baseline_sha256" in row:
        _hash(row["baseline_sha256"], identity + ".baseline_sha256")
    if row.get("supersedes") is not None:
        _text(row["supersedes"], identity + ".supersedes")
    references = row.get("evidence")
    if not isinstance(references, list) or not references:
        raise ValueError(f"{identity}: at least one evidence fingerprint required")
    for reference in references:
        reference = _object(reference, {"source_key", "path", "sha256"}, identity + ".evidence")
        if ("source_key" in reference) == ("path" in reference):
            raise ValueError(f"{identity}: evidence requires exactly one source_key or path")
        _text(reference.get("source_key", reference.get("path")), identity + ".evidence reference")
        _hash(reference.get("sha256"), identity + ".evidence.sha256")
    payload = {"parameter_override": "override", "model_update": "change", "review_record": "record"}[kind]
    if any(name in row for name in {"override", "change", "record"} - {payload}):
        raise ValueError(f"{identity}: payload does not match kind")
    if kind == "parameter_override":
        value = _object(row.get("override"), {"source_key", "field", "value", "unit", "vertical_datum"}, identity + ".override")
        source = _text(value.get("source_key"), identity + ".override.source_key")
        field = value.get("field")
        if field not in {"floor", "rim", "upstream_invert", "downstream_invert", "diameter", "length"}:
            raise ValueError(f"{identity}: unsupported override field")
        measurement = to_metres(value.get("value"), value.get("unit"))
        if field in {"diameter", "length"} and measurement <= 0:
            raise ValueError(f"{identity}: dimensions must be positive")
        if field not in {"diameter", "length"}:
            _text(value.get("vertical_datum"), identity + ".override.vertical_datum")
        return identity, (kind, row["scenario"], source, field)
    if kind == "model_update":
        _hash(row.get("baseline_sha256"), identity + ".baseline_sha256")
        change = _object(row.get("change"), {"action", "section", "object_id", "values", "source_key", "expected"}, identity + ".change")
        field = {"CONDUITS": "length", "XSECTIONS": "geom1"}.get(change.get("section"))
        if change.get("action") != "update" or field is None:
            raise ValueError(f"{identity}: supported model updates are existing conduit length or circular diameter")
        for key in ("values", "expected"):
            if not isinstance(change.get(key), dict) or set(change[key]) != {field}:
                raise ValueError(f"{identity}: values and old-value preconditions must name the supported field")
        target = _text(change.get("object_id"), identity + ".change.object_id")
        _text(change.get("source_key"), identity + ".change.source_key")
        units = _object(row.get("units"), {field}, identity + ".units")
        if set(units) != {field}:
            raise ValueError(f"{identity}: finished field units required")
        for value in (change["values"][field], change["expected"][field]):
            if to_metres(value, units[field]) <= 0:
                raise ValueError(f"{identity}: dimensions must be positive")
        return identity, (kind, row["scenario"], change["section"], _fold(target), field)
    if not isinstance(row.get("record"), dict) or not row["record"]:
        raise ValueError(f"{identity}: descriptive review_record data required")
    return identity, (kind, row["scenario"], identity)


def resolve_decision_ledger(path: str | Path, canonical: Mapping[str, Any],
                            config: Mapping[str, Any], baseline: SWMMDocument, *,
                            selected_pipe_keys: Sequence[str] | None = None) -> dict[str, Any]:
    """Freeze approved decisions into preparation and finished handoff inputs.

    Parameters
    ----------
    path : str or Path
        Private ledger JSON. Evidence paths resolve against its directory.
    canonical : mapping
        Ingested nodes/pipes with immutable source_record_sha256 fingerprints.
    config : mapping
        Effective configuration, current scenario and baseline target units.
    baseline : SWMMDocument
        Immutable baseline, exactly fingerprinted by every finished model update.
    selected_pipe_keys : sequence of str, optional
        Requested pipes. Overrides outside their pipe/endpoint set stay recorded
        but inactive; finished existing-model updates are a separate scope.

    Returns
    -------
    dict
        Snapshot, audit, parameter overrides, finished changes, instruction
        evidence and consumed file hashes. No files or model bytes are created.

    Raises
    ------
    ValueError
        Invalid JSON/contracts, conflicting approvals, invalid supersession,
        stale active evidence, changed baseline, units or old-value mismatch.

    Notes
    -----
    Only approved entries execute. Supersession retains history and must target
    the same field/scenario. Proposed entries cannot retire approved decisions.
    Active stale decisions fail closed before preparation/publication, regardless
    of legacy override policy. Approved review_record entries are intentionally
    non-executable but their current-scenario evidence is still fingerprinted.

    Source observations remain unchanged. Overrides keep their declared vertical
    datum for normal preparation. Finished updates use baseline units. Unique
    decision/<id> instruction keys retain the original source key in evidence.
    """
    ledger_path = Path(path).resolve()
    try:
        raw = ledger_path.read_bytes()
        snapshot = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"Cannot read decision ledger {ledger_path}: {error}") from error
    snapshot = _object(snapshot, {"schema_version", "kind", "metadata", "decisions"}, "Ledger")
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1 or snapshot.get("kind") != "umdev_manual_decision_ledger":
        raise ValueError("Unsupported decision ledger version/kind")
    if "metadata" in snapshot and not isinstance(snapshot["metadata"], dict):
        raise ValueError("Ledger metadata must be an object")
    if not isinstance(snapshot.get("decisions"), list):
        raise ValueError("Ledger decisions must be a list")
    entries, targets = {}, {}
    for row in snapshot["decisions"]:
        identity, target = _validate_entry(row)
        if identity in entries:
            raise ValueError(f"Duplicate decision id: {identity}")
        entries[identity], targets[identity] = row, target
    superseded = set()
    for identity, row in entries.items():
        previous = row.get("supersedes")
        if previous is None:
            continue
        if previous not in entries or previous == identity or targets[identity] != targets[previous]:
            raise ValueError(f"{identity}: supersession must reference the same target/scenario")
        if row["status"] == "approved":
            if entries[previous]["status"] != "approved":
                raise ValueError(f"{identity}: approved supersession requires approved predecessor")
            superseded.add(previous)
        visited, cursor = {identity}, previous
        while cursor is not None:
            if cursor in visited:
                raise ValueError(f"{identity}: supersession cycle")
            visited.add(cursor)
            cursor = entries[cursor].get("supersedes")
            if cursor is not None and cursor not in entries:
                raise ValueError(f"{identity}: unknown supersession predecessor")
    active = {}
    for identity, row in entries.items():
        if row["status"] == "approved" and identity not in superseded:
            if targets[identity] in active:
                raise ValueError(f"Conflicting active decisions: {active[targets[identity]]}, {identity}")
            active[targets[identity]] = identity
    sources = {row["key"]: row for group in ("nodes", "pipes") for row in canonical[group]}
    if len(sources) != sum(len(canonical[group]) for group in ("nodes", "pipes")):
        raise ValueError("Canonical source keys must be unique")
    pipe_keys = {row["key"] for row in canonical["pipes"]}
    needed = set(sources)
    if selected_pipe_keys is not None:
        selected = set(selected_pipe_keys)
        if selected - pipe_keys:
            raise ValueError("Selected pipe keys are absent from canonical sources")
        endpoints = {row[field] for row in canonical["pipes"] if row["key"] in selected for field in ("from_node", "to_node")}
        needed = selected | {row["key"] for row in canonical["nodes"] if row["asset_id"] in endpoints}
    scenario, baseline_hash = config["selection"]["scenario"], sha256(baseline.data).hexdigest()
    files = {str(ledger_path): sha256(raw).hexdigest()}
    overrides, changes, evidence, rows = [], [], {}, []
    for identity, row in entries.items():
        audit = {"decision_id": identity, "kind": row["kind"], "scenario": row["scenario"],
                 "recorded_status": row["status"], "recorded_by": row["recorded_by"],
                 "recorded_date": row["recorded_date"], "authorization": deepcopy(row["authorization"])}
        rows.append(audit)
        if identity in superseded:
            audit["status"] = "superseded"
            continue
        if row["status"] != "approved":
            audit["status"] = "not_approved"
            continue
        if row["scenario"] != scenario:
            audit["status"] = "deferred_scenario"
            continue
        if row["kind"] == "parameter_override" and row["override"]["source_key"] not in needed:
            if row["override"]["source_key"] not in sources:
                raise ValueError(f"{identity}: override source missing")
            audit["status"] = "outside_selected_scope"
            continue
        if row.get("baseline_sha256") is not None and row["baseline_sha256"] != baseline_hash:
            raise ValueError(f"{identity}: stale baseline fingerprint")
        source_evidence = {}
        for reference in row["evidence"]:
            if "source_key" in reference:
                key = reference["source_key"]
                if key not in sources or sources[key].get("source_record_sha256") != reference["sha256"]:
                    raise ValueError(f"{identity}: stale or missing source evidence {key}")
                source_evidence[key] = reference["sha256"]
            else:
                resource = (ledger_path.parent / reference["path"]).resolve()
                try:
                    fingerprint = sha256(resource.read_bytes()).hexdigest()
                except OSError as error:
                    raise ValueError(f"{identity}: missing evidence file {resource}") from error
                if fingerprint != reference["sha256"]:
                    raise ValueError(f"{identity}: stale file evidence {resource}")
                files[str(resource)] = fingerprint
        audit["status"] = "ready"
        if row["kind"] == "review_record":
            audit["status"] = "record_only"
        elif row["kind"] == "parameter_override":
            value = deepcopy(row["override"])
            key = value["source_key"]
            if key not in source_evidence:
                raise ValueError(f"{identity}: override must fingerprint its own source record")
            allowed = {"upstream_invert", "downstream_invert", "diameter", "length"} if key in pipe_keys else {"floor", "rim"}
            if value["field"] not in allowed:
                raise ValueError(f"{identity}: override field incompatible with source entity")
            if value["field"] in {"upstream_invert", "downstream_invert"}:
                # A manual port assignment depends on the structure observation
                # as well as the pipe identity; either changing requires review.
                endpoint = "from_node" if value["field"] == "upstream_invert" else "to_node"
                node_id = sources[key].get(endpoint)
                nodes = [node for node in canonical["nodes"] if node["asset_id"] == node_id]
                if len(nodes) != 1 or nodes[0]["key"] not in source_evidence:
                    raise ValueError(f"{identity}: pipe-end override must fingerprint its unique endpoint structure")
            authority = row["authorization"]
            value.update(evidence_sha256=source_evidence[key], rationale=row["rationale"],
                         reviewer=authority.get("reviewer", "Inherited authorization; recorded by " + row["recorded_by"]),
                         date=authority.get("date", row["recorded_date"]), decision_id=identity,
                         date_basis="authorization_date" if "date" in authority else "recording_date",
                         authorization=deepcopy(authority), ledger_path=str(ledger_path))
            overrides.append(value)
        else:
            change = deepcopy(row["change"])
            field = next(iter(change["values"]))
            if row["units"][field] != config["model"]["target"]["length_unit"]:
                raise ValueError(f"{identity}: finished units differ from baseline length units")
            matches = [record for record in baseline.records(change["section"]) if _fold(record.tokens[0]) == _fold(change["object_id"])]
            if len(matches) != 1:
                raise ValueError(f"{identity}: finished target missing or nonunique")
            old = interpret_fields(change["section"], matches[0])
            if change["section"] == "XSECTIONS" and str(old["shape"]).upper() != "CIRCULAR":
                raise ValueError(f"{identity}: diameter update requires existing circular shape")
            if not math.isclose(float(old[field]), float(change["expected"][field]), rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"{identity}: old-value precondition differs from baseline")
            original = change["source_key"]
            change["source_key"] = "decision/" + identity
            changes.append(change)
            evidence[change["source_key"]] = {"decision_id": identity, "decision": deepcopy(row),
                "original_instruction_source_key": original, "ledger_path": str(ledger_path)}
    summary = {name: sum(row["status"] == name for row in rows)
               for name in ("ready", "record_only", "superseded", "not_approved", "deferred_scenario", "outside_selected_scope")}
    audit = {"schema_version": 1, "ledger_path": str(ledger_path), "ledger_sha256": sha256(raw).hexdigest(),
             "baseline_sha256": baseline_hash, "scenario": scenario, "decision_count": len(entries),
             "override_count": len(overrides), "model_update_count": len(changes), "summary": summary,
             "decisions": rows, "model_generation": "not_performed_by_ledger_resolution"}
    return {"overrides": overrides, "changes": changes, "evidence": evidence,
            "side_input_hashes": files, "audit": audit, "snapshot": deepcopy(snapshot)}
