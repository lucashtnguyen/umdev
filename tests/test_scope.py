"""Synthetic complete-group scope, load preservation and directed-route tests.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
No private utility assets or scope decisions are embedded in these tests.
"""
from copy import deepcopy
import json
import pytest
from umdev.scope import plan_small_runs


def edge(u,v,length=100.,diameter=.2,kind='gravity'):
    return {'from_node':u,'to_node':v,'kind':kind,'length_m':length,'diameter_m':diameter}


def plan(links,*,candidates=None,protected=('CORE',),locked=(),loaded=(),reference=(),retired=(),max_length=400.,max_count=8):
    nodes={n:{}for r in links.values()for n in (r['from_node'],r['to_node'])}
    return plan_small_runs(nodes,links,candidate_link_ids=set(links)-set(protected)if candidates is None else candidates,
        protected_link_ids=protected,protected_node_ids=locked,loaded_node_ids=loaded,terminal_node_ids=['Z'],
        policy={'maximum_group_length_m':max_length,'maximum_group_pipe_count':max_count,'maximum_minor_diameter_m':.31,'dimensional_tolerance_m':1e-6,'allow_isolated_component_omission':False},
        reference_omission_ids=reference,retired_link_ids=retired)


def test_complete_terminal_tree_not_original_component_anchor_count():
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('Q','P')}
    before=deepcopy(links);result=plan(links)
    assert result['recommended_omit_link_ids']==['B','C']
    group=result['groups'][0]
    assert group['kind']=='peripheral_tree'and group['attachment_node_ids']==['A']
    assert group['total_length_m']==200.
    assert links==before
    assert result['joint_retained_network_guard']['passed']


def test_whole_tree_threshold_does_not_allow_recursive_small_piece_deletion():
    links={'CORE':edge('A','Z'),'B':edge('P','A',200),'C':edge('Q','P',200),'D':edge('R','Q',200)}
    result=plan(links,max_length=400.)
    assert result['recommended_omit_link_ids']==[]
    assert len(result['groups'])==1
    assert result['groups'][0]['link_ids']==['B','C','D']
    assert 'whole_group_length_exceeds_scope_policy'in result['groups'][0]['hold_reasons']


def test_complete_branching_tree_is_one_group_before_policy():
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('Q','P'),'D':edge('R','P')}
    result=plan(links)
    assert len(result['groups'])==1 and result['groups'][0]['link_ids']==['B','C','D']


@pytest.mark.parametrize('protection',['loaded','control','baseline'])
def test_load_control_and_baseline_protection_cannot_be_overridden_by_reference(protection):
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('Q','P')}
    kwargs={'loaded':['Q']}if protection=='loaded'else{'locked':['Q']}if protection=='control'else{'protected':['CORE','C']}
    result=plan(links,reference=['B','C'],**kwargs)
    assert result['recommended_omit_link_ids']==[]
    assert result['joint_retained_network_guard']['passed']


def test_complete_pendant_loop_omits_both_parallel_arms_together():
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('P','Q'),'D':edge('Q','A')}
    result=plan(links)
    assert result['recommended_omit_link_ids']==['B','C','D']
    assert len(result['groups'])==1
    assert result['groups'][0]['kind']=='pendant_parallel_loop'
    assert result['groups'][0]['attachment_node_ids']==['A']


def test_two_native_parallel_edges_are_not_collapsed():
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('P','A')}
    result=plan(links)
    assert result['groups'][0]['pipe_count']==2
    assert result['recommended_omit_link_ids']==['B','C']


def test_loop_and_attached_tree_assess_complete_size_not_separate_thresholds():
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('P','Q'),'D':edge('Q','A'),'E':edge('R','P',200)}
    result=plan(links,max_length=400.)
    assert result['recommended_omit_link_ids']==[]
    assert len(result['groups'])==1
    assert result['groups'][0]['link_ids']==['B','C','D','E']


def test_two_anchor_corridor_requires_real_directed_alternate_route():
    links={'CORE':edge('A','Z'),'ORIGINAL':edge('S','A'),'B':edge('S','P'),'C':edge('P','A')}
    result=plan(links,protected=['CORE','ORIGINAL'])
    group=result['groups'][0]
    assert result['recommended_omit_link_ids']==['B','C']
    assert group['alternate_directed_link_path']==['ORIGINAL']
    assert group['kind']=='redundant_serial_corridor'
    del links['ORIGINAL']
    result=plan(links,locked=['S'])
    assert not result['recommended_omit_link_ids']
    assert 'required_or_ambiguous_directed_anchor_connection'in result['groups'][0]['hold_reasons']


def test_unloaded_two_anchor_splitter_is_explicit_hold_not_redundant_corridor():
    links={'CORE':edge('A','Z'),'SECOND':edge('S','Z'),'B':edge('P','S'),'C':edge('P','A')}
    result=plan(links,protected=['CORE','SECOND'])
    assert not result['recommended_omit_link_ids']
    assert result['groups'][0]['retained_network_guard']['passed']
    assert result['groups'][0]['alternate_directed_link_path']is None


