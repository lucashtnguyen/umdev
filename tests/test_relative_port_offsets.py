"""Synthetic guards and hand calculations for local datum-invariant offsets.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
No private utility observations or asset identities are included.
"""
from copy import deepcopy

import pytest

from umdev import relative_port_offset_plan, observation_fingerprint
from umdev.configuration import validate_config, ConfigurationError
from umdev.evidence import collect_structure_ports, normalize_elevation
from umdev.records import network_from_dict
from umdev.relative_port_offsets import validate_relative_offset_frames
from test_qualified_ports import config_case


def case(incoming=40.1, outgoing=40.0, datum=None, qualifier='', unmodeled_low=None):
    """Create explicit incoming/outgoing bindings in an unknown local foot frame."""
    canonical={'nodes':[{'key':'u/n/N','asset_id':'N','dataset':'nodes','vertical_datum':datum,
                'source_record_sha256':'a'*64,'in_note':f'{incoming} (E) 15"{qualifier}',
                'out_note':f'{outgoing} (W) 15"'}],
        'pipes':[{'key':'u/p/I','asset_id':'I','from_node':'U','to_node':'N','diameter':.381,'source_record_sha256':'b'*64},
                 {'key':'u/p/O','asset_id':'O','from_node':'N','to_node':'D','diameter':.381,'source_record_sha256':'c'*64}], 'ports':[]}
    cfg={'ports':{'observations':[{'id':'in','dataset':'nodes','parser':'qualified_elevation_direction_diameter_v1','field':'in_note','role':'in','units':{'elevation':'ft','diameter':'in'}},
                                  {'id':'out','dataset':'nodes','parser':'qualified_elevation_direction_diameter_v1','field':'out_note','role':'out','units':{'elevation':'ft','diameter':'in'}}],
                  'assignment':{'diameter_tolerance':{'value':.001,'unit':'m'}}}}
    if unmodeled_low is not None:
        canonical['nodes'][0]['extra_note']=f'{unmodeled_low} (N) 6"'
        cfg['ports']['observations'].append({'id':'extra','dataset':'nodes','parser':'qualified_elevation_direction_diameter_v1','field':'extra_note','role':'in','units':{'elevation':'ft','diameter':'in'}})
    observations=collect_structure_ports(network_from_dict(canonical),cfg)
    reference=min((row for row in observations if row.get('invert') is not None),key=lambda row:row['invert'])['key']
    frame={'id':'R1','node_key':'u/n/N','node_source_sha256':'a'*64,'frame_id':'reviewed_single_local_frame',
           'basis':'Fictional same-node compatible local survey frame; absolute datum unverified',
           'reviewer':'Synthetic reviewer','date':'2026-10-16','reference_observation_key':reference,
           'observations':[{'observation_key':row['key'],'expected_fingerprint':observation_fingerprint(row)} for row in observations],
           'endpoints':[{'observation_key':'u/n/N/in','pipe_key':'u/p/I','pipe_source_sha256':'b'*64,'end':'downstream'},
                        {'observation_key':'u/n/N/out','pipe_key':'u/p/O','pipe_source_sha256':'c'*64,'end':'upstream'}]}
    cfg['ports']['assignment']['relative_offset_frames']=[frame]
    return canonical,cfg


def test_zero_outlet_and_tenth_foot_inlet_are_relative_only_and_sources_unchanged():
    canonical,cfg=case();before=deepcopy((canonical,cfg))
    result=relative_port_offset_plan(canonical,cfg)
    values={row['end']:row['relative_offset_m'] for row in result['assignments']}
    assert values==pytest.approx({'upstream':0,'downstream':.1*.3048})
    assert result['absolute_elevations_derived'] is False
    assert all(row['absolute_elevation_m'] is None for row in result['assignments'])
    assert result['frames'][0]['node_drop_protection']
    assert result['frames'][0]['absolute_vertical_basis']=='unresolved'
    assert (canonical,cfg)==before
    assert normalize_elevation(40*.3048, None, cfg).value is None


def test_common_unknown_vertical_shift_cancels_without_absolute_anchor():
    first=relative_port_offset_plan(*case())
    shifted=relative_port_offset_plan(*case(incoming=1040.1,outgoing=1040))
    assert [row['relative_offset_m'] for row in shifted['assignments']]==pytest.approx([row['relative_offset_m'] for row in first['assignments']])
    assert all(row['absolute_elevation_m'] is None for row in shifted['assignments'])


def test_incoming_minimum_preserves_small_negative_local_relation():
    result=relative_port_offset_plan(*case(incoming=39.99,outgoing=40))
    values={row['end']:row['relative_offset_m'] for row in result['assignments']}
    assert values==pytest.approx({'downstream':0,'upstream':.01*.3048})
    assert result['frames'][0]['local_in_out_relations'][0]['incoming_minus_outgoing_m']==pytest.approx(-.01*.3048)


