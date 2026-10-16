"""Resolve utility observations with explicit evidence ranking and QA flags.

Author: Lucas HT Nguyen
Last modified by: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained. Canonical numeric elevations/diameters use metres.
"""
from __future__ import annotations

from collections import defaultdict
import math
import re
from typing import Any, Mapping, Sequence

from .records import Assignment, Network, finite_number
from .observation_holds import apply_observation_holds
from .units import to_metres

COMPASS_DEGREES = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
                   "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}
_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
_INLINE = re.compile(
    rf'^\s*(?P<elevation>{_NUMBER})\s*(?P<elevation_unit>ft|feet|m|\x27|′)?\s*'
    rf'(?:\(\s*(?P<direction>NE|NW|SE|SW|N|E|S|W)\s*\))?\s*,?\s*'
    rf'(?:(?P<diameter>{_NUMBER})\s*(?P<diameter_unit>"|″|in(?:ch(?:es)?)?|mm|m)?)?\s*$', re.I)


def tolerance_metres(value: Any, default: float = 0.0) -> float:
    """Read an explicitly dimensioned tolerance, validating nonnegative finiteness."""
    if isinstance(value, Mapping):
        result = to_metres(value["value"], value["unit"])
    else:
        result = finite_number(value) if value is not None else default
    if result is None or result < 0 or not math.isfinite(result):
        raise ValueError("Tolerance must be finite and nonnegative")
    return result


def parse_inline_port(text: Any, *, node_id: str, key: str, role: str,
                      elevation_unit: str, diameter_unit: str,
                      vertical_datum: str | None = None) -> dict[str, Any]:
    """Parse one configured elevation/compass/diameter observation.

    Parameters
    ----------
    text : object
        Raw inline description. Elevations accept an optional unit or feet mark.
        Diameters accept explicit units; an omitted diameter suffix requires an
        explicit parenthesized compass and uses the configured diameter unit.
    node_id, key, role : str
        Structure identity, source-qualified observation identity and hydraulic
        structure role (in or out).
    elevation_unit, diameter_unit : str
        Declared measurement units. An explicit incompatible unit remains held.
    vertical_datum : str, optional
        Source vertical basis; unknown bases remain unresolved downstream.

    Returns
    -------
    dict
        SI values with exact raw text, parsing provenance and findings. Multiple,
        drop, capped or qualified descriptions remain unresolved observations.

    Notes
    -----
    A feet mark on an elevation is an explicit unit, never a numeric correction.
    A feet mark on a diameter is not reinterpreted as inches. Bare numeric pairs
    without a compass do not establish which number is the size. Negative/zero
    elevations are valid; a nonpositive diameter remains invalid.
    """
    raw = "" if text is None else str(text)
    match = _INLINE.fullmatch(raw)
    record = {"key": key, "structure_id": node_id, "role": role,
              "family": "inline_port", "raw": raw, "vertical_datum": vertical_datum,
              "invert": None, "diameter": None, "direction": None, "flags": []}
    if not match or (match["diameter"] is not None and
                     match["diameter_unit"] is None and match["direction"] is None):
        record["flags"] = ["unrecognized_port_syntax"]
        return record
    unit_aliases = {"feet": "ft", "'": "ft", "′": "ft",
                    '"': "in", "″": "in", "inch": "in", "inches": "in"}
    parsing = {"grammar_id": "elevation_direction_diameter_v1",
               "elevation_unit": elevation_unit, "diameter_unit": diameter_unit,
               "elevation_unit_basis": "explicit" if match["elevation_unit"] else "configured",
               "diameter_unit_basis": ("not_present" if match["diameter"] is None else
                                      "explicit" if match["diameter_unit"] else "configured_omitted_suffix"),
               "normalizations": []}
    record["parsing"] = parsing
    for name, configured in (("elevation_unit", elevation_unit), ("diameter_unit", diameter_unit)):
        stated = match[name]
        stated = unit_aliases.get(stated.lower(), stated.lower()) if stated else None
        if stated is not None and to_metres(1, stated) != to_metres(1, configured):
            record["flags"] = ["port_unit_conflict"]
            return record
    if match["elevation_unit"] in {"'", "′"}:
        parsing["normalizations"].append("elevation_feet_symbol")
    if match["diameter"] is not None and match["diameter_unit"] is None:
        parsing["normalizations"].append("diameter_unit_from_configuration")
    # Unit defaults interpret missing notation without modifying measurements.
    record["invert"] = to_metres(float(match["elevation"]), elevation_unit)
    if match["diameter"] is not None:
        record["diameter"] = to_metres(float(match["diameter"]), diameter_unit)
        if record["diameter"] <= 0:
            record["flags"] = ["invalid_port_diameter"]
    record["direction"] = match["direction"].upper() if match["direction"] else None
    return record


