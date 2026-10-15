"""Check local baseline ends and convergent, read-only floor donor planning.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained synthetic engineering fixtures; no private project inputs.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest

from test_pipeline import project
from test_pipeline_decisions import configure
from umdev import prepare_project
from umdev.models import baseline_pipe_end_parameters


@pytest.mark.parametrize(("convention", "unit", "flow", "expected"), [
    ("DEPTH", "m", "LPS", [10.2, 8.3, 8.4, 7.5, 7.6, 6.7]),
    ("ELEVATION", "m", "LPS", [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]),
    ("DEPTH", "ft", "MGD", [value * 0.3048 for value in [10.2, 8.3, 8.4, 7.5, 7.6, 6.7]]),
])
def test_baseline_context_reconstructs_each_local_end_and_node_family(tmp_path: Path,
    convention: str, unit: str, flow: str, expected: list[float]) -> None:
    """US/DS offsets reference their own node floors, never the opposite end."""
    initial = prepare_project(project(tmp_path))
    config = deepcopy(initial["config"])
    config["model"]["target"].update(flow_units=flow, length_unit=unit, link_offsets=convention)
    text = f"""[OPTIONS]
FLOW_UNITS {flow}
LINK_OFFSETS {convention}
[JUNCTIONS]
J 10 3
[STORAGE]
S 8 4 0 FUNCTIONAL 1 0 0
[DIVIDERS]
D 7 P3 CUTOFF 0
[OUTFALLS]
O 6 FREE
[CONDUITS]
P1 j s 10 0.013 0.2 0.3
P2 S D 10 0.013 0.4 0.5
P3 D O 10 0.013 0.6 0.7
[PUMPS]
NO_PIPE_END J O CURVE ON
[WEIRS]
NO_PIPE_END_EITHER J O TRANSVERSE 2 1 NO 0 0
"""
    document = initial["baseline"].from_bytes(text.encode())
    context = baseline_pipe_end_parameters(document, config)
    by_link = {(row["model_id"], row["end"]): row for rows in context.values() for row in rows}
    ordered = [by_link[(identity, end)]["invert_m"] for identity in ["P1", "P2", "P3"]
               for end in ["upstream", "downstream"]]
    assert ordered == pytest.approx(expected)
    assert set(context) == {"J", "S", "D", "O"}
    assert len(by_link) == 6
    assert by_link[("P1", "upstream")]["node_id"] == "J"
    assert all(row["basis"] == "baseline_model" for row in by_link.values())
    assert all(row["baseline_sha256"] == sha256(document.data).hexdigest() for row in by_link.values())


@pytest.mark.parametrize("mutation", ["star", "nan", "missing_offset", "missing_node", "duplicate_node", "duplicate_pipe"])
def test_baseline_context_rejects_unusable_or_ambiguous_values(tmp_path: Path, mutation: str) -> None:
    """Unsupported offsets and ambiguous identities never become invented zeros."""
    initial = prepare_project(project(tmp_path))
    text = initial["baseline"].data.decode()
    replacements = {
        "star": ("10 0.013 0 0", "10 0.013 * 0"),
        "nan": ("10 0.013 0 0", "10 0.013 nan 0"),
        "missing_offset": ("10 0.013 0 0", "10 0.013 0"),
        "missing_node": ("OLD_LINK OLD OUT 10", "OLD_LINK MISSING OUT 10"),
        "duplicate_node": ("OLD 20 2", "OLD 20 2\nold 20 2"),
        "duplicate_pipe": ("OLD_LINK OLD OUT 10 0.013 0 0", "OLD_LINK OLD OUT 10 0.013 0 0\nold_link OLD OUT 10 0.013 0 0"),
    }
    text = text.replace(*replacements[mutation])
    with pytest.raises(ValueError):
        baseline_pipe_end_parameters(initial["baseline"].from_bytes(text.encode()), initial["config"])


def synthetic_floor_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            *, shared_rim: float = 12) -> tuple[Path, dict[str, Any]]:
    """Create two pipes sharing a node; one cannot rewire an existing baseline link."""
    config_path = project(tmp_path)
    initial = prepare_project(config_path)
    canonical = deepcopy(initial["canonical"])
    node_template, pipe_template = canonical["nodes"][0], canonical["pipes"][0]
    canonical["nodes"] = []
    for identity, xy, rim in (("A", [0, 0], shared_rim), ("B", [10, 0], 11), ("C", [-10, 0], 12)):
        node = deepcopy(node_template)
        node.update(key=f"network/structures/{identity}", asset_id=identity, floor=None, rim=rim, coordinates=xy)
        node.pop("model_id", None)
        node.pop("model_association", None)
        canonical["nodes"].append(node)
    canonical["pipes"] = []
    for identity, start, finish, us, ds, geometry, model_id in (
        ("ACCEPT", "A", "B", 10, 9.5, [[0, 0], [10, 0]], "ACCEPT"),
        ("REWIRE", "C", "A", 10, 8, [[-10, 0], [0, 0]], "OLD_LINK"),
    ):
        pipe = deepcopy(pipe_template)
        pipe.update(key=f"network/pipes/{identity}", asset_id=identity, from_node=start, to_node=finish,
                    upstream_invert=us, downstream_invert=ds, vertical_datum="SYNTHETIC_DATUM",
                    coordinates=geometry, model_id=model_id)
        pipe.pop("model_association", None)
        canonical["pipes"].append(pipe)
    canonical["ports"] = []
    configure(config_path, {"parameters": {"nodes": {"floor_methods": ["lowest_structure_port", "lowest_applied_pipe_end"]}},
        "ports": {"observations": [], "assignment": {"precedence": ["pipe_numeric"]}}})
    monkeypatch.setattr("umdev.pipeline.ingest_sources", lambda resolved: deepcopy(canonical))
    return config_path, canonical


def test_writer_rejection_removes_floor_donor_without_preview_or_model_write(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch) -> None:
    """The lower end from an unsupported rewire cannot set the accepted pipe's floor."""
    config, _ = synthetic_floor_project(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Read-only floor planning invoked SHMM model operations")
    monkeypatch.setattr("shmm.preview_model_diff", forbidden)
    monkeypatch.setattr("shmm.edit_model", forbidden)
    bundle = prepare_project(config)
    nodes = {row["asset_id"]: row for row in bundle["prepared"]["nodes"]}
    pipes = {row["asset_id"]: row for row in bundle["prepared"]["pipes"]}
    assert nodes["A"]["floor_m"] == pytest.approx(10)
    assert pipes["ACCEPT"]["inlet_offset_m"] == pytest.approx(0)
    assert pipes["REWIRE"]["disposition"] == "hold"
    assert "directed_rewire_is_not_supported" in pipes["REWIRE"]["reasons"]
    assert bundle["applied_source_keys"] == ["network/pipes/ACCEPT"]
    assert len(bundle["membership"]) == 2
    assert len(bundle["requested_holds"]) == 1
    assert bundle["floor_donor_audit"]["iterations"] == 2
    assert bundle["floor_donor_audit"]["baseline_model_end_count"] == 2
    assert bundle["floor_donor_audit"]["accepted_source_keys"] == bundle["applied_source_keys"]
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_donor_removal_cascades_and_retains_first_writer_rejection(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch) -> None:
    """A newly exposed incomplete node triggers another pass; rejected pipes cannot revive."""
    config, _ = synthetic_floor_project(tmp_path, monkeypatch, shared_rim=9.8)
    bundle = prepare_project(config)
    assert bundle["applied_source_keys"] == []
    assert len(bundle["requested_holds"]) == 2
    audit = bundle["floor_donor_audit"]
    assert audit["iterations"] == 3
    rejections = {row["source_key"]: row for row in audit["rejections"]}
    assert rejections["network/pipes/REWIRE"]["iteration"] == 1
    assert rejections["network/pipes/ACCEPT"]["iteration"] == 2
    held = {row["asset_id"]: row for row in bundle["prepared"]["held"] if row["key"].startswith("network/pipes/")}
    assert "directed_rewire_is_not_supported" in held["REWIRE"]["reasons"]
    assert "upstream:incomplete_node_profile" in held["ACCEPT"]["reasons"]
    assert not (tmp_path / "results").exists()


def test_baseline_context_rejects_finite_operands_with_overflowing_absolute_end(tmp_path: Path) -> None:
    """Finite elevation and offset values still cannot produce infinite evidence."""
    initial = prepare_project(project(tmp_path))
    text = initial["baseline"].data.decode().replace("OLD 20 2", "OLD 1e308 2").replace(
        "10 0.013 0 0", "10 0.013 1e308 0")
    with pytest.raises(ValueError, match="absolute pipe-end elevation is nonfinite"):
        baseline_pipe_end_parameters(initial["baseline"].from_bytes(text.encode()), initial["config"])


@pytest.mark.parametrize(("section", "action", "values"), [
    ("CONDUITS", "update", {"inlet_node": "OUT"}),
    ("CONDUITS", "update", {"outlet_node": "OLD"}),
    ("CONDUITS", "update", {"inlet_offset": 0.2}),
    ("CONDUITS", "update", {"outlet_offset": 0.2}),
    ("CONDUITS", "add", {"inlet_node": "OLD", "outlet_node": "OUT", "length": 10,
                          "roughness": 0.013, "inlet_offset": 0, "outlet_offset": 0}),
    ("CONDUITS", "delete", {}),
    ("JUNCTIONS", "update", {"invert_elevation": 21}),
])
def test_legacy_hydraulic_end_changes_fail_before_any_outputs(tmp_path: Path, section: str,
    action: str, values: dict[str, Any]) -> None:
    """Independent legacy edits cannot invalidate the applied local-end context."""
    config = project(tmp_path)
    target = "OLD" if section == "JUNCTIONS" else "OLD_LINK"
    packet = [{"action": action, "section": section, "object_id": target,
               "values": values, "source_key": "legacy-review"}]
    (tmp_path / "legacy.json").write_text(json.dumps(packet), encoding="utf-8")
    configure(config, {"parameters": {"nodes": {"floor_methods": ["structure_floor", "lowest_applied_pipe_end"]}},
                       "model": {"changes_path": "legacy.json"}})
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Legacy finished changes affecting hydraulic pipe ends"):
        prepare_project(config)
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_legacy_dimension_updates_retain_current_file_fingerprint(tmp_path: Path) -> None:
    """Length and diameter edits do not change local pipe-end elevations."""
    config = project(tmp_path)
    path = tmp_path / "legacy.json"
    packet = [{"action": "update", "section": section, "object_id": "OLD_LINK",
               "values": values, "expected": expected, "source_key": "legacy-" + section}
              for section, values, expected in [("CONDUITS", {"length": 11}, {"length": 10}),
                                                ("XSECTIONS", {"geom1": 0.4}, {"geom1": 0.3})]]
    path.write_text(json.dumps(packet), encoding="utf-8")
    configure(config, {"parameters": {"nodes": {"floor_methods": ["structure_floor", "lowest_applied_pipe_end"]}},
                       "model": {"changes_path": "legacy.json"}})
    bundle = prepare_project(config)
    assert {"legacy-CONDUITS", "legacy-XSECTIONS"} <= {row["source_key"] for row in bundle["handoff"]["changes"]}
    assert bundle["handoff"]["side_input_hashes"][str(path)] == sha256(path.read_bytes()).hexdigest()
    assert not (tmp_path / "results").exists()


def test_prepared_connectivity_excludes_frozen_rejected_bridge(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected but independently well-formed bridge cannot join planned components."""
    config, canonical = synthetic_floor_project(tmp_path, monkeypatch)
    nodes = {row["asset_id"]: row for row in canonical["nodes"]}
    nodes["C"]["coordinates"] = [20, 0]
    fourth = deepcopy(nodes["C"])
    fourth.update(key="network/structures/D", asset_id="D", coordinates=[30, 0])
    canonical["nodes"].append(fourth)
    bridge = canonical["pipes"][1]
    bridge.update(from_node="B", to_node="C", upstream_invert=9.5, downstream_invert=9,
                  coordinates=[[10, 0], [20, 0]])
    extra = deepcopy(bridge)
    extra.update(key="network/pipes/SECOND", asset_id="SECOND", model_id="SECOND",
                 from_node="C", to_node="D", upstream_invert=9, downstream_invert=8.5,
                 coordinates=[[20, 0], [30, 0]])
    canonical["pipes"].append(extra)
    bundle = prepare_project(config)
    assert set(bundle["applied_source_keys"]) == {"network/pipes/ACCEPT", "network/pipes/SECOND"}
    findings = [row for row in bundle["prepared"]["findings"] if row["rule"] == "disconnected_component"]
    assert {tuple(row["node_ids"]) for row in findings} == {("A", "B"), ("C", "D")}


def test_legacy_changes_cannot_be_replaced_between_floor_passes(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch) -> None:
    """An external instruction packet has one frozen fingerprint for all passes."""
    from umdev.models import compile_handoff
    config, _ = synthetic_floor_project(tmp_path, monkeypatch)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([{ "action": "update", "section": "XSECTIONS", "object_id": "OLD_LINK",
        "values": {"geom1": 0.4}, "source_key": "legacy-diameter"}]), encoding="utf-8")
    configure(config, {"model": {"changes_path": "legacy.json"}})
    calls = 0
    def replace_after_first_compile(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        handoff = compile_handoff(*args, **kwargs)
        calls += 1
        if calls == 1:
            path.write_text(path.read_text(encoding="utf-8").replace("0.4", "0.5"), encoding="utf-8")
        return handoff
    monkeypatch.setattr("umdev.pipeline.compile_handoff", replace_after_first_compile)
    with pytest.raises(ValueError, match="Legacy finished instructions changed during preparation"):
        prepare_project(config)
    assert calls == 2
    assert not (tmp_path / "results").exists()
