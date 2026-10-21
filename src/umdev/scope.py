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


def _short_seed_policy(value: Any) -> dict[str,Any]|None:
    """Validate an opt-in scope exception without supplying utility thresholds."""
    numeric={'seed_diameter_threshold_m','minimum_seed_run_length_m',
             'diameter_tolerance_m','length_tolerance_m'}
    if not isinstance(value,Mapping) or set(value)-({'enabled'}|numeric) or not isinstance(value.get('enabled'),bool):
        raise ValueError('short_seed_connector_branches requires an explicit enabled Boolean and known keys')
    if value['enabled'] and numeric-set(value):
        raise ValueError('Enabled short-seed rule requires all SI thresholds and tolerances')
    for key in numeric&set(value):
        number=value[key]
        if (isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number)
                or number<0 or number==0 and key in {'seed_diameter_threshold_m','minimum_seed_run_length_m'}):
            raise ValueError('Invalid short-seed SI threshold: '+key)
    for tolerance,threshold in [('diameter_tolerance_m','seed_diameter_threshold_m'),
                                ('length_tolerance_m','minimum_seed_run_length_m')]:
        if tolerance in value and threshold in value and value[tolerance]>=value[threshold]:
            raise ValueError('Short-seed tolerance must be smaller than its threshold')
    return dict(value) if value['enabled'] else None


