"""Synthetic numerical and protection checks for finished-network preparation.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained tests with hand-calculated profiles and private-data-free IDs.
"""

from copy import deepcopy

import pytest

from umdev.network_methods import infer_missing_inverts, simplify_short_pipes


def network():
    """Build a serial synthetic gravity reach in one consistent length unit."""
    nodes = {"A": {"invert": 100.0, "kind": "junction"},
             "B": {"invert": None, "kind": "junction"},
             "C": {"invert": 96.0, "kind": "junction"}}
    links = {"P": {"from_node": "A", "to_node": "B", "kind": "gravity", "length": 10.0,
                   "diameter": 1.0, "material": "synthetic", "inlet_offset": 0.0, "outlet_offset": 0.0},
             "Q": {"from_node": "B", "to_node": "C", "kind": "gravity", "length": 30.0,
                   "diameter": 1.0, "material": "synthetic", "inlet_offset": 0.0, "outlet_offset": 0.0}}
    return nodes, links


def complete_network():
    nodes, links = network()
    nodes["B"]["invert"] = 99.0
    return nodes, links


def test_interpolation_hand_calculation_and_no_mutation():
    nodes, links = network()
    before = deepcopy((nodes, links))
    result = infer_missing_inverts(nodes, links, length_unit="ft")
    assert result["nodes"]["B"]["invert"] == pytest.approx(99.0)
    row = result["assignments"][0]
    assert row["method"] == "length_weighted_interpolation"
    assert row["upstream"]["node_id"] == "A"
    assert row["upstream"]["path"] == ["P"]
    assert row["downstream"]["distance"] == 30.0
    assert row["length_unit"] == "ft"
    assert result["preservation"]["known_values_preserved"]
    assert result["preservation"]["known_node_inverts"] == {"A": 100.0, "C": 96.0}
    assert not result["unresolved"]
    assert (nodes, links) == before


def test_interpolation_anchor_offsets_are_absolute_pipe_end_elevations():
    nodes, links = network()
    links["P"]["inlet_offset"] = 2.0
    links["Q"]["outlet_offset"] = 1.0
    result = infer_missing_inverts(nodes, links)
    assert result["nodes"]["B"]["invert"] == pytest.approx(100.75)
    assert result["nodes"]["A"]["invert"] == 100.0
    assert result["assignments"][0]["upstream"]["anchor_offset"] == 2.0


def test_multiple_unknown_nodes_use_original_anchors_and_cumulative_lengths():
    nodes, links = network()
    nodes["D"] = {"invert": None}
    links["Q"]["to_node"] = "D"
    links["Q"]["length"] = 20.0
    links["R"] = {**links["P"], "from_node": "D", "to_node": "C", "length": 10.0}
    result = infer_missing_inverts(nodes, links)
    assert result["nodes"]["B"]["invert"] == 99.0
    assert result["nodes"]["D"]["invert"] == 97.0
    assert all(row["upstream"]["node_id"] == "A" for row in result["assignments"])


def test_unknown_chain_with_no_anchor_is_not_fabricated():
    nodes, links = network()
    nodes["A"]["invert"] = nodes["C"]["invert"] = None
    result = infer_missing_inverts(nodes, links, extrapolation_slope=0.01)
    assert not result["assignments"]
    assert len(result["unresolved"]) == 3


@pytest.mark.parametrize("known_end, expected", [("A", 99.9), ("C", 96.3)])
def test_explicit_extrapolation_uses_correct_up_down_sign(known_end, expected):
    nodes, links = network()
    nodes["C" if known_end == "A" else "A"]["invert"] = None
    held = infer_missing_inverts(nodes, links)
    assert not held["assignments"]
    result = infer_missing_inverts(nodes, links, extrapolation_slope=0.01)
    assert result["nodes"]["B"]["invert"] == pytest.approx(expected)
    row = next(row for row in result["assignments"] if row["node_id"] == "B")
    assert row["flags"] == ["assumed_invert", "provisional_extrapolation"]
    assert row["extrapolation_slope"] == 0.01


