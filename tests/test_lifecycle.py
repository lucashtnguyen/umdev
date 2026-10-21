"""Verify retirement intent, mixed aggregates and native load dependency guards.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Synthetic records only; no utility-specific identifiers or engineering approvals.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from hymo import SWMMDocument

from umdev.lifecycle import lifecycle_record_fingerprint, lifecycle_source_fingerprint, plan_lifecycle_retirements
from umdev.reporting import stable_hash


def source(key: str, u: str = "A", v: str = "B", status: str = "abandoned") -> dict[str, Any]:
    """Build normalized lifecycle and the exact independently bound raw source."""
    row = {"key": "utility/pipes/" + key, "asset_id": key, "from_node": u, "to_node": v,
           "status": status, "length": 10., "diameter": .2,
           "source_attributes": {"id": key, "from": u, "to": v, "status": status}}
    row["source_record_sha256"] = stable_hash({"source_key": row["key"], "record": row["source_attributes"]})
    return row


def document(extra: str = "", *, corridor: bool = False) -> SWMMDocument:
    """Include an active alternative route, allowing isolated retired internals."""
    links = "R1 A X 10 .013 0 0\nR2 X Y 20 .013 0 0\nR3 Y B 30 .013 0 0\n" if corridor else "R A B 60 .013 0 0\n"
    xs = "R1 CIRCULAR 1 0 0 0 1\nR2 CIRCULAR 1 0 0 0 1\nR3 CIRCULAR 1 0 0 0 1\n" if corridor else "R CIRCULAR 1 0 0 0 1\n"
    text = ("[OPTIONS]\nFLOW_UNITS MGD\n[JUNCTIONS]\nA 100 10 0 0 0\nX 99 10 0 0 0\nY 98 10 0 0 0\nB 97 10 0 0 0\n"
            "[CONDUITS]\n" + links + "KEEP A B 60 .013 0 0\nLIVE X B 50 .013 0 0\n"
            + "[XSECTIONS]\n" + xs + "KEEP CIRCULAR 1 0 0 0 1\nLIVE CIRCULAR 1 0 0 0 1\n" + extra)
    if corridor:
        text = text.replace("LIVE X B 50 .013 0 0\n", "").replace("LIVE CIRCULAR 1 0 0 0 1\n", "")
    return SWMMDocument.from_bytes(text.encode())


def review_fields() -> dict[str, str]:
    """Provide explicit fictional review metadata for every decision."""
    return {"basis": "Reviewed fictional asset evidence", "reviewer": "Synthetic reviewer",
            "date": "2026-10-21", "decision": "approved"}


def binding(pipe: dict[str, Any], intent: str = "retire") -> dict[str, Any]:
    """Bind both the raw source and complete canonical lifecycle record."""
    return {"source_key": pipe["key"], "source_sha256": pipe["source_record_sha256"],
            "expected_fingerprint": lifecycle_source_fingerprint(pipe), "intent": intent}


def representation(doc: SWMMDocument, pipe: dict[str, Any], identity: str = "R") -> dict[str, Any]:
    """Construct one explicit complete obsolete-representation review."""
    tokens = next(r.tokens for r in doc.records("CONDUITS") if r.tokens[0] == identity)
    return {"id": "review-" + identity, "model_link_id": identity,
            "model_record_sha256": lifecycle_record_fingerprint("CONDUITS", tokens),
            "source_pipes": [binding(pipe)], "complete_source_membership": True,
            "preserved_representations": [], **review_fields()}


def node_disposition(doc: SWMMDocument, identity: str, target: str | None = "B", action: str = "remove") -> dict[str, Any]:
    """Approve only a named native node and optional explicit load receiver."""
    tokens = next(r.tokens for r in doc.records("JUNCTIONS") if r.tokens[0] == identity)
    result = {"node_id": identity, "node_record_sha256": lifecycle_record_fingerprint("JUNCTIONS", tokens),
              "action": action, **review_fields()}
    if target is not None:
        result["transfer_to_node_id"] = target
    return result


def corridor(extra: str = "") -> tuple[SWMMDocument, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Retire a parallel corridor with two internal junctions and a kept bypass."""
    doc = document(extra, corridor=True)
    pipes = [source("p1", "A", "X"), source("p2", "X", "Y"), source("p3", "Y", "B")]
    representations = [representation(doc, pipe, "R" + str(i)) for i, pipe in enumerate(pipes, 1)]
    return doc, {"pipes": pipes}, representations, [node_disposition(doc, "X"), node_disposition(doc, "Y")]


