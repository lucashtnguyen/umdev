"""Synthetic force-main parameter and pressure-connection regressions.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained; all measurements, identities and policies are fictional.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from umdev.configuration import ConfigurationError, load_config, validate_config
from umdev.parameters import prepare_network_from_dict, resolve_pressure_parameters

from test_preparation import policy, sample


def pressure_policy():
    """Return explicit synthetic coefficients and pressure endpoint roles."""
    config = policy()
    config["roughness"]["pressure_parameters"] = {
        "policy": "explicit_assumption", "equation": "H-W", "coefficient": 125,
        "conduit_manning_n": 0.014, "basis": "Synthetic test coefficient"}
    config["parameters"]["nodes"]["pressure_connections"] = {
        "classification": "internal_force_main_only", "surcharge_depth": {"value": 700, "unit": "ft"},
        "basis": "Synthetic sealed connection assumption",
        "node_overrides": {"001": "sealed", "003": "open_discharge"}}
    return config


def pressure_sample():
    """Return two synthetic pressure conduits with an internal junction."""
    canonical = sample()
    for pipe in canonical["pipes"]:
        pipe["pipe_kind"] = "force_main"
    return canonical


def test_pressure_coefficient_separate_from_partial_flow_manning_and_internal_seal():
    canonical, config = pressure_sample(), pressure_policy()
    original = deepcopy(canonical)
    prepared = prepare_network_from_dict(canonical, config)
    assert not prepared.held
    assert canonical == original
    assert all(pipe["shape"] == "FORCE_MAIN" for pipe in prepared.pipes)
    assert all(pipe["roughness"] == 0.014 for pipe in prepared.pipes)
    assert all(pipe["pressure_assignment"]["value"] == 125 for pipe in prepared.pipes)
    nodes = {node["asset_id"]: node for node in prepared.nodes}
    assert nodes["002"]["pressure_connection"]["method"] == "internal_force_main_only"
    assert nodes["002"]["surcharge_depth_m"] == pytest.approx(700 * 0.3048)
    assert nodes["003"]["pressure_connection"]["role"] == "open_discharge"
    assert nodes["003"]["surcharge_depth_m"] is None


def test_darcy_absolute_roughness_is_converted_to_metres():
    config = pressure_policy()
    config["roughness"]["pressure_parameters"].update(equation="D-W", coefficient={"value": 0.25, "unit": "mm"})
    pressure = resolve_pressure_parameters(config["roughness"])
    assert pressure["value"] == pytest.approx(0.00025)
    assert pressure["unit"] == "m"
    assert pressure["conduit_manning_n"] == 0.014


def test_terminal_connection_without_role_is_held_instead_of_assuming_sealed():
    config = pressure_policy()
    config["parameters"]["nodes"]["pressure_connections"]["node_overrides"].pop("001")
    prepared = prepare_network_from_dict(pressure_sample(), config)
    node = next(node for node in prepared.nodes if node["asset_id"] == "001")
    assert node["pressure_connection"]["role"] == "unresolved"
    assert "unresolved_pressure_connection_role" in node["pressure_connection"]["reasons"]
    assert prepared.pipes[0]["disposition"] == "hold"


def test_mixed_gravity_connection_requires_explicit_role_even_if_gravity_not_selected():
    canonical = pressure_sample()
    tributary = deepcopy(canonical["pipes"][0])
    tributary.update(key="s/p/other", asset_id="other", pipe_kind="gravity")
    canonical["pipes"].append(tributary)
    prepared = prepare_network_from_dict(canonical, pressure_policy(), selected_pipe_keys=["s/p/A", "s/p/B"])
    node = next(node for node in prepared.nodes if node["asset_id"] == "002")
    assert node["pressure_connection"]["role"] == "unresolved"
    assert node["surcharge_depth_m"] is None


def test_unselected_force_main_does_not_seal_selected_gravity_node():
    canonical = pressure_sample()
    canonical["pipes"][0]["pipe_kind"] = "gravity"
    prepared = prepare_network_from_dict(canonical, pressure_policy(), selected_pipe_keys=["s/p/A"])
    assert all("pressure_connection" not in node for node in prepared.nodes)
    assert not prepared.held


def test_pressure_policy_absent_leaves_force_main_held():
    prepared = prepare_network_from_dict(pressure_sample(), policy())
    assert all(pipe["disposition"] == "hold" for pipe in prepared.pipes)
    assert all("unsupported_pressure_or_parameter_contract" in pipe["reasons"] for pipe in prepared.pipes)


def test_pressure_node_policy_absent_leaves_force_main_held():
    config = pressure_policy()
    config["parameters"]["nodes"].pop("pressure_connections")
    prepared = prepare_network_from_dict(pressure_sample(), config)
    assert all(pipe["disposition"] == "hold" for pipe in prepared.pipes)
    assert "upstream:unresolved_pressure_connection_role" in prepared.pipes[0]["reasons"]


def test_existing_storage_is_preserved_as_wet_well_without_surcharge_assignment():
    config = pressure_policy()
    config["parameters"]["nodes"]["pressure_connections"]["node_overrides"].pop("001")
    prepared = prepare_network_from_dict(pressure_sample(), config,
        baseline_nodes={"001": {"floor_m": 102, "rim_m": 112, "model_section": "STORAGE"}})
    node = next(node for node in prepared.nodes if node["asset_id"] == "001")
    assert node["pressure_connection"]["role"] == "wet_well"
    assert node["surcharge_depth_m"] is None
    assert node["floor_m"] == 102 and node["rim_m"] == 112


def test_new_wet_well_cannot_be_invented_from_junction_profile():
    config = pressure_policy()
    config["parameters"]["nodes"]["pressure_connections"]["node_overrides"]["001"] = "wet_well"
    node = prepare_network_from_dict(pressure_sample(), config).nodes[0]
    assert node["disposition"] == "include"
    assert "unresolved_pressure_connection_role" in node["pressure_connection"]["reasons"]
    assert node["pressure_connection"]["method"] == "unsupported_new_wet_well_contract"


def test_missing_rim_is_still_held_with_pressure_seal():
    canonical = pressure_sample()
    canonical["nodes"][0]["rim"] = None
    prepared = prepare_network_from_dict(canonical, pressure_policy())
    assert prepared.nodes[0]["rim_m"] is None
    assert "missing_required_rim" in prepared.nodes[0]["reasons"]
    assert prepared.pipes[0]["disposition"] == "hold"


def validated_policy():
    """Combine a complete synthetic project schema with pressure additions."""
    config = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").to_dict()
    pressure = pressure_policy()
    config["roughness"]["pressure_parameters"] = pressure["roughness"]["pressure_parameters"]
    config["parameters"]["nodes"]["pressure_connections"] = pressure["parameters"]["nodes"]["pressure_connections"]
    return config


def test_pressure_configuration_validates():
    validate_config(validated_policy())


@pytest.mark.parametrize("edit", [
    lambda p: p.update(equation="unknown"),
    lambda p: p.update(coefficient=True),
    lambda p: p.update(coefficient=0),
    lambda p: p.update(conduit_manning_n=-1),
    lambda p: p.update(basis=""),
    lambda p: p.update(equation="D-W", coefficient=0.1),
    lambda p: p.update(equation="D-W", coefficient={"value": 0.1, "unit": "feet"}),
])
def test_invalid_pressure_configuration_is_rejected(edit):
    config = validated_policy()
    edit(config["roughness"]["pressure_parameters"])
    with pytest.raises(ConfigurationError):
        validate_config(config)


def test_invalid_connection_role_is_rejected():
    config = validated_policy()
    config["parameters"]["nodes"]["pressure_connections"]["node_overrides"]["001"] = "sealed_guess"
    with pytest.raises(ConfigurationError):
        validate_config(config)


def test_non_circular_source_pressure_shape_is_not_silently_replaced():
    canonical = pressure_sample()
    canonical["pipes"][0]["shape"] = "RECT_CLOSED"
    pipe = prepare_network_from_dict(canonical, pressure_policy()).pipes[0]
    assert pipe["disposition"] == "hold"
    assert "unsupported_pipe_shape" in pipe["reasons"]


def test_assumed_surcharge_respects_allowed_assignment_categories():
    config = pressure_policy()
    config["qa"]["allow_categories"] = ["observed", "provisional", "interpreted"]
    node = prepare_network_from_dict(pressure_sample(), config).nodes[0]
    assert "surcharge_depth:assignment_category_not_allowed" in node["pressure_connection"]["reasons"]


def test_held_pressure_role_does_not_remove_complete_gravity_pipe_at_shared_manhole():
    canonical = pressure_sample()
    canonical["pipes"][1]["pipe_kind"] = "gravity"
    prepared = prepare_network_from_dict(canonical, pressure_policy())
    pipes = {pipe["asset_id"]: pipe for pipe in prepared.pipes}
    node = next(node for node in prepared.nodes if node["asset_id"] == "002")
    assert node["disposition"] == "include"
    assert node["pressure_connection"]["role"] == "unresolved"
    assert pipes["A"]["disposition"] == "hold"
    assert "downstream:unresolved_pressure_connection_role" in pipes["A"]["reasons"]
    assert pipes["B"]["disposition"] == "include"


def test_invalid_model_connection_role_is_rejected():
    config = validated_policy()
    config["parameters"]["nodes"]["pressure_connections"]["model_node_overrides"] = {"ModeledNode": "sealed_guess"}
    with pytest.raises(ConfigurationError):
        validate_config(config)