def test_joint_parallel_corridors_cannot_both_remove_required_connection():
    links={'CORE':edge('A','Z'),'B':edge('S','P'),'C':edge('P','A'),'D':edge('S','Q'),'E':edge('Q','A')}
    result=plan(links,locked=['S'])
    assert result['recommended_omit_link_ids']==[]
    assert not result['joint_retained_network_guard']['passed']
    assert any('joint_omission_guard_failed'in g['hold_reasons']for g in result['groups'])


def test_force_main_and_major_station_roles_always_retained():
    links={'CORE':edge('A','Z'),'B':edge('P','A',kind='force_main'),'C':edge('Q','P')}
    result=plan(links)
    assert 'B'in result['protected_candidate_link_ids']
    assert result['recommended_omit_link_ids']==['C']


def test_reference_cannot_override_large_diameter_or_size_policy():
    links={'CORE':edge('A','Z'),'B':edge('P','A',diameter=.5)}
    result=plan(links,reference=['B'])
    assert not result['recommended_omit_link_ids']
    assert 'diameter_exceeds_minor_scope_policy'in result['groups'][0]['hold_reasons']


def test_retirement_is_separate_and_explicit_not_reference_status_inference():
    links={'CORE':edge('A','Z'),'OLD':edge('X','A'),'B':edge('P','A')}
    result=plan(links,retired=['OLD'])
    assert result['separately_retired_link_ids']==['OLD']
    assert 'OLD'not in result['recommended_omit_link_ids']
    with pytest.raises(ValueError,match='also be protected'):plan(links,protected=['CORE','OLD'],retired=['OLD'])


@pytest.mark.parametrize('field,value',[('length_m',0.),('length_m',float('nan')),('diameter_m',-1.),('diameter_m',True)])
def test_bad_geometry_rejected(field,value):
    links={'CORE':edge('A','Z'),'B':edge('P','A')};links['B'][field]=value
    with pytest.raises(ValueError):plan(links)


def test_unknown_identity_selfloop_and_missing_policy_fail_closed():
    links={'CORE':edge('A','Z'),'B':edge('P','A')}
    with pytest.raises(ValueError,match='unknown'):plan(links,reference=['MISSING'])
    links['B']['to_node']='P'
    with pytest.raises(ValueError,match='self'):plan(links)


def test_fingerprints_and_results_are_deterministic_under_mapping_order():
    links={'CORE':edge('A','Z'),'B':edge('P','A'),'C':edge('Q','P')}
    a=plan(links);b=plan(dict(reversed(list(links.items()))))
    assert a==b
    json.dumps(a,allow_nan=False)
    links['B']['length_m']+=1.
    assert plan(links)['input_fingerprint']!=a['input_fingerprint']
    assert plan(links)['groups'][0]['proposal_id']!=a['groups'][0]['proposal_id']


def test_identity_string_is_not_an_explicit_id_collection():
    links={'CORE':edge('A','Z'),'B':edge('P','A')}
    with pytest.raises(ValueError,match='not a string'):plan(links,candidates='B')


def test_separate_retirement_cannot_hide_a_loaded_orphan():
    links={'CORE':edge('A','Z'),'OLD':edge('X','A'),'B':edge('P','A')}
    with pytest.raises(ValueError,match='review load transfers first'):
        plan(links,retired=['OLD'],loaded=['X'])
    # Caller has separately reviewed/transferred that load to surviving A.
    result=plan(links,retired=['OLD'],loaded=['A'])
    assert result['load_protection_node_ids']==['A']
    assert result['recommended_omit_link_ids']==['B']


def test_separately_retired_pressure_link_is_not_reintroduced_as_protection():
    links={'CORE':edge('A','Z'),'OLD':edge('X','A',kind='force_main'),'B':edge('P','A')}
    result=plan(links,retired=['OLD'])
    assert result['separately_retired_link_ids']==['OLD']
    assert 'OLD'not in result['protected_candidate_link_ids']
    assert result['recommended_omit_link_ids']==['B']


def test_joint_failure_proposal_hash_binds_final_held_record():
    from hashlib import sha256
    links={'CORE':edge('A','Z'),'B':edge('S','P'),'C':edge('P','A'),'D':edge('S','Q'),'E':edge('Q','A')}
    result=plan(links,locked=['S'])
    assert not result['joint_retained_network_guard']['passed']
    for group in result['groups']:
        record={key:value for key,value in group.items()if key!='proposal_id'}
        expected=sha256(json.dumps({'input':result['input_fingerprint'],'group':record},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:24]
        assert group['proposal_id']=='SCOPE-'+expected


def test_group_length_accumulates_in_exact_id_order():
    links={'CORE':edge('A','Z'),'B':edge('P','A',1e16),'C':edge('Q','P',1.),'D':edge('R','Q',1.)}
    result=plan(links,max_length=2e16)
    group=result['groups'][0]
    assert group['total_length_m']==sum(links[k]['length_m']for k in sorted(group['link_ids']))
