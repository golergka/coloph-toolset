# Extraction milestones

The extraction plan tracks six sequential releases.

1. Declarations, canonical argument validation, and the shipping-quote example.
2. Catalogs, lazy trees, CLI generation, and a task CLI example.
3. Invocation, typed context, results, sync/async execution, and a runtime example. Released in 0.3.
4. An optional HTTP adapter and a standalone service example. Released in 0.4.
5. An optional Pydantic AI adapter and a deterministic agent example. Released in 0.5.
6. Hierarchical discovery, documentation gates, terminal controls, and a hierarchical agent example. Released in 0.6.

Each milestone adds a project under `examples/` and preserves every previous project.
CI runs all examples against the current package. Pins to obsolete releases do not replace current-version coverage.

Early releases exposed only the capabilities completed at that milestone.
Applications continue to own reference resolution, authorization, workflow policy, business resources, and application-specific output behavior.

The 0.x public API can change in a minor release. Release notes must describe required migrations.
Patch releases preserve documented contracts. The package must remain useful at every milestone.
