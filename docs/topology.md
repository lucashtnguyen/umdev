# Topology and island review

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-14
- Status: Implemented read-only diagnosis; manually maintained.

## Summary

Topology diagnosis explains whether candidate objects connect to an established
model network, form a new independent network, or remain an unsupported island.
Separate baseline components can represent legitimate independent service basins.
Their separation alone is not a newly introduced model defect.

Two questions are reported independently:

1. Can a node follow directed model links to any existing baseline node?
2. Can it follow directed model links to an explicitly declared outfall or terminal?

An existing baseline node reaches itself with zero links. That is an identity
anchor, not proof of an outfall route. A new node connected backwards to a baseline
network can share its weak component while failing both downstream-route tests.

All declared nodes participate, including junctions, storage, dividers and outfalls
with no incident links. Conduits, pumps, weirs, orifices and outlets participate as
nominal directed graph edges. A graph route through a pump does not establish pump
operation, head, capacity, control compatibility, grade, loading or hydraulic
acceptance. Reverse hydraulic flow under surcharge is outside this graph check.

### Review outputs

The diagnosis returns:

- Candidate and baseline weak components, counts, node/link membership and link families.
- Component classification: inherited baseline component, connected extension,
  merge of baseline components, new terminal network, unsupported island or isolated node.
- Per-node directed reachability to baseline and terminal anchors, each with a
  deterministic witness anchor, next node/link and number of edges.
- Baseline component splits, omitted baseline node/link IDs and changed endpoints.
- Dangling node references and missing terminal declarations as explicit findings.
- Held or excluded source connector candidates that touch actual modeled IDs,
  with source keys, disposition, lifecycle and hold/exclusion reasons retained.

Component IDs are deterministic for identical input graphs regardless of record
order. They are not persistent asset identifiers across different network revisions.
Geometry is not used: no nearest-neighbor association or snapping occurs.

The standard candidate runner retains `topology.json` plus separate
`topology_components.csv`, `topology_nodes.csv` and
`topology_connector_candidates.csv` review tables. The completion summary includes
component and route counts; these do not replace profile or engine QA.

### Source connector review

A source connector can bridge different candidate components, touch an unmodeled
endpoint, or supply a missing directed route within an existing weak component.
The report keeps that possibility separate from the actual graph. Source links
never become model edges merely because they could connect an island.

Endpoint IDs must already be crosswalked explicitly into model identity space.
The standard runner resolves unselected connector endpoints with the same frozen
crosswalk and configured identity-method order used for application. An asset-ID
fallback disabled by project policy is not silently re-enabled for diagnostics.
Unresolved endpoints remain identity findings retaining original source IDs,
disposition and hold reasons. Applied edges use their frozen handoff identities.
A source record with no modeled endpoint is counted separately; the diagnostic
does not invent a direct connection across a chain of unmodeled source nodes.
Already represented link/end identities are excluded from addition candidates.
A source ID already used with different model endpoints is explicitly flagged
as an identity conflict; it is not advertised as a usable direct-route addition.
A missing source endpoint remains an identity finding.

Before applying any connector, inspect identity, direction, lifecycle, existing
review decisions, geographic/scenario scope, parameter completeness and model-writer
capabilities. A deliberately excluded branch must not be silently reintroduced.
Expanding a network upstream of existing lumped loads can require explicit load
redistribution; graph connectivity does not make that engineering decision.

### Manual QA checklist

- Confirm whether the baseline's independent basins and terminals are intentional.
- Inspect introduced islands and every isolated new node.
- Inspect new nodes attached weakly but lacking a directed terminal route.
- Check changes that merge or split established components.
- Check source connector candidates and their original reasons for exclusion/hold.
- Resolve required engineering parameters and load/control effects before editing.
- Re-run topology and hydraulic/profile QA after an authorized correction.

## Developer README

Import `diagnose_topology` or `diagnose_swmm_topology` from `umdev.topology`.
Both are read-only callable APIs; no files are written by the module.

`diagnose_topology(nodes, links, baseline_nodes=..., baseline_links=...,
source_connectors=..., terminal_node_ids=..., identity_mode=...)` accepts canonical
mapping sequences. Nodes require `asset_id`; links require unique `asset_id`,
`from_node` and `to_node`. Link `section` or `family` is retained for review.
Every supplied link is an actual graph edge; pass unapplied requests separately
as `source_connectors`. A terminal is declared with `is_terminal=True`, node
`section="OUTFALLS"`, or an explicit `terminal_node_ids` entry.

Exact identity is the default. `identity_mode="ascii_case_insensitive"` implements
SWMM comparison while retaining display spelling and leading zeros. Duplicate or
missing actual IDs fail validation. Dangling references are included as diagnostic
vertices with `declared=False`, never as invented accepted node objects.

`diagnose_swmm_topology(candidate_document, baseline_document,
source_connectors=...)` reads immutable hymo documents across supported node/link
families, selects ASCII identity semantics and includes document hashes in its
report. It does not read a GIS database or resolve a crosswalk itself.

The result is JSON-serializable. Callers can retain the full report and export
`components`, `nodes`, `source_connector_candidates` and `findings` as separate
review tables through the ordinary reporting helpers. Topology diagnosis does not
change the edit plan or publication policy.

Graph traversal is iterative. Directed witnesses store next-hop pointers rather
than complete paths for every node, avoiding quadratic output for long networks.
No numerical units or spatial distance tolerances are involved.

Synthetic tests cover independent baseline basins, directed versus weak attachment,
isolated nodes, cycles/parallel links, baseline splits/merges, dangling references,
case semantics, held/excluded connector review, all SWMM node/link families,
non-mutation and deterministic record-order behavior. Run
`python -m pytest tests/test_topology.py` in the project Python 3.12 environment.
