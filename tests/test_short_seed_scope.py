"""Verify opt-in connector-branch scope decisions and continuous seed reaches.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Synthetic SI graphs only; recommendations do not retire assets or allocate loads.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

import pytest

from umdev.scope import plan_small_runs
from test_scope import edge


def rule(**updates: Any) -> dict[str, Any]:
    """Supply explicit fictional thresholds, with no public utility defaults."""
    return {"enabled": True, "seed_diameter_threshold_m": .2032,
        "minimum_seed_run_length_m": 152.4, "diameter_tolerance_m": 1e-6,
        "length_tolerance_m": 1e-6, **updates}


def branch() -> dict[str, dict[str, Any]]:
    """Create one continuous10-to12inch seed reach above two small connectors."""
    return {"CORE": edge("A", "Z"), "S1": edge("U", "M", 60., .254),
            "S2": edge("M", "N", 40., .3048),
            "C1": edge("N", "P", 200., .2032), "C2": edge("P", "A", 200., .2032)}


def plan(links: dict[str, dict[str, Any]], *, option: Any = None, omit_option: bool = False,
         max_length: float = 100., max_count: int = 2, max_diameter: float = .31,
         protected: tuple[str, ...] = ("CORE",), locked: tuple[str, ...] = (),
         loaded: tuple[str, ...] = (), allow_isolated: bool = False) -> dict[str, Any]:
    """Invoke the real complete-group planner and its ordinary protection sets."""
    nodes = {node: {} for row in links.values() for node in (row["from_node"], row["to_node"])}
    policy = {"maximum_group_length_m": max_length, "maximum_group_pipe_count": max_count,
        "maximum_minor_diameter_m": max_diameter, "dimensional_tolerance_m": 1e-6,
        "allow_isolated_component_omission": allow_isolated}
    if not omit_option: policy["short_seed_connector_branches"] = rule() if option is None else option
    return plan_small_runs(nodes, links, candidate_link_ids=set(links), protected_link_ids=protected,
        protected_node_ids=locked, loaded_node_ids=loaded, terminal_node_ids=["Z"], policy=policy)


def receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Return the one complete peripheral-tree assessment."""
    assert len(result["groups"]) == 1
    return result["groups"][0]["short_seed_connector_review"]


def test_default_absent_and_explicit_disabled_keep_legacy_size_holds() -> None:
    """An opt-in rule never changes omission recommendations by default."""
    absent = plan(branch(), omit_option=True)
    disabled = plan(branch(), option={"enabled": False})
    assert absent["recommended_omit_link_ids"] == disabled["recommended_omit_link_ids"] == []
    assert absent["groups"][0]["hold_reasons"] == disabled["groups"][0]["hold_reasons"] == [
        "whole_group_pipe_count_exceeds_scope_policy", "whole_group_length_exceeds_scope_policy"]
    assert "short_seed_connector_review" not in absent["groups"][0]
    assert "short_seed_connector_review" not in disabled["groups"][0]


def test_only_group_size_holds_are_waived_with_exact_evidence() -> None:
    """Long connectors can exceed ordinary group limits without becoming a trunk."""
    links = branch(); before = deepcopy(links)
    result = plan(links); review = receipt(result)
    assert result["recommended_omit_link_ids"] == ["C1", "C2", "S1", "S2"]
    assert review["eligible"] and review["size_override_applied"]
    assert review["original_hold_reasons"] == review["overridden_hold_reasons"] == [
        "whole_group_pipe_count_exceeds_scope_policy", "whole_group_length_exceeds_scope_policy"]
    assert review["final_hold_reasons"] == result["groups"][0]["hold_reasons"] == []
    assert review["joint_retained_network_guard_passed"] and all(review["protected_guards"].values())
    assert review["seed_runs"][0]["link_ids"] == ["S1", "S2"]
    assert review["seed_runs"][0]["total_length_m"] == 100.
    assert review["seed_runs"][0]["downstream_small_connector_link_ids"] == ["C1", "C2"]
    assert review["input_fingerprint"] == result["input_fingerprint"] and links == before


