"""Synthetic engineering regression checks for utility preparation.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
No municipal identities, measurements or project policies are used.
"""
from copy import deepcopy
import math

import pytest

from umdev.evidence import choose_assignment, normalize_elevation, parse_inline_port, resolve_ports
from umdev.parameters import prepare_network_from_dict, resolve_roughness
from umdev.records import network_from_dict


def policy():
    return {
        "model": {"target": {"link_offsets": "DEPTH", "vertical_datum": "TEST_B"}},
        "elevations": {"target_datum": "TEST_B", "allow_provisional": True,
                       "transforms": [{"id": "survey_test", "source_datum": "TEST_A", "target_datum": "TEST_B",
                                       "method": "constant_offset", "offset": {"value": 2, "unit": "m"},
                                       "verified": False, "basis": "Synthetic test shift"}]},
        "ports": {"observations": [], "assignment": {
            "precedence": ["pipe_numeric", "normalized_port", "inline_port"],
            "direction": "hint", "unique_role_diameter_fallback": {"enabled": True},
            "diameter_tolerance": {"value": 1, "unit": "mm"},
            "elevation_conflict_tolerance": {"value": 0.001, "unit": "m"},
            "endpoint_tolerance": {"value": 0.01, "unit": "m"}, "direction_half_sector_deg": 22.5,
            "numeric_text_conflict": "prefer_ranked_with_flag"}},
        "parameters": {"nodes": {"floor_methods": ["structure_floor", "unique_active_outgoing_port"],
                                   "rim_methods": ["structure_rim"]},
                       "pipes": {"length_methods": ["attribute_length", "geometry_length"],
                                 "diameter_methods": ["attribute_diameter"], "shape": "CIRCULAR"}},
        "roughness": {"parameter": "manning_n", "policy_id": "synthetic_only", "rules": {
            "TEST_MATERIAL": {"aliases": ["Example smooth pipe"], "value": 0.012,
                              "reference_range": [0.01, 0.02], "source": "Synthetic test policy", "assumptions": ["Test only"]}},
            "unknown_material": {"policy": "hold"}},
        "qa": {"rules": {}}
    }


def sample():
    return {"nodes": [
        {"key": "s/n/001", "dataset": "nodes", "asset_id": "001", "floor": 100, "rim": 110,
         "vertical_datum": "TEST_A", "coordinates": [0, 0]},
        {"key": "s/n/002", "dataset": "nodes", "asset_id": "002", "floor": 99, "rim": 109,
         "vertical_datum": "TEST_A", "coordinates": [10, 0]},
        {"key": "s/n/003", "dataset": "nodes", "asset_id": "003", "floor": 98, "rim": 108,
         "vertical_datum": "TEST_A", "coordinates": [20, 0]}],
        "pipes": [
        {"key": "s/p/A", "asset_id": "A", "from_node": "001", "to_node": "002", "diameter": 0.3,
         "length": 10, "upstream_invert": 100.2, "downstream_invert": 99.1, "vertical_datum": "TEST_A",
         "coordinates": [[0, 0], [10, 0]], "material": "Example smooth pipe", "pipe_kind": "gravity"},
        {"key": "s/p/B", "asset_id": "B", "from_node": "002", "to_node": "003", "diameter": 0.3,
         "length": 10, "upstream_invert": 99, "downstream_invert": 98.2, "vertical_datum": "TEST_A",
         "coordinates": [[10, 0], [20, 0]], "material": "TEST_MATERIAL", "pipe_kind": "gravity"}], "ports": []}


def test_complete_profile_hand_calculation_and_no_input_mutation():
    canonical, config = sample(), policy()
    original = deepcopy(canonical)
    prepared = prepare_network_from_dict(canonical, config).to_dict()
    assert not prepared["held"]
    nodes = {node["asset_id"]: node for node in prepared["nodes"]}
    pipe = prepared["pipes"][0]
    assert nodes["001"]["floor_m"] == 102
    assert nodes["001"]["rim_m"] == 112
    assert nodes["001"]["max_depth_m"] == 10
    assert pipe["inlet_offset_m"] == pytest.approx(0.2)
    assert pipe["outlet_offset_m"] == pytest.approx(0.1)
    assert pipe["grade"] == pytest.approx(0.11)
    assert pipe["roughness"] == 0.012
    assert canonical == original
    assert "provisional_vertical_basis" in pipe["assignments"]["upstream"]["flags"]