def parse_qualified_inline_port(text: Any, *, node_id: str, key: str, role: str,
                                elevation_unit: str, diameter_unit: str,
                                vertical_datum: str | None = None) -> dict[str, Any]:
    """Parse a scalar port with explicitly recognized trailing role/drop tokens.

    Parameters
    ----------
    text : object
        Strict elevation/direction/diameter notation, optionally followed by
        whitespace-separated OUT, IN and/or DROP tokens, case insensitive.
    node_id, key, role : str
        Structure and source observation identities and configured default role.
        A single explicit IN/OUT qualifier overrides that default role.
    elevation_unit, diameter_unit : str
        Declared source units, checked by the unchanged strict scalar parser.
    vertical_datum : str, optional
        Declared source datum, never inferred from a qualifier.

    Returns
    -------
    dict
        Strict parsed values with original raw text, qualifier spans, explicit
        drop metadata and configured/effective role provenance. Unknown remarks,
        duplicate qualifiers and contradictory IN/OUT tokens remain flagged.

    Notes
    -----
    DROP identifies a physical qualification, not which side of the drop a
    measured scalar represents. Conflicting same-end drop observations must
    remain unresolved without an explicit source-to-pipe reference or reviewed
    endpoint value. Numeric magnitude never decides endpoint attribution.
    The original elevation_direction_diameter_v1 contract is unchanged.
    """
    raw = "" if text is None else str(text)
    remaining = raw
    suffixes: list[dict[str, Any]] = []
    suffix_pattern = re.compile(r"\s+(?P<qualifier>OUT|IN|DROP)\s*$", re.I)
    while match := suffix_pattern.search(remaining):
        suffixes.append({"token": match["qualifier"].upper(), "raw": match["qualifier"],
                         "start": match.start("qualifier"), "end": match.end("qualifier")})
        remaining = remaining[:match.start()]
    suffixes.reverse()
    record = parse_inline_port(remaining, node_id=node_id, key=key, role=role,
                               elevation_unit=elevation_unit, diameter_unit=diameter_unit,
                               vertical_datum=vertical_datum)
    record["raw"] = raw
    qualifiers = [item["token"] for item in suffixes]
    record["qualifiers"] = qualifiers
    record["explicit_drop"] = "DROP" in qualifiers
    parsing = record.setdefault("parsing", {"elevation_unit": elevation_unit,
                                            "diameter_unit": diameter_unit, "normalizations": []})
    parsing.update(grammar_id="qualified_elevation_direction_diameter_v1",
                   qualifier_evidence=suffixes, scalar_text=remaining,
                   configured_role=role, role_basis="configured")
    explicit_roles = {"in" if item == "IN" else "out" for item in qualifiers if item in {"IN", "OUT"}}
    if len(explicit_roles) == 1:
        record["role"] = next(iter(explicit_roles))
        parsing["role_basis"] = "explicit_suffix"
    if suffixes:
        parsing["normalizations"].append("recognized_trailing_port_qualifiers")
    if len(explicit_roles) > 1:
        record["flags"].append("conflicting_port_role_qualifiers")
    if len(qualifiers) != len(set(qualifiers)):
        record["flags"].append("duplicate_port_qualifier")
    parsing["effective_role"] = record["role"]
    return record


