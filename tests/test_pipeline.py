"""Exercise complete preparation, handoff, diff publication and profile readback.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Synthetic utility inputs only; no private project files or credentials.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from hymo import read_document
from shmm import compare_written_model

from umdev.configuration import load_config
from umdev.models import instructions_from_handoff, verify_prepared_profiles
from umdev.pipeline import run_project
from umdev.reporting import write_csv

FIXTURES = Path(__file__).parent / "fixtures" / "schema_equivalence"
BASELINE = """; Synthetic preserved baseline. Author: Lucas HT Nguyen
; Contact: https://github.com/lucashtnguyen Date: 2026-10-14
[TITLE]
Synthetic baseline
[OPTIONS]
FLOW_UNITS LPS
LINK_OFFSETS DEPTH
[JUNCTIONS]
OLD 20 2
[OUTFALLS]
OUT 19 FREE
[CONDUITS]
OLD_LINK OLD OUT 10 0.013 0 0
[XSECTIONS]
OLD_LINK CIRCULAR 0.3 0 0 0
[COORDINATES]
OLD -20 0
OUT -10 0
[VERTICES]
[REPORT]
NODES ALL
LINKS ALL
"""


def project(tmp_path: Path, source: str = "utility_a.config.json", **overrides: object) -> Path:
    """Create a synthetic caller configuration, preserving fixture declarations."""
    baseline = tmp_path / "baseline.inp"
    if not baseline.exists():
        baseline.write_text(BASELINE, encoding="utf-8")
    value = {"extends": [str(FIXTURES / source)],
        "model": {"baseline": str(baseline), "baseline_metadata": {
            "horizontal_crs": {"epsg": 3857}, "map_unit": "m", "vertical_datum": "SYNTHETIC_DATUM"}},
        "publication": {"output_directory": str(tmp_path / "results")},
        "qa": {"engine": {"enabled": False}}}
    value.update(overrides)
    path = tmp_path / "project.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("source", ["utility_a.config.json", "utility_b.config.json"])
def test_full_update_two_schemas_and_manual_diffs(tmp_path: Path, source: str) -> None:
    """Both utility mappings produce complete profiles and independent diffs."""
    config = project(tmp_path, source)
    before = (tmp_path / "baseline.inp").read_bytes()
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["status"] == "verified_model_update"
    assert result["applied_source_pipes"] == 1
    assert result["new_junctions"] == 2
    assert (tmp_path / "baseline.inp").read_bytes() == before
    output = tmp_path / "review"
    for name in ["preview_diff.json", "preview_diff.csv", "preview_diff.html",
                 "written_model_diff.json", "written_model_diff.csv", "written_model_diff.html",
                 "review.html", "handoff.json", "membership.csv", "profile_verification.json"]:
        assert (output / name).exists()
    candidate = read_document(result["candidate"])
    nodes = {r.tokens[0]: r.tokens for r in candidate.records("JUNCTIONS")}
    assert float(nodes["001"][1]) == pytest.approx(10)
    assert sum(map(float, nodes["001"][1:3])) == pytest.approx(12)
    assert "0007" in {r.tokens[0] for r in candidate.records("CONDUITS")}
    written = json.loads((output / "written_model_diff.json").read_text())
    assert written["verification"]["matches_expected"] is True


def test_reproducible_candidate_and_no_overwrite(tmp_path: Path) -> None:
    """Same inputs/config reproduce bytes while each run remains separate."""
    config = project(tmp_path)
    first = run_project(config, output_directory=tmp_path / "first")
    second = run_project(config, output_directory=tmp_path / "second")
    assert first["candidate_sha256"] == second["candidate_sha256"]
    assert first["config_sha256"] == second["config_sha256"]
    with pytest.raises(FileExistsError):
        run_project(config, output_directory=tmp_path / "first")


def test_written_mutation_is_unrequested(tmp_path: Path) -> None:
    """A valid-looking independently changed candidate fails written-model QA."""
    config = project(tmp_path)
    result = run_project(config, output_directory=tmp_path / "review")
    output = Path(result["candidate"]).parent
    handoff = json.loads((output / "handoff.json").read_text())
    changes, vertices = instructions_from_handoff(handoff)
    changed = Path(result["candidate"]).read_bytes().replace(b"OLD 20 2", b"OLD 21 2")
    mutated = read_document(result["candidate"]).from_bytes(changed)
    diff = compare_written_model(read_document(tmp_path / "baseline.inp"), mutated,
        changes, vertex_changes=vertices, evidence=handoff["evidence"])
    assert diff.report["verification"]["matches_expected"] is False


def test_profile_readback_catches_zero_floor(tmp_path: Path) -> None:
    """Prevent the earlier class of incomplete offset-only model profiles."""
    config = project(tmp_path)
    result = run_project(config, output_directory=tmp_path / "review")
    output = Path(result["candidate"]).parent
    prepared = json.loads((output / "prepared.json").read_text())
    handoff = json.loads((output / "handoff.json").read_text())
    prepared["nodes"][0]["floor_m"] += 1
    with pytest.raises(ValueError, match="floor"):
        verify_prepared_profiles(read_document(result["candidate"]), prepared, handoff, load_config(config).data)


def test_csv_protects_text_and_keeps_numeric_values(tmp_path: Path) -> None:
    """Review CSV cannot turn a source formula-like string into a formula."""
    path = tmp_path / "review.csv"
    write_csv(path, [{"asset": "=BAD()", "elevation": -2.0}])
    text = path.read_text(encoding="utf-8-sig")
    assert "'=BAD()" in text and "-2.0" in text


def test_handoff_version_rejected() -> None:
    """Do not interpret unknown protocol versions as current instructions."""
    with pytest.raises(ValueError, match="schema"):
        instructions_from_handoff({"schema_version": "future"})


def test_complete_request_holds_missing_profile(tmp_path: Path) -> None:
    """Missing rim stays held; complete publication cannot silently omit it."""
    config = project(tmp_path)
    value = json.loads(config.read_text())
    packet = json.loads((FIXTURES / "utility_a.json").read_text())
    packet["datasets"]["AccessCovers"]["records"][0]["cover_m"] = None
    data = tmp_path / "missing_rim.json"
    data.write_text(json.dumps(packet), encoding="utf-8")
    value["sources"] = {"network": {"format": "json_packet", "path": str(data), "read_only": True}}
    value["publication"]["mode"] = "complete_request"
    config.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="held mandatory"):
        run_project(config, output_directory=tmp_path / "review")
    assert not (tmp_path / "review" / "completion.json").exists()


def packet_project(tmp_path: Path, packet: dict[str, object], **overrides: object) -> Path:
    """Bind a modified synthetic packet without changing public fixture inputs."""
    config = project(tmp_path, **overrides)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    value = json.loads(config.read_text())
    value["sources"] = {"network": {"format": "json_packet", "path": str(path), "read_only": True}}
    config.write_text(json.dumps(value), encoding="utf-8")
    return config


def test_source_target_crs_requires_explicit_reprojection(tmp_path: Path) -> None:
    """Matching raw/declaration CRS does not make a different target CRS valid."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text())
    for table in packet["datasets"].values():
        table["metadata"]["crs"]["epsg"] = 32610
    config = packet_project(tmp_path, packet)
    value = json.loads(config.read_text())
    value["datasets"] = {name: {"geometry": {"declared_crs": {"epsg": 32610}}} for name in ("pipes", "structures")}
    config.write_text(json.dumps(value))
    with pytest.raises(NotImplementedError, match="horizontal CRS"):
        run_project(config, output_directory=tmp_path / "review")


