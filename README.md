# umdev

- Author: Lucas HT Nguyen
- Last modified by: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-16
- Status: Implemented 0.1.0 callable runtime with explicit capability limits.

## Summary

umdev prepares utility GIS and engineering evidence for hydraulic model development.
It selects usable evidence, records assignments and uncertainty, assembles coherent
network parameters, and checks the resulting model. Reusable private project
configuration maps source fields, tables, units, domains, lifecycle values, port
descriptions, datum rules and engineering policies.

| Repository | Responsibility |
| --- | --- |
| umdev | Read-only source normalization, evidence reconciliation, parameter policies, scenario/scope selection and engineering QA |
| hymo | Read and interpret model documents |
| SHMM | Validate instructions, preserve other content, publish separate candidates and produce manual model diffs |

### Implemented workflow

The Python 3.12 runtime resolves JSON overlays, reads exported JSON packets or CSV,
normalizes lengths/elevations/map XY to metres, and prepares nodes/pipes using full
network attribution context. Port assignment supports direct references, ranked
evidence, compass hints and explicit provisional role/diameter fallback. Competing
assignments or missing mandatory values remain holds.

The inline parser recognizes a feet mark on an elevation and an omitted diameter
suffix only when that diameter follows an explicit parenthesized compass direction.
The configured observation units supply an omitted suffix; stated units must agree.
For example, an elevation followed by `(SE), 12` can use the configured diameter
unit, while a bare pair of numbers remains ambiguous and `12'` is not an accepted
diameter suffix. Original source text and parsing/unit provenance remain traceable.
The opt-in `qualified_elevation_direction_diameter_v1` parser additionally accepts
standalone trailing `OUT`, `IN` and `DROP` tokens. It preserves the exact raw text
and qualifier spans; explicit IN/OUT takes precedence over the configured field
role. DROP remains physical qualification metadata, not permission to choose the
lower of two pipe-end observations. Conflicting same-end drop measurements stay
held unless an explicit observation-to-pipe reference or reviewed endpoint value
governs the assignment. `ports.assignment.observation_pipe_references` maps an
exact collected observation key to `{pipe_key, basis}`. Invalid remarks, caps,
multiple scalar measurements and contradictory IN/OUT qualifiers remain held.
The original strict parser is unchanged. Review all new syntax/role decisions,
datum assumptions and drop profiles before applying prepared values.

### Reviewed observation holds

A suspect source observation can be excluded without rewriting its raw text or
parsed value. This is an opt-in, exact-record review decision, not a general
outlier correction. Configure `ports.assignment.observation_holds` as a mapping
from the exact collected observation key to `expected_fingerprint`, `reason`,
`reviewer` and ISO `date`. An empty or omitted mapping leaves selection unchanged.
Keep real utility decisions and their configuration outside this public repo.

Collect observations with the hold configuration omitted, then call public
`observation_fingerprint(port)` on the reviewed record. Its canonical SHA256 binds
the entire collected observation, including original raw text, parsed values,
parser/unit/datum metadata, configured attribution and immutable source-record
SHA256. Inline observations now expose their source key, field and source-row
hash. Normalized port rows already carry their source-row hash. Manually built
observations require that explicit source binding before they can be held.

A matching review adds `reviewed_observation_hold` and separate `_reviewed_hold`
evidence to a copied observation. Raw text and numeric values remain unchanged.
Held observations are excluded from both pipe-end resolution and lowest-structure-
invert floor selection. A `reviewed_observation_hold` finding retains the full
record and reason for manual QA. Other eligible measurements can still govern;
no replacement elevation is invented. Floors preserved from an existing model
remain governed by their existing floor policy.

Missing or duplicated observation keys, altered source rows, changed parsing or
attribution, malformed review entries and stale fingerprints fail closed before
preparation can publish a model. A renewed evidence review must issue a new
fingerprint. This deliberately blocks stale decisions even when an unrelated
field on the same source row changes. Automated tests use fictional records to
check selection/floor protection, raw-value preservation, no input mutation,
configuration validation and stale/missing/duplicate-source rejection.

Configured floor/rim sources, individual pipe-end inverts, lengths, diameters and
material-to-Manning-n policies produce finished parameters. Depth is rim minus floor.
DEPTH offsets are pipe-end invert minus node floor; ELEVATION offsets are absolute
pipe-end inverts. Grade is upstream minus downstream pipe invert, divided by length.
Retained geometry Z/M ordinates never silently become invert elevations.