def test_complete_retired_representation_yields_read_only_atomic_intent() -> None:
    """An explicit retired match produces only fingerprint-bound instructions."""
    doc, pipe = document(), source("retired")
    canonical = {"pipes": [pipe]}
    reviews = [representation(doc, pipe)]
    before = deepcopy((canonical, reviews)); data = doc.data
    result = plan_lifecycle_retirements(canonical, doc, representations=reviews, context_sha256="a" * 64)
    assert result["status"] == "ready" and result["executable_intent"]["delete_link_ids"] == ["R"]
    assert result["executable_intent"]["delete_node_ids"] == []
    assert result["baseline_sha256"] == sha256(data).hexdigest() and result["context_sha256"] == "a" * 64
    assert any(r["section"] == "XSECTIONS" for r in result["executable_intent"]["remove_link_metadata"])
    assert (canonical, reviews) == before and doc.data == data


@pytest.mark.parametrize("status", ["active", "unknown", "pending", "future"])
def test_unconfirmed_lifecycle_never_becomes_a_retirement(status: str) -> None:
    """A reviewed match or register Pending value is not lifecycle authority."""
    doc, pipe = document(), source("item", status=status)
    result = plan_lifecycle_retirements({"pipes": [pipe]}, doc, representations=[representation(doc, pipe)])
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("retirement_status_not_confirmed" in r for r in result["held"])


@pytest.mark.parametrize("change", ["incomplete", "proposed"])
def test_incomplete_or_unapproved_review_has_no_executable_intent(change: str) -> None:
    """All reviewed groups execute together or none execute."""
    doc, pipe = document(), source("item")
    row = representation(doc, pipe)
    row["complete_source_membership" if change == "incomplete" else "decision"] = False if change == "incomplete" else "proposed"
    result = plan_lifecycle_retirements({"pipes": [pipe]}, doc, representations=[row])
    assert result["status"] == "held" and result["executable_intent"] is None


@pytest.mark.parametrize("change", ["model", "raw_hash", "canonical_hash", "normalized_status", "raw_attributes"])
def test_stale_native_and_source_bindings_fail_closed(change: str) -> None:
    """Protect raw evidence and normalized lifecycle/orientation independently."""
    doc, pipe = document(), source("item")
    row = representation(doc, pipe)
    if change == "model": row["model_record_sha256"] = "0" * 64
    elif change == "raw_hash": row["source_pipes"][0]["source_sha256"] = "0" * 64
    elif change == "canonical_hash": row["source_pipes"][0]["expected_fingerprint"] = "0" * 64
    elif change == "normalized_status": pipe["status"] = "active"
    else: pipe["source_attributes"]["status"] = "active"
    with pytest.raises(ValueError, match="fingerprint|model record"):
        plan_lifecycle_retirements({"pipes": [pipe]}, doc, representations=[row])


@pytest.mark.parametrize("mode", ["existing", "rebuild", "exclude_scope", "missing"])
def test_active_part_of_aggregate_requires_explicit_preservation(mode: str) -> None:
    """Retirement of an aggregate does not change its active member's lifecycle."""
    doc = document()
    retired, active = source("old", "A", "X"), source("active", "X", "B", "active")
    row = representation(doc, retired)
    row["source_pipes"].append(binding(active, "preserve"))
    if mode != "missing":
        row["preserved_representations"] = [{"source_key": active["key"], "mode": mode,
            "model_link_id": "LIVE" if mode == "existing" else "NEW" if mode == "rebuild" else None,
            "from_node": "X", "to_node": "B", **review_fields()}]
    if mode == "exclude_scope":
        # Existing X->B would contradict a supposedly peripheral active stub.
        doc = SWMMDocument.from_bytes(doc.data.replace(b"LIVE X B 50 .013 0 0\n", b"").replace(b"LIVE CIRCULAR 1 0 0 0 1\n", b""))
    before = deepcopy(active)
    result = plan_lifecycle_retirements({"pipes": [retired, active]}, doc, representations=[row])
    assert result["status"] == ("held" if mode == "missing" else "ready")
    assert result["representations"][0]["action"] == "replace_aggregate"
    assert active == before and active["status"] == "active"
    if mode != "missing": assert result["executable_intent"]["preserved_representations"][0]["mode"] == mode
    if mode == "existing":
        evidence = result["representations"][0]["preservation_verification"][0]
        assert evidence["native"]["section"] == "CONDUITS"
        assert evidence["native"]["tokens"][3] == "50"
        assert evidence["native"]["sha256"] == lifecycle_record_fingerprint("CONDUITS", evidence["native"]["tokens"])
        assert evidence["xsections"][0]["tokens"][1:3] == ["CIRCULAR", "1"]


