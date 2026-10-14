"""Read-only existing storage endpoints with synthetic profiles and geometry.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained; no project data, coordinates or assumptions are included.
"""

from copy import deepcopy
from pathlib import Path

import pytest
from hymo import SWMMDocument
from shmm import preview_changes

from umdev.configuration import load_config
from umdev.models import baseline_node_parameters, compile_handoff, instructions_from_handoff, verify_prepared_profiles
from umdev.parameters import prepare_network_from_dict

FIXTURES=Path(__file__).parent/"fixtures/schema_equivalence"
BASELINE="""; Synthetic storage test, preserve all original rows verbatim.
[OPTIONS]
FLOW_UNITS LPS
LINK_OFFSETS DEPTH
[JUNCTIONS]
[STORAGE]
Store 50 8 1.2 TABULAR AreaCurve 0.1 0.2 ; retained non-profile tokens
[OUTFALLS]
OUT 40 FREE NO
[PUMPS]
P1 Store OUT PumpCurve OFF 2 1
[CURVES]
AreaCurve STORAGE 0 10
AreaCurve 8 20
PumpCurve PUMP3 0 2
PumpCurve 10 0
[COORDINATES]
Store 10 0
OUT 20 0
[REPORT]
NODES ALL
LINKS ALL
"""


def setup_case(reverse=False,baseline_text=BASELINE):
    """Build synthetic SI source observations and a preserved model profile."""
    config=load_config(FIXTURES/"utility_a.config.json").data
    config["model"]["baseline_metadata"]={"horizontal_crs":{"epsg":3857},"map_unit":"m","vertical_datum":"SYNTHETIC_DATUM"}
    config["parameters"]["nodes"]["update_existing_rims"]=True
    canonical={"nodes":[
        {"key":"s/n/new","asset_id":"NEW","dataset":"structures","floor":55 if not reverse else 45,"rim":65 if not reverse else 55,"coordinates":[0,0],"vertical_datum":"SYNTHETIC_DATUM"},
        {"key":"s/n/store","asset_id":"Store","dataset":"structures","floor":999,"rim":1000,"coordinates":[10,0],"vertical_datum":"SYNTHETIC_DATUM"}],
        "pipes":[{"key":"s/p/new","asset_id":"NEW_PIPE","from_node":"Store" if reverse else "NEW","to_node":"NEW" if reverse else "Store","diameter":0.3,"length":10,"upstream_invert":51 if reverse else 56,"downstream_invert":46 if reverse else 51,"coordinates":[[10,0],[0,0]] if reverse else [[0,0],[10,0]],"vertical_datum":"SYNTHETIC_DATUM","material":"PVC","pipe_kind":"gravity","status":"active"}],"ports":[]}
    document=SWMMDocument.from_bytes(baseline_text.encode())
    prepared=prepare_network_from_dict(canonical,config,baseline_nodes=baseline_node_parameters(document,config)).to_dict()
    return document,config,canonical,prepared


@pytest.mark.parametrize("shape", ["TABULAR AreaCurve 0.1 0.2", "FUNCTIONAL 10 0 1 0.1 0.2"])
def test_storage_numeric_profile_is_read_without_interpreting_curve_or_other_tokens(shape):
    document=SWMMDocument.from_bytes(BASELINE.replace("TABULAR AreaCurve 0.1 0.2",shape).encode())
    config=load_config(FIXTURES/"utility_a.config.json").data
    profile=baseline_node_parameters(document,config)["Store"]
    assert profile=={"floor_m":50,"rim_m":58,"max_depth_m":8,"model_section":"STORAGE","model_id":"Store"}
    assert "OUT" not in baseline_node_parameters(document,config)


