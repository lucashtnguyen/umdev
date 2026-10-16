"""Verify opt-in qualified structure ports without inferring drop endpoints.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Notes: Synthetic scalar notation, references and topology only.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from umdev.configuration import ConfigurationError, validate_config
from umdev.evidence import (collect_structure_ports, parse_inline_port,
                            parse_qualified_inline_port, resolve_ports)
from umdev.records import network_from_dict


def parse(text: str, role: str = "in") -> dict[str, Any]:
    """Parse a synthetic measured scalar using explicit source units."""
    return parse_qualified_inline_port(text, node_id="NODE_A", key="source/nodes/A/note",
        role=role, elevation_unit="ft", diameter_unit="in", vertical_datum="TEST")


@pytest.mark.parametrize("suffix,role", [("OUT", "out"), ("in", "in"), ("DROP OUT", "out"), ("in DROP", "in")])
def test_qualified_scalar_preserves_measurements_and_raw_role_provenance(suffix: str, role: str) -> None:
    """Explicit text, not the attribute name, controls a recognized IN/OUT role."""
    text = f" 101.25′ (E) 8 {suffix}  "
    row = parse(text, role="inlet" if role == "out" else "outlet")
    assert row["invert"] == pytest.approx(101.25 * .3048)
    assert row["diameter"] == pytest.approx(8 * .0254)
    assert row["role"] == role
    assert row["raw"] == text
    assert not row["flags"]
    assert row["parsing"]["grammar_id"] == "qualified_elevation_direction_diameter_v1"
    assert row["parsing"]["role_basis"] == "explicit_suffix"
    for qualifier in row["parsing"]["qualifier_evidence"]:
        assert text[qualifier["start"]:qualifier["end"]] == qualifier["raw"]
    assert row["explicit_drop"] == ("DROP" in suffix.upper())


def test_drop_alone_retains_configured_role_and_numeric_observation() -> None:
    """DROP is qualification metadata, not an inferred IN/OUT label."""
    row = parse("0 (NE) 8 DROP", role="out")
    assert row["invert"] == 0
    assert row["role"] == "out"
    assert row["explicit_drop"]
    assert row["parsing"]["role_basis"] == "configured"
    assert not row["flags"]


@pytest.mark.parametrize("text", ['101.25 (E) 8 CAPPED OUT', '101.25 (E) 8 ABANDONED',
                                 '101.25 (E) 8 STUB', '101.25 (E) 8 REMARK DROP',
                                 '101.25 (E) 8"OUT', '101.25/100.2 (E) 8 DROP',
                                 '101.25 (E) 8; 100.2 (E) 8 DROP', '101.25 8 OUT'])
def test_unknown_remarks_or_multiple_scalars_are_not_made_usable(text: str) -> None:
    """Recognized trailing text cannot hide an otherwise unsupported record."""
    row = parse(text)
    assert row["raw"] == text
    assert row["invert"] is None
    assert "unrecognized_port_syntax" in row["flags"]


@pytest.mark.parametrize("text,flag", [('101.25 (E) 8 OUT IN', 'conflicting_port_role_qualifiers'),
                                      ('101.25 (E) 8 DROP DROP', 'duplicate_port_qualifier')])
def test_ambiguous_qualifiers_remain_flagged_with_the_measurement(text: str, flag: str) -> None:
    """Known numeric content is retained for review while role ambiguity is held."""
    row = parse(text)
    assert row["invert"] == pytest.approx(101.25 * .3048)
    assert flag in row["flags"]


def test_original_strict_parser_still_rejects_qualified_records() -> None:
    """Opt-in registration does not broaden existing project cleaning silently."""
    row = parse_inline_port('101.25 (E) 8 DROP', node_id='NODE_A', key='source/a/note', role='in', elevation_unit='ft', diameter_unit='in')
    assert row["invert"] is None
    assert row["flags"] == ["unrecognized_port_syntax"]


def network_case(second: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one outgoing pipe with independently collected structure observations."""
    canonical = {"nodes": [
        {"key":"source/nodes/A", "asset_id":"NODE_A", "dataset":"structures", "coordinates":[0,0],
         "vertical_datum":"TEST", "note1":"101.25 (E) 8 OUT", "note2":second},
        {"key":"source/nodes/B", "asset_id":"NODE_B", "dataset":"structures", "coordinates":[30,0], "vertical_datum":"TEST"}],
        "pipes":[{"key":"source/pipes/P", "asset_id":"PIPE_P", "from_node":"NODE_A", "to_node":"NODE_B", "diameter":8*.0254, "coordinates":[[0,0],[30,0]]}], "ports":[]}
    config = {"elevations":{"target_datum":"TEST", "transforms":[{"id":"identity", "source_datum":"TEST", "target_datum":"TEST", "method":"identity", "verified":True, "basis":"Synthetic test datum"}]},
              "ports":{"observations":[{"id":"note1", "dataset":"structures", "field":"note1", "role":"in", "parser":"qualified_elevation_direction_diameter_v1", "units":{"elevation":"ft", "diameter":"in"}},
                                         {"id":"note2", "dataset":"structures", "field":"note2", "role":"in", "parser":"qualified_elevation_direction_diameter_v1", "units":{"elevation":"ft", "diameter":"in"}}],
                       "assignment":{"precedence":["reviewed_override","normalized_port","inline_port"], "direction":"hint", "unique_role_diameter_fallback":{"enabled":True},
                                     "endpoint_tolerance":{"value":.001,"unit":"m"}, "diameter_tolerance":{"value":.001,"unit":"m"}, "elevation_conflict_tolerance":{"value":.001,"unit":"m"}}}}
    return canonical, config


