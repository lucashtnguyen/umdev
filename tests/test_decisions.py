"""Synthetic regression checks for durable engineering-decision ledgers.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained. All utility identities, observations and review records are fictional.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest
from hymo import SWMMDocument

from umdev.decisions import resolve_decision_ledger

BASELINE=b"""; Synthetic ledger baseline
[OPTIONS]
FLOW_UNITS CFS
LINK_OFFSETS DEPTH
[JUNCTIONS]
UP 10 5
DN 9 5
[CONDUITS]
L-01 UP DN 100 0.013 0 0
[XSECTIONS]
L-01 CIRCULAR 1 0 0 0 1
[COORDINATES]
UP 0 0
DN 100 0
"""


@pytest.fixture
def case(tmp_path: Path) -> dict[str, Any]:
    """Provide independent source/structure hashes and a read-only baseline."""
    def observation(key: str, **fields: Any) -> dict[str, Any]:
        raw={"key":key,**fields}
        return {**raw,"source_record_sha256":sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()}
    up=observation("utility/access/0001",asset_id="0001",rim=15,raw_notes="Measured upstream connection")
    down=observation("utility/access/0002",asset_id="0002",rim=14,raw_notes="Measured downstream connection")
    pipe=observation("utility/lines/0007",asset_id="0007",from_node="0001",to_node="0002",diameter=0.3048)
    report=tmp_path/"review_evidence.txt";report.write_text("Fictional accepted profile review.\n",encoding="utf-8")
    return {"canonical":{"nodes":[up,down],"pipes":[pipe]},
            "config":{"selection":{"scenario":"existing"},"scenarios":{"existing":{},"future":{}},"model":{"target":{"length_unit":"ft"}}},
            "baseline":SWMMDocument.from_bytes(BASELINE),"report":report,"path":tmp_path/"decisions.json"}


def entry(case: dict[str, Any], identity: str="decision-1", field: str="upstream_invert") -> dict[str, Any]:
    """Create one approved source decision with pipe and endpoint evidence."""
    canonical=case["canonical"]
    key=canonical["nodes"][0]["key"] if field in {"floor","rim"} else canonical["pipes"][0]["key"]
    return {"id":identity,"status":"approved","kind":"parameter_override","scenario":"existing",
            "recorded_by":"Synthetic maintainer","recorded_date":"2026-10-15",
            "authorization":{"basis":"explicit_instruction","reference":"Synthetic review approval"},
            "rationale":"Assign the uniquely reviewed connection while retaining original observations.",
            "evidence":[{"source_key":r["key"],"sha256":r["source_record_sha256"]} for r in [*canonical["nodes"],*canonical["pipes"]]],
            "override":{"source_key":key,"field":field,"value":11 if field not in {"diameter","length"} else 2,"unit":"ft",**({"vertical_datum":"SYNTHETIC_DATUM"} if field not in {"diameter","length"} else {})}}


def finished(case: dict[str, Any], identity: str="finished-1", section: str="CONDUITS") -> dict[str, Any]:
    """Create a dimension edit with exact baseline identity and old-value guard."""
    row=entry(case,identity);row["kind"]="model_update";row.pop("override")
    field="length" if section=="CONDUITS" else "geom1"
    row.update(baseline_sha256=sha256(case["baseline"].data).hexdigest(),
               change={"action":"update","section":section,"object_id":"l-01","values":{field:125 if field=="length" else 1.25},"expected":{field:100 if field=="length" else 1},"source_key":"reviewed/authority"},
               units={field:"ft"})
    row["evidence"].append({"path":case["report"].name,"sha256":sha256(case["report"].read_bytes()).hexdigest()})
    return row


def resolve(case: dict[str, Any], rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Write only a synthetic ledger and resolve its frozen instructions."""
    case["path"].write_text(json.dumps({"schema_version":1,"kind":"umdev_manual_decision_ledger","decisions":rows}),encoding="utf-8")
    return resolve_decision_ledger(case["path"],case["canonical"],case["config"],case["baseline"],**kwargs)


def test_approved_override_preserves_units_datum_and_authorization_provenance(case):
    row=entry(case);row["authorization"].update(reviewer="Fictional engineer",date="2026-10-14")
    result=resolve(case,[row]);value=result["overrides"][0]
    assert value["source_key"]=="utility/lines/0007"
    assert value["value"]==11 and value["unit"]=="ft"
    assert value["vertical_datum"]=="SYNTHETIC_DATUM"
    assert value["reviewer"]=="Fictional engineer" and value["date"]=="2026-10-14"
    assert value["date_basis"]=="authorization_date" and value["decision_id"]=="decision-1"
    assert value["evidence_sha256"]==case["canonical"]["pipes"][0]["source_record_sha256"]
    assert result["audit"]["summary"]["ready"]==1


