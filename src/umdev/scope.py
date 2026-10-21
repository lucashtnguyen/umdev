"""Prepare complete minor-network scope proposals with retained-route guards.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Pure SI graph review. No project identities, lifecycle inference or model writing.
"""
from __future__ import annotations
from collections import defaultdict,deque
from hashlib import sha256
import json
import math
from typing import Any,Iterable,Mapping


def _ids(values: Iterable[str], allowed: set[str], label: str) -> set[str]:
    """Validate explicit exact identities without trimming or case conversion."""
    if isinstance(values,(str,bytes)):
        raise ValueError(label+' must be an iterable of exact identities, not a string')
    result=set(values)
    if any(not isinstance(k,str)or not k for k in result)or result-allowed:
        raise ValueError(label+' contains unknown or malformed identities')
    return result


def _adjacency(links: Mapping[str,Mapping[str,Any]], selected: Iterable[str], *, directed: bool=False) -> dict[str,list[tuple[str,str]]]:
    """Retain native edge identities, including parallel links."""
    adjacency=defaultdict(list)
    for key in sorted(selected):
        row=links[key];u,v=row['from_node'],row['to_node'];adjacency[u].append((key,v))
        if not directed:adjacency[v].append((key,u))
    return dict(adjacency)


def _path(links: Mapping[str,Mapping[str,Any]], selected: Iterable[str], source: str,target: str) -> list[str]|None:
    """Return a deterministic directed route, never infer reverse gravity flow."""
    if source==target:return []
    adjacency=_adjacency(links,selected,directed=True);seen={source};queue=deque([(source,[])])
    while queue:
        node,path=queue.popleft()
        for key,other in adjacency.get(node,()):
            if other==target:return [*path,key]
            if other not in seen:seen.add(other);queue.append((other,[*path,key]))
    return None


def _components(links: Mapping[str,Mapping[str,Any]], selected: Iterable[str]) -> list[set[str]]:
    """Find components among remaining link endpoints; isolated removed nodes vanish."""
    adjacency=_adjacency(links,selected);seen=set();groups=[]
    for first in sorted(adjacency):
        if first in seen:continue
        component={first};seen.add(first);queue=deque([first])
        while queue:
            for key,node in adjacency.get(queue.popleft(),()):
                if node not in seen:seen.add(node);component.add(node);queue.append(node)
        groups.append(component)
    return groups


def _blocks(links: Mapping[str,Mapping[str,Any]], selected: Iterable[str]) -> list[set[str]]:
    """Iterative Tarjan edge blocks; parallel edges are distinct, no recursion limit."""
    adjacency=_adjacency(links,selected);discovery={};low={};parents={};edge_parents={};edge_stack=[];blocks=[];counter=0
    for first in sorted(adjacency):
        if first in discovery:continue
        counter+=1;discovery[first]=low[first]=counter;frames=[(first,iter(adjacency[first]))]
        while frames:
            node,iterator=frames[-1]
            try:
                edge,other=next(iterator)
                if edge==edge_parents.get(node):continue
                if other not in discovery:
                    parents[other]=node;edge_parents[other]=edge;edge_stack.append(edge)
                    counter+=1;discovery[other]=low[other]=counter;frames.append((other,iter(adjacency.get(other,()))))
                elif discovery[other]<discovery[node]:low[node]=min(low[node],discovery[other]);edge_stack.append(edge)
            except StopIteration:
                frames.pop()
                if node in parents:
                    parent=parents[node];low[parent]=min(low[parent],low[node])
                    if low[node]>=discovery[parent]:
                        block=set()
                        while edge_stack:
                            edge=edge_stack.pop();block.add(edge)
                            if edge==edge_parents[node]:break
                        blocks.append(block)
    return sorted(blocks,key=lambda b:sorted(b))


def _guard(links: Mapping[str,Mapping[str,Any]], before: set[str], removed: set[str],
           protected_nodes: set[str],terminal_nodes: set[str]) -> dict[str,Any]:
    """Reject new islands or lost previously available protected-node terminal routes."""
    after=before-removed;old_components=_components(links,before);new_components=_components(links,after)
    old_membership={n:i for i,c in enumerate(old_components)for n in c};new_membership={n:i for i,c in enumerate(new_components)for n in c}
    split=[]
    for index,old in enumerate(old_components):
        children={new_membership[n]for n in old if n in new_membership}
        if len(children)>1:split.append({'original_component':index,'remaining_component_count':len(children)})
    missing_protected=sorted(n for n in protected_nodes if n in old_membership and n not in new_membership)
    def reaches_terminals(selected: set[str]) -> set[str]:
        reverse=defaultdict(list)
        for key in selected:reverse[links[key]['to_node']].append(links[key]['from_node'])
        reached=set(terminal_nodes);queue=deque(sorted(terminal_nodes))
        while queue:
            for node in reverse.get(queue.popleft(),()):
                if node not in reached:reached.add(node);queue.append(node)
        return reached
    old_routes=reaches_terminals(before);new_routes=reaches_terminals(after)
    lost=sorted((protected_nodes&old_routes)-new_routes)
    return {'passed':not(split or missing_protected or lost),'new_island_splits':split,
        'missing_protected_node_ids':missing_protected,'lost_protected_terminal_route_node_ids':lost,
        'remaining_component_count':len(new_components),'remaining_link_count':len(after),
        'limitation':'Nominal directed routing and weak connectivity only, not capacity or operating performance.'}


