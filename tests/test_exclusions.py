"""Synthetic scope-exclusion QA; no private utility identifiers or locations.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-15
Manually maintained regression and invalid-input tests.
"""
from copy import deepcopy
from pathlib import Path

from hymo import SWMMDocument
import pytest

from umdev.configuration import ConfigurationError, load_config, validate_config
from umdev.exclusions import apply_scope_exclusions


def setup_case(extra_pipes=(), *, baseline_extra="", mode="review"):
    """Build a known active gravity leaf joined to a preserved model junction."""
    baseline = SWMMDocument.from_bytes(("[JUNCTIONS]\nAnchor 10 2\nOther 9 2\n" + baseline_extra).encode())
    pipes = [{"key": "src/pipes/P", "asset_id": "P", "from_node": "Leaf", "to_node": "Anchor",
              "pipe_kind": "gravity", "status": "active", "source_record_sha256": "a" * 64}, *deepcopy(extra_pipes)]
    identities = {p[field] for p in pipes for field in ("from_node", "to_node") if p.get(field)}
    canonical = {"nodes": [{"key": "src/nodes/" + identity, "asset_id": identity} for identity in sorted(identities)], "pipes": pipes}
    config = {"scenarios": {"existing": {"include_statuses": ["active"], "exclude_statuses": ["abandoned"]}},
              "selection": {"scenario": "existing", "single_pipe_branches": {"mode": mode, "reason": "Reviewed branch scope"}}}
    membership = [{"source_key": p["key"], "asset_id": p["asset_id"], "disposition": "selected_for_preparation", "reasons": []} for p in pipes]
    return canonical, config, baseline, [p["key"] for p in pipes], membership


def call(case):
    return apply_scope_exclusions(*case)


def extra(start="Leaf", end="Far", status="active", kind="gravity"):
    return {"key": "src/pipes/Q", "asset_id": "Q", "from_node": start, "to_node": end, "status": status, "pipe_kind": kind}


def test_default_review_does_not_mutate_or_exclude():
    case = setup_case()
    before = deepcopy((case[0], case[1], case[3], case[4]))
    result = call(case)
    assert result["selected"] == ["src/pipes/P"]
    assert result["branch_candidates"][0]["status"] == "review_only"
    assert result["branch_candidates"][0]["leaf_node"] == "Leaf"
    assert (case[0], case[1], case[3], case[4]) == before
    assert result["membership"] is not case[4]


def test_enabled_rule_excludes_with_full_evidence():
    result = call(setup_case(mode="exclude"))
    assert result["selected"] == []
    row = result["membership"][0]
    assert row["disposition"] == "excluded"
    assert row["reasons"] == ["single_pipe_branch_scope_exclusion"]
    assert row["scope_exclusion"]["branch_evidence"]["leaf_active_source_degree"] == 1
    assert row["scope_exclusion"]["source_sha256"] == "a" * 64
    assert result["branch_candidates"][0]["applied"]


@pytest.mark.parametrize("status", ["active", "unknown"])
@pytest.mark.parametrize("kind", ["gravity", "force_main", "unknown"])
def test_complete_context_blocks_leaf_even_when_other_pipe_unselected(status, kind):
    case = setup_case([extra(status=status, kind=kind)], mode="exclude")
    case[4][1].update(disposition="not_selected", reasons=["outside_requested_asset_selection"])
    case[3].remove("src/pipes/Q")
    assert call(case)["branch_candidates"] == []


def test_known_inactive_link_does_not_block_active_leaf():
    result = call(setup_case([extra(status="abandoned")], mode="exclude"))
    assert [row["source_key"] for row in result["branch_candidates"]] == ["src/pipes/P"]


def test_branch_rule_never_cascades_after_explicit_exclusion():
    case = setup_case([extra()], mode="exclude")
    case[1]["selection"]["pipe_exclusions"] = [{"source_key": "src/pipes/Q", "reason": "Separate reviewed exclusion"}]
    result = call(case)
    assert result["selected"] == ["src/pipes/P"]
    assert result["branch_candidates"] == []