def test_explicit_out_in_a_generic_inlet_field_attributes_the_outgoing_end() -> None:
    """Field-level default role cannot suppress a source's explicit OUT suffix."""
    canonical, config = network_case()
    original = deepcopy(canonical)
    assignments, findings = resolve_ports(network_from_dict(canonical), config)
    assigned = assignments[("source/pipes/P", "upstream")]
    assert assigned.value == pytest.approx(101.25 * .3048)
    assert assigned.evidence[0]["raw"]["role"] == "out"
    assert not findings
    assert canonical == original


def test_two_drop_points_cannot_be_resolved_by_minimum_or_compass_rank() -> None:
    """A tangent match must not choose top/bottom of a drop by accident."""
    canonical, config = network_case("99.25 (N) 8 DROP OUT")
    canonical["nodes"][0]["note1"] = "101.25 (E) 8 DROP OUT"
    assignments, findings = resolve_ports(network_from_dict(canonical), config)
    assigned = assignments[("source/pipes/P", "upstream")]
    assert assigned.value is None
    assert assigned.method == "ambiguous_drop_port"
    assert sorted(item["value"] for item in assigned.alternatives) == pytest.approx([99.25*.3048,101.25*.3048])
    assert any(row["rule"] == "ambiguous_drop_port" for row in findings)


def test_explicit_drop_mapping_preserves_selected_point_and_review_basis() -> None:
    """An exact reviewed observation binding resolves the point without magnitude inference."""
    canonical, config = network_case("99.25 (N) 8 DROP OUT")
    canonical["nodes"][0]["note1"] = "101.25 (E) 8 DROP OUT"
    config["ports"]["assignment"]["observation_pipe_references"] = {"source/nodes/A/note1":{"pipe_key":"source/pipes/P", "basis":"Reviewed source diagram identifies the upper observation as this outgoing end."}}
    assigned, findings = resolve_ports(network_from_dict(canonical), config)
    result = assigned[("source/pipes/P", "upstream")]
    assert result.value == pytest.approx(101.25 * .3048)
    assert result.method == "explicit_pipe_reference"
    assert result.evidence[0]["raw"]["configured_pipe_reference"]["basis"].startswith("Reviewed")
    assert not any(row["rule"] == "ambiguous_drop_port" for row in findings)


def test_two_contradictory_explicit_drop_references_still_conflict() -> None:
    """Two explicit observations are not ordered or reduced to a minimum."""
    canonical, config = network_case("99.25 (E) 8 DROP OUT")
    canonical["nodes"][0]["note1"] = "101.25 (E) 8 DROP OUT"
    config["ports"]["assignment"]["observation_pipe_references"] = {
        f"source/nodes/A/{note}":{"pipe_key":"source/pipes/P", "basis":"Conflicting fictional records retained for review"} for note in ("note1","note2")}
    assignments, _ = resolve_ports(network_from_dict(canonical), config)
    assert assignments[("source/pipes/P", "upstream")].value is None


