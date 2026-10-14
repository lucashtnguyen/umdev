# umdev utility model development specification

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-14

Status: Draft v0.1 for review. Manually maintained.
The interfaces, records and configuration below are proposed contracts.

## Summary

### Purpose and end goal

umdev turns heterogeneous utility GIS, existing models and engineering observations
into reproducible model-development decisions and explicit model edits. It should
reproduce the private pilot workflow: select a model region and scenario, add missing
pipes and junctions, modify existing pipes, retain mapped bends, assign individual
structure ports, construct junction floors and rims, derive connection offsets,
assign roughness, and QA the resulting network and profiles.

The governing workflow is **use the best available evidence, record its basis and
uncertainty, then QA the resulting model**. Review flags do not automatically exclude
usable assets. Required values must come from evidence or an explicit configured
assumption or estimation method, never a hidden placeholder.

The core must work with multiple utilities through a reusable configuration contract.
A project can change table names, field mappings, domains, units, evidence ranking
and policy without modifying the core. New source formats or genuinely new parsing
methods may require a documented adapter or parser extension.

### Scope and library boundaries

| Concern | Owner |
| --- | --- |
| Raw GIS reading and field/domain normalization | umdev source adapters |
| Source identity, model crosswalks and topology reconciliation | umdev |
| Scenario membership, lifecycle, projects and scope | umdev project configuration |
| Port parsing, evidence assignment, vertical normalization and assumptions | umdev |
| Floors, rims, depths, offsets, lengths, diameters and material policy | umdev |
| Engineering QA before and after writing | umdev |
| Existing model reading, section interpretation and ordered geometry reading | hymo |
| Explicit model modification, preservation, publication and application audit | SHMM |
| Engine execution interface and file acceptance results | Existing SHMM interface where supported; umdev orchestrates and interprets |
| Engineering acceptance, calibration and promotion of a baseline | Project workflow and reviewer |

The first implementation targets SWMM gravity sewer and drainage networks and
scenario edits to an existing model. Pressure conduits are retained as a distinct
asset type and require their own parameter contract. Water-distribution engines,
catchment/load generation and calibration automation are later extensions.

A model adapter must advertise supported operations and sections. The first
implementation uses the existing hymo document readers and SHMM add/update/vertex
interfaces. Rename, delete, rewire, existing-vertex replacement, new model creation
and additional SWMM section editing remain explicit capability gaps until the
downstream libraries implement them. A sample model does not define the full SWMM
card inventory.

### Engineering workflow

1. Resolve a versioned project configuration, scenario and explicit baseline.
   Produce a schema-discovery report before accepting field mappings.
2. Capture read-only source snapshots, schemas, spatial metadata, domains and hashes.
   Record the difference between source content and later reviewer decisions.
3. Normalize source records into a canonical network while retaining raw values.
   Include all incident pipes needed for attribution, even those outside export scope.
4. Resolve asset identities and directed connectivity. Build an explicit model
   crosswalk; preserve ambiguous candidates for review.
5. Parse structure-port observations and assign them to pipe ends using configured
   evidence precedence and fallback rules.
6. Prepare coherent nodes and links, including absolute elevations, rims, depths,
   pipe-end offsets, units, material coefficients and ordered map geometry.
7. Evaluate scenario, geographic scope and completeness. Record every candidate's
   disposition, including additions, modifications, omissions and deferred operations.
8. Produce a review plan and pre-write QA. Provisional but defensible assignments
   may proceed under configured policy; unresolved required values remain held.
9. Compile supported explicit instructions for SHMM and publish a separate candidate.
   Preserve existing content outside the requested operation set.
10. Re-read through hymo, verify actual effects, and run network/profile QA.
    Run an engine only when explicitly configured with required external resources.
11. Report applied assignments, findings, held assets and revisions together.
    A reviewer can correct a decision and rerun without editing generated files.

Prepare-only, comparison-only, candidate-build and post-build QA are separate
callable workflows. Configuration can also request map/table review products when
a hydraulic candidate cannot be built.