New configurations default to a reviewed floor override, then the lowest usable
structure pipe-invert record, then the lowest local incoming/outgoing pipe end in
the accepted model plan. Structure minima do not require a unique outgoing pipe.
Pipe-end fallback excludes unselected/held/unwritable source pipes through a
read-only prepare/compile convergence check. Existing floors remain preserved;
proposed differences are reported for dependency reconciliation. See
[structure-first floor policy](docs/floors.md).

Evidence families omitted from ports.assignment.precedence are disabled. A project
can exclude pipe_numeric to retain raw GIS endpoint attributes without using them
for assignments, fallbacks or conflict checks.


The SWMM adapter hands finished instructions to SHMM, which creates a preview diff
before publication and an independent written-model diff after actual file readback.
umdev verifies prepared floors, reconstructed rims, pipe-end elevations, dimensions
and grades, then reports profile and connectivity findings.

### Queued decisions and read-only preparation

Keep one external JSON ledger under `review.ledger_path` for approved parameter
interpretations, guarded existing-model edits and non-executable review records.
It records who authorized each decision, why, scenario, evidence fingerprints and
baseline binding where required. Proposed, rejected, archived and other-scenario
entries do not execute. Active stale approvals fail before model publication. An active parameter approval
also fails if the configured field policy or preserved baseline prevents its selection.
The ledger cannot be combined with nonempty legacy override or finished-change
inputs. Existing callers can continue using those legacy inputs separately.
See [manual decisions](docs/decisions.md) for supported fields and the full contract.

Call `umdev.prepare_project(config_path)` to inspect a queued plan without creating
files, a preview, a candidate or engine results. The returned bundle includes
`canonical`, `prepared`, `membership`, `handoff`, `requested_holds`, and optional
`decision_ledger`/`decision_ledger_audit`; it also retains configuration and immutable
baseline objects for further orchestration. Membership `applied` and handoff
`applied_source_keys` retain their compiler vocabulary: during preparation they
mean accepted into the planned instructions, not written or hydraulically accepted.
Only `run_project` performs preview, writing and independent readback verification.

### Finished-network elevation inference and reviewed short pipes

Opt-in `infer_missing_inverts` and `simplify_short_pipes` callables prepare a
finished, accepted network before SHMM writes it. Interpolation uses cumulative
hydraulic lengths between original known anchors; extrapolation requires an
explicit positive slope and is flagged. Known floors remain unchanged. Ambiguous
branches, adverse anchors, pumps, protected structures and explicit drops are held.

Short gravity reaches can remain explicit, be selectively considered, or be
considered below a configured length threshold. A 20-ft policy is `20` for feet
inputs or `6.096` for metre inputs. Pumps, controls, inflows, branches, drops,
pressure mains and section/material changes are protected. Every merge is proposed
for review; only evidence-bound individual approvals change the returned network.
Total hydraulic length, outer inverts/offsets, source IDs, removed-node snapshots
and model-note text remain in the returned audit. The caller carries that audit
to its private decision ledger, QA export and SHMM comments/diffs.

These helpers do not run automatically in the existing project pipeline and write
no model files. See [input schema, methods and limitations](docs/network_methods.md).

### Outputs and manual QA

Keep project sources, model files, actual IDs, configuration, crosswalks, reviewed
decisions and outputs in private workspaces. Public tests/examples are synthetic.

A new run directory contains:

- `effective_config.json`, `configuration_origins.json`, `canonical.json`:
  resolved policy, declaration origins, normalized observations, raw values and hashes.
- `prepared.json`, `nodes.csv`, `pipes.csv`, `findings.csv`, `membership.csv`:
  assignments, assumptions, references and requested dispositions/holds.
- `handoff.json`: frozen target-unit instructions, evidence, scenario and fingerprints.
- `decision_ledger.json` and `decision_ledger_audit.json`, when a ledger is configured:
  the complete frozen ledger and each entry's applicability/execution-plan status.
- `preview_diff.json/.csv/.html` and `written_model_diff.json/.csv/.html`:
  requests/no-ops, observed/missing/unexpected effects and exact preservation checks.
- `candidate.inp`, `application_audit.json`, `profile_verification.json`:
  separate written model, SHMM audit and prepared-to-written profile checks.
- `topology.json`, `topology_components.csv`, `topology_nodes.csv` and
  `topology_connector_candidates.csv`: inherited basins, introduced islands,
  directed baseline/outfall routes and unapplied exact-ID connector evidence.
- `floor_donor_audit.json` when applied-end fallback is enabled: accepted donor
  identities, excluded-pipe reasons and convergence evidence.
- `candidate_qa.json/.csv`, `review.html` and, after successful integrity checks,
  `completion.json`. An explicitly enabled engine check adds separate artifacts.