def test_elevation_offsets_are_absolute_not_depths():
    config = policy()
    config["model"]["target"]["link_offsets"] = "ELEVATION"
    pipe = prepare_network_from_dict(sample(), config).pipes[0]
    assert pipe["inlet_offset_m"] == pytest.approx(102.2)
    assert pipe["outlet_offset_m"] == pytest.approx(101.1)


def test_existing_floor_is_preserved_and_offsets_recomputed():
    prepared = prepare_network_from_dict(sample(), policy(), baseline_nodes={"001": {"floor_m": 101, "rim_m": 111}})
    assert prepared.nodes[0]["floor_m"] == 101
    assert prepared.nodes[0]["rim_m"] == 111
    assert prepared.pipes[0]["inlet_offset_m"] == pytest.approx(1.2)


def test_missing_rim_stays_missing_and_holds_attached_pipe():
    canonical = sample()
    canonical["nodes"][0]["rim"] = None
    prepared = prepare_network_from_dict(canonical, policy())
    assert prepared.nodes[0]["rim_m"] is None
    assert prepared.pipes[0]["disposition"] == "hold"
    assert "upstream:incomplete_node_profile" in prepared.pipes[0]["reasons"]


@pytest.mark.parametrize("bad", [None, True, math.nan, math.inf, -math.inf, "bad"])
def test_nonfinite_or_invalid_required_port_values_are_held(bad):
    canonical = sample()
    canonical["pipes"][0]["upstream_invert"] = bad
    pipe = prepare_network_from_dict(canonical, policy()).pipes[0]
    assert pipe["upstream_invert_m"] is None
    assert pipe["disposition"] == "hold"


def test_measured_zero_is_retained():
    assignment = normalize_elevation(0, "TEST_A", policy())
    assert assignment.value == 2


def test_unknown_datum_is_not_inferred_from_magnitude():
    assignment = normalize_elevation(100, "UNKNOWN", policy())
    assert assignment.value is None
    assert "unknown_vertical_basis" in assignment.flags


def test_identity_cannot_silently_change_datum():
    config = policy()
    config["elevations"]["transforms"][0]["method"] = "identity"
    with pytest.raises(ValueError, match="identical declared datums"):
        normalize_elevation(100, "TEST_A", config)


def test_ambiguous_transform_rules_do_not_choose_first():
    config = policy()
    config["elevations"]["transforms"].append(deepcopy(config["elevations"]["transforms"][0]))
    assignment = normalize_elevation(100, "TEST_A", config)
    assert assignment.value is None
    assert "ambiguous_vertical_transform" in assignment.flags


def test_unverified_transform_can_be_disallowed():
    config = policy()
    config["elevations"]["allow_provisional"] = False
    assert normalize_elevation(100, "TEST_A", config).value is None


def test_inline_port_units_and_raw_text():
    row = parse_inline_port('100.2 (SE) 12"', node_id="001", key="obs/a", role="out",
                            elevation_unit="ft", diameter_unit="in", vertical_datum="TEST_A")
    assert row["invert"] == pytest.approx(100.2 * 0.3048)
    assert row["diameter"] == pytest.approx(0.3048)
    assert row["direction"] == "SE"
    assert row["raw"] == '100.2 (SE) 12"'


@pytest.mark.parametrize("text", ['100/101 (SE) 12" DROP', '100 (SE) 12" capped', '100 and 101', 'garbage'])
def test_qualified_or_multiple_inline_elevations_do_not_pick_arbitrary_number(text):
    row = parse_inline_port(text, node_id="001", key="obs/a", role="out", elevation_unit="m", diameter_unit="mm")
    assert row["invert"] is None
    assert row["flags"]


def with_port():
    canonical = sample()
    canonical["pipes"][0]["upstream_invert"] = None
    canonical["ports"].append({"key": "s/ports/port1", "structure_id": "001", "role": "out",
                               "invert": 100.2, "diameter": 0.3, "direction": "SW", "vertical_datum": "TEST_A"})
    return canonical


def test_unique_role_and_diameter_survives_direction_mismatch_with_flag():
    pipe = prepare_network_from_dict(with_port(), policy()).pipes[0]
    assert pipe["upstream_invert_m"] == pytest.approx(102.2)
    assert pipe["disposition"] == "include"
    assert pipe["assignments"]["upstream"]["method"] == "unique_role_diameter_fallback"
    assert "port_direction_mismatch" in pipe["assignments"]["upstream"]["flags"]


