"""Synthetic end-to-end pressure handoff and written-model verification.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained; no project identities, measurements or policies are used.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from hymo import SWMMDocument, interpret_fields
from shmm import preview_changes

from umdev.configuration import load_config
from umdev.models import baseline_node_parameters, compile_handoff, instructions_from_handoff, verify_prepared_profiles
from umdev.parameters import prepare_network_from_dict


FIXTURES = Path(__file__).parent / "fixtures/schema_equivalence"


def make_case(unit: str = "ft", equation: str = "H-W", extra: str = "", roles: dict[str, str] | None = None, pipe_kind: str = "force_main") -> tuple[SWMMDocument, dict[str, Any], dict[str, Any]]:
    """Prepare a fictional two-conduit pressure main with an open receiver."""
    flow = "CFS" if unit == "ft" else "LPS"
    config = load_config(FIXTURES / "utility_a.config.json").to_dict()
    config["model"]["target"].update(flow_units=flow, length_unit=unit)
    config["model"]["baseline_metadata"] = {
        "horizontal_crs": config["model"]["target"]["map_crs"],
        "map_unit": config["model"]["target"]["map_unit"], "vertical_datum": "SYNTHETIC_DATUM"}
    config["ports"]["observations"] = []
    config["ports"]["assignment"]["precedence"] = ["pipe_numeric"]
    config["roughness"]["pressure_parameters"] = {
        "policy": "explicit_assumption", "equation": equation,
        "coefficient": 120 if equation == "H-W" else {"value": 0.25, "unit": "mm"},
        "conduit_manning_n": 0.013, "basis": "Synthetic pressure test assumption"}
    config["parameters"]["nodes"]["pressure_connections"] = {
        "classification": "internal_force_main_only", "surcharge_depth": {"value": 999, "unit": "ft"},
        "basis": "Synthetic high surcharge allowance; no physical pressure rating",
        "node_overrides": roles if roles is not None else {"001": "sealed", "003": "open_discharge"}}
    canonical = {"nodes": [
        {"key": f"s/n/{identity}", "asset_id": identity, "dataset": "structures",
         "floor": floor, "rim": floor + 5, "coordinates": [index * 10, 0], "vertical_datum": "SYNTHETIC_DATUM"}
        for index, (identity, floor) in enumerate((("001", 30), ("002", 29), ("003", 28)))],
        "pipes": [
        {"key": f"s/p/{identity}", "asset_id": identity, "from_node": start, "to_node": end,
         "diameter": 0.3, "length": 10, "upstream_invert": high + 0.2,
         "downstream_invert": high - 0.8, "coordinates": [[index * 10, 0], [(index + 1) * 10, 0]],
         "material": "PVC", "pipe_kind": pipe_kind, "status": "active", "vertical_datum": "SYNTHETIC_DATUM"}
        for index, (identity, start, end, high) in enumerate((("A", "001", "002", 30), ("B", "002", "003", 29)))], "ports": []}
    text = f"[OPTIONS]\nFLOW_UNITS {flow}\nFLOW_ROUTING DYNWAVE\nFORCE_MAIN_EQUATION {equation}\nLINK_OFFSETS DEPTH\n" + extra
    baseline = SWMMDocument.from_bytes(text.encode())
    prepared = prepare_network_from_dict(canonical, config, baseline_nodes=baseline_node_parameters(baseline, config)).to_dict()
    assert not prepared["held"]
    return baseline, config, prepared


def render(baseline: SWMMDocument, config: dict[str, Any], prepared: dict[str, Any]) -> tuple[SWMMDocument, dict[str, Any]]:
    """Compile typed instructions and preview the actual SWMM bytes through SHMM."""
    handoff = compile_handoff(baseline, prepared, config, config_sha256="synthetic_test")
    changes, vertices = instructions_from_handoff(handoff)
    candidate = SWMMDocument.from_bytes(preview_changes(baseline, changes, vertex_changes=vertices).data)
    return candidate, handoff


def mutate(document: SWMMDocument, section: str, identity: str, column: int, value: str) -> SWMMDocument:
    """Change one written token independently of the compiler's instructions."""
    row = next(row for row in document.records(section) if row.tokens[0] == identity)
    lines = document.data.decode().splitlines(keepends=True)
    tokens = list(row.tokens)
    while len(tokens) <= column:
        tokens.append("0")
    tokens[column] = value
    lines[row.line_index] = " ".join(tokens) + "\n"
    return SWMMDocument.from_bytes("".join(lines).encode())


