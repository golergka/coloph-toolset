"""Pydantic AI registration copied from Coloph's native tool adapter.

The framework is imported only when registration occurs, so core, CLI, and
HTTP users do not need the optional dependency.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from ._decorator import annotation_with_description
from ._index import ToolDefinition

DepsT = TypeVar("DepsT")
ContextT = TypeVar("ContextT")

ContextFactory = Callable[[DepsT, ToolDefinition], ContextT | Awaitable[ContextT]]
Invoke = Callable[[ToolDefinition, ContextT, Mapping[str, Any]], Any | Awaitable[Any]]
InputError = Callable[[Exception], str | None]
Prepare = Callable[[Any, Any], Any]


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    if inspect.iscoroutinefunction(callback):
        return await callback(*args)
    result = await asyncio.to_thread(callback, *args)
    return await result if inspect.isawaitable(result) else result


def register_pydantic_ai_tool(
    agent: Any,
    *,
    deps_type: type[Any],
    tool_name: str,
    description: str,
    param_descriptions: Mapping[str, str],
    public_param_names: set[str] | frozenset[str],
    fn: Callable[..., Any],
    call: Callable[..., Any],
    tool_signature: inspect.Signature | None = None,
    prepare: Prepare | None = None,
    parameters_json_schema: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """Project one application dispatcher onto a Pydantic AI tool."""
    from pydantic_ai import RunContext

    sig = tool_signature or inspect.signature(fn)
    sig_params = list(sig.parameters.values())
    if not sig_params:
        raise TypeError(f"{tool_name}: tool callable must have a leading context parameter")
    localized_params = [
        parameter.replace(annotation=annotation_with_description(fn, parameter, param_descriptions[parameter.name]))
        if parameter.name in param_descriptions
        else parameter
        for parameter in sig_params[1:]
        if parameter.name in public_param_names
    ]
    projected = sig.replace(
        parameters=[
            inspect.Parameter(
                "ctx",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=RunContext[deps_type],  # type: ignore[valid-type]
            ),
            *localized_params,
        ]
    )
    functools.wraps(fn)(call)
    call.__name__ = tool_name
    call.__qualname__ = tool_name
    call.__doc__ = description
    setattr(call, "__signature__", projected)
    call.__annotations__ = {
        name: parameter.annotation
        for name, parameter in projected.parameters.items()
        if parameter.annotation is not inspect.Parameter.empty
    }
    if sig.return_annotation is not inspect.Signature.empty:
        call.__annotations__["return"] = sig.return_annotation

    if parameters_json_schema is not None or prepare is not None:

        def prepare_tool(ctx: Any, definition: Any) -> Any:
            if parameters_json_schema is not None:
                from dataclasses import replace

                definition = replace(definition, parameters_json_schema=parameters_json_schema)
            return prepare(ctx, definition) if prepare is not None else definition

        agent.tool(prepare=prepare_tool)(call)
    else:
        agent.tool(call)
    return call


@dataclass(frozen=True)
class PydanticAIAdapter(Generic[DepsT, ContextT]):
    """Register a selected catalog with typed per-call application context."""

    deps_type: type[DepsT]
    context_factory: ContextFactory[DepsT, ContextT]
    invoke: Invoke[ContextT]
    input_error: InputError | None = None
    prepare: Prepare | None = None

    def register(self, agent: Any, tools: Sequence[ToolDefinition]) -> list[Callable[..., Any]]:
        """Register exactly the supplied tools and return their wrappers."""
        return [self.register_tool(agent, tool) for tool in tools]

    def register_tool(self, agent: Any, tool: ToolDefinition) -> Callable[..., Any]:
        """Register one canonical declaration with Pydantic AI."""

        async def call(ctx: Any, **kwargs: Any) -> Any:
            context = cast(ContextT, await _call(self.context_factory, ctx.deps, tool))
            try:
                return await _call(self.invoke, tool, context, kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = self.input_error(exc) if self.input_error is not None else None
                if message is None:
                    raise
                from pydantic_ai import ModelRetry

                raise ModelRetry(message) from exc

        public_names = {
            parameter.name for parameter in tool.params if parameter.name not in tool.tool.model_hidden_args
        }
        return register_pydantic_ai_tool(
            agent,
            deps_type=self.deps_type,
            tool_name=tool.agent_name,
            description=tool.description,
            param_descriptions={parameter.name: parameter.description for parameter in tool.params},
            public_param_names=public_names,
            fn=tool.callable_fn,
            tool_signature=tool.callable_signature,
            call=call,
            prepare=self.prepare,
        )


__all__ = ["PydanticAIAdapter", "register_pydantic_ai_tool"]
