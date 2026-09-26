"""Optional direct and hierarchical Pydantic AI registration."""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from ._decorator import annotation_with_description
from ._index import ToolDefinition
from .hierarchical import (
    DocumentationLoader,
    HierarchicalInputError,
    HierarchicalState,
    HierarchicalToolAdapter,
    PreparedToolCall,
    root_dispatch_schema,
)

DepsT = TypeVar("DepsT")
ContextT = TypeVar("ContextT")

ContextFactory = Callable[[DepsT, ToolDefinition], ContextT | Awaitable[ContextT]]
Invoke = Callable[[ToolDefinition, ContextT, Mapping[str, Any]], Any | Awaitable[Any]]
InputError = Callable[[Exception], str | None]
Prepare = Callable[[Any, Any], Any]
StateFactory = Callable[[DepsT], HierarchicalState]
RequiredTools = Callable[[DepsT], Sequence[str]]
TerminalCallback = Callable[[DepsT, ToolDefinition, Any], Any | Awaitable[Any]]


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
    on_terminal: TerminalCallback[DepsT] | None = None

    def register(self, agent: Any, tools: Sequence[ToolDefinition]) -> list[Callable[..., Any]]:
        """Register exactly the supplied tools and return their wrappers."""
        return [self.register_tool(agent, tool) for tool in tools]

    def register_tool(self, agent: Any, tool: ToolDefinition) -> Callable[..., Any]:
        """Register one canonical declaration with Pydantic AI."""

        async def call(ctx: Any, **kwargs: Any) -> Any:
            context = cast(ContextT, await _call(self.context_factory, ctx.deps, tool))
            try:
                result = await _call(self.invoke, tool, context, kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = self.input_error(exc) if self.input_error is not None else None
                if message is None:
                    raise
                from pydantic_ai import ModelRetry

                raise ModelRetry(message) from exc
            if bool(getattr(tool.tool, "terminal", False)) and self.on_terminal is not None:
                await _call(self.on_terminal, ctx.deps, tool, result)
            return result

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


@dataclass(frozen=True)
class PydanticAIHierarchicalAdapter(Generic[DepsT, ContextT]):
    """Register one root tool per visible catalog root."""

    deps_type: type[DepsT]
    context_factory: ContextFactory[DepsT, ContextT]
    invoke: Invoke[ContextT]
    state_factory: StateFactory[DepsT]
    input_error: InputError | None = None
    document_loader: DocumentationLoader | None = None
    required_tools: RequiredTools[DepsT] | None = None
    on_terminal: TerminalCallback[DepsT] | None = None
    prepare: Prepare | None = None

    def register(self, agent: Any, tools: Sequence[ToolDefinition]) -> list[Callable[..., Any]]:
        """Register roots for exactly the supplied selection."""
        hierarchy = HierarchicalToolAdapter(tools, document_loader=self.document_loader)
        return [self._register_root(agent, hierarchy, root_name) for root_name in hierarchy.root_names]

    def _register_root(
        self,
        agent: Any,
        hierarchy: HierarchicalToolAdapter,
        root_name: str,
    ) -> Callable[..., Any]:
        def root_tool(ctx: Any, **payload: Any) -> Any:
            """Browse or execute an allowed command below this tool group."""
            return None

        async def call(ctx: Any, **payload: Any) -> Any:
            state = self.state_factory(ctx.deps)
            required = self.required_tools(ctx.deps) if self.required_tools is not None else ()
            try:
                prepared = hierarchy.prepare(root_name, payload, state, required_tool_ids=required)
                if not isinstance(prepared, PreparedToolCall):
                    return prepared
                context = cast(ContextT, await _call(self.context_factory, ctx.deps, prepared.tool))
                result = await _call(self.invoke, prepared.tool, context, prepared.arguments)
                completed = hierarchy.complete(prepared, result, state)
                if completed.terminal and self.on_terminal is not None:
                    await _call(self.on_terminal, ctx.deps, prepared.tool, completed.result)
                return completed.result
            except asyncio.CancelledError:
                raise
            except HierarchicalInputError as exc:
                from pydantic_ai import ModelRetry

                raise ModelRetry(str(exc)) from exc
            except Exception as exc:
                message = self.input_error(exc) if self.input_error is not None else None
                if message is None:
                    raise
                from pydantic_ai import ModelRetry

                raise ModelRetry(message) from exc

        root_tool.__name__ = root_name
        root_tool.__qualname__ = root_name
        root_tool.__doc__ = hierarchy.root_description(root_name)
        return register_pydantic_ai_tool(
            agent,
            deps_type=self.deps_type,
            tool_name=root_name,
            description=root_tool.__doc__,
            param_descriptions={"payload": "The root command envelope."},
            public_param_names={"payload"},
            fn=root_tool,
            call=call,
            prepare=self.prepare,
            parameters_json_schema=root_dispatch_schema(),
        )


__all__ = ["PydanticAIAdapter", "PydanticAIHierarchicalAdapter", "register_pydantic_ai_tool"]
