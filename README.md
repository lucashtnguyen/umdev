# umdev

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-14
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
Qualified, capped/drop or multiple-port text still requires supported interpretation.

Configured floor/rim sources, individual pipe-end inverts, lengths, diameters and
material-to-Manning-n policies produce finished parameters. Depth is rim minus floor.
DEPTH offsets are pipe-end invert minus node floor; ELEVATION offsets are absolute
pipe-end inverts. Grade is upstream minus downstream pipe invert, divided by length.
Retained geometry Z/M ordinates never silently become invert elevations.

The SWMM adapter hands finished instructions to SHMM, which creates a preview diff
before publication and an independent written-model diff after actual file readback.
umdev verifies prepared floors, reconstructed rims, pipe-end elevations, dimensions
and grades, then reports profile and connectivity findings.

### Outputs and manual QA

Keep project sources, model files, actual IDs, configuration, crosswalks, reviewed
decisions and outputs in private workspaces. Public tests/examples are synthetic.

A new run directory contains:

- `effective_config.json`, `configuration_origins.json`, `canonical.json`:
  resolved policy, declaration origins, normalized observations, raw values and hashes.
- `prepared.json`, `nodes.csv`, `pipes.csv`, `findings.csv`, `membership.csv`:
  assignments, assumptions, references and requested dispositions/holds.
- `handoff.json`: frozen target-unit instructions, evidence, scenario and fingerprints.
- `preview_diff.json/.csv/.html` and `written_model_diff.json/.csv/.html`:
  requests/no-ops, observed/missing/unexpected effects and exact preservation checks.
- `candidate.inp`, `application_audit.json`, `profile_verification.json`:
  separate written model, SHMM audit and prepared-to-written profile checks.
- `candidate_qa.json/.csv`, `review.html` and, after successful integrity checks,
  `completion.json`. An explicitly enabled engine check adds separate artifacts.

Start with `review.html` and `membership.csv`, then both SHMM HTML reports and the
actual candidate in a model viewer. Inspect floors, rims, depths, offsets, pipe-end
elevations, grades, geometry, roughness assumptions and disconnected components.
Compare candidate findings with baseline findings; preexisting findings still need QA.

Completion distinguishes edit integrity, engineering status and optional engine
results. Exact requested bytes do not establish hydraulic acceptance or calibration.
Ordinary warnings remain review items; configured hold findings remain holds.

### Capability limits

- Implemented input formats: `json_packet`/`json` and `csv`. Native GeoJSON,
  GeoPackage and File Geodatabase adapters are unavailable stubs. Externally export
  an inspected packet; installing GIS extras does not implement those drivers.
- Existing compatible SWMM baselines only. pyproj validates projected CRS/axes/units.
  Global reprojection and baseline datum conversion are unsupported. Configured
  identity/constant-offset observation transforms are separate traceable rules.
- The adapter writes supported circular gravity conduits, cross sections, junctions,
  coordinates and add-only ordered vertices. Pressure parameters, noncircular
  additions, deletes, renames, rewiring, existing-vertex replacement, existing-floor
  dependency edits and new model creation remain held or unsupported.
- `supported_subnetwork` publishes supported complete objects and lists holds.
  `complete_request` requires requested mandatory assignments and candidate hold
  findings to be clear; neither mode establishes engineering acceptance.
- The runner does not implement `geometry_review` publication, spatial asset
  matching, aggregation, generic estimators, native map/profile renderers or feature
  services. Use ingestion/preparation for table review, not hydraulic placeholders.
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
`run_project`. Lower-level `umdev.models` functions `compile_handoff`,
`instructions_from_handoff`, `verify_prepared_profiles` and
`candidate_engineering_qa` support separate orchestration and review.

Modules are under `src/umdev`: configuration, sources, records, evidence, parameters,
models, pipeline, qa, reporting and units. `schemas/project.schema.json` describes
version 1; runtime semantic/source/capability validation is also required.
Add ordinary field names, domains and policies through private configuration;
new formats, grammars or model operations require explicit implementation/tests.

Run `python -m pytest`. Synthetic tests cover schema equivalence through config,
units/datums, evidence conflicts and provisional fallback, floors/rims/offsets,
roughness, scoped overrides, deterministic selection, source preservation, written
profiles and SHMM diff integration. Engine paths/version must be explicitly enabled;
simulation is not a default. No repository lint/format command is prescribed.
