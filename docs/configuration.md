# umdev reusable configuration contract

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-15

Status: Version-1 configuration contract with an implemented umdev 0.1.0 subset.
`load_config` and `src/umdev/schemas/project.schema.json` now exist. Runtime
semantic validation governs current capabilities; broader design examples may
include unavailable drivers or future policies.

## Summary

### Implemented configuration subset

Use [examples/minimal/project.json](../examples/minimal/project.json) and
[examples/run_minimal.py](../examples/run_minimal.py) for executable synthetic use.
The broader GeoPackage design example below is not a current-driver demonstration.

| Concern | Current behavior |
| --- | --- |
| Sources | `json_packet`/`json`, CSV; native GIS format IDs raise unavailable-capability diagnostics |
| Overlays | Ordered `extends`, recursive dictionaries, atomic lists/nulls, declaration-relative resource paths and cycle detection |
| Units/CRS | Canonical SI metre values; declared source units; pyproj projected CRS/axis/unit checks; no reprojection |
| Identity | Explicit endpoint fields, asset/model IDs and optional JSON crosswalk; no spatial candidate matching |
| Port parsers | `normalized_port_row_v1` and `elevation_direction_diameter_v1` with configured fields/roles/units |
| Direction | `hint` permits configured unique role/diameter provisional fallback; `strict` withholds mismatching/unverified declared directions |
| Datum | `identity` or signed `constant_offset` with basis, verification, precedence and optional `asset_ids`/`source_keys` |
| Floors | `reviewed_override`, `structure_floor`, `unique_active_outgoing_port`, or explicitly configured `lowest_active_port`/`minimum_active_port` |
| Rims | `reviewed_override` or `structure_rim` |
| Pipe measurements | Reviewed/attribute length and diameter; explicit `geometry_length` alternative |
| Roughness | Gravity `manning_n` with exact aliases and optional reference ranges; unknown material holds or uses an explicit assumption |
| Scope | All, asset IDs, canonical-metre bounds or baseline-map extent; optional buffer; crossing linework held without clipping |
| Review overrides | Node floor/rim and pipe upstream/downstream invert/length/diameter, with source-record hash, units, rationale, reviewer/date |
| Publication | `supported_subnetwork` or `complete_request`; `geometry_review` is not implemented by the candidate runner |

Unsupported methods fail explicitly. Additional observation text fields can be
mapped through registered inline-parser observations; core source-field names
do not need modification. Ordinary project aliases/policies remain configuration.
New format drivers, grammars or algorithms require implementation and tests.

The remaining design discussion includes future survey transforms, polygon scope,
generic estimators, feature services and native review products. Those paragraphs
describe extension requirements, not implemented capabilities.

### Configuration purpose

A project configuration translates a utility's source schema and engineering policy
into the canonical preparation workflow. It must be possible to switch utility
field names, layer layouts, units, lifecycle domains and evidence ranking without
changing core algorithms.

The [synthetic example](../examples/config/utility_project.example.json) demonstrates
a GeoPackage network plus a CSV port schedule, denormalized structure descriptions,
independent existing/future scenarios and a best-available-data policy. Its paths,
field names and datum are fictional. It declares the configuration shape, not
available datasets or implemented adapters.

### Configuration groups

| Group | Required meaning |
| --- | --- |
| schema_version, kind | Version and contract identity; reject unsupported versions |
| metadata | Project alias, ownership, contact, date and configuration status |
| extends | Ordered configuration fragments or presets; resolution rules below |
| sources | Format, location and read-only access for each named input |
| datasets | Source/table binding, canonical entity, fields, IDs, domains, filters and geometry metadata |
| units | Internal engineering unit convention; SI implemented |
| model | Adapter, explicit baseline, declared baseline metadata, target conventions and capability handling |
| identity | Source-key composition, connectivity and model association methods |
| ports | Observation parsers, declared roles, units and assignment policy |
| elevations | Target datum, scoped transformation methods, provenance and provisional handling |
| parameters | Explicit node and link source/derivation precedence |
| roughness | Material aliases, values, ranges, assumptions and hydraulic parameter context |
| scenarios | Independent inclusion/exclusion lifecycle rules and unknown-status treatment |
| selection | Selected scenario, geographic/asset scope and attribution context |
| qa | Allowed assignment categories, findings, numerical tolerances and optional engine configuration |
| review | Reviewed overrides and changed-evidence treatment |
| publication | Completeness mode, separate output location and retained formats |

### Source and dataset mapping

A source describes a container or endpoint; a dataset binds one table/layer to a
canonical entity. For CSV, `table=null` means the source file itself. A field mapping
names an actual source field and its canonical destination. Dataset `id_field`
identifies source rows and supplies the canonical source `row_id`. Source keys use
source, dataset and row_id for nodes, pipes and ports; hydraulic asset IDs remain
separate explicit canonical fields.

Every numeric engineering field has a declared unit. The `missing` list and
`zero_is_missing` rule apply only to that mapped field. Missing zero in one source
must not make valid sea-level or negative elevations disappear elsewhere. Missing
optional `model_id` can proceed to an explicit crosswalk; it is not a fabricated model ID.