### Best available evidence and readiness

Each field assignment has a value, unit, evidence basis, method, alternatives,
confidence category, QA flags and disposition. Confidence categories are descriptive
labels, not invented statistical probabilities.

| Assignment category | Meaning |
| --- | --- |
| observed | Direct attributable measurement or source value |
| interpreted | Parsed or joined observation assigned to a particular asset/end |
| provisional | Defensible selection with an explicit unresolved qualification |
| estimated | Value from a named configured estimation method |
| assumed | Explicit project policy supplies a value without measurement |
| unresolved | No supported value or no resolved choice among competing candidates |

Assignment category is separate from operation disposition: include, include with
flags, hold, exclude by scenario, outside geographic scope, or unsupported operation.

The decision record must distinguish a geometry defect from a direction-label
mismatch, missing elevation, conflicting measurement, unmatched asset, uncertain
datum or model-floor transfer. A provisional assignment cannot be described as a
verified observation.

For example, a structure has one active outgoing 300-mm pipe and one outgoing
300-mm port. The port says southeast, while linework leaves southwest. A configured
unique-role-and-diameter fallback assigns the port elevation provisionally and
creates a direction-mismatch finding. If two outgoing 300-mm pipes compete, that
fallback cannot establish uniqueness. An explicit reviewed assignment or another
configured discriminating source is needed.

Default QA of an adverse gravity grade is a review finding, not automatic rejection
of an otherwise faithfully assigned profile. A strict project policy can hold it.
Missing mandatory values, invalid numeric serialization and unresolved identity
collisions cannot become usable records merely by lowering QA severity.

### Identity and directed topology

Canonical identity includes source identifier, layer/table identifier and raw asset
ID. Preserve leading zeros and original spelling. Source keys must remain unique
when different layers reuse an asset ID. Model IDs are separate; validate downstream
length, character and case-comparison constraints before instruction generation.

Connectivity may come from pipe endpoint fields, relationship tables, a supplied
crosswalk or an explicitly enabled spatial method. Nearest geometry is a candidate
association, not an implicit authoritative ID. Match distances and units belong in
configuration and decision evidence.

Digitization direction does not define hydraulic inlet/outlet direction. Orient
drawing vertices using established endpoint identities. Do not reverse hydraulic
connectivity simply to obtain a downhill grade. Detect duplicate assets, self-loops,
missing nodes, multipart lines, parallel pipes and replacement/aggregation cases.

Split/merge and aggregated model reaches require an explicit allocation policy for
lengths, end elevations, geometry, diameters and inherited loading/control references.
If the writer cannot express the resulting operation, retain a supported plan and
mark the application capability gap.

### Source observations and structure ports

Support both normalized port tables and denormalized structure fields. A port-table
row can identify structure, port, pipe, role, elevation, diameter, direction and datum.
An inline field can contain a described elevation, bearing sector, size and qualifiers.

Parser configuration names a registered grammar and role mapping. It must preserve
raw text and distinguish zero, absent value, unparsable text, multiple elevations,
units, capped/stub/abandoned ports, drop top/bottom notation and contradictory roles.

Build assignment candidates using direct pipe references first, then configured
role, diameter, geometry and direction evidence. A direction hint can be secondary
to a unique role/diameter connection. Unknown tangent geometry cannot be silently
treated as compatible. Parallel pipes and boundary compass bearings remain
ambiguous until resolved.

Conflicting numeric and textual elevations are retained together. A project may
select a preferred source provisionally when configured precedence explains the
choice; report the residual and rejected alternative. A genuine unresolved choice
has no selected value. An unmatched port remains a structure-level finding without
automatically invalidating an independently resolved pipe end.

### Junction floors and rims

Configuration defines an ordered floor policy: an explicit structure floor field,
an attributed outgoing port, a reviewed assignment, or a named derivation/estimation
method. A minimum of arbitrary parsed numbers is prohibited. A project may explicitly
choose the lowest of accepted active ports, with all participating observations and
the meaning of that convention recorded. A pipe-port floor is not automatically a
surveyed sump elevation.

