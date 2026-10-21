# Reviewed lifecycle retirements

Author: Lucas HT Nguyen  
Contact: [GitHub](https://github.com/lucashtnguyen)  
Date: 2026-10-21

## Summary

`plan_lifecycle_retirements` prepares explicit, reviewed instructions for removing
obsolete model conduits. It does not write model or source files. A GIS asset's
lifecycle and a model representation are different things: a single model pipe
can contain both retired and active GIS segments. The complete reviewed membership
must be supplied, and every active member needs an independent preservation or
scope disposition. Proximity and a pending register decision never authorize
automatic deletion.

The function takes the exact current Hymo `SWMMDocument`, complete canonical source
pipes, and private reviewed mappings. It binds complete model bytes, native tokens,
raw source hashes and full normalized source records. Keep real asset identifiers,
coordinates, decisions and evidence files outside this public repository.

Each obsolete representation requires the following exact keys:

| Key | Meaning |
| --- | --- |
| `id`, `model_link_id` | Unique review ID and exact obsolete native conduit |
| `model_record_sha256` | `lifecycle_record_fingerprint("CONDUITS", record.tokens)` |
| `source_pipes` | Complete list of reviewed source constituent bindings |
| `complete_source_membership` | Explicit `true` assertion; false leaves a hold |
| `preserved_representations` | Disposition of every active source member |
| `basis`, `reviewer`, `date`, `decision` | Explicit rationale, reviewer, ISO date, approved/proposed |

A source constituent binding contains only `source_key`, `source_sha256`,
`expected_fingerprint` and `intent`. The first hash is the canonical row's
immutable `source_record_sha256`; the second is
`lifecycle_source_fingerprint(canonical_pipe)`, binding normalized lifecycle,
endpoints and all raw evidence. If raw attributes are present, their original
source-qualified hash is also checked. Intent is `retire` or `preserve`.
Retirements require normalized abandoned/retired/removed/decommissioned status;
preserved members require active status. Normalize utility domain values upstream.

Each preservation contains exactly `source_key`, `model_link_id`, `mode`,
`from_node`, `to_node`, `basis`, `reviewer`, `date` and `decision`:

- `existing` verifies a real native CONDUITS record and source-oriented endpoints.
  The report retains its complete native record/hash and XSECTION records so that
  a reviewer can compare diameter, length and other parameters. This does not
  assert dimensional equivalence or resolve separate parameter-update questions.
- `rebuild` declares a replacement model identity and actual source endpoints.
  It creates no hydraulic values or record. The caller must prepare and publish
  the replacement and validate its final geometry and connection independently.
- `exclude_scope` requires a separate approved, explicit scope rationale and
  `model_link_id: null`. The source remains active in the inventory. Existing
  upstream native loading, other native connections, other active source
  connections or unsupported dependencies hold this narrow peripheral-branch
  disposition. It is never a general permission to remove active pipes.

Newly orphaned native nodes need explicit `node_dispositions`. Each contains
`node_id`, `node_record_sha256`, `action`, `basis`, `reviewer`, `date`, `decision`
and optional `transfer_to_node_id`. Fingerprint the original node's exact section
and tokens with `lifecycle_record_fingerprint`. Action is `retain` or `remove`.
Only ordinary junctions can be removed. A loaded junction cannot be removed
without an explicit surviving native transfer destination. The planner never
infers catchment destinations from proximity or pipe direction. Retaining an
orphan is reported; a separate final topology policy may prohibit it.

Compatible DWF FLOW definitions preserve identical pattern references; constant
INFLOWS FLOW definitions preserve identical conversion/multiplier/pattern tokens;
RDII areas preserve identical unit hydrograph references. The original rows and
native units remain visible and quantities add with exact decimal arithmetic.
Existing destination loading participates in the merge. Different definitions,
time-series inflows, duplicate native load slots and pollutant concentrations
are held. Concentrations cannot be summed as flow. This is record conservation,
not verification of the underlying catchment assignment.

Control references, treatment/subcatchment dependencies and opaque native token
occurrences remain holds. GROUNDWATER and LID_USAGE references are conservatively
identified through literal-token matching; their grammar is not rewritten.
Unknown numeric-token coincidences can therefore require manual review. Link and
node metadata are inventoried with exact section, record index, column and hash.
For REPORT selections, remove only the retired identifier token, preserving other
selected identifiers; the inventory is not permission to drop a whole mixed row.

All groups execute together or none execute. A ready report has
`executable_intent` with `delete_link_ids`, `delete_node_ids`,
`preserved_representations`, `node_dispositions`, `load_transfers`,
`remove_link_metadata` and `remove_node_metadata`. A held report has
`executable_intent: null` and explicit reasons. Inspect `representations`,
`orphan_nodes` and `reference_inventory` before authorizing publication.

## Developer README

Use the established Python 3.12 environment and callable workflow. Import
`plan_lifecycle_retirements`, `lifecycle_record_fingerprint` and
`lifecycle_source_fingerprint` from `umdev` or `umdev.lifecycle`.

Call `plan_lifecycle_retirements(canonical, current_document,
representations=reviewed_representations, node_dispositions=reviewed_nodes,
context_sha256=private_receipt_sha256)`. The optional context digest is recorded
as supplied; the caller must actually verify external evidence file bytes before
calling. There is no implicit configuration discovery or filesystem read.

The return value is an independent JSON-compatible plan. The caller must require
`status == "ready"`, revalidate the complete current-model hash, resolve the
private authorization ledger, and pass explicit finished records to SHMM. SHMM
owns all writing, preview/written diffs and readback. Final node/link dependency,
island, directed terminal-route and solver checks are separate requirements.
Ready never means survey accuracy, hydraulic capacity or engineering acceptance.

Run `python -m pytest tests/test_lifecycle.py`. Synthetic tests cover mixed active
aggregates, status/approval/source/native guards, preserved conduit identity,
orphan decisions, exact compatible load sums, incompatible loads, source/native
connections, control/groundwater/LID/opaque references and unchanged inputs.