def test_hazen_williams_and_manning_are_distinct_written_fields() -> None:
    baseline, config, prepared = make_case()
    candidate, handoff = render(baseline, config, prepared)
    assert handoff["applied_source_keys"] == ["s/p/A", "s/p/B"]
    assert not handoff["application_holds"]
    assert all(float(row.tokens[4]) == 0.013 for row in candidate.records("CONDUITS"))
    assert all(row.tokens[1] == "FORCE_MAIN" and float(row.tokens[3]) == 120 for row in candidate.records("XSECTIONS"))
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_pipes"] == 2


@pytest.mark.parametrize("unit,expected", [("ft", 999), ("m", 999 * 0.3048)])
def test_sealed_surcharge_units_and_open_receiving_zero(unit: str, expected: float) -> None:
    baseline, config, prepared = make_case(unit)
    candidate, handoff = render(baseline, config, prepared)
    rows = {row.tokens[0]: interpret_fields("JUNCTIONS", row) for row in candidate.records("JUNCTIONS")}
    assert float(rows["001"]["surcharge_depth"]) == pytest.approx(expected)
    assert float(rows["002"]["surcharge_depth"]) == pytest.approx(expected)
    assert float(rows["003"]["surcharge_depth"]) == 0
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_surcharge_instructions"] == 2


@pytest.mark.parametrize("unit,expected", [("ft", 0.25 / 25.4), ("m", 0.25)])
def test_darcy_roughness_uses_inches_or_millimetres_independent_of_pipe_length_units(unit: str, expected: float) -> None:
    """EPA SWMM 5.2 Table D-2 uses inches/mm for FORCE_MAIN Geom2."""
    baseline, config, prepared = make_case(unit, "D-W")
    assert prepared["pipes"][0]["pressure_assignment"]["value"] == pytest.approx(0.00025)
    candidate, handoff = render(baseline, config, prepared)
    assert all(float(row.tokens[3]) == pytest.approx(expected) for row in candidate.records("XSECTIONS"))
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_pipes"] == 2


def test_existing_storage_wet_well_card_and_curve_are_preserved() -> None:
    extra = "[STORAGE]\n001 30 5 0 TABULAR AREA 0.1 0.2\n[CURVES]\nAREA STORAGE 0 10\nAREA 5 15\n[COORDINATES]\n001 0 0\n"
    baseline, config, prepared = make_case("m", extra=extra, roles={"003": "open_discharge"})
    assert prepared["nodes"][0]["pressure_connection"]["role"] == "wet_well"
    assert prepared["nodes"][0]["surcharge_depth_m"] is None
    candidate, handoff = render(baseline, config, prepared)
    for section in ("STORAGE", "CURVES"):
        assert [row.tokens for row in candidate.records(section)] == [row.tokens for row in baseline.records(section)]
    assert not any(change["section"] == "STORAGE" for change in handoff["changes"])
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_pipes"] == 2


@pytest.mark.parametrize("setting,replacement,reason", [
    ("FLOW_ROUTING DYNWAVE", "FLOW_ROUTING KINWAVE", "force_main_requires_dynamic_wave"),
    ("FORCE_MAIN_EQUATION H-W", "FORCE_MAIN_EQUATION D-W", "force_main_equation_mismatch")])
def test_incompatible_model_options_hold_source_pressure_additions(setting: str, replacement: str, reason: str) -> None:
    baseline, config, prepared = make_case()
    baseline = SWMMDocument.from_bytes(baseline.data.replace(setting.encode(), replacement.encode()))
    candidate, handoff = render(baseline, config, prepared)
    assert not handoff["applied_source_keys"]
    assert all(row["reason"] == reason for row in handoff["application_holds"])
    assert not list(candidate.records("CONDUITS"))
    assert not any(change["section"] == "JUNCTIONS" for change in handoff["changes"])


def test_exact_existing_pump_abstraction_holds_duplicate_pressure_pipe() -> None:
    baseline, config, prepared = make_case("m", extra="[JUNCTIONS]\n001 30 5 0 0\n002 29 5 0 0\n[COORDINATES]\n001 0 0\n002 10 0\n[PUMPS]\nExistingPump 001 002 * ON 0 0\n")
    candidate, handoff = render(baseline, config, prepared)
    assert {row["asset_id"]: row["reason"] for row in handoff["application_holds"]} == {"A": "force_main_matches_existing_pump_abstraction"}
    assert [row.tokens[0] for row in candidate.records("CONDUITS")] == ["B"]
    assert [row.tokens for row in candidate.records("PUMPS")] == [row.tokens for row in baseline.records("PUMPS")]
    accounting = next(row for row in handoff["force_main_accounting"] if row["asset_id"] == "A")
    assert accounting["represented_pumps"] == ["ExistingPump"]
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_pipes"] == 1


