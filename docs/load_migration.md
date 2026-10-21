# Explicit native load migration

Author: Lucas HT Nguyen  
Contact: [GitHub](https://github.com/lucashtnguyen)  
Date: 2026-10-21

## Summary

`plan_load_migration` prepares reviewed relocations of node loads from one exact
native SWMM document to another. It writes no model or source files. Retiring a
pipe does not necessarily retire a node or justify moving its load: a node can
remain connected to a replacement pipe. Every actual load relocation therefore
needs an explicit reviewed source-node allocation. Catchment destinations and
split weights are supplied engineering decisions, never nearest-node inference.

The planner supports water-quantity DWF, external INFLOWS and RDII. Each source
mapping binds exact source/target node records, all native load rows and their
referenced definition closure. A destination can be an existing node or an
explicitly rebuilt node already present in the proposed physical target document.
No elevations, pipes, junctions, unit conversions or target locations are inferred.

### Conservation and native references

All allocation weights are positive decimal strings and must sum exactly to one.
Finite-decimal coefficients use exact rational arithmetic. The audit checks both
the source split totals and the coefficients independently parsed from each merged
output record. Existing destination loading participates; unrelated target load
records retain their exact tokens. Native FLOW_UNITS must be identical.

- DWF FLOW allocations scale the average flow while retaining all pattern IDs.
  Compatible records merge only when their complete effective pattern references
  agree. Pattern definitions are not normalized or rewritten.
- External FLOW allocations scale both the time-series multiplier and baseline.
  A constant contribution can join a named-series contribution with compatible
  baseline pattern and unit-factor metadata. Its unused empty-series scale factor
  is never added onto the named hydrograph. Different named series or incompatible
  patterns remain held; no resampling or synthetic combined series is generated.
- RDII allocations scale native sewered area and retain the exact unit hydrograph.
  Compatible areas add only for the same UH. Native area is acres under US flow
  units and hectares under SI flow units.

The inflow relation is `q(t) = cf * [sf * TS(t) + baseline * pattern(t)]`.
Both varying and baseline coefficients therefore receive each split weight.
For FLOW the engine derives its conversion from FLOW_UNITS; the serialized native
unit-factor token is retained conservatively. These contracts follow the official
[EPA SWMM5.2.4 inflow implementation](https://github.com/USEPA/Stormwater-Management-Model/blob/v5.2.4/src/solver/inflow.c)
and [RDII implementation](https://github.com/USEPA/Stormwater-Management-Model/blob/v5.2.4/src/solver/rdii.c).

Source and existing destination definitions are checked, including PATTERNS,
TIMESERIES, HYDROGRAPHS and referenced RAINGAGES. Complete continuation records
must retain identical tokens. A missing target definition produces an explicitly
nonexecutable copy proposal and a hold. Prepare the target catalog through SHMM,
review it, update its exact fingerprints, and replan. A changed target definition
with the same name is a collision, not permission to overwrite it.

An identical external FILE definition preserves its literal path and produces
`unverified_external_files`. The pure helper does not open, relocate, hash or
certify external file contents. Quantity-record readiness is not a claim that
forcing files are accessible or that a model can run.

### Holds and review responsibilities

Every loaded node marked for retirement needs a mapping. Explicit mappings may
also move loads from retained nodes; they clear only the source load rows, not
the physical node. A newly added target load at an unmapped retired node is also
held. Source/target node and load fingerprints prevent repeated or stale migrations.
Mappings that form source/destination chains or target a retired node are held.

Pollutant concentrations and pollutant mass-rate records are unsupported. They
are not treated as water flows, added or split using quantity weights. A source
or receiving node with quality coupling therefore holds the plan. Duplicate
native load slots also hold: SWMM setters overwrite such slots, so summing them
would invent a different load. Different DWF patterns, RDII UHs or external
hydrographs cannot be flattened into one native slot.

A load allocation removes only the load-migration veto. It does not approve
remapping controls, subcatchment runoff, groundwater, LID drains or treatment.
Such references at retired nodes remain explicit dependency holds. The caller
must separately verify source lifecycle/active connections, physical retirement,
remaining topology, directed terminal routes and the written model.

Inspect `allocations`, source `conservation`, rendered `group_conservation`,
source/target `definitions`, `definition_copy_intents` and `held`. A ready plan
has complete executable section-record sets; a held plan exposes proposed records
for review but no executable intent. This is quantity-record conservation, not
hydrologic calibration, catchment verification, simulation mass balance, capacity
assessment or engineering acceptance.

Keep real utility identities, measured values, review decisions and hashes in
private project configuration. Historical loading migration can be an offline
archive plan; do not activate historical loads in addition to current synthetic
forcing simply because an archival record plan is ready.

## Developer README

Use Python3.12 and import `plan_load_migration` and `node_load_fingerprint` from
`umdev` or `umdev.load_migration`. The helper opens no files and discovers no
configuration. Pass exact immutable Hymo `SWMMDocument` objects.

The callable contract is
`plan_load_migration(source_document, target_document, retired_node_ids=(),
mappings=reviewed_mappings, context_sha256=private_evidence_sha256)`.
The optional context digest is recorded; the caller verifies actual evidence
files and authorizations before invoking the helper.

Each mapping has exactly:

| Field | Meaning |
| --- | --- |
| `id`, `source_node_id` | Unique review ID and exact source node |
| `source_node_record_sha256` | `lifecycle_record_fingerprint(section, tokens)` |
| `source_load_fingerprint` | `node_load_fingerprint(source_document, source_id)` |
| `destinations` | Nonempty explicit allocation list |
| `basis`, `reviewer`, `date`, `decision` | Rationale, reviewer, ISO date, approved/proposed |

Each destination contains only `node_id`, `node_record_sha256`, `load_fingerprint`
and `weight`. Hash the exact target node and use
`node_load_fingerprint(target_document, destination_id)`. Weight must be a decimal
string, including a literal `"1"` for a whole-node transfer. No float coercion or
automatic renormalization occurs. Fingerprints include referenced definition
records and node tokens; changing either requires a new review binding.

The result includes `source_model_sha256`, `target_model_sha256`, review/context
digests, native units and complete audit records. `status == "ready"` yields
`executable_intent` with `sections`, `clear_source_node_ids` and
`affected_target_node_ids`. Top-level `sections` aliases these complete DWF,
INFLOWS and RDII token lists. Held plans have both `sections` and
`executable_intent` set to null; `proposed_sections` remains reviewable.

The caller must recheck the exact target model hash and private ledger, then ask
SHMM to publish the approved loading section records. SHMM owns all INP writing,
preview/written-model diffs and readback. Verify source clearing, destination sums,
all retained references and unchanged unrelated records on the written model.

Run `python -m pytest tests/test_load_migration.py`. Synthetic tests cover source
and target guards; retained and rebuilt nodes; split and merged hydrograph terms;
precise decimal conservation; native units; named/default coefficients; definition
closure, collisions and external-file warnings; unsupported quality and controls;
duplicate slots and unchanged inputs. Run the complete suite before committing.
