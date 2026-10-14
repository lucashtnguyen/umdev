# umdev to SHMM handoff and manual model QA

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-14

Status: Implemented in umdev 0.1.0 with SHMM >=0.1.2 and hymo >=0.2.1.

## Summary

umdev owns the engineering assignments and scenario plan. SHMM owns the comparison
between the baseline, the explicit requested updates and the model actually written.
Both preview and written-model diffs are required products of a candidate build.

### Implemented application boundary

`run_project` freezes a handoff with `models.compile_handoff`, produces both
SHMM comparisons, verifies prepared-to-written profiles and writes completion
only after integrity checks. Sources/config/outputs stay in private workspaces.

Prepared network objects compile supported circular gravity conduits and
junctions. Existing STORAGE endpoints may receive gravity-pipe connections using
preserved baseline floor/top parameters; no STORAGE, curve, coordinate or control
updates are generated for those endpoints. New storage creation, pressure-pipe
parameters and outfall-endpoint contracts remain unsupported. The storage model
top is not relabeled as a surveyed ground rim. `model.changes_path` can supply additional finished SHMM operations
with separate evidence. Writer capability holds are distinct from source-data
holds. Existing-floor dependencies, rewiring, shape conversion, pressure parameters
and existing-vertex replacement are unsupported by this adapter.

`supported_subnetwork` publishes complete supported objects and lists held
requests. `complete_request` requires mandatory assignments and candidate holds
to be clear. Neither mode establishes engineering acceptance. `geometry_review`
publication remains future work.

### Handoff contract

A version-1 JSON handoff contains:

| Field | Meaning |
| --- | --- |
| schema_version | Handoff contract version |
| baseline_sha256 | Exact baseline bytes against which operations were prepared |
| scenario | Selected scenario, separate from other existing/future plans |
| config_sha256 | Effective umdev configuration fingerprint |
| prepared_sha256 | Fingerprint of prepared nodes, links, assignments and operation membership |
| source_manifest | Read-only input snapshot/content hashes and adapter identities |
| changes | Serialized explicit SHMM Change instructions including expected old values |
| vertex_changes | Serialized ordered SHMM VertexChange instructions |
| evidence | Source-key-indexed assignment basis, source references, category, units and QA findings |
| application_holds | Objects excluded by writer capabilities, with reasons |
| side_input_hashes | Finished-change/crosswalk/review fingerprints checked before completion |
| applied_source_keys, model_ids | Explicit applied membership and source-to-target identities |

The handoff is data, not executable code. Model IDs and values are finished and in
target model units. Null required values, inferred deletes, implicit rewiring and
unsupported operations are not serialized as usable instructions. Every requested
object/field retains an assignment reference even when its value equals the baseline.

The fingerprint of a plan excludes volatile run timestamps. A stale baseline or
changed evidence requires preparation/preview again. Immutable instructions are
used for both preview and application.

### Preview diff

SHMM previews the supplied operations against the baseline and produces JSON, CSV
and a standalone filterable HTML report before publication. It shows object/section/
field, add/update, old/new values, target units, requested/no-op status, ordered
geometry changes, source basis and upstream QA flags. Nonspatial changes remain
visible. Unknown or repeated sections must not be collapsed into a misleading
single-row dictionary.

The preview includes baseline and expected candidate hashes and a raw text diff.
It distinguishes the actual effect of a requested operation from upstream
observations that were held or not requested. umdev supplies a linked membership/
hold report; SHMM does not select engineering evidence or repair source GIS.

### Written-model diff

After SHMM writes a separate candidate, read the actual file through hymo and
compare it with both the baseline and the expected candidate. Produce a second
JSON/CSV/HTML report. It must detect missing requested effects, unrequested changes,
altered geometry order and changes to comments, opaque cards or other preserved
text. No-op instructions remain explicit.

A comparison mismatch is an unsuccessful application verification, even if the
file parses or the SWMM engine returns success. Keep diagnostic artifacts separate
from a verified completion record. File integrity is distinct from umdev engineering
QA and from engine/hydraulic acceptance. Completion reports
`engineering_status` independently and records whether an engine ran;
`calibration_acceptance` remains unestablished.

### Manual review

Review preview and written-model reports together with the assignment/hold tables.
Inspect changed diameters, node floors and reconstructed rims, connection offsets,
absolute pipe inverts, grades, geometry and inherited model content. Maps/profile
views are optional extensions; their absence must not hide changes from the tables.
An HGL can only be displayed from matching engine results.

Sources, effective project config, handoff, reports and actual model outputs remain
in the private project workspace. Public tests contain synthetic networks.

## Developer README

The orchestrator is `umdev.run_project(config_path, output_directory=None)`.
Lower-level handoff/readback functions live in `umdev.models`.
Project configuration `schema_version` is integer `1`; the serialized handoff
and SHMM comparison schema versions are string `"1"`.

The callable SHMM contract is `preview_model_diff`,
`compare_written_model` and `write_diff_reports`. The adapter instantiates SHMM
Change/VertexChange records; it does not write INP text itself. Use
`preview_changes`/`edit_model` for the existing preserving transaction.

Implemented build order: prepare -> freeze handoff -> preview diff -> publish candidate
-> read actual file -> written-model diff -> umdev engineering QA -> completion
manifest. Engine checking is optional and recorded separately.

SHMM regression tests alter the written file independently to prove detection of unrequested
values, missing changes, geometry-order changes and preserved-text changes. A second
test must establish that exact no-op and repeated-section inputs are reported
faithfully. No approval prompt is introduced merely because a candidate has ordinary
engineering warnings.
