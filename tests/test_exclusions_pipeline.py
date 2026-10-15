"""Verify audited scope exclusions across preparation, publication, and QA.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained synthetic regression tests; no private utility records.
"""

from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
from typing import Any

import pytest
from hymo import read_document

from umdev.pipeline import prepare_project, run_project
from test_pipeline import BASELINE, FIXTURES, packet_project, project

PIPE_KEY = "network/pipes/0007"
EXCLUSION_REASON = "Reviewed single-service branch outside the modelling scope."


def _configure(config: Path, **updates: Any) -> None:
    """Update a caller-owned synthetic configuration in UTF-8."""
    value = json.loads(config.read_text(encoding="utf-8"))
    for section, contents in updates.items():
        value.setdefault(section, {}).update(contents)
    config.write_text(json.dumps(value), encoding="utf-8")


def _exclude(config: Path, key: str = PIPE_KEY, **fields: Any) -> None:
    """Record a reasoned per-source exclusion without altering source records."""
    _configure(config, selection={"pipe_exclusions": [
        {"source_key": key, "reason": EXCLUSION_REASON, **fields}]})


def _rows(path: Path) -> list[dict[str, str]]:
    """Read a human-review CSV while preserving textual asset identifiers."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _anchored_project(tmp_path: Path, packet: dict[str, Any] | None = None) -> Path:
    """Attach synthetic source node 002 to a valid existing junction."""
    if packet is None:
        packet = json.loads((FIXTURES / "utility_a.json").read_text(encoding="utf-8"))
    config = packet_project(tmp_path, packet)
    baseline = (BASELINE.replace("OLD 20 2", "OLD 9 3")
                .replace("OUT 19 FREE", "OUT 8 FREE")
                .replace("OLD -20 0", "OLD 0 10")
                .replace("OUT -10 0", "OUT 0 20"))
    (tmp_path / "baseline.inp").write_text(baseline, encoding="utf-8")
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text(json.dumps({"nodes": {"002": "OLD"}, "pipes": {}}), encoding="utf-8")
    _configure(config, identity={"model_association": {
        "methods": ["explicit_crosswalk", "asset_id"], "crosswalk_path": str(crosswalk)}})
    return config


def test_explicit_exclusion_releases_complete_request_missing_profile(tmp_path: Path) -> None:
    """A deliberately omitted incomplete branch is excluded, rather than held."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text(encoding="utf-8"))
    packet["datasets"]["AccessCovers"]["records"][0]["cover_m"] = None
    config = packet_project(tmp_path, packet)
    _exclude(config, decision_id="SYNTHETIC-001")
    _configure(config, publication={"mode": "complete_request"})
    baseline = (tmp_path / "baseline.inp").read_bytes()
    output = tmp_path / "review"
    result = run_project(config, output_directory=output)
    assert result["status"] == "verified_model_update"
    assert result["requested_source_pipes"] == 0
    assert result["applied_source_pipes"] == 0
    assert result["held_source_pipes"] == 0
    assert result["excluded_source_pipes"] == 1
    assert Path(result["candidate"]).read_bytes() == baseline
    membership = _rows(output / "membership.csv")
    assert membership[0]["disposition"] == "excluded"
    assert membership[0]["source_key"] == PIPE_KEY
    register = _rows(output / "scope_exclusions.csv")
    assert register[0]["source_key"] == PIPE_KEY
    assert EXCLUSION_REASON in json.dumps(register)
    assert "SYNTHETIC-001" in json.dumps(register)


def test_excluded_new_pipe_creates_no_nodes_and_preserves_context(tmp_path: Path) -> None:
    """Excluded source rows remain auditable but do not become writer inputs."""
    config = project(tmp_path)
    _exclude(config)
    plan = prepare_project(config)
    assert plan["selected_pipe_keys"] == []
    assert plan["prepared"]["pipes"] == []
    assert plan["prepared"]["nodes"] == []
    assert PIPE_KEY in {pipe["key"] for pipe in plan["canonical"]["pipes"]}
    assert len(plan["selection_exclusions"]) == 1
    assert plan["handoff"]["applied_source_keys"] == []
    baseline = (tmp_path / "baseline.inp").read_bytes()
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["new_junctions"] == 0
    assert Path(result["candidate"]).read_bytes() == baseline
    assert (tmp_path / "baseline.inp").read_bytes() == baseline
    written = json.loads((tmp_path / "review" / "written_model_diff.json").read_text(encoding="utf-8"))
    assert written["verification"]["matches_expected"] is True