def _short_seed_review(links: Mapping[str,Mapping[str,Any]], ids: set[str], *,
        kind: str,attachments: set[str],interior: set[str],protected_nodes: set[str],
        protected_links: set[str],loaded: set[str],terminals: set[str],
        rule: Mapping[str,Any],original_reasons: list[str],input_hash: str,
        source_graph_hash: str) -> dict[str,Any]:
    """Assess each continuous directed seed reach without merging tributaries.

    Notes
    -----
    A seed is strictly larger than threshold+tolerance. Seed continuity uses
    one incoming SEED and one outgoing SEED; a small lateral cannot split a
    continuous larger main. Seed confluences/divergences terminate a reach.
    A reach is short only when length+length_tolerance<minimum length. Every
    reach must need at least one actual small downstream connector to the sole
    retained attachment. This is a scope proposal, never a load/capacity test.
    """
    threshold=rule['seed_diameter_threshold_m']+rule['diameter_tolerance_m']
    seeds={key for key in ids if links[key]['kind']=='gravity' and links[key]['diameter_m']>threshold}
    connectors={key for key in ids if links[key]['kind']=='gravity' and links[key]['diameter_m']<=threshold}
    incoming=defaultdict(list);outgoing=defaultdict(list)
    for key in sorted(seeds):
        incoming[links[key]['to_node']].append(key);outgoing[links[key]['from_node']].append(key)
    starts=[key for key in sorted(seeds)
            if len(incoming[links[key]['from_node']])!=1 or len(outgoing[links[key]['from_node']])!=1]
    seen=set();runs=[];ambiguous=False
    attachment=next(iter(attachments)) if len(attachments)==1 else None
    for first in starts+sorted(seeds):
        if first in seen:continue
        # A residual edge without a directed start belongs to a cyclic seed
        # component. Report coverage, but never treat it as an ordinary reach.
        cyclic=first not in starts;ambiguous|=cyclic
        path=[];current=first
        while current not in seen:
            seen.add(current);path.append(current);node=links[current]['to_node']
            if len(incoming[node])!=1 or len(outgoing[node])!=1:break
            current=outgoing[node][0]
            if current in seen:cyclic=True;ambiguous=True;break
        length=math.fsum(float(links[key]['length_m'])for key in path)
        downstream=_path(links,ids,links[path[-1]]['to_node'],attachment) if attachment is not None else None
        small=[key for key in downstream or [] if key in connectors]
        runs.append({'link_ids':path,'from_node':links[path[0]]['from_node'],
            'to_node':links[path[-1]]['to_node'],'total_length_m':length,
            'below_minimum_length':length+rule['length_tolerance_m']<rule['minimum_seed_run_length_m'],
            'cyclic_or_ambiguous_trace':cyclic,'directed_attachment_route_link_ids':downstream,
            'downstream_small_connector_link_ids':small,
            'requires_small_downstream_connector':downstream is not None and bool(small)})
    vertices={n for key in ids for n in (links[key]['from_node'],links[key]['to_node'])}
    tree=len(_components(links,ids))==1 and len(ids)==len(vertices)-1
    guards={'whole_peripheral_tree':kind=='peripheral_tree' and tree,
        'single_retained_attachment':len(attachments)==1,
        'no_protected_internal_nodes':not(interior&(protected_nodes|loaded|terminals)),
        'no_protected_links':not(ids&protected_links),
        'gravity_links_only':all(links[key]['kind']=='gravity'for key in ids),
        'seed_edges_covered_once':seen==seeds and sum(len(run['link_ids'])for run in runs)==len(seeds),
        'unambiguous_seed_traces':not ambiguous,'has_large_seed_runs':bool(seeds),
        'has_small_connectors':bool(connectors),
        'all_seed_runs_below_minimum_length':bool(runs) and all(run['below_minimum_length']for run in runs),
        'every_seed_run_requires_downstream_small_connector':bool(runs) and all(run['requires_small_downstream_connector']for run in runs)}
    allowed={'whole_group_pipe_count_exceeds_scope_policy','whole_group_length_exceeds_scope_policy'}
    non_size=[reason for reason in original_reasons if reason not in allowed]
    failures=[key for key,value in guards.items()if not value]
    if non_size:failures.append('non_size_scope_hold_remains')
    eligible=not failures
    waived=[reason for reason in original_reasons if reason in allowed] if eligible else []
    return {'enabled':True,'eligible':eligible,'size_override_applied':bool(waived),
        'policy':dict(rule),'rule_configuration_fingerprint':sha256(json.dumps(dict(rule),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        'input_fingerprint':input_hash,'source_graph_fingerprint':source_graph_hash,
        'seed_link_ids':sorted(seeds),'small_connector_link_ids':sorted(connectors),'seed_runs':runs,
        'retained_attachment_node_id':attachment,'protected_guards':guards,
        'original_hold_reasons':list(original_reasons),'overridden_hold_reasons':waived,
        'non_size_hold_reasons':non_size,'eligibility_failures':failures,
        'basis':'Every continuous directed larger-pipe seed reach is shorter than the configured minimum and needs small downstream connectors. Only complete peripheral-tree length/count holds may be waived; all other protections remain binding.',
        'limitations':'This controls model scope only. Unknown real tributary loads, source roles and future allocation still require project review; no source lifecycle or physical pipe is changed.'}


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
        Optional short_seed_connector_branches is disabled when omitted. Enabled
        rules require explicit seed diameter/minimum continuous length and both
        SI tolerances. Only count/length holds on complete one-attachment trees
        can be waived; every separate seed run must be short and require a small
        downstream connector. Seed continuity ignores smaller side tributaries.
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
    short_seed_rule=_short_seed_policy(policy['short_seed_connector_branches']) if 'short_seed_connector_branches'in policy else None
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
    source_graph_hash=sha256(json.dumps({key:value for key,value in source.items()if key!='policy'},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() if short_seed_rule else None
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
        seed_review=None
        if short_seed_rule:
            seed_review=_short_seed_review(links,ids,kind=item['kind'],attachments=attachments,interior=interior,
                protected_nodes=protected_nodes,protected_links=retained,loaded=loaded,terminals=terminals,
                rule=short_seed_rule,original_reasons=reasons,input_hash=input_hash,source_graph_hash=source_graph_hash)
            reasons=[reason for reason in reasons if reason not in seed_review['overridden_hold_reasons']]
        record={'kind':item['kind'],'link_ids':sorted(ids),'internal_node_ids':sorted(interior),'attachment_node_ids':sorted(attachments),
            'total_length_m':length,'maximum_diameter_m':diameter,'pipe_count':len(ids),
            'recommended_action':'recommend_omit'if not reasons else'hold_review','hold_reasons':reasons,
            'reference_omission_link_ids':sorted(ids&reference),'reference_is_not_authority':True,
            'alternate_directed_link_path':alternate,'retained_network_guard':guard,
            'tributary_load_review':{'recorded_loaded_internal_node_ids':sorted(interior&loaded),
                'allocation_target_node_ids':sorted(attachments),'no_recorded_load_does_not_mean_zero_real_sewage':True,
                'basis':'Caller must preserve modeled loads and separately allocate future tributary loading; no loading is transferred here.'}}
        if seed_review is not None:record['short_seed_connector_review']=seed_review
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
    if short_seed_rule:
        for group in groups:
            review=group['short_seed_connector_review']
            review['joint_retained_network_guard_passed']=joint['passed']
            review['final_recommended_action']=group['recommended_action']
            review['final_hold_reasons']=list(group['hold_reasons'])
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
