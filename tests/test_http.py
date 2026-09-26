from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Annotated, Any

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from annotated_types import Ge
from starlette.applications import Starlette
from starlette.testclient import TestClient

from coloph_toolset import GroupNode, Leaf, ToolContext, build_index, tool
from coloph_toolset.http import (
    HttpAuthenticationError,
    HttpUserError,
    StarletteToolAdapter,
)


@tool()
def quote(
    ctx: ToolContext[dict[str, Any], None, object],
    quantity: Annotated[int, Ge(1)],
    note: str | None = None,
) -> dict[str, Any]:
    return {"quantity": quantity, "note": note, "authority": ctx.dependencies["authority"]}


def _tool():
    root = GroupNode("root", "Tools", (Leaf("quote", quote),), exposure={"http": None}, flatten=True)
    return build_index(root).by_dotted["quote"]


def _adapter(**overrides: Any) -> StarletteToolAdapter[str, dict[str, Any]]:
    values: dict[str, Any] = {
        "authenticate": lambda request: (
            request.headers.get("authorization") or (_ for _ in ()).throw(HttpAuthenticationError())
        ),
        "authorize": lambda authority, _tool, _envelope: authority == "Bearer local-test",
        "context_factory": lambda _request, authority, _tool, envelope: {
            "authority": authority,
            **envelope,
        },
        "invoke": lambda tool, context, arguments: tool.callable_fn(
            ToolContext({"authority": context["authority"]}, None), **arguments
        ),
        "context_fields": frozenset({"tenant"}),
    }
    values.update(overrides)
    return StarletteToolAdapter(**values)


def _client(adapter: StarletteToolAdapter[Any, Any] | None = None) -> TestClient:
    actual = adapter or _adapter()
    return TestClient(Starlette(routes=actual.routes((_tool(),))))


def test_authentication_and_authorization_precede_context_creation() -> None:
    contexts: list[str] = []
    adapter = _adapter(
        context_factory=lambda *_args: contexts.append("created") or {},
    )

    with _client(adapter) as client:
        unauthenticated = client.post("/tools/quote", json={"args": {"quantity": 1}})
        unauthorized = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer wrong"},
            json={"args": {"quantity": 1}},
        )

    assert unauthenticated.status_code == 401
    assert unauthorized.status_code == 403
    assert contexts == []


def test_request_validation_and_normalization_match_tool_contract() -> None:
    seen: list[dict[str, Any]] = []
    adapter = _adapter(invoke=lambda _tool, _ctx, arguments: seen.append(dict(arguments)) or arguments)

    with _client(adapter) as client:
        response = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"tenant": "demo", "args": {"quantity": "2", "note": None}},
        )
        unknown = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"tenant": "demo", "args": {"quantity": 2}, "extra": True},
        )
        constrained = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"tenant": "demo", "args": {"quantity": 0}},
        )

    assert response.status_code == 200
    assert response.json()["result"] == {"quantity": 2, "note": None}
    assert seen == [{"quantity": 2, "note": None}]
    assert unknown.status_code == 422
    assert constrained.status_code == 422


def test_user_and_internal_errors_have_distinct_sanitized_responses() -> None:
    observed: list[BaseException] = []

    def reject(*_args: object) -> object:
        raise HttpUserError("correct the request")

    with _client(_adapter(invoke=reject)) as client:
        user_error = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"args": {"quantity": 1}},
        )

    def fail(*_args: object) -> object:
        raise RuntimeError("secret connection string")

    with _client(_adapter(invoke=fail, observe_internal_error=lambda error, *_args: observed.append(error))) as client:
        internal = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"args": {"quantity": 1}},
        )

    assert user_error.status_code == 400
    assert user_error.json() == {"error": {"code": "tool_user_error", "message": "correct the request"}}
    assert internal.status_code == 500
    assert internal.json() == {"error": {"code": "tool_internal_error", "message": "tool execution failed"}}
    assert "secret connection string" not in internal.text
    assert len(observed) == 1


