"""Verify opt-in all-small peripheral scope decisions and hard protections.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Synthetic SI graphs only; omission approval and real load allocation are separate.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

import pytest

from umdev.scope import plan_small_runs
from test_scope import edge


def rule(**updates: Any) -> dict[str, Any]:
    """Supply explicit fictional small-pipe thresholds in SI metres."""
    return {"enabled": True, "maximum_diameter_m": .2032,
            "diameter_tolerance_m": 1e-6, **updates}


def branch() -> dict[str, dict[str, Any]]:
    """Create a long branched small-diameter tree draining to one core node."""
    return {"CORE": edge("A", "Z"), "B": edge("P", "A", 300., .2032),
            "C": edge("Q", "P", 250., .2032), "D": edge("R", "P", 250., .15)}


def plan(links: dict[str, dict[str, Any]], *, option: Any = None, omitted: bool = False,
         locked: tuple[str, ...] = (), loaded: tuple[str, ...] = (),
         protected: tuple[str, ...] = ("CORE",), maximum_diameter: float = .31,
         maximum_count: int = 1, maximum_length: float = 100.,
         isolated: bool = False, short_seed: bool = False) -> dict[str, Any]:
    """Invoke the public planner with explicit graph roles and scope policy."""
    nodes = {n: {} for r in links.values() for n in (r["from_node"], r["to_node"])}
    policy = {"maximum_group_length_m": maximum_length,
        "maximum_group_pipe_count": maximum_count, "maximum_minor_diameter_m": maximum_diameter,
        "dimensional_tolerance_m": 1e-6, "allow_isolated_component_omission": isolated}
    if not omitted: policy["all_small_peripheral_branches"] = rule() if option is None else option
    if short_seed:
        policy["short_seed_connector_branches"] = {"enabled": True,
            "seed_diameter_threshold_m": .2032, "minimum_seed_run_length_m": 152.4,
            "diameter_tolerance_m": 1e-6, "length_tolerance_m": 1e-6}
    return plan_small_runs(nodes, links, candidate_link_ids=set(links),
        protected_link_ids=protected, protected_node_ids=locked, loaded_node_ids=loaded,
        terminal_node_ids=["Z"], policy=policy)


def receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Return the assessment for the one complete peripheral group."""
    assert len(result["groups"]) == 1
    return result["groups"][0]["all_small_peripheral_review"]


def test_omitted_and_disabled_leave_legacy_recommendations_unchanged() -> None:
    """No implicit diameter choice or receipt is introduced to existing calls."""
    absent = plan(branch(), omitted=True)
    disabled = plan(branch(), option={"enabled": False})
    assert absent["recommended_omit_link_ids"] == disabled["recommended_omit_link_ids"] == []
    assert absent["groups"][0]["hold_reasons"] == disabled["groups"][0]["hold_reasons"]
    assert "all_small_peripheral_review" not in absent["groups"][0]
    assert "all_small_peripheral_review" not in disabled["groups"][0]


def test_whole_branch_size_holds_only_are_waived_with_all_actual_routes() -> None:
    """Each confluence tributary drains to the attachment; no sewage is inferred."""
    links = branch(); before = deepcopy(links)
    result = plan(links); review = receipt(result)
    assert result["recommended_omit_link_ids"] == ["B", "C", "D"]
    assert review["original_hold_reasons"] == review["overridden_hold_reasons"] == [
        "whole_group_pipe_count_exceeds_scope_policy", "whole_group_length_exceeds_scope_policy"]
    assert review["internal_node_directed_attachment_routes"] == {
        "P": ["B"], "Q": ["C", "B"], "R": ["D", "B"]}
    assert all(review["protected_guards"].values())
    assert review["eligible"] and review["size_override_applied"]
    assert review["final_hold_reasons"] == [] and review["joint_retained_network_guard_passed"]
    assert result["joint_retained_network_guard"]["remaining_link_count"] == 1 and links == before


@pytest.mark.parametrize("diameter, qualifies", [(.2032, True), (.2032005, True),
    (.203201, True), (.203202, False), (.254, False)])