def normalize_elevation(value: Any, datum: str | None, config: Mapping[str, Any],
                        *, source_key: str = "", asset_id: str = "") -> Assignment:
    """Place one SI elevation on the declared target basis without guessing datum.

    Notes
    -----
    Constant shifts are already explicitly dimensioned configuration. They are
    applied equally to port, floor and rim elevations. A non-verified transform
    remains provisional; a plausible downstream slope is not verification.
    """
    number = finite_number(value)
    if number is None:
        return Assignment(None, "unresolved", "missing_elevation", flags=("missing_elevation",))
    policy = config.get("elevations", {})
    target = policy.get("target_datum") or config.get("model", {}).get("target", {}).get("vertical_datum")
    candidates = []
    for rule in policy.get("transforms", []):
        if not datum or not target or rule.get("source_datum") != datum or rule.get("target_datum") != target:
            continue
        if rule.get("asset_ids") is not None and asset_id not in rule["asset_ids"]:
            continue
        if rule.get("source_keys") is not None and source_key not in rule["source_keys"]:
            continue
        candidates.append(rule)
    if len(candidates) != 1:
        return Assignment(None, "unresolved", "vertical_basis_unresolved",
                          evidence=({"value": number, "datum": datum, "source_key": source_key},),
                          flags=("ambiguous_vertical_transform" if candidates else "unknown_vertical_basis",))
    rule = candidates[0]
    method = rule.get("method")
    if method == "identity":
        if datum != target:
            raise ValueError("Identity vertical transformation requires identical declared datums")
        shift = 0.0
    elif method == "constant_offset":
        shift_value = rule.get("offset")
        if not isinstance(shift_value, Mapping):
            raise ValueError("Constant vertical transformation requires a dimensioned offset")
        shift = to_metres(shift_value["value"], shift_value["unit"])
    else:
        raise ValueError(f"Unsupported vertical transformation method: {method!r}")
    verified = rule.get("verified") is True
    if not verified and not policy.get("allow_provisional", False):
        return Assignment(None, "unresolved", "provisional_transform_disallowed",
                          flags=("provisional_vertical_basis",))
    return Assignment(number + shift, "observed" if verified else "provisional", method,
                      evidence=({"source_key": source_key, "raw_value_m": number,
                                 "source_datum": datum, "target_datum": target,
                                 "transform_id": rule.get("id"), "shift_m": shift,
                                 "verified": verified, "basis": rule.get("basis")},),
                      flags=() if verified else ("provisional_vertical_basis",))


def _outward_bearing(pipe: Mapping[str, Any], node: Mapping[str, Any],
                      endpoint_tolerance: float) -> float | None:
    """Return local outward azimuth from uniquely identified projected line end."""
    coordinates = pipe.get("coordinates") or []
    point = node.get("coordinates") or []
    if len(coordinates) < 2 or len(point) < 2:
        return None
    try:
        if any(finite_number(v) is None for p in coordinates for v in p[:2]):
            return None
        if any(finite_number(v) is None for v in point[:2]):
            return None
        distance = [math.dist(point[:2], coordinates[index][:2]) for index in (0, -1)]
    except (TypeError, ValueError):
        return None
    matching = [index for index, value in enumerate(distance) if value <= endpoint_tolerance]
    if len(matching) != 1:
        return None
    points = coordinates if matching[0] == 0 else list(reversed(coordinates))
    for next_point in points[1:]:
        dx, dy = next_point[0] - points[0][0], next_point[1] - points[0][1]
        if math.hypot(dx, dy) > 0:
            return math.degrees(math.atan2(dx, dy)) % 360
    return None


def _direction(value: Any) -> float | None:
    """Normalize an explicit compass label or azimuth without inferring one."""
    if isinstance(value, str) and value.upper() in COMPASS_DEGREES:
        return COMPASS_DEGREES[value.upper()]
    number = finite_number(value)
    return number % 360 if number is not None else None


