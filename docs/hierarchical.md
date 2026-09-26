# Hierarchical discovery and dispatch

`HierarchicalToolAdapter` exposes an explicit tool selection as root groups.
It does not read a global catalog. Group help, path resolution, generated
examples, validation, and dispatch all use the supplied selection. An omitted
tool cannot appear in discovery or become callable through an alias.

Call `prepare(root_name, payload, state)` with an invocation-owned
`HierarchicalState`. The result is one of these values:

- A dictionary with `documentation` and `command_invoked=False`. No tool body ran.
- A `PreparedToolCall` with the selected definition and normalized arguments.

After application dispatch and resource finalization, call
`complete(prepared, result, state)`. It returns the presented result and a
terminal flag. Pass `succeeded=False` after failed finalization. A failed call,
an error result, or a documentation packet cannot become terminal.

## Root payload

The root payload has an optional relative `command_path` and an `arguments`
object. An omitted path browses the root. A group path with empty arguments
browses that group. A leaf path uses the leaf's canonical Pydantic argument
model. Invalid paths report the first failed segment and visible children.

`root_dispatch_schema()` returns the provider-facing envelope schema. Native
exposure does not modify the catalog or CLI parser.

## First-use documents

Declare documents with `@tool(required_documents=(...))` and supply a
`DocumentationLoader`. Each returned `Documentation` has a name, text, and
content fingerprint.

The default first leaf call returns documentation without executing the body.
The next valid call can execute. State belongs to one invocation. A changed
fingerprint causes the document to be delivered again. A loader exception or
an omitted document records no delivery.

The state lock covers document loading, packet construction, and the delivery
decision. Concurrent first-use calls cannot prepare business execution while
the first packet is still being built.

`post_execution_first_use_documentation=True` is for audited operations that
are safe to execute before showing generated help. Explicit
`first_use_guidance` still runs before that first execution.

## Pydantic AI

Install `coloph-toolset[pydantic-ai]` and use
`PydanticAIHierarchicalAdapter`. Supply callbacks for application context,
invocation, state, and optional terminal handling. Supply `required_tools`
only when their full documentation is already in the effective model prompt.

The application owns authorization, resources, persistence, provider choice,
and the action that ends an invocation. The library has no filesystem layout,
workflow vocabulary, database, or built-in document source.
