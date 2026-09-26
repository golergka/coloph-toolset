# Changelog

## 0.6.1

- Let applications render loaded document sections with domain-specific labels.
- Omit unchanged documents that are already present in invocation context while preserving the leaf documentation gate.

## 0.6.0

- Add authorized root discovery and validated hierarchical leaf dispatch.
- Add invocation-owned first-use state and a document loader with content fingerprints.
- Prevent concurrent calls from passing a first-use gate before its packet is ready.
- Repeat documentation when loaded content changes and retain an undelivered state after loader failure.
- Add post-execution documentation and terminal success signals.
- Add optional hierarchical Pydantic AI registration without changing direct registration or CLI paths.
- Add the standalone hierarchical order agent example. Keep all earlier examples.

## 0.5.0

- Add optional direct Pydantic AI tool registration for an explicit catalog selection.
- Derive registered signatures from canonical declarations and omit hidden arguments.
- Create typed application context per call, await asynchronous callbacks, and keep synchronous callbacks off the event loop.
- Preserve cancellation and internal failures while allowing applications to classify correctable errors as model retries.
- Add the standalone deterministic Pydantic AI agent example. Keep all earlier examples.

## 0.4.0

- Add an optional Starlette adapter for generated tool endpoints.
- Authenticate and authorize requests before application context creation.
- Share canonical argument normalization between HTTP and direct invocation.
- Map validation, authorization, user, and internal failures to distinct responses.
- Run synchronous invocation callbacks outside the HTTP event loop and await asynchronous callbacks once.
- Add the standalone authenticated HTTP tool service example. Keep all earlier examples.

## 0.3.1

- Run synchronous tool bodies without an active event loop.
- Continue to await asynchronous tools and callbacks exactly once from the synchronous entrypoint.

## 0.3.0

- Add `ToolRuntime` for validated synchronous and asynchronous calls.
- Add typed `ToolContext` values for application dependencies, call state, and resources.
- Add explicit resource acquisition and finalization with commit state.
- Add before-call and after-success hooks that receive the raw business result.
- Add lifecycle events and failures that identify body, finalization, and presentation errors.
- Preserve raw results while output limits report truncation.
- Add the standalone inventory runtime example. Keep both earlier examples.

## 0.2.3

- Keep the transport test's annotation imports resolvable on Python 3.14.
- Require the Python 3.11–3.14 and minimum-dependency checks before publication.
- Preserve the library contracts from 0.2.2.

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
