# umdev to SHMM handoff and manual model QA

- Author: Lucas HT Nguyen
- Last modified by: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-15

Status: Implemented in umdev 0.1.0 with SHMM >=0.1.2 and hymo >=0.2.1.

## Summary

umdev owns the engineering assignments and scenario plan. SHMM owns the comparison
between the baseline, the explicit requested updates and the model actually written.
Both preview and written-model diffs are required products of a candidate build.

### Implemented application boundary

`run_project` freezes a handoff with `models.compile_handoff`, produces both
SHMM comparisons, verifies prepared-to-written profiles and writes completion
only after integrity checks. Sources/config/outputs stay in private workspaces.

Prepared network objects compile supported circular gravity and explicit FORCE_MAIN
conduits and junctions. Existing STORAGE endpoints may receive connections using
preserved baseline floor/top parameters; no STORAGE, curve, coordinate or control
updates are generated for those endpoints. New storage creation and
outfall-endpoint contracts remain unsupported. The storage model
top is not relabeled as a surveyed ground rim. `model.changes_path` can supply additional finished SHMM operations
with separate evidence. Writer capability holds are distinct from source-data
holds. Existing-floor dependencies, rewiring, shape conversion
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
| force_main_accounting | Every requested pressure pipe, matching existing pumps, planned membership and preparation holds |
| pressure_nodes | Pressure roles, basis and previous/expected junction surcharge, including no-op baseline settings |
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

Use `umdev.prepare_project(config_path)` for ingestion, identity binding, ledger
validation, parameter preparation and frozen handoff compilation without filesystem
outputs or SHMM preview/application. It returns inspectable holds even when a
complete-request publication would fail. Compiler membership labels `applied` are
planned acceptance at this stage; they do not report actual model edits.

The publication orchestrator is `umdev.run_project(config_path, output_directory=None)`.
It shares that same preparation path before previewing and writing. A configured
ledger adds `decision_ledger.json` and `decision_ledger_audit.json` to the run; its
compiled changes, attribution and consumed file fingerprints remain in the handoff.
The compiler rejects a ledger edit targeting a field already set by preparation.
Decision, source and baseline hashes are rechecked before and after application.
Lower-level handoff/readback functions live in `umdev.models`.
Project configuration `schema_version` is integer `1`; the serialized handoff
and SHMM comparison schema versions are string `"1"`.

The callable SHMM contract is `preview_model_diff`,
`compare_written_model` and `write_diff_reports`. The adapter instantiates SHMM
Change/VertexChange records; it does not write INP text itself. Use
`preview_changes`/`edit_model` for the existing preserving transaction.

For the `lowest_applied_pipe_end` floor fallback, preparation uses a monotone
read-only eligibility loop. It prepares the original requested rows, compiles the
supported handoff, removes rejected pipes from floor donation, then repeats until
the donor set equals the accepted source-instruction set. Rejected rows retain
their first reasons and remain held; they cannot revive as accepted non-donors.
This may conservatively retain a hold that a different engineering policy could
resolve. The optional `floor_donor_audit` bundle member records passes, accepted
keys and rejection reasons; normal publication writes `floor_donor_audit.json`.

The baseline context reader `baseline_pipe_end_parameters` uses actual CONDUITS
only. At a local end, DEPTH adds its explicit offset to that node's invert;
ELEVATION uses the explicit absolute end value. JUNCTIONS, STORAGE, OUTFALLS and
DIVIDERS provide their own invert fields, with no inferred rims or pump/regulator
levels. Missing, nonfinite and `*` offsets are unsupported here and fail explicitly.
Baseline ends superseded by planned accepted source updates are removed from
context; retained unchanged conduits keep their original local ends and byte hash.
Legacy finished changes that add/delete conduits, change their endpoints or
offsets, or change node invert elevations are rejected when applied-end floor
inference is enabled: those independent edits would invalidate the frozen context.
Legacy length and diameter updates remain supported and fingerprinted. Prepared
connectivity findings are rebuilt after frozen rejections, so held bridges cannot
join accepted source networks in those diagnostics.
Structure records retain priority, and existing model floor preservation still
prevents implicit dependent-end edits. No source GIS US/DS numeric field becomes
a structure observation merely by participating in this planning loop.