@pytest.mark.parametrize("length, expected", [(152.4, False), (499.999 * .3048, True),
                                              (499.999999 * .3048, False), (153., False)])
def test_minimum_reach_length_and_explicit_tolerance_boundary(length: float, expected: bool) -> None:
    """Equality is not short; an explicit tolerance band is not rounded away."""
    links = {"CORE": edge("A", "Z"), "SEED": edge("U", "N", length, .254),
             "CONNECTOR": edge("N", "A", 300., .2032)}
    result = plan(links); review = receipt(result)
    assert review["seed_runs"][0]["below_minimum_length"] is expected
    assert bool(result["recommended_omit_link_ids"]) is expected


@pytest.mark.parametrize("diameter, is_seed", [(.2032, False), (.2032005, False), (.203201, False), (.203202, True)])
def test_diameter_threshold_is_strict_with_a_declared_tolerance(diameter: float, is_seed: bool) -> None:
    """Exactly-threshold and tolerance-band pipes remain small connectors."""
    links = {"CORE": edge("A", "Z"), "SEED": edge("U", "N", 50., diameter),
             "CONNECTOR": edge("N", "A", 300., .2032)}
    result = plan(links); review = receipt(result)
    assert bool(review["seed_link_ids"]) is is_seed
    assert bool(result["recommended_omit_link_ids"]) is is_seed


def test_diameter_change_does_not_split_a_continuous_larger_reach() -> None:
    """Both10inch and12inch seeds contribute to the same100m reach."""
    result = plan(branch(), option=rule(minimum_seed_run_length_m=90.))
    review = receipt(result)
    assert review["seed_runs"][0]["link_ids"] == ["S1", "S2"]
    assert review["seed_runs"][0]["total_length_m"] == 100.
    assert not result["recommended_omit_link_ids"] and not review["eligible"]


def test_small_tributary_does_not_split_a_long_continuous_seed_main() -> None:
    """An8inch lateral cannot turn a180m main into two short90m reaches."""
    links = branch(); links["S1"]["length_m"] = links["S2"]["length_m"] = 90.
    links["LATERAL"] = edge("T", "M", 10., .2032)
    result = plan(links); review = receipt(result)
    assert len(review["seed_runs"]) == 1 and review["seed_runs"][0]["link_ids"] == ["S1", "S2"]
    assert review["seed_runs"][0]["total_length_m"] == 180.
    assert not result["recommended_omit_link_ids"]


def test_large_reaches_separated_by_small_pipe_are_not_summed() -> None:
    """Each70m seed run stays distinct, even though their combined length is140m."""
    links = {"CORE": edge("A", "Z"), "S1": edge("U", "V", 70., .254),
             "GAP": edge("V", "W", 80., .2032), "S2": edge("W", "N", 70., .254),
             "TAIL": edge("N", "A", 300., .2032)}
    result = plan(links, option=rule(minimum_seed_run_length_m=100.))
    review = receipt(result)
    assert sorted(run["total_length_m"] for run in review["seed_runs"]) == [70., 70.]
    assert result["recommended_omit_link_ids"] == ["GAP", "S1", "S2", "TAIL"]


def test_seed_confluence_keeps_three_reaches_instead_of_summing_tributaries() -> None:
    """Two incoming seed branches and one outgoing seed create three reaches."""
    links = {"CORE": edge("A", "Z"), "LEFT": edge("U", "J", 60., .254),
             "RIGHT": edge("V", "J", 60., .254), "DOWN": edge("J", "N", 60., .254),
             "TAIL": edge("N", "A", 300., .2032)}
    result = plan(links, option=rule(minimum_seed_run_length_m=100.)); review = receipt(result)
    assert len(review["seed_runs"]) == 3
    assert all(run["total_length_m"] == 60. for run in review["seed_runs"])
    assert Counter(key for run in review["seed_runs"] for key in run["link_ids"]) == Counter({"LEFT": 1, "RIGHT": 1, "DOWN": 1})
    assert review["eligible"] and len(result["recommended_omit_link_ids"]) == 4


