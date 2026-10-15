"""Run a configuration-driven utility preparation and audited model update.

Author: Lucas HT Nguyen
Last modified by: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained callable workflow. Project inputs and outputs are caller-owned.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

from hymo import read_document

from .configuration import load_config
from .models import bind_model_identities, resolve_model_identity, validate_crosswalk, baseline_node_parameters, baseline_pipe_end_parameters, candidate_engineering_qa, placeholder_node_register, compile_handoff, instructions_from_handoff, verify_prepared_profiles
from .parameters import DEFAULT_FLOOR_METHODS, prepare_network_from_dict
from .exclusions import apply_scope_exclusions
from .reporting import json_value, stable_hash, write_csv, write_json, write_review_html
from .qa import connectivity_findings
from .sources import ingest_sources, matches_predicates
from .topology import diagnose_swmm_topology
from .units import to_metres



def _segment_intersects_bounds(start: Any, end: Any, bounds: tuple[float, ...]) -> bool:
    """Test segment/rectangle intersection using Liang-Barsky clipping parameters."""
    xmin, ymin, xmax, ymax = bounds
    dx, dy = end[0] - start[0], end[1] - start[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, start[0] - xmin), (dx, xmax - start[0]),
                 (-dy, start[1] - ymin), (dy, ymax - start[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            low = max(low, ratio)
        else:
            high = min(high, ratio)
        if low > high:
            return False
    return True


def _check_source_map_basis(canonical: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    """Reject unconfigured reprojection before scope or bearing calculations."""
    target = config["model"]["target"]["map_crs"]
    for collection in ("nodes", "pipes"):
        for row in canonical[collection]:
            metadata = row.get("geometry_metadata")
            if metadata and metadata.get("source_crs") != target:
                raise NotImplementedError("Source and target horizontal CRS differ; an explicit reprojection adapter is required")


def select_pipe_keys(canonical: Mapping[str, Any], config: Mapping[str, Any],
                     baseline: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Select scenario/region membership without reducing attribution context.

    Notes
    -----
    Selection uses source status and explicit asset predicates. Scope buffers are
    canonical metres; boundary crossers are held without clipping. All source
    pipes remain available to port attribution before preparation of this subset.
    """
    selection = config["selection"]
    scenario = config["scenarios"][selection["scenario"]]
    scope = selection.get("scope", {"method": "all"})
    method = scope.get("method", "all")
    if scope.get("boundary_crossers", "hold") != "hold":
        raise NotImplementedError("Clipping requires an explicit supported topology operation")
    bounds = None
    buffer = scope.get("buffer", {"value": 0, "unit": "m"})
    margin = to_metres(buffer["value"], buffer["unit"])
    if method == "baseline_map_extent":
        map_unit = config["model"]["target"]["map_unit"]
        points = [tuple(to_metres(float(v), map_unit) for v in row.tokens[1:3])
                  for section in ("COORDINATES", "VERTICES") for row in baseline.records(section)]
        if not points:
            raise ValueError("Baseline has no coordinate/vertex extent")
        bounds = (min(p[0] for p in points), min(p[1] for p in points),
                  max(p[0] for p in points), max(p[1] for p in points))
    elif method == "bounds":
        bounds = tuple(scope["bounds"])
        if len(bounds) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in bounds):
            raise ValueError("Scope bounds must be four finite canonical-metre values")
    elif method not in {"all", "asset_ids"}:
        raise NotImplementedError("Unsupported geographic scope")
    if bounds is not None:
        if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            raise ValueError("Reversed scope bounds")
        bounds = (bounds[0] - margin, bounds[1] - margin, bounds[2] + margin, bounds[3] + margin)
    explicit = selection.get("explicit_pipe_keys")
    if explicit is not None and set(explicit) - {p["key"] for p in canonical["pipes"]}:
        raise ValueError("An explicitly requested source pipe key is absent")
    selected, membership = [], []
    for pipe in canonical["pipes"]:
        reason = None
        status = pipe.get("status", "unknown")
        if explicit is not None and pipe["key"] not in explicit:
            reason = "outside_requested_source_keys"
        elif not matches_predicates(pipe, selection.get("asset_filters", [])):
            reason = "outside_requested_asset_selection"
        elif status in scenario.get("exclude_statuses", []):
            reason = "excluded_by_scenario"
        elif method == "asset_ids" and pipe["asset_id"] not in scope.get("asset_ids", []):
            reason = "outside_asset_scope"
        elif bounds is not None:
            points = pipe.get("coordinates") or []
            if len(points) < 2:
                reason = "unusable_geometry_for_region"
            elif not all(bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3] for p in points):
                intersects = any(_segment_intersects_bounds(a, b, bounds) for a, b in zip(points, points[1:]))
                reason = "boundary_crossing_requires_topology_review" if intersects else "outside_region"
        # A provably out-of-scope asset must not block a complete regional request.
        # Unknown lifecycle remains a hold only after explicit/geographic exclusions.
        if reason is None and status not in scenario.get("include_statuses", []):
            if scenario.get("unknown_status", "hold") != "hold":
                raise NotImplementedError("Unknown-status inclusion requires an explicit lifecycle assignment")
            reason = "unknown_scenario_status"
        row = {"source_key": pipe["key"], "asset_id": pipe["asset_id"],
               "disposition": "selected_for_preparation" if reason is None else ("hold" if reason in {"unknown_scenario_status", "unusable_geometry_for_region", "boundary_crossing_requires_topology_review"} else "not_selected"),
               "reasons": [] if reason is None else [reason]}
        membership.append(row)
        if reason is None:
            selected.append(pipe["key"])
    return sorted(selected), membership