def test_inherited_authorization_keeps_recording_date_distinct(case):
    value=resolve(case,[entry(case)])["overrides"][0]
    assert value["date_basis"]=="recording_date"
    assert value["reviewer"]=="Inherited authorization; recorded by Synthetic maintainer"


@pytest.mark.parametrize("section,field,old,new",[("CONDUITS","length",100,125),("XSECTIONS","geom1",1,1.25)])
def test_finished_dimensions_use_baseline_units_and_unique_instruction_key(case,section,field,old,new):
    row=finished(case,section=section);result=resolve(case,[row]);change=result["changes"][0]
    assert change["expected"]=={field:old} and change["values"]=={field:new}
    assert change["source_key"]=="decision/finished-1"
    assert change["object_id"]=="l-01"
    evidence=result["evidence"][change["source_key"]]
    assert evidence["original_instruction_source_key"]=="reviewed/authority"
    assert evidence["decision"]["units"][field]=="ft"
    assert str(case["report"]) in result["side_input_hashes"]


@pytest.mark.parametrize("index",[0,1,2])
def test_stale_pipe_or_either_structure_evidence_blocks_active_override(case,index):
    row=entry(case)
    records=[*case["canonical"]["nodes"],*case["canonical"]["pipes"]]
    records[index]["source_record_sha256"]="f"*64
    with pytest.raises(ValueError,match="stale|source evidence"):
        resolve(case,[row])


@pytest.mark.parametrize("field,node_index",[("upstream_invert",0),("downstream_invert",1)])
def test_endpoint_assignment_requires_structure_fingerprint_even_when_pipe_is_unchanged(case,field,node_index):
    row=entry(case,field=field);node=case["canonical"]["nodes"][node_index]
    row["evidence"]=[ref for ref in row["evidence"] if ref.get("source_key")!=node["key"]]
    with pytest.raises(ValueError,match="endpoint|structure"):
        resolve(case,[row])


def test_override_requires_own_source_fingerprint(case):
    row=entry(case);key=row["override"]["source_key"]
    row["evidence"]=[ref for ref in row["evidence"] if ref.get("source_key")!=key]
    with pytest.raises(ValueError,match="own source"):
        resolve(case,[row])


def test_relative_evidence_file_hash_is_consumed_and_stale_file_blocks(case):
    row=finished(case)
    result=resolve(case,[row])
    assert result["side_input_hashes"][str(case["report"])]==sha256(case["report"].read_bytes()).hexdigest()
    case["report"].write_text("Changed fictional evidence.",encoding="utf-8")
    with pytest.raises(ValueError,match="stale file"):
        resolve(case,[row])


def test_missing_evidence_file_is_an_error(case):
    row=finished(case);case["report"].unlink()
    with pytest.raises(ValueError,match="missing evidence file"):
        resolve(case,[row])


def test_baseline_fingerprint_change_is_detected_before_finished_update(case):
    row=finished(case);case["baseline"]=SWMMDocument.from_bytes(BASELINE+b"; independently changed baseline\n")
    with pytest.raises(ValueError,match="stale baseline"):
        resolve(case,[row])


@pytest.mark.parametrize("status",["proposed","rejected","archived"])
def test_nonapproved_history_is_retained_without_executing_or_checking_stale_files(case,status):
    row=finished(case);row["status"]=status;case["report"].unlink()
    result=resolve(case,[row])
    assert not result["changes"] and not result["overrides"]
    assert result["audit"]["decisions"][0]["status"]=="not_approved"
    assert result["snapshot"]["decisions"][0]["status"]==status


def test_approved_future_decision_is_deferred_and_retained(case):
    row=finished(case);row["scenario"]="future";case["report"].unlink()
    result=resolve(case,[row])
    assert not result["changes"]
    assert result["audit"]["summary"]["deferred_scenario"]==1


def test_override_outside_selected_pipe_and_endpoint_scope_is_recorded_but_inactive(case):
    row=entry(case);result=resolve(case,[row],selected_pipe_keys=[])
    assert result["audit"]["summary"]["outside_selected_scope"]==1
    assert not result["overrides"]


def test_selected_pipe_keeps_endpoint_structure_override_active(case):
    result=resolve(case,[entry(case,field="rim")],selected_pipe_keys=["utility/lines/0007"])
    assert result["overrides"][0]["source_key"]=="utility/access/0001"