Start with `review.html` and `membership.csv`, then both SHMM HTML reports and the
actual candidate in a model viewer. Inspect floors, rims, depths, offsets, pipe-end
elevations, grades, geometry, roughness assumptions and disconnected components.
Compare candidate findings with baseline findings; preexisting findings still need QA.

Completion distinguishes edit integrity, engineering status and optional engine
results. Exact requested bytes do not establish hydraulic acceptance or calibration.
Ordinary warnings remain review items; configured hold findings remain holds.

### Network islands and nominal downstream routes

The read-only topology API distinguishes inherited independent baseline basins
from new islands, isolated nodes and extensions with reversed downstream access.
It reports directed routes to baseline nodes separately from outfall reachability,
retains all node/link families, and identifies exact-ID held/excluded source
connector candidates without inserting them or expanding scope. Diagnostic
identities obey the configured method order and frozen crosswalk; unresolved
endpoints remain findings instead of guessed asset-ID matches. Pump/regulator
operating conditions and hydraulic acceptance require separate engineering review.
See [topology and island review](docs/topology.md) for the contract and checklist.

### Scope exclusions and single-pipe branches

Individual pipes may be omitted through `selection.pipe_exclusions`, with a source
key, reason, optional exact source fingerprint and optional ledger decision ID.
`selection.single_pipe_branches.mode=review` reports candidates;
`mode=exclude` applies the rule with a required reason. Without an exclusion
policy the model plan is unchanged. `preserve_pipe_keys` keeps named branches;
explicit per-pipe exclusions take precedence over those rule exceptions.

A qualifying branch is a known-active gravity pipe connecting a new degree-one
source endpoint to an existing baseline JUNCTION. The complete active source
network establishes degree, including unselected pipes and unknown-status records.
Existing model assets/pump paths are protected. The graph is frozen once, so this
rule does not recursively strip a multi-pipe reach into shorter branches.

Exclusions precede identity binding, ledger parameter selection and floor donation.
They omit new assets or source updates and never delete baseline assets, move
loads, or erase source context. They are reported as **excluded**, separately from
unresolved data **holds**. Runs publish `scope_exclusions.csv` and
`single_pipe_branches.csv`; membership and the frozen handoff retain scope evidence.
`requested_source_pipes` counts the effective request after exclusions;
`excluded_source_pipes` separately counts exclusions applied to that selection.
Review scope decisions and loading implications alongside the two SHMM diffs.

### Missing manhole rims

`parameters.nodes.missing_rim.mode` selects **hold** (default), **error**, or
**placeholder**. Hold withholds the incomplete junction and its dependent pipes;
error raises before publishing a model. Placeholder mode requires a positive
explicit depth and its basis. It fills genuinely absent rims of new junctions
with known inverts; real source rims, existing model profiles, missing floors,
unresolved datums, invalid measurements and stale reviews retain their treatment.

For a recognizable text sentinel, configure `placeholder.kind` as `depth`,
`placeholder.value` as `12.345`, and `placeholder.unit` as `model`. SHMM writes
literal **12.345** in the JUNCTIONS **MaxDepth** field, with an inline
`UMDEV_PLACEHOLDER_RIM MaxDepth=12.345` comment. SWMM uses MaxDepth for junction
rims even when LINK_OFFSETS is ELEVATION; known invert elevations are retained.
The derived rim is invert plus depth, explicitly marked as an assumption.

Every run lists applied and held placeholder assignments in `placeholder_nodes.csv`.
Applied placeholders appear in candidate QA, both model diffs and the completion
count; their engineering status requires review. Independent readback verifies the
literal sentinel, marker and reconstructed profile. Placeholder geometry is for
review and does not establish hydraulic acceptance. See [configuration](docs/configuration.md).

### Force mains and sealed connections

Force mains require an explicit pressure-friction policy and endpoint roles.
Hazen-Williams C and Darcy-Weisbach absolute roughness remain separate from the
CONDUITS Manning field. The model equation must match the configured policy;
pressure conduits require Dynamic Wave routing. No options are silently changed.
Darcy-Weisbach Geom2 is written in **inches for US models or millimetres for SI**.

A configured surcharge allowance applies only to sealed pressure junctions that
are actually used by accepted force-main instructions, or identified internal
connections in existing force-main reaches. Receiving gravity manholes and storage
wet wells retain their settings. Terminal or mixed source connections require a
reviewed role; missing elevations stay held. Surcharge allowance is an assumption,
not a pressure rating. Exact source force-main endpoints matching existing pumps
are held for station reconciliation to prevent adding passive bypass links.