Select rim elevation independently using configured precedence. Floors, rims and
pipe-end elevations must share the target vertical basis before hydraulic assembly.

For SWMM junctions, in target model length units:

- Maximum depth = rim elevation minus junction floor elevation.
- With DEPTH offsets, connection offset = absolute pipe-end invert minus node floor.
- With ELEVATION offsets, supply the absolute pipe-end invert; do not apply the
  DEPTH formula to that field.
- Reconstruct the rim as written floor plus written maximum depth.
- Reconstruct each pipe end using the model's actual offset convention.

Apply these checks to the candidate, not only to source tables. A floor-only or
offset-only assignment with a zero rim/depth placeholder is not a complete profile.
A measured elevation of zero is different from using zero to fill missing data.

An existing model node has an existing floor and connected link ends. Source-relative
offsets cannot be copied onto that floor without recalculation. If changing the
floor, preserve or explicitly revise every affected absolute pipe end, depth, rim
and dependent object. Hold a required adjustment when the downstream writer cannot
express it.

### Units and reference systems

Retain source units on every measurement; normalize engineering calculations to SI
where practical and convert explicitly to model field units. Geometry's projected
unit can differ from hydraulic length units. Distinguish international feet,
US survey feet, metres, millimetres and inches. Do not derive units from magnitude.

Horizontal CRS and vertical datum are separate declarations. Record full CRS
metadata when available. Do not measure endpoint distances in geographic degrees.
Coordinate reprojection does not establish a vertical transformation.

Vertical policies can use an identity transformation, documented constant offset,
survey-control correction, or registered transformation method. Each rule records
source/target datum, affected area/assets, units, method, provenance and verification
status. A screening approximation stays provisional through export and QA; do not
promote it because a profile looks plausible. Apply the same transformation
consistently to floor, port and rim observations on that source basis.

Unknown/mixed datum may support geometry and same-source relative-height review.
It cannot silently supply absolute elevations on a different target datum. Project
overrides and conversions remain configuration, not core constants.

### Pipe geometry and hydraulic parameters

Scope can be selected by configured polygon, original-model extent, asset set or
network membership. Geographic and connectivity screens are distinct. Boundary
crossers can be held; clipping/splitting requires an explicit topology policy.

Retain all accepted XY bends and the intended inlet-to-outlet order. M ordinates
are measures, not elevations. Z ordinates become hydraulic observations only under
an explicit source/vertical policy. Multipart and curved geometry must be preserved,
handled by a configured conversion or reported as unsupported; do not collapse it
silently. Geometry-only QA must distinguish validity, usability by the writer and
regional exclusion.

Hydraulic length can use surveyed/attribute length, GIS path length, model length
or a reviewed value in configured order. A material discrepancy remains a QA finding;
drawing length need not equal effective hydraulic length.

Diameter and shape selection must respect actual units, source conflicts and
downstream capability. For a circular pipe, the crown at an end is the pipe invert
plus diameter. Pipe grade is (upstream pipe-end invert minus downstream pipe-end
invert) / hydraulic length. It is not the difference between junction floors divided
by length.

Material resolution uses exact normalized code/description aliases and preserves
conflicting domain labels. Roughness policy records selected Manning n, reference
range, condition/lining assumptions, source citation and approval/calibration status.
Unknown material can use an explicit project assumption with a QA flag. There is no
core blanket n. Manning n, Hazen-Williams C and Darcy-Weisbach roughness height are
distinct parameters and require separate dimensional/pipe-type contracts.

### Scenarios and model change planning

Existing and future scenarios share source observations but have independent
membership, construction/replacement decisions and compiled instructions. Utility
statuses and meanings are configured; unknown lifecycle data requires a recorded
decision rather than an assumed active state.

Comparison priority labels, including add/modify ranks, are optional source fields.
Rank alone does not supply engineering parameters. The plan can alternatively derive
adds/updates from an explicit crosswalk and selected baseline.