def choose_assignment(candidates: Sequence[Mapping[str, Any]], *, precedence: Sequence[str],
                      tolerance: float, conflict_policy: str = "prefer_ranked_with_flag") -> Assignment:
    """Choose ranked usable evidence while retaining contradictions and ties.

    Notes
    -----
    A higher-ranked missing value cannot suppress a usable lower-ranked source.
    Equal-ranked conflicting measurements never use input order as a tie breaker.
    Distinct lower-ranked enabled alternatives may be retained as provisional evidence.
    Families omitted from precedence are disabled, including fallback/conflict use.
    """
    ranking = {family: rank for rank, family in enumerate(precedence)}
    def rank(row: Mapping[str, Any]) -> tuple[int, int]:
        # Within one evidence family, a directly identified or geometry-matched
        # port is stronger than a compass-mismatch fallback. Keeping both at one
        # rank would let an unmatched weak port invalidate a unique strong one.
        association = {"explicit_pipe_reference": 0, "role_diameter_direction": 1,
                       "unique_role_diameter_fallback": 3}.get(row.get("method"), 0)
        return ranking.get(row.get("family"), len(ranking)), association
    usable = sorted((dict(row) for row in candidates if row.get("family") in ranking
                     and finite_number(row.get("value")) is not None),
                    key=lambda row: (*rank(row), str(row.get("source_key", ""))))
    if not usable:
        return Assignment(None, "unresolved", "no_usable_evidence", flags=("missing_required_evidence",))
    best_rank = rank(usable[0])
    preferred = [row for row in usable if rank(row) == best_rank]
    if max(row["value"] for row in preferred) - min(row["value"] for row in preferred) > tolerance:
        return Assignment(None, "unresolved", "equal_rank_conflict", alternatives=tuple(usable),
                          flags=("conflicting_equal_rank_evidence",))
    chosen = preferred[0]
    conflicts = [row for row in usable if abs(row["value"] - chosen["value"]) > tolerance]
    flags = set(chosen.get("flags", []))
    category = chosen.get("category", "interpreted")
    if conflicts:
        flags.add("conflicting_elevation_evidence")
        if conflict_policy != "prefer_ranked_with_flag":
            return Assignment(None, "unresolved", "conflicting_evidence_held", alternatives=tuple(usable),
                              flags=tuple(sorted(flags)))
        category = "provisional"
    if flags and category in ("observed", "interpreted"):
        category = "provisional"
    return Assignment(chosen["value"], category, chosen.get("method", chosen.get("family", "ranked_evidence")),
                      evidence=(chosen,), alternatives=tuple(row for row in usable if row is not chosen),
                      flags=tuple(sorted(flags)))