Implemented build order: prepare -> freeze handoff -> preview diff -> publish candidate
-> read actual file -> written-model diff -> umdev engineering QA -> completion
manifest. Engine checking is optional and recorded separately.

SHMM regression tests alter the written file independently to prove detection of unrequested
values, missing changes, geometry-order changes and preserved-text changes. A second
test must establish that exact no-op and repeated-section inputs are reported
faithfully. No approval prompt is introduced merely because a candidate has ordinary
engineering warnings.

### Pressure-pipe handoff

Force-main preparation provides `shape=FORCE_MAIN`, a separate `pressure_assignment`
(value, equation, canonical unit, basis), and a positive CONDUITS Manning field.
H-W C remains dimensionless. D-W absolute roughness stays in metres until the
adapter converts Geom2 to inches for US flow units or millimetres for SI flow units,
as specified in [EPA SWMM 5.2 Table D-2](https://www.epa.gov/system/files/documents/2022-04/swmm-users-manual-version-5.2.pdf).
SWMM manages its force-main friction calculation; the CONDUITS field is not reused
as C or absolute roughness. Baseline options must agree and use Dynamic Wave.

Sealed source connections receive the configured surcharge only when an accepted
force main uses the node. An enabled internal-connection policy also examines
existing modeled force mains: incoming pressure/pump plus outgoing force main,
without gravity/regulator connections. Storage, receiving manholes and unresolved
terminals retain their settings. Explicit source roles take precedence over baseline
inference. Exact or reversed force-main endpoint pairs matching existing pumps are
held to prevent a passive bypass. This is an association warning, not proof that
the detailed pressure network has been modeled.

`force_main_accounting.csv` and `pressure_connections.csv` expose this review.
Independent readback verifies force-main shape, coefficient and serialization units,
CONDUITS coefficient, and every specified or preserved pressure-node surcharge.
Missing pipe elevations, rims, roles or coefficients remain holds.

### Missing-rim placeholder handoff

A placeholder remains an assumed rim assignment with method
`missing_rim_placeholder`, a `placeholder_rim` flag, the original missing value,
policy, floor evidence and native depth quantity. Only accepted new-junction
instructions write it. The adapter supplies literal configured MaxDepth for
`unit=model`, avoiding round-trip floating-point spellings such as
12.344999999999999. It supplies a finished SHMM `Change.comment` containing
`UMDEV_PLACEHOLDER_RIM` and the native depth. SHMM owns comment serialization
and exact preview/written comparisons. Existing profiles remain preserved.

Run `placeholder_nodes.csv` includes held assignments as well as actually applied
node identities. Candidate QA joins applied assumptions to actual written node
IDs, so a placeholder cannot disappear merely because INP stores only numbers.
Readback checks the literal MaxDepth token, its marker and the floor/rim profile.
Completion counts placeholders separately from integrity status and always
requires engineering review when any are applied.

Known pipe crowns above a placeholder rim remain visible as
`pipe_crown_above_placeholder_rim` review findings. They do not automatically inherit
the hold rule for contradictory measured rims; projects can explicitly configure
the placeholder rule as a hold. The actual written-model check uses the applied
placeholder identities to retain this distinction.

### Scope exclusions before model preparation

Scope selection can explicitly exclude source pipes or apply the enabled single-pipe
branch rule. Exclusions happen before identity binding, selected ledger overrides
and the applied-end donor loop. They do not reduce full source attribution context.
The handoff includes `selection_exclusions` and `single_pipe_branches`; the run
writes both dedicated CSV registers and membership dispositions. Excluded pipes
produce no hydraulic/geometry instructions or orphan node additions. A source
exclusion never implies deletion of its existing baseline counterpart.

SHMM preview and written diffs continue to compare actual instructions with the
immutable baseline. Scope exclusions are explained in the upstream registers, not
invented as model deletions. On regeneration from the original baseline, newly added
assets in an earlier candidate may disappear when now excluded; the private previous-
run comparison must expose that scope change separately from integrity verification.
Completion counts the effective remaining request and exclusions separately.

Scope exclusions govern source preparation. Separately approved finished ledger
operations retain their independent scope; changing those approvals requires a
reviewed ledger decision rather than an inferred deletion or scope override.
