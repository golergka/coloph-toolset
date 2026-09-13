"""Inspect parameter annotations without evaluating context or return annotations."""

from __future__ import annotations
import __future__

import inspect
import sys
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union, get_args, get_origin


class DeclarationError(TypeError):
    """A function cannot supply a supported, unambiguous tool contract."""


def function_signature(fn: Callable[..., Any]) -> inspect.Signature:
    """Avoid eager annotation evaluation on Python 3.14 as well as older Python."""
    original = inspect.unwrap(fn)
    string_annotations = original.__code__.co_flags & __future__.annotations.compiler_flag
    if (
        sys.version_info >= (3, 14)
        and not string_annotations
        and getattr(original, "__annotate__", None)
    ):
        from annotationlib import Format

        return inspect.signature(fn, annotation_format=Format.STRING)
    return inspect.signature(fn)


def resolve_annotation(fn: Callable[..., Any], parameter: inspect.Parameter) -> Any:
    annotation = parameter.annotation
    if annotation is inspect.Parameter.empty:
        raise DeclarationError(f"{fn.__qualname__}.{parameter.name}: missing annotation")
    if isinstance(annotation, str):
        owner = inspect.unwrap(fn)
        namespace = {**vars(typing), **owner.__globals__}
        try:
            # Annotations are trusted developer code, not caller input.
            annotation = eval(annotation, namespace)
        except (NameError, AttributeError, SyntaxError, TypeError, ValueError) as exc:
            raise DeclarationError(
                f"{fn.__qualname__}.{parameter.name}: cannot resolve {annotation!r}: {exc}"
            ) from exc
    return annotation


def annotation_with_description(
    fn: Callable[..., Any], parameter: inspect.Parameter, description: str
) -> Any:
    """Replace a description while preserving the complete original annotation."""
    annotation = resolve_annotation(fn, parameter)
    if not description:
        return annotation
    return Annotated[_without_descriptions(annotation), description]


def _without_descriptions(annotation: Any) -> Any:
    from pydantic.fields import FieldInfo

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        base = _without_descriptions(args[0])
        metadata = [
            FieldInfo.merge_field_infos(item, description=None)
            if isinstance(item, FieldInfo)
            else item
            for item in args[1:]
            if not isinstance(item, str)
        ]
        return Annotated[(base, *metadata)] if metadata else base
    if origin is list:
        return list.__class_getitem__(_without_descriptions(args[0]))
    if origin in (Union, types.UnionType):
        combined = _without_descriptions(args[0])
        for arg in args[1:]:
            combined = combined | _without_descriptions(arg)
        return combined
    return annotation


@dataclass(frozen=True)
class Annotation:
    validated: Any
    scalar: type
    choices: tuple[Any, ...] | None = None
    repeated: bool = False
    nullable: bool = False
    description: str = ""
    metadata: tuple[Any, ...] = ()


def describe(annotation: Any, *, markers: tuple[type, ...], location: str) -> Annotation:
    """Retain constraints and remove only explicitly recognized descriptive metadata."""
    from pydantic import Field
    from pydantic.fields import FieldInfo

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        inner = describe(args[0], markers=markers, location=location)
        descriptions = [inner.description] if inner.description else []
        constraints: list[Any] = []
        metadata = list(inner.metadata)
        for item in args[1:]:
            if isinstance(item, str):
                descriptions.append(item)
            elif type(item) in markers:
                # Application markers never change validation or JSON Schema.
                metadata.append(item)
            elif isinstance(item, FieldInfo):
                defaults = Field()
                allowed = {"description", "title", "examples"}
                changed = {
                    key
                    for key in FieldInfo.__slots__
                    if not key.startswith("_")
                    and key not in allowed | {"metadata"}
                    and getattr(item, key) != getattr(defaults, key)
                }
                if changed:
                    raise DeclarationError(
                        f"{location}: unsupported Field options {sorted(changed)}; "
                        "function signatures own names and defaults"
                    )
                if item.description:
                    descriptions.append(item.description)
                for constraint in item.metadata:
                    _check_constraint(constraint, inner, location)
                constraints.append(item)
            else:
                _check_constraint(item, inner, location)
                constraints.append(item)
        if len(descriptions) > 1:
            raise DeclarationError(f"{location}: duplicate description metadata")
        description = descriptions[0] if descriptions else ""
        validated = Annotated[(inner.validated, *constraints)] if constraints else inner.validated
        return Annotation(
            validated,
            inner.scalar,
            inner.choices,
            inner.repeated,
            inner.nullable,
            description,
            tuple(metadata),
        )
    if origin in (Union, types.UnionType):
        remaining = [arg for arg in args if arg is not type(None)]
        if len(remaining) != 1 or len(args) != 2:
            raise DeclarationError(f"{location}: only nullable unions are supported")
        inner = describe(remaining[0], markers=markers, location=location)
        return Annotation(
            inner.validated | None,
            inner.scalar,
            inner.choices,
            inner.repeated,
            True,
            inner.description,
            inner.metadata,
        )
    if origin is list:
        if len(args) != 1:
            raise DeclarationError(f"{location}: list requires an element type")
        inner = describe(args[0], markers=markers, location=location)
        if inner.repeated or inner.nullable:
            raise DeclarationError(f"{location}: nested or nullable list elements are unsupported")
        return Annotation(
            list.__class_getitem__(inner.validated),
            inner.scalar,
            inner.choices,
            True,
            False,
            inner.description,
            inner.metadata,
        )
    if origin is Literal:
        if not args or not (
            all(type(value) is str for value in args) or all(type(value) is int for value in args)
        ):
            raise DeclarationError(
                f"{location}: Literal must contain only strings or only integers"
            )
        return Annotation(annotation, type(args[0]), tuple(args))
    if annotation in (bool, int, float, str):
        return Annotation(annotation, annotation)
    raise DeclarationError(f"{location}: unsupported annotation {annotation!r}")


def _check_constraint(value: Any, inner: Annotation, location: str) -> None:
    from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen, MultipleOf
    from pydantic import Field, Strict

    numeric = (Ge, Gt, Le, Lt, MultipleOf)
    length = (MinLen, MaxLen)
    general_type = type(Field(pattern="").metadata[0])
    if isinstance(value, numeric):
        supported = not inner.repeated and inner.scalar in (int, float) and inner.choices is None
    elif isinstance(value, length):
        supported = inner.repeated or (inner.scalar is str and inner.choices is None)
    elif isinstance(value, Strict):
        supported = inner.repeated or inner.choices is None
    elif type(value) is general_type and set(vars(value)) == {"pattern"}:
        supported = inner.scalar is str and not inner.repeated and inner.choices is None
    else:
        raise DeclarationError(f"{location}: unsupported annotation metadata {value!r}")
    if not supported:
        raise DeclarationError(f"{location}: constraint {value!r} does not apply to this type")
