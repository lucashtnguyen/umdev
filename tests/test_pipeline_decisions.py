"""Verify consolidated decisions and strictly read-only project preparation.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained; all utility records are synthetic.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from hymo import read_document

from test_pipeline import project
from umdev import prepare_project
from umdev.configuration import ConfigurationError, load_config
from umdev.models import compile_handoff
from umdev.pipeline import run_project


def configure(path: Path, additions: dict[str, Any]) -> None:
    """Merge top-level private caller settings into a synthetic project."""
    value = json.loads(path.read_text())
    for key, item in additions.items():
        if isinstance(item, dict):
            value.setdefault(key, {}).update(item)
        else:
            value[key] = item
    path.write_text(json.dumps(value), encoding="utf-8")


def decision(identity: str, kind: str, row: dict[str, Any], **payload: Any) -> dict[str, Any]:
    """Construct a fully attributed synthetic authorization record."""
    return {"id": identity, "status": "approved", "kind": kind, "scenario": "existing",
        "recorded_by": "Synthetic recorder", "recorded_date": "2026-10-15",
        "authorization": {"basis": "Synthetic test instruction", "reference": "test fixture",
                          "reviewer": "Synthetic reviewer", "date": "2026-10-15"},
        "rationale": "Synthetic independent QA change.",
        "evidence": [{"source_key": row["key"], "sha256": row["source_record_sha256"]}], **payload}


def ledger_project(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Create an override, a model update and a non-executable historical record."""
    config = project(tmp_path)
    initial = prepare_project(config)
    canonical = initial["canonical"]
    node = next(row for row in canonical["nodes"] if row["asset_id"] == "001")
    pipe = canonical["pipes"][0]
    override = decision("rim-review", "parameter_override", node, override={
        "source_key": node["key"], "field": "rim", "value": 12.5, "unit": "m",
        "vertical_datum": "SYNTHETIC_DATUM"})
    model_update = decision("legacy-diameter", "model_update", pipe,
        baseline_sha256=initial["baseline_sha256"], units={"geom1": "m"},
        change={"action": "update", "section": "XSECTIONS", "object_id": "OLD_LINK",
                "values": {"geom1": 0.4}, "expected": {"geom1": 0.3}, "source_key": "original-review"})
    record = decision("intent-only", "review_record", pipe, record={"intent": "Investigate another branch later"})
    proposal = deepcopy(model_update)
    proposal.update(id="unapproved-length", status="proposed", units={"length": "m"}, change={
        "action": "update", "section": "CONDUITS", "object_id": "OLD_LINK",
        "values": {"length": 99}, "expected": {"length": 10}, "source_key": "proposal"})
    ledger = {"schema_version": 1, "kind": "umdev_manual_decision_ledger",
              "decisions": [override, model_update, record, proposal]}
    ledger_path = tmp_path / "decisions.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    configure(config, {"review": {"ledger_path": "decisions.json"},
        "parameters": {"nodes": {"rim_methods": ["reviewed_override", "structure_rim"]}}})
    return config, ledger_path, ledger


