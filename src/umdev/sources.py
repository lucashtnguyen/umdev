"""Read-only source packets and CSV adapters with explicit canonical field mapping.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained. Native GIS drivers are optional future adapters; exported
JSON packets retain source geometry, raw attributes and inspected metadata.
"""

from __future__ import annotations

from copy import deepcopy
import csv
import io
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .configuration import ConfigurationError, ResolvedConfig, _unique_object, validate_config
from .units import LENGTH_FACTORS, to_metres


class SourceError(ValueError):
    """Raised for unavailable adapters, invalid source schemas or identity errors."""


class SourceCapabilityError(SourceError):
    """Raised when a declared source driver is not implemented or available."""


# TODO: STUB_NATIVE_GIS_ADAPTERS — GeoPackage, File Geodatabase and native
# GeoJSON readers need inspected field/domain/CRS driver contracts and fixtures;
# use read-only JSON packets until those adapters are implemented and validated.


def source_capabilities() -> dict[str, dict[str, Any]]:
    """Report available adapters without importing proprietary GIS packages."""
    return {name: {"available": name in {"json", "json_packet", "csv"},
                   "read_only": True,
                   "diagnostic": "implemented" if name in {"json", "json_packet", "csv"} else "Native GIS adapter unavailable; export an inspected JSON packet."}
            for name in ("json", "json_packet", "csv", "geojson", "geopackage", "file_geodatabase")}