def plan_small_runs(nodes: Mapping[str,Mapping[str,Any]],links: Mapping[str,Mapping[str,Any]], *,
        candidate_link_ids: Iterable[str],protected_link_ids: Iterable[str],protected_node_ids: Iterable[str],
        loaded_node_ids: Iterable[str],terminal_node_ids: Iterable[str],policy: Mapping[str,Any],
        reference_omission_ids: Iterable[str]=(),retired_link_ids: Iterable[str]=()) -> dict[str,Any]:
    """Propose complete peripheral trees, pendant loops and redundant minor corridors.

    Parameters
    ----------
    nodes, links : mapping
        Exact identities. Links declare from_node/to_node/kind and positive
        length_m/diameter_m for gravity. SI metres only; non-gravity links always
        protected. Additional source evidence is retained in the input fingerprint.
    candidate_link_ids : iterable of str
        Caller-selected scope-review footprint, not an instruction to delete.
    protected_link_ids, protected_node_ids : iterable of str
        Explicit baseline/trunk/station/control/connection roles. Every other
        noncandidate link is retained. Loaded nodes remain hard protections.
    loaded_node_ids, terminal_node_ids : iterable of str
        Native hydraulic loading nodes and valid terminal boundaries. Load
        aggregation must be reviewed/applied by caller before removing protection;
        this planner never silently transfers or discards loads.
    policy : mapping
        maximum_group_length_m, maximum_group_pipe_count, maximum_minor_diameter_m,
        dimensional_tolerance_m and allow_isolated_component_omission(Boolean).
        These are scope-review thresholds, never proof of negligible real flow.
    reference_omission_ids : iterable of str
        Optional model-comparison context only; cannot override any protection.
    retired_link_ids : iterable of str
        Separately authorized lifecycle retirement input. No lifecycle inference.

    Returns
    -------
    dict
        Exact whole groups, proposal hashes, input fingerprint, load allocation
        review targets and individual/joint retained-graph guards. Recommendations
        are review-only; callers require separate exact approvals before writing.

    Notes
    -----
    Topology-only peeling determines each complete peripheral tree BEFORE size
    thresholds. This prevents repeatedly deleting short pieces of a large branch.
    Edge blocks preserve complete one-attachment loops; both paths form one group.
    Two-anchor serial corridors require a separate directed alternate route.
    All recommendations together must preserve retained components and protected
    terminal routes. Reference deletions do not establish status or validity.
    """
    node_ids=set(nodes);link_ids=set(links)
    if any(not isinstance(k,str)or not k for k in node_ids|link_ids):raise ValueError('Exact nonempty identities required')
    candidates=_ids(candidate_link_ids,link_ids,'candidate_link_ids');protected_links=_ids(protected_link_ids,link_ids,'protected_link_ids')
    protected_nodes=_ids(protected_node_ids,node_ids,'protected_node_ids');loaded=_ids(loaded_node_ids,node_ids,'loaded_node_ids')
    terminals=_ids(terminal_node_ids,node_ids,'terminal_node_ids');retired=_ids(retired_link_ids,link_ids,'retired_link_ids')
    reference=_ids(reference_omission_ids,link_ids,'reference_omission_ids')
    if retired&protected_links:raise ValueError('Retired link cannot also be protected')
    required={'maximum_group_length_m','maximum_group_pipe_count','maximum_minor_diameter_m','dimensional_tolerance_m','allow_isolated_component_omission'}
    if required-set(policy):raise ValueError('Missing small-run scope policy')
    count=policy['maximum_group_pipe_count']
    if isinstance(count,bool)or not isinstance(count,int)or count<1:raise ValueError('Positive integer group count required')
    for key in ('maximum_group_length_m','maximum_minor_diameter_m','dimensional_tolerance_m'):
        value=policy[key]
        if isinstance(value,bool)or not isinstance(value,(int,float))or not math.isfinite(value)or value<0 or value==0 and key!='dimensional_tolerance_m':raise ValueError('Invalid SI scope threshold: '+key)
    if not isinstance(policy['allow_isolated_component_omission'],bool):raise ValueError('Explicit isolated-component Boolean required')
    for key,row in links.items():
        if row.get('from_node')not in nodes or row.get('to_node')not in nodes or row['from_node']==row['to_node']:raise ValueError('Unknown/self link endpoint: '+key)
        if not isinstance(row.get('kind'),str)or not row['kind']:raise ValueError('Explicit link kind required')
        if row['kind']=='gravity':
            for field in ('length_m','diameter_m'):
                value=row.get(field)
                if isinstance(value,bool)or not isinstance(value,(int,float))or not math.isfinite(value)or value<=0:raise ValueError('Positive finite gravity geometry required: '+key+'/'+field)
        elif key not in retired:protected_links.add(key)
    active=link_ids-retired;retained=(active-candidates)|protected_links
    active_endpoints={n for key in active for n in (links[key]['from_node'],links[key]['to_node'])}
    if (loaded-terminals)-active_endpoints:
        raise ValueError('Loaded nodes lack active incidence after separate retirements; review load transfers first: '+','.join(sorted((loaded-terminals)-active_endpoints)))
    locked=protected_nodes|loaded|terminals|{n for k in retained for n in (links[k]['from_node'],links[k]['to_node'])}
    eligible=(candidates-retained)&active
    source={'nodes':nodes,'links':links,'candidate_link_ids':sorted(candidates),'protected_link_ids':sorted(protected_links),
        'protected_node_ids':sorted(protected_nodes),'loaded_node_ids':sorted(loaded),'terminal_node_ids':sorted(terminals),
        'retired_link_ids':sorted(retired),'reference_omission_ids':sorted(reference),'policy':dict(policy)}
    input_hash=sha256(json.dumps(source,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    adjacency={n:{k for k,other in edges}for n,edges in _adjacency(links,active).items()}
    queue=deque(sorted(n for n,edges in adjacency.items()if len(edges)==1 and n not in locked));peeled=set()
    while queue:
        node=queue.popleft();edges=adjacency.get(node,set())
        if node in locked or len(edges)!=1:continue
        key=next(iter(edges))
        if key not in eligible:continue
        row=links[key];other=row['to_node']if row['from_node']==node else row['from_node']
        edges.remove(key);adjacency[other].remove(key);peeled.add(key)
        if len(adjacency[other])==1 and other not in locked:queue.append(other)
    removed_nodes={n for k in peeled for n in (links[k]['from_node'],links[k]['to_node'])if not adjacency.get(n)and n not in locked}
    raw=[];assigned=set()
    for first in sorted(peeled):
        if first in assigned:continue
        group={first};assigned.add(first);todo=[first]
        while todo:
            key=todo.pop()
            for node in (links[key]['from_node'],links[key]['to_node']):
                if node not in removed_nodes:continue
                for next_key in sorted(peeled-assigned):
                    if node in (links[next_key]['from_node'],links[next_key]['to_node']):assigned.add(next_key);group.add(next_key);todo.append(next_key)
        raw.append({'kind':'peripheral_tree','link_ids':group})
    remaining=active-peeled;consumed=set();loop_edges=set()
    for block in _blocks(links,remaining):
        vertices={n for k in block for n in (links[k]['from_node'],links[k]['to_node'])}
        if len(block)<len(vertices)or not block<=eligible:continue
        attachments={n for n in vertices if n in locked or any(k not in block for k in adjacency.get(n,()))}
        if len(attachments)>1:continue
        interior=vertices-attachments;attached=[g for g in raw if any(n in interior for k in g['link_ids']for n in (links[k]['from_node'],links[k]['to_node']))]
        ids=block|set().union(*(g['link_ids']for g in attached)) if attached else set(block)
        consumed.update(id(g)for g in attached);raw.append({'kind':'pendant_parallel_loop','link_ids':ids});loop_edges|=block
    raw=[g for g in raw if id(g)not in consumed]
    serial=eligible-peeled-loop_edges;visited=set()
    def boundary(node: str) -> bool:
        return node in locked or len(adjacency.get(node,()))!=2 or any(k not in serial for k in adjacency.get(node,()))
    for start in sorted(adjacency):
        if not boundary(start):continue
        for first in sorted(adjacency[start]&serial):
            if first in visited:continue
            corridor=[];current=start;edge=first
            while edge not in visited:
                visited.add(edge);corridor.append(edge);row=links[edge];other=row['to_node']if row['from_node']==current else row['from_node']
                if boundary(other):break
                options=adjacency[other]-{edge}
                if len(options)!=1:break
                current=other;edge=next(iter(options))
            if not corridor:continue
            ids=set(corridor);end=other
            if start==end:continue
            interior={n for k in ids for n in (links[k]['from_node'],links[k]['to_node'])}-{start,end}
            attached=[g for g in raw if g['kind']=='peripheral_tree'and any(n in interior for k in g['link_ids']for n in (links[k]['from_node'],links[k]['to_node']))]
            ids|=set().union(*(g['link_ids']for g in attached))if attached else set()
            consumed.update(id(g)for g in attached)
            raw.append({'kind':'redundant_serial_corridor','link_ids':ids,'corridor_anchor_ids':[start,end]})
    raw=[g for g in raw if id(g)not in consumed]
    included=set().union(*(g['link_ids']for g in raw))if raw else set()
    if eligible-included:raw.append({'kind':'complex_unresolved_scope','link_ids':eligible-included})
    groups=[]
    for item in sorted(raw,key=lambda g:sorted(g['link_ids'])):
        ids=set(item['link_ids']);vertices={n for k in ids for n in (links[k]['from_node'],links[k]['to_node'])}
        attachments={n for n in vertices if n in locked or any(k not in ids for k,other in _adjacency(links,active).get(n,()))}
        interior=vertices-attachments;reasons=[];length=sum(float(links[k]['length_m'])for k in sorted(ids));diameter=max(float(links[k]['diameter_m'])for k in ids)
        if len(ids)>count:reasons.append('whole_group_pipe_count_exceeds_scope_policy')
        if length>policy['maximum_group_length_m']+policy['dimensional_tolerance_m']:reasons.append('whole_group_length_exceeds_scope_policy')
        if diameter>policy['maximum_minor_diameter_m']+policy['dimensional_tolerance_m']:reasons.append('diameter_exceeds_minor_scope_policy')
        if interior&(protected_nodes|loaded|terminals):reasons.append('protected_interior_node')
        if ids&retained:reasons.append('protected_link')
        if not attachments and not policy['allow_isolated_component_omission']:reasons.append('isolated_component_requires_explicit_scope_review')
        if item['kind']=='complex_unresolved_scope':reasons.append('complex_topology_requires_manual_scope_review')
        alternate=None
        if item['kind']=='redundant_serial_corridor':
            a,b=item['corridor_anchor_ids'];forward=_path(links,ids,a,b);reverse=_path(links,ids,b,a)
            if forward is not None:alternate=_path(links,active-ids,a,b)
            elif reverse is not None:alternate=_path(links,active-ids,b,a)
            if alternate is None:reasons.append('required_or_ambiguous_directed_anchor_connection')
        guard=_guard(links,active,ids,locked,terminals)
        if not guard['passed']:reasons.append('retained_network_guard_failed')
        record={'kind':item['kind'],'link_ids':sorted(ids),'internal_node_ids':sorted(interior),'attachment_node_ids':sorted(attachments),
            'total_length_m':length,'maximum_diameter_m':diameter,'pipe_count':len(ids),
            'recommended_action':'recommend_omit'if not reasons else'hold_review','hold_reasons':reasons,
            'reference_omission_link_ids':sorted(ids&reference),'reference_is_not_authority':True,
            'alternate_directed_link_path':alternate,'retained_network_guard':guard,
            'tributary_load_review':{'recorded_loaded_internal_node_ids':sorted(interior&loaded),
                'allocation_target_node_ids':sorted(attachments),'no_recorded_load_does_not_mean_zero_real_sewage':True,
                'basis':'Caller must preserve modeled loads and separately allocate future tributary loading; no loading is transferred here.'}}
        record['proposal_id']='SCOPE-'+sha256(json.dumps({'input':input_hash,'group':record},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:24]
        groups.append(record)
    recommended=set().union(*(set(g['link_ids'])for g in groups if g['recommended_action']=='recommend_omit'))if groups else set()
    joint=_guard(links,active,recommended,locked,terminals)
    if not joint['passed']:
        for group in groups:
            if group['recommended_action']=='recommend_omit':group['recommended_action']='hold_review';group['hold_reasons'].append('joint_omission_guard_failed')
        recommended=set()
        # Approval identities bind the final held disposition, not a stale pre-guard action.
        for group in groups:
            record={key:value for key,value in group.items()if key!='proposal_id'}
            group['proposal_id']='SCOPE-'+sha256(json.dumps({'input':input_hash,'group':record},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:24]
    return {'status':'review_only_no_model_or_load_changes','input_fingerprint':input_hash,'policy':dict(policy),'groups':groups,
        'recommended_omit_link_ids':sorted(recommended),'held_link_ids':sorted(eligible-recommended),
        'protected_candidate_link_ids':sorted(candidates&retained),'separately_retired_link_ids':sorted(retired),
        'joint_retained_network_guard':joint,'load_protection_node_ids':sorted(loaded),
        'limitations':['Thresholds express model scope, not asset obsolescence or negligible tributary loading.',
            'Directed alternate routes do not establish capacity, headloss equivalence or operating validity.',
            'Reference deletions cannot authorize lifecycle retirement or override loads, controls or stations.',
            'Exact proposals require caller approval, current-input binding and SHMM preview/written-model QA.']}
