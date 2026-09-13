"""Public contracts for declarations, schemas, validation, and projections."""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args, get_origin

import pytest
from annotated_types import Gt, MinLen
from pydantic import Field, Strict, ValidationError

from coloph_toolset import DeclarationError, Tool, annotation_with_description, tool, tool_for

if TYPE_CHECKING:

    class UnavailableContext: ...

    class UnavailableReturn: ...


DEFAULT_LABELS = ["standard"]


@tool(model_hidden_args=("internal",))
def quote(
    ctx: UnavailableContext,
    count: Annotated[int, "Count", Field(gt=0)],
    destination: Annotated[str | None, "Destination"],
    labels: Annotated[list[str], "Labels"] = DEFAULT_LABELS,
    enabled: bool | None = None,
    internal: bool = False,
) -> UnavailableReturn:
    raise AssertionError("inspection and validation must not execute the body")


def test_original_function_and_lazy_return_annotations():
    declaration = tool_for(quote)
    assert declaration.fn is quote
    assert list(declaration.argument_model().model_fields) == [
        "count",
        "destination",
        "labels",
        "enabled",
    ]
    assert "ctx" not in declaration.json_schema()["properties"]
    assert get_origin(declaration.params[0].annotation) is Annotated
    assert get_args(declaration.params[0].annotation)[0] is int
    assert declaration.params[0].description == "Count"


def test_nullable_required_and_defaults():
    declaration = tool_for(quote)
    with pytest.raises(ValidationError) as missing:
        declaration.validate_arguments({"count": 2})
    assert missing.value.errors()[0]["loc"] == ("destination",)
    assert missing.value.errors()[0]["type"] == "missing"
    assert declaration.validate_arguments({"count": "2", "destination": None}) == {
        "count": 2,
        "destination": None,
        "labels": ["standard"],
        "enabled": None,
    }
    schema = declaration.json_schema()
    assert set(schema["required"]) == {"count", "destination"}
    assert schema["properties"]["count"]["exclusiveMinimum"] == 0
    assert {part["type"] for part in schema["properties"]["destination"]["anyOf"]} == {
        "string",
        "null",
    }


@pytest.mark.parametrize(
    "arguments,code,location",
    [
        ({"count": 0, "destination": None}, "greater_than", ("count",)),
        ({"count": "bad", "destination": None}, "int_parsing", ("count",)),
        ({"count": 1, "destination": None, "unknown": 2}, "extra_forbidden", ("unknown",)),
        ({"count": 1, "destination": None, "internal": False}, "extra_forbidden", ("internal",)),
        ({"count": 1, "destination": 42}, "string_type", ("destination",)),
    ],
)
def test_invalid_arguments(arguments, code, location):
    with pytest.raises(ValidationError) as error:
        tool_for(quote).validate_arguments(arguments)
    assert error.value.errors()[0]["type"] == code
    assert error.value.errors()[0]["loc"] == location


def test_hidden_projection_and_mutable_defaults_are_isolated():
    declaration = tool_for(quote)
    one = declaration.validate_arguments({"count": 1, "destination": "AR"})
    one["labels"].append("changed")
    two = declaration.validate_arguments({"count": 1, "destination": "AR"})
    assert two["labels"] == ["standard"]
    assert (
        declaration.validate_arguments({"count": 1, "destination": None, "internal": True}, include_hidden=True)[
            "internal"
        ]
        is True
    )
    assert "internal" in declaration.json_schema(include_hidden=True)["properties"]
    assert "internal" not in declaration.json_schema()["properties"]


def test_model_and_validator_normalize_identically():
    declaration = tool_for(quote)
    values = {"count": "3", "destination": "AR", "enabled": "false"}
    assert declaration.argument_model().model_validate(values).model_dump() == (declaration.validate_arguments(values))