Omitted update fields preserve baseline values. Baseline-only objects are not deleted
because an input layer is partial. Preserve loads, controls, pump curves, rainfall
references and scenario settings unless targeted by explicit supported operations.

A supported-subnetwork export may omit held additions while preserving the baseline;
report its reduced membership and disconnected components. A complete-request export
requires all requested mandatory assignments. A geometry review export produces
spatial/table products without inventing hydraulic floors. If explicit test assumptions
are enabled, list them per object and keep that mode separate from project-ready output.

### QA and review outputs

| Check class | Examples | Typical treatment |
| --- | --- | --- |
| Input/schema | Missing mapped field, wrong domain, nonfinite values | Mapping/value error or asset hold |
| Assignment | Direction mismatch, competing ports, numeric/text conflict | Flag provisional choice or hold unresolved assignment |
| Reference systems | Unknown datum, provisional transform, CRS mismatch | Explicit compatibility/verification finding |
| Geometry/topology | Multipart, reversal, missing node, duplicate ID, disconnected component | Configured handling, review or capability hold |
| Node/profile | Missing rim, incorrect depth, crown above rim, actual adverse grade | Required-value hold or engineering QA severity |
| Material/type | Unknown lining, assumed n, pressure formula mismatch | Explicit policy assumption or unresolved parameter |
| Publication | Unrequested edit, failed readback, stale baseline, dangling supported reference | Application failure |
| Engine | Parser errors, warnings, continuity, numerical instability | Distinct engine result, never implicit calibration acceptance |

Separate severity from disposition. Each finding identifies rule/version, object,
field, source evidence, calculated residual, units, consequence and review status.
Tolerance classifies significance; it must not hide a requested edit or alter the
written value.

Outputs per run include effective configuration and hash, input/schema manifests,
canonical source snapshots, assignment decisions, model crosswalk, scenario plan,
prepared node/link tables, QA findings, complete membership/hold tables, candidate
and SHMM audit when applicable, and candidate readback verification. Formats begin
with JSON and CSV; HTML review summaries and optional GIS layers follow.

Profile review should display ground/rim, junction floor, individual pipe inverts
and crowns with elevation and distance units. HGL/results are shown only when actual
engine output is available, with run identity; do not reuse stale results.

A decision ledger stores reviewed overrides with evidence hashes, rationale and
affected assets/fields. If evidence or policy changes, mark the prior decision stale
and re-evaluate under configured policy. Do not force a per-flag approval prompt in
normal best-available-data runs. Keep model promotion and external publication separate.

## Developer README

### Preview and written-model differences

The [handoff contract](handoff.md) defines the boundary with SHMM. Every candidate
build supplies explicit target-unit changes plus source-keyed assignment evidence
and QA flags. SHMM produces a baseline-to-requested-update preview diff before
publication, then a baseline-to-actual-written-model diff after reading the file.
Both JSON/CSV/filterable HTML products must retain no-ops, geometry order, hashes,
requested versus unrequested effects and preserved-text checks. umdev links its
complete membership/hold and engineering QA reports to those SHMM products.
No successful completion manifest is issued for a written-model mismatch.

### Canonical record contracts

Source adapters expose the following proposed records independently of source field
names. Records retain source references rather than flattening every observation
into a single unqualified attribute.

| Record | Required contents |
| --- | --- |
| Source reference | Source/dataset/row key, snapshot hash, field or relationship, raw value and source schema version |
| Measurement | Observation ID, value or missing condition, quantity, unit, CRS/datum where relevant, source references and qualifiers |
| Node | Stable canonical key, original asset ID, node type, status, point geometry, floor/rim observations and incident connections |
| Link | Stable canonical key, original asset ID, pipe type, directed endpoints, ordered geometry, length/diameter/material observations and source status |
| Port | Stable observation/structure key, role, optional direct pipe reference, elevation/diameter/direction observations and raw description |
| Assignment | Target object/field/end, selected value/unit, category, method/version, selected and rejected observations, QA flags and reviewer override |
| Prepared object | Final target-unit values, geometry, parameter assignment references, completeness and scenario membership |
| Change plan | Baseline hash, scenario, target model IDs, explicit operations/old-value conditions, affected dependencies and assignment references |
| Finding | Rule/version, object/field/end, severity, disposition, evidence/assignment references, residual/unit and review status |

