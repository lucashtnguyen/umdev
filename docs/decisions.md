# Consolidated manual decision ledger

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-15
- Manually maintained. Examples and tests are synthetic; actual decisions stay private.

## Summary

A private ledger records approved engineering assignments, previously authorized
model updates and non-executable review intentions in one place. It supplements
observations instead of overwriting source GIS or patching a candidate. The ledger
is a project input, separate from the derived manual-review register and SHMM's
record of what was actually written.

Set review.ledger_path to a declaration-relative JSON file. This setting cannot
be combined with legacy review.overrides_path, inline review.overrides or
model.changes_path. Old projects without a ledger keep their previous behavior.

Call umdev.prepare_project for a read-only plan. It ingests evidence, resolves
decisions, prepares parameters and compiles instructions, without invoking preview,
model editing, publication, an engine or output-directory creation. A caller can
save its ledger snapshot/audit and review tables separately. Membership labels
inherited from the compiler describe planned acceptance, not a completed edit.

Call run_project only when model generation is authorized. It uses the same
preparation, then creates the ordinary preview and written-model diffs. Each run
retains decision_ledger.json and decision_ledger_audit.json alongside the handoff.
Ledger instruction source keys are decision/<id>; original keys and complete
decision evidence remain attached for both SHMM comparisons.

### Decision record

The JSON object declares integer schema_version 1, kind
umdev_manual_decision_ledger, optional metadata and a decisions array.
Every decision has:

| Field | Meaning |
| --- | --- |
| id | Unique stable decision ID |
| status | approved, proposed, rejected or archived |
| kind | parameter_override, model_update or review_record |
| scenario | Exact scenario to which the decision belongs |
| recorded_by, recorded_date | Who entered the record and ISO-8601 recording date |
| authorization | Required basis and reference; reviewer and approval/authority date only when known |
| rationale | Engineering reason for the assignment |
| evidence | One or more fingerprinted canonical source records or external files |
| context | Optional original observation, earlier run, uncertainty and migration provenance |
| supersedes | Optional previous approved decision for the same target/scenario |
| baseline_sha256 | Required for finished model updates; optional for baseline-dependent source decisions |

A recording date is not an invented engineering signoff date. Migrations retain
inherited workflow authorization and original evidence, including an unknown
individual reviewer/date when that is all the previous records establish.

Only approved, unsuperseded decisions in the current scenario can execute.
Proposed replacements do not retire an approved predecessor. Conflicting active
approvals are rejected. Append a new ID and supersedes link for a correction;
retain the original entry. JSON is not an append-only database: frozen validation
and run snapshots preserve earlier versions, while the validator checks history
relationships within the supplied ledger.

### Executable assignments and capability limits

A parameter_override contains override with source_key, field, value and unit.
Supported node fields are floor/rim; supported pipe fields are upstream_invert,
downstream_invert, diameter and length. Elevations require vertical_datum.
Units/datum normalization and normal engineering QA still apply. Values may be
zero/negative for elevations; pipe dimensions must be positive.

An override must fingerprint its own canonical source record. Pipe-end overrides
also require the unique corresponding endpoint structure fingerprint. This prevents
a correction from silently surviving changes to the underlying port observation.
Overrides outside the selected pipe/endpoint scope are recorded as inactive.
Preparation rejects a current approval that its configured field policy does not
actually select, including an unsupported alteration of a preserved baseline profile.

A model_update contains change with an explicit update, section, object_id,
values, original source_key and expected old values. Version1 supports only
existing CONDUITS.length and CIRCULAR XSECTIONS.geom1 updates. units maps the
changed field to its baseline length unit; values are not implicitly converted.
An exact baseline hash, unique target, old-value precondition and explicit units
are required. Unsupported fields/operations need a future parameter contract;
do not package a pressure coefficient as a circular diameter or conduit length.
A finished decision cannot silently override an automatically prepared field.

A review_record contains a nonempty descriptive record mapping. It can retain an
approved intention awaiting an identity crosswalk, a representation decision or
historical parser release. It deliberately produces no model instructions.
Recording an approval does not establish that the adapter can execute it.

### Evidence, stale decisions and manual QA

Each evidence item declares exactly one source_key or path, plus a lowercase
64-character SHA-256. Source keys refer to canonical source_record_sha256 values.
External paths resolve relative to the ledger file. References should cover all
observations that the engineering assignment relies on, not only its target pipe.

Applicable approved evidence and baseline fingerprints must match. Stale/missing
active evidence fails the preparation before any model publication, even when
the project's legacy override policy would otherwise re-evaluate observations.
Other-scenario, archived and superseded decisions remain history rather than
causing a guessed current assignment. Current review_record evidence is checked
despite its intentionally non-executable nature.

For review, inspect original observation, replacement, units/datum, rationale,
authority, scenario, fingerprint and whether the decision is ready, deferred,
superseded or record-only. Compare the eventual SHMM written-model diff with
the ledger; a ready decision is not proof of a completed model edit or hydraulic
acceptance. Any direct model-viewer edits require separate comparison and
reconciliation into recorded instructions.

## Developer README

umdev.decisions.resolve_decision_ledger(path, canonical, config, baseline,
selected_pipe_keys=...) returns serializable snapshot, audit, overrides, changes,
evidence and side_input_hashes. It reads no model through a writer and creates no
files. prepare_project exposes these within its in-memory preparation bundle.

The runner freezes ledger/file fingerprints and rechecks them with source/config
hashes before completion. compile_handoff accepts optional frozen
decision_instructions; SHMM retains exclusive responsibility for model writing.

Run python -m pytest. Synthetic tests cover approvals and non-executable states,
supersession, conflicts, malformed/duplicate JSON, source and file changes,
both endpoint fingerprints, scope, dimensions/elevations, baseline units and
preconditions, policy selection, read-only preparation, report retention and
legacy compatibility. No private decisions are needed in tests.

### Reviewed scope policies

Explicit pipe exclusions and optional single-pipe branch rules live in private
selection configuration. Record their authorization in a `review_record` with the
policy file's SHA-256 evidence and use its ID in the selection policy. The existing
ledger record validates provenance; selection executes the declared policy. No new
model-update or synthetic parameter override is used to represent exclusion.
Individual exceptions can fingerprint the source pipe through `source_sha256`.
Runtime scope registers retain every applied/deferred exclusion, reason, decision
ID and topology evidence. Excluded source overrides become outside-selected-scope
decisions; they remain recorded and cannot silently alter the retained model.
