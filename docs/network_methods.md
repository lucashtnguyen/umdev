# Finished-network inference and reviewed simplification

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-16
- Manually maintained callable contract. Project data, decisions and policy stay private.

## Summary

These opt-in helpers prepare an already reconciled, finished network before SHMM
writes a model. They are separate from `prepare_project`/`run_project`: enabling a
project configuration key does not silently interpolate elevations or merge pipes.
The caller must map its final accepted network, retain the returned evidence, and
obtain a review decision for each merge. The helpers write no files and invoke no
hydraulic engine.

### Inputs and units

Both functions accept `nodes` and `links`, mappings from string identifiers to
JSON-compatible records. Input maps are deep-copied and never mutated.

| Record | Fields |
| --- | --- |
| Node | `invert`: finite absolute floor elevation or `None`; `kind`: `junction`, `storage`, `outfall` or other explicit type; absent kind means ordinary junction for this API |
| Link | `from_node`, `to_node`, `kind`; `kind='gravity'` is required for inference or simplification; other families are topology boundaries |
| Gravity link | positive finite `length` (hydraulic, not map chord length); optional nonnegative `inlet_offset` and `outlet_offset`, default zero |
| Merge candidate | known equal `diameter` and `material`; supplied `shape`, `roughness`, `manning_n`, `barrels`, `cross_section` must also match |
| Optional geometry | `vertices` are interior XY points in directed order; middle node `coordinates=[x,y]` is required if either merged link supplies vertices |
| Protection | truthy `protected`, `explicit_drop`, `has_inflow`, `inflows`, `inflow`, `controls`, `control`, `boundary`, `keep_explicit`, `has_control`, `has_loading`, `pressure`, `sealed`, `wet_well` or `is_pump_station` |

All elevations, offsets, hydraulic lengths and dimensional section fields must
already share one length unit and vertical datum. The `length_unit` argument is an
audit label, for example `ft` or `m`; it performs no conversion. Offsets are DEPTH
offsets above node floors. A caller adapting an ELEVATION-offset model must first
convert absolute pipe-end elevations to depth offsets. Do not mix an unknown datum
with a known datum simply to make a reach complete.

Only explicitly mapped information is checked. Callers must mark additional
loading, controls, station functionality or project-specific constraints as
protected. Sparse mappings must not conceal a pump, branch, structure role, local
loss or other material hydraulic difference.

### Missing invert inference

Call `infer_missing_inverts(nodes, links, extrapolation_slope=None,
anchor_tolerance=1e-8, length_unit='model')`.

A missing ordinary junction floor is interpolated only along an unbranched,
directed gravity chain between two original known anchors. The known upstream
pipe-end elevation is its floor plus the applicable outgoing offset; the downstream
anchor uses its floor plus the incoming offset. The missing floor is:

`z = z_up + (z_down - z_up) * L_up / (L_up + L_down)`.

Distances are cumulative accepted hydraulic lengths. Unknown intermediate nodes
must have zero offsets within tolerance and cannot be protected. No inferred
value becomes a new anchor during the same pass. Known measured/reviewed floor
values and their provenance are preserved exactly.

Adverse anchor grades are held; flat anchors are permitted with an explicit
`flat_anchor_grade` warning. Branches, cycles, pumps, unknown link families,
protected structures, explicit drops and offset conflicts are not crossed. This
conservative numerical helper does not resolve branch elevations automatically.

Setting an explicit positive `extrapolation_slope` permits a chain with exactly
one known anchor and a true dead end to use `fall = slope * length`. The sign is
negative downstream of an upstream anchor and positive upstream of a downstream
anchor. This is a provisional engineering assumption, not observed data. Both
`assumed_invert` and `provisional_extrapolation` flags identify it. A wholly
unanchored network remains unresolved. A branch never counts as a dead end.

The returned dictionary contains `nodes`, `assignments`, `unresolved`,
`length_unit`, and `preservation`. Each assignment records the method, inferred
value, flags, upstream/downstream anchor IDs, original floor and offset, absolute
anchor elevation, cumulative distance and traversed link IDs. The preservation
record includes all original known floors and an exact comparison result. The
same assignment is appended to the inferred node's `assumptions`. Unresolved
records identify the obstruction and traversed paths where available.