def collect_structure_ports(network: Network, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collect configured structure observations independently of pipe matching.

    Notes
    -----
    Canonical normalized port rows and parsed structure descriptions retain their
    original values, source keys, units/datum and parsing flags. An otherwise
    usable structure invert need not match an applied pipe to describe its floor.
    No numeric attribute from the pipe GIS table is included here.
    """
    ports = [{"family": "normalized_port", **dict(port)} for port in network.ports]
    node_datasets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in network.nodes:
        node_datasets[str(node.get("dataset", ""))].append(node)
    for observation in config.get("ports", {}).get("observations", []):
        if observation.get("parser") == "normalized_port_row_v1":
            continue
        parsers = {"elevation_direction_diameter_v1": parse_inline_port,
                   "qualified_elevation_direction_diameter_v1": parse_qualified_inline_port}
        if observation.get("parser") not in parsers:
            raise ValueError(f"Unsupported port parser: {observation.get('parser')!r}")
        selected_nodes = node_datasets.get(observation.get("dataset", ""), [])
        for node in selected_nodes:
            field = observation["field"]
            text = node.get(field)
            if text is None:
                text = node.get("source_attributes", {}).get(field)
            if text in (None, ""):
                continue
            units = observation["units"]
            parser = parsers[observation["parser"]]
            parsed = parser(text, node_id=node["asset_id"],
                key=f"{node['key']}/{observation['id']}", role=observation["role"],
                elevation_unit=units["elevation"], diameter_unit=units["diameter"],
                vertical_datum=node.get("vertical_datum"))
            parsed.update({"source_record_sha256": node.get("source_record_sha256"),
                           "source_key": node["key"], "source_field": field})
            ports.append(parsed)
    references = config.get("ports", {}).get("assignment", {}).get("observation_pipe_references", {})
    unknown_observations = set(references) - {port["key"] for port in ports}
    if unknown_observations:
        raise ValueError(f"Explicit pipe references name missing observations: {sorted(unknown_observations)}")
    for port in ports:
        reference = references.get(port["key"])
        if reference is None:
            continue
        if (not isinstance(reference, Mapping) or not isinstance(reference.get("pipe_key"), str)
                or not reference["pipe_key"].strip() or not isinstance(reference.get("basis"), str)
                or not reference["basis"].strip()):
            raise ValueError("Explicit observation pipe references require pipe_key and basis")
        existing_references = [port[name] for name in ("pipe_key", "pipe_id") if port.get(name)]
        pipe = next((item for item in network.pipes if item["key"] == reference["pipe_key"]), None)
        if pipe is None:
            port.setdefault("flags", []).append("unknown_port_pipe_reference")
        elif any(existing not in {pipe["key"], pipe["asset_id"]} for existing in existing_references):
            port.setdefault("flags", []).append("port_pipe_reference_conflict")
        else:
            port["pipe_key"] = reference["pipe_key"]
        port["configured_pipe_reference"] = dict(reference)
    holds = config.get("ports", {}).get("assignment", {}).get("observation_holds", {})
    return sorted(apply_observation_holds(ports, holds), key=lambda row: row["key"])


def resolve_ports(network: Network, config: Mapping[str, Any]) -> tuple[dict[tuple[str, str], Assignment], list[dict[str, Any]]]:
    """Attribute ports using full incident context before any geographic selection.

    Returns
    -------
    assignments, findings : tuple
        Assignments keyed by source pipe key and ``upstream``/``downstream``.
        Findings preserve unassigned ports independently of resolved pipe ends.
    """
    policy = config.get("ports", {}).get("assignment", {})
    precedence = policy.get("precedence", ["reviewed_override", "pipe_numeric", "normalized_port", "inline_port"])
    size_tolerance = tolerance_metres(policy.get("diameter_tolerance"))
    elevation_tolerance = tolerance_metres(policy.get("elevation_conflict_tolerance"))
    snap = tolerance_metres(policy.get("endpoint_tolerance"))
    sector = finite_number(policy.get("direction_half_sector_deg", 22.5))
    if sector is None or sector < 0 or sector > 180:
        raise ValueError("Direction half-sector must be finite between zero and 180 degrees")
    nodes = {row["asset_id"]: row for row in network.nodes}
    incidents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    findings: list[dict[str, Any]] = []
    excluded_context = set(policy.get("context_exclude_statuses", []))
    scenario = config.get("scenarios", {}).get(config.get("selection", {}).get("scenario"), {})
    excluded_context.update(scenario.get("exclude_statuses", []))
    for pipe in network.pipes:
        for end, node_field, role, field in (("upstream", "from_node", "out", "upstream_invert"),
                                             ("downstream", "to_node", "in", "downstream_invert")):
            identity = pipe.get(node_field)
            node = nodes.get(identity, {})
            if "pipe_numeric" in precedence:
                datum = pipe.get(f"{field}_datum", pipe.get("vertical_datum"))
                if datum is None and policy.get("pipe_numeric_datum") == "node":
                    datum = node.get("vertical_datum")
                assignment = normalize_elevation(pipe.get(field), datum, config, source_key=pipe["key"], asset_id=pipe["asset_id"])
                if assignment.value is not None:
                    candidates[(pipe["key"], end)].append({"value": assignment.value,
                        "source_key": pipe["key"], "field": field, "family": "pipe_numeric",
                        "category": assignment.category, "flags": list(assignment.flags),
                        "normalization": assignment.to_dict()})
            reviewed = pipe.get("_reviewed_overrides", {}).get(field)
            if reviewed is not None:
                review_value = normalize_elevation(reviewed["value_m"], reviewed["vertical_datum"], config,
                                                   source_key=pipe["key"], asset_id=pipe["asset_id"])
                if review_value.value is not None:
                    candidates[(pipe["key"], end)].append({"value": review_value.value,
                        "source_key": pipe["key"], "field": field, "family": "reviewed_override",
                        "method": "reviewed_override", "category": "provisional" if review_value.flags else "interpreted",
                        "review": reviewed, "normalization": review_value.to_dict(), "flags": list(review_value.flags)})
            if pipe.get("status") not in excluded_context:
                incidents[str(identity)].append({"pipe": pipe, "end": end, "role": role,
                    "bearing": _outward_bearing(pipe, node, snap)})
    ports = collect_structure_ports(network, config)
    for port in sorted(ports, key=lambda row: row["key"]):
        node_id = str(port.get("structure_id", port.get("node_id", "")))
        if "reviewed_observation_hold" in port.get("flags", []):
            findings.append({"rule": "reviewed_observation_hold", "object_key": port["key"],
                             "node_id": node_id, "severity": "warning", "disposition": "hold",
                             "raw": port, "review": port["_reviewed_hold"]})
            continue
        role = {"inlet": "in", "outlet": "out"}.get(port.get("role"), port.get("role"))
        related = [end for end in incidents.get(node_id, []) if end["role"] == role]
        direct = port.get("pipe_id") or port.get("pipe_key")
        size = finite_number(port.get("diameter"))
        size_matches = [end for end in related if size is None or (
            finite_number(end["pipe"].get("diameter")) is not None and
            abs(end["pipe"]["diameter"] - size) <= size_tolerance)]
        direction = _direction(port.get("direction_degrees", port.get("direction")))
        strict = [end for end in size_matches if direction is None or (
            end["bearing"] is not None and abs((end["bearing"] - direction + 180) % 360 - 180) <= sector)]
        flags = set(port.get("flags", []))
        targets: list[dict[str, Any]] = []
        method = "role_diameter_direction"
        if direct:
            targets = [end for end in related if direct in (end["pipe"]["key"], end["pipe"]["asset_id"])]
            method = "explicit_pipe_reference"
            if len(targets) == 1 and targets[0] not in size_matches:
                flags.add("port_diameter_mismatch")
        elif len(strict) == 1:
            unknown_tangents = [end for end in size_matches if direction is not None and end["bearing"] is None]
            if not unknown_tangents:
                targets = strict
        fallback = policy.get("unique_role_diameter_fallback", {}).get("enabled", False)
        if not direct and not targets and fallback and size is not None and len(size_matches) == 1:
            targets = size_matches
            method = "unique_role_diameter_fallback"
            flags.add("port_direction_unverified" if targets[0]["bearing"] is None else "port_direction_mismatch")
            custom_flag = policy.get("unique_role_diameter_fallback", {}).get("qa_flag")
            if custom_flag and targets[0]["bearing"] is not None:
                flags.add(custom_flag)
        if len(targets) != 1 or port.get("flags"):
            findings.append({"rule": "port_assignment_unresolved", "object_key": port["key"],
                             "node_id": node_id, "severity": "warning", "raw": port,
                             "candidate_pipe_keys": [end["pipe"]["key"] for end in size_matches]})
            continue
        target = targets[0]
        if direction is not None:
            bearing = target["bearing"]
            if bearing is None:
                flags.add("port_direction_unverified")
                if policy.get("direction", "hint") == "strict":
                    findings.append({"rule": "port_direction_unverified", "object_key": target["pipe"]["key"],
                                     "observation_key": port["key"], "severity": "warning", "disposition": "hold"})
                    continue
            elif abs((bearing - direction + 180) % 360 - 180) > sector:
                if policy.get("direction", "hint") == "strict":
                    findings.append({"rule": "port_direction_mismatch", "object_key": target["pipe"]["key"],
                                     "observation_key": port["key"], "severity": "warning", "disposition": "hold"})
                    continue
                flags.add("port_direction_mismatch")
        node = nodes.get(node_id, {})
        normalized = normalize_elevation(port.get("invert", port.get("elevation_m")),
            port.get("vertical_datum", node.get("vertical_datum")), config,
            source_key=port["key"], asset_id=target["pipe"]["asset_id"])
        if normalized.value is None:
            findings.append({"rule": "unresolved_port_vertical_basis", "object_key": port["key"],
                             "severity": "warning", "details": normalized.to_dict()})
            continue
        flags.update(normalized.flags)
        candidates[(target["pipe"]["key"], target["end"])].append({"value": normalized.value,
            "source_key": port["key"], "family": port.get("family", "normalized_port"),
            "method": method, "category": "provisional" if flags else "interpreted",
            "flags": sorted(flags), "raw": port, "normalization": normalized.to_dict()})
    assignments = {}
    for pipe in network.pipes:
        for end in ("upstream", "downstream"):
            field = f"{end}_invert"
            if field in pipe.get("_reviewed_override_holds", []):
                assignments[(pipe["key"], end)] = Assignment(None, "unresolved", "stale_override_hold", flags=("stale_reviewed_override",))
                continue
            endpoint_candidates = candidates[(pipe["key"], end)]
            enabled_ports = [item for item in endpoint_candidates if item.get("family") in precedence
                             and item.get("family") in {"inline_port", "normalized_port"}]
            explicit_resolution = any(item.get("method") == "explicit_pipe_reference" for item in enabled_ports)
            explicit_resolution |= any(item.get("family") == "reviewed_override" for item in endpoint_candidates
                                       if "reviewed_override" in precedence)
            drop_observations = [item for item in enabled_ports if item.get("raw", {}).get("explicit_drop")]
            conflicting_ports = (len(enabled_ports) > 1 and
                                 max(item["value"] for item in enabled_ports) - min(item["value"] for item in enabled_ports) > elevation_tolerance)
            if drop_observations and conflicting_ports and not explicit_resolution:
                # Top/bottom observations of a drop can share role, diameter
                # and azimuth. Neither their minimum nor a stronger tangent
                # match establishes which elevation belongs to this pipe end.
                assignments[(pipe["key"], end)] = Assignment(None, "unresolved", "ambiguous_drop_port",
                    alternatives=tuple(endpoint_candidates), flags=("ambiguous_drop_port",))
                findings.append({"rule": "ambiguous_drop_port", "object_key": pipe["key"], "end": end,
                                 "severity": "warning", "disposition": "hold",
                                 "observation_keys": [item["source_key"] for item in enabled_ports]})
                continue
            assignments[(pipe["key"], end)] = choose_assignment(endpoint_candidates,
                precedence=precedence, tolerance=elevation_tolerance,
                conflict_policy=policy.get("numeric_text_conflict", "prefer_ranked_with_flag"))
    return assignments, findings



def apply_reviewed_overrides(network: Network, config: Mapping[str, Any]) -> tuple[Network, list[dict[str, Any]]]:
    """Attach current scoped review decisions without replacing source observations.

    Parameters
    ----------
    network : Network
        Full canonical snapshot with per-row ``source_record_sha256`` hashes.
    config : mapping
        ``review.overrides`` contains reviewed entries loaded by orchestration.
        Each declares source_key, supported field, value, unit, evidence_sha256,
        rationale, reviewer and ISO date. Elevations also declare vertical_datum.

    Returns
    -------
    network, findings : tuple
        Copied rows with separately retained review evidence. Changed evidence
        invalidates only its scoped override. The default re-evaluates current
        observations; configured ``hold`` blocks that field pending review.
    """
    from copy import deepcopy
    from datetime import date

    policy = config.get("review", {})
    entries = policy.get("overrides", [])
    if not entries:
        return network, []
    nodes = [deepcopy(row) for row in network.nodes]
    pipes = [deepcopy(row) for row in network.pipes]
    lookup = {row["key"]: (row, "node") for row in nodes}
    lookup.update({row["key"]: (row, "pipe") for row in pipes})
    supported = {"node": {"floor", "rim"}, "pipe": {"upstream_invert", "downstream_invert", "diameter", "length"}}
    findings: list[dict[str, Any]] = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Reviewed override must be a mapping")
        required = ("source_key", "field", "unit", "evidence_sha256", "rationale", "reviewer", "date")
        if any(not isinstance(entry.get(name), str) or not entry[name].strip() for name in required):
            raise ValueError("Reviewed override requires identity, units, hash, rationale, reviewer and date")
        date.fromisoformat(entry["date"])
        identity = (entry["source_key"], entry["field"])
        if identity in seen:
            raise ValueError("Duplicate reviewed overrides for one source field")
        seen.add(identity)
        if entry["source_key"] not in lookup:
            findings.append({"rule": "reviewed_override_source_missing", "object_key": entry["source_key"],
                             "field": entry["field"], "severity": "warning", "disposition": "review"})
            continue
        row, kind = lookup[entry["source_key"]]
        if entry["field"] not in supported[kind]:
            raise ValueError(f"Unsupported reviewed override field: {entry['field']!r}")
        current_hash = row.get("source_record_sha256")
        if current_hash is None or entry["evidence_sha256"] != current_hash:
            held = policy.get("changed_evidence") == "hold"
            findings.append({"rule": "stale_reviewed_override", "object_key": row["key"],
                "field": entry["field"], "severity": "warning", "disposition": "hold" if held else "review",
                "expected_evidence_sha256": entry["evidence_sha256"], "current_evidence_sha256": current_hash})
            if held:
                row.setdefault("_reviewed_override_holds", []).append(entry["field"])
            continue
        value = to_metres(entry.get("value"), entry["unit"])
        if entry["field"] in ("diameter", "length") and value <= 0:
            raise ValueError("Reviewed length and diameter must be positive")
        if entry["field"] in ("floor", "rim", "upstream_invert", "downstream_invert") and not entry.get("vertical_datum"):
            raise ValueError("Reviewed elevation must declare its vertical datum")
        row.setdefault("_reviewed_overrides", {})[entry["field"]] = {**deepcopy(dict(entry)), "value_m": value}
    return Network(tuple(nodes), tuple(pipes), network.ports, network.manifest, network.findings), findings
