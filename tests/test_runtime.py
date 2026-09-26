from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from coloph_toolset import (
    Finalization,
    InvocationError,
    ResourceLifecycle,
    ToolContext,
    ToolRuntime,
    ToolUnavailableError,
    tool,
)


@dataclass
class State:
    calls: int = 0


def runtime(**kwargs: Any) -> ToolRuntime[dict[str, Any], State, object]:
    return ToolRuntime(dependencies={"service": "inventory"}, state_factory=State, **kwargs)


def test_validation_precedes_availability_resources_and_body() -> None:
    events: list[str] = []

    @tool()
    def reserve(ctx: ToolContext[dict[str, Any], State, object], quantity: int) -> int:
        events.append("body")
        return quantity

    tools = runtime(
        availability=lambda *_args: events.append("availability") or True,
        resources=ResourceLifecycle(
            acquire=lambda _ctx: events.append("acquire") or object(),
            finalize=lambda *_args: Finalization(),
        ),
    )

    with pytest.raises(ValidationError):
        tools.invoke_sync(reserve, {"quantity": "wrong"})

    assert events == []


def test_unavailable_call_does_not_acquire_or_execute() -> None:
    events: list[str] = []

    @tool()
    def reserve(ctx: ToolContext[dict[str, Any], State, object]) -> None:
        events.append("body")

    tools = runtime(
        availability=lambda *_args: False,
        resources=ResourceLifecycle(
            acquire=lambda _ctx: events.append("acquire") or object(),
            finalize=lambda *_args: Finalization(),
        ),
    )

    with pytest.raises(ToolUnavailableError):
        tools.invoke_sync(reserve, {})

    assert events == []


def test_success_lifecycle_resolves_then_runs_hooks_finalizes_and_presents() -> None:
    events: list[object] = []
    resource = object()

    @tool()
    def reserve(ctx: ToolContext[dict[str, Any], State, object], quantity: int) -> dict[str, int]:
        assert ctx.resource is resource
        events.append(("body", quantity))
        return {"quantity": quantity}

    class Hook:
        def before_call(self, _tool: object, _ctx: object, arguments: dict[str, Any]) -> str:
            events.append(("before", arguments.copy()))
            return "hook-state"

        def after_success(
            self,
            _tool: object,
            _ctx: object,
            arguments: dict[str, Any],
            result: object,
            state: object,
        ) -> dict[str, str]:
            events.append(("after", arguments.copy(), result, state))
            return {"hook": "done"}

    def acquire(_ctx: object) -> object:
        events.append("acquire")
        return resource

    def resolve(_tool: object, _ctx: object, arguments: dict[str, Any]) -> dict[str, int]:
        events.append(("resolve", arguments.copy()))
        return {"quantity": arguments["quantity"] + 1}

    def finalize(_ctx: object, actual: object, error: BaseException | None) -> Finalization:
        events.append(("finalize", actual, error))
        return Finalization(committed=True, metadata={"transaction": "committed"})

    def present(_ctx: object, result: dict[str, int]) -> str:
        events.append(("present", result))
        return f"reserved {result['quantity']}"

    tools = runtime(
        resources=ResourceLifecycle(acquire=acquire, finalize=finalize),
        resolve_arguments=resolve,
        hooks=(Hook(),),
    )
    result = tools.invoke_sync(reserve, {"quantity": "2"}, presenter=present)

    assert result.raw == {"quantity": 3}
    assert result.output == "reserved 3"
    assert result.committed is True
    assert result.metadata == {"hook": "done", "transaction": "committed"}
    assert events == [
        "acquire",
        ("resolve", {"quantity": 2}),
        ("before", {"quantity": 3}),
        ("body", 3),
        ("after", {"quantity": 3}, {"quantity": 3}, "hook-state"),
        ("finalize", resource, None),
        ("present", {"quantity": 3}),
    ]


def test_body_failure_finalizes_and_retains_the_primary_error() -> None:
    body_error = RuntimeError("body failed")
    cleanup_error = OSError("rollback failed")
    finalized_with: list[BaseException | None] = []

    @tool()
    def reserve(ctx: ToolContext[dict[str, Any], State, object]) -> None:
        raise body_error

    def finalize(_ctx: object, _resource: object, error: BaseException | None) -> Finalization:
        finalized_with.append(error)
        raise cleanup_error

    tools = runtime(resources=ResourceLifecycle(acquire=lambda _ctx: object(), finalize=finalize))

    with pytest.raises(InvocationError) as caught:
        tools.invoke_sync(reserve, {})

    assert caught.value.phase == "body"
    assert caught.value.cause is body_error
    assert caught.value.cleanup_error is cleanup_error
    assert finalized_with == [body_error]


