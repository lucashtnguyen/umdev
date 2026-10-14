"""Verify connector diagnostics obey frozen, configured identity methods.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained synthetic cases; excluded source links never become model edges.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from umdev.pipeline import _topology_source_connectors
from umdev.topology import diagnose_topology


def source_case() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Provide an unselected held edge with competing source/model names."""
    canonical = {"nodes": [
        {"key": "s/n/SOURCE_A", "asset_id": "SOURCE_A", "model_id": "MAPPED_A"},
        {"key": "s/n/SOURCE_B", "asset_id": "SOURCE_B", "model_id": "MAPPED_B"}],
        "pipes": [{"key": "s/p/SOURCE_LINK", "asset_id": "SOURCE_LINK", "model_id": "MAPPED_LINK",
                   "from_node": "SOURCE_A", "to_node": "SOURCE_B", "pipe_kind": "gravity", "status": "active"}]}
    membership = [{"source_key": "s/p/SOURCE_LINK", "disposition": "hold", "reasons": ["missing_profile"]}]
    config = {"identity": {"model_association": {"methods": ["explicit_crosswalk"],
                                                  "crosswalk_path": "not-read-by-diagnostic.json"}}}
    crosswalk = {"nodes": {"SOURCE_A": "MODEL_A", "SOURCE_B": "MODEL_B"}, "pipes": {"SOURCE_LINK": "MODEL_LINK"}}
    return canonical, membership, config, crosswalk


def test_unselected_connectors_use_frozen_crosswalk_instead_of_raw_asset_or_mapped_ids() -> None:
    """Partial preparation must not skip configured identity rules during later QA."""
    canonical, membership, config, crosswalk = source_case()
    original = deepcopy((canonical, membership, config, crosswalk))
    connectors = _topology_source_connectors(canonical, membership, {}, config, crosswalk)
    row = connectors[0]
    assert (row["from_node"], row["to_node"], row["model_id"]) == ("MODEL_A", "MODEL_B", "MODEL_LINK")
    assert row["node_model_association"]["from_node"]["method"] == "explicit_crosswalk"
    assert row["pipe_model_association"]["method"] == "explicit_crosswalk"
    assert row["source_from_node"] == "SOURCE_A"
    assert row["disposition"] == "hold"
    assert row["reasons"] == ["missing_profile"]
    assert not row["identity_issues"]
    assert (canonical, membership, config, crosswalk) == original
    report = diagnose_topology([{"asset_id": "MODEL_A"}, {"asset_id": "MODEL_B"}], [], source_connectors=connectors)
    assert report["source_connector_candidates"][0]["classification"] == "bridges_candidate_components"
    assert report["summary"]["links"] == 0


def test_unresolved_endpoint_does_not_fall_back_to_matching_raw_model_name() -> None:
    """A coincident raw string cannot bypass a disabled asset-ID fallback."""
    canonical, membership, config, crosswalk = source_case()
    del crosswalk["nodes"]["SOURCE_B"]
    connectors = _topology_source_connectors(canonical, membership, {}, config, crosswalk)
    row = connectors[0]
    assert row["from_node"] == "MODEL_A"
    assert row["to_node"] is None
    assert row["source_to_node"] == "SOURCE_B"
    assert "to_node" in row["identity_issues"]
    # The actual model deliberately has a node named SOURCE_B. It still cannot
    # be joined when the declared crosswalk-only policy did not map that asset.
    report = diagnose_topology([{"asset_id": "MODEL_A"}, {"asset_id": "SOURCE_B"}], [], source_connectors=connectors)
    assert report["source_connector_candidates"] == []
    finding = next(item for item in report["findings"] if item["rule"] == "unusable_source_connector_identity")
    assert finding["source_to_node"] == "SOURCE_B"
    assert finding["identity_issues"]["to_node"]
    assert finding["disposition"] == "hold"
    assert finding["reasons"] == ["missing_profile"]