@pytest.mark.parametrize("issue", ["branch", "pump", "drop", "offset", "boundary", "controlled_link"])
def test_interpolation_holds_protected_and_ambiguous_cases(issue):
    nodes, links = network()
    if issue == "branch":
        nodes["D"] = {"invert": 101.0}
        links["R"] = {**links["P"], "from_node": "D"}
    elif issue == "pump":
        links["P"]["kind"] = "pump"
    elif issue == "drop":
        nodes["B"]["explicit_drop"] = True
    elif issue == "offset":
        links["Q"]["inlet_offset"] = 0.2
    elif issue == "boundary":
        nodes["B"]["kind"] = "outfall"
    else:
        links["P"]["controls"] = ["rule"]
    result = infer_missing_inverts(nodes, links, extrapolation_slope=0.01)
    assert result["nodes"]["B"]["invert"] is None
    assert result["unresolved"]


def test_remote_branch_cannot_masquerade_as_dead_end_for_extrapolation():
    nodes, links = network()
    nodes["C"]["invert"] = None
    nodes["D"] = {"invert": None}
    links["R"] = {**links["P"], "from_node": "D", "to_node": "C"}
    result = infer_missing_inverts(nodes, links, extrapolation_slope=0.01)
    assert result["nodes"]["B"]["invert"] is None
    assert result["unresolved"][0]["downstream"]["status"] == "branch"


def test_cycle_is_held_even_with_extrapolation_policy():
    nodes, links = network()
    nodes["A"]["invert"] = nodes["C"]["invert"] = None
    links["R"] = {**links["P"], "from_node": "C", "to_node": "A"}
    result = infer_missing_inverts(nodes, links, extrapolation_slope=0.01)
    assert not result["assignments"]
    assert result["unresolved"][0]["upstream"]["status"] == "cycle"


def test_conflicting_known_anchors_preserved_and_flat_anchors_flagged():
    nodes, links = network()
    nodes["C"]["invert"] = 110.0
    result = infer_missing_inverts(nodes, links)
    assert result["unresolved"][0]["reason"] == "adverse_anchor_grade"
    assert result["nodes"]["C"]["invert"] == 110.0
    nodes["C"]["invert"] = 100.0
    result = infer_missing_inverts(nodes, links)
    assert "flat_anchor_grade" in result["assignments"][0]["flags"]


@pytest.mark.parametrize("field,value", [("length", 0), ("length", -1), ("length", float("nan")), ("inlet_offset", -1), ("outlet_offset", True)])
def test_invalid_link_quantities_raise(field, value):
    nodes, links = network()
    links["P"][field] = value
    with pytest.raises(ValueError):
        infer_missing_inverts(nodes, links)


@pytest.mark.parametrize("slope", [0, -0.1, float("inf"), True])
def test_invalid_extrapolation_slope_rejected(slope):
    with pytest.raises(ValueError):
        infer_missing_inverts(*network(), extrapolation_slope=slope)


def test_unrecognized_endpoint_and_bad_floor_rejected():
    nodes, links = network()
    links["P"]["from_node"] = "absent"
    with pytest.raises(ValueError, match="undeclared endpoint"):
        infer_missing_inverts(nodes, links)
    nodes, links = network()
    nodes["A"]["invert"] = float("nan")
    with pytest.raises(ValueError):
        infer_missing_inverts(nodes, links)


def test_simplification_default_and_preview_do_not_modify_network():
    nodes, links = complete_network()
    before = deepcopy((nodes, links))
    explicit = simplify_short_pipes(nodes, links)
    assert not explicit["merges"]
    preview = simplify_short_pipes(nodes, links, mode="simplify_eligible", length_unit="ft")
    assert len(preview["merges"]) == 1
    row = preview["merges"][0]
    assert row["status"] == "proposed"
    assert row["length"] == 40.0
    assert row["source_link_ids"] == ["P", "Q"]
    assert row["retained_link_id"] == "Q"
    assert row["length_unit"] == "ft"
    assert (preview["nodes"], preview["links"]) == before
    assert (nodes, links) == before


