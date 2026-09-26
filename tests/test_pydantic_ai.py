from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from annotated_types import Ge

from coloph_toolset import Leaf, ToolDefinition, tool, tool_for
from coloph_toolset.pydantic_ai import PydanticAIAdapter

pydantic_ai = pytest.importorskip("pydantic_ai")
ModelRetry = pydantic_ai.ModelRetry


class FakeAgent:
    def __init__(self) -> None:
        self.tools: list[Any] = []

    def tool(self, wrapper=None, **_kwargs):
        if wrapper is None:
            return self.tool
        self.tools.append(wrapper)
        return wrapper


def definition(fn: Any, name: str = "sample") -> ToolDefinition:
    return ToolDefinition(("demo", name), Leaf(name, fn, exposure={"agent": None}), tool_for(fn), {"agent": None})


@dataclass(frozen=True)
class Deps:
    prefix: str


def test_registers_canonical_public_signature_without_hidden_arguments() -> None:
    @tool(model_hidden_args=("secret",))
    def sample(ctx: object, quantity: Annotated[int, Ge(1)], label: str = "default", secret: str = "app") -> str:
        """Create a sample."""
        return f"{quantity}:{label}:{secret}"

    agent = FakeAgent()
    adapter = PydanticAIAdapter(Deps, lambda deps, _tool: deps, lambda tool, deps, args: args)
    [wrapper] = adapter.register(agent, [definition(sample)])

    assert agent.tools == [wrapper]
    assert wrapper.__name__ == "demo_sample"
    assert wrapper.__doc__ == "Create a sample."
    assert list(inspect.signature(wrapper).parameters) == ["ctx", "quantity", "label"]


def test_typed_context_and_async_invocation_are_awaited_once() -> None:
    @tool()
    async def sample(ctx: object, value: int) -> str:
        return str(value)

    calls: list[tuple[str, int]] = []

    async def invoke(_tool: ToolDefinition, deps: Deps, args: dict[str, Any]) -> str:
        calls.append((deps.prefix, args["value"]))
        await asyncio.sleep(0)
        return f"{deps.prefix}:{args['value']}"

    agent = FakeAgent()
    [wrapper] = PydanticAIAdapter(Deps, lambda deps, _tool: deps, invoke).register(agent, [definition(sample)])

    assert asyncio.run(wrapper(SimpleNamespace(deps=Deps("item")), value=3)) == "item:3"
    assert calls == [("item", 3)]


def test_sync_callbacks_run_off_the_event_loop_and_calls_do_not_share_context() -> None:
    @tool()
    def sample(ctx: object, value: int) -> int:
        return value

    loop_thread = None
    callback_threads: list[int] = []
    contexts: list[object] = []

    def context_factory(_deps: Deps, _tool: ToolDefinition) -> object:
        import threading

        callback_threads.append(threading.get_ident())
        context = object()
        contexts.append(context)
        return context

    agent = FakeAgent()
    [wrapper] = PydanticAIAdapter(Deps, context_factory, lambda _tool, _context, args: args["value"]).register(
        agent, [definition(sample)]
    )

    async def run() -> list[int]:
        import threading

        nonlocal loop_thread
        loop_thread = threading.get_ident()
        return await asyncio.gather(*(wrapper(SimpleNamespace(deps=Deps("x")), value=value) for value in range(4)))

    assert asyncio.run(run()) == [0, 1, 2, 3]
    assert callback_threads and all(thread != loop_thread for thread in callback_threads)
    assert len({id(context) for context in contexts}) == 4


def test_recoverable_errors_become_model_retry_and_internal_errors_propagate() -> None:
    @tool()
    def sample(ctx: object) -> str:
        return "unused"

    def recover(error: Exception) -> str | None:
        return str(error) if isinstance(error, ValueError) else None

    failure: Exception = ValueError("correct input")

    def invoke(*_args: object) -> object:
        raise failure

    agent = FakeAgent()
    [wrapper] = PydanticAIAdapter(Deps, lambda deps, _tool: deps, invoke, input_error=recover).register(
        agent, [definition(sample)]
    )
    context = SimpleNamespace(deps=Deps("x"))

    with pytest.raises(ModelRetry, match="correct input"):
        asyncio.run(wrapper(context))

    failure = RuntimeError("internal")
    with pytest.raises(RuntimeError, match="internal"):
        asyncio.run(wrapper(context))


def test_register_exposes_only_the_supplied_selection() -> None:
    @tool()
    def first(ctx: object) -> str:
        return "first"

    @tool()
    def second(ctx: object) -> str:
        return "second"

    agent = FakeAgent()
    PydanticAIAdapter(Deps, lambda deps, _tool: deps, lambda *_args: "ok").register(agent, [definition(first, "first")])

    assert [wrapper.__name__ for wrapper in agent.tools] == ["demo_first"]