@pytest.mark.parametrize("reverse", [False,True])
def test_gravity_storage_reference_preserves_cards_curves_geometry_and_profile(reverse):
    document,config,canonical,prepared=setup_case(reverse)
    original=deepcopy(canonical)
    storage=next(n for n in prepared["nodes"] if n["asset_id"]=="Store")
    assert storage["is_existing"] and storage["model_section"]=="STORAGE"
    assert storage["floor_m"]==50 and storage["rim_m"]==58
    assert storage["assignments"]["rim"]["method"]=="preserved_baseline_rim"
    assert storage["assignments"]["floor"]["evidence"][0]["model_section"]=="STORAGE"
    assert not prepared["held"]
    pipe=prepared["pipes"][0]
    assert pipe["inlet_offset_m" if reverse else "outlet_offset_m"]==1
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    assert not handoff["application_holds"]
    changes,vertices=instructions_from_handoff(handoff)
    assert all(change.section!="STORAGE" for change in changes)
    assert not any(change.object_id=="Store" and change.section in {"JUNCTIONS","COORDINATES"} for change in changes)
    candidate=SWMMDocument.from_bytes(preview_changes(document,changes,vertex_changes=vertices).data)
    for section in ["STORAGE","CURVES","PUMPS","OUTFALLS"]:
        assert [candidate.lines[row.line_index] for row in candidate.records(section)]==[document.lines[row.line_index] for row in document.records(section)]
    assert next(r.tokens for r in candidate.records("COORDINATES") if r.tokens[0]=="Store")==("Store","10","0")
    verification=verify_prepared_profiles(candidate,prepared,handoff,config)
    assert verification["checked_nodes"]==2 and verification["checked_pipes"]==1
    assert canonical==original


def test_existing_storage_cannot_be_edited_through_prepared_floor_or_rim():
    document,config,_,prepared=setup_case()
    node=next(n for n in prepared["nodes"] if n["asset_id"]=="Store")
    node["rim_m"]=59;node["max_depth_m"]=9
    with pytest.raises(NotImplementedError,match="STORAGE floor/rim"):
        compile_handoff(document,prepared,config,config_sha256="synthetic")


def test_zero_depth_storage_holds_node_and_pipe_instead_of_inventing_profile():
    document,config,_,prepared=setup_case(baseline_text=BASELINE.replace("Store 50 8 1.2","Store 50 0 0"))
    node=next(n for n in prepared["nodes"] if n["asset_id"]=="Store")
    assert node["disposition"]=="hold" and "rim_not_above_floor" in node["reasons"]
    assert prepared["pipes"][0]["disposition"]=="hold"
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    assert not handoff["applied_source_keys"]


@pytest.mark.parametrize("extra", ["[JUNCTIONS]\nstore 1 2\n","[STORAGE]\nSTORE 1 2 0 TABULAR AreaCurve\n","[OUTFALLS]\nsToRe 1 FREE\n"])
def test_case_insensitive_node_namespace_collisions_are_rejected(extra):
    section,record=extra.split("\n",1)
    document=SWMMDocument.from_bytes(BASELINE.replace(section+"\n",section+"\n"+record).encode())
    config=load_config(FIXTURES/"utility_a.config.json").data
    with pytest.raises(ValueError,match="identity collision"):
        baseline_node_parameters(document,config)


def test_storage_readback_detects_mutated_depth():
    document,config,_,prepared=setup_case()
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    changes,vertices=instructions_from_handoff(handoff)
    result=preview_changes(document,changes,vertex_changes=vertices)
    mutated=SWMMDocument.from_bytes(result.data.replace(b"Store 50 8 1.2",b"Store 50 9 1.2"))
    with pytest.raises(ValueError,match="rim"):
        verify_prepared_profiles(mutated,prepared,handoff,config)


def test_pressure_remains_unsupported_even_if_forged_prepared_include():
    document,config,_,prepared=setup_case()
    prepared["pipes"][0]["pipe_kind"]="force_main"
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    assert handoff["application_holds"][0]["reason"]=="unsupported_pressure_or_parameter_contract"
    assert not handoff["applied_source_keys"]


def test_existing_storage_without_baseline_map_point_is_held():
    document,config,_,prepared=setup_case(baseline_text=BASELINE.replace("Store 10 0\n",""))
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    assert handoff["application_holds"][0]["reason"]=="existing_storage_endpoint_requires_baseline_coordinates"


def test_existing_outfall_endpoint_remains_unsupported():
    document,config,_,prepared=setup_case()
    prepared["nodes"][1]["model_id"]="OUT"
    # Select the source endpoint explicitly; do not manufacture an outfall rim.
    storage=next(n for n in prepared["nodes"] if n["asset_id"]=="Store")
    storage["model_id"]="OUT"
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    assert handoff["application_holds"][0]["reason"]=="existing_nonjunction_endpoint_requires_supported_parameter_contract"



def test_new_storage_request_is_not_silently_written_as_junction():
    document,config,_,prepared=setup_case()
    node=next(n for n in prepared["nodes"] if n["asset_id"]=="NEW")
    node["model_section"]="STORAGE"
    handoff=compile_handoff(document,prepared,config,config_sha256="synthetic")
    assert handoff["application_holds"][0]["reason"]=="new_storage_requires_supported_parameter_contract"
    assert not handoff["applied_source_keys"]