def test_competing_same_size_pipe_outside_export_prevents_unique_fallback():
    canonical = with_port()
    other = deepcopy(canonical["pipes"][0])
    other.update(key="s/p/C", asset_id="C", upstream_invert=None)
    canonical["pipes"].append(other)
    pipe = prepare_network_from_dict(canonical, policy(), selected_pipe_keys=["s/p/A"]).pipes[0]
    assert pipe["upstream_invert_m"] is None
    assert pipe["disposition"] == "hold"


def test_direct_pipe_reference_resolves_parallel_assets_with_warning():
    canonical = with_port()
    other = deepcopy(canonical["pipes"][0])
    other.update(key="s/p/C", asset_id="C", upstream_invert=None)
    canonical["pipes"].append(other)
    canonical["ports"][0]["pipe_id"] = "A"
    pipe = prepare_network_from_dict(canonical, policy(), selected_pipe_keys=["s/p/A"]).pipes[0]
    assert pipe["upstream_invert_m"] == pytest.approx(102.2)
    assert pipe["assignments"]["upstream"]["method"] == "explicit_pipe_reference"


def test_strict_direction_policy_holds_mismatch():
    config = policy()
    config["ports"]["assignment"]["direction"] = "strict"
    assert prepare_network_from_dict(with_port(), config).pipes[0]["upstream_invert_m"] is None


def test_missing_preferred_numeric_uses_lower_rank_and_conflicting_numeric_flagged():
    candidates = [{"family": "pipe_numeric", "value": None},
                  {"family": "normalized_port", "value": 12, "source_key": "ports/a"}]
    assert choose_assignment(candidates, precedence=["pipe_numeric", "normalized_port"], tolerance=0).value == 12
    candidates[0]["value"] = 14
    chosen = choose_assignment(candidates, precedence=["pipe_numeric", "normalized_port"], tolerance=0)
    assert chosen.value == 14
    assert chosen.category == "provisional"
    assert len(chosen.alternatives) == 1
    assert "conflicting_elevation_evidence" in chosen.flags


def test_equal_rank_conflicts_never_pick_input_order():
    candidates = [{"family": "inline_port", "value": 12, "source_key": "a"},
                  {"family": "inline_port", "value": 14, "source_key": "b"}]
    assert choose_assignment(candidates, precedence=["inline_port"], tolerance=0).value is None
    assert choose_assignment(list(reversed(candidates)), precedence=["inline_port"], tolerance=0).value is None


def test_unique_outgoing_port_floor_is_not_lowest_arbitrary_number():
    canonical = sample()
    canonical["nodes"][1]["floor"] = None
    node = prepare_network_from_dict(canonical, policy()).nodes[1]
    assert node["floor_m"] == 101
    assert node["assignments"]["floor"]["method"] == "unique_active_outgoing_port"


def test_adverse_grade_is_a_qa_finding_not_silent_reversal_or_hold():
    canonical = sample()
    canonical["pipes"][0].update(upstream_invert=100.2, downstream_invert=101)
    prepared = prepare_network_from_dict(canonical, policy())
    pipe = prepared.pipes[0]
    assert pipe["from_node"] == "001"
    assert pipe["grade"] < 0
    assert pipe["disposition"] == "include"
    assert any(row["rule"] == "adverse_gravity_grade" for row in prepared.findings)


def test_project_can_hold_adverse_grade_explicitly():
    canonical, config = sample(), policy()
    canonical["pipes"][0].update(upstream_invert=100.2, downstream_invert=101)
    config["qa"]["rules"]["adverse_gravity_grade"] = {"disposition": "hold"}
    assert prepare_network_from_dict(canonical, config).pipes[0]["disposition"] == "hold"


def test_negative_depth_offset_is_not_clamped():
    canonical = sample()
    canonical["pipes"][0]["upstream_invert"] = 99.5
    pipe = prepare_network_from_dict(canonical, policy()).pipes[0]
    assert pipe["inlet_offset_m"] == -0.5
    assert pipe["disposition"] == "hold"


def test_unknown_material_requires_explicit_assumption():
    config = policy()
    assert resolve_roughness("MISSING", "gravity", config["roughness"])["value"] is None
    config["roughness"]["unknown_material"] = {"policy": "explicit_assumption", "value": 0.014,
                                                 "basis": "Synthetic fallback for test only"}
    assignment = resolve_roughness("MISSING", "gravity", config["roughness"])
    assert assignment["value"] == 0.014
    assert assignment["category"] == "assumed"
    assert assignment["flags"]


def test_pressure_conduit_cannot_inherit_gravity_roughness():
    assert resolve_roughness("TEST_MATERIAL", "force_main", policy()["roughness"])["value"] is None