def test_unknown_selected_pipe_key_is_not_silently_treated_as_empty_scope(case):
    with pytest.raises(ValueError,match="[Ss]elected|[Uu]nknown"):
        resolve(case,[entry(case)],selected_pipe_keys=["utility/lines/typo"])


def test_approved_record_only_reviews_validate_evidence_without_executing(case):
    row=entry(case);row.pop("override");row.update(kind="review_record",record={"outcome":"defer_pressure_route","reason":"Synthetic contract not yet supported."})
    result=resolve(case,[row])
    assert result["audit"]["summary"]["record_only"]==1
    assert not result["overrides"] and not result["changes"]
    case["canonical"]["pipes"][0]["source_record_sha256"]="f"*64
    with pytest.raises(ValueError,match="stale"):
        resolve(case,[row])


def test_approved_supersession_preserves_history_and_executes_only_successor(case):
    first=entry(case,"first");first["evidence"][0]["sha256"]="f"*64
    second=entry(case,"second");second["supersedes"]="first";second["override"]["value"]=12
    result=resolve(case,[first,second])
    assert len(result["overrides"])==1 and result["overrides"][0]["value"]==12
    assert result["audit"]["summary"]["superseded"]==1
    assert len(result["snapshot"]["decisions"])==2


def test_proposed_successor_cannot_retire_an_approved_assignment(case):
    first=entry(case,"first");second=entry(case,"second");second.update(status="proposed",supersedes="first")
    result=resolve(case,[first,second])
    assert result["overrides"][0]["decision_id"]=="first"
    assert result["audit"]["summary"]["superseded"]==0


@pytest.mark.parametrize("alter",["target","scenario","missing","self","cycle","unapproved_previous"])
def test_invalid_supersession_cannot_change_history(case,alter):
    first=entry(case,"first");second=entry(case,"second");second["supersedes"]="first"
    if alter=="target":second["override"]["field"]="downstream_invert"
    elif alter=="scenario":second["scenario"]="future"
    elif alter=="missing":second["supersedes"]="missing"
    elif alter=="self":second["supersedes"]="second"
    elif alter=="cycle":first["supersedes"]="second"
    else:first["status"]="proposed"
    with pytest.raises(ValueError,match="supersession"):
        resolve(case,[first,second])


def test_unlinked_conflicting_approvals_fail_even_if_values_happen_to_equal(case):
    with pytest.raises(ValueError,match="Conflicting active"):
        resolve(case,[entry(case,"first"),entry(case,"second")])


def test_finished_target_case_aliases_conflict(case):
    first=finished(case,"first");second=finished(case,"second");second["change"]["object_id"]="L-01"
    with pytest.raises(ValueError,match="Conflicting active"):
        resolve(case,[first,second])


def test_duplicate_decision_ids_are_rejected(case):
    row=entry(case)
    with pytest.raises(ValueError,match="Duplicate decision id"):
        resolve(case,[row,deepcopy(row)])


def test_duplicate_json_keys_are_rejected_before_resolution(case):
    case["path"].write_text('{"schema_version":1,"schema_version":2,"kind":"umdev_manual_decision_ledger","decisions":[]}',encoding="utf-8")
    with pytest.raises(ValueError,match="Duplicate JSON key"):
        resolve_decision_ledger(case["path"],case["canonical"],case["config"],case["baseline"])


@pytest.mark.parametrize("field,entity",[("floor","pipe"),("rim","pipe"),("diameter","node"),("upstream_invert","node")])
def test_override_field_must_match_source_entity(case,field,entity):
    row=entry(case,field=field)
    row["override"]["source_key"]=case["canonical"]["pipes" if entity=="pipe" else "nodes"][0]["key"]
    with pytest.raises(ValueError,match="incompatible.*entity"):
        resolve(case,[row])


@pytest.mark.parametrize("unit",["m","in","us_survey_ft"])
def test_finished_dimensions_cannot_silently_convert_from_other_units(case,unit):
    row=finished(case);row["units"]["length"]=unit
    with pytest.raises(ValueError,match="finished units differ"):
        resolve(case,[row])


def test_wrong_old_value_precondition_blocks_update(case):
    row=finished(case);row["change"]["expected"]["length"]=99
    with pytest.raises(ValueError,match="old-value precondition"):
        resolve(case,[row])


