"""Regress ordered, enabled model identity methods at preparation and handoff.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Synthetic identities and policies only; manually maintained tests.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from hymo import SWMMDocument

from umdev.models import bind_model_identities, compile_handoff, resolve_model_identity, validate_crosswalk


def identity_config(methods: list[str] | None = None) -> dict:
    """Return a deliberately scoped identity policy."""
    return {"identity": {"model_association": {} if methods is None else {"methods": methods}}}


def source_node() -> dict:
    """Return conflicting source/model identifiers for explicit precedence tests."""
    return {"key": "source/nodes/001", "asset_id": "001", "model_id": "MAPPED",
            "source_attributes": {"original_target": "MAPPED"}}


@pytest.mark.parametrize("methods,expected,method", [
    (["mapped_model_id", "explicit_crosswalk", "asset_id"], "MAPPED", "mapped_model_id"),
    (["explicit_crosswalk", "mapped_model_id", "asset_id"], "CROSSWALK", "explicit_crosswalk"),
    (["asset_id", "mapped_model_id", "explicit_crosswalk"], "001", "asset_id"),
    (["asset_id"], "001", "asset_id"),
    (["explicit_crosswalk"], "CROSSWALK", "explicit_crosswalk")])
def test_ordered_enabled_methods_select_the_same_identity(methods: list[str], expected: str, method: str) -> None:
    """Source model IDs, crosswalks and asset fallbacks are explicit alternatives."""
    assert resolve_model_identity(source_node(), "nodes", identity_config(methods),
                                  {"nodes": {"001": "CROSSWALK"}}) == (expected, method)


def test_absent_method_policy_preserves_documented_compatibility_order() -> None:
    """An absent policy differs from an explicitly empty method list."""
    assert resolve_model_identity(source_node(), "nodes", identity_config(), {}) == ("MAPPED", "mapped_model_id")
    with pytest.raises(ValueError, match="Unresolved"):
        resolve_model_identity(source_node(), "nodes", identity_config([]), {})


def test_disabled_fallback_cannot_create_an_unrequested_model_identity() -> None:
    """Missing crosswalk cannot silently fall through to mapped or asset IDs."""
    with pytest.raises(ValueError, match="Unresolved"):
        resolve_model_identity(source_node(), "nodes", identity_config(["explicit_crosswalk"]), {})


def test_binding_preserves_source_mapping_and_is_idempotent() -> None:
    """Baseline lookup and compilation share the exact resolved method/evidence."""
    canonical = {"nodes": [source_node(), {"key": "s/n/2", "asset_id": "002"}],
                 "pipes": [{"key": "s/p/A", "asset_id": "A", "from_node": "001", "to_node": "002"}]}
    config = identity_config(["explicit_crosswalk", "mapped_model_id", "asset_id"])
    crosswalk = {"nodes": {"001": "CROSSWALK"}, "pipes": {"A": "TARGET_PIPE"}}
    original_attributes = deepcopy(canonical["nodes"][0]["source_attributes"])
    bind_model_identities(canonical, config, crosswalk, ["s/p/A"])
    assert canonical["nodes"][0]["model_id"] == "CROSSWALK"
    assert canonical["nodes"][0]["model_association"]["mapped_model_id"] == "MAPPED"
    assert canonical["nodes"][0]["source_attributes"] == original_attributes
    first = deepcopy(canonical)
    bind_model_identities(canonical, config, crosswalk, ["s/p/A"])
    assert canonical == first
    assert resolve_model_identity(canonical["nodes"][0], "nodes", config, crosswalk) == ("CROSSWALK", "explicit_crosswalk")


def test_unselected_full_network_context_does_not_require_partial_crosswalk_entries() -> None:
    """A selected scope can use a partial crosswalk while attribution sees all pipes."""
    canonical = {"nodes": [{"key": "s/n/1", "asset_id": "001"}, {"key": "s/n/2", "asset_id": "002"},
                            {"key": "s/n/3", "asset_id": "003"}],
                 "pipes": [{"key": "s/p/A", "asset_id": "A", "from_node": "001", "to_node": "002"},
                           {"key": "s/p/B", "asset_id": "B", "from_node": "002", "to_node": "003"}]}
    config = identity_config(["explicit_crosswalk"])
    crosswalk = {"nodes": {"001": "N1", "002": "N2"}, "pipes": {"A": "P1"}}
    bind_model_identities(canonical, config, crosswalk, ["s/p/A"])
    assert canonical["pipes"][0]["model_id"] == "P1"
    assert "model_id" not in canonical["pipes"][1]
    assert "model_id" not in canonical["nodes"][2]


def test_distinct_sources_cannot_silently_collapse_into_one_model_node() -> None:
    """Many-source aggregation needs its own explicit supported contract."""
    canonical = {"nodes": [{"key": "s/n/1", "asset_id": "001"}, {"key": "s/n/2", "asset_id": "002"}],
                 "pipes": [{"key": "s/p/A", "asset_id": "A", "from_node": "001", "to_node": "002"}]}
    with pytest.raises(ValueError, match="Multiple source nodes"):
        bind_model_identities(canonical, identity_config(["explicit_crosswalk"]),
                             {"nodes": {"001": "JOIN", "002": "join"}, "pipes": {"A": "P1"}}, ["s/p/A"])


@pytest.mark.parametrize("value", [{"nodes": []}, {"nodes": {"001": 1}}, {"nodes": {"001": ""}}, {"unknown": {}}])
def test_malformed_crosswalk_is_rejected_without_identifier_coercion(value: object) -> None:
    """Numeric/empty target identities must not erase leading-zero semantics."""
    with pytest.raises(ValueError, match="Crosswalk"):
        validate_crosswalk(value)


def test_compilation_uses_enabled_order_without_pipeline_mutation() -> None:
    """The direct model adapter uses the same precedence as the full runner."""
    config = identity_config(["mapped_model_id", "explicit_crosswalk", "asset_id"])
    config.update({"model": {"target": {"flow_units": "LPS", "length_unit": "m", "link_offsets": "DEPTH",
                                       "map_crs": {"epsg": 3857}, "map_unit": "m", "vertical_datum": "TEST"},
                              "baseline_metadata": {"horizontal_crs": {"epsg": 3857}, "map_unit": "m", "vertical_datum": "TEST"}},
                   "selection": {"scenario": "existing"}})
    baseline = SWMMDocument.from_bytes(b"[OPTIONS]\nFLOW_UNITS LPS\nLINK_OFFSETS DEPTH\n[JUNCTIONS]\nMAPPED 10 2\nOTHER 9 2\n[COORDINATES]\nMAPPED 0 0\nOTHER 10 0\n")
    prepared = {"nodes": [
        {"key": "s/n/1", "asset_id": "001", "model_id": "MAPPED", "coordinates": [0, 0],
         "floor_m": 10, "rim_m": 12, "max_depth_m": 2},
        {"key": "s/n/2", "asset_id": "002", "model_id": "OTHER", "coordinates": [10, 0],
         "floor_m": 9, "rim_m": 11, "max_depth_m": 2}],
        "pipes": [{"key": "s/p/A", "asset_id": "A", "model_id": "MAPPED_PIPE", "disposition": "include",
                   "from_node": "001", "to_node": "002", "shape": "CIRCULAR", "length_m": 10,
                   "diameter_m": 0.3, "roughness": 0.013, "inlet_offset_m": 0, "outlet_offset_m": 0,
                   "coordinates": [[0, 0], [10, 0]]}], "findings": []}
    handoff = compile_handoff(baseline, prepared, config, config_sha256="test-hash",
                              crosswalk_snapshot={"nodes": {"001": "DIFFERENT"}, "pipes": {"A": "DIFFERENT_PIPE"}})
    assert handoff["model_ids"]["pipes"]["s/p/A"] == "MAPPED_PIPE"
    assert handoff["model_ids"]["nodes"]["001"] == "MAPPED"
