"""Prepare reviewed, quantity-conserving native SWMM load relocation plans.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-21
Pure preparation only. SHMM owns model publication and written-model checks.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from hashlib import sha256
from typing import Any, Mapping, Sequence

from hymo import SWMMDocument

from .lifecycle import _hash, _key, _native, _references, _review, _text, lifecycle_record_fingerprint
from .reporting import stable_hash

_NODE_SECTIONS = ("JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS")
_LOAD_SECTIONS = ("DWF", "INFLOWS", "RDII")
_DEFINITIONS = ("PATTERNS", "TIMESERIES", "HYDROGRAPHS", "RAINGAGES")
_INLET_SOURCE = "https://raw.githubusercontent.com/USEPA/Stormwater-Management-Model/v5.2.4/src/solver/inflow.c"
_RDII_SOURCE = "https://raw.githubusercontent.com/USEPA/Stormwater-Management-Model/v5.2.4/src/solver/rdii.c"


def _decimal(value: Any, label: str) -> Decimal:
    """Read a finite explicitly serialized decimal without binary rounding."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " requires a finite decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(label + " requires a finite decimal string") from error
    if not number.is_finite():
        raise ValueError(label + " must be finite")
    return number


def _fraction(value: str, label: str) -> Fraction:
    """Keep finite decimal quantities exact through split and merge arithmetic."""
    return Fraction(_decimal(value, label))


def _serialized(value: Fraction) -> str:
    """Serialize an exact finite-decimal result with adequate local precision."""
    # Products/sums of finite decimals have denominators with only2/5 factors.
    # The precision below exceeds both integer digit counts, avoiding the
    # process-global Decimal context's otherwise silent28-digit rounding.
    with localcontext() as context:
        context.prec = max(32, len(str(abs(value.numerator))) + len(str(value.denominator)) + 2)
        result = Decimal(value.numerator) / Decimal(value.denominator)
    return format(result, "f")


def _load_rows(document: SWMMDocument, identity: str) -> list[dict[str, Any]]:
    """Read every supported native load row at one case-insensitive node ID."""
    return [{"section": section, "tokens": list(record.tokens),
             "record_sha256": lifecycle_record_fingerprint(section, record.tokens)}
            for section in _LOAD_SECTIONS for record in document.records(section)
            if _key(record.tokens[0]) == _key(identity)]


def _catalog(document: SWMMDocument) -> dict[tuple[str, str], list[list[str]]]:
    """Collect complete named definition blocks, including continuation rows."""
    records: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    for section in _DEFINITIONS:
        for record in document.records(section):
            records[section, _key(record.tokens[0])].append(list(record.tokens))
    return dict(records)


