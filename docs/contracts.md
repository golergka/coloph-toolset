# Argument contract

## Declaration lifetime

`@tool()` attaches a `Tool` at `fn.tool` and returns the original function.
`tool_for(fn)` reads that declaration. `Tool(fn, ...)` also works without a decorator for application integration.

Signature structure is validated at construction. Parameter annotations are resolved lazily on first inspection.
Schema construction validates defaults and rejects unsupported metadata. A malformed declaration raises `DeclarationError`, the exported name for `TypeError`.
Applications must build their argument models during startup before they accept calls.

Annotations are trusted developer code. String annotations resolve in the original function's global namespace, including through `functools.wraps`.
Function-local forward names must be available in that namespace or use evaluated annotations.
Context and return annotations are not resolved. Schema inspection never calls the body.
Do not mutate a function's signature, annotations, defaults, or declaration after registration.

## Supported signatures and types

- Plain synchronous functions with named external arguments. Positional-only external arguments and variadic arguments are rejected.
- A required injected context named `ctx` as the first positional parameter.
- `bool`, `int`, `float`, and `str`.
- Homogeneous string or integer `Literal` choices.
- Lists of supported scalar or literal values. Nested lists and nullable list elements are not supported in 0.1.
- Nullable forms of those types, including a nullable list.
- `Annotated` descriptions and the constraint metadata listed next.

Async functions, generators, bound methods, arbitrary callable objects, unconstrained `Any`, mixed unions, and nested models are not supported in 0.1.
These declarations fail explicitly. Async execution belongs to a later milestone.

## Metadata

One string in `Annotated` supplies the argument description.
Supported Pydantic `Field` options are `description`, `title`, `examples`, `gt`, `ge`, `lt`, `le`, `multiple_of`, `min_length`, `max_length`, `pattern`, and `strict`.
Direct `annotated_types.Gt`, `Ge`, `Lt`, `Le`, `MultipleOf`, `MinLen`, and `MaxLen` metadata also works, as does `pydantic.Strict`.
Constraints must apply to the annotated type. Duplicate descriptions and unsupported metadata raise `DeclarationError`.
Literal choices cannot carry additional value constraints in 0.1. Their containing list can carry list constraints.

Function signatures own defaults. `Field` defaults, default factories, aliases, serializers, validators, schema overrides, and exclusion controls are rejected.
This keeps validation, parameter names, and exported schemas aligned.

Applications can pass exact marker classes through `metadata_types=(MyMarker,)`.
The declaration retains these markers in `ToolParam.metadata` and removes them from the validation annotation.
Markers are descriptive data only. They cannot implement Pydantic validators or change JSON Schema through this interface.

## Validation and defaults

`Tool.validate_arguments(object, include_hidden=False)` returns normalized values in a new dictionary.
It raises `pydantic.ValidationError` for invalid input. `error.errors()` provides field locations, codes, messages, and input values.
Applications own error presentation and any redaction of submitted values.

Pydantic 2's default coercion rules apply. Numeric strings can become numbers. Accepted boolean strings can become booleans.
Strings do not automatically accept numbers. `Field(strict=True)` disables coercion for its annotated type.
This policy is identical through `validate_arguments()` and the generated argument model.

Omission uses the declared default. Explicit null requires a nullable annotation.
A nullable argument without a default is still required. Unknown arguments are forbidden.
Defaults are validated even for hidden arguments. Validation supplies independent list values, so one call cannot mutate another call's validated defaults.

Hidden arguments are absent from the default schema and rejected as external input.
`include_hidden=True` creates the full contract for trusted application callers.
The restricted result contains only visible arguments. The application owns how it supplies private values during execution.

## Public inspection API

- `Tool.params`: ordered `ToolParam` records with the validation annotation, default, description, scalar type, choices, repeated/nullable flags, and application metadata.
- `ToolParam.required`: whether the signature has no default.
- `Tool.description` and `Tool.summary`: full docstring and first line, or an empty string.
- `Tool.argument_model(include_hidden=False, name=None, descriptions=None)`: a Pydantic model for that projection.
- `Tool.json_schema(include_hidden=False)`: a fresh validation schema.
- `annotation_with_description(fn, parameter, description)`: a description override that retains the original annotation for adapter signatures.

Description overrides accept real parameter names only. They do not change validation.
Treat generated model classes and parameter metadata as read-only.

## Boundaries

Importing `coloph_toolset` does not import Pydantic until annotation/schema work needs it.
The package does not replace stdout or read environment files.
It has no KB names, reference registry, credentials, database connections, workflow state, or agent framework dependency.

The [Pydantic field contract](https://docs.pydantic.dev/latest/concepts/fields/) describes the underlying constraint and default behavior.