def test_diameter_update_cannot_change_an_existing_noncircular_shape(case):
    row=finished(case,section="XSECTIONS")
    case["baseline"]=SWMMDocument.from_bytes(BASELINE.replace(b"L-01 CIRCULAR",b"L-01 RECT_OPEN"))
    row["baseline_sha256"]=sha256(case["baseline"].data).hexdigest()
    with pytest.raises(ValueError,match="circular shape"):
        resolve(case,[row])


@pytest.mark.parametrize("alter",["unknown_unit","zero","negative","nonfinite","missing_datum","wrong_payload","unknown_field","missing_authority","bad_date"])
def test_invalid_decision_contracts_are_explicit_errors(case,alter):
    row=entry(case)
    if alter=="unknown_unit":row["override"]["unit"]="furlong"
    elif alter=="zero":row["override"].update(field="diameter",value=0)
    elif alter=="negative":row["override"].update(field="length",value=-1)
    elif alter=="nonfinite":row["override"]["value"]="nan"
    elif alter=="missing_datum":row["override"].pop("vertical_datum")
    elif alter=="wrong_payload":row["record"]={"unexpected":True}
    elif alter=="unknown_field":row["override"]["field"]="unregistered_parameter"
    elif alter=="missing_authority":row.pop("authorization")
    else:row["recorded_date"]="not-a-date"
    with pytest.raises(ValueError):resolve(case,[row])


def test_resolution_is_deterministic_and_preserves_canonical_baseline_and_files(case):
    rows=[entry(case),finished(case)]
    first=resolve(case,rows)
    canonical=deepcopy(case["canonical"]);baseline=case["baseline"].data
    ledger=case["path"].read_bytes();report=case["report"].read_bytes()
    second=resolve_decision_ledger(case["path"],case["canonical"],case["config"],case["baseline"])
    assert first==second
    assert case["canonical"]==canonical and case["baseline"].data==baseline
    assert case["path"].read_bytes()==ledger and case["report"].read_bytes()==report
    first["snapshot"]["decisions"][0]["rationale"]="changed returned copy"
    assert json.loads(case["path"].read_bytes())["decisions"][0]["rationale"]==rows[0]["rationale"]



@pytest.mark.parametrize("mode",["missing","ambiguous"])
def test_pipe_end_requires_one_actual_structure_identity(case,mode):
    row=entry(case)
    if mode=="missing":case["canonical"]["pipes"][0]["from_node"]="unknown-structure"
    else:
        duplicate=deepcopy(case["canonical"]["nodes"][0]);duplicate["key"]="utility/other_access/0001"
        case["canonical"]["nodes"].append(duplicate)
        row["evidence"].append({"source_key":duplicate["key"],"sha256":duplicate["source_record_sha256"]})
    with pytest.raises(ValueError,match="unique endpoint structure"):
        resolve(case,[row])


def test_canonical_source_key_collision_is_rejected(case):
    row=entry(case);case["canonical"]["nodes"][1]["key"]=case["canonical"]["nodes"][0]["key"]
    with pytest.raises(ValueError,match="source keys must be unique"):
        resolve(case,[row])


@pytest.mark.parametrize("mode",["missing","nonunique"])
def test_finished_target_must_exist_exactly_once(case,mode):
    row=finished(case)
    if mode=="missing":row["change"]["object_id"]="NO_SUCH_CONDUIT"
    else:
        case["baseline"]=SWMMDocument.from_bytes(BASELINE.replace(b"[XSECTIONS]",b"l-01 UP DN 100 0.013 0 0\n[XSECTIONS]"))
        row["baseline_sha256"]=sha256(case["baseline"].data).hexdigest()
    with pytest.raises(ValueError,match="target missing or nonunique"):
        resolve(case,[row])


@pytest.mark.parametrize("field,value,unit",[("length",300,"mm"),("diameter",12,"in"),("floor",-2,"m"),("rim",0,"ft")])
def test_override_units_and_valid_zero_or_negative_elevations_remain_explicit(case,field,value,unit):
    row=entry(case,field=field);row["override"].update(value=value,unit=unit)
    result=resolve(case,[row])
    assert result["overrides"][0]["value"]==value and result["overrides"][0]["unit"]==unit


def test_dimension_only_override_needs_own_pipe_not_unrelated_endpoint_evidence(case):
    row=entry(case,field="diameter");key=row["override"]["source_key"]
    row["evidence"]=[ref for ref in row["evidence"] if ref.get("source_key")==key]
    assert resolve(case,[row])["audit"]["override_count"]==1


def test_higher_order_unknown_contract_fields_fail_closed(case):
    row=entry(case);row["unexpected_policy"]="silently_apply"
    with pytest.raises(ValueError,match="unknown keys"):
        resolve(case,[row])