def test_material_aliases_exact_and_overlap_rejected():
    config = policy()
    assert resolve_roughness("example SMOOTH pipe", "gravity", config["roughness"])["value"] == 0.012
    assert resolve_roughness("prefix Example smooth pipe", "gravity", config["roughness"])["value"] is None
    config["roughness"]["rules"]["OTHER"] = {"aliases": ["Example smooth pipe"], "value": 0.015}
    with pytest.raises(ValueError, match="Ambiguous"):
        resolve_roughness("Example smooth pipe", "gravity", config["roughness"])


@pytest.mark.parametrize("bad", [True, 0, -1, math.nan, math.inf])
def test_invalid_material_coefficients_rejected(bad):
    config = policy()
    config["roughness"]["rules"]["TEST_MATERIAL"]["value"] = bad
    with pytest.raises(ValueError, match="positive and finite"):
        resolve_roughness("TEST_MATERIAL", "gravity", config["roughness"])


def test_unknown_selected_key_rejected_before_preparation():
    with pytest.raises(ValueError, match="not found"):
        prepare_network_from_dict(sample(), policy(), selected_pipe_keys=["missing"])


def test_duplicate_asset_identity_rejected_preserving_leading_zero():
    canonical = sample()
    assert network_from_dict(canonical).nodes[0]["asset_id"] == "001"
    canonical["nodes"].append({**canonical["nodes"][0], "key": "other/node"})
    with pytest.raises(ValueError, match="ambiguous"):
        network_from_dict(canonical)


def test_identical_inputs_reproduce_semantic_artifacts():
    assert prepare_network_from_dict(sample(), policy()).to_dict() == prepare_network_from_dict(sample(), policy()).to_dict()


def test_abandoned_outlet_does_not_obstruct_unique_active_floor():
    canonical, config = sample(), policy()
    canonical["nodes"][0]["floor"] = None
    canonical["pipes"][0]["status"] = "active"
    other = deepcopy(canonical["pipes"][0])
    other.update(key="s/p/retired", asset_id="retired", status="abandoned", upstream_invert=97)
    canonical["pipes"].append(other)
    config["selection"] = {"scenario": "existing"}
    config["scenarios"] = {"existing": {"include_statuses": ["active"], "exclude_statuses": ["abandoned"]}}
    prepared = prepare_network_from_dict(canonical, config, selected_pipe_keys=["s/p/A"])
    assert prepared.nodes[0]["floor_m"] == pytest.approx(102.2)


def test_shape_is_not_an_unrecorded_core_default():
    config = policy()
    del config["parameters"]["pipes"]["shape"]
    prepared = prepare_network_from_dict(sample(), config)
    assert prepared.pipes[0]["disposition"] == "hold"
    assert prepared.pipes[0]["shape"] is None
    assert "missing_required_shape" in prepared.pipes[0]["reasons"]


def test_inline_explicit_unit_cannot_be_silently_reinterpreted_by_config():
    row = parse_inline_port('100 ft (SE) 12"', node_id="001", key="obs/a", role="out",
                            elevation_unit="m", diameter_unit="in")
    assert row["invert"] is None
    assert row["flags"] == ["port_unit_conflict"]


def reviewed_config(canonical, *, source_key="s/p/A", field="upstream_invert", value=100.4):
    config = policy()
    for row in canonical["nodes"] + canonical["pipes"]:
        row["source_record_sha256"] = "synthetic-evidence-a"
    config["ports"]["assignment"]["precedence"].insert(0, "reviewed_override")
    config["parameters"]["nodes"]["floor_methods"].insert(0, "reviewed_override")
    config["parameters"]["nodes"]["rim_methods"].insert(0, "reviewed_override")
    config["parameters"]["pipes"]["length_methods"].insert(0, "reviewed_override")
    config["review"] = {"changed_evidence": "mark_stale_and_re_evaluate", "overrides": [{
        "source_key": source_key, "field": field, "value": value, "unit": "m", "vertical_datum": "TEST_A",
        "evidence_sha256": "synthetic-evidence-a", "rationale": "Synthetic reviewed field observation",
        "reviewer": "Test reviewer", "date": "2026-10-14"}]}
    return config