def test_seed_divergence_breaks_reaches_and_away_branch_cannot_route_to_attachment() -> None:
    """Declared direction is respected rather than reversing one seed branch."""
    links = {"CORE": edge("A", "Z"), "UP": edge("U", "J", 60., .254),
             "TOWARD": edge("J", "N", 60., .254), "AWAY": edge("J", "V", 60., .254),
             "TAIL": edge("N", "A", 300., .2032)}
    result = plan(links); review = receipt(result)
    assert len(review["seed_runs"]) == 3 and not review["eligible"]
    assert not result["recommended_omit_link_ids"]
    away = next(run for run in review["seed_runs"] if run["link_ids"] == ["AWAY"])
    assert away["directed_attachment_route_link_ids"] is None


def test_direct_large_pipe_to_attachment_is_not_eligible_despite_small_upstream_pipe() -> None:
    """A connector elsewhere in the tree does not meet the downstream condition."""
    links = {"CORE": edge("A", "Z"), "SMALL": edge("U", "N", 300., .2032),
             "SEED": edge("N", "A", 50., .254)}
    result = plan(links); review = receipt(result)
    assert review["protected_guards"]["has_small_connectors"]
    assert review["seed_runs"][0]["directed_attachment_route_link_ids"] == []
    assert not review["eligible"] and not result["recommended_omit_link_ids"]


@pytest.mark.parametrize("case", ["parallel", "cycle", "reconvergence"])
def test_loops_parallel_seeds_and_reconvergence_cannot_receive_tree_exception(case: str) -> None:
    """The size waiver never promotes cyclic or multi-path network groups."""
    links = {"CORE": edge("A", "Z"), "TAIL": edge("N", "A", 300., .2032)}
    if case == "parallel": links.update(S1=edge("U", "N", 50., .254), S2=edge("U", "N", 50., .254))
    elif case == "cycle": links.update(S1=edge("U", "N", 50., .254), S2=edge("N", "U", 50., .254))
    else: links.update(S1=edge("U", "P", 50., .254), S2=edge("P", "N", 50., .254),
                       S3=edge("U", "Q", 50., .254), S4=edge("Q", "N", 50., .254))
    result = plan(links, max_count=1)
    assert not result["recommended_omit_link_ids"]
    reviews = [group["short_seed_connector_review"] for group in result["groups"]]
    assert all(not row["size_override_applied"] for row in reviews)
    assert all(not row["protected_guards"]["whole_peripheral_tree"] for row in reviews)
    for row in reviews:
        assert Counter(key for run in row["seed_runs"] for key in run["link_ids"]) == Counter({key: 1 for key in row["seed_link_ids"]})


def test_two_attachment_alternate_corridor_cannot_receive_exception() -> None:
    """Even a safe alternate route is outside the whole peripheral-tree rule."""
    links = {"CORE": edge("A", "Z"), "OLD": edge("U", "A"),
             "SEED": edge("U", "N", 50., .254), "TAIL": edge("N", "A", 300., .2032)}
    result = plan(links, protected=("CORE", "OLD")); review = receipt(result)
    assert result["groups"][0]["alternate_directed_link_path"] == ["OLD"]
    assert not review["protected_guards"]["single_retained_attachment"]
    assert not review["size_override_applied"] and not result["recommended_omit_link_ids"]


def test_zero_attachment_component_cannot_receive_exception() -> None:
    """An unrelated island is not a small connector branch of the retained network."""
    links = {"CORE": edge("A", "Z"), "SEED": edge("U", "N", 50., .254),
             "TAIL": edge("N", "V", 300., .2032)}
    result = plan(links, allow_isolated=True); review = receipt(result)
    assert review["retained_attachment_node_id"] is None
    assert not review["eligible"] and not result["recommended_omit_link_ids"]


@pytest.mark.parametrize("protection", ["loaded", "station", "existing_link", "required_connection", "pressure"])
def test_option_cannot_bypass_load_station_existing_or_pressure_protections(protection: str) -> None:
    """The caller's full-graph role protections remain mandatory."""
    links = branch(); options: dict[str, Any] = {"max_length": 1., "max_count": 1}
    if protection == "loaded": options["loaded"] = ("U",)
    elif protection == "station": options["locked"] = ("M",)
    elif protection == "existing_link": options["protected"] = ("CORE", "S1")
    elif protection == "required_connection": options["protected"] = ("CORE", "C1")
    else: links["C1"]["kind"] = "force_main"
    result = plan(links, **options)
    assert not result["recommended_omit_link_ids"]
    assert result["joint_retained_network_guard"]["passed"]
    assert all(not group["short_seed_connector_review"]["size_override_applied"] for group in result["groups"])