def test_finalization_failure_after_body_is_not_reported_as_body_failure() -> None:
    @tool()
    def reserve(ctx: ToolContext[dict[str, Any], State, object]) -> str:
        return "done"

    cleanup_error = RuntimeError("commit failed")
    tools = runtime(
        resources=ResourceLifecycle(
            acquire=lambda _ctx: object(),
            finalize=lambda *_args: (_ for _ in ()).throw(cleanup_error),
        )
    )

    with pytest.raises(InvocationError) as caught:
        tools.invoke_sync(reserve, {})

    assert caught.value.phase == "finalization"
    assert caught.value.cause is cleanup_error


def test_presentation_failure_reports_whether_work_committed() -> None:
    @tool()
    def reserve(ctx: ToolContext[dict[str, Any], State, object]) -> dict[str, bool]:
        return {"reserved": True}

    tools = runtime(
        resources=ResourceLifecycle(
            acquire=lambda _ctx: object(),
            finalize=lambda *_args: Finalization(committed=True),
        )
    )

    def fail(_ctx: object, _result: object) -> str:
        raise RuntimeError("format failed")

    with pytest.raises(InvocationError) as caught:
        tools.invoke_sync(reserve, {}, presenter=fail)

    assert caught.value.phase == "presentation"
    assert caught.value.committed is True


def test_async_tool_is_awaited_once_and_cancellation_finalizes() -> None:
    calls: list[str] = []
    finalized: list[BaseException | None] = []

    @tool()
    async def wait(ctx: ToolContext[dict[str, Any], State, object]) -> None:
        calls.append("body")
        await asyncio.Event().wait()

    async def finalize(_ctx: object, _resource: object, error: BaseException | None) -> Finalization:
        finalized.append(error)
        return Finalization(committed=False)

    async def scenario() -> None:
        task = asyncio.create_task(
            runtime(resources=ResourceLifecycle(acquire=lambda _ctx: object(), finalize=finalize)).invoke(wait, {})
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert calls == ["body"]
    assert len(finalized) == 1
    assert isinstance(finalized[0], asyncio.CancelledError)


def test_context_state_is_fresh_unless_shared_explicitly() -> None:
    @tool()
    def count(ctx: ToolContext[dict[str, Any], State, object]) -> int:
        ctx.state.calls += 1
        return ctx.state.calls

    tools = runtime()
    assert tools.invoke_sync(count, {}).output == 1
    assert tools.invoke_sync(count, {}).output == 1

    shared = State()
    assert tools.invoke_sync(count, {}, state=shared).output == 1
    assert tools.invoke_sync(count, {}, state=shared).output == 2


@pytest.mark.parametrize("value", ["abcdef", 123456, 12.3456, True, None, {"answer": "abcdef"}])
def test_limits_are_explicit_and_raw_business_data_is_retained(value: object) -> None:
    @tool()
    def result(ctx: ToolContext[dict[str, Any], State, object]) -> object:
        return value

    invoked = runtime().invoke_sync(result, {}, max_output_chars=4)

    assert invoked.raw == value
    assert invoked.original_output_chars is not None
    assert invoked.truncated == (invoked.original_output_chars > 4)
    if invoked.truncated:
        assert isinstance(invoked.output, str)
        assert len(invoked.output) == 4


def test_default_runtime_does_not_capture_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    @tool()
    def legacy(ctx: ToolContext[dict[str, Any], State, object]) -> None:
        print("legacy output")

    result = runtime().invoke_sync(legacy, {})

    assert result.output is None
    assert capsys.readouterr().out == "legacy output\n"


def test_sync_entrypoint_rejects_an_active_event_loop_without_calling_body() -> None:
    calls: list[str] = []

    @tool()
    async def sample(ctx: ToolContext[dict[str, Any], State, object]) -> None:
        calls.append("body")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="active event loop"):
            runtime().invoke_sync(sample, {})

    asyncio.run(scenario())
    assert calls == []


def test_concurrent_calls_have_independent_context_and_resources() -> None:
    resources: list[object] = []

    async def acquire(_ctx: object) -> object:
        resource = object()
        resources.append(resource)
        return resource

    @tool()
    async def sample(ctx: ToolContext[dict[str, Any], State, object], value: int) -> tuple[int, int, object]:
        ctx.state.calls += 1
        await asyncio.sleep(0)
        return value, ctx.state.calls, ctx.resource

    tools = runtime(
        resources=ResourceLifecycle(
            acquire=acquire,
            finalize=lambda *_args: Finalization(committed=True),
        )
    )

    async def scenario() -> list[object]:
        return list(await asyncio.gather(*(tools.invoke(sample, {"value": value}) for value in range(4))))

    results = asyncio.run(scenario())
    assert [item.raw[:2] for item in results] == [(0, 1), (1, 1), (2, 1), (3, 1)]
    assert [item.raw[2] for item in results] == resources
    assert len({id(resource) for resource in resources}) == 4
