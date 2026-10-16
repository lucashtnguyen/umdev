"""Declaration-relative configuration overlays and supported-contract validation.

Author: Lucas HT Nguyen
Last modified by: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-16
Manually maintained; configuration cannot execute code or arbitrary SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .units import LENGTH_FACTORS


class ConfigurationError(ValueError):
    """Raised when a configuration cannot be resolved or safely interpreted."""


@dataclass(frozen=True)
class ResolvedConfig:
    """Resolved JSON values, declaration origins and deterministic content hash."""

    data: dict[str, Any]
    origins: dict[str, str]
    files: tuple[str, ...]
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Return an independent copy suitable for a retained run artifact."""
        return deepcopy(self.data)


TOP_KEYS = set("schema_version kind metadata extends sources datasets units model identity ports elevations parameters roughness scenarios selection qa review publication extensions".split())
FIELDS = set("asset_id from_node to_node model_id diameter length upstream_invert downstream_invert material status pipe_kind floor rim vertical_datum outlet_description inlet_description port_id structure_id pipe_id role invert direction review_priority existing_selection future_selection decision description roughness".split())
PATH_KEYS = {"path", "baseline", "crosswalk_path", "overrides_path", "ledger_path", "output_directory", "executable", "version_library", "changes_path"}
FORMAT_IDS = {"json_packet", "json", "csv", "geojson", "geopackage", "file_geodatabase"}
INLINE_PARSERS = {"elevation_direction_diameter_v1", "qualified_elevation_direction_diameter_v1"}
PARSERS = {"normalized_port_row_v1"} | INLINE_PARSERS


