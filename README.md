# umdev

- Author: Lucas HT Nguyen
- Contact: [GitHub](https://github.com/lucashtnguyen)
- Date: 2026-10-14

Status: Specification draft for review. The model-development library is not implemented.

## Summary

umdev prepares utility GIS and engineering evidence for hydraulic model development.
It selects the best available data, records assignments and uncertainty, assembles
coherent network parameters, and supports QA of the resulting model.

The first model target is SWMM sewer and drainage networks. GIS field names, tables,
units, domains, port descriptions, lifecycle rules and engineering policies are
project configuration. Support for another utility's GIS must not require copying
the core or adding municipality-specific branches.

| Repository | Responsibility |
| --- | --- |
| umdev | Source adapters, evidence reconciliation, project policies, prepared parameters, scenario plans and engineering QA |
| hymo | Read and interpret model documents |
| SHMM | Validate explicit edit instructions, preserve unrelated content and publish audited model files |

Review the [development specification](docs/specification.md), the
[configuration contract](docs/configuration.md), and the
[synthetic project configuration](examples/config/utility_project.example.json).
The [SHMM handoff and manual QA contract](docs/handoff.md) defines required preview
and written-model diffs and their ownership.

All umdev interfaces and configuration keys in these documents are proposals.

A direction-label mismatch can be a QA finding when topology, role and diameter
identify one pipe end. Missing required values or unresolved competing assignments
remain explicit holds. A valid file or a positive pipe grade does not establish
calibration.

Project GIS, actual asset IDs, model files, review decisions and credentials belong
in private project workspaces. Public examples and tests must use synthetic data;
translating real coordinates alone is not sufficient anonymization.

## Developer README

The proposed environment is Python 3.12 with callable APIs and an edit-and-run
VS Code workflow. A CLI is optional future work. No umdev installation or execution
command is available yet.

Current repository contents:

- `docs/specification.md`: scope, workflow, parameter contracts, QA and acceptance tests.
- `docs/configuration.md`: reusable configuration semantics and validation rules.
- `examples/config/utility_project.example.json`: proposed JSON configuration using
  fictional field names and paths; it is not executable against existing data.
- `LICENSE`: existing BSD 3-Clause license.

Implementation will add a versioned configuration schema, Python package, pytest
suite and synthetic fixtures. Dependency versions and GIS drivers must be verified
when implementation starts. Planned tests cover configuration-only changes between
different source schemas, source preservation, provisional assignments, complete
junction profiles and reproducible model publication.

Unresolved work is listed under implementation phases and review decisions in the
specification. Documentation checks do not substitute for runtime acceptance tests.