def test_diameter_hold_remains_and_no_prior_hold_is_silently_erased() -> None:
    """The optional rule can waive count/length only when all hard guards pass."""
    links = branch(); links["S2"]["diameter_m"] = .4
    result = plan(links); review = receipt(result)
    assert "diameter_exceeds_minor_scope_policy" in result["groups"][0]["hold_reasons"]
    assert result["groups"][0]["hold_reasons"] == review["original_hold_reasons"]
    assert review["overridden_hold_reasons"] == [] and not review["eligible"]


def test_ordinary_small_group_needs_no_size_override_even_when_rule_is_eligible() -> None:
    """A normal recommendation is distinguished from the new exception path."""
    result = plan(branch(), max_length=1000., max_count=10); review = receipt(result)
    assert result["recommended_omit_link_ids"] and review["eligible"]
    assert not review["size_override_applied"] and not review["original_hold_reasons"]


@pytest.mark.parametrize("option", [None, True, False, {}, {"enabled": 1}, {"enabled": "true"},
    {"enabled": True}, {"enabled": False, "unknown": 1}, {"enabled": False, "length_tolerance_m": float("nan")},
    rule(seed_diameter_threshold_m=0.), rule(seed_diameter_threshold_m=True), rule(minimum_seed_run_length_m=-1.),
    rule(length_tolerance_m=-1.), rule(diameter_tolerance_m=float("inf")),
    rule(length_tolerance_m=152.4), rule(diameter_tolerance_m=.2032)])
def test_malformed_flags_and_enabled_or_disabled_thresholds_are_rejected(option: Any) -> None:
    """A typo or invalid disabled payload cannot silently create a policy."""
    if option is None:
        nodes = {node: {} for row in branch().values() for node in (row["from_node"], row["to_node"])}
        policy = {"maximum_group_length_m": 100., "maximum_group_pipe_count": 2,
            "maximum_minor_diameter_m": .31, "dimensional_tolerance_m": 1e-6,
            "allow_isolated_component_omission": False, "short_seed_connector_branches": None}
        with pytest.raises(ValueError):
            plan_small_runs(nodes, branch(), candidate_link_ids=set(branch()), protected_link_ids=["CORE"],
                protected_node_ids=[], loaded_node_ids=[], terminal_node_ids=["Z"], policy=policy)
    else:
        with pytest.raises(ValueError): plan(branch(), option=option)


def test_config_source_and_final_proposal_fingerprints_are_bound() -> None:
    """Review receipts retain exact graph/config provenance and final decisions."""
    links = branch(); links["S1"]["source_record_sha256"] = "a" * 64
    original = plan(links); changed_policy = plan(links, option=rule(minimum_seed_run_length_m=160.))
    links["S1"]["source_record_sha256"] = "b" * 64
    changed_source = plan(links)
    a, b, c = receipt(original), receipt(changed_policy), receipt(changed_source)
    assert a["rule_configuration_fingerprint"] != b["rule_configuration_fingerprint"]
    assert a["source_graph_fingerprint"] == b["source_graph_fingerprint"] != c["source_graph_fingerprint"]
    assert len({result["input_fingerprint"] for result in (original, changed_policy, changed_source)}) == 3
    for result in (original, changed_policy, changed_source):
        group = result["groups"][0]; record = {key: value for key, value in group.items() if key != "proposal_id"}
        digest = sha256(json.dumps({"input": result["input_fingerprint"], "group": record}, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:24]
        assert group["proposal_id"] == "SCOPE-" + digest
        json.dumps(result, allow_nan=False)


def test_mapping_order_does_not_change_seed_paths_or_receipts() -> None:
    """Deterministic graph ordering is part of reproducible review identities."""
    links = branch()
    assert plan(links) == plan(dict(reversed(list(links.items()))))