def test_native_pump_cannot_impersonate_preserved_source_pipe() -> None:
    """Matching endpoint identities do not establish matching asset families."""
    doc = document()
    doc = SWMMDocument.from_bytes(doc.data.replace(b"LIVE X B 50 .013 0 0\n", b"") + b"[PUMPS]\nLIVE X B CURVE OFF 0 0\n")
    retired, active = source("old", "A", "X"), source("active", "X", "B", "active")
    row = representation(doc, retired); row["source_pipes"].append(binding(active, "preserve"))
    row["preserved_representations"] = [{"source_key": active["key"], "model_link_id": "LIVE", "mode": "existing",
        "from_node": "X", "to_node": "B", **review_fields()}]
    result = plan_lifecycle_retirements({"pipes": [retired, active]}, doc, representations=[row])
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("preserved_existing_link_not_verified" in r for r in result["held"])


@pytest.mark.parametrize("change", ["wrong_orientation", "not_existing", "identity_collision", "unapproved", "load", "native_connection", "source_connection"])
def test_preservation_guards_prevent_silent_loss_or_false_existing_match(change: str) -> None:
    """A preserved member must have defensible geometry, scope and dependencies."""
    extra = '[INFLOWS]\nX FLOW "" FLOW 1 1 .001\n' if change == "load" else ""
    doc = document(extra)
    retired, active = source("old", "A", "X"), source("active", "X", "B", "active")
    row = representation(doc, retired); row["source_pipes"].append(binding(active, "preserve"))
    entry = {"source_key": active["key"], "mode": "existing", "model_link_id": "LIVE", "from_node": "X", "to_node": "B", **review_fields()}
    row["preserved_representations"] = [entry]
    pipes = [retired, active]
    if change == "wrong_orientation": entry["from_node"], entry["to_node"] = "B", "X"
    elif change == "not_existing": entry["model_link_id"] = "ABSENT"
    elif change == "identity_collision": entry.update(mode="rebuild", model_link_id="KEEP")
    elif change == "unapproved": entry["decision"] = "proposed"
    else:
        entry.update(mode="exclude_scope", model_link_id=None)
        if change != "native_connection":
            doc = SWMMDocument.from_bytes(doc.data.replace(b"LIVE X B 50 .013 0 0\n", b"").replace(b"LIVE CIRCULAR 1 0 0 0 1\n", b""))
        if change == "source_connection": pipes.append(source("incoming", "Y", "X", "active"))
    if change == "wrong_orientation":
        with pytest.raises(ValueError, match="orientation"):
            plan_lifecycle_retirements({"pipes": pipes}, doc, representations=[row])
    else:
        result = plan_lifecycle_retirements({"pipes": pipes}, doc, representations=[row])
        assert result["status"] == "held" and result["executable_intent"] is None


def test_compatible_native_flows_and_rdii_areas_are_preserved_exactly() -> None:
    """Retargeting preserves patterns/UH and adds native decimal quantities only."""
    extra = ('[DWF]\nX FLOW .01 WD WE\nY FLOW .02 WD WE\nB FLOW .03 WD WE\n'
             '[INFLOWS]\nX FLOW "" FLOW 1 1 .001\nY FLOW "" FLOW 1 1 .002\nB FLOW "" FLOW 1 1 .003\n'
             '[RDII]\nX UH 1.25\nY UH 2.5\nB UH 3.75\n')
    doc, canonical, reviews, dispositions = corridor(extra)
    before = deepcopy((canonical, reviews, dispositions)); data = doc.data
    result = plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
    assert result["status"] == "ready" and result["executable_intent"]["delete_node_ids"] == ["X", "Y"]
    groups = {r["section"]: r for r in result["executable_intent"]["load_transfers"]}
    assert {key: row["total_native"] for key, row in groups.items()} == {"DWF": "0.06", "INFLOWS": "0.006", "RDII": "7.50"}
    assert groups["DWF"]["tokens"][3:] == ["WD", "WE"] and groups["RDII"]["tokens"][1] == "UH"
    assert all(row["target_node"] == "B" and len(row["source_records"]) == 3 for row in groups.values())
    assert (canonical, reviews, dispositions) == before and doc.data == data


@pytest.mark.parametrize("change", ["missing", "proposed", "no_transfer", "bad_destination", "retired_destination", "stale_node", "extra_node"])
def test_orphan_removal_requires_exact_approved_load_destination(change: str) -> None:
    """No orphan or assigned model loading is silently discarded."""
    doc, canonical, reviews, dispositions = corridor('[INFLOWS]\nX FLOW "" FLOW 1 1 .001\n')
    if change == "missing": dispositions.pop(0)
    elif change == "proposed": dispositions[0]["decision"] = "proposed"
    elif change == "no_transfer": del dispositions[0]["transfer_to_node_id"]
    elif change == "bad_destination": dispositions[0]["transfer_to_node_id"] = "ABSENT"
    elif change == "retired_destination": dispositions[0]["transfer_to_node_id"] = "Y"
    elif change == "stale_node": dispositions[0]["node_record_sha256"] = "0" * 64
    else: dispositions.append(node_disposition(doc, "A"))
    if change in {"stale_node", "extra_node"}:
        with pytest.raises(ValueError): plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
    else:
        result = plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
        assert result["status"] == "held" and result["executable_intent"] is None


