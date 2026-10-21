"""Check exact native quantity relocation, split hydrographs and source guards.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Fictional records only; neither source data nor model files are written.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from typing import Any

import pytest
from hymo import SWMMDocument

from umdev.lifecycle import lifecycle_record_fingerprint
from umdev.load_migration import node_load_fingerprint, plan_load_migration


def document(loads: str = "", *, units: str = "CFS", nodes: tuple[str, ...] = ("A", "B", "C", "D"),
             definitions: bool = True, external: bool = False) -> SWMMDocument:
    """Construct a complete synthetic native catalog without external file reads."""
    header = "[OPTIONS]\nFLOW_UNITS " + units + "\n[JUNCTIONS]\n"
    header += "".join(node + " 100 10 0 0 0\n" for node in nodes)
    catalog = ('[PATTERNS]\nP DAILY 1 1 1 1 1 1 1\nQ DAILY 2 2 2 2 2 2 2\n'
               '[TIMESERIES]\n' + ('TS FILE "unchanged missing forcing.dat"\n' if external else 'TS 01/01/2000 0:00 0\nTS 01/01/2000 1:00 10\n')
               + 'OTHER 01/01/2000 0:00 1\nRAIN 01/01/2000 0:00 0\n'
               '[HYDROGRAPHS]\nUH RG\nUH ALL SHORT .1 1 2 0 0 0\nUH2 RG\nUH2 ALL SHORT .2 1 2 0 0 0\n'
               '[RAINGAGES]\nRG INTENSITY 1:00 1 TIMESERIES RAIN\n')
    return SWMMDocument.from_bytes((header + loads + (catalog if definitions else "")).encode())


def native_node_hash(doc: SWMMDocument, node: str) -> str:
    """Bind an exact declared fictional junction record."""
    tokens = next(r.tokens for r in doc.records("JUNCTIONS") if r.tokens[0].upper() == node.upper())
    return lifecycle_record_fingerprint("JUNCTIONS", tokens)


def mapping(source: SWMMDocument, target: SWMMDocument, *, node: str = "A",
            destinations: tuple[tuple[str, str], ...] = (("B", "1"),)) -> dict[str, Any]:
    """Review both endpoint records and full load/definition fingerprints."""
    return {"id": "mapping-" + node, "source_node_id": node,
        "source_node_record_sha256": native_node_hash(source, node),
        "source_load_fingerprint": node_load_fingerprint(source, node),
        "destinations": [{"node_id": key, "node_record_sha256": native_node_hash(target, key),
                          "load_fingerprint": node_load_fingerprint(target, key), "weight": weight}
                         for key, weight in destinations],
        "basis": "Explicit fictional catchment allocation, not inferred by proximity",
        "reviewer": "Synthetic technical reviewer", "date": "2026-10-21", "decision": "approved"}


def rows(plan: dict[str, Any], section: str) -> dict[str, list[str]]:
    """Index the intended native section after successful planning."""
    assert plan["status"] == "ready" and plan["executable_intent"]
    assert plan["sections"] == plan["executable_intent"]["sections"] == plan["proposed_sections"]
    return {row[0]: row for row in plan["sections"][section]}


def test_full_quantity_migration_preserves_existing_targets_and_unrelated_records() -> None:
    """DWF patterns, external hydrograph coefficients and UH areas stay separate."""
    source = document('[DWF]\nA FLOW .1 P\n[INFLOWS]\nA FLOW TS FLOW 1.0 2 3 P\n[RDII]\nA UH 12\n')
    target = document('[DWF]\nB FLOW .2 P\nC FLOW .333 Q\n[INFLOWS]\nB FLOW TS FLOW 1 1.5 7 P\nC FLOW OTHER FLOW 1 4 5 Q\n[RDII]\nB UH 6\nC UH2 9\n')
    reviewed = [mapping(source, target)]; before = deepcopy(reviewed)
    source_bytes, target_bytes = source.data, target.data
    result = plan_load_migration(source, target, retired_node_ids=["A"], mappings=reviewed, context_sha256="a" * 64)
    assert rows(result, "DWF")["B"] == ["B", "FLOW", "0.3", "P"]
    assert rows(result, "INFLOWS")["B"] == ["B", "FLOW", "TS", "FLOW", "1", "3.5", "10", "P"]
    assert rows(result, "RDII")["B"] == ["B", "UH", "18"]
    for section in ("DWF", "INFLOWS", "RDII"):
        assert rows(result, section)["C"] == list(next(r.tokens for r in target.records(section) if r.tokens[0] == "C"))
    assert all(row["passed"] for row in result["conservation"])
    assert result["source_model_sha256"] == sha256(source_bytes).hexdigest()
    assert result["target_model_sha256"] == sha256(target_bytes).hexdigest()
    assert reviewed == before and source.data == source_bytes and target.data == target_bytes


def test_explicit_split_scales_both_external_hydrograph_terms() -> None:
    """Hand-check q=sf*T+base*P at arbitrary values, not just constant totals."""
    source = document('[INFLOWS]\nA FLOW TS FLOW 1 2 3 P\n')
    target = document()
    reviewed = mapping(source, target, destinations=(("B", ".25"), ("C", ".75")))
    result = plan_load_migration(source, target, retired_node_ids=["A"], mappings=[reviewed])
    output = rows(result, "INFLOWS")
    assert output["B"] == ["B", "FLOW", "TS", "FLOW", "1", "0.5", "0.75", "P"]
    assert output["C"] == ["C", "FLOW", "TS", "FLOW", "1", "1.5", "2.25", "P"]
    for series, pattern in ((0, 1), (4, 2), (13, .5)):
        original = 2 * series + 3 * pattern
        migrated = sum(float(row[5]) * series + float(row[6]) * pattern for row in output.values())
        assert migrated == pytest.approx(original)
    assert result["conservation"][0]["before"] == result["conservation"][0]["allocated"]


def test_split_dwf_average_and_rdii_area_preserve_native_units_and_references() -> None:
    """Quantity weights do not alter unit-hydrograph or dry-weather pattern data."""
    source = document('[DWF]\nA FLOW .1 P\n[RDII]\nA UH 12\n')
    target = document()
    reviewed = mapping(source, target, destinations=(("B", ".25"), ("C", ".75")))
    result = plan_load_migration(source, target, mappings=[reviewed])
    assert rows(result, "DWF")["B"][2:] == ["0.025", "P"]
    assert rows(result, "DWF")["C"][2:] == ["0.075", "P"]
    assert rows(result, "RDII")["B"][1:] == ["UH", "3"]
    assert rows(result, "RDII")["C"][1:] == ["UH", "9"]
    assert {r["units"] for r in result["conservation"]} == {"CFS", "acres"}


def test_retained_source_node_can_have_explicit_load_reassignment() -> None:
    """A retired pipe does not imply its still-connected node must be deleted."""
    source = document('[INFLOWS]\nA FLOW "" FLOW 1 1 .001\n')
    reviewed = mapping(source, source)
    result = plan_load_migration(source, source, retired_node_ids=[], mappings=[reviewed])
    assert "A" not in rows(result, "INFLOWS") and rows(result, "INFLOWS")["B"][6] == ".001"
    assert result["executable_intent"]["clear_source_node_ids"] == ["A"]
    assert any(r.tokens[0] == "A" for r in source.records("JUNCTIONS"))


def test_rebuilt_destination_is_bound_to_the_explicit_target_document() -> None:
    """The planner assigns no node geometry and requires its native target record."""
    source = document('[INFLOWS]\nA FLOW "" FLOW 1 1 .001\n')
    target = document(nodes=("B", "C", "NEW"))
    reviewed = mapping(source, target, destinations=(("NEW", "1"),))
    result = plan_load_migration(source, target, retired_node_ids=["A"], mappings=[reviewed])
    assert rows(result, "INFLOWS")["NEW"][6] == ".001"
    assert result["executable_intent"]["affected_target_node_ids"] == ["NEW"]


def test_many_sources_merge_without_duplicate_native_slots() -> None:
    """One destination FLOW record represents the exact combined compatible input."""
    source = document('[INFLOWS]\nA FLOW "" FLOW 1 1 .001\nC FLOW "" FLOW 1 1 .002\n')
    target = document('[INFLOWS]\nB FLOW "" FLOW 1 1 .003\n')
    reviewed = [mapping(source, target), mapping(source, target, node="C")]
    result = plan_load_migration(source, target, mappings=reviewed)
    assert len(rows(result, "INFLOWS")) == 1 and rows(result, "INFLOWS")["B"][6] == "0.006"


def test_constant_baseline_merges_into_named_series_without_adding_inactive_scale() -> None:
    """An empty-series SF must not inflate a different source's real hydrograph."""
    source = document('[INFLOWS]\nA FLOW TS FLOW 1.0 2 3\n')
    target = document('[INFLOWS]\nB FLOW "" FLOW 1 99 7\n')
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert rows(result, "INFLOWS")["B"] == ["B", "FLOW", "TS", "FLOW", "1", "2", "10"]


