"""Synthetic graph and model-family tests for reproducible island diagnosis.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. Topological routes do not establish hydraulic acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest
from hymo import SWMMDocument

from umdev.topology import diagnose_swmm_topology, diagnose_topology


def nodes(*identities: str) -> list[dict[str, Any]]:
    """Declare every model node, including nodes with no incident links."""
    return [{"asset_id": identity} for identity in identities]


def link(identity: str, start: str, end: str, family: str = "CONDUITS", **metadata: Any) -> dict[str, Any]:
    """Create an explicit directed link independent of geometry or elevations."""
    return {"asset_id": identity, "from_node": start, "to_node": end, "section": family, **metadata}


def by_node(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index retained display identities from a diagnosis."""
    return {row["node_id"]: row for row in report["nodes"]}


def test_known_separate_baseline_networks_are_not_introduced_islands() -> None:
    """Two independently draining baseline basins remain distinct known networks."""
    declared = nodes("A", "OUT_A", "B", "OUT_B")
    declared[1]["section"] = declared[3]["section"] = "OUTFALLS"
    edges = [link("LA", "A", "OUT_A"), link("LB", "B", "OUT_B")]
    result = diagnose_topology(declared, edges, baseline_nodes=declared, baseline_links=edges)
    assert result["summary"]["weak_components"] == 2
    assert result["summary"]["baseline_weak_components"] == 2
    assert {row["classification"] for row in result["components"]} == {"baseline_component"}
    assert not result["findings"]
    assert all(row["toward_terminal"]["reachable"] for row in result["nodes"])


def test_baseline_attachment_does_not_prove_directed_drainage() -> None:
    """A backwards-facing added branch is weakly attached but has no downstream route."""
    old_nodes = nodes("OLD", "OUT")
    old_nodes[1]["section"] = "OUTFALLS"
    old_links = [link("OLD_LINK", "OLD", "OUT")]
    candidate_nodes = old_nodes + nodes("NEW")
    result = diagnose_topology(candidate_nodes, old_links + [link("BRANCH", "OLD", "NEW")],
                               baseline_nodes=old_nodes, baseline_links=old_links)
    new = by_node(result)["NEW"]
    assert result["components"][0]["classification"] == "baseline_connected_extension"
    assert not new["toward_baseline"]["reachable"]
    assert not new["toward_terminal"]["reachable"]
    assert any(row["rule"] == "new_nodes_without_nominal_downstream_anchor" for row in result["findings"])


def test_upstream_new_node_reaches_baseline_then_outfall() -> None:
    """Separate next-hop witnesses explain nominal routes without copying long paths."""
    old_nodes = nodes("OLD", "OUT")
    old_nodes[1]["section"] = "OUTFALLS"
    old_links = [link("OLD_LINK", "OLD", "OUT")]
    result = diagnose_topology(old_nodes + nodes("NEW"), old_links + [link("NEW_LINK", "NEW", "OLD")],
                               baseline_nodes=old_nodes, baseline_links=old_links)
    new = by_node(result)["NEW"]
    assert new["toward_baseline"] == {"reachable": True, "anchor_id": "OLD", "next_node_id": "OLD", "next_link_id": "NEW_LINK", "edge_count": 1}
    assert new["toward_terminal"]["anchor_id"] == "OUT"
    assert new["toward_terminal"]["edge_count"] == 2
    assert by_node(result)["OLD"]["toward_baseline"]["edge_count"] == 0


def test_baseline_anchor_zero_edge_route_is_not_outfall_acceptance() -> None:
    """A known isolated modeled node reaches itself as baseline, without an outfall."""
    result = diagnose_topology(nodes("KNOWN"), [], baseline_nodes=nodes("KNOWN"))
    node = by_node(result)["KNOWN"]
    assert node["toward_baseline"]["reachable"]
    assert not node["toward_terminal"]["reachable"]
    assert result["summary"]["isolated_declared_nodes"] == 1
    assert result["components"][0]["classification"] == "baseline_component"


def test_all_isolated_nodes_are_included_even_without_edges() -> None:
    """New orphan nodes are not lost by building a graph from links only."""
    result = diagnose_topology(nodes("A", "B"), [])
    assert result["summary"]["weak_components"] == 2
    assert result["summary"]["new_isolated_nodes"] == 2
    assert result["summary"]["isolated_declared_nodes"] == 2
    assert [row["node_ids"] for row in result["components"]] == [["A"], ["B"]]


