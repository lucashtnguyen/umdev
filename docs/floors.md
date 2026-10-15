# Structure-first floors and local modeled pipe ends

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-15
- Manually maintained; project evidence and policies remain private.

## Summary

The default floor rule for new configurations is:

1. Use an explicit reviewed floor decision when supplied.
2. Use the lowest usable pipe-invert observation recorded at the structure.
3. If structure records cannot supply a floor, use the lowest local incoming or
   outgoing pipe-end elevation retained in the model plan.

For an incoming pipe, the local elevation is its downstream end. For an outgoing
pipe, it is its upstream end. The remote end of a conduit and unrelated/unmodeled
GIS branches do not describe the floor at this structure. The minimum is a
documented hydraulic-model floor convention, not a surveyed physical sump bottom.

Structure observations are collected from configured description fields and
normalized port tables, independently of port-to-pipe matching. A usable invert
can describe the structure floor even when its diameter/compass cannot identify
one modeled pipe. Ambiguous or qualified multi-value text, conflicting units,
unresolved vertical basis and explicitly inactive records remain unusable.
All comparisons occur after conversion to canonical metres and the configured
target vertical basis. Zero and negative elevations can be valid measurements.

The configuration floor_methods order is reviewed_override, lowest_structure_port,
lowest_applied_pipe_end. If floor_methods is absent, this is the new default;
an explicitly empty list is invalid. Existing explicit legacy methods keep their
documented behavior. Projects adopting the new rule should declare this order
so their policy is visible in retained configuration.

### Choosing pipe-end evidence

ports.assignment.precedence defines enabled evidence families in ranking order.
Omitting pipe_numeric disables raw pipe-table upstream/downstream invert values
for selection, fallback and conflict checking. The raw fields/provenance remain
available for inspection. Structure tables and approved structure-based end
corrections can therefore govern without accidental reuse of a pipe GIS number.

This pipe-end selection policy is independent of structure-floor collection:
a structure record can remain usable for a floor without being an enabled/matched
pipe-end assignment. Lower-ranked enabled sources may resolve missing preferred
values; disabled sources cannot silently become a last-resort fallback.

### Which model pipes can donate a fallback elevation?

Baseline CONDUITS contribute their actual local end elevations. DEPTH convention
uses local node invert plus local conduit offset; ELEVATION uses the explicit
absolute offset value. Typed junction/storage/outfall/divider node references are
read, but pumps, weirs and other regulator links supply no invented pipe invert.
The baseline fingerprint, conduit identity, node identity and end are retained.
Unsupported or missing baseline offset syntax fails explicitly.

New source pipes contribute only when they survive preparation and compilation
into model instructions. prepare_project repeatedly prepares and compiles without
writing a model, removes rejected donors and retains their original hold reasons.
Removed pipes cannot revive as hidden non-donors. The process stops when donor keys
equal accepted source-pipe instruction keys; the finite set only shrinks.
Baseline ends are restored when a proposed existing-pipe replacement is rejected.

An apparent floor from an unsupported, unselected, held or otherwise unwritable
pipe therefore cannot release another model object. floor_donor_audit records
iterations, accepted keys, rejection reasons and baseline context counts.
run_project saves this as floor_donor_audit.json alongside the ordinary model diffs.

### Existing model floors and manual QA

Existing model floors remain preserved under the current dependent-edit contract.
prepared nodes retain proposed_floor_assignment separately. A material difference
produces existing_floor_differs_from_policy with the baseline/proposed values,
residual and source basis. Changing an established floor requires preserving every
attached pipe-end elevation and updating depths/offsets together; this feature does
not silently perform that separate reconciliation.

Inspect the source records contributing to the minimum, datum qualifications,
local pipe-end membership, excluded donor reasons, proposed baseline differences
and resulting rim/depth/offset checks. An accepted preparation plan does not prove
a candidate was written or that hydraulic simulation/calibration passed.

## Developer README

prepare_network and prepare_network_from_dict accept optional floor_donor_pipe_keys
and model_pipe_ends. The donor list must be a subset of selected_pipe_keys.
Standalone preparation is provisional: its caller must supply a verified donor
set or use prepare_project for writer-aware convergence.

umdev.models.baseline_pipe_end_parameters returns model-node IDs mapped to
finite local CONDUITS end records with basis baseline_model and SHA-256.
Do not pass arbitrary GIS endpoint columns into this context.
collect_structure_ports provides the common read-only structure observation
collection used by floor derivation and pipe attribution.

Run python -m pytest. Synthetic cases cover unmatched/multiple structure records,
local incoming/outgoing end selection, disabled numeric evidence, unknown datums,
valid zero/negative values, stale/invalid baseline context, preserved floors,
writer rejections and multi-pass donor removal. Public examples contain no utility
asset IDs, locations or project coefficients.