def test_documented_defaults_are_preserved_and_split_explicitly() -> None:
    """A three-token inflow has SF1/base0, both represented correctly on split."""
    source, target = document('[INFLOWS]\nA FLOW TS\n'), document()
    full = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert rows(full, "INFLOWS")["B"] == ["B", "FLOW", "TS"]
    split = plan_load_migration(source, target, mappings=[mapping(source, target, destinations=(("B", ".4"), ("C", ".6")))])
    assert rows(split, "INFLOWS")["B"] == ["B", "FLOW", "TS", "FLOW", "1", "0.4", "0"]


def test_exact_high_precision_coefficients_do_not_round_at_global_decimal_precision() -> None:
    """Finite-decimal products remain exact beyond the default28-digit context."""
    value = "123456789012345678901234567890.123456789"
    source, target = document('[DWF]\nA FLOW ' + value + '\n'), document()
    result = plan_load_migration(source, target, mappings=[mapping(source, target, destinations=(("B", ".123456789"), ("C", ".876543211")))])
    audit = result["conservation"][0]
    assert audit["before"]["average_flow"] == audit["allocated"]["average_flow"] == value
    assert audit["passed"] and rows(result, "DWF")["B"][2] == "15241578751714678875171467887.517146788750190521"


@pytest.mark.parametrize("weight", ["0", "-1", "1.01", "NaN", "Infinity", "bad", 1, True])
def test_invalid_or_implicit_weights_are_rejected(weight: Any) -> None:
    """Never normalize an ambiguous allocation or coerce a binary float weight."""
    source, target = document('[DWF]\nA FLOW .1\n'), document()
    reviewed = mapping(source, target); reviewed["destinations"][0]["weight"] = weight
    with pytest.raises(ValueError): plan_load_migration(source, target, mappings=[reviewed])