def test_prepare_project_never_writes_or_calls_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete queued plan is inspectable without output, preview or engine work."""
    config, _, _ = ledger_project(tmp_path)
    configure(config, {"qa": {"engine": {"enabled": True, "executable": "unused-engine.exe",
        "version_library": "unused-engine.dll", "expected_version": "5.2.4"}}})
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Read-only preparation invoked a model operation")
    monkeypatch.setattr("shmm.preview_model_diff", forbidden)
    monkeypatch.setattr("shmm.edit_model", forbidden)
    monkeypatch.setattr("shmm.engine.check_engine", forbidden)
    bundle = prepare_project(config)
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert not (tmp_path / "results").exists()
    assert bundle["decision_ledger_audit"]["override_count"] == 1
    assert bundle["decision_ledger_audit"]["model_update_count"] == 1
    prepared = next(row for row in bundle["prepared"]["nodes"] if row["asset_id"] == "001")
    assert prepared["rim_m"] == pytest.approx(12.5)
    source = next(row for row in bundle["canonical"]["nodes"] if row["asset_id"] == "001")
    assert source["rim"] == pytest.approx(12)
    assert len(bundle["applied_source_keys"]) == 1  # Planned compiler acceptance only.


def test_ledger_run_publishes_snapshot_and_both_diffs(tmp_path: Path) -> None:
    """Only approved executable records affect the separately written candidate."""
    config, ledger_path, ledger = ledger_project(tmp_path)
    before = (tmp_path / "baseline.inp").read_bytes()
    result = run_project(config, output_directory=tmp_path / "review")
    output = tmp_path / "review"
    assert json.loads((output / "decision_ledger.json").read_text()) == ledger
    audit = json.loads((output / "decision_ledger_audit.json").read_text())
    assert audit["summary"]["record_only"] == 1
    assert audit["summary"]["not_approved"] == 1
    handoff = json.loads((output / "handoff.json").read_text())
    assert str(ledger_path) in handoff["side_input_hashes"]
    assert handoff["evidence"]["decision/legacy-diameter"]["original_instruction_source_key"] == "original-review"
    candidate = read_document(result["candidate"])
    xsections = {row.tokens[0]: row.tokens for row in candidate.records("XSECTIONS")}
    links = {row.tokens[0]: row.tokens for row in candidate.records("CONDUITS")}
    nodes = {row.tokens[0]: row.tokens for row in candidate.records("JUNCTIONS")}
    assert float(xsections["OLD_LINK"][2]) == pytest.approx(0.4)
    assert float(links["OLD_LINK"][3]) == pytest.approx(10)
    assert float(nodes["001"][1]) + float(nodes["001"][2]) == pytest.approx(12.5)
    assert (tmp_path / "baseline.inp").read_bytes() == before
    for name in ("preview_diff.json", "written_model_diff.json"):
        assert (output / name).exists()
    assert json.loads((output / "written_model_diff.json").read_text())["verification"]["matches_expected"]


@pytest.mark.parametrize("failure", ["source", "baseline", "expected"])
def test_stale_decisions_fail_before_output(tmp_path: Path, failure: str) -> None:
    """Changed evidence, baseline and old field values cannot create artifacts."""
    config, ledger_path, ledger = ledger_project(tmp_path)
    row = ledger["decisions"][1]
    if failure == "source":
        row["evidence"][0]["sha256"] = "0" * 64
    elif failure == "baseline":
        row["baseline_sha256"] = "0" * 64
    else:
        row["change"]["expected"]["geom1"] = 0.2
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        prepare_project(config)
    with pytest.raises(ValueError):
        run_project(config, output_directory=tmp_path / "must-not-exist")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("legacy", ["path", "inline", "changes"])
def test_ledger_rejects_parallel_legacy_inputs(tmp_path: Path, legacy: str) -> None:
    """A run has exactly one source of manually approved instructions."""
    config = project(tmp_path)
    values: dict[str, Any] = {"review": {"ledger_path": "decisions.json"}}
    if legacy == "path":
        values["review"]["overrides_path"] = "old.json"
    elif legacy == "inline":
        values["review"]["overrides"] = [{"source_key": "synthetic"}]
    else:
        values["model"] = {"changes_path": "old.json"}
    configure(config, values)
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        load_config(config)


def test_ledger_paths_retain_declaration_origin_and_empty_legacy_inputs(tmp_path: Path) -> None:
    """Overlay resolution never rebases an inherited ledger onto the caller file."""
    config = project(tmp_path)
    folder = tmp_path / "policies"
    folder.mkdir()
    policy = folder / "review.json"
    policy.write_text(json.dumps({"review": {"ledger_path": "ledger.json", "overrides_path": "", "overrides": []},
                                  "model": {"changes_path": ""}}), encoding="utf-8")
    value = json.loads(config.read_text())
    value["extends"].append(str(policy))
    config.write_text(json.dumps(value), encoding="utf-8")
    resolved = load_config(config)
    assert resolved.data["review"]["ledger_path"] == str(folder / "ledger.json")
    assert resolved.data["review"]["overrides_path"] == ""
    assert resolved.data["model"]["changes_path"] == ""


def test_legacy_finished_changes_remain_read_only_and_compatible(tmp_path: Path) -> None:
    """Existing callers retain their finished-instruction route without a ledger."""
    config = project(tmp_path)
    changes = [{"action": "update", "section": "XSECTIONS", "object_id": "OLD_LINK",
                "values": {"geom1": 0.4}, "expected": {"geom1": 0.3}, "source_key": "legacy-change"}]
    (tmp_path / "changes.json").write_text(json.dumps(changes), encoding="utf-8")
    configure(config, {"model": {"changes_path": "changes.json"}})
    bundle = prepare_project(config)
    assert bundle["decision_ledger"] is None
    assert any(row["source_key"] == "legacy-change" for row in bundle["handoff"]["changes"])
    assert not (tmp_path / "results").exists()
    result = run_project(config, output_directory=tmp_path / "legacy-review")
    assert result["status"] == "verified_model_update"
    assert not (tmp_path / "legacy-review" / "decision_ledger.json").exists()


def test_compiler_rejects_competing_instruction_sources_and_prepared_fields(tmp_path: Path) -> None:
    """Frozen manual edits cannot replace a field already supplied by preparation."""
    config = project(tmp_path)
    bundle = prepare_project(config)
    cfg = deepcopy(bundle["config"])
    instructions = {"changes": [{"action": "update", "section": "XSECTIONS", "object_id": "0007",
        "values": {"geom1": 0.4}, "expected": {"geom1": 0.3}, "source_key": "decision/test"}],
        "evidence": {}, "side_input_hashes": {}}
    with pytest.raises(ValueError, match="conflicts with a prepared model field"):
        compile_handoff(bundle["baseline"], bundle["prepared"], cfg,
                        config_sha256="synthetic", decision_instructions=instructions)
    cfg["model"]["changes_path"] = "disabled-legacy.json"
    with pytest.raises(ValueError, match="legacy"):
        compile_handoff(bundle["baseline"], bundle["prepared"], cfg,
                        config_sha256="synthetic", decision_instructions={})


def test_read_only_preparation_exposes_complete_request_holds(tmp_path: Path) -> None:
    """Missing mandatory parameters remain inspectable without publication gates."""
    config = project(tmp_path, qa={"allow_categories": ["observed"]},
                     publication={"mode": "complete_request", "output_directory": str(tmp_path / "results")})
    bundle = prepare_project(config)
    assert len(bundle["requested_holds"]) == 1
    assert bundle["applied_source_keys"] == []
    assert not (tmp_path / "results").exists()
    with pytest.raises(ValueError, match="held mandatory assignments"):
        run_project(config, output_directory=tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.parametrize("methods", [["structure_rim"], ["structure_rim", "reviewed_override"]])
def test_active_approval_must_be_selected_by_field_policy(tmp_path: Path, methods: list[str]) -> None:
    """Absent or lower-ranked override methods cannot silently ignore approval."""
    config, _, _ = ledger_project(tmp_path)
    configure(config, {"parameters": {"nodes": {"rim_methods": methods}}})
    with pytest.raises(ValueError, match="not selected by field policy or preserved baseline"):
        prepare_project(config)
    assert not (tmp_path / "results").exists()


def test_active_approval_cannot_bypass_preserved_existing_rim(tmp_path: Path) -> None:
    """A valid approved rim does not silently override the baseline profile policy."""
    config, ledger_path, ledger = ledger_project(tmp_path)
    baseline = tmp_path / "baseline.inp"
    baseline.write_text(baseline.read_text().replace("[OUTFALLS]", "001 10 2\n[OUTFALLS]"), encoding="utf-8")
    ledger["decisions"] = [ledger["decisions"][0]]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="not selected by field policy or preserved baseline"):
        prepare_project(config)
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("field", ["floor", "rim", "length", "diameter", "upstream_invert", "downstream_invert"])
def test_all_supported_approval_fields_require_selected_review_method(tmp_path: Path, field: str) -> None:
    """Every supported override maps to its actual prepared assignment method."""
    config = project(tmp_path)
    initial = prepare_project(config)
    canonical = initial["canonical"]
    pipe = canonical["pipes"][0]
    source = next(row for row in canonical["nodes"] if row["asset_id"] == "001") if field in {"floor", "rim"} else pipe
    values = {"floor": 10.1, "rim": 12.5, "length": 12, "diameter": 0.35,
              "upstream_invert": 10.1, "downstream_invert": 9.6}
    value: dict[str, Any] = {"source_key": source["key"], "field": field, "value": values[field], "unit": "m"}
    if field not in {"length", "diameter"}:
        value["vertical_datum"] = "SYNTHETIC_DATUM"
    approval = decision("unused-approval", "parameter_override", source, override=value)
    if field.endswith("_invert"):
        endpoint = pipe["from_node" if field == "upstream_invert" else "to_node"]
        node = next(row for row in canonical["nodes"] if row["asset_id"] == endpoint)
        approval["evidence"].append({"source_key": node["key"], "sha256": node["source_record_sha256"]})
    ledger = {"schema_version": 1, "kind": "umdev_manual_decision_ledger", "decisions": [approval]}
    (tmp_path / "decisions.json").write_text(json.dumps(ledger), encoding="utf-8")
    configure(config, {"review": {"ledger_path": "decisions.json"}})
    with pytest.raises(ValueError, match="not selected by field policy or preserved baseline"):
        prepare_project(config)
    assert not (tmp_path / "results").exists()