Each run includes `force_main_accounting.csv` and `pressure_connections.csv` with
source/pump membership, held reasons, roles, original/expected surcharge and basis.
The preview and written-model diffs cover the actual FORCE_MAIN and surcharge
instructions; independent readback checks shape, friction coefficient, units and
surcharge, including preserved open receivers. See [pressure configuration](docs/configuration.md).

### Capability limits

- Implemented input formats: `json_packet`/`json` and `csv`. Native GeoJSON,
  GeoPackage and File Geodatabase adapters are unavailable stubs. Externally export
  an inspected packet; installing GIS extras does not implement those drivers.
- Existing compatible SWMM baselines only. pyproj validates projected CRS/axes/units.
  Global reprojection and baseline datum conversion are unsupported. Configured
  identity/constant-offset observation transforms are separate traceable rules.
- The adapter writes supported circular gravity and explicitly parameterized force-main conduits, cross sections, junctions,
  coordinates and add-only ordered vertices. Existing STORAGE objects can serve
  as pipe endpoints using their preserved model floor and top elevation;
  storage records, curves, initial depths, coordinates and controls are not edited.
  New storage objects, outfall-endpoint parameter contracts, noncircular
  additions, deletes, renames, rewiring, existing-vertex replacement, existing-floor
  dependency edits and new model creation remain held or unsupported.
- `supported_subnetwork` publishes supported complete objects and lists holds.
  `complete_request` requires requested mandatory assignments and candidate hold
  findings to be clear; neither mode establishes engineering acceptance.
- The runner does not implement `geometry_review` publication, spatial asset
  matching, aggregation, generic estimators, native map/profile renderers or feature
  services. Use ingestion/preparation for table review; the explicit missing-rim
  policy is the only supported synthetic profile placeholder.
- Material coefficients are configured assumptions. Unknown material holds or uses
  an explicit documented assumption; pressure coefficients cannot inherit Manning n.

See the [specification](docs/specification.md), [configuration](docs/configuration.md)
and [SHMM handoff](docs/handoff.md). The [minimal project](examples/minimal/project.json)
is executable. The broader [design example](examples/config/utility_project.example.json)
includes future native GIS capabilities and is not a current-driver demonstration.

## Developer README

Use Python 3.12 or newer with hymo >=0.2.1, SHMM >=0.1.2 and pyproj >=3.6,<4.
Install matching local development branches before using unreleased APIs; this does
not assert a public package release. In an activated environment, install this
checkout with `python -m pip install -e ".[test]"` or equivalent environment tooling.
ArcGIS licensing is not required by the core.

Run [examples/run_minimal.py](examples/run_minimal.py) in VS Code. For your own
edit-and-run script, import `Path` from `pathlib` and `run_project` from `umdev`,
then call `run_project(Path("examples/minimal/project.json"),
output_directory=Path("runs/synthetic-review"))`. An explicit output directory must
not exist; omitting it creates a timestamped child of the configured output root.
Relative resource paths resolve against the configuration file declaring them.

Public functions: `load_config`, `ingest_sources`, `prepare_network_from_dict`,
`prepare_project`, `run_project`. Lower-level `umdev.models` functions `compile_handoff`,
`instructions_from_handoff`, `verify_prepared_profiles` and
`candidate_engineering_qa` support separate orchestration and review.
`umdev.topology.diagnose_topology` and `diagnose_swmm_topology` expose read-only
component, directed-route and source-connector diagnostics.

Modules are under `src/umdev`: configuration, sources, records, evidence, parameters,
models, pipeline, decisions, qa, topology, reporting and units. `schemas/project.schema.json` describes
version 1; runtime semantic/source/capability validation is also required.
Add ordinary field names, domains and policies through private configuration;
new formats, grammars or model operations require explicit implementation/tests.

Run `python -m pytest`. Synthetic tests cover schema equivalence through config,
units/datums, evidence conflicts and provisional fallback, floors/rims/offsets,
roughness, scoped overrides, deterministic selection, source preservation, written
profiles, consolidated-ledger staleness and non-executable records, read-only project
preparation, and SHMM diff integration. Engine paths/version must be explicitly enabled;
simulation is not a default. No repository lint/format command is prescribed.

Known pipe crowns above a placeholder rim remain visible as
`pipe_crown_above_placeholder_rim` review findings. They do not automatically inherit
the hold rule for contradictory measured rims; projects can explicitly configure
the placeholder rule as a hold. The actual written-model check uses the applied
placeholder identities to retain this distinction.

Scope exclusions govern source preparation. Separately approved finished ledger
operations retain their independent scope; changing those approvals requires a
reviewed ledger decision rather than an inferred deletion or scope override.
