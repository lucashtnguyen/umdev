# Reduced-network scope proposals

Author: Lucas HT Nguyen  
Contact: https://github.com/lucashtnguyen  
Date: 2026-10-21

## Summary

`plan_small_runs` inventories complete minor-network groups for manual trunk-model
scope review. It never deletes assets, retires lifecycle records, transfers loads
or writes SWMM. A small diameter or absence of a recorded model load does not
establish negligible real sewage. Reference-model omissions are context only.

The caller explicitly selects candidate links and protects original modeled
assets, major stations, pressure mains, loads, controls, trunks and required
connections. All other links remain retained. Non-gravity links are protected
automatically, except separately authorized lifecycle retirements. Before scope
planning, any loads orphaned by those retirements must already have reviewed
surviving destinations; a loaded nonterminal without active incidence is rejected.

Topology is assessed before whole-group size thresholds. This prevents repeatedly
removing small pieces of a large branch. Proposals distinguish peripheral trees,
complete one-attachment loops, and serial two-anchor corridors with an actual
directed alternate route. Complex connections or splitters remain held. Parallel
link identities remain distinct. Individual and joint guards reject new island
splits, disappearing protected nodes and lost previously available protected-node
routes to declared terminals. Joint conflicts hold all affected recommendations
rather than choosing a path arbitrarily. Existing islands are reported; this
helper does not repair them or certify the starting network.

Review group IDs, whole lengths/counts, attachment nodes, hydraulic references,
alternate routes and the joint retained-network result. Independently confirm
loading inventories and future tributary allocation. A graph alternate route
does not establish adequate capacity, acceptable headloss, equivalent routing or
operating performance. Explicit project exceptions remain separate approvals;
the caller must not silently turn a held group into a recommendation.

### Review footprint and complete context

The caller must supply the full current graph and include every eligible minor
link in the connected review area. A link omitted from `candidate_link_ids` is
an intentional retained boundary in this contract. Passing only links omitted
by a reference model can therefore split a larger real branch at an artificial
boundary. First run an inventory over all eligible minor links, then compare
reference edits and approve complete groups. The planner does not infer missing
GIS context or expand a caller's deliberately restricted footprint.

## Developer README

Import `plan_small_runs` from `umdev` or `umdev.scope`. It is a pure Python helper
with no filesystem, GIS licence, engine or NetworkX dependency. All dimensions
are **SI metres**, including diameters and tolerance; convert native model units
explicitly before calling. No project identities belong in this public module.

### Input contract

Pass `nodes` and `links` mappings keyed by exact nonempty IDs. Each link declares
`from_node`, `to_node` and `kind`. Gravity links use `kind="gravity"` and positive
finite `length_m` and `diameter_m`. Other kinds are retained. Additional JSON-safe
metadata is included in the input fingerprint. Self-links and unknown endpoints
are rejected. Inputs are not mutated.

Required keyword arguments are iterable ID collections:

- `candidate_link_ids`: the review footprint, not deletion authority.
- `protected_link_ids`: baseline/trunk/station/control/required-route links.
- `protected_node_ids`: control, connection and other protected junctions.
- `loaded_node_ids`: every applicable native hydraulic load/reference node.
- `terminal_node_ids`: valid receiving boundaries.
- `policy`: the explicit threshold mapping below.

Optional `reference_omission_ids` carry comparison context only. Optional
`retired_link_ids` remove separately authorized lifecycle dispositions before
review. A retirement cannot also be explicitly protected. Plain strings are
rejected where an iterable of identities is required. The caller is responsible
for complete load/control inventories and for separately reviewing any transfer.

Required policy keys:

| Key | Contract |
| --- | --- |
| `maximum_group_length_m` | Positive finite total whole-group length. |
| `maximum_group_pipe_count` | Positive integer, excluding Boolean values. |
| `maximum_minor_diameter_m` | Positive finite largest group diameter. |
| `dimensional_tolerance_m` | Nonnegative finite dimension-comparison tolerance. |
| `allow_isolated_component_omission` | Explicit Boolean; normally false. |

No utility-specific threshold is implicit. A caller may consider, for example,
a 365.76 m whole-group length, eight pipes and 0.3048 m largest diameter, but
these are provisional scope policies requiring review, not engineering standards.

### Optional short-seed connector branches