Identifier fields preserve leading zeros. Trimming is explicit. Domain decoding
uses inspected source domains or a configured map and retains raw codes and decoded
labels. Unmapped status and material are different findings and may have different
project policies. Contradictory code/description pairs remain visible.

Dataset filters operate on raw rows before canonical normalization. Selection
`asset_filters` operate on canonical fields afterward. Predicates use a restricted
named operator contract such as equals, in, not_in, lt, lte, gt and gte, with numeric
units where applicable. Configuration cannot execute Python or arbitrary SQL.
Excluded selection assets still participate in full-network port attribution when
needed to establish uniqueness.

Geometry mapping declares expected CRS and linear units and is checked against
actual source metadata. A mismatch is a finding/error, not permission to relabel
coordinates. M/Z, curves, multipart handling and supported conversions are explicit.

### Port observations and evidence precedence

The runtime implements two registered parser IDs:

- `normalized_port_row_v1` consumes canonical port-table fields.
- `elevation_direction_diameter_v1` parses a described elevation, compass direction
  and diameter; the observation mapping supplies role and units.

New field names and additional observation mappings require config changes only.
A new language or complex grammar can require a registered parser extension.
Parameterization and raw text are recorded in each observation.

The implemented inline grammar is one elevation, an optional parenthesized compass
direction and an optional diameter. Elevation unit text may include a feet mark
(`'`); it is interpreted as feet and must be compatible with the configured
observation elevation unit. Explicit diameter suffixes must likewise agree with the
configured observation diameter unit.

An omitted diameter suffix is accepted only after an explicit parenthesized compass
direction. The observation's configured `units.diameter` supplies that suffix; the
parser does not infer inches from magnitude or utility-specific conventions.
An elevation followed by `(SE), 12` therefore has an unambiguous configured size,
while an unqualified two-number string remains unresolved. A diameter written as
`12'` is not accepted as an inch abbreviation or silently reinterpreted. This rule
is reusable syntax handling, not an asset-specific correction.

Retain the original source attribute and raw description, parsed numeric values,
configured/stated units and the parsing/default-unit rule used. Those observations
continue through normal datum normalization, evidence ranking and port assignment;
syntax acceptance alone does not establish the correct pipe end or surveyed basis.
Multiple measurements, cap/drop/stub qualifiers, unsupported unit suffixes and
competing assignments remain unresolved or explicitly held rather than having
qualifiers stripped away.

The `ports.assignment.precedence` list orders evidence families. `reviewed_override`
references the decision ledger, `pipe_numeric` uses direct endpoint attributes,
`normalized_port` uses the port table, and `inline_port` uses descriptions.

Precedence is evaluated after usability checks. A missing or invalid preferred
observation does not suppress a usable lower-ranked source. Two distinct values
with equal rank remain unresolved unless another configured rule disambiguates them.
A preferred source with a contradictory alternative can be selected provisionally
under `prefer_ranked_with_flag`; both values and their residual remain in the audit.

Direct port-to-pipe references require a configured identity join, not comparison
of arbitrary display text. Each relationship should use dataset-qualified identity.
Direction is a hint in the example. A unique role/diameter fallback can resolve a
connection with a compass mismatch; competing candidates remain held. Tolerances
describe matching uncertainty and are not universal engineering thresholds.

### Floors, rims, offsets and estimates

`floor_methods` and `rim_methods` are ordered sources/derivations. A structure floor
field is distinct from an outgoing-port-derived floor. If a method yields competing
valid floors, do not choose the smallest number as an unrecorded tie breaker.

General named estimators are future work. The current loader rejects a nonempty
`estimators` list. Explicit floor methods are recorded policy derivations, not
generic interpolation. Selecting a minimum-active-port floor is an explicit
configured derivation, not an invisible tie breaker.

The target `link_offsets` determines serialization: DEPTH uses port minus node floor,
while ELEVATION uses absolute port elevation. Rim minus floor sets junction depth.
All three heights are placed on the same target basis before assembly. Existing-node
floor changes would require all attached ends/depths to be preserved and are
currently unsupported by the writer adapter. Existing floors stay fixed;
supported rim changes can update depth without moving the floor.

Required missing floor or rim remains held in the example. To produce geometry
review, export GIS/table products. Do not insert hydraulic zeros merely to force
every geometry into an INP.

### Units and vertical transformations

The example uses metre-based target hydraulics and millimetre source diameters.
The resolver must validate the model adapter's flow/length relationship, actual
baseline options and map units. SWMM map CRS is supplied as external metadata,
not claimed to be encoded in the INP.

A transformation includes its source/target datum, registered method, applicability,
provenance, verification and parameters where needed. Constant offsets additionally
declare offset value and unit. Future survey/spatial methods must declare resources and fingerprints;
those transformation methods are not implemented. Conflicting overlapping rules require an explicit precedence;
there is no global municipality-specific offset.

The example's identity transformation retains a fictional declared local datum;
`verified=false` records that it is not a survey certification. `allow_provisional`
permits the declared basis with findings. Unknown/mixed datum cannot use an identity
rule for a known target without evidence.