def _definition_roots(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """Read only documented load-reference columns; malformed rows remain QA."""
    result = set()
    for row in rows:
        tokens, section = row["tokens"], row["section"]
        if section == "DWF":
            result.update(("PATTERNS", _key(token)) for token in tokens[3:7] if token)
        elif section == "INFLOWS":
            if len(tokens) > 2 and tokens[2]: result.add(("TIMESERIES", _key(tokens[2])))
            if len(tokens) > 7 and tokens[7]: result.add(("PATTERNS", _key(tokens[7])))
        elif section == "RDII" and len(tokens) > 1 and tokens[1]:
            result.add(("HYDROGRAPHS", _key(tokens[1])))
    return result


def _closure(document: SWMMDocument, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bind load definitions, UH rain-gage references and external file tokens."""
    catalog = _catalog(document)
    pending, seen, result = sorted(_definition_roots(rows)), set(), []
    while pending:
        section, identity = pending.pop(0)
        if (section, identity) in seen: continue
        seen.add((section, identity))
        records = catalog.get((section, identity))
        files = []
        for tokens in records or []:
            if section == "HYDROGRAPHS" and len(tokens) == 2:
                pending.append(("RAINGAGES", _key(tokens[1])))
            elif section == "RAINGAGES" and len(tokens) >= 6:
                if tokens[4].upper() == "TIMESERIES": pending.append(("TIMESERIES", _key(tokens[5])))
                elif tokens[4].upper() == "FILE": files.append(tokens[5])
            elif section == "TIMESERIES" and len(tokens) >= 3 and tokens[1].upper() == "FILE":
                files.append(tokens[2])
        result.append({"section": section, "object_id": identity, "records": records,
            "definition_sha256": stable_hash(records), "external_files": files})
    return sorted(result, key=lambda row: (row["section"], row["object_id"]))


def node_load_fingerprint(document: SWMMDocument, node_id: str) -> str:
    """Fingerprint a node, all native load rows and their definition closure.

    Parameters
    ----------
    document : SWMMDocument
        Exact source or target document; no files are opened by this helper.
    node_id : str
        Existing native node identity under SWMM ASCII case-insensitive matching.

    Returns
    -------
    str
        SHA256 of exact node/load tokens and referenced pattern/UH/series/gage
        definition records. External file paths are bound, contents are unread.

    Raises
    ------
    ValueError
        The native node is missing or ambiguous.
    """
    identity = _key(_text(node_id, "node id"))
    nodes = _native(document, _NODE_SECTIONS)
    if identity not in nodes: raise ValueError("Load fingerprint node is absent")
    rows = _load_rows(document, identity)
    return stable_hash({"node": nodes[identity], "loads": rows, "definitions": _closure(document, rows)})


def _component(row: Mapping[str, Any]) -> dict[str, Any]:
    """Interpret supported quantity coefficients while retaining original tokens."""
    tokens, section = list(row["tokens"]), row["section"]
    if section == "DWF":
        if not 3 <= len(tokens) <= 7: raise ValueError("Unsupported DWF row shape")
        if tokens[1].upper() != "FLOW": raise ValueError("Pollutant concentration migration is unsupported")
        coefficients = {"average_flow": _fraction(tokens[2], "DWF average flow")}
        signature = tuple(_key(t) for t in tokens[3:] + [""] * (7-len(tokens)))
        series = None
    elif section == "INFLOWS":
        if not 3 <= len(tokens) <= 8: raise ValueError("Unsupported INFLOWS row shape")
        if tokens[1].upper() != "FLOW": raise ValueError("Pollutant inflow migration is unsupported")
        if len(tokens) > 3 and tokens[3].upper() != "FLOW": raise ValueError("Unsupported FLOW inflow type")
        scale = tokens[5] if len(tokens) > 5 else "1"
        baseline = tokens[6] if len(tokens) > 6 else "0"
        coefficients = {"series_scale": _fraction(scale, "INFLOWS scale"), "baseline_flow": _fraction(baseline, "INFLOWS baseline")}
        # FLOW conversion is imposed by native FLOW_UNITS in EPA inflow.c.
        # Retain the serialized type/units-factor conservatively for merging.
        signature = ((tokens[3].upper() if len(tokens) > 3 else "FLOW"),
                     _fraction(tokens[4] if len(tokens) > 4 else "1", "FLOW units-factor token"), _key(tokens[7]) if len(tokens) > 7 else "")
        series = _key(tokens[2]) if tokens[2] else None
    else:
        if len(tokens) != 3 or not tokens[1]: raise ValueError("Unsupported RDII row shape")
        area = _fraction(tokens[2], "RDII area")
        if area < 0: raise ValueError("RDII area must be nonnegative")
        coefficients = {"sewered_area": area}
        signature, series = (_key(tokens[1]),), None
    return {"section": section, "tokens": tokens, "signature": signature,
            "series": series, "coefficients": coefficients}


def _scaled(component: Mapping[str, Any], target: str, weight: Fraction) -> dict[str, Any]:
    """Scale actual quantity coefficients, preserving opaque native references."""
    result = deepcopy(dict(component)); tokens = result["tokens"]; tokens[0] = target
    result["coefficients"] = {key: value * weight for key, value in component["coefficients"].items()}
    if weight == 1: return result
    if component["section"] in {"DWF", "RDII"}:
        tokens[2] = _serialized(next(iter(result["coefficients"].values())))
    else:
        defaults = [target, tokens[1], tokens[2], "FLOW", "1", "1", "0"]
        tokens.extend(defaults[len(tokens):7])
        # q(t) = cf * [sf*TS(t) + baseline*pattern(t)]. A split scales
        # BOTH terms; scaling the baseline alone changes the hydrograph shape.
        tokens[5] = _serialized(result["coefficients"]["series_scale"])
        tokens[6] = _serialized(result["coefficients"]["baseline_flow"])
    return result


def _merge(components: Sequence[Mapping[str, Any]], target: str) -> dict[str, Any]:
    """Combine only one exactly representable native node/quantity load slot."""
    if len({c["signature"] for c in components}) != 1:
        raise ValueError("Incompatible patterns, unit factors or unit hydrographs")
    named_series = {c["series"] for c in components if c["series"]}
    if len(named_series) > 1: raise ValueError("Different time series cannot share one native FLOW slot")
    first = components[0]; section = first["section"]
    tokens = list(first["tokens"]); tokens[0] = target
    if len(components) == 1:
        return {"tokens": tokens, "coefficients": dict(first["coefficients"])}
    summed = {name: sum((c["coefficients"][name] for c in components), Fraction(0)) for name in first["coefficients"]}
    if section in {"DWF", "RDII"}:
        tokens[2] = _serialized(next(iter(summed.values())))
    else:
        selected = next((c for c in components if c["series"]), first)
        defaults = [target, tokens[1], selected["tokens"][2], "FLOW", "1", "1", "0"]
        tokens.extend(defaults[len(tokens):7]); tokens[2] = selected["tokens"][2]
        if named_series:
            # Empty-series scale factors have no active term. They must not be
            # added onto a named series when constant and varying flows merge.
            summed["series_scale"] = sum((c["coefficients"]["series_scale"] for c in components if c["series"]), Fraction(0))
            tokens[5] = _serialized(summed["series_scale"])
        else:
            summed["series_scale"] = first["coefficients"]["series_scale"]
            tokens[5] = _serialized(summed["series_scale"])
        tokens[6] = _serialized(summed["baseline_flow"])
    return {"tokens": tokens, "coefficients": summed}


def _flow_units(document: SWMMDocument) -> str | None:
    """Require an explicit compatible native quantity unit basis."""
    rows = [r.tokens for r in document.records("OPTIONS") if r.tokens[0].upper() == "FLOW_UNITS"]
    if len(rows) > 1: raise ValueError("Duplicate FLOW_UNITS option")
    return rows[0][1].upper() if rows and len(rows[0]) == 2 else None


def plan_load_migration(
    source_document: SWMMDocument, target_document: SWMMDocument, *,
    retired_node_ids: Sequence[str] = (), mappings: Sequence[Mapping[str, Any]],
    context_sha256: str | None = None,
) -> dict[str, Any]:
    """Plan explicit load relocation, split allocation and compatible merging.

    Parameters
    ----------
    source_document, target_document : SWMMDocument
        Exact read-only source and proposed physical target. They may be the
        same checkpoint. Rebuilt destination nodes must exist in target_document.
    retired_node_ids : sequence of str, optional
        Source-node removal obligations. Every loaded retired node must map;
        mappings may also explicitly clear loads from retained source nodes.
    mappings : sequence of mapping
        Exact keys id/source_node_id/source_node_record_sha256/
        source_load_fingerprint/destinations/basis/reviewer/date/decision.
        Destination keys node_id/node_record_sha256/load_fingerprint/weight.
        Weight is a positive decimal string; each source's weights sum exactly1.
    context_sha256 : str, optional
        Caller-verified private review evidence digest, retained without file I/O.

    Returns
    -------
    dict
        Source/target byte hashes, records, weighted allocations, coefficient
        conservation, definitions and proposed_sections. executable_intent is
        present only for an entirely ready plan. Missing definitions return held,
        nonexecutable copy intents; prepare target and replan after review.

    Raises
    ------
    ValueError
        Malformed/duplicate mappings, stale node/load hashes or invalid weights.

    Notes
    -----
    Supports FLOW DWF, FLOW INFLOWS and RDII only. Concentrations and quality
    coupling are held, never added or split as quantity loads. Native flow units
    must be identical. DWF average and RDII area scale linearly; external inflow
    SF and baseline both scale. Different definitions cannot be flattened into a
    shared native slot. No hydrograph resampling or artificial series generation
    occurs. Identical external FILE tokens retain their references with a content
    warning; no external data are read or certified. No geometry is retired here.
    """
    if context_sha256 is not None: _hash(context_sha256, "context fingerprint")
    if not isinstance(retired_node_ids, (list, tuple)) or not isinstance(mappings, (list, tuple)):
        raise ValueError("Retired nodes and mappings must be explicit sequences")
    source_nodes = _native(source_document, _NODE_SECTIONS)
    target_nodes = _native(target_document, _NODE_SECTIONS)
    retired = {_key(_text(identity, "retired node")) for identity in retired_node_ids}
    if len(retired) != len(retired_node_ids) or retired - set(source_nodes):
        raise ValueError("Retired node IDs must be unique existing source nodes")
    held, reviews, seen_ids, sources, targets = [], [], set(), set(), set()
    required = {"id", "source_node_id", "source_node_record_sha256", "source_load_fingerprint",
                "destinations", "basis", "reviewer", "date", "decision"}
    for raw in mappings:
        if not isinstance(raw, Mapping) or set(raw) != required: raise ValueError("Load mapping requires exact reviewed schema keys")
        row = deepcopy(dict(raw)); _review(row)
        identity = _text(row["id"], "mapping id"); source = _key(_text(row["source_node_id"], "source node"))
        if identity in seen_ids or source in sources or source not in source_nodes:
            raise ValueError("Duplicate mapping identity or absent/duplicate source node")
        seen_ids.add(identity); sources.add(source)
        if _hash(row["source_node_record_sha256"], "source node fingerprint") != source_nodes[source]["sha256"]:
            raise ValueError("Stale source node fingerprint")
        if _hash(row["source_load_fingerprint"], "source load fingerprint") != node_load_fingerprint(source_document, source):
            raise ValueError("Stale source load fingerprint")
        if not _load_rows(source_document, source): held.append("mapped_source_has_no_load:" + source)
        if _load_rows(target_document, source) and _load_rows(source_document, source) != _load_rows(target_document, source):
            held.append("source_load_changed_in_target:" + source)
        if row["decision"] != "approved": held.append("mapping_not_approved:" + identity)
        destinations = row["destinations"]
        if not isinstance(destinations, list) or not destinations: raise ValueError("Mapping requires explicit destinations")
        weights, seen_destinations = [], set()
        for destination in destinations:
            if not isinstance(destination, Mapping) or set(destination) != {"node_id", "node_record_sha256", "load_fingerprint", "weight"}:
                raise ValueError("Destination requires exact node/hash/weight schema")
            key = _key(_text(destination["node_id"], "destination node"))
            if key in seen_destinations or key not in target_nodes: raise ValueError("Duplicate or absent destination node")
            seen_destinations.add(key); targets.add(key)
            if _hash(destination["node_record_sha256"], "target node fingerprint") != target_nodes[key]["sha256"]:
                raise ValueError("Stale target node fingerprint")
            if _hash(destination["load_fingerprint"], "target load fingerprint") != node_load_fingerprint(target_document, key):
                raise ValueError("Stale target load fingerprint")
            weight = _fraction(destination["weight"], "split weight")
            if weight <= 0 or weight > 1: raise ValueError("Split weights must be positive and at most1")
            weights.append(weight)
        if sum(weights, Fraction(0)) != 1: raise ValueError("Split weights must sum exactly1")
        reviews.append(row)
    for key in retired:
        if _load_rows(source_document, key) and key not in sources: held.append("retired_loaded_node_without_mapping:" + key)
        if _load_rows(target_document, key) and key not in sources: held.append("retired_target_load_without_mapping:" + key)
    if targets & retired: held.append("destination_is_retired_node")
    if targets & sources: held.append("mapping_chains_or_self_destination_unsupported")
    units = _flow_units(source_document)
    if units not in {"CFS", "GPM", "MGD", "CMS", "LPS", "MLD"} or units != _flow_units(target_document):
        held.append("native_flow_units_missing_or_different")
    for document, label in ((source_document, "source"), (target_document, "target")):
        refs = _references(document, set(source_nodes if label == "source" else target_nodes), set())
        for reference in refs:
            if _key(reference["asset_id"]) in retired and reference["kind"] in {"unsupported_dependency", "opaque_token_occurrence"}:
                held.append("retired_node_unsupported_dependency:" + label + ":" + reference["section"] + ":" + reference["asset_id"])
    allocations, conservation, definition_reviews, external_files = [], [], {}, []
    target_definitions = {}
    copy_intents = []; target_catalog = _catalog(target_document)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    destination_names = {key: target_nodes[key]["tokens"][0] for key in targets}
    for key in sorted(targets):
        seen = set()
        target_rows = _load_rows(target_document, key)
        for definition in _closure(target_document, target_rows):
            definition_key = definition["section"], definition["object_id"]
            target_definitions[definition_key] = deepcopy(definition)
            if definition["records"] is None:
                held.append("missing_target_destination_definition:" + "/".join(definition_key))
            for path in definition["external_files"]:
                external_files.append({"section": definition_key[0], "object_id": definition_key[1], "path_token": path,
                    "status": "unverified_external_content", "note": "Only identical native definition/path tokens are checked. File contents, accessibility and runnable forcing are not validated."})
        for row in target_rows:
            section = row["section"]
            if section in seen: held.append("duplicate_or_quality_target_load_slot:" + section + ":" + key)
            seen.add(section)
            try: component = _component(row)
            except ValueError as error:
                held.append("unsupported_target_load:" + key + ":" + str(error)); continue
            groups[section, key].append(component)
    for review in reviews:
        source = _key(review["source_node_id"]); rows = _load_rows(source_document, source)
        seen = set()
        for row in rows:
            section = row["section"]
            if section in seen: held.append("duplicate_or_quality_source_load_slot:" + section + ":" + source)
            seen.add(section)
            try: component = _component(row)
            except ValueError as error:
                held.append("unsupported_source_load:" + source + ":" + str(error)); continue
            coefficient_sums = {key: Fraction(0) for key in component["coefficients"]}
            for destination in review["destinations"]:
                target = _key(destination["node_id"]); weight = _fraction(destination["weight"], "split weight")
                contribution = _scaled(component, destination_names[target], weight)
                groups[section, target].append(contribution)
                for name, value in contribution["coefficients"].items(): coefficient_sums[name] += value
                allocations.append({"mapping_id": review["id"], "section": section,
                    "source_node_id": source_nodes[source]["tokens"][0], "target_node_id": destination_names[target],
                    "weight": destination["weight"], "source_record": deepcopy(row),
                    "allocated_tokens": contribution["tokens"],
                    "coefficients": {name: _serialized(value) for name, value in contribution["coefficients"].items()}})
            conservation.append({"mapping_id": review["id"], "section": section,
                "source_node_id": source_nodes[source]["tokens"][0],
                "before": {k: _serialized(v) for k, v in component["coefficients"].items()},
                "allocated": {k: _serialized(v) for k, v in coefficient_sums.items()},
                "passed": coefficient_sums == component["coefficients"],
                "series_scale_active": bool(component["series"]),
                "units": "dimensionless_series_scale_and_" + str(units) if section == "INFLOWS" else "acres" if section == "RDII" and units in {"CFS", "GPM", "MGD"} else "hectares" if section == "RDII" else units})
        for definition in _closure(source_document, rows):
            key = definition["section"], definition["object_id"]
            if key in definition_reviews: continue
            definition_reviews[key] = deepcopy(definition)
            if definition["records"] is None:
                held.append("missing_source_definition:" + "/".join(key)); continue
            if key not in target_catalog:
                held.append("missing_target_definition:" + "/".join(key))
                copy_intents.append({**deepcopy(definition), "executable": False, "action": "prepare_target_definition_then_replan"})
            elif target_catalog[key] != definition["records"]:
                held.append("changed_target_definition:" + "/".join(key))
            for path in definition["external_files"]:
                external_files.append({"section": key[0], "object_id": key[1], "path_token": path,
                    "status": "unverified_external_content", "note": "Only identical native definition/path tokens are checked. File contents, accessibility and runnable forcing are not validated."})
    merged, group_conservation = {}, []
    for key, components in sorted(groups.items()):
        try:
            merged[key] = _merge(components, destination_names[key[1]])
            interpreted = _component({"section": key[0], "tokens": merged[key]["tokens"]})
            expected = {name: sum((component["coefficients"][name] for component in components), Fraction(0))
                        for name in components[0]["coefficients"]}
            actual = dict(interpreted["coefficients"])
            series_active = any(component["series"] for component in components)
            if key[0] == "INFLOWS":
                if series_active:
                    expected["series_scale"] = sum((component["coefficients"]["series_scale"] for component in components if component["series"]), Fraction(0))
                else:
                    # A blank-series SF is not an active hydrograph coefficient.
                    # Baseline flow must still conserve exactly after rendering.
                    expected.pop("series_scale"); actual.pop("series_scale")
            group_conservation.append({"section": key[0], "target_node_id": destination_names[key[1]],
                "component_count": len(components), "series_scale_active": series_active,
                "expected": {name: _serialized(value) for name, value in expected.items()},
                "rendered": {name: _serialized(value) for name, value in actual.items()},
                "passed": expected == actual})
        except ValueError as error:
            held.append("unrepresentable_target_load:" + "/".join(key) + ":" + str(error))
    proposed_sections = {}
    for section in _LOAD_SECTIONS:
        output, emitted = [], set()
        for record in target_document.records(section):
            key = _key(record.tokens[0])
            if key in sources or key in retired: continue
            if key in targets:
                if key not in emitted and (section, key) in merged:
                    output.append(merged[section, key]["tokens"]); emitted.add(key)
            else: output.append(list(record.tokens))
        for (family, key), row in sorted(merged.items()):
            if family == section and key not in emitted:
                output.append(row["tokens"]); emitted.add(key)
        proposed_sections[section] = output
    if not all(row["passed"] for row in conservation): held.append("coefficient_conservation_failed")
    if not all(row["passed"] for row in group_conservation): held.append("group_coefficient_conservation_failed")
    intent = None
    if not held:
        intent = {"sections": proposed_sections,
            "clear_source_node_ids": sorted(source_nodes[key]["tokens"][0] for key in sources),
            "affected_target_node_ids": sorted(destination_names.values())}
    return {"schema_version": 1, "status": "held" if held else "ready",
        "source_model_sha256": sha256(source_document.data).hexdigest(),
        "target_model_sha256": sha256(target_document.data).hexdigest(),
        "context_sha256": context_sha256,
        "review_sha256": stable_hash({"retired_node_ids": retired_node_ids, "mappings": mappings}),
        "native_flow_units": units, "retired_node_ids": list(retired_node_ids),
        "mappings": reviews, "allocations": allocations, "conservation": conservation,
        "group_conservation": group_conservation, "target_definitions": [target_definitions[key] for key in sorted(target_definitions)],
        "definitions": list(definition_reviews.values()), "definition_copy_intents": copy_intents,
        "unverified_external_files": list({stable_hash(row): row for row in external_files}.values()), "held": sorted(set(held)),
        "proposed_sections": proposed_sections, "sections": proposed_sections if not held else None, "executable_intent": intent,
        "basis": {"inflow_dwf": _INLET_SOURCE, "rdii": _RDII_SOURCE},
        "limitations": ["Quantity-record migration only; no geometry retirement, model writing or catchment allocation inference.",
            "Patterns, time series and UH records retain exact symbolic definitions; external file contents are unverified.",
            "Pollutant concentrations, control/GW/LID/subcatchment remaps and incompatible hydrograph synthesis are unsupported holds.",
            "A ready plan does not establish hydraulic capacity, mass balance, actual model execution or engineering acceptance."]}