Entity IDs, source asset IDs and target model IDs are separate. Optional measurements
stay absent with an explicit missing condition; no sentinel is passed into arithmetic.
Represent field-level provenance and scoped datum metadata even when a node/link has
a convenient summary value.

### Source formats and capability discovery

The first source contracts must accept tabular CSV and JSON export packets retaining
raw attributes, geometry, units, CRS, domains and relationship metadata. Add GeoPackage
and File Geodatabase reading through optional adapters or a documented external
exporter. Native File Geodatabase capability must be reported and tested in the
actual driver/environment; it is not assumed from a file extension.

ArcGIS licensing is not a core dependency. An ArcGIS export workflow can capture a
portable source packet in its supported environment, then the Python 3.12 core can
prepare it. Adapter availability and source-packet versions are in the run manifest.
Feature-service access and additional vendor formats are extensions with read-only
snapshot and authentication contracts.

### Proposed package structure and interfaces

Keep the computational core independent of GIS drivers, ArcGIS licensing and the
model writer. Proposed components:

| Component | Contract |
| --- | --- |
| configuration | Validate schema, resolve private project overlays and freeze effective policy |
| sources | Inspect/read source formats and expose canonical observations with provenance |
| identity and topology | Build explicit associations and directed network candidates |
| evidence | Parse ports and reconcile ranked observations and reviewed overrides |
| parameters | Assemble complete nodes/links with units, datum and policy metadata |
| scenarios and plans | Select membership and produce supported explicit change plans |
| qa | Evaluate source assignments, prepared parameters and candidate readback |
| models | hymo reading and SHMM instruction translation; capability checks |
| reports | JSON/CSV and later HTML/profile/GIS review products |

A proposed callable sequence is configuration resolution, source inspection,
normalization, preparation, plan compilation, candidate application and candidate QA.
Exact Python API names will be specified during phase 1; these are not existing APIs.

Adapters return records plus schema metadata and content fingerprints. Parser,
transform and estimator extensions use named, trusted registered implementations.
Configuration is data: no eval, arbitrary Python import string or unrestricted query
execution. Existing project modules are extraction candidates, not assumed production
architecture.

### Configuration and reproducibility

Use versioned JSON for the first contract. See [configuration](configuration.md) and
the [synthetic example](../examples/config/utility_project.example.json).

Configuration must be sufficient to adapt at least two different utility GIS schemas
without changes to the parameter/QA core. It covers:

- Source formats, tables, identifiers, domains, relationships, filters and field mappings.
- Measurement units, CRS/datum declarations and per-field missing-value semantics.
- Inline port grammars and normalized port-table mappings.
- Source ranking, association fallback and provisional assignment rules.
- Floor/rim, length/diameter, material and pressure-parameter policies.
- Scenarios, geographic scope, crosswalk rules and model compatibility.
- QA thresholds, finding severity, export completeness and optional engine checks.
- Output locations, retained artifacts and reviewed overrides.

Record canonicalized effective-config hash, adapter/parser/policy versions, dependency
versions, source hashes and plan hash. Publication checks that the baseline and evidence
still match the preview. Selection/assignment is deterministic with stable sorting
and explicit tie handling; reproducibility compares semantic artifacts and candidate
bytes while excluding declared timestamps and run-folder names.

No source database, model, private configuration or historical decision is modified
by a preparation run. Generated outputs are separate. Public tests are synthetic;
a moved or rotated real network may remain identifiable.

### Tests and acceptance criteria