def test_baseline_internal_surcharge_updates_but_open_receiving_setting_is_preserved() -> None:
    extra = """[JUNCTIONS]
P1 100 10 0 997 0
P2 99 10 0 997 0
Receiver 98 10 0 997 0
[STORAGE]
Wet 100 10 0 FUNCTIONAL 10 0 1
[OUTFALLS]
OUT 97 FREE NO
[PUMPS]
Pump Wet P1 * ON 0 0
[CONDUITS]
FM1 P1 P2 100 0.013 0 0
FM2 P2 Receiver 100 0.013 0 0
G Receiver OUT 100 0.013 0 0
[XSECTIONS]
FM1 FORCE_MAIN 1 120 0 0
FM2 FORCE_MAIN 1 120 0 0
G CIRCULAR 1 0 0 0
"""
    baseline, config, _ = make_case(extra=extra)
    prepared = {"nodes": [], "pipes": [], "findings": []}
    candidate, handoff = render(baseline, config, prepared)
    rows = {row.tokens[0]: interpret_fields("JUNCTIONS", row) for row in candidate.records("JUNCTIONS")}
    assert float(rows["P1"]["surcharge_depth"]) == 999
    assert float(rows["P2"]["surcharge_depth"]) == 999
    assert float(rows["Receiver"]["surcharge_depth"]) == 997
    roles = {row["model_id"]: row["role"] for row in handoff["pressure_nodes"]}
    assert roles["P1"] == roles["P2"] == "sealed"
    assert roles["Receiver"] == "open_discharge"
    assert [row.tokens for row in candidate.records("STORAGE")] == [row.tokens for row in baseline.records("STORAGE")]
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_surcharge_instructions"] == 2
    altered_receiver = mutate(candidate, "JUNCTIONS", "Receiver", 4, "0")
    with pytest.raises(ValueError, match="surcharge depth"):
        verify_prepared_profiles(altered_receiver, prepared, handoff, config)


@pytest.mark.parametrize("section,identity,column,value,message", [
    ("JUNCTIONS", "002", 4, "0", "surcharge depth"),
    ("JUNCTIONS", "003", 4, "999", "surcharge depth"),
    ("XSECTIONS", "A", 1, "CIRCULAR", "pipe shape"),
    ("XSECTIONS", "A", 3, "121", "force-main coefficient"),
    ("CONDUITS", "A", 4, "0.014", "conduit Manning")])
def test_written_pressure_mutations_are_detected(section: str, identity: str, column: int, value: str, message: str) -> None:
    baseline, config, prepared = make_case()
    candidate, handoff = render(baseline, config, prepared)
    altered = mutate(candidate, section, identity, column, value)
    with pytest.raises(ValueError, match=message):
        verify_prepared_profiles(altered, prepared, handoff, config)


def test_existing_force_main_coefficient_updates_use_same_darcy_unit_contract() -> None:
    extra = """[JUNCTIONS]
001 30 5 0 0
002 29 5 0 0
[COORDINATES]
001 0 0
002 10 0
[CONDUITS]
A 001 002 10 0.013 0.2 0.2
[XSECTIONS]
A FORCE_MAIN 0.3 0.5 0 0
"""
    baseline, config, prepared = make_case("m", "D-W", extra=extra)
    candidate, handoff = render(baseline, config, prepared)
    updated = next(row for row in candidate.records("XSECTIONS") if row.tokens[0] == "A")
    assert float(updated.tokens[3]) == pytest.approx(0.25)
    assert verify_prepared_profiles(candidate, prepared, handoff, config)["checked_pipes"] == 2


@pytest.mark.parametrize("classification,role,expected", [
    ("internal_force_main_only", "open_discharge", 997),
    ("explicit_roles", "sealed", 999)])
