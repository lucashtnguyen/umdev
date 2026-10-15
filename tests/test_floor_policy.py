"""Synthetic tests for local, tiered and auditable utility floor inference.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained. Elevations are fictional metres; no project data is used.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from umdev.evidence import choose_assignment, resolve_ports
from umdev.parameters import prepare_network, prepare_network_from_dict
from umdev.records import network_from_dict


def policy(*, methods: list[str] | None=None, precedence: list[str] | None=None) -> dict[str, Any]:
    """Return explicit synthetic units and evidence families with no floor defaults."""
    node={"rim_methods":["structure_rim"]}
    if methods is not None:node["floor_methods"]=methods
    return {"model":{"target":{"link_offsets":"DEPTH","vertical_datum":"TEST_DATUM"}},
        "elevations":{"target_datum":"TEST_DATUM","allow_provisional":False,"transforms":[{"id":"same","source_datum":"TEST_DATUM","target_datum":"TEST_DATUM","method":"identity","verified":True,"basis":"Synthetic common elevation basis."}]},
        "ports":{"observations":[],"assignment":{"precedence":precedence if precedence is not None else ["reviewed_override","normalized_port","inline_port","pipe_numeric"],"direction":"hint","unique_role_diameter_fallback":{"enabled":True},"diameter_tolerance":{"value":0.001,"unit":"m"},"endpoint_tolerance":{"value":0.01,"unit":"m"},"direction_half_sector_deg":22.5,"elevation_conflict_tolerance":{"value":0.001,"unit":"m"},"numeric_text_conflict":"prefer_ranked_with_flag"}},
        "parameters":{"nodes":node,"pipes":{"length_methods":["attribute_length"],"diameter_methods":["attribute_diameter"],"shape":"CIRCULAR"}},
        "roughness":{"parameter":"manning_n","rules":{"PVC":{"aliases":["PVC"],"value":0.013}},"unknown_material":{"policy":"hold"}},"qa":{"rules":{}}}


def network() -> dict[str, Any]:
    """Return incoming C-to-A and outgoing A-to-B with distinct remote ends."""
    return {"nodes":[{"key":"u/n/"+identity,"asset_id":identity,"dataset":"structures","floor":None,"rim":200,"coordinates":xy,"vertical_datum":"TEST_DATUM","source_record_sha256":"a"*64} for identity,xy in [("A",[0,0]),("B",[10,0]),("C",[-10,0]),("D",[20,0])]],
        "pipes":[{"key":"u/p/I","asset_id":"I","from_node":"C","to_node":"A","diameter":0.3,"length":10,"upstream_invert":1,"downstream_invert":20,"coordinates":[[-10,0],[0,0]],"vertical_datum":"TEST_DATUM","material":"PVC","pipe_kind":"gravity","status":"active"},
                 {"key":"u/p/O","asset_id":"O","from_node":"A","to_node":"B","diameter":0.3,"length":10,"upstream_invert":30,"downstream_invert":5,"coordinates":[[0,0],[10,0]],"vertical_datum":"TEST_DATUM","material":"PVC","pipe_kind":"gravity","status":"active"}],"ports":[]}


def port(key: str, elevation: Any, *, role: str="in", node_id: str="A", datum: str | None="TEST_DATUM", diameter: float=9.9) -> dict[str, Any]:
    """Build a normalized structure-port observation, intentionally often unmatched."""
    return {"key":"u/ports/"+key,"structure_id":node_id,"role":role,"invert":elevation,"vertical_datum":datum,"diameter":diameter,"family":"normalized_port"}


def node(prepared: Any, identity: str="A") -> dict[str, Any]:
    """Find one prepared structure for independent engineering assertions."""
    return next(row for row in prepared.nodes if row["asset_id"]==identity)


def add_inline(canonical: dict[str, Any], config: dict[str, Any], field: str, text: Any, *, role: str="in", node_id: str="A") -> None:
    """Configure a described-port field without changing reusable core grammar."""
    next(row for row in canonical["nodes"] if row["asset_id"]==node_id)[field]=text
    config["ports"]["observations"].append({"id":field,"dataset":"structures","field":field,"parser":"elevation_direction_diameter_v1","role":role,"units":{"elevation":"m","diameter":"mm"}})


def test_structure_tier_uses_lowest_of_multiple_unmatched_ports_before_applied_ends():
    canonical=network();canonical["ports"]=[port("higher",50,role="out"),port("lower",40),port("middle",45)]
    prepared=prepare_network_from_dict(canonical,policy())
    floor=node(prepared)
    assert floor["floor_m"]==40
    assert floor["assignments"]["floor"]["method"]=="lowest_structure_port"
    # No port with 9.9m diameter matches the 0.3m links, but the structure
    # measurements still identify its lowest physical opening independently.
    assert prepared.pipes[0]["downstream_invert_m"]==20


def test_incoming_structure_port_lower_than_outgoing_defines_floor():
    canonical=network();config=policy()
    add_inline(canonical,config,"incoming_note","12 m, 300 mm",role="in")
    add_inline(canonical,config,"outgoing_note","14 m, 300 mm",role="out")
    assert node(prepare_network_from_dict(canonical,config))["floor_m"]==12


def test_normalized_port_table_supports_floor_without_pipe_reference_or_geometry_context():
    canonical=network();canonical["ports"]=[port("survey",7.25)]
    next(row for row in canonical["nodes"] if row["asset_id"]=="A")["coordinates"]=None
    prepared=prepare_network_from_dict(canonical,policy())
    assert node(prepared)["floor_m"]==7.25
    # Floor evidence survives independently, while missing map geometry remains
    # a separate completeness issue for new hydraulic objects.
    assert "unusable_point_geometry" in node(prepared)["reasons"]


@pytest.mark.parametrize("bad",[None,"unknown",float("nan"),float("inf"),True])
def test_invalid_structure_elevations_do_not_hide_usable_port(bad):
    canonical=network();canonical["ports"]=[port("bad",bad),port("good",10)]
    assert node(prepare_network_from_dict(canonical,policy()))["floor_m"]==10


def test_unknown_datum_structure_port_is_skipped_before_minimum():
    canonical=network();canonical["ports"]=[port("unknown",-100,datum="UNKNOWN"),port("good",12)]
    assert node(prepare_network_from_dict(canonical,policy()))["floor_m"]==12


def test_invalid_inline_text_does_not_hide_other_structure_opening():
    canonical=network();config=policy()
    add_inline(canonical,config,"ambiguous_note","several conflicting measurements")
    add_inline(canonical,config,"valid_note","15 m, 300 mm",role="out")
    assert node(prepare_network_from_dict(canonical,config))["floor_m"]==15


@pytest.mark.parametrize("minimum",[0,-2.5])
def test_zero_and_negative_structure_elevations_are_measured_values(minimum):
    canonical=network();canonical["ports"]=[port("minimum",minimum),port("higher",10)]
    assert node(prepare_network_from_dict(canonical,policy()))["floor_m"]==minimum


def test_applied_source_fallback_uses_local_incoming_end_not_remote_or_outgoing_only():
    prepared=prepare_network_from_dict(network(),policy(methods=["lowest_applied_pipe_end"]))
    assert node(prepared)["floor_m"]==20
    assignment=node(prepared)["assignments"]["floor"]
    assert assignment["method"]=="lowest_applied_pipe_end"
    # Incoming I has remoteUS1/localDS20; outgoing O has localUS30/remoteDS5.
    assert node(prepared)["floor_m"] not in [1,5,30]


def test_unselected_lower_pipe_end_cannot_donate_floor():
    canonical=network();canonical["pipes"].append({"key":"u/p/U","asset_id":"U","from_node":"A","to_node":"D","diameter":0.3,"length":20,"upstream_invert":-50,"downstream_invert":-100,"coordinates":[[0,0],[20,0]],"vertical_datum":"TEST_DATUM","material":"PVC","pipe_kind":"gravity","status":"active"})
    prepared=prepare_network_from_dict(canonical,policy(methods=["lowest_applied_pipe_end"]),selected_pipe_keys=["u/p/I","u/p/O"])
    assert node(prepared)["floor_m"]==20


def test_explicit_donor_filter_excludes_held_source_pipe_evidence():
    prepared=prepare_network_from_dict(network(),policy(methods=["lowest_applied_pipe_end"]),floor_donor_pipe_keys=["u/p/O"])
    assert node(prepared)["floor_m"]==30
    assert node(prepared)["assignments"]["floor"]["method"]=="lowest_applied_pipe_end"


@pytest.mark.parametrize("donors",[["u/p/missing"],["u/p/I"]])
def test_donor_keys_must_exist_and_be_in_selected_set(donors):
    with pytest.raises(ValueError):
        prepare_network_from_dict(network(),policy(),selected_pipe_keys=["u/p/O"],floor_donor_pipe_keys=donors)


def test_only_local_modeled_pipe_ends_can_supply_secondary_floor():
    canonical=network()
    for p in canonical["pipes"]:p["upstream_invert"]=p["downstream_invert"]=None
    model={"A":[{"model_id":"legacy-incoming","node_id":"A","end":"downstream","invert_m":16,"basis":"baseline_model","baseline_sha256":"b"*64},{"model_id":"legacy-outgoing","node_id":"A","end":"upstream","invert_m":18,"basis":"baseline_model","baseline_sha256":"b"*64}],"B":[{"model_id":"far-end","node_id":"B","end":"downstream","invert_m":-100,"basis":"baseline_model","baseline_sha256":"b"*64}]}
    prepared=prepare_network_from_dict(canonical,policy(methods=["lowest_applied_pipe_end"]),model_pipe_ends=model)
    assert node(prepared)["floor_m"]==16
    assert node(prepared,"B")["floor_m"]==-100
    assert "baseline_model" in str(node(prepared)["assignments"]["floor"]["evidence"])


def test_model_end_context_uses_bound_model_node_identity():
    canonical=network();next(n for n in canonical["nodes"] if n["asset_id"]=="A")["model_id"]="MODEL_A"
    model={"MODEL_A":[{"model_id":"legacy-incoming","node_id":"MODEL_A","end":"downstream","invert_m":17,"basis":"baseline_model","baseline_sha256":"b"*64}]}
    prepared=prepare_network_from_dict(canonical,policy(methods=["lowest_applied_pipe_end"]),floor_donor_pipe_keys=[],model_pipe_ends=model)
    assert node(prepared)["floor_m"]==17


@pytest.mark.parametrize("edit",[lambda r:r.update(node_id="OTHER"),lambda r:r.update(end="both"),lambda r:r.update(invert_m=float("nan")),lambda r:r.update(basis="assumed_remote_context")])
def test_invalid_modeled_end_context_is_rejected(edit):
    record={"model_id":"legacy","node_id":"A","end":"downstream","invert_m":10,"basis":"baseline_model","baseline_sha256":"b"*64};edit(record)
    with pytest.raises(ValueError):
        prepare_network_from_dict(network(),policy(),model_pipe_ends={"A":[record]})


def test_reviewed_floor_assignment_precedes_all_minimum_rules():
    canonical=network();canonical["ports"]=[port("lower",5)]
    config=policy();config["review"]={"overrides":[{"source_key":"u/n/A","field":"floor","value":9,"unit":"m","vertical_datum":"TEST_DATUM","evidence_sha256":"a"*64,"rationale":"Synthetic approved floor after drop connection review.","reviewer":"Synthetic engineer","date":"2026-10-15"}]}
    floor=node(prepare_network_from_dict(canonical,config))
    assert floor["floor_m"]==9 and floor["assignments"]["floor"]["method"]=="reviewed_override"


def test_existing_floor_is_preserved_while_policy_proposal_is_reported():
    canonical=network();canonical["ports"]=[port("survey",12)]
    prepared=prepare_network_from_dict(canonical,policy(),baseline_nodes={"A":{"floor_m":15,"rim_m":200,"model_section":"JUNCTIONS"}})
    floor=node(prepared)
    assert floor["floor_m"]==15
    assert floor["proposed_floor_assignment"]["value"]==12
    assert floor["proposed_floor_assignment"]["method"]=="lowest_structure_port"
    assert any(f["rule"]=="existing_floor_differs_from_policy" and f["object_key"]=="u/n/A" for f in prepared.findings)


def test_existing_floor_equal_within_tolerance_has_no_discrepancy_finding():
    canonical=network();canonical["ports"]=[port("survey",12)]
    prepared=prepare_network_from_dict(canonical,policy(),baseline_nodes={"A":{"floor_m":12.0001,"rim_m":200}})
    assert not any(f["rule"]=="existing_floor_differs_from_policy" and f["object_key"]=="u/n/A" for f in prepared.findings)


def test_no_floor_evidence_remains_missing_instead_of_forced_zero():
    canonical=network()
    for p in canonical["pipes"]:p["upstream_invert"]=p["downstream_invert"]=None
    prepared=prepare_network_from_dict(canonical,policy())
    assert node(prepared)["floor_m"] is None
    assert "missing_required_floor" in node(prepared)["reasons"]


def test_default_floor_chain_excludes_legacy_structure_floor_scalar():
    canonical=network();next(n for n in canonical["nodes"] if n["asset_id"]=="A")["floor"]=-500
    canonical["ports"]=[port("measured_opening",25)]
    assert node(prepare_network_from_dict(canonical,policy()))["floor_m"]==25


def test_disabled_numeric_family_cannot_win_supply_fallback_or_emit_conflict():
    canonical=network();config=policy(precedence=["inline_port"])
    # Raw numeric end elevations are lower but their family is disabled.
    add_inline(canonical,config,"outgoing_note","40 m, 300 mm",role="out")
    add_inline(canonical,config,"receiver_note","39 m, 300 mm",role="in",node_id="B")
    assignments,_=resolve_ports(network_from_dict(canonical),config)
    outgoing=assignments[("u/p/O","upstream")]
    assert outgoing.value==40 and "conflicting_elevation_evidence" not in outgoing.flags
    assert not any(a.get("family")=="pipe_numeric" for a in outgoing.alternatives)
    prepared=prepare_network_from_dict(canonical,config)
    assert node(prepared)["floor_m"]==40
    assert node(prepared,"C")["floor_m"] is None


def test_disabled_numeric_only_candidate_does_not_become_unlisted_fallback():
    candidate={"value":-100,"source_key":"u/p/raw","family":"pipe_numeric","method":"pipe_numeric","category":"observed"}
    assignment=choose_assignment([candidate],precedence=["inline_port"],tolerance=0.001)
    assert assignment.value is None and not assignment.evidence and not assignment.alternatives


def test_disabled_numeric_low_value_does_not_flag_a_conflict_with_enabled_family():
    assignment=choose_assignment([{"value":1,"source_key":"u/p/raw","family":"pipe_numeric","method":"pipe_numeric","category":"observed"},{"value":10,"source_key":"u/notes/valid","family":"inline_port","method":"inline_port","category":"interpreted"}],precedence=["inline_port"],tolerance=0.001)
    assert assignment.value==10 and assignment.category=="interpreted"
    assert "conflicting_elevation_evidence" not in assignment.flags
    assert len(assignment.evidence)==1 and not assignment.alternatives


def test_public_network_and_dict_entry_points_match_without_mutating_input():
    canonical=network();canonical["ports"]=[port("survey",12)]
    original=deepcopy(canonical);config=policy()
    direct=prepare_network(network_from_dict(canonical),config,floor_donor_pipe_keys=["u/p/O"])
    mapped=prepare_network_from_dict(canonical,config,floor_donor_pipe_keys=["u/p/O"])
    assert direct.to_dict()==mapped.to_dict() and canonical==original



def test_vertical_normalization_occurs_before_selecting_lowest_structure_port():
    canonical=network();canonical["ports"]=[port("raw_low_but_shifted",1,datum="ALTERNATE"),port("target_low",50)]
    config=policy();config["elevations"]["transforms"].append({"id":"synthetic_shift","source_datum":"ALTERNATE","target_datum":"TEST_DATUM","method":"constant_offset","offset":{"value":100,"unit":"m"},"verified":True,"basis":"Fictional surveyed shift for regression."})
    floor=node(prepare_network_from_dict(canonical,config))
    assert floor["floor_m"]==50
    assert "101.0" in str(floor["assignments"]["floor"]["evidence"])


@pytest.mark.parametrize("mode",["port_status","referenced_pipe_status"])
def test_explicit_inactive_context_cannot_lower_structure_floor(mode):
    canonical=network();canonical["ports"]=[port("excluded",-100),port("current",12)]
    config=policy();config["ports"]["assignment"]["context_exclude_statuses"]=["abandoned"]
    if mode=="port_status":canonical["ports"][0]["status"]="abandoned"
    else:
        canonical["ports"][0]["pipe_id"]="I"
        canonical["pipes"][0]["status"]="abandoned"
    assert node(prepare_network_from_dict(canonical,config))["floor_m"]==12


def test_held_model_end_context_is_not_a_floor_donor():
    canonical=network();model={"A":[{"model_id":"held","node_id":"A","end":"downstream","invert_m":-100,"basis":"baseline_model","baseline_sha256":"b"*64,"disposition":"hold"},{"model_id":"current","node_id":"A","end":"upstream","invert_m":17,"basis":"baseline_model","baseline_sha256":"b"*64}]}
    prepared=prepare_network_from_dict(canonical,policy(methods=["lowest_applied_pipe_end"]),floor_donor_pipe_keys=[],model_pipe_ends=model)
    assert node(prepared)["floor_m"]==17


def test_case_insensitive_model_context_collision_is_rejected():
    record={"model_id":"existing","node_id":"A","end":"downstream","invert_m":10,"basis":"baseline_model","baseline_sha256":"b"*64}
    with pytest.raises(ValueError,match="identity collision"):
        prepare_network_from_dict(network(),policy(),model_pipe_ends={"A":[record],"a":[record]})


@pytest.mark.parametrize("fingerprint",[None,"not-a-fingerprint"])
def test_model_end_context_requires_a_baseline_fingerprint(fingerprint):
    record={"model_id":"existing","node_id":"A","end":"downstream","invert_m":10,"basis":"baseline_model","baseline_sha256":fingerprint}
    with pytest.raises(ValueError):
        prepare_network_from_dict(network(),policy(),model_pipe_ends={"A":[record]})
