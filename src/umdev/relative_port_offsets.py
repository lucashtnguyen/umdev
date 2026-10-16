"""Preserve reviewed local pipe-end differences without resolving absolute datum.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained preparation helper. No source or model files are modified.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

from .observation_holds import observation_fingerprint
from .records import finite_number, network_from_dict

_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_UNKNOWN_BASES = {'', 'UNKNOWN', 'N/A', 'NA', 'NONE', 'UNSPECIFIED', 'NOT KNOWN'}


def _text(value: Any, label: str) -> str:
    """Require an explicit identity or review basis without coercion."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' requires nonempty text')
    return value


def _hash(value: Any, label: str) -> str:
    """Require a lower-case source/observation SHA-256 fingerprint."""
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(label + ' requires lowercase SHA256')
    return value


def validate_relative_offset_frames(frames: Any) -> None:
    """Validate exact source-bound reviews; an empty list disables the helper.

    Parameters
    ----------
    frames : list of mapping
        Each frame requires id, node_key, node_source_sha256, frame_id, basis,
        reviewer, ISO date, reference_observation_key, observations and endpoints.
        Observation bindings contain observation_key/expected_fingerprint.
        Endpoints contain observation_key/pipe_key/pipe_source_sha256/end.

    Raises
    ------
    ValueError
        Unknown keys, invalid hashes/dates or duplicate frame/endpoint identities.
    """
    if not isinstance(frames, list):
        raise ValueError('relative_offset_frames must be a list')
    required = {'id', 'node_key', 'node_source_sha256', 'frame_id', 'basis', 'reviewer', 'date',
                'reference_observation_key', 'observations', 'endpoints'}
    ids, nodes, targets = set(), set(), set()
    for frame in frames:
        if not isinstance(frame, Mapping) or set(frame) != required:
            raise ValueError('Relative frame requires only its exact reviewed schema keys')
        for key in required - {'observations', 'endpoints'}:
            _text(frame[key], 'Relative frame ' + key)
        _hash(frame['node_source_sha256'], 'Relative frame node source')
        if frame['id'] in ids or frame['node_key'] in nodes:
            raise ValueError('Duplicate relative frame id or node; model separate chambers explicitly')
        ids.add(frame['id']); nodes.add(frame['node_key'])
        if date.fromisoformat(frame['date']).isoformat() != frame['date']:
            raise ValueError('Relative frame date must be ISO YYYY-MM-DD')
        if not isinstance(frame['observations'], list) or not frame['observations']:
            raise ValueError('Relative frame requires reviewed observations')
        observed = set()
        for row in frame['observations']:
            if not isinstance(row, Mapping) or set(row) != {'observation_key', 'expected_fingerprint'}:
                raise ValueError('Relative observation requires only observation_key and expected_fingerprint')
            key = _text(row['observation_key'], 'Relative observation key')
            _hash(row['expected_fingerprint'], 'Relative observation fingerprint')
            if key in observed:
                raise ValueError('Duplicate reviewed relative observation')
            observed.add(key)
        if frame['reference_observation_key'] not in observed:
            raise ValueError('Relative reference must be an explicitly fingerprinted observation')
        if not isinstance(frame['endpoints'], list) or not frame['endpoints']:
            raise ValueError('Relative frame requires explicit represented endpoint bindings')
        for row in frame['endpoints']:
            if not isinstance(row, Mapping) or set(row) != {'observation_key', 'pipe_key', 'pipe_source_sha256', 'end'}:
                raise ValueError('Relative endpoint requires exact observation/pipe/hash/end keys')
            _text(row['pipe_key'], 'Relative pipe key')
            _hash(row['pipe_source_sha256'], 'Relative pipe source')
            if row['observation_key'] not in observed or row['end'] not in {'upstream', 'downstream'}:
                raise ValueError('Relative endpoint must name a reviewed observation and upstream/downstream end')
            target = (row['pipe_key'], row['end'])
            if target in targets:
                raise ValueError('Duplicate relative pipe endpoint; DROP points cannot be selected by magnitude')
            targets.add(target)


def _basis(value: Any) -> str:
    """Classify an opaque declared source basis without assigning any transform."""
    label = '' if value is None else str(value).strip().upper()
    if label in _UNKNOWN_BASES:
        return 'unknown'
    if any(symbol in label for symbol in ('/', '|', ',', ';')) or ' AND ' in label or 'MIXED' in label:
        return 'mixed'
    return label