`policy.short_seed_connector_branches` is disabled when omitted or when its
`enabled` value is false. The ordinary whole-group rules remain unchanged by
default. An enabled rule requires all four SI values below; there are no implicit
utility thresholds. Unknown keys, non-Boolean `enabled`, invalid dimensions and
nonfinite values are rejected. Supplied values are validated even when disabled.

| Nested key | Contract |
| --- | --- |
| `enabled` | Explicit Boolean. |
| `seed_diameter_threshold_m` | Positive finite threshold separating larger seed pipes from small connectors. |
| `minimum_seed_run_length_m` | Positive finite minimum continuous larger-pipe length to retain under this optional rule. |
| `diameter_tolerance_m` | Nonnegative finite tolerance, smaller than the diameter threshold. |
| `length_tolerance_m` | Nonnegative finite tolerance, smaller than the minimum seed length. |

A pipe is a larger seed only when its diameter exceeds the configured threshold
plus its diameter tolerance. A seed reach is short only when its summed length
plus the length tolerance is strictly less than the minimum length. Equality
and the tolerance band remain held. For example, a caller could explicitly use
0.2032 m and 152.4 m (8 inches and 500 feet); these are scope choices requiring
review, not hydraulic design criteria.

Continuous reaches are traced in the directed **seed-only** graph. One incoming
and one outgoing larger seed continue the same reach even when a smaller lateral
joins or the larger diameter changes. Seed confluences and divergences separate
reaches. Small intervening pipes also separate reaches; separate headwaters and
tributaries are never added together to reach the length threshold. Every seed
edge is accounted once.

The exception applies only to complete peripheral trees with one retained
attachment. Every separate seed reach must be short and have an actual directed
path from its downstream end to that attachment containing at least one small
connector. A large pipe directly reaching the attachment, a small pipe elsewhere
in the tree, loops and multiple attachments do not qualify. Multiple consecutive
downstream small connectors are allowed.

An eligible branch may exceed the ordinary whole-group pipe count and total
length. Only those two holds may be waived. All diameter, load, pressure,
station/control, protected-node/link, individual-route and joint-network guards
remain binding. The caller must still provide the full graph and complete
protections; this option cannot discover undocumented stations or real tributary
loads. Its recommendation omits modeled detail, not physical assets or loads.

Enabled groups include `short_seed_connector_review`, with exact seed runs and
downstream connector paths, protected-guard results, original hold reasons,
explicitly overridden count/length reasons, final holds and joint-guard result.
Separate rule-configuration and source-graph fingerprints complement the full
input and final proposal fingerprints. A failed non-size guard retains the
original size holds too. No hold disappears without its explicit waiver receipt.

### Outputs and approval binding

The JSON-serializable result contains `groups`, `recommended_omit_link_ids`,
`held_link_ids`, `protected_candidate_link_ids`, `separately_retired_link_ids`,
`load_protection_node_ids` and `joint_retained_network_guard`. `status` explicitly
states review-only intent.

Each group records kind, exact link/internal/attachment IDs, total length, largest
diameter, pipe count, hold reasons, reference omissions, alternate directed link
path, individual retained-network guard and tributary-load review targets.
Multiple attachment targets are not instructions to allocate all future loading
to one node. The absence of recorded loads is explicitly distinguished from zero
real sewage.

`input_fingerprint` binds the sorted finite JSON inputs, policy and declared
protections. Each `proposal_id` binds that fingerprint and its complete final
group record, including joint-guard holds. Callers must bind exact reviewed
proposal IDs to current input fingerprints, revalidate before SHMM writes, and
inspect SHMM preview and written-model diffs. This helper does not consume or
execute approval records.

### Verification

Run `python -m pytest tests/test_scope.py tests/test_short_seed_scope.py`.
Synthetic tests cover complete-tree
thresholds, load/control/baseline protection, pendant and true parallel loops,
required directed routes, two-anchor splitters, joint-removal failure, explicit
retirement separation, loaded-orphan rejection, finite SI dimensions, exact-ID
validation and reproducible input/proposal fingerprints. The optional rule also
tests exact length/diameter boundaries, tolerances, continuous diameter changes,
small side tributaries, separate headwaters, seed branching, actual directed
connector paths, cycles/parallel paths, unchanged hard protections and disabled
compatibility. No project data or actual scope decisions are embedded in tests.
