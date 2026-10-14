# umdev reusable configuration contract

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-14

Status: Draft v0.1. This defines proposed schema version 1; no configuration loader
or JSON Schema is implemented yet.

## Summary

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
| units | Internal engineering unit convention; SI proposed |
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

The example uses two proposed registered parser IDs:

- `normalized_port_row_v1` consumes canonical port-table fields.
- `elevation_direction_diameter_v1` parses a described elevation, compass direction
  and diameter; the observation mapping supplies role and units.

New field names and additional observation mappings require config changes only.
A new language or complex grammar can require a registered parser extension.
Parameterization and raw text are recorded in each observation.

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

Named estimators are opt-in, with inputs, applicability, method/version and units.
An empty estimator list disables estimation. An estimated value may be applied with
flags when project policy permits; it cannot be called a surveyed value.

The target `link_offsets` determines serialization: DEPTH uses port minus node floor,
while ELEVATION uses absolute port elevation. Rim minus floor sets junction depth.
All three heights are placed on the same target basis before assembly. Existing-node
floor changes require recomputation/preservation of attached link ends and depths.

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
declare offset value and unit. Survey or spatial methods declare their resources
and fingerprints. Conflicting overlapping rules require an explicit precedence;
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
a reviewed assumption. Condition, lining, deposits and rehabilitation can supersede
the nominal material policy through explicit observations or reviewed overrides.

Pressure pipes require a separate parameter contract. Manning n cannot populate a
Hazen-Williams C-factor or Darcy-Weisbach roughness-height field. Missing pressure
parameters remain visible even when gravity material rules are otherwise usable.

### Findings and export completeness

`qa.profile` is a policy identifier, not a hidden bundle of hard-coded values.
The effective configuration must expand every preset into explicit resolved rules.

The example treats direction mismatch, actual adverse grade, crown above rim,
disconnection and declared provisional datum as warnings. Engineering findings
remain in prepared and post-build QA. Strict projects can change their disposition
under a named export profile. Missing required data and writer integrity errors are
independent of configurable warning severity.

`supported_subnetwork` builds supported complete assignments and reports every held
request and disconnected component. `complete_request` requires all requested
mandatory parameters. `geometry_review` produces spatial/table artifacts without
a hydraulic candidate. Explicit test assumptions must remain per-object decisions.

### Review changes and scope

Reviewed overrides identify source keys, fields/ends, replacement values and units,
rationale, evidence fingerprints and reviewer/date. They cannot silently modify
source files. Changed evidence marks a prior override stale; the selected
reevaluation policy determines whether the asset proceeds provisionally or is held.

A configured region is separate from network membership. The example's original-model
extent is one optional geographic policy, not a universal definition of a utility
service area. Polygon scope and explicit asset lists are alternatives. Clipping
requires a topology operation, not just a geometry predicate.

## Developer README

### Resolution and validation

Relative paths resolve against the file declaring them. Paths imported through
`extends` retain their declaration file as provenance. Filesystem paths and source
metadata containing private information stay in private project workspaces.

Proposed merge rules are deterministic: resolve extends left to right, then apply
the project file; recursively merge dictionaries, replace lists atomically, and treat
null as an explicit nullable value rather than deletion. Reject cycles and unresolved
references. ID-keyed policy dictionaries allow a project to override one rule without
replacing an unrelated rule list.

Unknown keys fail validation except documented extension blocks. Reject missing
mapped fields, incompatible entities, invalid units, unrecognized parser/method IDs,
ambiguous aliases, duplicate IDs, invalid tolerances, unsupported model units and
source/output path collisions before publication. A source field absent from a table
is a mapping error; an optional measurement absent in a valid row is a data condition.

The first implementation will add JSON Schema and semantic validation. JSON parsing
alone does not validate GIS schemas, joins, units, engineering applicability or writer
capabilities. Schema discovery should generate a draft mapping without assuming the
meaning of unfamiliar fields.

### Configuration artifacts

Retain raw configuration, canonical resolved configuration, file/preset origins,
schema/policy/parser versions and a deterministic hash of the resolved content.
Record file/feature-service snapshot hashes and all derived decisions for the run.
Credentials and authentication tokens are never config values; use a separately
configured adapter authentication provider.

Implementation acceptance requires a second synthetic example with different
table/field names and normalized versus inline ports. The same core must produce
equivalent canonical parameters from both through configuration alone. Source-format
capability discovery must report unavailable drivers rather than silently dropping
a source.