def relative_port_offset_plan(canonical: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare reviewed within-node offsets in SI, deriving no absolute elevation.

    Parameters
    ----------
    canonical : mapping
        Full ingested nodes/pipes/ports, preserving immutable source-record hashes.
    config : mapping
        Parser configuration and opt-in ports.assignment.relative_offset_frames.
        Explicit reviews assert one compatible local vertical frame per node.

    Returns
    -------
    dict
        Ready relative-only endpoint assignments, complete frame evidence, held
        frames and drop-protection flags. Absolute floor/end/rim values are not
        assigned. Source observations/config are unchanged.

    Raises
    ------
    ValueError
        Stale/missing source or observation guards, conflicting bindings, wrong
        endpoint/role, or a reference above another reviewed local observation.

    Notes
    -----
    For a common additive vertical frame, h_i = (z_i + C) - (z_ref + C)
    = z_i - z_ref. The unknown C cancels. All collected readings already use
    metres; no project conversion is done here. The reference must be the lowest
    explicitly reviewed compatible observation, incoming OR outgoing; it may be
    unmodeled. A later independently inferred model floor can be paired with
    these relative values under an explicit floor policy. Unknown raw readings
    never become absolute model anchors, even when one offset is zero.

    Mixed vertical labels, held/invalid observations and nonpositive/mismatched
    pipe diameters hold the complete frame. Distinct source-side in/out readings
    retain their sign; an outgoing port above an incoming one is not flattened.
    Pressure/gravity hydraulic connectivity is outside this vertical-only helper.
    Existing known model pipe ends must be protected separately by the caller.
    """
    frames = config.get('ports', {}).get('assignment', {}).get('relative_offset_frames', [])
    validate_relative_offset_frames(frames)
    result: dict[str, Any] = {'schema_version': 1, 'kind': 'umdev_relative_port_offset_plan',
        'absolute_elevations_derived': False, 'relative_unit': 'm', 'assignments': [], 'frames': [], 'held': [],
        'review_configuration_sha256': sha256(json.dumps(frames, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()}
    if not frames:
        return result
    from .evidence import collect_structure_ports, tolerance_metres
    network = network_from_dict(canonical)
    nodes = {row['key']: row for row in network.nodes}
    pipes = {row['key']: row for row in network.pipes}
    by_key: dict[str, list[dict[str, Any]]] = {}
    for observation in collect_structure_ports(network, config):
        by_key.setdefault(observation['key'], []).append(observation)
    size_tolerance = tolerance_metres(config.get('ports', {}).get('assignment', {}).get('diameter_tolerance'))
    for frame in frames:
        node = nodes.get(frame['node_key'])
        if node is None or node.get('source_record_sha256') != frame['node_source_sha256']:
            raise ValueError('Missing or stale relative frame node source: ' + frame['node_key'])
        observations, reasons = {}, []
        for reference in frame['observations']:
            key = reference['observation_key']; matches = by_key.get(key, [])
            if len(matches) != 1:
                raise ValueError('Relative observation must match exactly once: ' + key)
            row = matches[0]
            if row.get('structure_id') != node['asset_id']:
                raise ValueError('Relative observations must belong to one reviewed node')
            if '_reviewed_hold' in row or 'reviewed_observation_hold' in row.get('flags', []):
                reasons.append('reviewed_observation_held:' + key)
            else:
                if observation_fingerprint(row) != reference['expected_fingerprint']:
                    raise ValueError('Stale relative observation fingerprint: ' + key)
            if row.get('flags') or finite_number(row.get('invert')) is None:
                reasons.append('invalid_or_held_relative_observation:' + key)
            observations[key] = row
        bases = {_basis(row.get('vertical_datum')) for row in observations.values()}
        if 'mixed' in bases or _basis(node.get('vertical_datum')) == 'mixed':
            reasons.append('mixed_vertical_basis')
        elif len(bases) != 1:
            reasons.append('incompatible_vertical_bases')
        bindings = []
        for endpoint in frame['endpoints']:
            pipe = pipes.get(endpoint['pipe_key'])
            if pipe is None or pipe.get('source_record_sha256') != endpoint['pipe_source_sha256']:
                raise ValueError('Missing or stale relative pipe source: ' + endpoint['pipe_key'])
            field, role = ('from_node', 'out') if endpoint['end'] == 'upstream' else ('to_node', 'in')
            observation = observations[endpoint['observation_key']]
            normalized_role = {'inlet': 'in', 'outlet': 'out'}.get(observation.get('role'), observation.get('role'))
            if pipe.get(field) != node['asset_id'] or normalized_role != role:
                raise ValueError('Relative endpoint/node/observation role conflict')
            for key in ('pipe_key', 'pipe_id'):
                if observation.get(key) and observation[key] not in {pipe['key'], pipe['asset_id']}:
                    raise ValueError('Relative endpoint conflicts with source port pipe reference')
            observed_size, pipe_size = finite_number(observation.get('diameter')), finite_number(pipe.get('diameter'))
            if pipe_size is None or pipe_size <= 0:
                reasons.append('missing_or_invalid_relative_endpoint_pipe_diameter:' + endpoint['pipe_key'])
            elif observed_size is not None and (observed_size <= 0 or abs(observed_size - pipe_size) > size_tolerance):
                reasons.append('relative_port_pipe_diameter_conflict:' + endpoint['observation_key'])
            bindings.append((endpoint, pipe, observation))
        report = {'review_id': frame['id'], 'node_key': node['key'], 'node_id': node['asset_id'],
            'frame_id': frame['frame_id'], 'node_source_sha256': frame['node_source_sha256'],
            'review': deepcopy(frame), 'source_observations': deepcopy(list(observations.values())),
            'absolute_elevations_derived': False, 'absolute_vertical_basis': 'unresolved' if bases == {'unknown'} else 'unchanged_source_basis',
            'unreviewed_observation_keys': sorted(key for key, rows in by_key.items() if any(row.get('structure_id') == node['asset_id'] for row in rows) and key not in observations)}
        if reasons:
            report.update(status='hold', reasons=sorted(set(reasons)), node_drop_protection=True)
            result['held'].append({'review_id': frame['id'], 'node_key': node['key'], 'reasons': report['reasons']})
            result['frames'].append(report)
            continue
        reference = observations[frame['reference_observation_key']]
        local_zero = min(row['invert'] for row in observations.values())
        if reference['invert'] != local_zero:
            raise ValueError('Relative reference is not the lowest reviewed observation; incoming ports must also be considered')
        offsets = {key: row['invert'] - local_zero for key, row in observations.items()}
        if any(not math.isfinite(value) or value < 0 for value in offsets.values()):
            raise ValueError('Invalid relative offset arithmetic')
        local_relations = [{'incoming_observation_key': key, 'outgoing_observation_key': out_key,
                           'incoming_minus_outgoing_m': row['invert'] - out_row['invert']}
                          for key, row in observations.items() if row.get('role') in {'in', 'inlet'}
                          for out_key, out_row in observations.items() if out_row.get('role') in {'out', 'outlet'}]
        drop = any(value != 0 for value in offsets.values())
        report.update(status='ready_relative_only', reference_observation_key=reference['key'],
            relative_offsets_m=offsets, node_drop_protection=drop,
            local_in_out_relations=local_relations,
            flags=['reviewed_common_local_frame_assumption', 'relative_only_no_absolute_datum'] + (['preserved_local_port_difference'] if drop else []))
        result['frames'].append(report)
        for endpoint, pipe, observation in bindings:
            result['assignments'].append({'review_id': frame['id'], 'node_key': node['key'], 'node_id': node['asset_id'],
                'source_pipe_key': pipe['key'], 'source_pipe_id': pipe['asset_id'], 'end': endpoint['end'],
                'relative_offset_m': offsets[observation['key']], 'absolute_elevation_m': None,
                'observation_key': observation['key'], 'reference_observation_key': reference['key'],
                'frame_id': frame['frame_id'], 'node_drop_protection': drop,
                'method': 'reviewed_local_relative_port_offset', 'flags': report['flags'],
                'evidence': {'observation': deepcopy(observation), 'reference': deepcopy(reference),
                             'review': deepcopy(frame), 'source_pipe_sha256': endpoint['pipe_source_sha256']}})
    return result
