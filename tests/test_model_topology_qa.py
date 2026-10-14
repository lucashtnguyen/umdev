"""Check complete-model QA for legitimate basins, direction and isolated nodes.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Synthetic models only; graph reachability does not establish hydraulic acceptance.
"""
from __future__ import annotations

from hymo import SWMMDocument

from umdev.models import candidate_engineering_qa

CONFIG = {"model": {"target": {"length_unit": "m", "link_offsets": "DEPTH"}}}
TWO_BASINS = b"""[OPTIONS]
FLOW_UNITS LPS
LINK_OFFSETS DEPTH
[JUNCTIONS]
A 10 3
B 20 3
[OUTFALLS]
X 9 FREE
Y 19 FREE
[CONDUITS]
AX A X 10 0.013 0 0
BY B Y 10 0.013 0 0
[XSECTIONS]
AX CIRCULAR 0.3 0 0 0
BY CIRCULAR 0.3 0 0 0
"""


def test_separate_outfall_basins_are_informational() -> None:
    """Separate valid outlet networks are not unsupported island warnings."""
    findings = candidate_engineering_qa(SWMMDocument.from_bytes(TWO_BASINS), CONFIG)
    assert len(findings) == 2
    assert all(r["rule"] == "independent_outfall_component" for r in findings)
    assert all(r["severity"] == "info" and r["disposition"] == "informational" for r in findings)


def test_isolated_declared_node_is_not_lost_from_link_only_graph() -> None:
    """A node with no links is a modeled object requiring connectivity review."""
    data = TWO_BASINS.replace(b"B 20 3", b"B 20 3\nISOLATED 30 2")
    findings = candidate_engineering_qa(SWMMDocument.from_bytes(data), CONFIG)
    isolated = [r for r in findings if r["rule"] == "isolated_model_node"]
    assert len(isolated) == 1 and isolated[0]["node_ids"] == ["ISOLATED"]


def test_weak_connection_to_outfall_does_not_prove_directed_route() -> None:
    """A reversed edge is connected on a map but lacks a nominal outlet path."""
    data = TWO_BASINS.replace(b"AX A X 10", b"AX X A 10")
    findings = candidate_engineering_qa(SWMMDocument.from_bytes(data), CONFIG)
    missing = [r for r in findings if r["rule"] == "nodes_without_directed_outfall_route"]
    assert len(missing) == 1 and missing[0]["node_ids"] == ["A"]