def test_boolean_and_missing_docstring():
    @tool()
    def flag(ctx, enabled: bool):
        return enabled

    declaration = tool_for(flag)
    assert declaration.description == declaration.summary == ""
    with pytest.raises(ValidationError):
        declaration.validate_arguments({})
    assert declaration.validate_arguments({"enabled": False}) == {"enabled": False}


def test_constraint_kinds_and_list_elements():
    @tool()
    def constraints(
        ctx,
        count: Annotated[int, Gt(0)],
        names: Annotated[list[Annotated[str, MinLen(2)]], Field(min_length=1)],
        code: Annotated[str, Field(pattern="^[A-Z]+$")],
        size: Literal[1, 2],
        weight: Annotated[float, Field(ge=0, le=10, multiple_of=0.5)],
        exact: Annotated[int, Strict()],
    ):
        return count

    declaration = tool_for(constraints)
    good = {"count": 1, "names": ["ab"], "code": "AR", "size": 1, "weight": 1.5, "exact": 1}
    assert declaration.validate_arguments(good) == good
    for bad in (
        {"names": ["a"]},
        {"names": []},
        {"code": "ar"},
        {"size": 3},
        {"weight": 0.3},
        {"exact": "1"},
    ):
        with pytest.raises(ValidationError):
            declaration.validate_arguments(good | bad)


@pytest.mark.parametrize("annotation", [dict[str, int], Any, int | str, list[list[str]], list[str | None]])
def test_unsupported_annotations(annotation):
    def fn(ctx, value):
        return value

    fn.__annotations__["value"] = annotation
    with pytest.raises(DeclarationError):
        Tool(fn).json_schema()


@pytest.mark.parametrize(
    "metadata",
    [
        object(),
        Field(alias="other"),
        Field(default=2),
        Field(default_factory=list),
        Field(exclude=True),
        Field(validate_default=False),
        Field(json_schema_extra={"type": "string"}),
    ],
)
def test_unknown_or_contract_changing_metadata_is_rejected(metadata):
    def fn(ctx, value):
        return value

    fn.__annotations__["value"] = Annotated[int, metadata]
    with pytest.raises(DeclarationError):
        Tool(fn).argument_model()


def test_invalid_default_including_hidden_is_a_declaration_error():
    def fn(ctx, value: Annotated[int, Field(gt=0)] = 0):
        return value

    for hidden in ((), ("value",)):
        with pytest.raises(DeclarationError, match="invalid argument contract"):
            Tool(fn, model_hidden_args=hidden).json_schema()


def test_none_default_does_not_make_nonnullable_type_nullable():
    def fn(ctx, value: int = None):
        return value

    with pytest.raises(DeclarationError):
        Tool(fn).json_schema()


def test_wrapped_annotations_resolve_in_original_globals():
    def original(ctx, value: Annotated[int, "An integer"]) -> UnavailableReturn:
        return value

    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        return original(*args, **kwargs)

    declaration = Tool(wrapped)
    assert declaration.validate_arguments({"value": "3"}) == {"value": 3}
    changed = annotation_with_description(original, inspect.signature(original).parameters["value"], "Changed")
    assert changed.__metadata__ == ("Changed",)


def test_description_override_preserves_constraints():
    declaration = tool_for(quote)
    model = declaration.argument_model(descriptions={"count": "New help"})
    assert model.model_json_schema()["properties"]["count"]["description"] == "New help"
    with pytest.raises(ValidationError):
        model.model_validate({"count": 0, "destination": None})
    assert declaration.json_schema()["properties"]["count"]["description"] == "Count"
    with pytest.raises(DeclarationError):
        declaration.argument_model(descriptions={"missing": "help"})