def test_reviewed_merge_preserves_outer_profile_total_length_and_lineage():
    nodes, links = complete_network()
    links["P"]["inlet_offset"] = 0.5
    links["Q"]["outlet_offset"] = 0.2
    preview = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    merge_id = preview["merges"][0]["merge_id"]
    applied = simplify_short_pipes(nodes, links, mode="simplify_eligible", approved_merge_ids=[merge_id])
    assert set(applied["nodes"]) == {"A", "C"}
    assert set(applied["links"]) == {"Q"}
    merged = applied["links"]["Q"]
    assert merged["from_node"] == "A"
    assert merged["to_node"] == "C"
    assert merged["length"] == sum(link["length"] for link in links.values())
    assert merged["inlet_offset"] == 0.5
    assert merged["outlet_offset"] == 0.2
    assert merged["path_node_ids"] == ["A", "B", "C"]
    assert merged["source_link_ids"] == ["P", "Q"]
    assert merged["assumptions"][-1]["upstream_invert"] == 100.5
    assert merged["assumptions"][-1]["downstream_invert"] == 96.2
    assert "UMDEV_SHORT_PIPE sources=P,Q" in merged["assumptions"][-1]["model_note"]
    assert applied["merges"][0]["status"] == "applied"


def test_approval_stales_on_changed_evidence():
    nodes, links = complete_network()
    preview = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    links["Q"]["length"] += 1
    with pytest.raises(ValueError, match="stale"):
        simplify_short_pipes(nodes, links, mode="simplify_eligible", approved_merge_ids=[preview["merges"][0]["merge_id"]])


def test_selective_and_keep_policies_and_strict_threshold():
    nodes, links = complete_network()
    assert not simplify_short_pipes(nodes, links, mode="selective")["merges"]
    assert simplify_short_pipes(nodes, links, mode="selective", selected_link_ids=["P"])["merges"]
    assert not simplify_short_pipes(nodes, links, mode="simplify_eligible", keep_link_ids=["Q"])["merges"]
    assert not simplify_short_pipes(nodes, links, mode="simplify_eligible", threshold=10)["merges"]


@pytest.mark.parametrize("issue,reason", [
    ("diameter", "changed_diameter"), ("material", "changed_material"),
    ("roughness", "changed_roughness"), ("unknown_material", "unknown_diameter_or_material"),
    ("drop", "intermediate_drop"), ("inflow", "protected_node"),
    ("control", "protected_link"), ("storage", "protected_node"),
    ("outfall", "protected_node"), ("pump", "non_gravity_pair"),
    ("force_main", "non_gravity_pair"), ("adverse", "nonmonotone_profile"),
    ("unknown_floor", "unknown_profile"),
])
def test_simplification_protects_engineering_features(issue, reason):
    nodes, links = complete_network()
    if issue in {"diameter", "material", "roughness"}:
        links["Q"][issue] = 2.0 if issue == "diameter" else "different"
    elif issue == "unknown_material":
        del links["Q"]["material"]
    elif issue == "drop":
        links["P"]["outlet_offset"] = 0.2
    elif issue == "inflow":
        nodes["B"]["inflows"] = [{"value": 0.1}]
    elif issue == "control":
        links["Q"]["controls"] = ["rule"]
    elif issue in {"storage", "outfall"}:
        nodes["B"]["kind"] = issue
    elif issue in {"pump", "force_main"}:
        links["Q"]["kind"] = issue
    elif issue == "adverse":
        nodes["B"]["invert"] = 110.0
    else:
        nodes["B"]["invert"] = None
    result = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    assert not result["merges"]
    assert reason in result["withheld"][0]["reasons"]