| ID | Required regression or acceptance test |
| --- | --- |
| A01 | Two synthetic utilities use different table/field/domain/port layouts; configuration changes reproduce the same canonical network |
| A02 | SI and US inputs produce equivalent target parameters, including inch, foot and projected geometry conversions |
| A03 | Unique outgoing role/diameter survives conflicting compass text as provisional; competing candidates remain unresolved |
| A04 | Numeric/text conflicts retain both observations and a selected-source rationale or explicit hold |
| A05 | Floor 100.0, rim 110.0 and end 100.2 produce depth 10.0 and DEPTH offset 0.2; readback reconstructs both heights |
| A06 | Three connected nodes with distinct floors and rims yield their measured pipe grades; zero-floor/depth regressions fail |
| A07 | Existing-floor edits trigger recomputation/preservation of all attached absolute ends and fail on unsupported dependent operations |
| A08 | Datum transforms apply consistently to floors, ports and rims; unknown/mixed labels and unverified transforms stay visible |
| A09 | Geometry direction, bends, M/Z interpretation, multipart/curve handling and boundary crossers follow configured policy |
| A10 | Material aliases, contradictory domains, unknown-material assumptions, lining and pressure-formula separation are tested |
| A11 | Existing/future scenarios have separate deterministic membership and no implicit deletion of baseline-only assets |
| A12 | Held assets appear in complete membership reports; partial export counts and disconnected components are explicit |
| A13 | Candidate readback checks every prepared field and untouched source content; stale inputs never publish as current |
| A14 | Changed evidence invalidates a scoped reviewed override; unrelated assignments remain stable |
| A15 | Identical inputs/config reproduce decisions and candidate bytes; source files remain unchanged |
| A16 | Optional engine checks report actual version/resources/results separately from engineering acceptance |
| A17 | Private pilot replay reproduces the previous strict profile workflow outside the public repository, then exercises the best-available fallback policy |
| A18 | A third GIS schema requiring only new mappings/policies runs without municipality-specific core branches |

Use pytest, hand calculations, realistic synthetic topology, invalid-input cases and
dimensionally equivalent examples. Test policy changes as well as numeric outputs.
Do not test only whether a generated model can be read.

### Implementation phases

1. **Contract and fixtures:** review this spec; define JSON Schema, immutable canonical
   records and proposed APIs; create two synthetic GIS schema fixtures and configuration
   validation tests. Establish Python 3.12 packaging and driver capability discovery.
2. **Reusable preparation:** normalize sources, identities, units and inline/table ports;
   implement ranked evidence, provisional assignment, override ledger and QA.
3. **Complete parameters:** floors/rims/offsets, geometry, lengths/diameters, material and
   scenario policies with hand-calculation and conflict tests.
4. **Model integration:** compile existing supported SHMM operations, publish/read back
   candidates, verify preservation, and replay the private pilot outside the repository.
5. **Review loop:** reusable reports, profiles, scoped decision edits, post-build network
   QA and configured engine checks; test a third utility schema.
6. **Extensions:** aggregated reaches, load transfers, pressure networks, additional SWMM
   cards and other model targets as explicitly reviewed capabilities.

The first useful release is complete when A01 through A15 pass and the private pilot
replay A17 produces a coherent candidate and traceable holds. Engine execution is optional
unless enabled for that run. Later features must remain named capability gaps.

### Decisions for specification review

Confirm the proposed first-release boundaries, JSON configuration/overlay model,
provisional assignment defaults, floor policy choices and the distinction between
warnings and required-data holds. Decide which additional source formats and model
operations are essential for the first utility deployment.

No core municipality field names, vertical shifts, pipe cutoffs, status lists or
roughness defaults are prescribed. Review project policies independently from reusable
algorithms.

### References

The downstream interfaces were inspected in the local hymo and SHMM repositories;
their broader planned features are not treated as implemented. SWMM node/link field
semantics follow the [EPA SWMM 5.2 User's Manual](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P10145M6.TXT),
including link-offset conventions and closed-conduit roughness guidance.
