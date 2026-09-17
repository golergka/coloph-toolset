# Catalogs and generated CLIs

A `Leaf` mounts one decorated function at a named path. A `GroupNode` owns an ordered sequence of leaves and groups.
The root name does not contribute a path segment. A group with `flatten=True` contributes its children at its parent path.

`build_index(root)` creates a catalog with one `ToolDefinition` per leaf.
Each definition retains its path, function declaration, leaf, and effective exposure.
Its dotted path is its stable identity. A leaf alias controls its native name.

The catalog rejects invalid names, duplicate paths, command/group collisions, conflicting aliases, and conflicting CLI flags.
Names contain letters, digits, underscores, and hyphens. The first character must be a letter or digit.
Global alias checks include the underscore path used by hierarchical discovery.

## Deferred groups

A `DeferredGroup` owns a loader and a cached child sequence.
The first successful load stores that sequence. Concurrent readers share the same successful load.
A failed load leaves the group unloaded, so the next access can retry.

`resolve_tool(root, path)` loads the requested path and applies the same local declaration checks as full catalog construction.
It does not load unrelated named groups. Full construction additionally checks global alias collisions.
Flattened groups must expose their children to identify sibling paths; they cannot provide isolation from sibling inspection.

## Exposure and selection

A child inherits its parent's exposure unless the child declares its own exposure.
A declared exposure replaces the inherited mapping. Exposure controls presentation; the application still owns authorization.

`catalog.select_tools(patterns)` accepts exact dotted paths and `prefix.*` patterns.
It returns sorted, deduplicated definitions and rejects every unmatched pattern.
`selection.excluding(...)` replaces wildcard patterns with surviving exact identities.

`CompiledToolTree(selection)` contains only selected definitions.
Its help, path resolution, and alias resolution cannot restore excluded commands.
For codec dispatch, construct `ToolCatalog` from the same selected definitions.
For CLI generation, pass their exact IDs through `allowed_dotted`.
That set is authoritative, including for commands without default CLI exposure.

## Parsing and execution

Create a `ToolArgumentParser` with the desired executable name.
Pass it to `build_parser_from_tree(root, surface, parser=parser)`.
For demand-driven help and parsing, also pass `requested_path`.
The root and unvisited group help use deferred headers without loading their children.

Parsing attaches the resolved definition as `args._bound` and validates values through the declaration's canonical model.
`kwargs_from_args(bound.tool, args)` returns validated arguments, including independent defaults.
The application supplies context, authorizes the call, and executes the function.
The library does not acquire resources or execute a parsed call.

Omitted arguments preserve declared defaults. Repeated flags replace the default list and append values in input order.
Required booleans require a flag. Boolean flags accept `true`, `false`, `1`, `0`, `yes`, or `no`.
Long boolean flags also have a generated `--no-...` form.
`Cli` metadata supplies a primary flag, aliases, or a metavar.

## Semantic calls

`ToolCall` retains a definition and explicitly supplied arguments.
`encode_cli_call` and `decode_cli_call` accept an `executable` parameter, which defaults to `tool`.
Decoding validates constraints while retaining only explicit arguments.
Encoding uses assignment syntax for values that start with a minus sign.

Explicit nulls and empty lists have no named-flag representation in this release.
Encoding those values fails explicitly. Omit the argument to use its declared default.
`ToolCallTemplate` is documentation syntax and must not be dispatched.

## Application integration

Applications can extend the node dataclasses with their own policy fields.
The optional `bind(TreeEntry)` callback receives the leaf, resolved exposure, and ancestors from root to parent.
Both exact resolution and full construction call that same binder.
The binder owns application declaration checks and returns a `ToolDefinition` or compatible record.
An application tool wrapper exposes its public declaration as `declaration` and supplies compatible `params` and descriptions.

`leaf_defaults` adds runtime namespace defaults. `leaf_visible` controls the default presentation.
An exact `allowed_dotted` selection takes precedence over default visibility.
`command_metavar` controls the root help label. The library supplies no application-specific root flags.

## Compatibility

The project remains on `0.x`. Releases can change public interfaces before `1.0`.
Version `0.2.0` preserves the `0.1` declaration API and its shipping example.
Applications that adopt the new CLI must account for canonical validation and replacement of repeated defaults.
