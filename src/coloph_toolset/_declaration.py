"""Lazy tool descriptions and one schema-backed argument contract."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, TypeVar

from ._annotations import (
    DeclarationError,
    describe,
    function_signature,
    resolve_annotation,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

F = TypeVar("F", bound=Callable[..., Any])
_MISSING = inspect.Parameter.empty


@dataclass(frozen=True)
class Parameter:
    """One external argument, retaining both its annotation and adapter metadata."""

    name: str
    annotation: Any
    validation_annotation: Any
    default: Any
    description: str
    type: type
    choices: tuple[Any, ...] | None
    repeated: bool
    nullable: bool
    metadata: tuple[Any, ...]

    @property
    def required(self) -> bool:
        return self.default is _MISSING


@dataclass(frozen=True)
class Tool:
    """Describe a synchronous function without wrapping or executing its body.

    Set context_parameter=None for a function without an injected context.
    Hidden parameters remain available only through an explicitly full projection.
    """

    fn: Callable[..., Any]
    context_parameter: str | None = "ctx"
    hidden_args: tuple[str, ...] = ()
    metadata_types: tuple[type, ...] = ()
    _models: dict[Any, type[BaseModel]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not inspect.isfunction(self.fn):
            raise DeclarationError("a tool must be a Python function")
        original = inspect.unwrap(self.fn)
        if any(
            predicate(function)
            for predicate in (
                inspect.iscoroutinefunction,
                inspect.isasyncgenfunction,
                inspect.isgeneratorfunction,
            )
            for function in (self.fn, original)
        ):
            raise DeclarationError("async and generator tools are not supported in this release")
        if self.context_parameter is not None and (
            not isinstance(self.context_parameter, str) or not self.context_parameter.isidentifier()
        ):
            raise DeclarationError("context_parameter must be a parameter name or None")
        if not isinstance(self.hidden_args, tuple) or any(
            not isinstance(name, str) for name in self.hidden_args
        ):
            raise DeclarationError("hidden_args must be a tuple of parameter names")
        if len(set(self.hidden_args)) != len(self.hidden_args):
            raise DeclarationError("hidden_args must not contain duplicates")
        if not isinstance(self.metadata_types, tuple) or any(
            not isinstance(marker, type) for marker in self.metadata_types
        ):
            raise DeclarationError("metadata_types must be a tuple of application marker types")
        # Signature structure is cheap and eager. Annotation work remains lazy.
        self._external_signature()

    def _external_signature(self) -> tuple[inspect.Parameter, ...]:
        params = tuple(function_signature(self.fn).parameters.values())
        if self.context_parameter is not None:
            if not params or params[0].name != self.context_parameter:
                raise DeclarationError(
                    f"{self.fn.__qualname__}: first parameter must be {self.context_parameter!r}"
                )
            if (
                params[0].kind
                not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                or params[0].default is not _MISSING
            ):
                raise DeclarationError(
                    "the injected context must be a required positional parameter"
                )
            params = params[1:]
        for param in params:
            if param.kind not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise DeclarationError(f"{param.name}: tool arguments must be named parameters")
            if param.name.startswith("_"):
                raise DeclarationError(f"{param.name}: leading underscores are unsupported")
        by_name = {param.name: param for param in params}
        for name in self.hidden_args:
            if name not in by_name:
                raise DeclarationError(f"hidden argument {name!r} is not an external parameter")
            if by_name[name].default is _MISSING:
                raise DeclarationError(f"hidden argument {name!r} requires a safe default")
        return params

    @cached_property
    def parameters(self) -> tuple[Parameter, ...]:
        result = []
        for param in self._external_signature():
            annotation = resolve_annotation(self.fn, param)
            info = describe(
                annotation,
                markers=self.metadata_types,
                location=f"{self.fn.__qualname__}.{param.name}",
            )
            result.append(
                Parameter(
                    param.name,
                    annotation,
                    info.validated,
                    _MISSING if param.default is _MISSING else deepcopy(param.default),
                    info.description,
                    info.scalar,
                    info.choices,
                    info.repeated,
                    info.nullable,
                    info.metadata,
                )
            )
        return tuple(result)

    @cached_property
    def description(self) -> str:
        return (inspect.getdoc(self.fn) or "").strip()

    @property
    def summary(self) -> str:
        return self.description.split("\n", 1)[0]

    def argument_model(
        self,
        *,
        include_hidden: bool = False,
        name: str | None = None,
        descriptions: Mapping[str, str] | None = None,
    ) -> type[BaseModel]:
        """Return the canonical Pydantic model for one argument projection."""
        from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, create_model
        from pydantic.errors import PydanticInvalidForJsonSchema, PydanticUserError
        from pydantic_core import SchemaError

        overrides = dict(descriptions or {})
        unknown = set(overrides) - {param.name for param in self.parameters}
        if unknown or any(not isinstance(value, str) for value in overrides.values()):
            raise DeclarationError(f"invalid description overrides: {sorted(unknown)}")
        key = (include_hidden, name, tuple(sorted(overrides.items())))
        if key in self._models:
            return self._models[key]
        fields: dict[str, Any] = {}
        try:
            for param in self.parameters:
                if not param.required:
                    TypeAdapter(param.validation_annotation).validate_python(
                        deepcopy(param.default)
                    )
                if param.name in self.hidden_args and not include_hidden:
                    continue
                fields[param.name] = (
                    param.validation_annotation,
                    Field(
                        default=... if param.required else deepcopy(param.default),
                        description=overrides.get(param.name, param.description),
                    ),
                )
            model: type[BaseModel] = create_model(
                name or f"{self.fn.__name__}_arguments",
                __config__=ConfigDict(
                    extra="forbid",
                    validate_default=True,
                    protected_namespaces=(),
                    revalidate_instances="always",
                ),
                **fields,
            )
            model.model_json_schema()
        except (
            ValidationError,
            SchemaError,
            PydanticUserError,
            PydanticInvalidForJsonSchema,
        ) as exc:
            raise DeclarationError(
                f"{self.fn.__qualname__}: invalid argument contract: {exc}"
            ) from exc
        self._models[key] = model
        return model

    def validate_arguments(
        self, arguments: object, *, include_hidden: bool = False
    ) -> dict[str, Any]:
        """Normalize arguments or raise Pydantic ValidationError. Never execute the body."""
        model = self.argument_model(include_hidden=include_hidden)
        parsed = model.model_validate(arguments)
        return {name: getattr(parsed, name) for name in model.model_fields}

    def json_schema(self, *, include_hidden: bool = False) -> dict[str, Any]:
        """Export a fresh validation schema for the requested projection."""
        return self.argument_model(include_hidden=include_hidden).model_json_schema()


def tool(
    *,
    context_parameter: str | None = "ctx",
    hidden_args: tuple[str, ...] = (),
    metadata_types: tuple[type, ...] = (),
) -> Callable[[F], F]:
    """Attach a Tool as fn.tool and preserve the original callable and its typing."""

    def decorate(fn: F) -> F:
        if hasattr(fn, "tool"):
            raise DeclarationError(f"{fn.__qualname__}: function already has a tool declaration")
        fn.__dict__["tool"] = Tool(fn, context_parameter, hidden_args, metadata_types)
        return fn

    return decorate


def tool_for(fn: Callable[..., Any]) -> Tool:
    """Read the Tool attached by the public decorator."""
    declaration = getattr(fn, "tool", None)
    if not isinstance(declaration, Tool):
        raise DeclarationError("function is not decorated with coloph_toolset.tool")
    return declaration
