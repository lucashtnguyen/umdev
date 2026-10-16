"""Synthetic source-bound observation holds preserve values and block selection.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
No utility observations or project policy are included.
"""
from copy import deepcopy
import pytest
from umdev import observation_fingerprint
from umdev.observation_holds import apply_observation_holds,validate_observation_holds
from umdev.evidence import collect_structure_ports,resolve_ports
from umdev.records import network_from_dict
from umdev.parameters import prepare_network_from_dict
from umdev.configuration import validate_config,ConfigurationError
from test_floor_policy import network,policy,port,node,add_inline
from test_qualified_ports import config_case


def binding(observation):
    return {'expected_fingerprint':observation_fingerprint(observation),'reason':'Fictional observation reviewed as suspect, not corrected','reviewer':'Synthetic reviewer','date':'2026-10-16'}


def case():
    canonical=network();cfg=policy(precedence=['normalized_port','inline_port'])
    canonical['ports']=[{**port('suspect',2,role='out',diameter=.3),'source_record_sha256':'a'*64},{**port('usable',12,role='out',diameter=.3),'source_record_sha256':'b'*64}]
    observations=collect_structure_ports(network_from_dict(canonical),cfg)
    target=next(r for r in observations if r['key'].endswith('/suspect'))
    cfg['ports']['assignment']['observation_holds']={target['key']:binding(target)}
    return canonical,cfg,target


def test_reviewed_hold_excluded_from_pipe_and_floor_with_unchanged_raw_value():
    canonical,cfg,target=case();before=deepcopy(canonical)
    collected=collect_structure_ports(network_from_dict(canonical),cfg)
    held=next(r for r in collected if r['key']==target['key'])
    assert held['invert']==2
    assert held['_reviewed_hold']['raw_and_parsed_values_preserved']
    ends,findings=resolve_ports(network_from_dict(canonical),cfg)
    assert ends[('u/p/O','upstream')].value==12
    assert node(prepare_network_from_dict(canonical,cfg))['floor_m']==12
    assert any(f['rule']=='reviewed_observation_hold' and f['raw']['invert']==2 for f in findings)
    assert canonical==before


def test_default_no_hold_keeps_original_selection_and_values():
    canonical,cfg,_=case();cfg['ports']['assignment'].pop('observation_holds')
    assert node(prepare_network_from_dict(canonical,cfg))['floor_m']==2
    observations=collect_structure_ports(network_from_dict(canonical),cfg)
    assert not any('_reviewed_hold' in r for r in observations)


@pytest.mark.parametrize('field,value',[('invert',3),('source_record_sha256','c'*64),('vertical_datum','CHANGED'),('role','in')])
def test_changed_evidence_fails_closed(field,value):
    canonical,cfg,_=case();canonical['ports'][0][field]=value
    with pytest.raises(ValueError,match='Stale reviewed'):collect_structure_ports(network_from_dict(canonical),cfg)


def test_missing_duplicate_and_unfingerprinted_observations_fail_closed():
    canonical,cfg,target=case();hold=cfg['ports']['assignment']['observation_holds']
    with pytest.raises(ValueError,match='exactly once'):apply_observation_holds([],hold)
    with pytest.raises(ValueError,match='exactly once'):apply_observation_holds([target,target],hold)
    missing=deepcopy(target);missing.pop('source_record_sha256')
    with pytest.raises(ValueError,match='source_record_sha256'):observation_fingerprint(missing)


def test_inline_fingerprint_preserves_exact_source_text_and_source_hash():
    canonical=network();cfg=policy();add_inline(canonical,cfg,'out_note','12 m, 300 mm',role='out')
    collected=collect_structure_ports(network_from_dict(canonical),cfg);row=collected[0]
    assert row['source_key']=='u/n/A' and row['source_field']=='out_note' and row['source_record_sha256']=='a'*64
    cfg['ports']['assignment']['observation_holds']={row['key']:binding(row)}
    held=collect_structure_ports(network_from_dict(canonical),cfg)[0]
    assert held['raw']=='12 m, 300 mm' and held['invert']==12
    assert observation_fingerprint(dict(reversed(list(row.items()))))==observation_fingerprint(row)
    canonical['nodes'][0]['out_note']='12 m,  300 mm'
    with pytest.raises(ValueError,match='Stale reviewed'):collect_structure_ports(network_from_dict(canonical),cfg)


@pytest.mark.parametrize('change',[{'unexpected':True},{'reviewer':''},{'date':'16-10-2026'},{'expected_fingerprint':'A'*64}])
def test_malformed_hold_config_rejected(change):
    cfg=config_case();row={'expected_fingerprint':'a'*64,'reason':'Synthetic review','reviewer':'Reviewer','date':'2026-10-16',**change}
    cfg['ports']['assignment']['observation_holds']={'s/n/A/note':row}
    with pytest.raises(ConfigurationError):validate_config(cfg)


def test_valid_hold_config_and_finite_json_contract():
    cfg=config_case();cfg['ports']['assignment']['observation_holds']={'s/n/A/note':{'expected_fingerprint':'a'*64,'reason':'Synthetic review','reviewer':'Reviewer','date':'2026-10-16'}}
    validate_config(cfg)
    with pytest.raises(ValueError,match='finite JSON'):observation_fingerprint({'key':'s/n/A','source_record_sha256':'a'*64,'invert':float('nan')})
