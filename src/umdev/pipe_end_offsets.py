"""Review-gated provisional high-side offsets for missing gravity pipe ends.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained pure preparation; no source floors or model files are written.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Mapping


def _finite(value: Any, name: str, *, positive: bool=False) -> float:
    """Require finite dimensional data, never interpret missing or bool as zero."""
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or positive and value<=0:
        raise ValueError(f'{name} must be finite'+(' and positive' if positive else ''))
    return float(value)


def propose_missing_pipe_end_offsets(nodes: Mapping[str,Mapping[str,Any]], links: Mapping[str,Mapping[str,Any]], *, provisional_slope: float, selected_link_ids: Iterable[str], approved_proposal_ids: Iterable[str]=(), length_unit: str='model', allow_unknown_downstream_rim: bool=False, tolerance: float=1e-8, configuration_sha256: str | None=None) -> dict[str,Any]:
    """Propose and optionally approve a high-side offset without moving anchors.

    Parameters
    ----------
    nodes : mapping
        Node IDs to known ``invert`` and optional known ``rim`` elevations.
        Omit rim when it is a placeholder rather than a reliable ground anchor.
    links : mapping
        IDs to gravity records with from_node, to_node, length, diameter and
        native DEPTH inlet_offset/outlet_offset. upstream_invert and
        downstream_invert are explicitly observed/reviewed elevations or None;
        an inferred floor is never an observed pipe end. Source provenance
        belongs in these records and is included in the approval fingerprint.
    provisional_slope : float
        Explicit positive elevation fall per hydraulic length; dimensionless.
    selected_link_ids : iterable of str
        Exact individually considered gravity pipes; no network-wide correction.
    approved_proposal_ids : iterable of str
        Exact IDs from an earlier proposal pass with identical data and policy.
    length_unit : str
        Common length/elevation/diameter unit label; no conversion occurs.
    allow_unknown_downstream_rim : bool
        Default False holds unknown-rim profiles. True permits a missing unchanged
        downstream rim with a warning; the newly raised upstream crown always
        requires a known rim. All known rims constrain endpoint crowns.
    configuration_sha256 : str, optional
        Fingerprint of the caller's entire private source/policy configuration,
        excluding approvals to avoid a circular hash. Bound into every proposal.
    tolerance : float
        Nonnegative elevation consistency tolerance in the same length unit.

    Returns
    -------
    dict
        Independent nodes/links, proposals, applied assignments and withheld
        cases. A returned link changes only after its exact approval ID matches.

    Raises
    ------
    ValueError
        Policy is invalid, selected IDs are unknown or approvals are stale.

    Notes
    -----
    Only adverse profiles with a genuinely unobserved upstream end are eligible.
    The provisional upstream elevation is fixed_downstream + slope*length.
    An unknown downstream end uses its unchanged node floor+existing offset and
    is explicitly identified as a floor-based assumption, not a measurement.
    A nonzero existing upstream offset, known upstream end or explicit drop is
    protected. Known downstream values must agree with the native floor+offset.
    Both endpoint crowns must remain below any known rim. This is a reviewable
    geometric assumption, not a surveyed value or hydraulic validation.
    """
    slope=_finite(provisional_slope,'provisional_slope',positive=True)
    tol=_finite(tolerance,'tolerance')
    if tol<0 or not isinstance(allow_unknown_downstream_rim,bool) or not isinstance(length_unit,str) or not length_unit.strip(): raise ValueError('Invalid offset policy')
    if configuration_sha256 is not None and (not isinstance(configuration_sha256,str) or len(configuration_sha256)!=64 or any(c not in '0123456789abcdef' for c in configuration_sha256)): raise ValueError('configuration_sha256 must be lowercase SHA256')
    selected=set(selected_link_ids);approved=set(approved_proposal_ids)
    if any(not isinstance(x,str) or not x for x in selected|approved): raise ValueError('Exact nonempty string IDs required')
    if selected-set(links): raise ValueError('Unknown selected pipe IDs: '+str(sorted(selected-set(links))))
    copied_nodes=deepcopy(dict(nodes));copied_links=deepcopy(dict(links));proposals=[];withheld=[];assignments=[]
    policy={'provisional_slope':slope,'length_unit':length_unit,'allow_unknown_downstream_rim':allow_unknown_downstream_rim,'tolerance':tol,'selected_link_ids':sorted(selected),'configuration_sha256':configuration_sha256}
    for identity in sorted(selected):
        link=links[identity];reason=None
        start,end=link.get('from_node'),link.get('to_node')
        if start not in nodes or end not in nodes or start==end: raise ValueError('Invalid selected pipe endpoints: '+identity)
        upnode,downnode=nodes[start],nodes[end]
        if str(link.get('kind','')).lower()!='gravity': reason='non_gravity_pipe'
        elif link.get('explicit_drop') or link.get('protected'): reason='protected_pipe_or_explicit_drop'
        elif upnode.get('invert') is None or downnode.get('invert') is None: reason='missing_node_floor'
        elif link.get('upstream_invert') is not None: reason='known_upstream_pipe_end_preserved'
        if reason: withheld.append({'link_id':identity,'reason':reason});continue
        upfloor=_finite(upnode['invert'],'upstream floor');downfloor=_finite(downnode['invert'],'downstream floor')
        length=_finite(link.get('length'),'hydraulic length',positive=True);diameter=_finite(link.get('diameter'),'diameter',positive=True)
        inlet=_finite(link.get('inlet_offset',0),'inlet_offset');outlet=_finite(link.get('outlet_offset',0),'outlet_offset')
        if min(inlet,outlet)<0: raise ValueError('Native depth offsets must be nonnegative')
        if inlet>tol: withheld.append({'link_id':identity,'reason':'existing_nonzero_upstream_offset_preserved'});continue
        downstream=downfloor+outlet;known_down=link.get('downstream_invert')
        if known_down is not None:
            known_down=_finite(known_down,'known downstream pipe end')
            if abs(known_down-downstream)>tol: withheld.append({'link_id':identity,'reason':'known_downstream_native_offset_conflict'});continue
            downstream=known_down
        if upfloor+inlet>=downstream-tol: withheld.append({'link_id':identity,'reason':'profile_not_adverse'});continue
        upstream=downstream+slope*length;offset=upstream-upfloor
        if not math.isfinite(upstream) or not math.isfinite(offset): raise ValueError('Provisional pipe end overflow')
        flags=['provisional_high_side_missing_pipe_end','unmeasured_upstream_pipe_end']
        if known_down is None: flags.append('downstream_floor_based_pipe_end_not_observed')
        checks=[]
        for name,node,elevation in [('upstream',upnode,upstream),('downstream',downnode,downstream)]:
            rim=node.get('rim');crown=elevation+diameter
            if not math.isfinite(crown): raise ValueError('Provisional crown overflow')
            if rim is None:
                checks.append({'end':name,'status':'unknown_rim','crown':crown})
                if name=='upstream' or not allow_unknown_downstream_rim: reason='unknown_rim_requires_explicit_policy'
                flags.append('unknown_'+name+'_rim')
            else:
                rim=_finite(rim,name+' known rim');checks.append({'end':name,'status':'passes' if crown<=rim+tol else 'crown_above_known_rim','crown':crown,'known_rim':rim})
                if crown>rim+tol:reason='pipe_crown_above_known_rim'
        if reason: withheld.append({'link_id':identity,'reason':reason,'rim_crown_checks':checks});continue
        evidence={'link_id':identity,'link':deepcopy(dict(link)),'nodes':{start:deepcopy(dict(upnode)),end:deepcopy(dict(downnode))},'policy':policy}
        digest=sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
        proposal={'proposal_id':'OFFSET_'+identity+'_'+digest[:16],'source_sha256':digest,'link_id':identity,'from_node':start,'to_node':end,'upstream_invert':upstream,'downstream_invert':downstream,'original_inlet_offset':inlet,'inlet_offset':offset,'outlet_offset':outlet,'hydraulic_length':length,'diameter':diameter,'provisional_slope':slope,'length_unit':length_unit,'downstream_anchor_basis':'observed_or_reviewed_pipe_end' if known_down is not None else 'unchanged_node_floor_plus_offset_not_observed_pipe_end','rim_crown_checks':checks,'flags':flags,'source_evidence':evidence,'known_floors_and_pipe_ends_preserved':True,'status':'proposed_requires_individual_review'}
        proposals.append(proposal)
    stale=approved-{r['proposal_id'] for r in proposals}
    if stale:raise ValueError('Unknown or stale pipe-end offset approvals: '+str(sorted(stale)))
    for row in proposals:
        if row['proposal_id'] not in approved:continue
        row['status']='applied_provisional_assumption';link=copied_links[row['link_id']]
        link['inlet_offset']=row['inlet_offset']
        link['assumptions']=[*deepcopy(link.get('assumptions',[])),deepcopy(row)]
        assignments.append(deepcopy(row))
    return {'nodes':copied_nodes,'links':copied_links,'proposals':proposals,'assignments':assignments,'withheld':withheld,'length_unit':length_unit,'policy':policy}
