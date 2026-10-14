"""Verify conservative, auditable cleaning of utility inline-port notation.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained synthetic regressions; no municipal data or project policy.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from hymo import SWMMDocument
from shmm import preview_changes

from umdev.evidence import parse_inline_port
from umdev.models import compile_handoff, instructions_from_handoff, verify_prepared_profiles
from umdev.parameters import prepare_network_from_dict


FT_TO_M = 0.3048
IN_TO_M = 0.0254


def parse(text: str, *, elevation_unit: str = "ft", diameter_unit: str = "in") -> dict[str, Any]:
    """Parse one fictional source observation with explicitly configured units."""
    return parse_inline_port(text, node_id="NODE_A", key="source/nodes/A/outlet",
                             role="out", elevation_unit=elevation_unit,
                             diameter_unit=diameter_unit, vertical_datum="SYNTHETIC_BASIS")


@pytest.mark.parametrize("symbol", ["'", "′"])
def test_elevation_feet_mark_and_compass_separated_bare_size_use_declared_units(symbol: str) -> None:
    """One physical observation has equivalent ASCII and typographic notation."""
    text = f"101.25{symbol} (SW) 8"
    row = parse(text)
    assert row["invert"] == pytest.approx(101.25 * FT_TO_M)
    assert row["diameter"] == pytest.approx(8 * IN_TO_M)
    assert row["direction"] == "SW"
    assert not row["flags"]
    provenance = row["parsing"]
    assert provenance["grammar_id"] == "elevation_direction_diameter_v1"
    assert provenance["elevation_unit"] == "ft"
    assert provenance["diameter_unit"] == "in"
    assert provenance["elevation_unit_basis"] == "explicit"
    assert provenance["diameter_unit_basis"] == "configured_omitted_suffix"
    assert "elevation_feet_symbol" in provenance["normalizations"]
    assert "diameter_unit_from_configuration" in provenance["normalizations"]
    assert row["raw"] == text


def test_omitted_size_suffix_uses_configured_mm_without_guessing_inches() -> None:
    """Bare sizes are interpreted by configuration, not by a global unit default."""
    row = parse("31.0 (E) 250", elevation_unit="m", diameter_unit="mm")
    assert row["invert"] == 31.0
    assert row["diameter"] == 0.25
    assert row["parsing"]["diameter_unit"] == "mm"
    assert row["parsing"]["elevation_unit_basis"] == "configured"
    assert row["parsing"]["diameter_unit_basis"] == "configured_omitted_suffix"


@pytest.mark.parametrize("text", [
    '101.25 (SW) 8"', '101.25 ft (SW) 8 in', '101.25 feet (SW) 8 inches',
    '101.25 (SW), 8"', '101.25 (SW) 8″', '101.25 8"'])
def test_existing_explicit_diameter_syntax_remains_supported(text: str) -> None:
    """The additional cleaning must preserve previously supported observations."""
    row = parse(text)
    assert row["invert"] == pytest.approx(101.25 * FT_TO_M)
    assert row["diameter"] == pytest.approx(8 * IN_TO_M)
    assert row["parsing"]["diameter_unit_basis"] == "explicit"
    assert not row["flags"]


def test_single_elevation_does_not_fabricate_direction_or_diameter() -> None:
    """A valid single scalar remains an elevation-only source observation."""
    row = parse("101.25")
    assert row["invert"] == pytest.approx(101.25 * FT_TO_M)
    assert row["diameter"] is None
    assert row["direction"] is None
    assert row["parsing"]["diameter_unit_basis"] == "not_present"
    assert row["parsing"]["elevation_unit_basis"] == "configured"


@pytest.mark.parametrize("text", ["101.25 8", "101.25' 8", "101.25, 8", "101.25 SW 8"])
def test_bare_numeric_pair_without_parenthesized_compass_remains_ambiguous(text: str) -> None:
    """Two unsuffixed numbers cannot be split into engineering quantities by guess."""
    row = parse(text)
    assert row["flags"]
    assert row["invert"] is None
    assert row["raw"] == text


@pytest.mark.parametrize("symbol", ["'", "′"])
def test_explicit_feet_elevation_cannot_be_reinterpreted_as_configured_metres(symbol: str) -> None:
    """Accepting a feet mark never authorizes an implicit incompatible unit relabel."""
    row = parse(f"101.25{symbol} (SW) 8", elevation_unit="m")
    assert row["flags"] == ["port_unit_conflict"]
    assert row["invert"] is None


@pytest.mark.parametrize("text", ["101.25 (SW) 12'", "101.25 (SW) 12′", "101.25' (SW) 12'"])
def test_feet_mark_on_diameter_is_not_cleaned_into_inches(text: str) -> None:
    """A diameter unit typo requires a separate explicit policy or reviewed decision."""
    row = parse(text)
    assert row["flags"]
    assert row["invert"] is None
    assert row["diameter"] is None


@pytest.mark.parametrize("text", [
    '101.25\' (SW) 8 CAPPED', '101.25\' (SW) 8 ABANDONED',
    '101.25\' (SW) 8 DROP', '101.25\' (SW) 8 STUB',
    '101.25/102.5 (SW) 8', '101.25\' (SW) 8; 102.5 (E) 8'])
def test_qualified_or_multiple_observations_are_not_silently_cleaned(text: str) -> None:
    """New punctuation support cannot turn a qualified/drop port into accepted evidence."""
    row = parse(text)
    assert row["flags"]
    assert row["invert"] is None
    assert row["raw"] == text


@pytest.mark.parametrize("elevation", [0.0, -10.25])
def test_measured_zero_and_negative_elevations_are_retained(elevation: float) -> None:
    """Zero is a measurement here; source-specific missing sentinels belong in mappings."""
    row = parse(f"{elevation}' (NW) 8")
    assert row["invert"] == pytest.approx(elevation * FT_TO_M)
    assert not row["flags"]


@pytest.mark.parametrize("diameter", [0.0, -8.0])
def test_nonpositive_diameter_retains_measurement_but_is_flagged(diameter: float) -> None:
    """A malformed size is never clamped or replaced by an assumed positive size."""
    row = parse(f"101.25' (NW) {diameter}")
    assert row["diameter"] == pytest.approx(diameter * IN_TO_M)
    assert "invalid_port_diameter" in row["flags"]


def test_raw_text_including_outer_whitespace_is_preserved() -> None:
    """Trimming for recognition must not erase the original source evidence."""
    text = " \t101.25′ (sw) 8  "
    row = parse(text)
    assert row["raw"] == text
    assert row["direction"] == "SW"
    assert row["invert"] == pytest.approx(101.25 * FT_TO_M)
    assert not row["flags"]


def preparation_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """Construct two complete fictional pipe profiles with mixed inline notation."""
    canonical = {"nodes": [
        {"key": "source/nodes/A", "dataset": "utility_nodes", "asset_id": "NODE_A",
         "coordinates": [0, 0], "floor": None, "rim": 110 * FT_TO_M,
         "vertical_datum": "SYNTHETIC_BASIS", "outlet_description": "100.5' (E) 12"},
        {"key": "source/nodes/B", "dataset": "utility_nodes", "asset_id": "NODE_B",
         "coordinates": [30.48, 0], "floor": None, "rim": 109 * FT_TO_M,
         "vertical_datum": "SYNTHETIC_BASIS", "outlet_description": '99.7 (E) 12"',
         "inlet_description": "99.8′ (W) 12"},
        {"key": "source/nodes/C", "dataset": "utility_nodes", "asset_id": "NODE_C",
         "coordinates": [60.96, 0], "floor": 98 * FT_TO_M, "rim": 108 * FT_TO_M,
         "vertical_datum": "SYNTHETIC_BASIS", "inlet_description": '98.2 (W) 12"'}],
        "pipes": [
            {"key": "source/pipes/A", "asset_id": "PIPE_A", "from_node": "NODE_A", "to_node": "NODE_B",
             "length": 30.48, "diameter": 0.3048, "coordinates": [[0, 0], [30.48, 0]],
             "material": "SYNTHETIC_MATERIAL", "pipe_kind": "gravity"},
            {"key": "source/pipes/B", "asset_id": "PIPE_B", "from_node": "NODE_B", "to_node": "NODE_C",
             "length": 30.48, "diameter": 0.3048, "coordinates": [[30.48, 0], [60.96, 0]],
             "material": "SYNTHETIC_MATERIAL", "pipe_kind": "gravity"}], "ports": []}
    config = {"model": {"target": {"flow_units": "CFS", "length_unit": "ft", "link_offsets": "DEPTH",
                                   "map_crs": {"epsg": 3857}, "map_unit": "m", "vertical_datum": "SYNTHETIC_BASIS"},
                        "baseline_metadata": {"horizontal_crs": {"epsg": 3857}, "map_unit": "m", "vertical_datum": "SYNTHETIC_BASIS"}},
              "selection": {"scenario": "existing"},
              "elevations": {"target_datum": "SYNTHETIC_BASIS", "allow_provisional": True,
                             "transforms": [{"id": "synthetic_identity", "source_datum": "SYNTHETIC_BASIS",
                                             "target_datum": "SYNTHETIC_BASIS", "method": "identity", "verified": True,
                                             "basis": "Synthetic unit test basis."}]},
              "ports": {"observations": [{"id": "node_outlet", "dataset": "utility_nodes", "field": "outlet_description",
                                          "parser": "elevation_direction_diameter_v1", "role": "out",
                                          "units": {"elevation": "ft", "diameter": "in"}},
                                         {"id": "node_inlet", "dataset": "utility_nodes", "field": "inlet_description",
                                          "parser": "elevation_direction_diameter_v1", "role": "in",
                                          "units": {"elevation": "ft", "diameter": "in"}}],
                        "assignment": {"precedence": ["pipe_numeric", "normalized_port", "inline_port"],
                                       "direction": "hint", "endpoint_tolerance": {"value": 0.001, "unit": "m"},
                                       "diameter_tolerance": {"value": 0.001, "unit": "m"},
                                       "elevation_conflict_tolerance": {"value": 0.001, "unit": "m"}}},
              "parameters": {"nodes": {"floor_methods": ["structure_floor", "unique_active_outgoing_port"],
                                       "rim_methods": ["structure_rim"]},
                             "pipes": {"length_methods": ["attribute_length"], "diameter_methods": ["attribute_diameter"],
                                       "shape": "CIRCULAR"}},
              "roughness": {"parameter": "manning_n", "rules": {"SYNTHETIC_MATERIAL": {"value": 0.013,
                                                                                              "assumptions": ["Synthetic test coefficient."]}},
                            "unknown_material": {"policy": "hold"}},
              "qa": {"readback_absolute_tolerance": {"value": 1e-9, "unit": "m"}}}
    return canonical, config


def test_cleaned_ports_produce_complete_coherent_profiles_and_verified_shmm_preview() -> None:
    """Preparation and serialized readback agree with independent profile hand calculations."""
    canonical, config = preparation_case()
    untouched = deepcopy(canonical)
    prepared = prepare_network_from_dict(canonical, config).to_dict()
    assert not prepared["held"]
    nodes = {row["asset_id"]: row for row in prepared["nodes"]}
    pipes = {row["asset_id"]: row for row in prepared["pipes"]}
    assert nodes["NODE_A"]["floor_m"] == pytest.approx(100.5 * FT_TO_M)
    assert nodes["NODE_A"]["rim_m"] == pytest.approx(110 * FT_TO_M)
    assert nodes["NODE_A"]["max_depth_m"] == pytest.approx(9.5 * FT_TO_M)
    assert nodes["NODE_B"]["floor_m"] == pytest.approx(99.7 * FT_TO_M)
    assert nodes["NODE_B"]["max_depth_m"] == pytest.approx(9.3 * FT_TO_M)
    assert pipes["PIPE_A"]["inlet_offset_m"] == pytest.approx(0)
    assert pipes["PIPE_A"]["outlet_offset_m"] == pytest.approx(0.1 * FT_TO_M)
    assert pipes["PIPE_A"]["grade"] == pytest.approx(0.007)
    assert pipes["PIPE_B"]["inlet_offset_m"] == pytest.approx(0)
    assert pipes["PIPE_B"]["outlet_offset_m"] == pytest.approx(0.2 * FT_TO_M)
    assert pipes["PIPE_B"]["grade"] == pytest.approx(0.015)
    baseline = SWMMDocument.from_bytes(b"[OPTIONS]\nFLOW_UNITS CFS\nLINK_OFFSETS DEPTH\n[JUNCTIONS]\nOLD_A 10 2\nOLD_B 9 2\n[CONDUITS]\nOLD_LINK OLD_A OLD_B 10 0.013 0 0\n[XSECTIONS]\nOLD_LINK CIRCULAR 1 0 0 0\n[COORDINATES]\nOLD_A -20 0\nOLD_B -10 0\n")
    handoff = compile_handoff(baseline, prepared, config, config_sha256="synthetic-config")
    changes, vertices = instructions_from_handoff(handoff)
    result = preview_changes(baseline, changes, vertex_changes=vertices)
    candidate = SWMMDocument.from_bytes(result.data)
    verification = verify_prepared_profiles(candidate, prepared, handoff, config)
    assert verification["checked_nodes"] == 3
    assert verification["checked_pipes"] == 2
    assert canonical == untouched
    assert "configured_omitted_suffix" in str(prepared["decisions"])


@pytest.mark.parametrize("suffix", ["CAPPED", "UNKNOWN_QUALIFIER", "DROP"])
def test_unrecognized_qualifier_stays_held_without_inventing_floor_or_invalidating_other_pipe(suffix: str) -> None:
    """Grammar extension is local; unrelated independently resolved profiles remain usable."""
    canonical, config = preparation_case()
    canonical["nodes"][0]["outlet_description"] += " " + suffix
    prepared = prepare_network_from_dict(canonical, config).to_dict()
    pipes = {row["asset_id"]: row for row in prepared["pipes"]}
    nodes = {row["asset_id"]: row for row in prepared["nodes"]}
    assert pipes["PIPE_A"]["disposition"] == "hold"
    assert pipes["PIPE_A"]["upstream_invert_m"] is None
    assert nodes["NODE_A"]["floor_m"] is None
    assert pipes["PIPE_B"]["disposition"] == "include"