def _crosswalk_canonical(canonical: dict[str, Any], config: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Freeze an explicit crosswalk once; selected identities are bound later."""
    path = config.get("identity", {}).get("model_association", {}).get("crosswalk_path")
    if not path:
        return {}, None
    data = Path(path).read_bytes()
    mapping = validate_crosswalk(json.loads(data))
    return {str(path): sha256(data).hexdigest()}, mapping



def _topology_source_connectors(canonical: Mapping[str, Any], membership: list[dict[str, Any]],
                                 handoff: Mapping[str, Any], config: Mapping[str, Any],
                                 crosswalk_snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Resolve diagnostic connector identities using the frozen application policy.

    Notes
    -----
    Selected rows use the explicit applied handoff when available. Other source
    nodes/links use exactly the configured identity methods and frozen crosswalk;
    asset-ID fallback is never enabled merely for topology diagnostics. Missing
    endpoint identities stay unresolved with source references and reasons.
    This function does not mutate canonical rows, membership or edit instructions.
    """
    decisions = {row["source_key"]: row for row in membership}
    source_nodes = {row["asset_id"]: row for row in canonical["nodes"]}
    crosswalk = crosswalk_snapshot or {"nodes": {}, "pipes": {}}
    frozen_ids = handoff.get("model_ids", {})

    def resolve(row: Mapping[str, Any], entity: str, frozen_key: str) -> tuple[str | None, str | None, str | None]:
        frozen = frozen_ids.get(entity, {}).get(frozen_key)
        if frozen is not None:
            return frozen, "frozen_applied_handoff", None
        try:
            identity, method = resolve_model_identity(row, entity, config, crosswalk)
            return identity, method, None
        except ValueError as error:
            return None, None, str(error)

    result = []
    for pipe in canonical["pipes"]:
        decision = decisions[pipe["key"]]
        pipe_id, pipe_method, pipe_error = resolve(pipe, "pipes", pipe["key"])
        issues = {}
        if pipe_error is not None:
            issues["model_id"] = pipe_error
        endpoint_ids = {}
        provenance = {}
        for field in ("from_node", "to_node"):
            source_id = pipe.get(field)
            if not isinstance(source_id, str) or not source_id.strip():
                endpoint_ids[field] = None
                issues[field] = "Missing or invalid source endpoint identifier"
                provenance[field] = {"source_asset_id": source_id, "resolved_model_id": None, "method": None}
                continue
            # A directed source endpoint field is itself an explicit identity
            # observation. A missing structure row supplies no mapped model ID,
            # but an enabled crosswalk/asset-ID method can still resolve that ID.
            node = source_nodes.get(source_id, {"key": f"{pipe['key']}/{field}", "asset_id": source_id})
            identity, method, error = resolve(node, "nodes", source_id)
            endpoint_ids[field] = identity
            provenance[field] = {"source_asset_id": source_id, "resolved_model_id": identity, "method": method}
            if error is not None:
                issues[field] = error
        result.append({"key": pipe["key"], "asset_id": pipe["asset_id"],
            "model_id": pipe_id, "model_identity_resolved": pipe_id is not None,
            **endpoint_ids, "source_from_node": pipe.get("from_node"), "source_to_node": pipe.get("to_node"),
            "node_model_association": provenance, "pipe_model_association": {"method": pipe_method},
            "identity_issues": issues, "status": pipe.get("status"), "family": pipe.get("pipe_kind"),
            "disposition": decision["disposition"], "reasons": deepcopy(decision["reasons"])})
    return result


def _assert_prepared_inputs_current(project: Mapping[str, Any]) -> None:
    """Check frozen source, configuration, baseline and decision fingerprints."""
    if load_config(project["config_path"]).sha256 != project["resolved_config"].sha256:
        raise ValueError("Configuration changed during application")
    for source in project["canonical"]["manifest"].values():
        if sha256(Path(source["path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Source snapshot changed during application")
    if sha256(project["baseline_path"].read_bytes()).hexdigest() != project["baseline_sha256"]:
        raise ValueError("Source baseline changed during application")
    for name, expected in project["handoff"]["side_input_hashes"].items():
        if sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError("Finished instructions/crosswalk/review evidence changed during application")


def _assert_ledger_overrides_selected(prepared: Mapping[str, Any],
                                      overrides: list[dict[str, Any]]) -> None:
    """Reject active approved values bypassed by policy or baseline preservation.

    Notes
    -----
    Ledger resolution establishes evidence currency and authorization. Field
    preparation must also demonstrate actual selection; merely attaching an
    override to a source row does not mean it governs the proposed model value.
    """
    nodes = {row["key"]: row for row in prepared["nodes"]}
    pipes = {row["key"]: row for row in prepared["pipes"]}
    for override in overrides:
        key, field = override["source_key"], override["field"]
        row = nodes.get(key, pipes.get(key, {}))
        if field in {"length", "diameter"}:
            method = row.get(field + "_method")
        else:
            assignment_field = {"upstream_invert": "upstream", "downstream_invert": "downstream"}.get(field, field)
            method = row.get("assignments", {}).get(assignment_field, {}).get("method")
        if method != "reviewed_override":
            raise ValueError(f"Active approved override {override.get('decision_id', key)!r} "
                             f"for {key}.{field} was not selected by field policy or preserved baseline")


def prepare_project(config_path: str | Path) -> dict[str, Any]:
    """Resolve and validate a project plan without previewing or writing a model.

    Parameters
    ----------
    config_path : str or Path
        Versioned project configuration with declaration-relative input paths.

    Returns
    -------
    dict
        In-memory bundle with resolved/runtime configurations, immutable hymo
        baseline, source snapshots, selected keys, prepared rows, membership,
        frozen handoff, and optional decision ledger snapshot/audit. Path and
        document/config objects are intentionally retained; this bundle is not
        itself a JSON artifact. Its prepared/handoff/ledger members are serializable.

    Raises
    ------
    ValueError
        Configuration, current evidence, identity or approved decision validation
        fails. Stale active ledger approvals never become usable instructions.

    Notes
    -----
    This callable creates no directories or artifacts and never invokes SHMM
    preview/application or an engine. Holds remain available in returned tables;
    publication completeness and actual writer/readback checks belong to run_project.
    Baseline/source/decision fingerprints are rechecked before returning.
    """
    config_path = Path(config_path).resolve()
    resolved = load_config(config_path)
    config = deepcopy(resolved.data)
    baseline_path = Path(config["model"]["baseline"])
    baseline = read_document(baseline_path, encoding=config["model"].get("encoding", "utf-8"))
    baseline_hash = sha256(baseline.data).hexdigest()
    canonical = ingest_sources(resolved)
    _check_source_map_basis(canonical, config)
    side_hashes, crosswalk_snapshot = _crosswalk_canonical(canonical, config)
    overrides_path = config.get("review", {}).get("overrides_path")
    if overrides_path:
        raw = Path(overrides_path).read_bytes()
        ledger = json.loads(raw)
        config["review"]["overrides"] = ledger if isinstance(ledger, list) else ledger["overrides"]
        side_hashes[str(overrides_path)] = sha256(raw).hexdigest()
    selected, membership = select_pipe_keys(canonical, config, baseline)
    # Explicit scope decisions precede model-identity requirements: a rejected
    # source branch need not acquire a manufactured target identity to be omitted.
    scope_selection = apply_scope_exclusions(canonical, config, baseline, selected, membership,
                                             crosswalk_snapshot=crosswalk_snapshot or {})
    selected, membership = scope_selection["selected"], scope_selection["membership"]
    bind_model_identities(canonical, config, crosswalk_snapshot or {}, selected)
    decision_instructions = None
    ledger_path = config.get("review", {}).get("ledger_path")
    if ledger_path:
        from .decisions import resolve_decision_ledger
        decision_instructions = resolve_decision_ledger(ledger_path, canonical, config, baseline,
                                                        selected_pipe_keys=selected)
        config.setdefault("review", {})["overrides"] = deepcopy(decision_instructions["overrides"])
    baseline_nodes = baseline_node_parameters(baseline, config)
    uses_applied_floor = "lowest_applied_pipe_end" in config.get("parameters", {}).get("nodes", {}).get("floor_methods", DEFAULT_FLOOR_METHODS)
    donor_keys = set(selected)
    rejected: dict[str, dict[str, Any]] = {}
    baseline_ends = baseline_pipe_end_parameters(baseline, config) if uses_applied_floor else {}
    ascii_upper = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    pipe_ids = {row["key"]: row["model_id"].translate(ascii_upper)
                for row in canonical["pipes"] if row["key"] in donor_keys}
    floor_donor_audit = None
    legacy_changes_path = config["model"].get("changes_path")
    legacy_changes_hash = sha256(Path(legacy_changes_path).read_bytes()).hexdigest() if legacy_changes_path else None
    # Every rejection removes a donor permanently. Keeping requested rows while
    # freezing rejections prevents a formerly held pipe from reviving as an
    # accepted non-donor and changing the meaning of the minimum floor.
    for iteration in range(len(selected) + 1):
        preparation_options: dict[str, Any] = {"baseline_nodes": baseline_nodes, "selected_pipe_keys": selected}
        if uses_applied_floor:
            replacing_ids = {pipe_ids[key] for key in donor_keys}
            model_ends = {node: [row for row in rows if row["model_id"].translate(ascii_upper) not in replacing_ids]
                          for node, rows in baseline_ends.items()}
            preparation_options.update(floor_donor_pipe_keys=sorted(donor_keys), model_pipe_ends=model_ends)
        prepared = prepare_network_from_dict(canonical, config, **preparation_options).to_dict()
        for row in prepared["pipes"]:
            if row["key"] in rejected:
                rejection = rejected[row["key"]]
                row["disposition"] = "hold"
                row["reasons"] = sorted(set(row.get("reasons", [])) | set(rejection["reasons"]))
                row["floor_donor_rejection"] = deepcopy(rejection)
        if rejected:
            prepared["held"] = [deepcopy(row) for group in ("nodes", "pipes") for row in prepared[group]
                                if row.get("disposition") == "hold"]
            # Core attribution still sees every requested source row. Rebuild
            # prepared connectivity after the frozen application mask so held
            # source bridges cannot merge otherwise separate planned networks.
            prepared["findings"] = [row for row in prepared["findings"] if row.get("rule") != "disconnected_component"]
            prepared["findings"].extend(connectivity_findings(prepared["pipes"], config))
        if decision_instructions is not None:
            _assert_ledger_overrides_selected(prepared, decision_instructions["overrides"])
        handoff = compile_handoff(baseline, prepared, config, config_sha256=resolved.sha256,
                                  source_manifest=canonical.get("manifest"), crosswalk_snapshot=crosswalk_snapshot,
                                  decision_instructions=decision_instructions)
        if legacy_changes_path and handoff["side_input_hashes"].get(str(legacy_changes_path)) != legacy_changes_hash:
            raise ValueError("Legacy finished instructions changed during preparation")
        accepted = set(handoff["applied_source_keys"])
        if not uses_applied_floor:
            break
        removed = donor_keys - accepted
        if not removed:
            if accepted != donor_keys:
                raise ValueError("Applied pipe-end floor donors differ from accepted source instructions")
            for source_key, rejection in sorted(rejected.items()):
                for reason in rejection["reasons"]:
                    handoff["application_holds"].append({"source_key": source_key,
                        "asset_id": rejection["asset_id"], "reason": reason,
                        "floor_donor_rejection_iteration": rejection["iteration"]})
            floor_donor_audit = {"iterations": iteration + 1, "accepted_source_keys": sorted(accepted),
                "rejections": [{"source_key": key, **deepcopy(value)} for key, value in sorted(rejected.items())],
                "baseline_model_end_count": sum(map(len, model_ends.values())),
                "model_generation": "not_performed_by_floor_donor_resolution"}
            break
        writer_reasons: dict[str, list[str]] = {}
        for hold in handoff["application_holds"]:
            writer_reasons.setdefault(hold["source_key"], []).append(hold["reason"])
        for row in prepared["pipes"]:
            if row["key"] in removed:
                reasons = sorted(set(row.get("reasons", [])) | set(writer_reasons.get(row["key"], [])))
                rejected[row["key"]] = {"asset_id": row["asset_id"], "iteration": iteration + 1,
                    "reasons": reasons or ["not_accepted_for_applied_floor_context"],
                    "original_prepared_disposition": row["disposition"]}
        donor_keys.intersection_update(accepted)
    else:
        raise RuntimeError("Applied pipe-end floor resolution failed to converge monotonically")
    handoff["side_input_hashes"].update(side_hashes)
    handoff["selection_exclusions"] = scope_selection["exclusions"]
    handoff["single_pipe_branches"] = scope_selection["branch_candidates"]
    dispositions = {row["key"]: row for row in prepared["pipes"]}
    writer_holds = {row["source_key"]: row["reason"] for row in handoff["application_holds"]}
    applied = set(handoff["applied_source_keys"])
    for row in membership:
        if row["source_key"] in dispositions:
            prepared_row = dispositions[row["source_key"]]
            row["disposition"] = "applied" if row["source_key"] in applied else "hold"
            row["reasons"] = prepared_row.get("reasons", [])
            if row["source_key"] in writer_holds:
                row["reasons"] = row["reasons"] + [writer_holds[row["source_key"]]]
    project = {"config_path": config_path, "resolved_config": resolved, "config": config,
               "baseline_path": baseline_path, "baseline": baseline, "baseline_sha256": baseline_hash,
               "canonical": canonical, "crosswalk_snapshot": crosswalk_snapshot,
               "selected_pipe_keys": selected, "membership": membership, "prepared": prepared,
               "selection_exclusions": scope_selection["exclusions"],
               "single_pipe_branches": scope_selection["branch_candidates"],
               "handoff": handoff, "applied_source_keys": sorted(applied),
               "requested_holds": [row for row in membership if row["disposition"] == "hold"],
               "floor_donor_audit": floor_donor_audit,
               "decision_ledger": deepcopy(decision_instructions["snapshot"]) if decision_instructions is not None else None,
               "decision_ledger_audit": deepcopy(decision_instructions["audit"]) if decision_instructions is not None else None}
    _assert_prepared_inputs_current(project)
    return project


def run_project(config_path: str | Path, *, output_directory: str | Path | None = None) -> dict[str, Any]:
    """Prepare, preview, publish, independently diff and QA a model update.

    Parameters
    ----------
    config_path : str or Path
        Versioned project JSON with declaration-relative source and model paths.
    output_directory : str or Path, optional
        New run directory. Otherwise create a UTC child of configured output root.

    Returns
    -------
    dict
        Verified completion summary with artifact paths, memberships and hashes.
        Engineering findings remain independent of model-file integrity.

    Raises
    ------
    ValueError
        Incomplete complete-request mode, stale inputs, invalid parameters or
        a written candidate differing from its previewed instructions.

    Notes
    -----
    The baseline/source files are never overwritten. A failed run may retain
    diagnostic artifacts but cannot publish a verified completion manifest.
    GIS field names, vertical offsets, material defaults and scenario rules are
    entirely supplied by configuration. No CLI is required.
    """
    from shmm import compare_written_model, edit_model, preview_model_diff, write_diff_reports

    project = prepare_project(config_path)
    resolved, config = project["resolved_config"], project["config"]
    baseline_path, baseline = project["baseline_path"], project["baseline"]
    baseline_hash = project["baseline_sha256"]
    canonical, crosswalk_snapshot = project["canonical"], project["crosswalk_snapshot"]
    selected, membership = project["selected_pipe_keys"], project["membership"]
    prepared, handoff = project["prepared"], project["handoff"]
    dispositions = {row["key"]: row for row in prepared["pipes"]}
    applied = set(project["applied_source_keys"])
    requested_holds = project["requested_holds"]
    mode = config["publication"].get("mode", "supported_subnetwork")
    if mode == "complete_request" and requested_holds:
        raise ValueError("Complete-request publication has held mandatory assignments")
    if mode not in {"supported_subnetwork", "complete_request"}:
        raise NotImplementedError("Candidate runner supports complete/subnetwork modes; use ingestion/preparation for geometry review")
    changes, vertices = instructions_from_handoff(handoff)
    placeholder_nodes = placeholder_node_register(prepared, handoff, config)
    applied_placeholders = [row for row in placeholder_nodes if row["disposition"] == "applied"]
    # Freeze planned bytes and evidence before creating any hydraulic candidate.
    preview = preview_model_diff(baseline, changes, vertex_changes=vertices,
                                evidence=handoff["evidence"], expected_hash=baseline_hash)
    output = Path(output_directory) if output_directory is not None else (
        Path(config["publication"]["output_directory"]) /
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if output == baseline_path.parent.resolve() or output in baseline_path.resolve().parents:
        raise ValueError("Run directory would overlap the baseline location")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "effective_config.json", resolved.to_dict())
    write_json(output / "configuration_origins.json", {"origins": resolved.origins, "files": resolved.files})
    write_json(output / "canonical.json", canonical)
    write_json(output / "prepared.json", prepared)
    write_json(output / "handoff.json", handoff)
    write_csv(output / "scope_exclusions.csv", project["selection_exclusions"])
    write_csv(output / "single_pipe_branches.csv", project["single_pipe_branches"])
    write_csv(output / "force_main_accounting.csv", handoff.get("force_main_accounting", []))
    write_csv(output / "pressure_connections.csv", handoff.get("pressure_nodes", []))
    write_csv(output / "placeholder_nodes.csv", placeholder_nodes)
    if project["floor_donor_audit"] is not None:
        write_json(output / "floor_donor_audit.json", project["floor_donor_audit"])
    if project["decision_ledger"] is not None:
        write_json(output / "decision_ledger.json", project["decision_ledger"])
        write_json(output / "decision_ledger_audit.json", project["decision_ledger_audit"])
    write_csv(output / "membership.csv", membership)
    write_csv(output / "nodes.csv", prepared["nodes"])
    write_csv(output / "pipes.csv", prepared["pipes"])
    write_csv(output / "findings.csv", prepared["findings"])
    write_diff_reports(preview, output, prefix="preview_diff")
    candidate_path = output / "candidate.inp"
    def assert_inputs_current() -> None:
        _assert_prepared_inputs_current(project)
    assert_inputs_current()
    edit_model(baseline_path, changes, output_path=candidate_path, audit_path=output / "application_audit.json",
               vertex_changes=vertices, expected_hash=baseline_hash, encoding=config["model"].get("encoding", "utf-8"))
    candidate = read_document(candidate_path, encoding=config["model"].get("encoding", "utf-8"))
    written = compare_written_model(baseline, candidate, changes, vertex_changes=vertices,
                                   evidence=handoff["evidence"], expected_hash=baseline_hash)
    write_diff_reports(written, output, prefix="written_model_diff")
    if not written.report["verification"]["matches_expected"]:
        raise ValueError("Written-model diff disagrees with previewed instructions")
    profile_verification = verify_prepared_profiles(candidate, prepared, handoff, config)
    write_json(output / "profile_verification.json", profile_verification)
    candidate_findings = (candidate_engineering_qa(candidate, config,
        placeholder_node_ids=[row["model_id"] for row in applied_placeholders])
        if applied_placeholders else candidate_engineering_qa(candidate, config))
    from .qa import finding
    for node in applied_placeholders:
        candidate_findings.append(finding("placeholder_rim", "model/JUNCTIONS/" + node["model_id"], config,
            source_key=node["source_key"], asset_id=node["asset_id"], field="max_depth",
            native_placeholder_value=node["model_max_depth"], native_unit=node["unit"],
            rim_m=node["rim_m"], floor_m=node["floor_m"], assignment=node["assignment"],
            basis=node["basis"]))
    baseline_findings = candidate_engineering_qa(baseline, config)
    identity = lambda row: (row["rule"], row["object_key"], row.get("endpoint"))
    previous = {identity(row) for row in baseline_findings}
    for row in candidate_findings:
        row["baseline_status"] = "preexisting_or_changed" if identity(row) in previous else "introduced"
    write_json(output / "candidate_qa.json", {"baseline_findings": baseline_findings, "candidate_findings": candidate_findings})
    write_csv(output / "candidate_qa.csv", candidate_findings)
    # Exact identity connector candidates remain evidence only. Neither graph
    # diagnosis nor a missing outlet path authorizes adding excluded source pipes.
    source_connectors = _topology_source_connectors(canonical, membership, handoff, config, crosswalk_snapshot)
    topology_report = diagnose_swmm_topology(candidate, baseline, source_connectors=source_connectors)
    write_json(output / "topology.json", topology_report)
    write_csv(output / "topology_components.csv", topology_report["components"])
    write_csv(output / "topology_nodes.csv", topology_report["nodes"])
    write_csv(output / "topology_connector_candidates.csv", topology_report["source_connector_candidates"])
    write_review_html(output / "review.html", prepared, membership, candidate_findings=candidate_findings)
    # Re-read configured inputs so a stale captured configuration or source packet
    # cannot be reported as a verified current result.
    assert_inputs_current()
    engine = {"status": "not_run"}
    engine_policy = config.get("qa", {}).get("engine", {})
    if engine_policy.get("enabled"):
        from shmm.engine import check_engine
        engine_directory = output / "engine"
        engine_directory.mkdir()
        engine = check_engine(candidate_path, executable=engine_policy["executable"],
                              version_library=engine_policy["version_library"],
                              expected_version=engine_policy["expected_version"],
                              output_directory=engine_directory)
        engine = {"status": "passed" if engine.passed else "failed", "result": json_value(engine)}
        write_json(output / "engine_check.json", engine)
    # The optional solver may take long enough for source inputs to change.
    # Verify provenance again before certifying this run.
    assert_inputs_current()
    engineering_holds = sum(row.get("disposition") == "hold" for row in candidate_findings)
    if mode == "complete_request" and engineering_holds:
        raise ValueError("Complete-request publication has written-model engineering holds")
    engineering_status = "held" if engineering_holds else ("review_required" if applied_placeholders or any(row.get("severity") != "info" for row in candidate_findings) else "clear")
    completion = {"status": "verified_model_update", "candidate": str(candidate_path),
        "review": str(output / "review.html"), "scenario": config["selection"]["scenario"],
        "config_sha256": resolved.sha256, "baseline_sha256": baseline_hash,
        "candidate_sha256": sha256(candidate.data).hexdigest(), "requested_source_pipes": len(selected),
        "excluded_source_pipes": sum(row["disposition"] == "excluded" for row in membership),
        "applied_source_pipes": len(applied), "held_source_pipes": sum(row["disposition"] == "hold" and row["source_key"] in dispositions for row in membership),
        "scope_or_status_holds": sum(row["disposition"] == "hold" and row["source_key"] not in dispositions for row in membership),
        "new_junctions": sum(c.action == "add" and c.section == "JUNCTIONS" for c in changes),
        "applied_placeholder_nodes": len(applied_placeholders),
        "held_placeholder_nodes": len(placeholder_nodes) - len(applied_placeholders),
        "explicit_existing_updates": sum(c.action == "update" for c in changes),
        "preview_matches_written": True, "source_preservation": True,
        "engineering_findings": len(prepared["findings"]), "candidate_qa_findings": len(candidate_findings), "engine": engine,
        "engineering_status": engineering_status, "written_engineering_holds": engineering_holds,
        "topology": topology_report["summary"],
        "calibration_acceptance": "not_established"}
    write_json(output / "completion.json", completion)
    return completion