def test_diameter_boundary_uses_explicit_tolerance(diameter: float, qualifies: bool) -> None:
    """Maximum diameter plus tolerance is inclusive; larger assets remain held."""
    links = branch(); links["B"]["diameter_m"] = diameter
    result = plan(links); review = receipt(result)
    assert review["eligible"] is qualifies
    assert bool(result["recommended_omit_link_ids"]) is qualifies
    if not qualifies: assert not review["overridden_hold_reasons"]


def test_configured_diameter_change_changes_scope_not_source_graph_fingerprint() -> None:
    """Project thresholds are explicit configuration, never embedded defaults."""
    links = branch(); links["B"]["diameter_m"] = .254
    low, high = plan(links), plan(links, option=rule(maximum_diameter_m=.26))
    assert not low["recommended_omit_link_ids"] and high["recommended_omit_link_ids"]
    a, b = receipt(low), receipt(high)
    assert a["source_graph_fingerprint"] == b["source_graph_fingerprint"]
    assert a["rule_configuration_fingerprint"] != b["rule_configuration_fingerprint"]


@pytest.mark.parametrize("reversed_link", ["B", "C", "D"])
def test_reversed_gravity_direction_cannot_receive_size_waiver(reversed_link: str) -> None:
    """A visually connected tree still needs real directed routes for every node."""
    links = branch(); row = links[reversed_link]
    row["from_node"], row["to_node"] = row["to_node"], row["from_node"]
    result = plan(links); review = receipt(result)
    assert not review["eligible"] and not result["recommended_omit_link_ids"]
    assert not review["protected_guards"]["every_internal_node_has_directed_attachment_route"]


@pytest.mark.parametrize("topology", ["parallel", "cycle", "reconvergence", "two_attachments", "island"])
def test_nonperipheral_or_ambiguous_groups_do_not_receive_size_waiver(topology: str) -> None:
    """Loops, reconvergence, alternate corridors and islands are separate review."""
    links = {"CORE": edge("A", "Z"), "B": edge("P", "A", 300.)}
    protected = ("CORE",)
    if topology == "parallel": links["C"] = edge("P", "A", 300.)
    elif topology == "cycle": links.update(C=edge("P", "Q", 300.), D=edge("Q", "A", 300.))
    elif topology == "reconvergence":
        links.update(C=edge("R", "P", 300.), D=edge("R", "Q", 300.), E=edge("Q", "A", 300.))
    elif topology == "two_attachments":
        links.update(C=edge("U", "P", 300.), ORIGINAL=edge("U", "A")); protected += ("ORIGINAL",)
    else: links = {"CORE": edge("A", "Z"), "B": edge("U", "P", 300.), "C": edge("P", "V", 300.)}
    result = plan(links, protected=protected, isolated=True)
    assert not result["recommended_omit_link_ids"]
    assert all(not g["all_small_peripheral_review"]["size_override_applied"] for g in result["groups"])


@pytest.mark.parametrize("protection", ["load", "station", "baseline", "required", "pressure", "control"])
def test_native_role_and_existing_asset_protections_remain_binding(protection: str) -> None:
    """Caller protections are never waived to obtain a whole small-branch result."""
    links = {"CORE": edge("A", "Z"), "B": edge("P", "A", 300.), "C": edge("Q", "P", 300.)}
    options: dict[str, Any] = {}
    if protection == "load": options["loaded"] = ("Q",)
    elif protection in {"station", "control"}: options["locked"] = ("Q",)
    elif protection == "baseline": options["protected"] = ("CORE", "C")
    elif protection == "required": options["protected"] = ("CORE", "B")
    else: links["C"]["kind"] = "force_main"
    result = plan(links, **options)
    assert result["joint_retained_network_guard"]["passed"]
    if protection == "required":
        # A protected downstream link is an intentional retained boundary;
        # its otherwise unprotected tributary can be a different review group.
        assert "B" not in result["recommended_omit_link_ids"]
        assert result["recommended_omit_link_ids"] == ["C"]
    else:
        assert not result["recommended_omit_link_ids"]
        assert all(not g["all_small_peripheral_review"]["size_override_applied"] for g in result["groups"])