def test_mixed_batch_excluded_and_applied_are_separate_counts(tmp_path: Path) -> None:
    """Scope exclusions do not inflate holds or remove an unrelated valid pipe."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text(encoding="utf-8"))
    duplicate = deepcopy(packet["datasets"]["GravitySegments"]["records"][0])
    duplicate.update(segment_text="0008", start_access="003", finish_access="004")
    duplicate["shape"]["coordinates"] = [[20, 0], [20, 10]]
    packet["datasets"]["GravitySegments"]["records"].append(duplicate)
    for original, identifier in zip(deepcopy(packet["datasets"]["AccessCovers"]["records"]), ("003", "004")):
        original["access_text"] = identifier
        original["shape"]["coordinates"][0] = 20
        packet["datasets"]["AccessCovers"]["records"].append(original)
    config = packet_project(tmp_path, packet)
    _exclude(config)
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["requested_source_pipes"] == 1
    assert result["applied_source_pipes"] == 1
    assert result["excluded_source_pipes"] == 1
    assert result["held_source_pipes"] == 0
    document = read_document(result["candidate"])
    assert {row.tokens[0] for row in document.records("CONDUITS")} == {"OLD_LINK", "0008"}
    assert {row.tokens[0] for row in document.records("JUNCTIONS")} == {"OLD", "003", "004"}
    membership = {row["source_key"]: row for row in _rows(tmp_path / "review" / "membership.csv")}
    assert membership[PIPE_KEY]["disposition"] == "excluded"
    assert membership["network/pipes/0008"]["disposition"] == "applied"


def test_stale_explicit_exclusion_fails_before_publication(tmp_path: Path) -> None:
    """An obsolete source fingerprint cannot silently remove a changed asset."""
    config = project(tmp_path)
    _exclude(config, source_sha256="0" * 64)
    output = tmp_path / "review"
    with pytest.raises(ValueError, match="(?i)stale|fingerprint|sha256"):
        run_project(config, output_directory=output)
    assert not output.exists()


def test_matching_explicit_exclusion_fingerprint_is_accepted(tmp_path: Path) -> None:
    """An exact current source record supports reproducible reviewed exclusions."""
    config = project(tmp_path)
    plan = prepare_project(config)
    fingerprint = plan["canonical"]["pipes"][0]["source_record_sha256"]
    _exclude(config, source_sha256=fingerprint)
    excluded = prepare_project(config)
    assert excluded["selected_pipe_keys"] == []
    assert len(excluded["selection_exclusions"]) == 1


def test_branch_review_mode_preserves_candidate_and_reports_eligible_pipe(tmp_path: Path) -> None:
    """Review-only detection inventories a branch without changing model bytes."""
    config = _anchored_project(tmp_path)
    original = run_project(config, output_directory=tmp_path / "original")
    _configure(config, selection={"single_pipe_branches": {"mode": "review"}})
    review = run_project(config, output_directory=tmp_path / "review")
    assert review["candidate_sha256"] == original["candidate_sha256"]
    assert review["excluded_source_pipes"] == 0
    assert review["applied_source_pipes"] == 1
    rows = _rows(tmp_path / "review" / "single_pipe_branches.csv")
    assert PIPE_KEY in {row["source_key"] for row in rows}
    assert prepare_project(config)["single_pipe_branches"]


def test_branch_rule_excludes_only_one_pipe_and_does_not_prune_longer_reach(tmp_path: Path) -> None:
    """Fixed source degrees protect a two-pipe tributary from iterative pruning."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text(encoding="utf-8"))
    pipe = packet["datasets"]["GravitySegments"]["records"][0]
    for identifier, upstream, downstream, coordinates in (
        ("0008", "003", "004", [[20, -10], [20, 0]]),
        ("0009", "004", "002", [[20, 0], [0, 10]]),
    ):
        branch = deepcopy(pipe)
        branch.update(segment_text=identifier, start_access=upstream, finish_access=downstream, bore_mm=400)
        branch["shape"]["coordinates"] = coordinates
        packet["datasets"]["GravitySegments"]["records"].append(branch)
    for identifier, floor, coordinates in (("003", 12, [20, -10]), ("004", 11, [20, 0])):
        node = deepcopy(packet["datasets"]["AccessCovers"]["records"][0])
        node.update(access_text=identifier, base_m=floor, cover_m=floor + 2,
                    leaves=f"{floor} m (N), 400 mm", enters=f"{floor} m (S), 400 mm")
        node["shape"]["coordinates"] = coordinates
        packet["datasets"]["AccessCovers"]["records"].append(node)
    config = _anchored_project(tmp_path, packet)
    _configure(config, selection={"single_pipe_branches": {
        "mode": "exclude", "reason": EXCLUSION_REASON}})
    plan = prepare_project(config)
    assert set(plan["selected_pipe_keys"]) == {"network/pipes/0008", "network/pipes/0009"}
    assert {row["source_key"] for row in plan["selection_exclusions"]} == {PIPE_KEY}
    assert "001" not in {node["asset_id"] for node in plan["prepared"]["nodes"]}
    assert {row["source_key"] for row in plan["membership"] if row["disposition"] == "excluded"} == {PIPE_KEY}


def test_branch_preserve_exception_keeps_requested_pipe(tmp_path: Path) -> None:
    """A project keep exception overrides the optional single-branch rule."""
    config = _anchored_project(tmp_path)
    _configure(config, selection={"single_pipe_branches": {
        "mode": "exclude", "reason": EXCLUSION_REASON, "preserve_pipe_keys": [PIPE_KEY]}})
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["applied_source_pipes"] == 1
    assert result["excluded_source_pipes"] == 0
    assert result["new_junctions"] == 1
    assert "0007" in {row.tokens[0] for row in read_document(result["candidate"]).records("CONDUITS")}
    rows = _rows(tmp_path / "review" / "single_pipe_branches.csv")
    assert len(rows) == 1
    assert rows[0]["status"] == "preserved_by_exception"



def test_explicit_exclusion_precedes_unresolved_model_identity_binding(tmp_path: Path) -> None:
    """An excluded source needs no invented target identity to leave the scope."""
    config = project(tmp_path)
    _configure(config, identity={"model_association": {"methods": ["mapped_model_id"]}})
    with pytest.raises(ValueError, match="Unresolved .* model identity"):
        prepare_project(config)
    _exclude(config)
    plan = prepare_project(config)
    assert plan["selected_pipe_keys"] == []
    assert plan["prepared"]["pipes"] == []
    assert plan["prepared"]["nodes"] == []
    baseline = (tmp_path / "baseline.inp").read_bytes()
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["excluded_source_pipes"] == 1
    assert result["new_junctions"] == 0
    assert Path(result["candidate"]).read_bytes() == baseline