def test_sync_invocation_runs_off_event_loop_thread_and_async_invocation_is_awaited() -> None:
    request_thread: list[int] = []
    sync_thread: list[int] = []

    async def context_factory(_request: object, authority: str, _tool: object, _envelope: object) -> dict[str, Any]:
        request_thread.append(threading.get_ident())
        return {"authority": authority}

    def sync_invoke(*_args: object) -> str:
        sync_thread.append(threading.get_ident())
        return "sync"

    with _client(_adapter(context_factory=context_factory, invoke=sync_invoke)) as client:
        sync_response = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"args": {"quantity": 1}},
        )

    calls: list[str] = []

    async def async_invoke(*_args: object) -> str:
        await asyncio.sleep(0)
        calls.append("async")
        return "async"

    with _client(_adapter(invoke=async_invoke)) as client:
        async_response = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"args": {"quantity": 1}},
        )

    assert sync_response.json()["result"] == "sync"
    assert request_thread != sync_thread
    assert async_response.json()["result"] == "async"
    assert calls == ["async"]


def test_concurrent_requests_have_distinct_contexts() -> None:
    @dataclass
    class Context:
        marker: object

    contexts: list[Context] = []

    def context_factory(*_args: object) -> Context:
        context = Context(object())
        contexts.append(context)
        return context

    async def invoke(_tool: object, context: Context, arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"marker": id(context.marker), "quantity": arguments["quantity"]}

    adapter = _adapter(context_factory=context_factory, invoke=invoke)
    app = Starlette(routes=adapter.routes((_tool(),)))

    def call(value: int) -> dict[str, Any]:
        with TestClient(app) as client:
            return client.post(
                "/tools/quote",
                headers={"authorization": "Bearer local-test"},
                json={"args": {"quantity": value}},
            ).json()["result"]

    async def run() -> list[dict[str, Any]]:
        return list(await asyncio.gather(*(asyncio.to_thread(call, value) for value in range(1, 5))))

    results = asyncio.run(run())
    assert [result["quantity"] for result in results] == [1, 2, 3, 4]
    assert len({result["marker"] for result in results}) == 4
    assert len({id(context) for context in contexts}) == 4


def test_route_generation_rejects_collisions() -> None:
    tool = _tool()
    adapter = _adapter()

    try:
        adapter.routes((tool, tool))
    except ValueError as exc:
        assert "duplicate generated HTTP tool path" in str(exc)
    else:
        raise AssertionError("duplicate route was accepted")


def test_cancellation_propagates_without_retry() -> None:
    started = asyncio.Event()
    calls = 0

    async def invoke(*_args: object) -> object:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()

    adapter = _adapter(invoke=invoke)

    class Request:
        headers = {"authorization": "Bearer local-test"}

        async def json(self) -> object:
            return {"args": {"quantity": 1}}

    async def run() -> None:
        task = asyncio.create_task(adapter.handle(Request(), _tool()))  # type: ignore[arg-type]
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls == 1


def test_internal_failure_is_not_retried() -> None:
    calls = 0

    def fail(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("failed")

    with _client(_adapter(invoke=fail)) as client:
        response = client.post(
            "/tools/quote",
            headers={"authorization": "Bearer local-test"},
            json={"args": {"quantity": 1}},
        )

    assert response.status_code == 500
    assert calls == 1


def test_authentication_internal_failure_is_sanitized() -> None:
    observed: list[BaseException] = []

    def fail_authentication(_request: object) -> str:
        raise RuntimeError("secret authentication state")

    adapter = _adapter(
        authenticate=fail_authentication,
        observe_internal_error=lambda error, *_args: observed.append(error),
    )
    with _client(adapter) as client:
        response = client.post("/tools/quote", json={"args": {"quantity": 1}})

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "tool_internal_error", "message": "tool execution failed"}}
    assert "secret authentication state" not in response.text
    assert len(observed) == 1