def test_new_network_with_explicit_terminal_is_distinct_from_unsupported_island() -> None:
    """A new self-contained terminal network remains a separate review category."""
    declared = nodes("A", "OUT", "ISLAND_A", "ISLAND_B")
    result = diagnose_topology(declared, [link("DRAIN", "A", "OUT"), link("ISLAND", "ISLAND_A", "ISLAND_B")],
                               terminal_node_ids=["OUT"])
    classes = {row["classification"] for row in result["components"]}
    assert classes == {"new_network_with_terminal", "new_unsupported_island"}
    assert result["summary"]["new_unsupported_islands"] == 1


def test_baseline_components_merging_is_explicit() -> None:
    """A newly connecting link may change basin topology and must be identified."""
    old_nodes = nodes("A", "B")
    result = diagnose_topology(old_nodes, [link("BRIDGE", "A", "B")], baseline_nodes=old_nodes)
    assert result["components"][0]["classification"] == "baseline_component_merge"
    assert len(result["components"][0]["baseline_component_ids"]) == 2


def test_removed_baseline_link_reports_component_split() -> None:
    """A disappeared connector cannot hide behind per-component summaries."""
    declared = nodes("A", "B", "C")
    old = [link("AB", "A", "B"), link("BC", "B", "C")]
    result = diagnose_topology(declared, [old[0]], baseline_nodes=declared, baseline_links=old)
    assert result["missing_baseline_link_ids"] == ["BC"]
    assert len(result["baseline_component_splits"]) == 1
    assert any(row["rule"] == "baseline_component_split" for row in result["findings"])


def test_missing_baseline_node_and_rewired_edge_are_reported() -> None:
    """The graph audit identifies omissions and directed endpoint changes separately."""
    result = diagnose_topology(nodes("A", "B"), [link("AB", "B", "A")],
                               baseline_nodes=nodes("A", "B", "C"), baseline_links=[link("AB", "A", "B")])
    assert result["missing_baseline_node_ids"] == ["C"]
    assert result["changed_baseline_link_endpoints"] == ["AB"]


def test_exact_id_held_connector_is_review_candidate_without_modifying_graph() -> None:
    """A held connector can explain an island but never becomes an approved edge."""
    declared = nodes("A", "OUT", "ISLAND")
    declared[1]["section"] = "OUTFALLS"
    old_nodes = declared[:2]
    edges = [link("ACTIVE", "A", "OUT")]
    held = [link("HELD", "ISLAND", "A", key="source/pipes/held", disposition="hold", reasons=["missing_rim"], status="active")]
    original = deepcopy((declared, edges, held))
    result = diagnose_topology(declared, edges, baseline_nodes=old_nodes, baseline_links=edges, source_connectors=held)
    connector = result["source_connector_candidates"][0]
    assert connector["classification"] == "bridges_candidate_components"
    assert connector["could_supply_directed_terminal_route"]
    assert connector["could_supply_directed_baseline_route"]
    assert connector["disposition"] == "hold"
    assert connector["reasons"] == ["missing_rim"]
    assert not connector["applied_to_graph"]
    assert not by_node(result)["ISLAND"]["toward_terminal"]["reachable"]
    assert result["summary"]["links"] == 1
    assert (declared, edges, held) == original


def test_excluded_source_branch_is_not_scope_expansion() -> None:
    """A source edge into an unmodeled endpoint retains its original exclusion."""
    result = diagnose_topology(nodes("A"), [], source_connectors=[
        link("EXCLUDED", "OUTSIDE", "A", key="s/p/x", disposition="excluded_by_scenario", reasons=["GIS_only_scope"], status="abandoned")])
    row = result["source_connector_candidates"][0]
    assert row["classification"] == "touches_unmodeled_endpoint"
    assert row["unmodeled_node_ids"] == ["OUTSIDE"]
    assert row["status"] == "abandoned"
    assert row["disposition"] == "excluded_by_scenario"
    assert "OUTSIDE" not in by_node(result)


def test_within_component_connector_can_explain_missing_directed_route() -> None:
    """Weak connectivity alone cannot diagnose a backwards-facing branch."""
    declared = nodes("A", "OUT", "B")
    result = diagnose_topology(declared, [link("AO", "A", "OUT"), link("AB", "A", "B")],
                               terminal_node_ids=["OUT"], source_connectors=[link("BA", "B", "A", disposition="hold")])
    connector = result["source_connector_candidates"][0]
    assert connector["classification"] == "within_candidate_component"
    assert connector["could_supply_directed_terminal_route"]