def _read_source(source: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    format_id = source["format"]
    if not source_capabilities()[format_id]["available"]:
        raise SourceCapabilityError(f"{format_id} adapter is unavailable; use read-only exported JSON packet or CSV")
    path = Path(source["path"])
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SourceError(f"Cannot read source {path}: {exc}") from exc
    encoding = source.get("encoding", "utf-8-sig")
    try:
        text = data.decode(encoding)
        if format_id == "csv":
            reader = csv.DictReader(io.StringIO(text), delimiter=source.get("delimiter", ","))
            fields = reader.fieldnames
            if not fields or len(set(fields)) != len(fields):
                raise SourceError("CSV requires unique header field names")
            content = {"records": list(reader), "fields": fields}
        else:
            content = json.loads(text, object_pairs_hook=_unique_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError, csv.Error) as exc:
        raise SourceError(f"Invalid {format_id} source {path}: {exc}") from exc
    return content, {"format": format_id, "path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _table(content: Any, name: str | None) -> dict[str, Any]:
    if name is None:
        value = content
    elif isinstance(content, dict):
        tables = content.get("datasets", content.get("data", content))
        if not isinstance(tables, dict) or name not in tables:
            raise SourceError(f"Source table not found: {name!r}")
        value = tables[name]
    else:
        raise SourceError("Named tables require a JSON object container")
    if isinstance(value, list):
        value = {"records": value}
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise SourceError(f"Table {name!r} requires a records list")
    if not all(isinstance(row, dict) for row in value["records"]):
        raise SourceError(f"Table {name!r} rows must be objects")
    return value


def matches_predicates(row: Mapping[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Evaluate the restricted configured predicate contract without executing SQL.

    Notes
    -----
    Numeric predicates on canonical SI values may declare their comparison unit.
    Missing values fail ordered comparisons. Equality retains raw types.
    """
    for rule in rules:
        field = rule["field"]
        if field not in row:
            raise SourceError(f"Predicate references missing field {field!r}")
        actual = row[field]
        expected = rule.get("values", rule.get("value"))
        if "unit" in rule:
            expected = [to_metres(item, rule["unit"]) for item in expected] if isinstance(expected, list) else to_metres(expected, rule["unit"])
        operator = rule["operator"]
        if operator == "equals":
            passed = actual == expected
        elif operator in {"in", "not_in"}:
            passed = actual in expected
            if operator == "not_in":
                passed = not passed
        elif actual is None:
            passed = False
        else:
            try:
                passed = {"lt": lambda: actual < expected, "lte": lambda: actual <= expected,
                          "gt": lambda: actual > expected, "gte": lambda: actual >= expected}[operator]()
            except (TypeError, KeyError) as exc:
                raise SourceError(f"Invalid comparison for {field!r}: {operator}") from exc
        if not passed:
            return False
    return True


def _identifier(value: Any, *, trim: bool, context: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SourceError(f"Identifier {context} must be text or an integer, never a float")
    result = str(value).strip() if trim else str(value)
    if not result:
        raise SourceError(f"Identifier {context} cannot be empty")
    return result


def _crs_id(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("epsg", value.get("latestWkid", value.get("wkid", value.get("wkt"))))
    return value


def _projected_crs(value: Any, unit: str) -> Any:
    """Inspect authoritative projected XY axes without transforming coordinates.

    Notes
    -----
    Equal CRS labels alone do not establish linear units: geographic angular
    coordinates and geocentric XYZ are not valid planar network geometry. PROJ's
    database supplies CRS classification and axis-to-metre conversion factors.
    The configured source unit must agree with both projected XY axis units.
    """
    try:
        from pyproj import CRS
        from pyproj.exceptions import CRSError
    except ImportError as exc:
        raise SourceCapabilityError("Authoritative CRS inspection requires the declared pyproj dependency; install umdev dependencies before geometry ingestion") from exc
    identifier = _crs_id(value)
    if identifier is None:
        raise SourceError("Geometry CRS metadata is missing; planar coordinates cannot be inferred")
    try:
        crs = CRS.from_user_input(identifier)
    except (CRSError, TypeError, ValueError) as exc:
        raise SourceError(f"Unrecognized geometry CRS: {value!r}") from exc
    if not crs.is_projected or crs.is_compound or len(crs.axis_info) != 2:
        raise SourceError(f"Geometry requires a projected two-dimensional linear CRS, received {crs.to_string()}; geographic/angular, geocentric and compound coordinates require an explicit upstream transformation")
    declared_factor = LENGTH_FACTORS[unit]
    for axis in crs.axis_info:
        factor = axis.unit_conversion_factor
        if not isinstance(factor, (int, float)) or not math.isfinite(factor) or not math.isclose(factor, declared_factor, rel_tol=1e-12, abs_tol=1e-15):
            raise SourceError(f"Geometry linear unit {unit!r} conflicts with CRS {crs.to_string()} axis {axis.name!r} unit {axis.unit_name!r} (metre factor {factor!r}); correct source metadata or explicitly transform upstream")
    return crs


def _geometry(raw: Any, mapping: Mapping[str, Any], metadata: Mapping[str, Any], entity: str) -> tuple[Any, list[str]]:
    if raw is None:
        return None, ["missing_geometry"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, ["invalid_geometry_json"]
    if not isinstance(raw, dict):
        return None, ["invalid_geometry"]
    actual_crs = raw.get("spatialReference", metadata.get("crs", metadata.get("spatialReference")))
    declared = mapping.get("declared_crs")
    if declared is not None and actual_crs is None:
        raise SourceError("Geometry CRS metadata is missing; declared CRS cannot relabel unknown source coordinates")
    unit = mapping["linear_unit"]
    if metadata.get("linear_unit") is not None and metadata["linear_unit"] != unit:
        raise SourceError("Geometry linear unit conflicts with source metadata")
    source_crs = _projected_crs(actual_crs, unit)
    if declared is not None:
        declared_crs = _projected_crs(declared, unit)
        if not declared_crs.equals(source_crs):
            raise SourceError(f"Geometry CRS mismatch: declared {declared!r}, source {actual_crs!r}")
    if "curvePaths" in raw or "curveRings" in raw:
        return None, ["unsupported_curved_geometry"]
    if entity == "node":
        if "x" in raw and "y" in raw:
            coords = [raw["x"], raw["y"]]
        elif raw.get("type") == "Point":
            coords = raw.get("coordinates")
        else:
            return None, ["invalid_point_geometry"]
    else:
        paths = raw.get("paths")
        if paths is not None:
            if not isinstance(paths, list) or len(paths) != 1:
                return None, ["unsupported_multipart_geometry"]
            coords = paths[0]
        elif raw.get("type") == "LineString":
            coords = raw.get("coordinates")
        elif raw.get("type") == "MultiLineString":
            return None, ["unsupported_multipart_geometry"]
        else:
            return None, ["invalid_line_geometry"]
    try:
        def point(value: Any) -> list[float]:
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                raise ValueError("XY pair required")
            return [to_metres(value[0], unit), to_metres(value[1], unit)]
        if entity == "node":
            return point(coords), []
        if not isinstance(coords, list) or len(coords) < 2:
            raise ValueError("Line requires two points")
        points = [point(p) for p in coords]
        if not any(a != b for a, b in zip(points, points[1:])):
            return None, ["degenerate_line_geometry"]
        # Only XY are hydraulic-map coordinates. Z/M remain in the raw geometry;
        # neither is silently interpreted as a port or node elevation.
        return points, []
    except (TypeError, ValueError, IndexError):
        return None, ["nonfinite_or_invalid_geometry"]


def ingest_sources(config: Mapping[str, Any] | ResolvedConfig) -> dict[str, Any]:
    """Read configured datasets and normalize engineering values to SI.

    Parameters
    ----------
    config : mapping or ResolvedConfig
        Valid schema-version-1 configuration with explicit source field mappings.

    Returns
    -------
    dict
        ``pipes``, ``nodes``, ``ports``, source ``manifest`` and ingestion findings.
        Each row preserves source attributes, raw geometry and measurement provenance.

    Raises
    ------
    SourceError
        For missing mapped fields, invalid identities, CRS mismatch or malformed sources.
    SourceCapabilityError
        For unavailable native GIS adapters. No source is silently omitted.
    """
    data = config.data if isinstance(config, ResolvedConfig) else dict(config)
    validate_config(data)
    loaded: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    for name, source in data.get("sources", {}).items():
        loaded[name], manifest[name] = _read_source(source)
    result: dict[str, Any] = {"pipes": [], "nodes": [], "ports": [], "manifest": manifest, "findings": []}
    seen_assets: dict[str, set[str]] = {"pipe": set(), "node": set(), "port": set()}
    for name, mapping in data.get("datasets", {}).items():
        source = mapping["source"]
        table = _table(loaded[source], mapping.get("table"))
        metadata = table.get("metadata", {})
        fields = table.get("fields")
        if fields is not None:
            names = {item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else item[0] for item in fields}
        else:
            names = set().union(*(set(row) for row in table["records"])) if table["records"] else set()
        expected = {mapping["id_field"], *(field["source"] for field in mapping["fields"].values()), *(rule["field"] for rule in mapping.get("filters", []))}
        if "geometry" in mapping:
            expected.add(mapping["geometry"]["field"])
        if expected - names:
            raise SourceError(f"Dataset {name} missing mapped source fields: {sorted(expected - names)}")
        seen_rows: set[str] = set()
        for raw in table["records"]:
            if not matches_predicates(raw, mapping.get("filters", [])):
                continue
            row_id = _identifier(raw.get(mapping["id_field"]), trim=False, context=f"{name}.row_id")
            if row_id in seen_rows:
                raise SourceError(f"Duplicate row identity in {name}: {row_id}")
            seen_rows.add(row_id)
            # Escaping slash/percent makes tuple identities unambiguous and portable.
            key = "/".join(str(part).replace("%", "%25").replace("/", "%2F") for part in (source, name, row_id))
            record: dict[str, Any] = {"key": key, "row_id": row_id, "source": source, "dataset": name,
                                      "source_attributes": deepcopy(raw), "measurement_provenance": {}}
            fingerprint = json.dumps({"source_key": key, "record": raw}, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            record["source_record_sha256"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            for canonical, field in mapping["fields"].items():
                if field["source"] not in raw:
                    raise SourceError(f"Mapped field absent from row {key}: {field['source']}")
                original = raw[field["source"]]
                value = original
                missing = original is None or any(type(original) is type(sentinel) and original == sentinel for sentinel in field.get("missing", []))
                if not missing and field["type"] == "number" and not isinstance(original, bool):
                    try:
                        number = float(original)
                        missing = any(not isinstance(sentinel, bool) and isinstance(sentinel, (int, float)) and number == sentinel for sentinel in field.get("missing", []))
                    except (TypeError, ValueError):
                        pass
                if not missing and field.get("zero_is_missing"):
                    try:
                        missing = not isinstance(original, bool) and float(original) == 0.0
                    except (TypeError, ValueError):
                        pass
                if missing:
                    value = None
                elif field["type"] == "identifier":
                    value = _identifier(original, trim=field.get("trim", False), context=f"{key}.{canonical}")
                elif field["type"] == "string":
                    value = str(original).strip() if field.get("trim", False) else str(original)
                else:
                    try:
                        value = to_metres(original, field["unit"]) if field["unit"] in LENGTH_FACTORS else to_metres(original, "m")
                    except ValueError:
                        value = None
                        result["findings"].append({"code": "invalid_numeric_measurement", "severity": "warning", "key": key, "field": canonical, "raw": original})
                provenance = {"source_field": field["source"], "raw_value": deepcopy(original), "source_unit": field.get("unit"), "canonical_unit": "m" if field.get("unit") in LENGTH_FACTORS else field.get("unit"), "source_key": key}
                record["measurement_provenance"][canonical] = provenance
                if value is not None and canonical in mapping.get("domains", {}):
                    domain = mapping["domains"][canonical]
                    domain_values = domain.get("values", {}) if domain["method"] == "configured_map" else table.get("domains", {}).get(field["source"])
                    if not isinstance(domain_values, dict):
                        raise SourceError(f"Missing inspected source domain for {key}.{canonical}")
                    decoded = domain_values.get(str(original))
                    if decoded is None:
                        policy = domain.get("unmapped", "unknown")
                        if policy == "error":
                            raise SourceError(f"Unmapped domain code for {key}.{canonical}: {original!r}")
                        result["findings"].append({"code": "unmapped_domain", "severity": "warning", "key": key, "field": canonical, "raw": original})
                        value = value if policy == "retain_raw_with_flag" else "unknown"
                    else:
                        value = decoded
                    provenance["decoded_value"] = value
                record[canonical] = value
            if "vertical_datum" not in record and "vertical_datum" in mapping:
                record["vertical_datum"] = mapping["vertical_datum"]
            entity = mapping["entity"]
            identity = record.get("asset_id", record.get("port_id", row_id))
            if identity is None:
                raise SourceError(f"Missing required asset identity: {key}")
            if identity in seen_assets[entity]:
                raise SourceError(f"Duplicate canonical {entity} identity: {identity}")
            seen_assets[entity].add(identity)
            if "geometry" in mapping:
                geomap = mapping["geometry"]
                rawgeo = raw.get(geomap["field"])
                record["raw_geometry"] = deepcopy(rawgeo)
                record["coordinates"], flags = _geometry(rawgeo, geomap, metadata, entity)
                record["geometry_metadata"] = {"source_crs": deepcopy(geomap.get("declared_crs")), "source_linear_unit": geomap["linear_unit"], "canonical_linear_unit": "m", "ordinate_policy": "XY_only_Z_M_retained_raw", "crs_validation": "PROJ_projected_2D_axes_and_units"}
                for flag in flags:
                    result["findings"].append({"code": flag, "severity": "warning", "key": key})
            result[{"pipe": "pipes", "node": "nodes", "port": "ports"}[entity]].append(record)
        manifest[source].setdefault("datasets", {})[name] = {"table": mapping.get("table"), "input_rows": len(table["records"]), "retained_rows": len(seen_rows), "metadata": deepcopy(metadata)}
    return result
