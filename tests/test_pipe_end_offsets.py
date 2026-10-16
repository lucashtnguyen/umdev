"""Synthetic review-gated offsets preserve known anchors and native geometry.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Common units are fictional feet; no project records are included.
"""
from copy import deepcopy
import pytest
from umdev.pipe_end_offsets import propose_missing_pipe_end_offsets


def case():
    nodes={'U':{'invert':100.,'rim':110.,'source_record_sha256':'a'*64},'D':{'invert':102.,'rim':112.,'source_record_sha256':'b'*64}}
    links={'P':{'kind':'gravity','from_node':'U','to_node':'D','length':100.,'diameter':1.,'inlet_offset':0.,'outlet_offset':.2,'upstream_invert':None,'downstream_invert':102.2,'source_record_sha256':'c'*64}}
    return nodes,links


def plan(nodes,links,**kwargs):
    return propose_missing_pipe_end_offsets(nodes,links,provisional_slope=.001,selected_link_ids=['P'],length_unit='ft',**kwargs)


def test_hand_calculation_review_gate_and_all_anchors_preserved():
    nodes,links=case();before=deepcopy((nodes,links));proposed=plan(nodes,links);p=proposed['proposals'][0]
    assert p['upstream_invert']==pytest.approx(102.3)
    assert p['inlet_offset']==pytest.approx(2.3)
    assert proposed['links']==links and not proposed['assignments']
    applied=plan(nodes,links,approved_proposal_ids=[p['proposal_id']])
    assert applied['links']['P']['inlet_offset']==pytest.approx(2.3)
    assert applied['links']['P']['outlet_offset']==.2
    assert applied['nodes']==nodes and applied['links']['P']['downstream_invert']==102.2
    assert applied['assignments'][0]['known_floors_and_pipe_ends_preserved']
    assert (nodes,links)==before


def test_both_missing_ports_uses_explicitly_assumed_downstream_floor():
    nodes,links=case();links['P']['downstream_invert']=None;links['P']['outlet_offset']=0
    p=plan(nodes,links)['proposals'][0]
    assert p['upstream_invert']==pytest.approx(102.1)
    assert p['downstream_anchor_basis']=='unchanged_node_floor_plus_offset_not_observed_pipe_end'
    assert 'downstream_floor_based_pipe_end_not_observed' in p['flags']


@pytest.mark.parametrize('change,reason',[({'upstream_invert':99},'known_upstream_pipe_end_preserved'),({'inlet_offset':.1},'existing_nonzero_upstream_offset_preserved'),({'kind':'force_main'},'non_gravity_pipe'),({'explicit_drop':True},'protected_pipe_or_explicit_drop'),({'downstream_invert':101},'known_downstream_native_offset_conflict')])
def test_known_end_pressure_drop_or_offset_protected(change,reason):
    nodes,links=case();links['P'].update(change)
    r=plan(nodes,links);assert not r['proposals'] and r['withheld'][0]['reason']==reason


def test_known_rim_crown_exceedance_holds_even_if_unknown_rims_allowed():
    nodes,links=case();nodes['U']['rim']=103
    r=plan(nodes,links,allow_unknown_downstream_rim=True)
    assert not r['proposals'] and r['withheld'][0]['reason']=='pipe_crown_above_known_rim'


def test_unknown_rim_default_held_and_opt_in_flagged():
    nodes,links=case();nodes['D']['rim']=None
    assert plan(nodes,links)['withheld'][0]['reason']=='unknown_rim_requires_explicit_policy'
    assert 'unknown_downstream_rim' in plan(nodes,links,allow_unknown_downstream_rim=True)['proposals'][0]['flags']


@pytest.mark.parametrize('target',[('node','rim',111),('link','length',101),('link','source_record_sha256','d'*64)])
def test_changed_source_record_stales_exact_approval(target):
    nodes,links=case();approval=plan(nodes,links)['proposals'][0]['proposal_id']
    family,key,value=target
    (nodes['U'] if family=='node' else links['P'])[key]=value
    with pytest.raises(ValueError,match='stale'):plan(nodes,links,approved_proposal_ids=[approval])


@pytest.mark.parametrize('slope',[0,-.001,True,float('nan'),float('inf')])
def test_invalid_slopes_rejected(slope):
    nodes,links=case()
    with pytest.raises(ValueError):propose_missing_pipe_end_offsets(nodes,links,provisional_slope=slope,selected_link_ids=['P'])


def test_nonadverse_unknown_id_and_overflow_fail_safely():
    nodes,links=case();nodes['U']['invert']=103
    assert plan(nodes,links)['withheld'][0]['reason']=='profile_not_adverse'
    with pytest.raises(ValueError,match='Unknown selected'):propose_missing_pipe_end_offsets(nodes,links,provisional_slope=.001,selected_link_ids=['missing'])
    nodes['U']['invert']=100;links['P']['length']=1e308
    with pytest.raises(ValueError,match='overflow'):propose_missing_pipe_end_offsets(nodes,links,provisional_slope=1e308,selected_link_ids=['P'])


def test_upstream_rim_always_required_and_external_config_hash_bound():
    nodes,links=case();nodes['U']['rim']=None
    assert not plan(nodes,links,allow_unknown_downstream_rim=True)['proposals']
    nodes,links=case()
    p=plan(nodes,links,configuration_sha256='a'*64)['proposals'][0]
    with pytest.raises(ValueError,match='stale'):
        plan(nodes,links,configuration_sha256='b'*64,approved_proposal_ids=[p['proposal_id']])


def test_crown_overflow_never_returns_nonfinite_review_evidence():
    nodes,links=case();nodes['U']['invert']=1e308;nodes['D']['invert']=1.2e308
    links['P']['diameter']=1e308;links['P']['downstream_invert']=None
    with pytest.raises(ValueError,match='crown overflow'):plan(nodes,links)