def _keys(value: object, allowed: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{context} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigurationError(f"Unknown keys in {context}: {sorted(unknown)}")
    return value


def _finite(value: object, context: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigurationError(f"{context} must be a finite number")
    if value < 0 or (positive and value <= 0):
        raise ConfigurationError(f"{context} must be {'positive' if positive else 'nonnegative'}")


def _quantity(value: object, context: str) -> None:
    data = _keys(value, {"value", "unit"}, context)
    _finite(data.get("value"), context + ".value")
    if data.get("unit") not in LENGTH_FACTORS:
        raise ConfigurationError(f"Unsupported unit in {context}: {data.get('unit')!r}")


def _predicates(predicates: object, context: str) -> None:
    if not isinstance(predicates, list):
        raise ConfigurationError(f"{context} must be a list")
    for rule in predicates:
        rule = _keys(rule, {"field", "operator", "value", "values", "unit"}, context)
        if rule.get("operator") not in {"equals", "in", "not_in", "lt", "lte", "gt", "gte"}:
            raise ConfigurationError(f"Unsupported predicate operator in {context}")
        if not isinstance(rule.get("field"), str):
            raise ConfigurationError(f"Predicate field is required in {context}")
        if rule["operator"] in {"in", "not_in"} and not isinstance(rule.get("values", rule.get("value")), list):
            raise ConfigurationError(f"Membership predicate requires list values in {context}")


def _validate_config(config: Mapping[str, Any]) -> None:
    """Validate schema/version, mappings, references and supported policy subset.

    Notes
    -----
    Source table fields and CRS are checked by ingestion. Optional policy groups
    are allowed for mapping-only usage; preparation validates required policies.
    Unknown keys are rejected, including nested public contract groups.
    """
    data = _keys(dict(config), TOP_KEYS, "configuration")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ConfigurationError("Only schema_version 1 is supported")
    if data.get("kind") != "umdev_project":
        raise ConfigurationError("kind must be umdev_project")
    _keys(data.get("metadata", {}), set("name author contact date status description".split()), "metadata")
    sources = data.get("sources", {})
    datasets = data.get("datasets", {})
    if not isinstance(sources, dict) or not isinstance(datasets, dict):
        raise ConfigurationError("sources and datasets must be objects")
    for name, source in sources.items():
        source = _keys(source, {"format", "path", "read_only", "encoding", "delimiter", "extensions"}, f"sources.{name}")
        if source.get("format") not in FORMAT_IDS:
            raise ConfigurationError(f"Unsupported source format: {source.get('format')!r}")
        if source.get("read_only", True) is not True:
            raise ConfigurationError("Sources must be read_only=true")
        if not isinstance(source.get("path"), str) or not source["path"]:
            raise ConfigurationError(f"sources.{name}.path is required")
    for name, dataset in datasets.items():
        dataset = _keys(dataset, set("source table entity id_field filters fields domains geometry vertical_datum extensions".split()), f"datasets.{name}")
        if dataset.get("source") not in sources:
            raise ConfigurationError(f"Unknown source for dataset {name}")
        if dataset.get("entity") not in {"pipe", "node", "port"}:
            raise ConfigurationError(f"Unsupported entity for dataset {name}")
        if not isinstance(dataset.get("id_field"), str) or not dataset["id_field"]:
            raise ConfigurationError(f"Dataset {name} requires id_field")
        fields = dataset.get("fields", {})
        # Described ports are not limited to one inlet/outlet field. Additional
        # canonical text fields are admitted only through an explicit registered
        # observation reference on this dataset, keeping unrelated typos visible.
        observations = data.get("ports", {}).get("observations", [])
        observation_fields = {item.get("field") for item in observations
                              if isinstance(item, dict) and item.get("dataset") == name
                              and item.get("parser") in INLINE_PARSERS
                              and isinstance(item.get("field"), str)}
        if any(not field.isascii() or not field.isidentifier() for field in observation_fields):
            raise ConfigurationError(f"Observation canonical fields must be identifier strings in {name}")
        allowed_fields = FIELDS | observation_fields
        if not isinstance(fields, dict) or set(fields) - allowed_fields:
            raise ConfigurationError(f"Unknown canonical fields in {name}: {sorted(set(fields) - allowed_fields)}")
        for field in observation_fields - FIELDS:
            if field in fields and (not isinstance(fields[field], dict) or fields[field].get("type") != "string"):
                raise ConfigurationError(f"Additional inline observation field must map text: {name}.{field}")
        required = {"pipe": {"asset_id", "from_node", "to_node"}, "node": {"asset_id"}, "port": {"structure_id", "role", "invert"}}[dataset["entity"]]
        if not required <= set(fields):
            raise ConfigurationError(f"Dataset {name} missing canonical identity mappings: {sorted(required - set(fields))}")
        for canonical, field in fields.items():
            field = _keys(field, {"source", "type", "trim", "missing", "zero_is_missing", "unit", "extensions"}, f"datasets.{name}.fields.{canonical}")
            if not isinstance(field.get("source"), str) or field.get("type") not in {"identifier", "number", "string"}:
                raise ConfigurationError(f"Invalid mapping for {name}.{canonical}")
            if field["type"] == "number" and field.get("unit") not in set(LENGTH_FACTORS) | {"dimensionless", "degrees"}:
                raise ConfigurationError(f"Explicit supported unit required for {name}.{canonical}")
            if not isinstance(field.get("missing", []), list):
                raise ConfigurationError(f"Missing sentinels must be a list for {name}.{canonical}")
        for canonical, domain in dataset.get("domains", {}).items():
            if canonical not in fields:
                raise ConfigurationError(f"Domain refers to unmapped field {name}.{canonical}")
            domain = _keys(domain, {"method", "values", "unmapped"}, f"domains.{canonical}")
            if domain.get("method") not in {"configured_map", "source_domain"}:
                raise ConfigurationError(f"Unsupported domain method for {canonical}")
            if domain.get("unmapped", "unknown") not in {"unknown", "retain_raw_with_flag", "error"}:
                raise ConfigurationError(f"Unsupported unmapped-domain policy for {canonical}")
        if "geometry" in dataset:
            geo = _keys(dataset["geometry"], {"field", "declared_crs", "linear_unit", "m_ordinate", "z_ordinate", "multipart", "curves"}, f"geometry.{name}")
            if geo.get("linear_unit") not in LENGTH_FACTORS or not isinstance(geo.get("field"), str):
                raise ConfigurationError(f"Geometry field and explicit length unit required for {name}")
            if geo.get("multipart", "hold") != "hold" or geo.get("curves", "hold") != "hold":
                raise ConfigurationError("Only hold is implemented for multipart/curved geometry")
        _predicates(dataset.get("filters", []), f"datasets.{name}.filters")
    if "units" in data:
        unit = _keys(data["units"], {"canonical_engineering"}, "units")
        if unit.get("canonical_engineering") != "SI":
            raise ConfigurationError("Canonical engineering units must be SI")
    _validate_policies(data)
    # A candidate cannot replace a declared source file or baseline.
    out = data.get("publication", {}).get("output_directory")
    if out:
        output = Path(out).resolve()
        inputs = [Path(source["path"]).resolve() for source in sources.values()]
        baseline = data.get("model", {}).get("baseline")
        if baseline:
            inputs.append(Path(baseline).resolve())
        if output in inputs:
            raise ConfigurationError("Output directory collides with an input path")


def _validate_policies(data: dict[str, Any]) -> None:
    groups = {
        "model": set("adapter baseline encoding baseline_metadata target unsupported_operations changes_path extensions".split()),
        "identity": set("source_key_components connectivity model_association extensions".split()),
        "ports": {"observations", "assignment", "extensions"},
        "elevations": set("target_datum transforms allow_provisional unknown_or_mixed_datum extensions".split()),
        "parameters": {"nodes", "pipes", "extensions"},
        "roughness": set("policy_id status parameter rules unknown_material pressure_parameters extensions".split()),
        "selection": set("scenario scope attribution_context asset_filters explicit_pipe_keys pipe_exclusions single_pipe_branches extensions".split()),
        "qa": set("profile allow_categories rules readback_absolute_tolerance engine extensions".split()),
        "review": {"ledger_path", "overrides_path", "overrides", "changed_evidence", "extensions"},
        "publication": set("mode output_directory overwrite_sources formats retain_effective_config extensions".split()),
    }
    for group, keys in groups.items():
        if group in data:
            _keys(data[group], keys, group)
    model = data.get("model", {})
    if model and model.get("adapter") != "swmm":
        raise ConfigurationError("Only the swmm model adapter is implemented")
    for name in ("baseline_metadata", "target"):
        if name in model:
            allowed = {"horizontal_crs", "map_unit", "vertical_datum"} if name == "baseline_metadata" else {"flow_units", "length_unit", "link_offsets", "map_crs", "map_unit", "vertical_datum"}
            _keys(model[name], allowed, f"model.{name}")
    target = model.get("target", {})
    if target:
        if target.get("link_offsets") not in {"DEPTH", "ELEVATION"}:
            raise ConfigurationError("SWMM offsets must be DEPTH or ELEVATION")
        flow = target.get("flow_units")
        expected = "ft" if flow in {"CFS", "GPM", "MGD"} else "m" if flow in {"CMS", "LPS", "MLD"} else None
        if expected is None or target.get("length_unit") != expected:
            raise ConfigurationError("SWMM flow and length units are incompatible")
    identity = data.get("identity", {})
    if identity.get("source_key_components", ["source", "dataset", "row_id"]) != ["source", "dataset", "row_id"]:
        raise ConfigurationError("Only source/dataset/row_id source keys are implemented")
    if "connectivity" in identity:
        conn = _keys(identity["connectivity"], {"method", "pipe_dataset", "node_dataset"}, "identity.connectivity")
        if conn.get("method") != "endpoint_fields":
            raise ConfigurationError("Only endpoint_fields connectivity is implemented")
        for key, entity in (("pipe_dataset", "pipe"), ("node_dataset", "node")):
            if conn.get(key) not in data.get("datasets", {}) or data["datasets"][conn[key]]["entity"] != entity:
                raise ConfigurationError(f"Invalid {key} reference")
    if "model_association" in identity:
        assoc = _keys(identity["model_association"], {"methods", "crosswalk_path", "spatial_candidates"}, "identity.model_association")
        if set(assoc.get("methods", [])) - {"mapped_model_id", "explicit_crosswalk", "asset_id"}:
            raise ConfigurationError("Unsupported model-association method")
        if "spatial_candidates" in assoc:
            spatial = _keys(assoc["spatial_candidates"], {"enabled", "tolerance"}, "spatial_candidates")
            if spatial.get("enabled"):
                raise ConfigurationError("Spatial model association is not implemented")
            if "tolerance" in spatial:
                _quantity(spatial["tolerance"], "spatial_candidates.tolerance")
    observations = data.get("ports", {}).get("observations", [])
    seen: set[str] = set()
    if not isinstance(observations, list):
        raise ConfigurationError("Port observations must be a list")
    for obs in observations:
        obs = _keys(obs, {"id", "dataset", "parser", "field", "role", "units"}, "ports.observations")
        if not isinstance(obs.get("id"), str) or obs["id"] in seen:
            raise ConfigurationError("Port observation IDs must be unique strings")
        seen.add(obs["id"])
        if obs.get("dataset") not in data.get("datasets", {}) or obs.get("parser") not in PARSERS:
            raise ConfigurationError("Unknown port parser or dataset reference")
        expected_entity = "node" if obs["parser"] in INLINE_PARSERS else "port"
        if data["datasets"][obs["dataset"]]["entity"] != expected_entity:
            raise ConfigurationError("Port parser is incompatible with referenced dataset entity")
        if obs["parser"] in INLINE_PARSERS:
            if obs.get("field") not in data["datasets"][obs["dataset"]]["fields"]:
                raise ConfigurationError("Inline port field is not mapped")
            _keys(obs.get("units", {}), {"elevation", "diameter"}, "port units")
            if any(obs.get("units", {}).get(key) not in LENGTH_FACTORS for key in ("elevation", "diameter")):
                raise ConfigurationError("Inline port elevation and diameter units are required")
            if obs.get("role") not in {"in", "out", "inlet", "outlet"}:
                raise ConfigurationError("Inline port role must be in/out")
    assignment = data.get("ports", {}).get("assignment", {})
    _keys(assignment, set("precedence direction unique_role_diameter_fallback competing_candidates numeric_text_conflict endpoint_tolerance diameter_tolerance direction_half_sector_deg elevation_conflict_tolerance context_exclude_statuses pipe_numeric_datum observation_pipe_references".split()), "ports.assignment")
    if "observation_pipe_references" in assignment:
        references = assignment["observation_pipe_references"]
        if not isinstance(references, dict):
            raise ConfigurationError("observation_pipe_references must be an object")
        for observation_key, reference in references.items():
            if not isinstance(observation_key, str) or not observation_key.strip():
                raise ConfigurationError("Explicit pipe reference keys must identify collected observations")
            reference = _keys(reference, {"pipe_key", "basis"}, "observation_pipe_references")
            if any(not isinstance(reference.get(key), str) or not reference[key].strip() for key in ("pipe_key", "basis")):
                raise ConfigurationError("Explicit observation pipe references require pipe_key and basis")
    for key in ("endpoint_tolerance", "diameter_tolerance", "elevation_conflict_tolerance"):
        if key in assignment:
            _quantity(assignment[key], "ports.assignment." + key)
    if "direction_half_sector_deg" in assignment:
        _finite(assignment["direction_half_sector_deg"], "direction_half_sector_deg")
        if assignment["direction_half_sector_deg"] > 180:
            raise ConfigurationError("Direction half-sector cannot exceed 180 degrees")
    if "unique_role_diameter_fallback" in assignment:
        _keys(assignment["unique_role_diameter_fallback"], {"enabled", "category", "qa_flag"}, "unique_role_diameter_fallback")
    if assignment.get("unique_role_diameter_fallback", {}).get("category", "provisional") != "provisional":
        raise ConfigurationError("Direction fallback category must remain provisional")
    if set(assignment.get("precedence", [])) - {"reviewed_override", "pipe_numeric", "normalized_port", "inline_port"}:
        raise ConfigurationError("Unsupported port evidence family")
    transforms = data.get("elevations", {}).get("transforms", [])
    seen = set()
    for transform in transforms:
        transform = _keys(transform, {"id", "source_datum", "target_datum", "method", "verified", "basis", "offset", "applicability", "precedence", "asset_ids", "source_keys"}, "elevations.transforms")
        if transform.get("id") in seen or not transform.get("id"):
            raise ConfigurationError("Datum transform IDs must be unique")
        seen.add(transform["id"])
        if transform.get("method") not in {"identity", "constant_offset"}:
            raise ConfigurationError("Only identity/constant_offset datum methods are implemented")
        if transform["method"] == "identity" and transform.get("source_datum") != transform.get("target_datum"):
            raise ConfigurationError("Identity transform cannot relabel a vertical datum")
        if transform["method"] == "constant_offset":
            offset = _keys(transform.get("offset", {}), {"value", "unit"}, "datum offset")
            if offset.get("unit") not in LENGTH_FACTORS or isinstance(offset.get("value"), bool) or not isinstance(offset.get("value"), (int, float)) or not math.isfinite(offset["value"]):
                raise ConfigurationError("Datum offset requires finite signed value and length unit")
    for entity in ("nodes", "pipes"):
        if entity in data.get("parameters", {}):
            allowed = {"floor_methods", "rim_methods", "missing_floor_or_rim", "estimators", "update_existing_rims", "pressure_connections", "missing_rim"} if entity == "nodes" else {"length_methods", "diameter_methods", "shape", "missing_required_value"}
            _keys(data["parameters"][entity], allowed, f"parameters.{entity}")
            if data["parameters"][entity].get("estimators"):
                raise ConfigurationError("Named estimators are not implemented")
    if assignment.get("direction", "hint") not in {"hint", "strict"}:
        raise ConfigurationError("Unsupported direction assignment policy")
    if assignment.get("competing_candidates", "hold") != "hold":
        raise ConfigurationError("Competing candidates must remain held")
    if assignment.get("numeric_text_conflict", "prefer_ranked_with_flag") not in {"prefer_ranked_with_flag", "hold"}:
        raise ConfigurationError("Unsupported numeric/text conflict policy")
    if assignment.get("pipe_numeric_datum", "node") not in {"node", "explicit"}:
        raise ConfigurationError("Unsupported direct-pipe datum policy")
    for transform in transforms:
        if transform.get("applicability"):
            raise ConfigurationError("Generic transform applicability predicates are not implemented; use asset_ids/source_keys")
    node_policy = data.get("parameters", {}).get("nodes", {})
    if set(node_policy.get("floor_methods", [])) - {"reviewed_override", "structure_floor", "unique_active_outgoing_port", "lowest_active_port", "minimum_active_port", "lowest_structure_port", "lowest_applied_pipe_end"}:
        raise ConfigurationError("Unsupported node-floor method")
    if set(node_policy.get("rim_methods", [])) - {"reviewed_override", "structure_rim"}:
        raise ConfigurationError("Unsupported node-rim method")
    if "missing_rim" in node_policy:
        missing_rim = _keys(node_policy["missing_rim"], {"mode", "placeholder", "basis"}, "parameters.nodes.missing_rim")
        mode = missing_rim.get("mode", "hold")
        if mode not in {"hold", "error", "placeholder"}:
            raise ConfigurationError("Missing-rim mode must be hold, error or placeholder")
        if mode != "placeholder":
            if set(missing_rim) - {"mode"}:
                raise ConfigurationError("Missing-rim placeholder and basis require placeholder mode")
        else:
            if not isinstance(missing_rim.get("basis"), str) or not missing_rim["basis"].strip():
                raise ConfigurationError("Missing-rim placeholder requires an explicit basis")
            placeholder = _keys(missing_rim.get("placeholder"), {"kind", "value", "unit", "vertical_datum"}, "missing-rim placeholder")
            kind = placeholder.get("kind")
            if kind != "depth":
                raise ConfigurationError("Missing-rim placeholder kind must be depth; SWMM has no native absolute-rim field")
            value = placeholder.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ConfigurationError("Missing-rim placeholder value must be finite")
            unit = placeholder.get("unit")
            if unit != "model" and unit not in LENGTH_FACTORS:
                raise ConfigurationError("Missing-rim placeholder requires a supported length unit or model")
            if unit == "model" and data.get("model", {}).get("target", {}).get("length_unit") not in LENGTH_FACTORS:
                raise ConfigurationError("Model-unit placeholder requires an explicit target length_unit")
            if value <= 0:
                raise ConfigurationError("Missing-rim placeholder depth must be positive")
            if "vertical_datum" in placeholder:
                raise ConfigurationError("Missing-rim placeholder depth cannot declare a vertical datum")
    if "pressure_connections" in node_policy:
        pressure_nodes = _keys(node_policy["pressure_connections"],
            {"classification", "surcharge_depth", "basis", "node_overrides", "model_node_overrides"}, "parameters.nodes.pressure_connections")
        if pressure_nodes.get("classification", "explicit_roles") not in {"explicit_roles", "internal_force_main_only"}:
            raise ConfigurationError("Unsupported pressure connection classification")
        _quantity(pressure_nodes.get("surcharge_depth"), "pressure connection surcharge_depth")
        if not isinstance(pressure_nodes.get("basis"), str) or not pressure_nodes["basis"].strip():
            raise ConfigurationError("Pressure connection policy requires an explicit basis")
        for namespace in ("node_overrides", "model_node_overrides"):
            overrides = pressure_nodes.get(namespace, {})
            if (not isinstance(overrides, dict) or any(not isinstance(key, str) or not key.strip() or
                    role not in {"sealed", "open_discharge", "wet_well"} for key, role in overrides.items())):
                raise ConfigurationError(f"Pressure {namespace} require node IDs and sealed/open_discharge/wet_well roles")
    pipe_policy = data.get("parameters", {}).get("pipes", {})
    if set(pipe_policy.get("length_methods", [])) - {"reviewed_override", "attribute_length", "geometry_length"}:
        raise ConfigurationError("Unsupported pipe-length method")
    if set(pipe_policy.get("diameter_methods", [])) - {"reviewed_override", "attribute_diameter"}:
        raise ConfigurationError("Unsupported pipe-diameter method")
    if pipe_policy.get("shape", "CIRCULAR") != "CIRCULAR":
        raise ConfigurationError("Only CIRCULAR gravity shape is implemented")
    aliases: dict[str, str] = {}
    rough = data.get("roughness", {})
    if rough and rough.get("parameter") != "manning_n":
        raise ConfigurationError("Only gravity Manning n material policy is implemented")
    for name, rule in rough.get("rules", {}).items():
        _keys(rule, {"aliases", "value", "reference_range", "source", "source_url", "assumptions"}, f"roughness.rules.{name}")
        _finite(rule.get("value"), f"roughness.{name}.value", positive=True)
        limits = rule.get("reference_range")
        if limits is not None and (not isinstance(limits, list) or len(limits) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0 for x in limits) or not limits[0] <= rule["value"] <= limits[1]):
            raise ConfigurationError(f"Roughness {name} is outside its finite reference range")
        for alias in [name, *rule.get("aliases", [])]:
            normalized = str(alias).strip().casefold()
            if normalized in aliases and aliases[normalized] != name:
                raise ConfigurationError(f"Ambiguous material alias: {alias}")
            aliases[normalized] = name
    if "unknown_material" in rough:
        unknown = _keys(rough["unknown_material"], {"policy", "value", "category", "basis", "qa_flag"}, "roughness.unknown_material")
        if unknown.get("policy") not in {"hold", "explicit_assumption"}:
            raise ConfigurationError("Unsupported unknown-material policy")
        if unknown["policy"] == "explicit_assumption":
            _finite(unknown.get("value"), "unknown-material roughness", positive=True)
            if not unknown.get("basis"):
                raise ConfigurationError("Assumed roughness requires a basis")
    if "pressure_parameters" in rough:
        pressure = _keys(rough["pressure_parameters"],
            {"policy", "equation", "coefficient", "conduit_manning_n", "basis"}, "roughness.pressure_parameters")
        if pressure.get("policy") not in {"hold", "require_separate_contract", "explicit_assumption"}:
            raise ConfigurationError("Unsupported pressure parameter policy")
        if pressure.get("policy") == "explicit_assumption":
            if pressure.get("equation") == "H-W":
                _finite(pressure.get("coefficient"), "H-W coefficient", positive=True)
            elif pressure.get("equation") == "D-W":
                _quantity(pressure.get("coefficient"), "D-W absolute roughness")
                _finite(pressure["coefficient"]["value"], "D-W absolute roughness", positive=True)
            else:
                raise ConfigurationError("Pressure equation must be H-W or D-W")
            _finite(pressure.get("conduit_manning_n"), "Pressure partial-flow Manning n", positive=True)
            if not isinstance(pressure.get("basis"), str) or not pressure["basis"].strip():
                raise ConfigurationError("Pressure parameters require an explicit basis")
    for name, scenario in data.get("scenarios", {}).items():
        _keys(scenario, {"include_statuses", "exclude_statuses", "unknown_status"}, f"scenarios.{name}")
        if set(scenario.get("include_statuses", [])) & set(scenario.get("exclude_statuses", [])):
            raise ConfigurationError(f"Overlapping include/exclude statuses in {name}")
    selection = data.get("selection", {})
    if "explicit_pipe_keys" in selection and (not isinstance(selection["explicit_pipe_keys"], list) or not all(isinstance(key, str) for key in selection["explicit_pipe_keys"])):
        raise ConfigurationError("Explicit pipe keys must be a list of source-qualified strings")
    exclusions = selection.get("pipe_exclusions", [])
    if not isinstance(exclusions, list):
        raise ConfigurationError("selection.pipe_exclusions must be a list")
    seen_exclusions: set[str] = set()
    for exclusion in exclusions:
        exclusion = _keys(exclusion, {"source_key", "reason", "source_sha256", "decision_id"}, "selection.pipe_exclusions")
        key = exclusion.get("source_key")
        if not isinstance(key, str) or not key.strip() or key in seen_exclusions:
            raise ConfigurationError("Pipe exclusions require unique nonempty source keys")
        seen_exclusions.add(key)
        if not isinstance(exclusion.get("reason"), str) or not exclusion["reason"].strip():
            raise ConfigurationError("Pipe exclusions require a nonempty reason")
        fingerprint = exclusion.get("source_sha256")
        if "source_sha256" in exclusion and (not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint)):
            raise ConfigurationError("Pipe exclusion source_sha256 must be lowercase SHA-256")
        if "decision_id" in exclusion and (not isinstance(exclusion["decision_id"], str) or not exclusion["decision_id"].strip()):
            raise ConfigurationError("Pipe exclusion decision_id must be a nonempty string")
    if "single_pipe_branches" in selection:
        branch = _keys(selection["single_pipe_branches"], {"mode", "reason", "preserve_pipe_keys", "decision_id"}, "selection.single_pipe_branches")
        if branch.get("mode", "review") not in {"review", "exclude"}:
            raise ConfigurationError("Single-pipe branch mode must be review or exclude")
        if branch.get("mode", "review") == "exclude" and (not isinstance(branch.get("reason"), str) or not branch["reason"].strip()):
            raise ConfigurationError("Single-pipe branch exclusions require a nonempty reason")
        if "reason" in branch and (not isinstance(branch["reason"], str) or not branch["reason"].strip()):
            raise ConfigurationError("Single-pipe branch reason must be a nonempty string")
        if "decision_id" in branch and (not isinstance(branch["decision_id"], str) or not branch["decision_id"].strip()):
            raise ConfigurationError("Single-pipe branch decision_id must be a nonempty string")
        preserved = branch.get("preserve_pipe_keys", [])
        if not isinstance(preserved, list) or not all(isinstance(key, str) and key.strip() for key in preserved) or len(set(preserved)) != len(preserved):
            raise ConfigurationError("Preserved branch keys must be unique nonempty source keys")
    if "scenario" in selection and selection["scenario"] not in data.get("scenarios", {}):
        raise ConfigurationError("Selection refers to an unknown scenario")
    _predicates(selection.get("asset_filters", []), "selection.asset_filters")
    if "scope" in selection:
        scope = _keys(selection["scope"], {"method", "buffer", "boundary_crossers", "asset_ids", "bounds"}, "selection.scope")
        if scope.get("method") not in {"all", "baseline_map_extent", "asset_ids", "bounds"}:
            raise ConfigurationError("Unsupported selection scope method")
        if scope.get("method") == "bounds":
            bounds = scope.get("bounds")
            if not isinstance(bounds, list) or len(bounds) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in bounds) or bounds[0] > bounds[2] or bounds[1] > bounds[3]:
                raise ConfigurationError("Scope bounds must be ordered finite [xmin,ymin,xmax,ymax] in canonical metres")
        if "buffer" in scope:
            _quantity(scope["buffer"], "selection.scope.buffer")
    qa = data.get("qa", {})
    if "readback_absolute_tolerance" in qa:
        _quantity(qa["readback_absolute_tolerance"], "qa.readback_absolute_tolerance")
    for name, rule in qa.get("rules", {}).items():
        _keys(rule, {"severity", "disposition"}, f"qa.rules.{name}")
        if rule.get("severity") not in {"info", "warning", "error"}:
            raise ConfigurationError(f"Unsupported QA severity for {name}")
        if rule.get("disposition", "review") not in {"review", "hold"}:
            raise ConfigurationError(f"Unsupported QA disposition for {name}")
    if "engine" in qa:
        _keys(qa["engine"], {"enabled", "executable", "version_library", "expected_version", "external_resources"}, "qa.engine")
    review = data.get("review", {})
    ledger_path = review.get("ledger_path")
    if ledger_path is not None and not isinstance(ledger_path, str):
        raise ConfigurationError("review.ledger_path must be a path string or null")
    if isinstance(ledger_path, str) and ledger_path and not ledger_path.strip():
        raise ConfigurationError("review.ledger_path cannot be whitespace")
    if ledger_path and (review.get("overrides_path") or review.get("overrides") or model.get("changes_path")):
        raise ConfigurationError("review.ledger_path is mutually exclusive with nonempty legacy overrides_path, overrides and model.changes_path")
    if review.get("changed_evidence", "mark_stale_and_re_evaluate") not in {"mark_stale_and_re_evaluate", "hold"}:
        raise ConfigurationError("Unsupported changed-evidence policy")
    if "overrides" in review and not isinstance(review["overrides"], list):
        raise ConfigurationError("Runtime reviewed overrides must be a list")
    publication = data.get("publication", {})
    if publication.get("overwrite_sources", False):
        raise ConfigurationError("Source overwrite is prohibited")
    if publication and publication.get("mode") not in {"supported_subnetwork", "complete_request", "geometry_review"}:
        raise ConfigurationError("Unsupported publication completeness mode")


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate known contract keys, references, units and supported policy values.

    Parameters
    ----------
    config : mapping
        Schema-version-1 project values. Full source-field validation occurs while
        reading datasets; native driver availability is reported by ingestion.

    Raises
    ------
    ConfigurationError
        If configuration structure, semantics or requested capabilities are invalid.
    """
    try:
        _validate_config(config)
    except ConfigurationError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Malformed configuration: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(left)
    for key, value in right.items():
        result[key] = _merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else deepcopy(value)
    return result


def load_config(path: str | Path) -> ResolvedConfig:
    """Resolve ordered JSON overlays and validate the effective configuration.

    Parameters
    ----------
    path : str or pathlib.Path
        Project configuration file. Relative resource paths retain the directory
        of the file declaring them, including inherited fragments.

    Returns
    -------
    ResolvedConfig
        Independent values, leaf declaration origins, files and SHA-256 digest.

    Raises
    ------
    ConfigurationError
        For cycles, invalid JSON, unsupported contracts or missing references.
    """
    files: list[str] = []

    def resolve(file: Path, stack: tuple[Path, ...]) -> tuple[dict[str, Any], dict[str, str]]:
        file = file.resolve()
        if file in stack:
            raise ConfigurationError("Configuration extends cycle: " + " -> ".join(map(str, (*stack, file))))
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"Cannot read configuration {file}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigurationError(f"Configuration must be an object: {file}")
        parents = raw.get("extends", [])
        if not isinstance(parents, list) or not all(isinstance(item, str) for item in parents):
            raise ConfigurationError("extends must be a list of paths")
        merged: dict[str, Any] = {}
        origins: dict[str, str] = {}
        for parent in parents:
            inherited, inherited_origins = resolve(file.parent / parent, (*stack, file))
            merged = _merge(merged, inherited)
            origins.update(inherited_origins)
        raw.pop("extends", None)

        def declared(value: Any, trail: tuple[str, ...]) -> Any:
            if isinstance(value, dict):
                return {key: declared(item, (*trail, key)) for key, item in value.items()}
            origins["/" + "/".join(trail)] = str(file)
            if trail and trail[-1] in PATH_KEYS and isinstance(value, str) and value.strip():
                return str((file.parent / value).resolve())
            return value

        merged = _merge(merged, declared(raw, ()))
        if str(file) not in files:
            files.append(str(file))
        # Discard leaf origins shadowed by atomic list/null replacement.
        def leaves(value: Any, trail: tuple[str, ...] = ()) -> set[str]:
            if isinstance(value, dict):
                return set().union(*(leaves(item, (*trail, key)) for key, item in value.items())) if value else set()
            return {"/" + "/".join(trail)}
        active = leaves(merged)
        return merged, {key: val for key, val in origins.items() if key in active}

    config, origins = resolve(Path(path), ())
    validate_config(config)
    serialized = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return ResolvedConfig(config, origins, tuple(files), hashlib.sha256(serialized).hexdigest())