def test_application_metadata_is_explicit_and_cannot_run_validation_hooks():
    @dataclass(frozen=True)
    class Reference:
        kind: str

        def __get_pydantic_core_schema__(self, source, handler):
            raise AssertionError("application marker must not become a schema extension")

    def fn(ctx, value):
        return value

    marker = Reference("record")
    fn.__annotations__["value"] = Annotated[str, "Reference", marker]
    with pytest.raises(DeclarationError):
        Tool(fn).argument_model()
    declaration = Tool(fn, metadata_types=(Reference,))
    assert declaration.params[0].metadata == (marker,)
    assert declaration.validate_arguments({"value": "demo"}) == {"value": "demo"}


@pytest.mark.parametrize(
    "factory",
    [
        lambda: lambda: None,
        lambda: lambda other: None,
        lambda: lambda ctx, *args: None,
        lambda: lambda ctx, **kwargs: None,
        lambda: lambda ctx, value, /: None,
        lambda: lambda *, ctx: None,
        lambda: lambda ctx=None: None,
    ],
)
def test_invalid_signatures_fail_eagerly(factory):
    with pytest.raises(DeclarationError):
        Tool(factory())


@pytest.mark.parametrize("hidden", [("missing",), ("value",), ("ctx",), ("value", "value")])
def test_invalid_hidden_declarations(hidden):
    def fn(ctx, value: int):
        return value

    with pytest.raises(DeclarationError):
        Tool(fn, model_hidden_args=hidden)


def test_async_and_generators_fail_eagerly():
    async def async_fn(ctx, value: str):
        return value

    def generator(ctx):
        yield 1

    async def async_generator(ctx):
        yield 1

    for fn in (async_fn, generator, async_generator):
        with pytest.raises(DeclarationError, match="async and generator"):
            Tool(fn)


def test_unannotated_parameter_fails_lazily():
    declaration = Tool(lambda ctx, value: value)
    with pytest.raises(DeclarationError, match="must be annotated"):
        declaration.argument_model()


def test_duplicate_descriptions_and_inapplicable_constraints_fail():
    def fn(ctx, value):
        return value

    for annotation in (Annotated[str, "one", "two"], Annotated[str, Field(gt=0)]):
        fn.__annotations__["value"] = annotation
        with pytest.raises(DeclarationError):
            Tool(fn).argument_model()


def test_validation_rejects_corrupted_model_instances():
    declaration = tool_for(quote)
    instance = declaration.argument_model().model_construct(count=0, destination=None)
    with pytest.raises(ValidationError):
        declaration.validate_arguments(instance)
    with pytest.raises(ValidationError):
        declaration.argument_model().model_validate(instance)


def test_schema_mutation_does_not_change_later_exports():
    declaration = tool_for(quote)
    first = declaration.json_schema()
    first["properties"].clear()
    assert "count" in declaration.json_schema()["properties"]


def test_public_decorator_cannot_replace_existing_metadata():
    with pytest.raises(DeclarationError, match="already has"):
        tool()(quote)
    with pytest.raises(DeclarationError, match="not a @tool-decorated"):
        tool_for(lambda: None)


def test_empty_description_remains_an_explicit_schema_field():
    def fn(ctx, value: Annotated[str, ""]):
        return value

    assert Tool(fn).json_schema()["properties"]["value"]["description"] == ""


def test_signature_description_override_replaces_field_description():
    def fn(ctx, count: Annotated[int, Field(description="Old", gt=0)] | None):
        return count

    assert Tool(fn).params[0].description == "Old"
    annotation = annotation_with_description(fn, inspect.signature(fn).parameters["count"], "New")
    fn.__annotations__["count"] = annotation
    declaration = Tool(fn)
    schema = declaration.json_schema()
    assert schema["properties"]["count"]["description"] == "New"
    assert declaration.validate_arguments({"count": None}) == {"count": None}
    with pytest.raises(ValidationError):
        declaration.validate_arguments({"count": 0})


def test_constraints_on_literal_values_fail_as_declaration_errors():
    def fn(ctx, value: Annotated[Literal[1, 2], Field(strict=True)]):
        return value

    with pytest.raises(DeclarationError):
        Tool(fn).argument_model()
