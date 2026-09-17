# Changelog

## 0.2.2

- Let CLI adapters decode transport values before canonical validation.
- Expose the parser validation hook for application transport adapters.
- Preserve the duplicate dotted ID diagnostic when sibling paths collide.

## 0.2.1

- Permit uv's generated `.gitignore` file in the release artifact directory.
- Preserve the catalog and CLI contracts from 0.2.0.

## 0.2.0

- Add catalogs, nested and flattened groups, deferred loading, and deterministic selections.
- Share local validation between exact resolution and full catalog construction.
- Reject command/group collisions, invalid paths, alias collisions, and conflicting CLI flags.
- Generate CLIs with configurable executable names and explicit application integration callbacks.
- Preserve defaults and nullable booleans; validate constraints after parsing.
- Replace list defaults with supplied repeated flags and retain boolean negation during semantic round trips.
- Reject explicit nulls and empty lists when CLI flags cannot represent them.
- Keep help available for functions without docstrings and hide excluded deferred group headers.
- Add the task CLI example and keep the shipping example working against this release.

The declaration API remains compatible with 0.1. Execution remains application-owned.
The package remains on 0.x; new adapters can require documented migrations.

## 0.1.0

- Add lazy function declarations and parameter inspection.
- Generate JSON Schema and argument models from the same retained annotations.
- Preserve supported constraints, nullable required inputs, defaults, and hidden argument projections.
- Reject unsupported signatures and metadata before application dispatch.
- Add the standalone shipping-quote example and clean wheel smoke coverage.

Execution remains application-owned in this release.