@pytest.mark.parametrize("kind", ["force_main", "unknown"])
def test_only_known_gravity_candidate_qualifies(kind):
    case = setup_case(mode="exclude")
    case[0]["pipes"][0]["pipe_kind"] = kind
    assert call(case)["branch_candidates"] == []


@pytest.mark.parametrize("section", ["CONDUITS", "PUMPS", "WEIRS", "ORIFICES", "OUTLETS"])
def test_any_existing_model_link_protects_endpoint_pair(section):
    case = setup_case(mode="exclude", baseline_extra=f"[{section}]\nExisting Anchor Leaf 1\n")
    assert call(case)["branch_candidates"] == []
    assert call(case)["selected"] == ["src/pipes/P"]


def test_existing_link_id_protected_even_with_different_endpoints():
    assert call(setup_case(mode="exclude", baseline_extra="[CONDUITS]\nP Anchor Other 1\n"))["branch_candidates"] == []


@pytest.mark.parametrize("section", ["JUNCTIONS", "STORAGE", "OUTFALLS", "DIVIDERS"])
def test_existing_leaf_node_is_preserved(section):
    assert call(setup_case(mode="exclude", baseline_extra="Leaf 1 2\n" if section == "JUNCTIONS" else f"[{section}]\nLeaf 1 2\n"))["branch_candidates"] == []


def test_branch_preserve_exception_and_explicit_exclusion_precedence():
    case = setup_case(mode="exclude")
    case[1]["selection"]["single_pipe_branches"]["preserve_pipe_keys"] = ["src/pipes/P"]
    assert call(case)["selected"] == ["src/pipes/P"]
    case[1]["selection"]["pipe_exclusions"] = [{"source_key": "src/pipes/P", "reason": "Reviewed", "decision_id": "D1", "source_sha256": "a" * 64}]
    result = call(case)
    assert not result["selected"]
    assert result["membership"][0]["reasons"] == ["explicit_scope_exclusion"]
    assert result["exclusions"][0]["decision_id"] == "D1"


def test_explicit_exclusion_of_baseline_representation_never_edits_document():
    case = setup_case(baseline_extra="[CONDUITS]\nP Anchor Other 1\n")
    before = case[2].data
    case[1]["selection"]["pipe_exclusions"] = [{"source_key": "src/pipes/P", "reason": "Skip source updates"}]
    assert call(case)["selected"] == []
    assert case[2].data == before


@pytest.mark.parametrize("method", ["explicit", "branch"])
@pytest.mark.parametrize("disposition", ["hold", "not_selected"])
def test_held_exclusion_and_outside_scope_audit(method, disposition):
    case = setup_case(mode="exclude" if method == "branch" else "review")
    if method == "explicit":
        case[1]["selection"]["pipe_exclusions"] = [{"source_key": "src/pipes/P", "reason": "Reviewed"}]
    case[3].clear()
    case[4][0].update(disposition=disposition, reasons=["original_reason"])
    result = call(case)
    assert result["exclusions"][0]["previous_reasons"] == ["original_reason"]
    assert result["membership"][0]["disposition"] == ("excluded" if disposition == "hold" else "not_selected")
    assert result["exclusions"][0]["applied"] == (disposition == "hold")


def test_stale_fingerprint_rejected():
    case = setup_case()
    case[1]["selection"]["pipe_exclusions"] = [{"source_key": "src/pipes/P", "reason": "Reviewed", "source_sha256": "b" * 64}]
    with pytest.raises(ValueError, match="Stale"):
        call(case)


