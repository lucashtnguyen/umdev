"""Engineering QA for prepared and reconstructed node/pipe profiles.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. Inputs and residuals use metres, grades are m/m.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from .records import finite_number
from .evidence import tolerance_metres


def finding(rule: str, object_key: str, config: Mapping[str, Any], **details: Any) -> dict[str, Any]:
    """Build one versioned engineering finding with configured treatment."""
    policy = config.get("qa", {}).get("rules", {}).get(rule, {})
    return {"rule": rule, "rule_version": 1, "object_key": object_key,
            "severity": policy.get("severity", "warning"),
            "disposition": policy.get("disposition", "review"),
            "review_status": "unreviewed", **details}


def profile_findings(nodes: Sequence[Mapping[str, Any]], pipes: Sequence[Mapping[str, Any]],
                     config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check reconstructed rims, individual pipe ends, crowns and gravity grades.

    Notes
    -----
    Pipe grade = (upstream pipe invert - downstream pipe invert) / hydraulic
    length. Junction-floor differences are not a substitute. Gravity adverse
    grades are findings by default, not reasons to reverse pipe connectivity.
    """
    results = []
    tolerance = tolerance_metres(config.get("qa", {}).get("readback_absolute_tolerance"), default=1e-9)
    node_map = {row["asset_id"]: row for row in nodes}
    for node in nodes:
        floor = finite_number(node.get("floor_m"))
        rim = finite_number(node.get("rim_m"))
        depth = finite_number(node.get("max_depth_m"))
        if floor is None or rim is None or depth is None:
            continue
        if rim <= floor:
            results.append(finding("rim_not_above_floor", node["key"], config,
                                   residual_m=rim - floor, unit="m"))
        if abs(floor + depth - rim) > tolerance:
            results.append(finding("rim_reconstruction_mismatch", node["key"], config,
                                   residual_m=floor + depth - rim, unit="m"))
    for pipe in pipes:
        if pipe.get("disposition") == "hold":
            continue
        upstream = finite_number(pipe.get("upstream_invert_m"))
        downstream = finite_number(pipe.get("downstream_invert_m"))
        length = finite_number(pipe.get("length_m"))
        diameter = finite_number(pipe.get("diameter_m"))
        if None in (upstream, downstream, length, diameter) or length <= 0:
            continue
        if pipe.get("pipe_kind") == "gravity" and upstream < downstream:
            results.append(finding("adverse_gravity_grade", pipe["key"], config,
                                   residual_m=upstream - downstream,
                                   grade=(upstream - downstream) / length, unit="m"))
        for endpoint, node_field, elevation in (("upstream", "from_node", upstream),
                                                ("downstream", "to_node", downstream)):
            node = node_map.get(pipe[node_field], {})
            rim = finite_number(node.get("rim_m"))
            if rim is not None and elevation + diameter > rim:
                results.append(finding("pipe_crown_above_rim", pipe["key"], config,
                    endpoint=endpoint, node_id=pipe[node_field], unit="m",
                    residual_m=elevation + diameter - rim))
    return results


def connectivity_findings(pipes: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Report all weak network components without inventing hydraulic boundaries."""
    graph: dict[str, set[str]] = defaultdict(set)
    for pipe in pipes:
        if pipe.get("disposition") == "hold":
            continue
        upstream, downstream = pipe["from_node"], pipe["to_node"]
        graph[upstream].add(downstream)
        graph[downstream].add(upstream)
    unseen = set(graph)
    components = []
    while unseen:
        seed = min(unseen)
        component = set()
        pending = [seed]
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            unseen.discard(node)
            pending.extend(graph[node] - component)
        components.append(sorted(component))
    if len(components) <= 1:
        return []
    return [finding("disconnected_component", component[0], config,
                    node_ids=component, component_index=index, component_count=len(components))
            for index, component in enumerate(components)]