@pytest.mark.parametrize("extra", [
    '[DWF]\nX FLOW .01 WD\nY FLOW .02 OTHER\n',
    '[RDII]\nX UH1 1\nY UH2 2\n',
    '[INFLOWS]\nX FLOW SERIES FLOW 1 1 .001\n',
    '[DWF]\nX TSS 10 WD\n',
])
def test_incompatible_or_nonconstant_loads_are_held_not_combined(extra: str) -> None:
    """Different patterns, unit hydrographs or pollutant concentrations need review."""
    doc, canonical, reviews, dispositions = corridor(extra)
    result = plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("load_" in reason for reason in result["held"])


def test_duplicate_native_load_slot_is_not_summed_as_two_independent_sources() -> None:
    """A malformed duplicate native definition cannot manufacture extra flow."""
    doc, canonical, reviews, dispositions = corridor('[INFLOWS]\nX FLOW "" FLOW 1 1 .001\nX FLOW "" FLOW 1 1 .002\n')
    result = plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any("duplicate_native_load_slot" in reason for reason in result["held"])


@pytest.mark.parametrize("extra", [
    '[CONTROLS]\nRULE TEST\nIF NODE X DEPTH > 1\nTHEN PUMP R1 STATUS = OFF\n',
    '[SUBCATCHMENTS]\nS RG X 1 0 10 1 0\n',
    '[GROUNDWATER]\nS AQ X 0 0 0 0 0 0 0 0\n',
    '[LID_USAGE]\nS LID 1 1 1 0 0 0 FILE X\n',
    '[UNKNOWN_EXTENSION]\nS X payload\n',
])
def test_control_catchment_and_opaque_dependencies_block_node_removal(extra: str) -> None:
    """Unsupported reference grammars remain visible rather than being guessed."""
    doc, canonical, reviews, dispositions = corridor(extra)
    result = plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
    assert result["status"] == "held" and result["executable_intent"] is None
    assert any(r["family"] == "node" and r["asset_id"] == "X" for r in result["reference_inventory"])


def test_explicit_report_metadata_is_accounted_without_losing_native_values() -> None:
    """Report selections and geometry metadata can be explicitly cleaned by SHMM."""
    extra = '[REPORT]\nNODES X B\nLINKS R1 KEEP\n[COORDINATES]\nX 1 2\n[TAGS]\nNode X OLD\nLink R1 OLD\n'
    doc, canonical, reviews, dispositions = corridor(extra)
    result = plan_lifecycle_retirements(canonical, doc, representations=reviews, node_dispositions=dispositions)
    assert result["status"] == "ready"
    assert {r["section"] for r in result["executable_intent"]["remove_node_metadata"]} == {"REPORT", "COORDINATES", "TAGS"}
    assert {r["section"] for r in result["executable_intent"]["remove_link_metadata"]} == {"XSECTIONS", "REPORT", "TAGS"}


@pytest.mark.parametrize("change", ["duplicate_review", "duplicate_source", "missing_source", "unknown_schema", "invalid_date", "native_collision", "undeclared_endpoint"])
def test_malformed_or_ambiguous_inputs_are_rejected(change: str) -> None:
    """Reject collisions and stale schema before the caller can publish intents."""
    doc, pipe = document(), source("item")
    reviews = [representation(doc, pipe)]; canonical = {"pipes": [pipe]}
    if change == "duplicate_review": reviews.append(deepcopy(reviews[0]))
    elif change == "duplicate_source": canonical["pipes"].append(deepcopy(pipe))
    elif change == "missing_source": canonical["pipes"] = []
    elif change == "unknown_schema": reviews[0]["unexpected"] = True
    elif change == "invalid_date": reviews[0]["date"] = "21/10/2026"
    elif change == "native_collision": doc = SWMMDocument.from_bytes(doc.data.replace(b"KEEP A B", b"r A B"))
    else: doc = SWMMDocument.from_bytes(doc.data.replace(b"KEEP A B", b"KEEP A ABSENT"))
    with pytest.raises(ValueError): plan_lifecycle_retirements(canonical, doc, representations=reviews)


def test_empty_review_is_a_noop_and_invalid_context_hash_is_rejected() -> None:
    """The helper is opt-in and never discovers deletion targets on its own."""
    doc = document()
    result = plan_lifecycle_retirements({"pipes": []}, doc, representations=[])
    assert result["status"] == "ready" and result["executable_intent"]["delete_link_ids"] == []
    with pytest.raises(ValueError, match="context fingerprint"):
        plan_lifecycle_retirements({"pipes": []}, doc, representations=[], context_sha256="invalid")