def test_reviewed_endpoint_supersedes_numeric_without_erasing_original_observation():
    canonical = sample()
    config = reviewed_config(canonical)
    prepared = prepare_network_from_dict(canonical, config)
    pipe = prepared.pipes[0]
    assert pipe["upstream_invert_m"] == pytest.approx(102.4)
    assert pipe["upstream_invert"] == 100.2
    assert pipe["assignments"]["upstream"]["method"] == "reviewed_override"
    assert "conflicting_elevation_evidence" in pipe["assignments"]["upstream"]["flags"]
    assert pipe["assignments"]["upstream"]["evidence"][0]["review"]["rationale"]


def test_reviewed_floor_and_rim_use_scoped_field_and_recompute_depth():
    canonical = sample()
    config = reviewed_config(canonical, source_key="s/n/001", field="rim", value=111)
    prepared = prepare_network_from_dict(canonical, config)
    assert prepared.nodes[0]["rim_m"] == 113
    assert prepared.nodes[0]["max_depth_m"] == 11
    config["review"]["overrides"][0].update(field="floor", value=99)
    prepared = prepare_network_from_dict(canonical, config)
    assert prepared.nodes[0]["floor_m"] == 101
    assert prepared.nodes[0]["max_depth_m"] == 11


def test_changed_evidence_invalidates_only_targeted_override_and_re_evaluates():
    canonical = sample()
    config = reviewed_config(canonical)
    original = prepare_network_from_dict(canonical, config)
    canonical["pipes"][0]["source_record_sha256"] = "synthetic-evidence-b"
    changed = prepare_network_from_dict(canonical, config)
    assert original.pipes[0]["upstream_invert_m"] == pytest.approx(102.4)
    assert changed.pipes[0]["upstream_invert_m"] == pytest.approx(102.2)
    assert original.pipes[1] == changed.pipes[1]
    assert any(row["rule"] == "stale_reviewed_override" for row in changed.findings)


def test_stale_review_can_explicitly_hold_targeted_field():
    canonical = sample()
    config = reviewed_config(canonical)
    config["review"]["changed_evidence"] = "hold"
    canonical["pipes"][0]["source_record_sha256"] = "new-evidence"
    pipe = prepare_network_from_dict(canonical, config).pipes[0]
    assert pipe["upstream_invert_m"] is None
    assert pipe["disposition"] == "hold"


def test_reviewed_length_preserves_attribute_and_changes_effective_hydraulic_grade():
    canonical = sample()
    config = reviewed_config(canonical, field="length", value=20)
    pipe = prepare_network_from_dict(canonical, config).pipes[0]
    assert pipe["length"] == 10
    assert pipe["length_m"] == 20
    assert pipe["grade"] == pytest.approx(0.055)


@pytest.mark.parametrize("field", ["asset_id", "from_node", "bogus"])
def test_review_cannot_rename_or_arbitrarily_mutate_source_fields(field):
    canonical = sample()
    config = reviewed_config(canonical, field=field)
    with pytest.raises(ValueError, match="Unsupported"):
        prepare_network_from_dict(canonical, config)


def test_two_heterogeneous_utility_schemas_produce_equivalent_complete_profiles():
    from pathlib import Path
    from umdev.configuration import load_config
    from umdev.sources import ingest_sources

    fixture_directory = Path(__file__).parent / "fixtures" / "schema_equivalence"
    prepared = []
    for name in ("utility_a.config.json", "utility_b.config.json"):
        config = load_config(fixture_directory / name).data
        canonical = ingest_sources(config)
        prepared.append(prepare_network_from_dict(canonical, config))
    assert not prepared[0].held
    assert not prepared[1].held
    for first, second in zip(prepared[0].nodes, prepared[1].nodes):
        assert first["asset_id"] == second["asset_id"]
        for field in ("floor_m", "rim_m", "max_depth_m"):
            assert first[field] == pytest.approx(second[field], abs=1e-10)
    for first, second in zip(prepared[0].pipes, prepared[1].pipes):
        assert first["asset_id"] == second["asset_id"]
        for field in ("diameter_m", "length_m", "roughness", "upstream_invert_m", "downstream_invert_m", "inlet_offset_m", "outlet_offset_m", "grade"):
            assert first[field] == pytest.approx(second[field], abs=1e-10)



def test_project_can_disallow_provisional_assignments_without_lowering_validation():
    config = policy()
    config["qa"]["allow_categories"] = ["observed", "interpreted", "assumed"]
    prepared = prepare_network_from_dict(sample(), config)
    assert prepared.pipes[0]["disposition"] == "hold"
    assert "upstream:assignment_category_not_allowed" in prepared.pipes[0]["reasons"]