### Short-pipe simplification

Call `simplify_short_pipes(nodes, links, mode='explicit_all', threshold=20.0,
keep_link_ids=(), selected_link_ids=(), protected_node_ids=(),
approved_merge_ids=(), drop_tolerance=1e-6, length_unit='model')`.

The threshold uses the common input unit. For a 20-ft policy, pass `threshold=20`
with feet data or `threshold=6.096` with metre data. The comparison is strictly
less than the threshold.

- `explicit_all` keeps every pipe.
- `selective` considers only `selected_link_ids` for absorption.
- `simplify_eligible` considers all short eligible gravity pipes.
- `keep_link_ids` protect a pipe from both absorption and absorbing a neighbor.
- `protected_node_ids` augment the record-level protection flags.

Only serial pairs with exactly one incoming and one outgoing link at the middle
ordinary junction qualify. Degree checks include every link family. Pressure mains
remain explicit. A pump, branch, protected node/link, changed section/material/
roughness, unknown floor, adverse intermediate profile, nonzero middle offset,
local loss, flow restriction, seepage or flap gate prevents simplification.

Candidates are processed deterministically by link ID, preferring absorption into
the downstream neighbor. Each pass proposes disjoint pairs. The neighbor retains
its ID; the short source ID remains in ordered `source_link_ids`. The hydraulic
length is the sum of both original lengths; outer endpoint IDs, offsets and
pipe-end elevations are preserved. The removed junction remains in the audit
snapshot and path lineage, and its XY becomes a map vertex when geometry is
provided. The local profile and intermediate storage are approximated by the
longer conduit, so every merge requires engineering review.

`merges` contains `proposed` records by default; the returned network is unchanged.
Review their profiles, structures, load/control references and hydraulic purpose.
Then call the function with only the approved exact `merge_id` values. Approval IDs
include a hash of all involved source-node and source-link records, so an approval
cannot silently apply after evidence changes. Unknown/stale approvals raise an
error. Each approved proposal becomes `applied`; other proposals remain explicit.
The caller can inspect a new pass after applying a reviewed pass.

Each record includes source snapshots, ordered source IDs, individual and total
lengths, removed node, outer inverts/offsets, the evidence hash, flags and a
`model_note`. Applied links aggregate these records under `assumptions`. These
records are the input to a private decision ledger, export QA sheet, profile
review and SHMM INP comments. They are not themselves an authorization ledger.
SHMM remains responsible for writing supported edits and comparing the written
model with the preview; the caller must remove every dependent model row/reference
when eliminating a junction or link.

### QA/QC and limitations

Synthetic pytest cases check hand calculations, preservation, both extrapolation
signs, feet/metre equivalence, offset-aware anchors, multiple missing nodes,
branches/cycles/pumps/controls/drops, unknown profiles, explicit keep/selection,
strict thresholds, stale approvals, exact total length and outer profile,
geometry retention and malformed quantities. Result determinism is checked with
reversed input order. No hydraulic simulation or model file mutation is performed.

A reviewer must inspect inferred grades, datum consistency, interpolation bounds,
extrapolation extent, short-pipe merge profiles, network connectivity, loading and
station representation. These helpers do not choose engineering slopes, connect
islands, assign outfalls, alter pump station plumbing, infer unknown pressure-main
profiles, guarantee numerical stability, or establish a calibrated engineering
model. No unresolved stubs are introduced.

## Developer README

The public callables are exported from `umdev` and implemented in
`src/umdev/network_methods.py`. Run the targeted checks with
`.venv/Scripts/python.exe -m pytest tests/test_network_methods.py -q` and the full
suite with `.venv/Scripts/python.exe -m pytest -q` in an installed Python 3.12
repository environment. Preserve the existing project orchestrator for source
normalization, scope, approved decisions and SHMM handoff; map its final network
to these helpers explicitly. Export every assumption and unresolved record rather
than discarding them when preparing writer instructions.
