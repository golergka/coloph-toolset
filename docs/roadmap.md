# Extraction milestones

The [Coloph epic](https://github.com/golergka/coloph/issues/1332) tracks six sequential releases.

1. Declarations, canonical argument validation, and the shipping-quote example.
2. Catalogs, lazy trees, CLI generation, and a task CLI example.
3. Invocation, typed context, results, sync/async execution, and a runtime example.
4. An optional HTTP adapter and a standalone service example.
5. An optional Pydantic AI adapter and a deterministic agent example.
6. Hierarchical discovery, documentation gates, terminal controls, and a hierarchical agent example.

Each milestone adds a project under `examples/` and preserves every previous project.
CI runs all examples against the current package. Pins to obsolete releases do not replace current-version coverage.

Version 0.1 does not promise CLI or HTTP parity in Coloph before those adapters migrate.
Coloph retains reference resolution, authorization, workflow policy, resource management, and output behavior until their respective extraction milestones.

The 0.x public API can change in a minor release. Release notes must describe required migrations.
Patch releases preserve documented contracts. The package must remain useful at every milestone.
