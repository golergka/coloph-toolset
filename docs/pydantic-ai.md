# Pydantic AI adapter

Install the optional adapter with `uv add "coloph-toolset[pydantic-ai]"`.
Versions 1.93 through the current 1.x release are supported.

`PydanticAIAdapter` registers exactly the supplied `ToolDefinition` values.
Schemas use the canonical declaration and omit hidden arguments. For every
accepted call, the adapter creates a new application context and invokes the
tool once. It awaits asynchronous callbacks and runs synchronous callbacks in
a worker thread. Calls do not share a global lock.

Pydantic AI handles schema validation and returns invalid calls to the model as
retry prompts before application code runs. The optional `input_error`
callback can classify correctable application exceptions as `ModelRetry`.
Unclassified failures and cancellation propagate.

The application owns the model, provider, dependencies, authorization,
resources, persistence, budgets, transcripts, and result presentation. Core
package imports do not load Pydantic AI.

`PydanticAIHierarchicalAdapter` registers one tool for each visible root in an
explicit selection. Its `state_factory` returns invocation-owned
`HierarchicalState`. The adapter uses the same context and invocation
callbacks as direct registration. Correctable path and leaf-validation errors
become model retries.

The optional `on_terminal` callback runs only after a terminal leaf succeeds.
The application decides how that signal ends its model loop.