def test_unmodeled_lowest_observation_is_fingerprinted_reference():
    result=relative_port_offset_plan(*case(unmodeled_low=39.9))
    assert result['frames'][0]['reference_observation_key']=='u/n/N/extra'
    assert sorted(row['relative_offset_m'] for row in result['assignments'])==pytest.approx([.1*.3048,.2*.3048])


def test_reference_cannot_be_arbitrary_outlet_above_reviewed_incoming():
    canonical,cfg=case(incoming=39.99,outgoing=40)
    cfg['ports']['assignment']['relative_offset_frames'][0]['reference_observation_key']='u/n/N/out'
    with pytest.raises(ValueError,match='lowest reviewed'):relative_port_offset_plan(canonical,cfg)


@pytest.mark.parametrize('mutation',['node_hash','pipe_hash','raw','units','reference_hash','missing_observation','wrong_node','wrong_role','duplicate_end'])
def test_stale_and_conflicting_exact_bindings_fail_closed(mutation):
    canonical,cfg=case(unmodeled_low=39.9);frame=cfg['ports']['assignment']['relative_offset_frames'][0]
    if mutation=='node_hash':canonical['nodes'][0]['source_record_sha256']='d'*64
    elif mutation=='pipe_hash':canonical['pipes'][0]['source_record_sha256']='d'*64
    elif mutation=='raw':canonical['nodes'][0]['in_note']='40.20 (E) 15"'
    elif mutation=='units':cfg['ports']['observations'][0]['units']['elevation']='m'
    elif mutation=='reference_hash':next(row for row in frame['observations'] if row['observation_key']=='u/n/N/extra')['expected_fingerprint']='0'*64
    elif mutation=='missing_observation':frame['observations'][0]['observation_key']='missing'
    elif mutation=='wrong_node':canonical['pipes'][0]['to_node']='different'
    elif mutation=='wrong_role':frame['endpoints'][0]['end']='upstream'
    elif mutation=='duplicate_end':frame['endpoints'].append(deepcopy(frame['endpoints'][0]))
    with pytest.raises(ValueError):relative_port_offset_plan(canonical,cfg)


def test_mixed_datum_and_invalid_raw_observation_hold_entire_frame():
    result=relative_port_offset_plan(*case(datum='NGVD29 / NAVD88'))
    assert not result['assignments'] and 'mixed_vertical_basis' in result['held'][0]['reasons']
    result=relative_port_offset_plan(*case(qualifier=' CAPPED'))
    assert not result['assignments']
    assert result['frames'][0]['source_observations'][0]['raw']


def test_diameter_conflict_holds_without_partial_assignments():
    canonical,cfg=case();canonical['pipes'][0]['diameter']=.2
    result=relative_port_offset_plan(canonical,cfg)
    assert not result['assignments']
    assert any('diameter_conflict' in reason for reason in result['held'][0]['reasons'])


@pytest.mark.parametrize('diameter',[None,0,-.1])
@pytest.mark.parametrize('typed',[True,False])
def test_unknown_or_invalid_endpoint_diameter_holds_with_or_without_port_size(diameter,typed):
    canonical,cfg=case()
    if not typed:
        canonical['nodes'][0]['in_note']='40.1 (E)'
        canonical['nodes'][0]['out_note']='40.0 (W)'
        fresh={row['key']:row for row in collect_structure_ports(network_from_dict(canonical),cfg)}
        for row in cfg['ports']['assignment']['relative_offset_frames'][0]['observations']:
            row['expected_fingerprint']=observation_fingerprint(fresh[row['observation_key']])
    canonical['pipes'][0]['diameter']=diameter
    result=relative_port_offset_plan(canonical,cfg)
    assert not result['assignments']
    assert any('endpoint_pipe_diameter' in reason for reason in result['held'][0]['reasons'])


def test_empty_configuration_preserves_default_behavior():
    result=relative_port_offset_plan({}, {})
    assert result['assignments']==[] and result['frames']==[]
    assert not result['absolute_elevations_derived']


@pytest.mark.parametrize('change',[{'unexpected':True},{'basis':''},{'date':'2026/10/16'},{'node_source_sha256':'A'*64}])
def test_configuration_contract_rejects_malformed_reviews(change):
    _,cfg=case();frame=cfg['ports']['assignment']['relative_offset_frames'][0]
    frame.update(change)
    public=config_case();public['ports']['assignment']['relative_offset_frames']=[frame]
    with pytest.raises(ConfigurationError):validate_config(public)


def test_valid_configuration_and_pressure_port_difference_do_not_rewire():
    canonical,cfg=case();canonical['pipes'][0]['pipe_kind']='force_main'
    public=config_case();public['ports']['assignment']['relative_offset_frames']=deepcopy(cfg['ports']['assignment']['relative_offset_frames'])
    validate_config(public)
    result=relative_port_offset_plan(canonical,cfg)
    assert len(result['assignments'])==2
    assert canonical['pipes'][0]['to_node']=='N'