def test_split_weights_must_sum_exactly_one() -> None:
    """No residual quantity disappears behind a floating-point tolerance."""
    source, target = document('[DWF]\nA FLOW .1\n'), document()
    reviewed = mapping(source, target, destinations=(("B", ".5"), ("C", ".49999999999999999999")))
    with pytest.raises(ValueError, match="sum exactly1"):
        plan_load_migration(source, target, mappings=[reviewed])


@pytest.mark.parametrize("change", ["source_node", "source_load", "source_definition", "target_node", "target_load"])
def test_stale_source_target_and_definition_bindings_fail_closed(change: str) -> None:
    """Exact fingerprints protect each stage, including referenced pattern values."""
    source, target = document('[DWF]\nA FLOW .1 P\n'), document('[DWF]\nB FLOW .2 P\n')
    reviewed = mapping(source, target)
    if change == "source_node": source = SWMMDocument.from_bytes(source.data.replace(b"A 100 10", b"A 101 10"))
    elif change == "source_load": source = SWMMDocument.from_bytes(source.data.replace(b"A FLOW .1", b"A FLOW .3"))
    elif change == "source_definition": source = SWMMDocument.from_bytes(source.data.replace(b"P DAILY 1 1", b"P DAILY 2 1"))
    elif change == "target_node": target = SWMMDocument.from_bytes(target.data.replace(b"B 100 10", b"B 101 10"))
    else: target = SWMMDocument.from_bytes(target.data.replace(b"B FLOW .2", b"B FLOW .3"))
    with pytest.raises(ValueError, match="Stale"):
        plan_load_migration(source, target, mappings=[reviewed])


@pytest.mark.parametrize("family", ["DWF", "INFLOWS", "RDII"])
def test_incompatible_native_definitions_are_held_without_partial_execution(family: str) -> None:
    """Different temporal patterns, actual series or UH curves cannot be flattened."""
    originals = {"DWF": ('A FLOW .1 P', 'B FLOW .2 Q'),
                 "INFLOWS": ('A FLOW TS FLOW 1 1 .1', 'B FLOW OTHER FLOW 1 1 .2'),
                 "RDII": ('A UH 1', 'B UH2 2')}
    a, b = originals[family]
    source, target = document('[' + family + ']\n' + a + '\n'), document('[' + family + ']\n' + b + '\n')
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["status"] == "held" and result["executable_intent"] is result["sections"] is None
    assert any("unrepresentable_target_load" in reason for reason in result["held"])