def test_missing_new_node_geometry_is_held_before_model_handoff():
    canonical = sample()
    canonical["nodes"][0]["coordinates"] = None
    prepared = prepare_network_from_dict(canonical, policy())
    assert prepared.nodes[0]["disposition"] == "hold"
    assert "unusable_point_geometry" in prepared.nodes[0]["reasons"]
    assert prepared.pipes[0]["disposition"] == "hold"


@pytest.mark.parametrize("limits", [[0.02, 0.01], [0, 1], [math.nan, 1], "ab", [0.01]])
def test_malformed_material_reference_ranges_fail_explicitly(limits):
    config = policy()
    config["roughness"]["rules"]["TEST_MATERIAL"]["reference_range"] = limits
    with pytest.raises(ValueError, match="range"):
        resolve_roughness("TEST_MATERIAL", "gravity", config["roughness"])


def test_strict_direction_does_not_accept_unmeasured_tangent_with_direct_reference():
    canonical, config = with_port(), policy()
    config["ports"]["assignment"]["direction"] = "strict"
    canonical["ports"][0]["pipe_id"] = "A"
    canonical["pipes"][0]["coordinates"] = [[1, 0], [10, 0]]
    pipe = prepare_network_from_dict(canonical, config).pipes[0]
    assert pipe["upstream_invert_m"] is None


def test_unknown_tangent_fallback_is_not_mislabeled_as_known_direction_mismatch():
    canonical, config = with_port(), policy()
    config["ports"]["assignment"]["unique_role_diameter_fallback"]["qa_flag"] = "port_direction_mismatch"
    canonical["pipes"][0]["coordinates"] = [[1, 0], [10, 0]]
    pipe = prepare_network_from_dict(canonical, config).pipes[0]
    flags = pipe["assignments"]["upstream"]["flags"]
    assert "port_direction_unverified" in flags
    assert "port_direction_mismatch" not in flags


def test_unique_direction_match_outranks_mismatched_fallback_from_same_port_family():
    canonical = sample()
    canonical["pipes"][0]["downstream_invert"] = None
    canonical["ports"] = [
        {"key": "ports/strong", "structure_id": "002", "role": "in", "invert": 99.1,
         "diameter": 0.3, "direction": "W", "vertical_datum": "TEST_A"},
        {"key": "ports/weak", "structure_id": "002", "role": "in", "invert": 99.2,
         "diameter": 0.3, "direction": "SE", "vertical_datum": "TEST_A"}]
    pipe = prepare_network_from_dict(canonical, policy()).pipes[0]
    assignment = pipe["assignments"]["downstream"]
    assert pipe["downstream_invert_m"] == pytest.approx(101.1)
    assert pipe["disposition"] == "include"
    assert assignment["method"] == "role_diameter_direction"
    assert assignment["alternatives"][0]["value"] == pytest.approx(101.2)
    assert assignment["category"] == "provisional"
    assert "conflicting_elevation_evidence" in assignment["flags"]


def test_two_equally_strong_direction_matches_with_conflicting_values_remain_held():
    canonical = sample()
    canonical["pipes"][0]["downstream_invert"] = None
    canonical["ports"] = [
        {"key": "ports/a", "structure_id": "002", "role": "in", "invert": 99.1,
         "diameter": 0.3, "direction": "W", "vertical_datum": "TEST_A"},
        {"key": "ports/b", "structure_id": "002", "role": "in", "invert": 99.2,
         "diameter": 0.3, "direction": "W", "vertical_datum": "TEST_A"}]
    pipe = prepare_network_from_dict(canonical, policy()).pipes[0]
    assert pipe["downstream_invert_m"] is None
    assert pipe["disposition"] == "hold"


def test_case_variant_explicit_model_id_uses_existing_floor():
    canonical = sample()
    canonical["nodes"][0]["model_id"] = "model_lower"
    prepared = prepare_network_from_dict(canonical, policy(),
                                         baseline_nodes={"MODEL_LOWER": {"floor_m": 101, "rim_m": 111}})
    assert prepared.nodes[0]["is_existing"]
    assert prepared.nodes[0]["floor_m"] == 101
    assert prepared.pipes[0]["inlet_offset_m"] == pytest.approx(1.2)


def test_case_colliding_baseline_node_ids_are_rejected():
    with pytest.raises(ValueError, match="Case-insensitive"):
        prepare_network_from_dict(sample(), policy(), baseline_nodes={
            "node_x": {"floor_m": 100, "rim_m": 110}, "NODE_X": {"floor_m": 100, "rim_m": 110}})
