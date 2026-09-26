"""Validated sync and async tool invocation.

This file began as Coloph's ``core/tools/_invoke.py``. The public runtime keeps
the lifecycle ordering and turns Coloph authorization, references, database
transactions, workflow policies, and audit writes into explicit callbacks.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, Literal, Mapping, Protocol, TypeVar, cast

from ._ctx import ToolContext
from ._decorator import Tool, tool_for
from ._text import PresentedOutput, ToolOutput, canonical_output, limit_output


DependenciesT = TypeVar("DependenciesT")
StateT = TypeVar("StateT")
ResourceT = TypeVar("ResourceT")
HookStateT = TypeVar("HookStateT")

InvocationPhase = Literal[
    "validation",
    "availability",
    "acquisition",
    "resolution",
    "before_call",
    "body",
    "after_success",
    "finalization",
    "presentation",
    "complete",
]
InvocationStatus = Literal["started", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class Finalization:
    """Application-reported resource outcome.

    ``committed`` is true only when the application's durable work is final.
    Metadata is retained on the successful invocation result.
    """

    committed: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvocationEvent:
    """One observable lifecycle transition."""

    phase: InvocationPhase
    status: InvocationStatus
    committed: bool | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class InvocationResult:
    """Successful raw business data and its transport presentation."""

    raw: Any
    output: ToolOutput
    truncated: bool
    original_output_chars: int | None
    committed: bool | None
    metadata: Mapping[str, Any]


class InvocationError(RuntimeError):
    """A lifecycle failure with durable-state and cleanup information."""

    def __init__(
        self,
        phase: InvocationPhase,
        cause: BaseException,
        *,
        committed: bool | None = None,
        cleanup_error: BaseException | None = None,
    ) -> None:
        self.phase = phase
        self.cause = cause
        self.committed = committed
        self.cleanup_error = cleanup_error
        message = f"tool invocation failed during {phase}: {cause}"
        if cleanup_error is not None:
            message += f"; cleanup also failed: {cleanup_error}"
        super().__init__(message)


class ToolUnavailableError(RuntimeError):
    """Raised when an availability callback rejects a validated call."""


class ToolHook(Protocol[DependenciesT, StateT, ResourceT]):
    """Policy hook around the body. Hooks receive the raw result."""

    def before_call(
        self,
        tool: Tool,
        context: ToolContext[DependenciesT, StateT, ResourceT],
        arguments: dict[str, Any],
    ) -> Any | Awaitable[Any]: ...

    def after_success(
        self,
        tool: Tool,
        context: ToolContext[DependenciesT, StateT, ResourceT],
        arguments: dict[str, Any],
        result: Any,
        state: Any,
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


Acquire = Callable[[ToolContext[DependenciesT, StateT, ResourceT]], ResourceT | Awaitable[ResourceT]]
Finalize = Callable[
    [ToolContext[DependenciesT, StateT, ResourceT], ResourceT, BaseException | None],
    Finalization | Awaitable[Finalization],
]


@dataclass(frozen=True)
class ResourceLifecycle(Generic[DependenciesT, StateT, ResourceT]):
    """Explicit acquisition and finalization for one call-owned resource."""

    acquire: Acquire[DependenciesT, StateT, ResourceT]
    finalize: Finalize[DependenciesT, StateT, ResourceT]


_STATE_NOT_SUPPLIED = object()


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ToolRuntime(Generic[DependenciesT, StateT, ResourceT]):
    """Execute declared tools through one explicit application boundary."""

    def __init__(
        self,
        *,
        dependencies: DependenciesT,
        state_factory: Callable[[], StateT],
        resources: ResourceLifecycle[DependenciesT, StateT, ResourceT] | None = None,
        availability: Callable[
            [Tool, ToolContext[DependenciesT, StateT, ResourceT], dict[str, Any]], bool | Awaitable[bool]
        ]
        | None = None,
        resolve_arguments: Callable[
            [Tool, ToolContext[DependenciesT, StateT, ResourceT], dict[str, Any]],
            Mapping[str, Any] | Awaitable[Mapping[str, Any]],
        ]
        | None = None,
        hooks: tuple[ToolHook[DependenciesT, StateT, ResourceT], ...] = (),
        observer: Callable[[InvocationEvent], None | Awaitable[None]] | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.state_factory = state_factory
        self.resources = resources
        self.availability = availability
        self.resolve_arguments = resolve_arguments
        self.hooks = hooks
        self.observer = observer

    async def _emit(self, event: InvocationEvent) -> None:
        if self.observer is not None:
            await _await_if_needed(self.observer(event))

    async def invoke(
        self,
        declared: Tool | Callable[..., Any],
        arguments: Mapping[str, Any],
        *,
        state: StateT | object = _STATE_NOT_SUPPLIED,
        call: Callable[..., Any] | None = None,
        presenter: Callable[[ToolContext[DependenciesT, StateT, ResourceT], Any], ToolOutput] | None = None,
        max_output_chars: int | None = None,
    ) -> InvocationResult:
        """Validate and execute one call, awaiting async bodies and callbacks."""
        tool = declared if isinstance(declared, Tool) else tool_for(declared)
        body = call or tool.fn
        call_state = self.state_factory() if state is _STATE_NOT_SUPPLIED else cast(StateT, state)
        context = ToolContext[DependenciesT, StateT, ResourceT](self.dependencies, call_state)

        await self._emit(InvocationEvent("validation", "started"))
        try:
            validated = tool.validate_arguments(dict(arguments))
        except BaseException as exc:
            await self._emit(InvocationEvent("validation", "failed", error=exc))
            raise
        await self._emit(InvocationEvent("validation", "succeeded"))

        if self.availability is not None:
            await self._emit(InvocationEvent("availability", "started"))
            try:
                available = await _await_if_needed(self.availability(tool, context, dict(validated)))
                if not available:
                    raise ToolUnavailableError(f"{tool.fn.__qualname__} is unavailable")
            except BaseException as exc:
                await self._emit(InvocationEvent("availability", "failed", error=exc))
                raise
            await self._emit(InvocationEvent("availability", "succeeded"))

        resource: ResourceT | None = None
        acquired = False
        finalization = Finalization()
        if self.resources is not None:
            await self._emit(InvocationEvent("acquisition", "started"))
            try:
                resource = cast(ResourceT, await _await_if_needed(self.resources.acquire(context)))
                acquired = True
                context = context.with_resource(resource)
            except BaseException as exc:
                await self._emit(InvocationEvent("acquisition", "failed", error=exc))
                raise InvocationError("acquisition", exc) from exc
            await self._emit(InvocationEvent("acquisition", "succeeded"))

        phase: InvocationPhase = "resolution"
        primary_error: BaseException | None = None
        result: Any = None
        metadata: dict[str, Any] = {}
        try:
            if self.resolve_arguments is not None:
                await self._emit(InvocationEvent("resolution", "started"))
                validated = dict(await _await_if_needed(self.resolve_arguments(tool, context, dict(validated))))
                await self._emit(InvocationEvent("resolution", "succeeded"))

            phase = "before_call"
            hook_states: list[tuple[ToolHook[DependenciesT, StateT, ResourceT], Any]] = []
            if self.hooks:
                await self._emit(InvocationEvent("before_call", "started"))
                for hook in self.hooks:
                    hook_state = await _await_if_needed(hook.before_call(tool, context, dict(validated)))
                    hook_states.append((hook, hook_state))
                await self._emit(InvocationEvent("before_call", "succeeded"))

            phase = "body"
            await self._emit(InvocationEvent("body", "started"))
            result = await _await_if_needed(body(context, **validated))
            await self._emit(InvocationEvent("body", "succeeded"))

            phase = "after_success"
            if hook_states:
                await self._emit(InvocationEvent("after_success", "started"))
                for hook, hook_state in hook_states:
                    hook_metadata = await _await_if_needed(
                        hook.after_success(tool, context, dict(validated), result, hook_state)
                    )
                    metadata.update(hook_metadata)
                await self._emit(InvocationEvent("after_success", "succeeded"))
        except BaseException as exc:
            primary_error = exc
            status: InvocationStatus = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            await self._emit(InvocationEvent(phase, status, error=exc))

        cleanup_error: BaseException | None = None
        if acquired:
            assert self.resources is not None
            assert resource is not None
            try:
                await self._emit(InvocationEvent("finalization", "started"))
                finalization = cast(
                    Finalization,
                    await _await_if_needed(self.resources.finalize(context, resource, primary_error)),
                )
                metadata.update(finalization.metadata)
                await self._emit(InvocationEvent("finalization", "succeeded", committed=finalization.committed))
            except BaseException as exc:
                cleanup_error = exc
                await self._emit(InvocationEvent("finalization", "failed", error=exc))

        if primary_error is not None:
            if isinstance(primary_error, asyncio.CancelledError):
                if cleanup_error is not None:
                    primary_error.add_note(f"resource cleanup also failed: {cleanup_error}")
                raise primary_error
            raise InvocationError(
                phase,
                primary_error,
                committed=finalization.committed,
                cleanup_error=cleanup_error,
            ) from primary_error
        if cleanup_error is not None:
            raise InvocationError("finalization", cleanup_error, committed=finalization.committed) from cleanup_error

        await self._emit(InvocationEvent("presentation", "started", committed=finalization.committed))
        try:
            presentation = canonical_output(
                result,
                None if presenter is None else lambda raw: presenter(context, raw),
            )
            limited: PresentedOutput = limit_output(presentation, max_output_chars)
        except BaseException as exc:
            await self._emit(InvocationEvent("presentation", "failed", committed=finalization.committed, error=exc))
            raise InvocationError("presentation", exc, committed=finalization.committed) from exc
        await self._emit(InvocationEvent("presentation", "succeeded", committed=finalization.committed))
        await self._emit(InvocationEvent("complete", "succeeded", committed=finalization.committed))
        return InvocationResult(
            raw=result,
            output=limited.value,
            truncated=limited.truncated,
            original_output_chars=limited.original_chars,
            committed=finalization.committed,
            metadata=metadata,
        )

    def invoke_sync(
        self,
        declared: Tool | Callable[..., Any],
        arguments: Mapping[str, Any],
        **kwargs: Any,
    ) -> InvocationResult:
        """Run one call outside an event loop; async tools are awaited once."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("invoke_sync cannot run inside an active event loop; await invoke instead")
        return asyncio.run(self.invoke(declared, arguments, **kwargs))


__all__ = [
    "Finalization",
    "InvocationError",
    "InvocationEvent",
    "InvocationPhase",
    "InvocationResult",
    "ResourceLifecycle",
    "ToolHook",
    "ToolRuntime",
    "ToolUnavailableError",
]
