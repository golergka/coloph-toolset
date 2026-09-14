"""Schema-backed argument contract for tool declarations."""

from __future__ import annotations

import inspect
from copy import deepcopy
from typing import Any

from ._decorator import Tool, signature_with_tool_params


def tool_argument_model(
    tool: Tool,
    *,
    include_hidden: bool = False,
    name: str | None = None,
    descriptions: dict[str, str] | None = None,
) -> Any:
    """Build the argument contract from one canonical tool signature."""
    from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, create_model
    from pydantic.errors import PydanticInvalidForJsonSchema, PydanticUserError
    from pydantic_core import SchemaError

    params = tool.params
    hidden = () if include_hidden else tool.model_hidden_args
    public_names = {param.name for param in params if param.name not in hidden}
    tool_params = {param.name: param for param in params}
    overrides = descriptions or {}
    unknown = set(overrides) - set(tool_params)
    if unknown or any(not isinstance(value, str) for value in overrides.values()):
        raise TypeError(f"invalid description overrides: {sorted(unknown)}")
    fields: dict[str, Any] = {}
    try:
        for parameter in list(signature_with_tool_params(tool).parameters.values())[1:]:
            tool_param = tool_params[parameter.name]
            default = ... if parameter.default is inspect.Parameter.empty else deepcopy(parameter.default)
            if default is not ...:
                TypeAdapter(tool_param.annotation).validate_python(deepcopy(default))
            if parameter.name not in public_names:
                continue
            fields[parameter.name] = (
                tool_param.annotation,
                Field(
                    default=default,
                    description=overrides.get(parameter.name, tool_param.description),
                ),
            )
        model = create_model(
            name or f"{tool.fn.__name__}_arguments",
            __config__=ConfigDict(
                extra="forbid",
                validate_default=True,
                protected_namespaces=(),
                revalidate_instances="always",
            ),
            **fields,
        )
        model.model_json_schema()
        return model
    except (
        ValidationError,
        SchemaError,
        PydanticUserError,
        PydanticInvalidForJsonSchema,
    ) as exc:
        raise TypeError(f"{tool.fn.__qualname__}: invalid argument contract: {exc}") from exc
