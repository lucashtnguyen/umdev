"""Fail-closed reviewed holds for exact, source-bound structure observations.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained; no source measurement is deleted or numerically changed.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

_SHA256=re.compile(r'[0-9a-f]{64}')


def validate_observation_holds(holds: object) -> None:
    """Validate exact review bindings independently of project orchestration.

    Parameters
    ----------
    holds : mapping
        Exact observation keys mapped to expected_fingerprint, reason, reviewer
        and ISO-8601 date. Empty mappings disable the opt-in mechanism.

    Raises
    ------
    ValueError
        An entry lacks its hash, review rationale, reviewer or valid ISO date.
    """
    if not isinstance(holds,Mapping): raise ValueError('observation_holds must be an object')
    required={'expected_fingerprint','reason','reviewer','date'}
    for key,row in holds.items():
        if not isinstance(key,str) or not key.strip(): raise ValueError('Observation hold requires an exact nonempty key')
        if not isinstance(row,Mapping) or set(row)!=required: raise ValueError('Observation hold requires only expected_fingerprint, reason, reviewer, date')
        if any(not isinstance(row[n],str) or not row[n].strip() for n in required): raise ValueError('Observation hold fields must be nonempty strings')
        if not _SHA256.fullmatch(row['expected_fingerprint']): raise ValueError('Observation hold fingerprint must be lowercase SHA256')
        try:
            if date.fromisoformat(row['date']).isoformat()!=row['date']: raise ValueError()
        except ValueError as exc: raise ValueError('Observation hold date must be ISO YYYY-MM-DD') from exc


def observation_fingerprint(observation: Mapping[str,Any]) -> str:
    """Hash the complete pre-hold observation including raw and source evidence.

    Parameters
    ----------
    observation : mapping
        A fresh collected port with key and immutable source_record_sha256.
        Parsed value, units/datum, raw text and attribution metadata are bound.

    Returns
    -------
    str
        SHA256 of canonical UTF-8 JSON; dictionary order does not affect it.

    Raises
    ------
    ValueError
        Evidence lacks source binding, is already held or is not finite JSON.

    Notes
    -----
    A changed source row, parser result or reference requires renewed review.
    No normalization is performed here; exact raw strings remain distinguishable.
    """
    if not isinstance(observation.get('key'),str) or not observation['key'].strip(): raise ValueError('Observation fingerprint requires exact key')
    source=observation.get('source_record_sha256')
    if not isinstance(source,str) or not _SHA256.fullmatch(source): raise ValueError('Observation fingerprint requires source_record_sha256')
    if '_reviewed_hold' in observation or 'reviewed_observation_hold' in observation.get('flags',[]): raise ValueError('Fingerprint fresh unheld observation evidence')
    try: payload=json.dumps(dict(observation),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError) as exc: raise ValueError('Observation fingerprint requires finite JSON evidence') from exc
    return sha256(payload.encode('utf8')).hexdigest()


def apply_observation_holds(observations: Sequence[Mapping[str,Any]], holds: Mapping[str,Any]) -> list[dict[str,Any]]:
    """Copy observations and mark reviewed records unusable without editing values.

    Parameters
    ----------
    observations : sequence of mapping
        Fresh collected observations, before reviewed hold flags are attached.
    holds : mapping
        Validated exact observation bindings; missing/stale keys fail closed.

    Returns
    -------
    list of dict
        Independent records retaining raw/parsed values and reviewed evidence.
        Flags prohibit held rows from port assignment and floor selection.

    Raises
    ------
    ValueError
        A reviewed key is missing/duplicated or its fingerprint is stale.
    """
    validate_observation_holds(holds)
    result=deepcopy(list(observations))
    if not holds: return result
    by_key={}
    for row in result: by_key.setdefault(row.get('key'),[]).append(row)
    for key,review in holds.items():
        matches=by_key.get(key,[])
        if len(matches)!=1: raise ValueError(f'Reviewed observation hold must match exactly once: {key}')
        row=matches[0];actual=observation_fingerprint(row)
        if actual!=review['expected_fingerprint']: raise ValueError(f'Stale reviewed observation hold fingerprint: {key}')
        flags=list(row.get('flags',[]));flags.append('reviewed_observation_hold');row['flags']=flags
        row['_reviewed_hold']={**deepcopy(dict(review)),'observation_key':key,'actual_fingerprint':actual,'disposition':'excluded_from_resolution_and_floor_selection','raw_and_parsed_values_preserved':True}
    return result