def test_existing_source_link_is_not_recommended_as_parallel_addition() -> None:
    """Exact link/end identity excludes already represented source records."""
    result = diagnose_topology(nodes("A", "B"), [link("MODEL_EDGE", "A", "B")], source_connectors=[
        link("GIS_EDGE", "A", "B", model_id="MODEL_EDGE", disposition="hold")])
    assert not result["source_connector_candidates"]
    assert result["summary"]["source_connectors_already_present"] == 1


def test_connector_with_no_modeled_endpoint_is_counted_without_synthetic_bridge() -> None:
    """Missing multi-edge source chains are not advertised as direct reconnections."""
    result = diagnose_topology(nodes("A"), [], source_connectors=[link("UNRELATED", "X", "Y")])
    assert result["summary"]["source_connectors_without_model_endpoint"] == 1
    assert not result["source_connector_candidates"]


def test_missing_actual_endpoint_is_diagnostic_not_an_invented_declared_node() -> None:
    """Dangling references remain visible even when graph traversal includes them."""
    result = diagnose_topology(nodes("A"), [link("BROKEN", "A", "UNDECLARED")])
    ghost = by_node(result)["UNDECLARED"]
    assert not ghost["declared"]
    assert not ghost["toward_terminal"]["reachable"]
    assert result["summary"]["referenced_only_nodes"] == 1
    assert "UNDECLARED" in result["components"][0]["referenced_only_node_ids"]
    assert "UNDECLARED" not in result["components"][0]["new_node_ids"]
    assert any(row["rule"] == "missing_node_reference" for row in result["findings"])


def test_missing_terminal_is_not_synthesized_as_outfall() -> None:
    """Only explicitly declared node objects can act as terminal anchors."""
    result = diagnose_topology(nodes("A"), [link("DANGLING", "A", "OUT")], terminal_node_ids=["OUT"])
    assert not by_node(result)["A"]["toward_terminal"]["reachable"]
    assert any(row["rule"] == "terminal_node_not_declared" for row in result["findings"])


def test_swmm_case_comparison_and_leading_zeros_are_explicit() -> None:
    """Case-insensitive mode does not strip original spelling or merge numeric IDs."""
    declared = nodes("a", "001", "1")
    result = diagnose_topology(declared, [link("L", "A", "001")], terminal_node_ids=["001"], identity_mode="ascii_case_insensitive")
    assert set(by_node(result)) == {"a", "001", "1"}
    assert by_node(result)["a"]["toward_terminal"]["reachable"]
    assert by_node(result)["1"]["isolated"]
    exact = diagnose_topology(nodes("a"), [link("L", "A", "a")])
    assert exact["summary"]["referenced_only_nodes"] == 1


@pytest.mark.parametrize("mode", ["exact", "ascii_case_insensitive"])
def test_cycles_parallel_edges_and_pump_family_are_traversable_deterministically(mode: str) -> None:
    """Nominal graph routes support common hydraulic link families without simulating them."""
    declared = nodes("A", "B", "OUT")
    edges = [link("L2", "A", "B"), link("L1", "A", "B", "PUMPS"),
             link("CYCLE", "B", "A", "WEIRS"), link("BO", "B", "OUT", "ORIFICES")]
    result = diagnose_topology(declared, edges, terminal_node_ids=["OUT"], identity_mode=mode)
    assert by_node(result)["A"]["toward_terminal"]["next_link_id"] == "L1"
    assert by_node(result)["A"]["toward_terminal"]["edge_count"] == 2
    assert result["components"][0]["link_families"] == {"CONDUITS": 1, "ORIFICES": 1, "PUMPS": 1, "WEIRS": 1}


