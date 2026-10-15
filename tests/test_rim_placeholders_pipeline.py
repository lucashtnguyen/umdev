"""Check missing-rim publication modes against actual written SWMM profiles.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained tests with synthetic utility inputs only.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from hymo import read_document
import pytest

from test_pipeline import FIXTURES, packet_project
from umdev.pipeline import run_project


def _packet() -> dict[str, Any]:
    """Return independent synthetic source inputs with one unknown rim."""
    packet = json.loads((FIXTURES / "utility_a.json").read_text(encoding="utf-8"))
    packet["datasets"]["AccessCovers"]["records"][0]["cover_m"] = None
    return packet


def _policy(mode: str = "placeholder", *, kind: str = "depth",
            value: float = 12.345, unit: str = "model") -> dict[str, Any]:
    """Declare the sentinel quantity explicitly, including elevation datum."""
    placeholder: dict[str, Any] = {"kind": kind, "value": value, "unit": unit}
    if kind == "elevation":
        placeholder["vertical_datum"] = "SYNTHETIC_DATUM"
    return {"mode": mode, "placeholder": placeholder,
            "basis": "Synthetic missing-rim sentinel; manual survey required."}


def _config(tmp_path: Path, packet: dict[str, Any] | None = None,
            policy: dict[str, Any] | None = None, **overrides: Any) -> Path:
    """Prepare an isolated project without changing shared public fixtures."""
    return packet_project(tmp_path, _packet() if packet is None else packet,
        parameters={"nodes": {"missing_rim": _policy() if policy is None else policy}},
        **overrides)


def _json(path: Path) -> dict[str, Any]:
    """Read a UTF-8 pipeline report."""
    return json.loads(path.read_text(encoding="utf-8"))


def _register(path: Path) -> list[dict[str, str]]:
    """Read the human-review CSV, preserving textual source identifiers."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_placeholder_depth_converts_units_and_preserves_real_rim(tmp_path: Path) -> None:
    """Native-unit 12.345 denotes depth; known rims stay observed."""
    config = _config(tmp_path)
    source_before = (tmp_path / "source.json").read_bytes()
    baseline_before = (tmp_path / "baseline.inp").read_bytes()
    result = run_project(config, output_directory=tmp_path / "review")
    nodes = {record.tokens[0]: record.tokens
             for record in read_document(result["candidate"]).records("JUNCTIONS")}
    assert float(nodes["001"][1]) == pytest.approx(10)
    assert float(nodes["001"][2]) == pytest.approx(12.345)
    assert sum(map(float, nodes["001"][1:3])) == pytest.approx(22.345)
    assert tuple(map(float, nodes["002"][1:3])) == pytest.approx((9, 2))
    assert result["applied_source_pipes"] == 1
    assert result["applied_placeholder_nodes"] == 1
    assert result["engineering_status"] == "review_required"
    assert (tmp_path / "source.json").read_bytes() == source_before
    assert (tmp_path / "baseline.inp").read_bytes() == baseline_before


def test_placeholder_has_traceable_preview_written_and_actual_qa(tmp_path: Path) -> None:
    """Both diff stages expose assumed sentinel values and actual-node QA."""
    result = run_project(_config(tmp_path), output_directory=tmp_path / "review")
    output = Path(result["candidate"]).parent
    for name in ("preview_diff.json", "written_model_diff.json"):
        report = _json(output / name)
        operation = next(row for row in report["requested_operations"]
                         if row["section"] == "JUNCTIONS" and row["object_id"] == "001")
        rim = operation["evidence"]["assignments"]["rim"]
        assert rim["category"] == "assumed"
        assert rim["method"] == "missing_rim_placeholder"
        assert "placeholder_rim" in rim["flags"]
        assert float(operation["after"]["max_depth"]) == pytest.approx(12.345)
        assert "Synthetic missing-rim sentinel" in json.dumps(rim)
    written = _json(output / "written_model_diff.json")
    assert written["verification"]["matches_expected"] is True
    findings = _json(output / "candidate_qa.json")["candidate_findings"]
    assert any(row["rule"] == "placeholder_rim"
               and row["object_key"] == "model/JUNCTIONS/001" for row in findings)
    rows = _register(output / "placeholder_nodes.csv")
    assert len(rows) == 1
    assert rows[0]["disposition"] == "applied"
    assert "001" in rows[0].values()
    assert json.loads(rows[0]["assignment"])["method"] == "missing_rim_placeholder"


def test_placeholder_review_cannot_be_cleared_by_info_severity(tmp_path: Path) -> None:
    """Numerically valid sentinel profiles always require engineering review."""
    informational = {"severity": "info", "disposition": "review"}
    qa = {"engine": {"enabled": False}, "rules": {
        name: informational for name in ("placeholder_rim", "disconnected_component",
            "component_without_outfall", "nodes_without_directed_outfall_route")}}
    result = run_project(_config(tmp_path, qa=qa), output_directory=tmp_path / "review")
    assert result["applied_placeholder_nodes"] == 1
    assert result["engineering_status"] == "review_required"


@pytest.mark.parametrize("policy", [{}, {"mode": "hold"}])
def test_missing_rim_default_or_explicit_hold_omits_asset(
        tmp_path: Path, policy: dict[str, Any]) -> None:
    """Opt-in placeholders cannot silently alter the default withholding policy."""
    result = run_project(_config(tmp_path, policy=policy),
                         output_directory=tmp_path / "review")
    nodes = {record.tokens[0] for record in read_document(result["candidate"]).records("JUNCTIONS")}
    assert "001" not in nodes
    assert result["applied_source_pipes"] == 0
    assert result["held_source_pipes"] == 1
    assert result["applied_placeholder_nodes"] == 0