def test_endpoint_field_can_use_explicit_crosswalk_without_a_structure_row() -> None:
    """A source endpoint ID is valid identity evidence even if its structure data are missing."""
    canonical, membership, config, crosswalk = source_case()
    canonical["nodes"].pop()
    connectors = _topology_source_connectors(canonical, membership, {}, config, crosswalk)
    assert connectors[0]["to_node"] == "MODEL_B"
    assert not connectors[0]["identity_issues"]


def test_missing_source_endpoint_remains_a_diagnostic_record() -> None:
    """A held edge with no endpoint cannot disappear from identity QA."""
    canonical, membership, config, crosswalk = source_case()
    canonical["pipes"][0]["to_node"] = None
    connectors = _topology_source_connectors(canonical, membership, {}, config, crosswalk)
    assert len(connectors) == 1
    assert connectors[0]["to_node"] is None
    assert connectors[0]["identity_issues"]["to_node"]


@pytest.mark.parametrize("methods,expected", [
    (["explicit_crosswalk", "mapped_model_id", "asset_id"], ("MODEL_A", "MODEL_B", "MODEL_LINK")),
    (["mapped_model_id", "explicit_crosswalk", "asset_id"], ("MAPPED_A", "MAPPED_B", "MAPPED_LINK")),
    (["asset_id"], ("SOURCE_A", "SOURCE_B", "SOURCE_LINK"))])
def test_diagnostic_method_order_matches_application_policy(methods: list[str], expected: tuple[str, str, str]) -> None:
    """Diagnostic convenience does not reorder source identity precedence."""
    canonical, membership, config, crosswalk = source_case()
    config["identity"]["model_association"]["methods"] = methods
    row = _topology_source_connectors(canonical, membership, {}, config, crosswalk)[0]
    assert (row["from_node"], row["to_node"], row["model_id"]) == expected


def test_applied_handoff_targets_remain_authoritative_for_existing_candidate_edges() -> None:
    """Already applied identities are frozen application evidence, not a new source inference."""
    canonical, membership, config, crosswalk = source_case()
    handoff = {"model_ids": {"nodes": {"SOURCE_A": "FROZEN_A", "SOURCE_B": "FROZEN_B"},
                              "pipes": {"s/p/SOURCE_LINK": "FROZEN_LINK"}}}
    row = _topology_source_connectors(canonical, membership, handoff, config, crosswalk)[0]
    assert (row["from_node"], row["to_node"], row["model_id"]) == ("FROZEN_A", "FROZEN_B", "FROZEN_LINK")
    assert row["node_model_association"]["from_node"]["method"] == "frozen_applied_handoff"


def test_unresolved_pipe_model_id_does_not_match_raw_asset_to_existing_model_link() -> None:
    """An unenabled raw-asset fallback must not suppress unresolved identity evidence."""
    canonical, membership, config, crosswalk = source_case()
    crosswalk["pipes"] = {}
    connectors = _topology_source_connectors(canonical, membership, {}, config, crosswalk)
    row = connectors[0]
    assert row["model_id"] is None
    assert row["model_identity_resolved"] is False
    assert row["identity_issues"]["model_id"]
    report = diagnose_topology([{"asset_id": "MODEL_A"}, {"asset_id": "MODEL_B"}],
                               [{"asset_id": "SOURCE_LINK", "from_node": "MODEL_A", "to_node": "MODEL_B"}],
                               source_connectors=connectors)
    assert report["summary"]["source_connectors_already_present"] == 0
    candidate = report["source_connector_candidates"][0]
    assert not candidate["model_identity_resolved"]
    assert candidate["identity_issues"]["model_id"]


def test_review_annotation_cannot_mutate_membership_reasons() -> None:
    """Diagnostic records are independent of original source dispositions."""
    canonical, membership, config, crosswalk = source_case()
    connectors = _topology_source_connectors(canonical, membership, {}, config, crosswalk)
    connectors[0]["reasons"].append("reviewed")
    assert membership[0]["reasons"] == ["missing_profile"]