def test_drop_metadata_does_not_hold_a_single_attributed_observation() -> None:
    """A uniquely identified observation is numeric evidence with a visible qualifier."""
    canonical, config = network_case()
    canonical["nodes"][0]["note1"] = "101.25 (E) 8 DROP OUT"
    assignments, _ = resolve_ports(network_from_dict(canonical), config)
    result = assignments[("source/pipes/P", "upstream")]
    assert result.value == pytest.approx(101.25 * .3048)
    assert result.evidence[0]["raw"]["explicit_drop"]


def test_stale_observation_mapping_is_rejected_and_unknown_pipe_stays_held() -> None:
    """An explicit review binding cannot silently disappear or target another pipe."""
    canonical, config = network_case()
    config["ports"]["assignment"]["observation_pipe_references"] = {"source/nodes/A/missing":{"pipe_key":"source/pipes/P", "basis":"Reviewed mapping"}}
    with pytest.raises(ValueError, match="missing observations"):
        collect_structure_ports(network_from_dict(canonical), config)
    config["ports"]["assignment"]["observation_pipe_references"] = {"source/nodes/A/note1":{"pipe_key":"source/pipes/UNKNOWN", "basis":"Reviewed mapping"}}
    assignments, _ = resolve_ports(network_from_dict(canonical), config)
    assert assignments[("source/pipes/P", "upstream")].value is None


def test_contradictory_existing_pipe_id_is_not_hidden_by_a_matching_pipe_key() -> None:
    """A new reference never overrides a contradictory original direct binding."""
    canonical, config = network_case()
    canonical["nodes"][0]["note1"] = None
    canonical["ports"] = [{"key":"source/ports/A", "structure_id":"NODE_A", "role":"out", "invert":30.0,
                           "vertical_datum":"TEST", "pipe_key":"source/pipes/P", "pipe_id":"WRONG_PIPE"}]
    config["ports"]["assignment"]["observation_pipe_references"] = {"source/ports/A":{"pipe_key":"source/pipes/P", "basis":"Reviewed intended mapping"}}
    ports = collect_structure_ports(network_from_dict(canonical), config)
    assert "port_pipe_reference_conflict" in ports[0]["flags"]
    assignments, _ = resolve_ports(network_from_dict(canonical), config)
    assert assignments[("source/pipes/P", "upstream")].value is None


def config_case() -> dict[str, Any]:
    """Build the minimum configuration needed to validate a registered inline field."""
    return {"schema_version":1, "kind":"umdev_project",
            "sources":{"source":{"format":"json_packet", "path":"fictional.json"}},
            "datasets":{"structures":{"source":"source", "entity":"node", "id_field":"ID", "fields":{"asset_id":{"source":"ID", "type":"identifier"}, "qualified_note":{"source":"NOTE", "type":"string"}}}},
            "ports":{"observations":[{"id":"note", "dataset":"structures", "parser":"qualified_elevation_direction_diameter_v1", "field":"qualified_note", "role":"in", "units":{"elevation":"ft", "diameter":"in"}}], "assignment":{"observation_pipe_references":{"source/nodes/A/note":{"pipe_key":"source/pipes/P", "basis":"Reviewed source diagram"}}}}}


def test_configuration_registers_qualified_parser_and_additional_text_fields() -> None:
    """Opt-in parser IDs and explicit observation fields work through normal validation."""
    validate_config(config_case())


@pytest.mark.parametrize("reference", [{"pipe_key":"P"}, {"pipe_key":"P", "basis":""}, "P", {"pipe_key":"P", "basis":"Reviewed", "unknown":True}])
def test_configuration_rejects_unjustified_or_malformed_pipe_bindings(reference: Any) -> None:
    """Explicit disambiguation requires a stable key and reviewer-supplied basis."""
    config = config_case()
    config["ports"]["assignment"]["observation_pipe_references"]["source/nodes/A/note"] = reference
    with pytest.raises(ConfigurationError):
        validate_config(config)