@pytest.mark.parametrize("mutation", ["unknown_exclusion", "duplicate_exclusion", "unknown_preserve", "missing_membership", "duplicate_membership", "wrong_selection"])
def test_invalid_runtime_inputs_rejected(mutation):
    case = setup_case()
    if mutation in {"unknown_exclusion", "duplicate_exclusion"}:
        entry = {"source_key": "unknown" if mutation == "unknown_exclusion" else "src/pipes/P", "reason": "Reviewed"}
        case[1]["selection"]["pipe_exclusions"] = [entry] * (2 if mutation == "duplicate_exclusion" else 1)
    elif mutation == "unknown_preserve":
        case[1]["selection"]["single_pipe_branches"]["preserve_pipe_keys"] = ["unknown"]
    elif mutation == "missing_membership":
        case[4].clear()
    elif mutation == "duplicate_membership":
        case[4].append(deepcopy(case[4][0]))
    else:
        case[3].clear()
    with pytest.raises(ValueError):
        call(case)


def test_frozen_crosswalk_and_ascii_case_matching():
    case = setup_case(mode="exclude")
    case[1]["identity"] = {"model_association": {"methods": ["explicit_crosswalk"], "crosswalk_path": "never-read.json"}}
    crosswalk = {"nodes": {row["asset_id"]: row["asset_id"].lower() for row in case[0]["nodes"]}, "pipes": {"P": "p"}}
    assert apply_scope_exclusions(*case, crosswalk_snapshot=crosswalk)["selected"] == []
    del crosswalk["nodes"]["Anchor"]
    assert apply_scope_exclusions(*case, crosswalk_snapshot=crosswalk)["branch_candidates"] == []


def test_explicit_exclusion_needs_no_identity_resolution():
    case = setup_case(mode="exclude")
    case[1]["identity"] = {"model_association": {"methods": []}}
    case[1]["selection"]["pipe_exclusions"] = [{"source_key": "src/pipes/P", "reason": "Incomplete identity excluded"}]
    assert call(case)["selected"] == []


@pytest.mark.parametrize("entry", [None, {}, {"source_key": "", "reason": "x"}, {"source_key": "s", "reason": ""}, {"source_key": "s", "reason": "x", "source_sha256": "ABC"}, {"source_key": "s", "reason": "x", "decision_id": None}, {"source_key": "s", "reason": "x", "typo": 1}])
def test_explicit_exclusion_config_validation(entry):
    config = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").to_dict()
    config["selection"]["pipe_exclusions"] = entry if entry is None else [entry]
    with pytest.raises(ConfigurationError):
        validate_config(config)


@pytest.mark.parametrize("branch", [{"mode": "delete"}, {"mode": "exclude"}, {"mode": "exclude", "reason": " "}, {"preserve_pipe_keys": ["s", "s"]}, {"preserve_pipe_keys": "s"}, {"typo": 1}, {"decision_id": " "}])
def test_branch_config_validation(branch):
    config = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").to_dict()
    config["selection"]["single_pipe_branches"] = branch
    with pytest.raises(ConfigurationError):
        validate_config(config)


def test_valid_scope_policy_configuration():
    config = load_config(Path(__file__).parent / "fixtures/schema_equivalence/utility_a.config.json").to_dict()
    config["selection"].update(pipe_exclusions=[{"source_key": "s", "reason": "Reviewed", "source_sha256": "a" * 64, "decision_id": "D1"}], single_pipe_branches={"mode": "exclude", "reason": "Scope", "preserve_pipe_keys": ["s"]})
    validate_config(config)


def test_rule_decision_id_retained_in_audit():
    case = setup_case(mode="exclude")
    case[1]["selection"]["single_pipe_branches"]["decision_id"] = "SCOPE1"
    result = call(case)
    assert result["exclusions"][0]["decision_id"] == "SCOPE1"
    assert result["branch_candidates"][0]["decision_id"] == "SCOPE1"


def test_applied_branch_audit_has_only_topology_evidence():
    result = call(setup_case(mode="exclude"))
    audit = result["exclusions"][0]
    assert audit["applied"] is True
    assert "applied" not in audit["branch_evidence"]
    assert "status" not in audit["branch_evidence"]
    assert "mode" not in audit["branch_evidence"]
    assert "preserved" not in audit["branch_evidence"]
    assert audit["branch_evidence"]["leaf_active_source_degree"] == 1