def test_simplification_counts_all_branches_and_protects_explicit_nodes():
    nodes, links = complete_network()
    nodes["D"] = {"invert": 95.0}
    links["R"] = {**links["Q"], "from_node": "B", "to_node": "D", "kind": "force_main"}
    assert not simplify_short_pipes(nodes, links, mode="simplify_eligible")["merges"]
    nodes, links = complete_network()
    assert not simplify_short_pipes(nodes, links, mode="simplify_eligible", protected_node_ids=["B"])["merges"]


def test_merge_preserves_map_vertices_and_withholds_without_middle_xy():
    nodes, links = complete_network()
    links["P"]["vertices"] = [[1, 2]]
    links["Q"]["vertices"] = [[3, 4]]
    assert not simplify_short_pipes(nodes, links, mode="simplify_eligible")["merges"]
    nodes["B"]["coordinates"] = [2, 3]
    preview = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    result = simplify_short_pipes(nodes, links, mode="simplify_eligible", approved_merge_ids=[preview["merges"][0]["merge_id"]])
    assert result["links"]["Q"]["vertices"] == [[1, 2], [2, 3], [3, 4]]


def test_upstream_absorption_and_deterministic_order():
    nodes, links = complete_network()
    links["P"]["length"], links["Q"]["length"] = 30.0, 10.0
    normal = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    reversed_input = simplify_short_pipes(dict(reversed(list(nodes.items()))), dict(reversed(list(links.items()))), mode="simplify_eligible")
    assert normal == reversed_input
    assert normal["merges"][0]["retained_link_id"] == "P"


@pytest.mark.parametrize("kwargs", [{"mode": "invalid"}, {"threshold": 0}, {"drop_tolerance": -1}, {"keep_link_ids": ["absent"]}, {"selected_link_ids": ["absent"]}, {"protected_node_ids": ["absent"]}, {"approved_merge_ids": ["absent"]}])
def test_invalid_simplification_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        simplify_short_pipes(*complete_network(), **kwargs)


def test_unit_scaled_interpolation_and_merge_are_equivalent():
    feet_nodes, feet_links = network()
    feet = infer_missing_inverts(feet_nodes, feet_links, length_unit="ft")
    metre_nodes, metre_links = deepcopy(feet_nodes), deepcopy(feet_links)
    for node in metre_nodes.values():
        if node["invert"] is not None:
            node["invert"] *= 0.3048
    for link in metre_links.values():
        for key in ("length", "diameter", "inlet_offset", "outlet_offset"):
            link[key] *= 0.3048
    metres = infer_missing_inverts(metre_nodes, metre_links, length_unit="m")
    assert metres["nodes"]["B"]["invert"] == pytest.approx(feet["nodes"]["B"]["invert"] * 0.3048)
    result = simplify_short_pipes(metres["nodes"], metre_links, threshold=20 * 0.3048, mode="simplify_eligible", length_unit="m")
    assert result["merges"][0]["length"] == pytest.approx(40 * 0.3048)


def test_invalid_diameter_is_not_a_known_compatible_section():
    nodes, links = complete_network()
    links["P"]["diameter"] = links["Q"]["diameter"] = -1.0
    with pytest.raises(ValueError, match="diameter"):
        simplify_short_pipes(nodes, links, mode="simplify_eligible")


@pytest.mark.parametrize("field,value", [("entry_loss", 0.2), ("exit_loss", 0.3), ("flow_limit", 0.01), ("flap_gate", "YES")])
def test_local_losses_and_flow_restrictions_are_protected(field, value):
    nodes, links = complete_network()
    links["P"][field] = value
    result = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    assert not result["merges"]
    assert "protected_hydraulic_feature" in result["withheld"][0]["reasons"]


def test_equal_nonzero_middle_offsets_remain_explicit():
    nodes, links = complete_network()
    links["P"]["outlet_offset"] = links["Q"]["inlet_offset"] = 0.2
    result = simplify_short_pipes(nodes, links, mode="simplify_eligible")
    assert not result["merges"]
    assert "intermediate_drop" in result["withheld"][0]["reasons"]