def test_ordinary_diameter_hold_cannot_be_waived_by_larger_optional_threshold() -> None:
    """The optional rule narrows scope; the ordinary diameter bound stays hard."""
    links = branch(); links["B"]["diameter_m"] = .254
    result = plan(links, maximum_diameter=.21, option=rule(maximum_diameter_m=.3))
    review = receipt(result)
    assert not result["recommended_omit_link_ids"]
    assert review["original_hold_reasons"] == result["groups"][0]["hold_reasons"]
    assert "diameter_exceeds_minor_scope_policy" in review["non_size_hold_reasons"]


def test_ordinary_small_branch_can_qualify_without_a_size_override() -> None:
    """An ordinary recommendation is distinguished from an exception receipt."""
    result = plan(branch(), maximum_count=10, maximum_length=1000.)
    review = receipt(result)
    assert review["eligible"] and not review["size_override_applied"]
    assert not review["original_hold_reasons"] and result["recommended_omit_link_ids"]


def test_both_optional_rules_are_independent_and_keep_original_hold_receipts() -> None:
    """A no-seed tree is eligible only through the all-small policy."""
    result = plan(branch(), short_seed=True); group = result["groups"][0]
    small = group["all_small_peripheral_review"]; seed = group["short_seed_connector_review"]
    assert result["recommended_omit_link_ids"] and small["eligible"] and not seed["eligible"]
    assert small["original_hold_reasons"] == seed["original_hold_reasons"]
    assert not seed["overridden_hold_reasons"] and seed["final_recommended_action"] == "recommend_omit"


@pytest.mark.parametrize("option", [True, False, {}, {"enabled": 1}, {"enabled": "true"},
    {"enabled": True}, {"enabled": False, "unknown": 1},
    {"enabled": False, "maximum_diameter_m": float("nan")}, rule(maximum_diameter_m=0.),
    rule(maximum_diameter_m=True), rule(maximum_diameter_m=-1.), rule(diameter_tolerance_m=-1.),
    rule(diameter_tolerance_m=float("inf")), rule(diameter_tolerance_m=.2032)])
def test_malformed_enabled_and_disabled_payloads_are_rejected(option: Any) -> None:
    """Invalid flags, incomplete thresholds and overlarge tolerances fail closed."""
    with pytest.raises(ValueError): plan(branch(), option=option)


def test_explicit_null_rule_is_rejected() -> None:
    """Null cannot silently mean disabled when the nested option is present."""
    links = branch(); nodes = {n: {} for r in links.values() for n in (r["from_node"], r["to_node"])}
    policy = {"maximum_group_length_m": 100., "maximum_group_pipe_count": 1,
        "maximum_minor_diameter_m": .31, "dimensional_tolerance_m": 1e-6,
        "allow_isolated_component_omission": False, "all_small_peripheral_branches": None}
    with pytest.raises(ValueError):
        plan_small_runs(nodes, links, candidate_link_ids=set(links), protected_link_ids=["CORE"],
            protected_node_ids=[], loaded_node_ids=[], terminal_node_ids=["Z"], policy=policy)


def test_graph_source_and_final_proposal_fingerprints_are_bound_and_deterministic() -> None:
    """Changes to source provenance alter approvals; mapping order does not."""
    links = branch(); links["B"]["source_sha256"] = "a" * 64
    result = plan(links)
    assert result == plan(dict(reversed(list(links.items()))))
    links["B"]["source_sha256"] = "b" * 64
    changed = plan(links)
    assert receipt(result)["source_graph_fingerprint"] != receipt(changed)["source_graph_fingerprint"]
    assert result["input_fingerprint"] != changed["input_fingerprint"]
    for value in (result, changed):
        group = value["groups"][0]; record = {k: v for k, v in group.items() if k != "proposal_id"}
        expected = sha256(json.dumps({"input": value["input_fingerprint"], "group": record},
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:24]
        assert group["proposal_id"] == "SCOPE-" + expected
        json.dumps(value, allow_nan=False)