@pytest.mark.parametrize("location", ["source", "target"])
def test_pollutant_concentrations_are_not_summed_as_water_flow(location: str) -> None:
    """Unsupported quality coupling blocks the complete quantity migration plan."""
    source = document('[DWF]\nA FLOW .1\n' + ('A TSS 25\n' if location == "source" else ''))
    target = document('[DWF]\nB TSS 30\n' if location == "target" else '')
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("Pollutant" in reason for reason in result["held"])


def test_missing_definition_copy_intents_are_explicitly_nonexecutable() -> None:
    """Missing target catalogs must be prepared and verified before replanning."""
    source = document('[DWF]\nA FLOW .1 P\n[INFLOWS]\nA FLOW TS FLOW 1 2 3\n[RDII]\nA UH 12\n')
    target = document(definitions=False)
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["status"] == "held" and result["executable_intent"] is None
    names = {(r["section"], r["object_id"]) for r in result["definition_copy_intents"]}
    assert names == {("PATTERNS", "P"), ("TIMESERIES", "TS"), ("HYDROGRAPHS", "UH"), ("RAINGAGES", "RG"), ("TIMESERIES", "RAIN")}
    assert all(r["executable"] is False and r["records"] for r in result["definition_copy_intents"])
    assert result["allocations"] and result["proposed_sections"]


@pytest.mark.parametrize("change", ["missing_source", "changed_target"])
def test_definition_collisions_or_undefined_source_references_are_not_ignored(change: str) -> None:
    """Names alone cannot hide changed pattern factors or an absent source curve."""
    source = document('[DWF]\nA FLOW .1 P\n', definitions=change != "missing_source")
    target = document()
    if change == "changed_target": target = SWMMDocument.from_bytes(target.data.replace(b"P DAILY 1 1", b"P DAILY 2 1"))
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any(("missing_source_definition" if change == "missing_source" else "changed_target_definition") in reason for reason in result["held"])


def test_identical_external_file_definitions_remain_with_content_validation_warning() -> None:
    """Pure planning preserves FILE references without claiming file accessibility."""
    source, target = document('[INFLOWS]\nA FLOW TS FLOW 1 2 3\n', external=True), document(external=True)
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert rows(result, "INFLOWS")["B"][2] == "TS"
    assert result["unverified_external_files"] == [{"section": "TIMESERIES", "object_id": "TS",
        "path_token": "unchanged missing forcing.dat", "status": "unverified_external_content",
        "note": "Only identical native definition/path tokens are checked. File contents, accessibility and runnable forcing are not validated."}]
    assert next(r for r in result["definitions"] if r["object_id"] == "TS")["records"] == [["TS", "FILE", "unchanged missing forcing.dat"]]


@pytest.mark.parametrize("family, target_row", [
    ("INFLOWS", 'B FLOW TS FLOW 1 2 3'),
    ("DWF", 'B FLOW .1 P'),
    ("RDII", 'B UH 12'),
])
def test_existing_destination_definitions_are_validated_even_when_source_has_none(family: str, target_row: str) -> None:
    """A moved constant load cannot bless a preexisting unresolved target reference."""
    source = document('[INFLOWS]\nA FLOW "" FLOW 1 1 .01\n')
    target = document('[' + family + ']\n' + target_row + '\n', definitions=False)
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("missing_target_destination_definition" in reason for reason in result["held"])


def test_existing_destination_external_series_has_unverified_content_warning() -> None:
    """Target-owned hydrographs retain their source path and file-access limitation."""
    source = document('[INFLOWS]\nA FLOW "" FLOW 1 1 .01\n')
    target = document('[INFLOWS]\nB FLOW TS FLOW 1 2 3\n', external=True)
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert rows(result, "INFLOWS")["B"][2] == "TS"
    assert result["unverified_external_files"][0]["status"] == "unverified_external_content"
    assert result["target_definitions"][0]["object_id"] == "TS"


def test_rendered_group_coefficients_are_independently_reconstructed() -> None:
    """Post-merge parsing verifies both active hydrograph terms, including targets."""
    source = document('[INFLOWS]\nA FLOW TS FLOW 1 2 3\n')
    target = document('[INFLOWS]\nB FLOW TS FLOW 1 4 5\n')
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["group_conservation"] == [{"section": "INFLOWS", "target_node_id": "B", "component_count": 2,
        "series_scale_active": True, "expected": {"series_scale": "6", "baseline_flow": "8"},
        "rendered": {"series_scale": "6", "baseline_flow": "8"}, "passed": True}]


