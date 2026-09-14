"""The `@tool` decorator + its lazy introspection model.

A `@tool`-decorated function carries a `Tool` description on `fn.tool`. The
function signature + per-parameter annotations + docstring are the single
source of truth. Introspection is LAZY and PER-PARAMETER so that return
annotations (which may reference TYPE_CHECKING-only names) are never evaluated.
"""

from __future__ import annotations
import __future__

import inspect
import sys
import types
import typing
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated, Any, Callable, Literal, Union, get_args, get_origin

_MISSING = inspect.Parameter.empty
_NONE_TYPE = type(None)

_SCALAR_TYPES = (bool, int, float, str)

DeclarationError = TypeError


@dataclass(frozen=True)
class ToolParam:
    """One named parameter of a tool function (excluding `ctx`)."""

    name: str
    annotation: Any
    type: type  # bool/int/float/str base scalar
    default: Any  # _MISSING if required
    description: str
    choices: tuple[Any, ...] | None  # all-str or all-int, from Literal[...]
    repeated: bool  # list[T] params -> repeatable flag
    nullable: bool
    metadata: tuple[Any, ...]

    @property
    def required(self) -> bool:
        return self.default is _MISSING


@dataclass
class Tool:
    """A tool description constructed by the `@tool` decorator."""

    fn: Callable[..., Any]
    model_hidden_args: tuple[str, ...] = ()
    metadata_types: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        original = inspect.unwrap(self.fn)
        if not inspect.isfunction(original):
            raise TypeError("a tool must be a Python function")
        if any(
            predicate(function)
            for predicate in (
                inspect.iscoroutinefunction,
                inspect.isasyncgenfunction,
                inspect.isgeneratorfunction,
            )
            for function in (self.fn, original)
        ):
            raise TypeError("async and generator tools are not supported in this release")
        if any(not isinstance(name, str) for name in self.model_hidden_args):
            raise TypeError("model_hidden_args must contain parameter names")
        if len(set(self.model_hidden_args)) != len(self.model_hidden_args):
            raise TypeError("model_hidden_args must not contain duplicates")
        if any(not isinstance(marker, type) for marker in self.metadata_types):
            raise TypeError("metadata_types must contain marker types")
        _validate_signature(self)

    @cached_property
    def params(self) -> tuple[ToolParam, ...]:
        return _compute_params(self)

    @cached_property
    def summary(self) -> str:
        doc = inspect.getdoc(self.fn) or ""
        return doc.split("\n", 1)[0].strip()

    @cached_property
    def description(self) -> str:
        return (inspect.getdoc(self.fn) or "").strip()

    def argument_model(
        self,
        *,
        include_hidden: bool = False,
        name: str | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> Any:
        from ._capability_help import tool_argument_model

        return tool_argument_model(
            self,
            include_hidden=include_hidden,
            name=name,
            descriptions=descriptions,
        )

    def validate_arguments(self, arguments: object, *, include_hidden: bool = False) -> dict[str, Any]:
        model = self.argument_model(include_hidden=include_hidden)
        parsed = model.model_validate(arguments)
        result: dict[str, Any] = parsed.model_dump()
        return result

    def json_schema(self, *, include_hidden: bool = False) -> dict[str, Any]:
        schema: dict[str, Any] = self.argument_model(include_hidden=include_hidden).model_json_schema()
        return schema


def annotation_with_description(fn: Callable[..., Any], p: inspect.Parameter, description: str) -> Any:
    """Return `p.annotation` with its user-facing description localized."""
    resolved = _resolve_annotation(fn, p)
    if not description:
        return resolved
    return _replace_annotation_description(resolved, description)


def tool(
    *,
    model_hidden_args: tuple[str, ...] = (),
    metadata_types: tuple[type, ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach a `Tool` description to `fn` as `fn.tool`.

    `model_hidden_args` keeps operator-only parameters in the CLI/API contract
    while omitting them from native model schemas; the tool body must provide a
    safe default or runtime resolver for each hidden argument.
    `metadata_types` names application-owned `Annotated` markers that are
    retained for adapters but excluded from validation and JSON Schema.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if hasattr(fn, "tool"):
            raise TypeError(f"{fn.__qualname__}: function already has a tool declaration")
        setattr(fn, "tool", Tool(fn, tuple(model_hidden_args), tuple(metadata_types)))
        return fn

    return decorator


def tool_for(fn: Callable[..., Any]) -> Tool:
    """Return the `Tool` attached to `fn` by `@tool`, failing loudly otherwise."""
    attached = getattr(fn, "tool", None)
    if not isinstance(attached, Tool):
        raise TypeError("{function} is not a @tool-decorated function".format(function=fn.__qualname__))
    return attached


def signature_with_tool_params(tool: Tool) -> inspect.Signature:
    """Return the callable signature with localized tool parameters."""
    sig = _function_signature(tool.fn)
    existing = {param.name: param for param in sig.parameters.values()}
    projected: list[inspect.Parameter] = []
    for param in tool.params:
        original = existing.get(param.name)
        if original is None:
            raise TypeError(
                "no callable parameter backs synthetic tool parameter {parameter!r}".format(parameter=param.name)
            )
        projected.append(original.replace(annotation=annotation_with_description(tool.fn, original, param.description)))
    context = list(sig.parameters.values())[:1]
    if not context:
        raise TypeError(
            "@tool {function!r} must take a context parameter as its first argument".format(
                function=tool.fn.__qualname__
            )
        )
    return sig.replace(parameters=[*context, *projected])


# ----------------------------------------------------------------------
# Lazy per-parameter introspection
# ----------------------------------------------------------------------


def _function_signature(fn: Callable[..., Any]) -> inspect.Signature:
    """Read parameters without evaluating unrelated deferred annotations."""
    original = inspect.unwrap(fn)
    string_annotations = original.__code__.co_flags & __future__.annotations.compiler_flag
    if sys.version_info >= (3, 14) and not string_annotations and getattr(original, "__annotate__", None):
        from annotationlib import Format

        return inspect.signature(fn, annotation_format=Format.STRING)
    return inspect.signature(fn)


def _validate_signature(tool: Tool) -> None:
    sig_params = list(_function_signature(tool.fn).parameters.values())
    if not sig_params:
        raise TypeError(
            "@tool {function!r} must take a context parameter as its first argument".format(
                function=tool.fn.__qualname__
            )
        )
    context = sig_params[0]
    if context.name != "ctx":
        raise TypeError(
            "@tool {function!r} first parameter must be named 'ctx', got {actual!r}".format(
                function=tool.fn.__qualname__, actual=context.name
            )
        )
    if (
        context.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        or context.default is not _MISSING
    ):
        raise TypeError("the injected context must be a required positional parameter")
    by_name = {parameter.name: parameter for parameter in sig_params[1:]}
    for parameter in by_name.values():
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise TypeError(
                "@tool {function!r} param {param!r} must be positional-or-keyword or "
                "keyword-only (no *args/**kwargs), got {kind}".format(
                    function=tool.fn.__qualname__,
                    param=parameter.name,
                    kind=parameter.kind.name,
                )
            )
    for name in tool.model_hidden_args:
        if name not in by_name:
            raise TypeError(f"model_hidden_args names unknown parameter {name!r}")
        if by_name[name].default is _MISSING:
            raise TypeError(f"model_hidden_args names required parameter {name!r}")


def _compute_params(tool: Tool) -> tuple[ToolParam, ...]:
    fn = tool.fn
    sig = _function_signature(fn)
    sig_params = list(sig.parameters.values())
    if not sig_params:
        raise TypeError(
            "@tool {function!r} must take a context parameter as its first argument".format(function=fn.__qualname__)
        )
    if sig_params[0].name != "ctx":
        raise TypeError(
            "@tool {function!r} first parameter must be named 'ctx', got {actual!r}".format(
                function=fn.__qualname__,
                actual=sig_params[0].name,
            )
        )

    params: list[ToolParam] = []
    for p in sig_params[1:]:
        if p.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            raise TypeError(
                "@tool {function!r} param {param!r} must be positional-or-keyword or keyword-only "
                "(no *args/**kwargs), got {kind}".format(
                    function=fn.__qualname__,
                    param=p.name,
                    kind=p.kind.name,
                )
            )
        resolved = _resolve_annotation(fn, p)
        base, choices, repeated, nullable, description, metadata = _describe_annotation(
            fn, p.name, resolved, tool.metadata_types
        )
        params.append(
            ToolParam(
                name=p.name,
                annotation=_validation_annotation(resolved, tool.metadata_types),
                type=base,
                default=p.default if p.default is not _MISSING else _MISSING,
                description=description,
                choices=choices,
                repeated=repeated,
                nullable=nullable,
                metadata=metadata,
            )
        )
    return tuple(params)


def _resolve_annotation(fn: Callable[..., Any], p: inspect.Parameter) -> Any:
    ann = p.annotation
    if ann is inspect.Parameter.empty:
        raise TypeError(
            "@tool {function!r} param {param!r} must be annotated".format(function=fn.__qualname__, param=p.name)
        )
    if not isinstance(ann, str):
        return ann
    # ``inspect.signature`` follows ``functools.wraps`` chains, so evaluate a
    # preserved string annotation in the same original function's globals.
    # A transport-neutral return adapter may live in another module.
    annotation_owner = inspect.unwrap(fn)
    eval_ns = {**vars(typing), **annotation_owner.__globals__}
    try:
        return eval(ann, eval_ns)
    except (NameError, AttributeError, SyntaxError, TypeError, ValueError) as exc:
        # EXPECTED_EXCEPTION: annotation eval failure re-raised as a clear TypeError naming tool+param.
        raise TypeError(
            "@tool {function!r} param {param!r}: could not evaluate annotation {annotation!r}: {error}".format(
                function=fn.__qualname__,
                param=p.name,
                annotation=ann,
                error=exc,
            )
        ) from exc


def _describe_annotation(
    fn: Callable[..., Any],
    param_name: str,
    resolved: Any,
    metadata_types: tuple[type, ...],
) -> tuple[type, tuple[Any, ...] | None, bool, bool, str, tuple[Any, ...]]:
    """Return the original Coloph parameter shape plus its generic metadata."""
    state: dict[str, Any] = {"description": "", "metadata": []}

    def consume(metadata: tuple[Any, ...]) -> None:
        from pydantic.fields import FieldInfo

        for meta in metadata:
            if isinstance(meta, str):
                if state["description"]:
                    raise TypeError(
                        "@tool {function!r} param {param!r}: duplicate description metadata".format(
                            function=fn.__qualname__,
                            param=param_name,
                        )
                    )
                state["description"] = meta
            elif isinstance(meta, FieldInfo):
                if meta.description:
                    if state["description"]:
                        raise TypeError(
                            f"@tool {fn.__qualname__!r} param {param_name!r}: duplicate description metadata"
                        )
                    state["description"] = meta.description
                _validate_constraint_metadata(fn, param_name, meta)
            elif type(meta) in metadata_types:
                state["metadata"].append(meta)
            else:
                _validate_constraint_metadata(fn, param_name, meta)

    # Peel outer Annotated / Optional wrappers, accumulating metadata.
    current = resolved
    nullable = False
    while True:
        if hasattr(current, "__metadata__"):
            consume(current.__metadata__)
            current = get_args(current)[0]
            continue
        origin = get_origin(current)
        if origin is Union or origin is types.UnionType:
            non_none = [a for a in get_args(current) if a is not _NONE_TYPE]
            if len(non_none) == 1:
                nullable = True
                current = non_none[0]
                continue
            raise TypeError(
                "@tool {function!r} param {param!r}: ambiguous union annotation {annotation!r}".format(
                    function=fn.__qualname__,
                    param=param_name,
                    annotation=resolved,
                )
            )
        break

    repeated = False
    choices: tuple[Any, ...] | None = None
    origin = get_origin(current)
    if origin is list:
        repeated = True
        element = get_args(current)[0]
        if hasattr(element, "__metadata__"):
            consume(element.__metadata__)
            element = get_args(element)[0]
        element_base = get_args(element)[0] if hasattr(element, "__metadata__") else element
        if element_base not in (str, int):
            raise TypeError(
                "@tool {function!r} param {param!r}: list element must be str or int, got {element!r}".format(
                    function=fn.__qualname__,
                    param=param_name,
                    element=element_base,
                )
            )
        base: type = element_base
    elif get_origin(current) is Literal:
        base, choices = _literal_base_and_choices(fn, param_name, get_args(current))
    elif isinstance(current, type) and current in _SCALAR_TYPES:
        base = current
    else:
        raise TypeError(
            "@tool {function!r} param {param!r}: unsupported annotation {annotation!r} "
            "(expected bool/int/float/str, Literal, or list[str|int])".format(
                function=fn.__qualname__,
                param=param_name,
                annotation=resolved,
            )
        )

    _validate_constraints_apply(fn, param_name, resolved, base, choices, repeated)
    return base, choices, repeated, nullable, state["description"], tuple(state["metadata"])


def _validate_constraint_metadata(fn: Callable[..., Any], param_name: str, meta: Any) -> None:
    from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen, MultipleOf
    from pydantic import Field, Strict
    from pydantic.fields import FieldInfo

    if isinstance(meta, (Ge, Gt, Le, Lt, MaxLen, MinLen, MultipleOf, Strict)):
        return
    if isinstance(meta, FieldInfo):
        defaults = Field()
        allowed = {"description", "title", "examples", "metadata"}
        changed = {
            key
            for key in FieldInfo.__slots__
            if not key.startswith("_") and key not in allowed and getattr(meta, key) != getattr(defaults, key)
        }
        if changed:
            raise TypeError(
                f"@tool {fn.__qualname__!r} param {param_name!r}: unsupported Field options {sorted(changed)}"
            )
        for nested in meta.metadata:
            _validate_constraint_metadata(fn, param_name, nested)
        return
    pattern_type = type(Field(pattern="").metadata[0])
    if type(meta) is pattern_type and set(vars(meta)) == {"pattern"}:
        return
    raise TypeError(f"@tool {fn.__qualname__!r} param {param_name!r}: unsupported metadata {meta!r}")


def _iter_constraint_metadata(annotation: Any) -> list[Any]:
    result: list[Any] = []
    if hasattr(annotation, "__metadata__"):
        result.extend(meta for meta in annotation.__metadata__ if not isinstance(meta, str))
        result.extend(_iter_constraint_metadata(get_args(annotation)[0]))
        return result
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType, list):
        for argument in get_args(annotation):
            if argument is not _NONE_TYPE:
                result.extend(_iter_constraint_metadata(argument))
    return result


def _validate_constraints_apply(
    fn: Callable[..., Any],
    param_name: str,
    resolved: Any,
    base: type,
    choices: tuple[Any, ...] | None,
    repeated: bool,
) -> None:
    from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen, MultipleOf
    from pydantic import Field, Strict
    from pydantic.fields import FieldInfo

    numeric = (Ge, Gt, Le, Lt, MultipleOf)
    length = (MinLen, MaxLen)
    pattern_type = type(Field(pattern="").metadata[0])
    metadata = _iter_constraint_metadata(resolved)
    expanded: list[Any] = []
    for item in metadata:
        expanded.extend(item.metadata if isinstance(item, FieldInfo) else (item,))
    for item in expanded:
        if isinstance(item, numeric):
            valid = not repeated and base in (int, float) and choices is None
        elif isinstance(item, length):
            valid = repeated or (base is str and choices is None)
        elif isinstance(item, Strict):
            valid = repeated or choices is None
        elif type(item) is pattern_type:
            valid = base is str and not repeated and choices is None
        else:
            continue
        if not valid:
            raise TypeError(
                f"@tool {fn.__qualname__!r} param {param_name!r}: constraint {item!r} does not apply to this type"
            )


def _validation_annotation(annotation: Any, metadata_types: tuple[type, ...]) -> Any:
    """Remove application markers while keeping the copied annotation contract."""
    if hasattr(annotation, "__metadata__"):
        base = _validation_annotation(get_args(annotation)[0], metadata_types)
        metadata = tuple(meta for meta in annotation.__metadata__ if type(meta) not in metadata_types)
        return Annotated[(base, *metadata)] if metadata else base
    origin = get_origin(annotation)
    if origin is list:
        return list.__class_getitem__(_validation_annotation(get_args(annotation)[0], metadata_types))
    if origin in (Union, types.UnionType):
        arguments = [_validation_annotation(arg, metadata_types) for arg in get_args(annotation)]
        combined = arguments[0]
        for argument in arguments[1:]:
            combined = combined | argument
        return combined
    return annotation


def _replace_annotation_description(annotation: Any, description: str) -> Any:
    return Annotated[(_without_annotation_descriptions(annotation), description)]


def _without_annotation_descriptions(annotation: Any) -> Any:
    if hasattr(annotation, "__metadata__"):
        from pydantic.fields import FieldInfo

        base = _without_annotation_descriptions(get_args(annotation)[0])
        metadata = [
            FieldInfo.merge_field_infos(meta, description=None) if isinstance(meta, FieldInfo) else meta
            for meta in annotation.__metadata__
            if not isinstance(meta, str)
        ]
        return Annotated[(base, *metadata)] if metadata else base

    origin = get_origin(annotation)
    if origin is list:
        return list.__class_getitem__(_without_annotation_descriptions(get_args(annotation)[0]))
    if origin in (Union, types.UnionType):
        arguments = [_without_annotation_descriptions(item) for item in get_args(annotation)]
        combined = arguments[0]
        for argument in arguments[1:]:
            combined = combined | argument
        return combined
    return annotation


def _literal_base_and_choices(
    fn: Callable[..., Any], param_name: str, values: tuple[Any, ...]
) -> tuple[type, tuple[Any, ...]]:
    """Literal[...] → (base type, choices). All-str or all-int, never mixed."""
    if all(isinstance(v, str) for v in values):
        return str, tuple(values)
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return int, tuple(values)
    raise TypeError(
        "@tool {function!r} param {param!r}: Literal values must be all-str or all-int, got {values!r}".format(
            function=fn.__qualname__,
            param=param_name,
            values=values,
        )
    )