def test_missing_rim_error_stops_before_candidate_publication(tmp_path: Path) -> None:
    """Strict exception mode publishes neither candidate nor completion."""
    config = _config(tmp_path, policy={"mode": "error"})
    with pytest.raises(ValueError, match="(?i)rim"):
        run_project(config, output_directory=tmp_path / "review")
    assert not (tmp_path / "review" / "candidate.inp").exists()
    assert not (tmp_path / "review" / "completion.json").exists()


def test_placeholder_does_not_supply_a_missing_elevation_datum(tmp_path: Path) -> None:
    """A depth sentinel cannot turn a datum-unknown floor into valid elevation."""
    packet = _packet()
    packet["datasets"]["AccessCovers"]["records"][0]["basis"] = None
    result = run_project(_config(tmp_path, packet), output_directory=tmp_path / "review")
    assert result["held_source_pipes"] == 1
    assert result["applied_placeholder_nodes"] == 0
    nodes = {record.tokens[0] for record in read_document(result["candidate"]).records("JUNCTIONS")}
    assert "001" not in nodes


def test_unknown_floor_is_not_filled_by_missing_rim_policy(tmp_path: Path) -> None:
    """Sentinel support supplies no invented floor or pipe-end elevation."""
    packet = _packet()
    packet["datasets"]["AccessCovers"]["records"][0]["base_m"] = None
    result = run_project(_config(tmp_path, packet), output_directory=tmp_path / "review")
    assert result["applied_source_pipes"] == 0
    assert result["held_source_pipes"] == 1
    assert result["applied_placeholder_nodes"] == 0
    assert "001" not in {row.tokens[0] for row in read_document(result["candidate"]).records("JUNCTIONS")}


def test_existing_junction_keeps_baseline_profile_with_missing_source_rim(tmp_path: Path) -> None:
    """Placeholder mode cannot overwrite an associated existing model profile."""
    config = _config(tmp_path)
    baseline = tmp_path / "baseline.inp"
    baseline.write_text(baseline.read_text(encoding="utf-8").replace("OLD 20 2", "OLD 9 3"),
                        encoding="utf-8")
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text(json.dumps({"nodes": {"001": "OLD"}, "pipes": {}}), encoding="utf-8")
    value = _json(config)
    value["identity"] = {"model_association": {
        "methods": ["explicit_crosswalk", "asset_id"], "crosswalk_path": str(crosswalk)}}
    config.write_text(json.dumps(value), encoding="utf-8")
    result = run_project(config, output_directory=tmp_path / "review")
    node = next(row for row in read_document(result["candidate"]).records("JUNCTIONS")
                if row.tokens[0] == "OLD")
    assert tuple(map(float, node.tokens[1:3])) == pytest.approx((9, 3))
    assert result["applied_placeholder_nodes"] == 0
    assert result["applied_source_pipes"] == 1


@pytest.mark.parametrize("length_unit,flow_units,factor", [("m", "LPS", 1.0), ("ft", "CFS", 0.3048)])
@pytest.mark.parametrize("link_offsets", ["DEPTH", "ELEVATION"])
def test_literal_native_depth_does_not_depend_on_pipe_offset_convention(
        tmp_path: Path, length_unit: str, flow_units: str, factor: float,
        link_offsets: str) -> None:
    """SWMM sentinel lives in MaxDepth for US/SI and either link-offset mode."""
    config = _config(tmp_path, model={"baseline": str(tmp_path / "baseline.inp"),
        "baseline_metadata": {"horizontal_crs": {"epsg": 3857}, "map_unit": "m",
                              "vertical_datum": "SYNTHETIC_DATUM"},
        "target": {"length_unit": length_unit, "flow_units": flow_units,
                   "link_offsets": link_offsets}})
    baseline = tmp_path / "baseline.inp"
    text = baseline.read_text(encoding="utf-8").replace("FLOW_UNITS LPS", "FLOW_UNITS " + flow_units)
    text = text.replace("LINK_OFFSETS DEPTH", "LINK_OFFSETS " + link_offsets)
    if link_offsets == "ELEVATION":
        text = text.replace("OLD_LINK OLD OUT 10 0.013 0 0", "OLD_LINK OLD OUT 10 0.013 20 19")
    baseline.write_text(text, encoding="utf-8")
    result = run_project(config, output_directory=tmp_path / "review")
    candidate = Path(result["candidate"])
    node = next(row for row in read_document(candidate).records("JUNCTIONS")
                if row.tokens[0] == "001")
    assert float(node.tokens[1]) == pytest.approx(10 / factor)
    assert node.tokens[2] == "12.345"
    assert "UMDEV_PLACEHOLDER_RIM" in candidate.read_text(encoding="utf-8")
    assert "12.345" in candidate.read_text(encoding="utf-8")
    assert result["applied_placeholder_nodes"] == 1


def test_held_pipe_does_not_publish_or_count_its_placeholder_node(tmp_path: Path) -> None:
    """An unrelated pipe hold cannot leave an orphan synthetic manhole behind."""
    packet = _packet()
    packet["datasets"]["GravitySegments"]["records"][0]["wall"] = "UNKNOWN_MATERIAL"
    result = run_project(_config(tmp_path, packet), output_directory=tmp_path / "review")
    candidate = Path(result["candidate"])
    assert result["applied_source_pipes"] == 0
    assert result["held_source_pipes"] == 1
    assert result["applied_placeholder_nodes"] == 0
    assert "001" not in {row.tokens[0] for row in read_document(candidate).records("JUNCTIONS")}
    assert "UMDEV_PLACEHOLDER_RIM" not in candidate.read_text(encoding="utf-8")
    rows = _register(tmp_path / "review" / "placeholder_nodes.csv")
    assert len(rows) == 1
    assert rows[0]["disposition"] == "held"