@pytest.mark.parametrize("extra", [
    '[CONTROLS]\nRULE TEST\nIF NODE A DEPTH > 1\n',
    '[SUBCATCHMENTS]\nS RG A 1 0 10 1 0\n',
    '[GROUNDWATER]\nS AQ A 0 0 0 0 0 0 0 0\n',
    '[LID_USAGE]\nS LID 1 1 1 0 0 0 FILE A\n',
])
def test_load_mapping_does_not_authorize_other_retired_node_dependency_remaps(extra: str) -> None:
    """Control, runoff and groundwater semantics remain separate engineering holds."""
    source, target = document('[DWF]\nA FLOW .1\n' + extra), document()
    result = plan_load_migration(source, target, retired_node_ids=["A"], mappings=[mapping(source, target)])
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("unsupported_dependency" in reason for reason in result["held"])


@pytest.mark.parametrize("case", ["unmapped_source_load", "new_target_load", "changed_source_in_target", "retired_destination", "mapping_chain", "proposed", "different_units"])
def test_missing_obligations_and_unsupported_live_migrations_are_held(case: str) -> None:
    """No source/target load can silently disappear through physical preparation."""
    source, target = document('[DWF]\nA FLOW .1\n'), document()
    retired = ["A"]; reviewed = [mapping(source, target)]
    if case == "unmapped_source_load": reviewed = []
    elif case == "new_target_load":
        source, target = document(), document('[DWF]\nA FLOW .2\n'); reviewed = []
    elif case == "changed_source_in_target":
        target = document('[DWF]\nA FLOW .2\n'); reviewed = [mapping(source, target)]
    elif case == "retired_destination":
        retired.append("B")
    elif case == "mapping_chain":
        source = document('[DWF]\nA FLOW .1\nB FLOW .2\n')
        reviewed = [mapping(source, target), mapping(source, target, node="B", destinations=(("C", "1"),))]
    elif case == "proposed": reviewed[0]["decision"] = "proposed"
    else: target = document(units="LPS"); reviewed = [mapping(source, target)]
    result = plan_load_migration(source, target, retired_node_ids=retired, mappings=reviewed)
    assert result["status"] == "held" and result["executable_intent"] is result["sections"] is None


@pytest.mark.parametrize("side", ["source", "target"])
def test_duplicate_native_load_slots_are_not_double_counted(side: str) -> None:
    """EPA setters replace duplicate slots, so summing duplicate rows is unsafe."""
    source = document('[INFLOWS]\nA FLOW "" FLOW 1 1 .1\n' + ('A FLOW "" FLOW 1 1 .2\n' if side == "source" else ''))
    target = document('[INFLOWS]\nB FLOW "" FLOW 1 1 .3\nB FLOW "" FLOW 1 1 .4\n' if side == "target" else '')
    result = plan_load_migration(source, target, mappings=[mapping(source, target)])
    assert result["status"] == "held" and any("duplicate_or_quality" in reason for reason in result["held"])


@pytest.mark.parametrize("case", ["mapping_duplicate", "source_duplicate", "destination_duplicate", "unknown_schema", "invalid_date", "stale_hash", "invalid_context"])
def test_malformed_mapping_and_review_contracts_raise(case: str) -> None:
    """Only exact source/target-bound review contracts can yield load intents."""
    source, target = document('[DWF]\nA FLOW .1\n'), document()
    reviewed = [mapping(source, target)]
    options = {}
    if case == "mapping_duplicate": reviewed.append(deepcopy(reviewed[0]))
    elif case == "source_duplicate":
        second = deepcopy(reviewed[0]); second["id"] = "other"; reviewed.append(second)
    elif case == "destination_duplicate": reviewed[0]["destinations"] *= 2
    elif case == "unknown_schema": reviewed[0]["unknown"] = True
    elif case == "invalid_date": reviewed[0]["date"] = "21/10/2026"
    elif case == "stale_hash": reviewed[0]["source_load_fingerprint"] = "0" * 64
    else: options["context_sha256"] = "invalid"
    with pytest.raises(ValueError): plan_load_migration(source, target, mappings=reviewed, **options)


def test_empty_plan_preserves_target_load_records_exactly() -> None:
    """The helper never discovers allocations or retirement candidates itself."""
    target = document('[DWF]\nB FLOW .1000 Q\n[INFLOWS]\nC FLOW OTHER\n')
    result = plan_load_migration(target, target, mappings=[])
    assert result["status"] == "ready"
    for section in ("DWF", "INFLOWS", "RDII"):
        assert result["sections"][section] == [list(r.tokens) for r in target.records(section)]
    assert not result["allocations"] and not result["executable_intent"]["clear_source_node_ids"]
