"""Synthetic missing-rim policy contracts and unit/datum regression checks.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained; no private utility data or project configuration.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from umdev.configuration import ConfigurationError, load_config, validate_config
from umdev.placeholders import resolve_missing_rim
from umdev.records import Assignment


FLOOR = Assignment(100.0, "observed", "structure_floor")
MISSING = Assignment(None, "unresolved", "missing_rim", flags=("missing_required_rim",))


def config(mode="placeholder", kind="depth", value=123.0):
    """Return one explicit synthetic missing-rim policy."""
    policy = {"mode": mode}
    if mode == "placeholder":
        policy.update(placeholder={"kind": kind, "value": value, "unit": "ft"},
                      basis="Visible synthetic model-development placeholder")
        if kind == "elevation":
            policy["placeholder"]["vertical_datum"] = "TEST_DATUM"
    return {"parameters": {"nodes": {"missing_rim": policy}},
            "elevations": {"target_datum": "TEST_DATUM"}}


def resolve(node=None, floor=FLOOR, rim=MISSING, policy=None, existing=False):
    """Resolve a synthetic junction with no rim by default."""
    return resolve_missing_rim(node or {"key": "synthetic/node", "asset_id": "N1", "rim": None},
        floor, rim, policy if policy is not None else config(), is_existing=existing)


def test_depth_placeholder_hand_calculation_and_immutable_evidence():
    policy = config()
    original = deepcopy(policy)
    result = resolve(policy=policy)
    assert result.value == pytest.approx(100 + 123 * 0.3048)
    assert result.category == "assumed"
    assert result.method == "missing_rim_placeholder"
    assert "placeholder_rim" in result.flags
    assert result.evidence[0]["floor_assignment"] == FLOOR.to_dict()
    assert result.evidence[0]["is_placeholder"] is True
    result.evidence[0]["policy"]["basis"] = "modified result"
    assert policy == original


@pytest.mark.parametrize("unit", ["ft", "m"])
def test_model_units_give_literal_native_depth_for_us_and_si_models(unit):
    policy = config(value=12.345)
    policy["parameters"]["nodes"]["missing_rim"]["placeholder"]["unit"] = "model"
    policy["model"] = {"target": {"length_unit": unit}}
    factor = 0.3048 if unit == "ft" else 1.0
    result = resolve(policy=policy)
    assert result.value - FLOOR.value == pytest.approx(12.345 * factor)
    assert result.evidence[0]["resolved_length_unit"] == unit
    assert result.evidence[0]["placeholder_depth_m"] == pytest.approx(12.345 * factor)


def test_absolute_elevation_is_rejected_instead_of_converting_native_depth():
    with pytest.raises(ValueError, match="no native absolute-rim field"):
        resolve(policy=config(kind="elevation"))


def test_depth_preserves_provisional_floor_qualification():
    floor = Assignment(100, "provisional", "test", flags=("provisional_vertical_basis",))
    assert "provisional_vertical_basis" in resolve(floor=floor).flags


@pytest.mark.parametrize("mode", ["hold", "error", "placeholder"])
def test_existing_structures_are_never_changed_by_missing_rim_policy(mode):
    assert resolve(policy=config(mode), existing=True) is MISSING


def test_default_hold_and_existing_measured_rim_remain_unchanged():
    assert resolve(policy={}) is MISSING
    observed = Assignment(130.0, "observed", "structure_rim")
    assert resolve(rim=observed) is observed


def test_missing_floor_is_not_repaired_but_error_mode_still_except_missing_rim():
    unknown_floor = Assignment(None, "unresolved", "missing_floor")
    assert resolve(floor=unknown_floor) is MISSING
    with pytest.raises(ValueError, match="Missing rim elevation"):
        resolve(floor=unknown_floor, policy=config("error"))


@pytest.mark.parametrize("raw", [0, False, "bad", -9999, float("nan"), "123"])
def test_nonmissing_source_observations_cannot_be_replaced(raw):
    node = {"rim": None, "measurement_provenance": {"rim": {"raw_value": raw}}}
    assert resolve(node=node) is MISSING
    assert resolve(node=node, policy=config("error")) is MISSING


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_actually_missing_provenance_allows_placeholder(raw):
    node = {"rim": None, "measurement_provenance": {"rim": {"raw_value": raw}}}
    assert resolve(node=node).method == "missing_rim_placeholder"


@pytest.mark.parametrize("node", [
    {"rim": None, "_reviewed_override_holds": ["rim"]},
    {"rim": None, "_reviewed_overrides": {"rim": {"value_m": 150}}},
    {"rim": 150},
])
def test_reviewed_or_present_rim_cannot_be_bypassed(node):
    assert resolve(node=node) is MISSING


@pytest.mark.parametrize("method", ["stale_override_hold", "vertical_basis_unresolved", "provisional_transform_disallowed"])
def test_unresolved_nonmissing_rim_holds_cannot_be_bypassed(method):
    unresolved = Assignment(None, "unresolved", method)
    assert resolve(rim=unresolved) is unresolved


def test_configuration_accepts_explicit_depth_and_missing_actions():
    for policy in (config(), config("hold"), config("error")):
        base = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").data
        base["parameters"]["nodes"]["missing_rim"] = policy["parameters"]["nodes"]["missing_rim"]
        base["elevations"]["target_datum"] = "TEST_DATUM"
        validate_config(base)


@pytest.mark.parametrize("change", [
    {"mode": "guess"}, {"mode": "hold", "basis": "unused"},
    {"mode": "placeholder"},
    {"mode": "placeholder", "basis": "", "placeholder": {"kind": "depth", "value": 123, "unit": "ft"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "depth", "value": 0, "unit": "ft"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "depth", "value": -1, "unit": "ft"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "depth", "value": True, "unit": "ft"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "depth", "value": float("inf"), "unit": "ft"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "depth", "value": 123, "unit": "feet"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "depth", "value": 123, "unit": "ft", "vertical_datum": "TEST_DATUM"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "elevation", "value": 123, "unit": "ft"}},
    {"mode": "placeholder", "basis": "test", "placeholder": {"kind": "elevation", "value": 123, "unit": "ft", "vertical_datum": "WRONG_DATUM"}},
])
def test_configuration_rejects_ambiguous_or_invalid_placeholder_policies(change):
    base = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").data
    base["parameters"]["nodes"]["missing_rim"] = change
    with pytest.raises(ConfigurationError):
        validate_config(base)


@pytest.mark.parametrize("unit", ["ft", "m"])
def test_configuration_accepts_model_unit_depth_on_declared_target(unit):
    base = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").data
    policy = config(value=12.345)["parameters"]["nodes"]["missing_rim"]
    policy["placeholder"]["unit"] = "model"
    base["parameters"]["nodes"]["missing_rim"] = policy
    base["model"]["target"]["length_unit"] = unit
    base["model"]["target"]["flow_units"] = "CFS" if unit == "ft" else "CMS"
    validate_config(base)


def test_configuration_rejects_model_unit_without_declared_target_length():
    base = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").data
    policy = config()["parameters"]["nodes"]["missing_rim"]
    policy["placeholder"]["unit"] = "model"
    base["parameters"]["nodes"]["missing_rim"] = policy
    base["model"]["target"].pop("length_unit", None)
    with pytest.raises(ConfigurationError):
        validate_config(base)


def test_canonical_blank_rim_is_missing():
    assert resolve(node={"rim": " "}).method == "missing_rim_placeholder"


def test_extreme_finite_depth_cannot_overflow_rim_elevation():
    floor = Assignment(1e308, "observed", "structure_floor")
    with pytest.raises(ValueError, match="nonfinite"):
        resolve(floor=floor, policy={"parameters": {"nodes": {"missing_rim": {
            "mode": "placeholder", "basis": "overflow regression", "placeholder": {
                "kind": "depth", "value": 1e308, "unit": "m"}}}}})


def crown_findings(placeholder=False, *, assignment_marker=False, hold_placeholder=False):
    """Evaluate one crown crossing a synthetic rim under explicit source QA holds."""
    from umdev.qa import profile_findings

    node = {"key": "synthetic/node", "asset_id": "N1", "floor_m": 100,
            "rim_m": 101, "max_depth_m": 1}
    if placeholder:
        if assignment_marker:
            node["assignments"] = {"rim": {"method": "missing_rim_placeholder"}}
        else:
            node["has_placeholder_rim"] = True
    pipe = {"key": "synthetic/pipe", "asset_id": "P1", "from_node": "N1",
            "to_node": "N2", "upstream_invert_m": 100.5, "downstream_invert_m": 100,
            "length_m": 10, "diameter_m": 1, "pipe_kind": "gravity"}
    rules = {"pipe_crown_above_rim": {"severity": "error", "disposition": "hold"}}
    if hold_placeholder:
        rules["pipe_crown_above_placeholder_rim"] = {"severity": "error", "disposition": "hold"}
    return profile_findings([node], [pipe], {"qa": {"rules": rules}})


def test_crown_above_real_rim_keeps_source_error_hold():
    rows = crown_findings()
    assert len(rows) == 1
    assert rows[0]["rule"] == "pipe_crown_above_rim"
    assert rows[0]["severity"] == "error"
    assert rows[0]["disposition"] == "hold"
    assert "rim_basis" not in rows[0]


@pytest.mark.parametrize("assignment_marker", [True, False])
def test_crown_above_placeholder_retains_residual_without_inheriting_source_hold(assignment_marker):
    rows = crown_findings(True, assignment_marker=assignment_marker)
    assert len(rows) == 1
    row = rows[0]
    assert row["rule"] == "pipe_crown_above_placeholder_rim"
    assert row["severity"] == "warning"
    assert row["disposition"] == "review"
    assert row["rim_basis"] == "placeholder"
    assert row["object_key"] == "synthetic/pipe"
    assert row["endpoint"] == "upstream"
    assert row["node_id"] == "N1"
    assert row["residual_m"] == pytest.approx(0.5)


def test_placeholder_crown_rule_can_be_explicitly_held():
    rows = crown_findings(True, hold_placeholder=True)
    assert rows[0]["rule"] == "pipe_crown_above_placeholder_rim"
    assert rows[0]["severity"] == "error"
    assert rows[0]["disposition"] == "hold"