def test_node_and_link_row_order_do_not_change_report() -> None:
    """Canonical report bytes are reproducible under input iteration changes."""
    declared = nodes("B", "A", "OUT", "NEW")
    edges = [link("Z", "B", "OUT"), link("A", "A", "B")]
    connectors = [link("H1", "NEW", "A", disposition="hold"), link("H2", "NEW", "B", disposition="not_selected")]
    first = diagnose_topology(declared, edges, baseline_nodes=declared[:3], baseline_links=edges,
                              terminal_node_ids=["OUT"], source_connectors=connectors)
    second = diagnose_topology(list(reversed(declared)), list(reversed(edges)), baseline_nodes=list(reversed(declared[:3])),
                               baseline_links=list(reversed(edges)), terminal_node_ids=["OUT"], source_connectors=list(reversed(connectors)))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.parametrize("declared,edges,kwargs", [
    (nodes("A", "A"), [], {}),
    (nodes("A", "a"), [], {"identity_mode": "ascii_case_insensitive"}),
    (nodes("A", "B"), [link("L", "A", "B"), link("L", "B", "A")], {}),
    (nodes("A"), [{"asset_id": "L", "from_node": "A", "to_node": None}], {}),
    ([{"asset_id": 1}], [], {}),
    (nodes("A"), [], {"identity_mode": "guess"})])
def test_invalid_actual_graph_identities_fail_before_analysis(declared: list, edges: list, kwargs: dict) -> None:
    """Actual graph identities must be explicit and unambiguous."""
    with pytest.raises(ValueError):
        diagnose_topology(declared, edges, **kwargs)


def test_invalid_source_connector_becomes_review_finding() -> None:
    """An incomplete held source endpoint cannot create an inferred connection."""
    result = diagnose_topology(nodes("A"), [], source_connectors=[{"asset_id": "BAD", "from_node": "A", "to_node": None}])
    assert not result["source_connector_candidates"]
    assert any(row["rule"] == "unusable_source_connector_identity" for row in result["findings"])


def test_all_model_node_and_link_families_and_isolated_storage_are_read_only() -> None:
    """The SWMM wrapper inspects actual families, including nodes with no edges."""
    data = b"[JUNCTIONS]\nA 10 2\n[STORAGE]\nS 8 3 0 FUNCTIONAL 1 1 0\nISOLATED_STORAGE 9 3 0 FUNCTIONAL 1 1 0\n[DIVIDERS]\nD 7 BYPASS\n[OUTFALLS]\nOUT 5 FREE\n[CONDUITS]\nC A S 10 0.013 0 0\n[PUMPS]\nP S D CURVE ON\n[WEIRS]\nW D OUT TRANSVERSE 0 1 NO 0\n[ORIFICES]\nO A D SIDE 0 0.6 NO\n[OUTLETS]\nT S OUT 0 FUNCTIONAL/DEPTH 1 1 NO\n"
    document = SWMMDocument.from_bytes(data)
    report = diagnose_swmm_topology(document, document)
    assert report["summary"]["declared_nodes"] == 5
    assert report["summary"]["links"] == 5
    assert report["summary"]["isolated_declared_nodes"] == 1
    assert by_node(report)["A"]["toward_terminal"]["reachable"]
    assert by_node(report)["ISOLATED_STORAGE"]["isolated"]
    assert report["candidate_sha256"] == report["baseline_sha256"]
    assert document.data == data


def test_empty_graph_is_a_valid_empty_diagnosis() -> None:
    """An empty graph is represented explicitly rather than divided by component counts."""
    result = diagnose_topology([], [])
    assert result["summary"]["weak_components"] == 0
    assert result["components"] == result["nodes"] == []


def test_existing_model_link_with_different_source_endpoints_is_an_identity_conflict() -> None:
    """A reused link ID cannot be presented as a supported new connecting edge."""
    result = diagnose_topology(nodes("A", "B", "OUT"), [link("EXISTING", "A", "OUT")],
                               terminal_node_ids=["OUT"], source_connectors=[
                                   link("GIS", "B", "OUT", model_id="EXISTING", disposition="hold")])
    connector = result["source_connector_candidates"][0]
    assert connector["existing_model_link_endpoint_conflict"]
    assert connector["existing_model_endpoints"] == ["A", "OUT"]
    assert not connector["could_supply_directed_terminal_route"]
    assert not connector["applied_to_graph"]


def test_connector_report_is_independent_of_mutable_input_reasons() -> None:
    """Annotating a review artifact cannot modify retained source dispositions."""
    source = [link("SOURCE", "A", "B", reasons=["missing_parameter"], disposition="hold")]
    result = diagnose_topology(nodes("A"), [], source_connectors=source)
    result["source_connector_candidates"][0]["reasons"].append("reviewed_in_report")
    assert source[0]["reasons"] == ["missing_parameter"]