def test_complete_request_unknown_status_is_hold(tmp_path: Path) -> None:
    """Requested unknown lifecycle cannot disappear into not-selected counts."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text())
    packet["datasets"]["GravitySegments"]["records"][0]["service"] = "unknown"
    config = packet_project(tmp_path, packet, publication={"mode": "complete_request", "output_directory": str(tmp_path / "runs")})
    with pytest.raises(ValueError, match="held mandatory"):
        run_project(config, output_directory=tmp_path / "review")


def test_crossing_both_outside_endpoints_requires_topology_review(tmp_path: Path) -> None:
    """A segment crossing the region remains a hold even with both ends outside."""
    config = project(tmp_path, selection={"scope": {"method": "bounds", "bounds": [-1, 1, 1, 9], "boundary_crossers": "hold"}},
                     publication={"mode": "complete_request", "output_directory": str(tmp_path / "runs")})
    with pytest.raises(ValueError, match="held mandatory"):
        run_project(config, output_directory=tmp_path / "review")


def test_case_different_explicit_existing_node_crosswalk(tmp_path: Path) -> None:
    """Preparation and QA follow solver case-insensitive identity consistently."""
    config = project(tmp_path)
    baseline = tmp_path / "baseline.inp"
    baseline.write_text(BASELINE.replace("OLD 20 2", "OLD 9 3"))
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text(json.dumps({"nodes": {"001": "old"}, "pipes": {}}))
    value = json.loads(config.read_text())
    value["identity"] = {"model_association": {"methods": ["explicit_crosswalk", "asset_id"], "crosswalk_path": str(crosswalk)}}
    config.write_text(json.dumps(value))
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["applied_source_pipes"] == 1
    assert result["new_junctions"] == 1
    candidate = read_document(result["candidate"])
    old = next(r for r in candidate.records("JUNCTIONS") if r.tokens[0] == "OLD")
    assert tuple(map(float, old.tokens[1:3])) == (9, 3)
    findings = json.loads((Path(result["candidate"]).parent / "candidate_qa.json").read_text())["candidate_findings"]
    assert not any(r["rule"] == "disconnected_component" for r in findings)


def test_changed_source_during_preview_has_no_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A preview-to-write race is caught before hydraulic candidate publication."""
    import shmm
    packet = json.loads((FIXTURES / "utility_a.json").read_text())
    config = packet_project(tmp_path, packet)
    original = shmm.preview_model_diff
    def change_after_preview(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        path = tmp_path / "source.json"
        path.write_text(path.read_text() + " ")
        return result
    monkeypatch.setattr(shmm, "preview_model_diff", change_after_preview)
    with pytest.raises(ValueError, match="Source snapshot changed"):
        run_project(config, output_directory=tmp_path / "review")
    assert not (tmp_path / "review" / "candidate.inp").exists()
    assert not (tmp_path / "review" / "completion.json").exists()


def test_future_scenario_is_separate_handoff(tmp_path: Path) -> None:
    """Scenario-specific outputs never share their evidence/operation manifest."""
    config = project(tmp_path, scenarios={"future": {"include_statuses": ["active", "planned"],
        "exclude_statuses": ["retired"], "unknown_status": "hold"}}, selection={"scenario": "future"})
    result = run_project(config, output_directory=tmp_path / "future")
    assert result["scenario"] == "future"
    handoff = json.loads((tmp_path / "future" / "handoff.json").read_text())
    assert handoff["scenario"] == "future" and handoff["applied_source_keys"]


def test_written_engineering_hold_blocks_complete_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-write QA holds cannot certify a complete requested model."""
    import umdev.pipeline as pipeline
    config = project(tmp_path, publication={"mode": "complete_request", "output_directory": str(tmp_path / "runs")})
    original = pipeline.candidate_engineering_qa
    def held_candidate(document: object, config: object) -> list[dict[str, object]]:
        findings = original(document, config)
        if any(row.tokens[0] == "0007" for row in document.records("CONDUITS")):
            findings.append({"rule": "synthetic_manual_hold", "object_key": "model/CONDUITS/0007",
                             "severity": "error", "disposition": "hold"})
        return findings
    monkeypatch.setattr(pipeline, "candidate_engineering_qa", held_candidate)
    with pytest.raises(ValueError, match="written-model engineering holds"):
        run_project(config, output_directory=tmp_path / "review")
    assert (tmp_path / "review" / "candidate_qa.json").exists()
    assert not (tmp_path / "review" / "completion.json").exists()
    html = (tmp_path / "review" / "review.html").read_text()
    assert "synthetic_manual_hold" in html and "candidate_qa.csv" in html


def test_source_changed_during_optional_engine_has_no_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Solver runtime cannot hide a stale source behind a successful file diff."""
    from shmm.engine import EngineCheck
    import shmm.engine as engine
    packet = json.loads((FIXTURES / "utility_a.json").read_text())
    config = packet_project(tmp_path, packet, qa={"engine": {"enabled": True,
        "executable": str(tmp_path / "synthetic-engine.exe"), "version_library": str(tmp_path / "synthetic-engine.dll"),
        "expected_version": 52004}})
    def fake_engine(*args: object, **kwargs: object) -> EngineCheck:
        source = tmp_path / "source.json"
        source.write_text(source.read_text() + " ")
        return EngineCheck(52004, 0, (), "", "", "", None, None, "synthetic", None, None)
    monkeypatch.setattr(engine, "check_engine", fake_engine)
    with pytest.raises(ValueError, match="Source snapshot changed"):
        run_project(config, output_directory=tmp_path / "review")
    assert not (tmp_path / "review" / "completion.json").exists()


@pytest.mark.parametrize("scope", [
    {"method": "bounds", "bounds": [-1, -1, 1, 11], "boundary_crossers": "hold"},
    {"method": "asset_ids", "asset_ids": ["0007"]},
])
def test_out_of_scope_unknown_status_does_not_block_complete_request(tmp_path: Path, scope: dict[str, object]) -> None:
    """Unknown lifecycle outside a proven regional/asset exclusion is irrelevant."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text())
    extra = deepcopy(packet["datasets"]["GravitySegments"]["records"][0])
    extra["segment_text"] = "FAR_UNKNOWN"
    extra["service"] = "unknown"
    extra["start_access"], extra["finish_access"] = "FAR_A", "FAR_B"
    extra["shape"]["coordinates"] = [[1000, 1000], [1000, 1010]]
    for index, name in enumerate(("FAR_A", "FAR_B")):
        node = deepcopy(packet["datasets"]["AccessCovers"]["records"][index])
        node["access_text"] = name
        node["shape"]["coordinates"] = [1000, 1000 + 10 * index]
        packet["datasets"]["AccessCovers"]["records"].append(node)
    packet["datasets"]["GravitySegments"]["records"].append(extra)
    config = packet_project(tmp_path, packet, selection={"scope": scope},
        publication={"mode": "complete_request", "output_directory": str(tmp_path / "runs")})
    result = run_project(config, output_directory=tmp_path / "review")
    assert result["requested_source_pipes"] == result["applied_source_pipes"] == 1
    assert result["scope_or_status_holds"] == 0