def test_baseline_model_node_roles_override_automatic_classification(classification: str, role: str, expected: float) -> None:
    extra = """[JUNCTIONS]
Start 100 10 0 997 0
Middle 99 10 0 997 0
End 98 10 0 997 0
[CONDUITS]
FM1 Start Middle 100 0.013 0 0
FM2 Middle End 100 0.013 0 0
[XSECTIONS]
FM1 FORCE_MAIN 1 120 0 0
FM2 FORCE_MAIN 1 120 0 0
"""
    baseline, config, _ = make_case(extra=extra)
    config["parameters"]["nodes"]["pressure_connections"].update(
        classification=classification, model_node_overrides={"Middle": role})
    prepared = {"nodes": [], "pipes": [], "findings": []}
    candidate, handoff = render(baseline, config, prepared)
    row = next(row for row in candidate.records("JUNCTIONS") if row.tokens[0] == "Middle")
    assert float(interpret_fields("JUNCTIONS", row)["surcharge_depth"]) == expected
    assert next(row for row in handoff["pressure_nodes"] if row["model_id"] == "Middle")["role"] == role
    verify_prepared_profiles(candidate, prepared, handoff, config)


def test_contradictory_source_and_model_pressure_roles_fail() -> None:
    baseline, config, prepared = make_case("m", extra="[JUNCTIONS]\n001 30 5 0 0\n[COORDINATES]\n001 0 0\n")
    config["parameters"]["nodes"]["pressure_connections"]["model_node_overrides"] = {"001": "open_discharge"}
    with pytest.raises(ValueError, match="[Cc]onflict|[Cc]ontradict"):
        compile_handoff(baseline, prepared, config, config_sha256="synthetic_test")


def test_baseline_auto_sealing_accounts_for_new_accepted_gravity_connections() -> None:
    extra = """[JUNCTIONS]
001 30 5 0 997
002 29 5 0 997
Receiver 28 5 0 0
[CONDUITS]
FM1 001 002 10 0.013 0 0
FM2 002 Receiver 10 0.013 0 0
[XSECTIONS]
FM1 FORCE_MAIN 0.3 120 0 0
FM2 FORCE_MAIN 0.3 120 0 0
[COORDINATES]
001 0 0
002 10 0
Receiver 20 0
"""
    baseline, config, prepared = make_case("m", extra=extra, pipe_kind="gravity")
    candidate, handoff = render(baseline, config, prepared)
    assert handoff["applied_source_keys"] == ["s/p/A", "s/p/B"]
    junction = next(row for row in candidate.records("JUNCTIONS") if row.tokens[0] == "002")
    assert float(interpret_fields("JUNCTIONS", junction)["surcharge_depth"]) == 997
    pressure = next(row for row in handoff["pressure_nodes"] if row["model_id"] == "002")
    assert pressure["role"] == "unresolved"
    assert pressure["changed"] is False
    verify_prepared_profiles(candidate, prepared, handoff, config)


def test_source_auto_sealing_accounts_for_existing_gravity_connections() -> None:
    extra = """[JUNCTIONS]
002 29 5 0 0
Receiver 28 5 0 0
[CONDUITS]
G 002 Receiver 10 0.013 0 0
[XSECTIONS]
G CIRCULAR 0.3 0 0 0
[COORDINATES]
002 10 0
Receiver 20 0
"""
    baseline, config, prepared = make_case("m", extra=extra)
    assert next(node for node in prepared["nodes"] if node["asset_id"] == "002")["pressure_connection"]["method"] == "internal_force_main_only"
    candidate, handoff = render(baseline, config, prepared)
    assert not handoff["applied_source_keys"]
    assert {row["asset_id"] for row in handoff["application_holds"]} == {"A", "B"}
    junction = next(row for row in candidate.records("JUNCTIONS") if row.tokens[0] == "002")
    assert float(interpret_fields("JUNCTIONS", junction)["surcharge_depth"]) == 0
    assert [row.tokens[0] for row in candidate.records("CONDUITS")] == ["G"]

    # An explicit engineering role can authorize the same mixed connection;
    # an automatically inferred sealed role cannot do so.
    baseline, config, prepared = make_case("m", extra=extra,
        roles={"001": "sealed", "002": "sealed", "003": "open_discharge"})
    candidate, handoff = render(baseline, config, prepared)
    assert handoff["applied_source_keys"] == ["s/p/A", "s/p/B"]
    junction = next(row for row in candidate.records("JUNCTIONS") if row.tokens[0] == "002")
    assert float(interpret_fields("JUNCTIONS", junction)["surcharge_depth"]) == pytest.approx(999 * 0.3048)
    assert [row.tokens for row in candidate.records("CONDUITS") if row.tokens[0] == "G"] == [row.tokens for row in baseline.records("CONDUITS")]
    verify_prepared_profiles(candidate, prepared, handoff, config)