### Material and pressure parameters

Roughness rules use exact normalized aliases with unique membership. A configured
coefficient must be positive/finite, within its cited reference range where one is
declared, and attached to a compatible hydraulic parameter and pipe type.

The example's unknown-material fallback is an explicit synthetic assumption,
not a core default or a calibrated value. Projects can replace it with a hold or
a reviewed assumption. General condition/lining/rehabilitation policies remain extension targets.
The current review ledger does not implement roughness overrides; use configured
material assumptions or separately reviewed finished model instructions.

Pressure pipes require a separate parameter contract. Manning n cannot populate a
Hazen-Williams C-factor or Darcy-Weisbach roughness-height field. Missing pressure
parameters remain visible even when gravity material rules are otherwise usable.

### Findings and export completeness

`qa.rules` configures implemented finding severity and review/hold disposition.
`qa.profile` can retain a policy label; no preset expansion is implemented.
Configure effective rules explicitly. Required-data/writer failures are distinct
from configurable engineering warning severity.

The example treats direction mismatch, actual adverse grade, crown above rim,
disconnection and declared provisional datum as warnings. Engineering findings
remain in prepared and post-build QA. Strict projects can change their disposition
under a named export profile. Missing required data and writer integrity errors are
independent of configurable warning severity.

`supported_subnetwork` builds supported complete assignments and reports every held
request and disconnected component. `complete_request` requires all requested
mandatory parameters. `geometry_review` is a future publication target;
the current runner rejects it. Use ingestion/preparation for table review. Explicit test assumptions must remain per-object decisions.

### Consolidated manual decisions

`review.ledger_path` names one external version-1 JSON decision ledger, resolved
against the configuration file declaring the path, including through `extends`.
The ledger stores `parameter_override`, guarded `model_update`, and non-executable
`review_record` entries with authorization, rationale, scenario and current evidence.
Active approvals with stale evidence, stale baseline binding or mismatched expected
old values raise before publication; legacy `changed_evidence` does not relax this.

A nonempty ledger path is mutually exclusive with nonempty `review.overrides_path`,
`review.overrides`, and `model.changes_path`. Empty legacy paths/lists are disabled.
Legacy inputs remain supported in projects without a ledger. Runtime preparation
injects compiled ledger overrides into an in-memory configuration copy; source
configuration and source observations are never rewritten. Override policy methods
remain authoritative. If an active approved override is bypassed by method order or
preserved baseline policy, preparation raises before output instead of silently
ignoring the approval. See
[manual decisions](decisions.md) for the complete ledger contract.

### Review changes and scope

Reviewed overrides identify source keys, fields/ends, replacement values and units,
rationale, evidence fingerprints and reviewer/date. They cannot silently modify
source files. A documented legacy-model rim may support an explicit private reviewed
override with identity, source/model reference and vertical-basis evidence. This
does not create an automatic legacy-model or nearest-node rim fallback in the core.
Changed evidence marks a prior override stale; the selected
reevaluation policy determines whether the asset proceeds provisionally or is held.

A configured region is separate from network membership. The example's original-model
extent is one optional geographic policy, not a universal definition of a utility
service area. Explicit asset lists and rectangular bounds are supported alternatives;
polygon scope is a future extension. Clipping
requires a topology operation, not just a geometry predicate.

## Developer README

### Resolution and validation

Relative paths resolve against the file declaring them. Paths imported through
`extends` retain their declaration file as provenance. Filesystem paths and source
metadata containing private information stay in private project workspaces.

Implemented merge rules are deterministic: resolve extends left to right, then apply
the project file; recursively merge dictionaries, replace lists atomically, and treat
null as an explicit nullable value rather than deletion. Reject cycles and unresolved
references. ID-keyed policy dictionaries allow a project to override one rule without
replacing an unrelated rule list.

Unknown keys fail validation except documented extension blocks. Reject missing
mapped fields, incompatible entities, invalid units, unrecognized parser/method IDs,
ambiguous aliases, duplicate IDs, invalid tolerances, unsupported model units and
source/output path collisions before publication. A source field absent from a table
is a mapping error; an optional measurement absent in a valid row is a data condition.

JSON Schema and semantic validation are implemented. Ingestion checks mapped
source fields and declared spatial metadata, including projected CRS/units through
pyproj. JSON parsing alone does not establish engineering applicability or writer
support. Automatic discovery and draft mapping generation remain future work.
A schema-accepted format name does not prove a native driver is implemented.

### Configuration artifacts

Retain raw configuration, canonical resolved configuration, file/preset origins,
schema/policy/parser versions and a deterministic hash of the resolved content.
Record file/feature-service snapshot hashes and all derived decisions for the run.
Credentials and authentication tokens are never config values; use a separately
configured adapter authentication provider.

The synthetic `tests/fixtures/schema_equivalence` cases exercise different
table/field names and normalized versus inline ports through configuration alone.
These establish regression coverage, not universal utility-schema acceptance. Source-format
capability discovery must report unavailable drivers rather than silently dropping
a source.
