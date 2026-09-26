from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Event
from typing import Annotated, Any

import pytest
from annotated_types import Gt

from coloph_toolset import (
    Documentation,
    GroupNode,
    HierarchicalInputError,
    HierarchicalState,
    HierarchicalToolAdapter,
    Leaf,
    PreparedToolCall,
    build_index,
    tool,
)


def _tools(*functions: tuple[str, str, Any]) -> tuple[Any, ...]:
    groups: dict[str, list[Leaf]] = {}
    for group, name, function in functions:
        groups.setdefault(group, []).append(Leaf(name, function))
    catalog = build_index(
        GroupNode(
            "root",
            "Root",
            children=tuple(
                GroupNode(group, f"{group} commands", children=tuple(leaves)) for group, leaves in groups.items()
            ),
        )
    )
    return tuple(catalog.by_dotted[key] for key in sorted(catalog.by_dotted))


def test_native_metadata_is_validated_at_declaration() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="greater_than"):

        @tool(model_example={"count": 0})
        def invalid_example(ctx: object, count: Annotated[int, Gt(0)]) -> None:
            pass

    with pytest.raises(TypeError, match="required_documents must not contain duplicates"):

        @tool(required_documents=("guide", "guide"))
        def duplicate_documents(ctx: object) -> None:
            pass


def test_discovery_contains_only_the_authorized_selection() -> None:
    @tool()
    def visible(ctx: object) -> str:
        """Visible command."""
        return "visible"

    @tool()
    def secret(ctx: object) -> str:
        """Secret command."""
        return "secret"

    _secret_tool, visible_tool = _tools(("work", "secret", secret), ("work", "visible", visible))
    adapter = HierarchicalToolAdapter((visible_tool,))

    result = adapter.prepare("work", {}, HierarchicalState())

    assert isinstance(result, dict)
    assert "visible" in result["documentation"]
    assert "secret" not in result["documentation"]
    assert result["command_invoked"] is False


def test_group_help_and_invalid_paths_do_not_prepare_execution() -> None:
    @tool(model_example={"count": 2})
    def run(ctx: object, count: Annotated[int, Gt(0)]) -> str:
        """Run work."""
        return str(count)

    [definition] = _tools(("work", "run", run))
    adapter = HierarchicalToolAdapter((definition,))
    state = HierarchicalState()

    group = adapter.prepare("work", {}, state)
    assert isinstance(group, dict)
    assert 'Example call: {"arguments":{"count":2},"command_path":"run"}' in group["documentation"]

    with pytest.raises(HierarchicalInputError, match=r"(?s)segment 1: 'missing'.*run: Run work"):
        adapter.prepare("work", {"command_path": "missing", "arguments": {}}, state)
    with pytest.raises(HierarchicalInputError, match="command_path must be a string"):
        adapter.prepare("work", {"command_path": ["run"], "arguments": {}}, state)


def test_first_use_gate_precedes_canonical_validation_and_execution() -> None:
    @tool()
    def run(ctx: object, count: Annotated[int, Gt(0)], mode: str = "safe") -> str:
        """Run work."""
        return f"{count}:{mode}"

    [definition] = _tools(("work", "run", run))
    adapter = HierarchicalToolAdapter((definition,))
    state = HierarchicalState()
    payload = {"command_path": "run", "arguments": {"count": -1}}

    first = adapter.prepare("work", payload, state)
    assert isinstance(first, dict)
    assert first["command_invoked"] is False

    with pytest.raises(HierarchicalInputError, match=r"(?s)Invalid arguments for work.run.*greater_than"):
        adapter.prepare("work", payload, state)
    with pytest.raises(HierarchicalInputError, match=r"(?s)Invalid arguments for work.run.*extra_forbidden"):
        adapter.prepare(
            "work",
            {"command_path": "run", "arguments": {"count": 1, "unknown": True}},
            state,
        )

    prepared = adapter.prepare("work", {"command_path": "run", "arguments": {"count": "2"}}, state)
    assert isinstance(prepared, PreparedToolCall)
    assert prepared.arguments == {"count": 2, "mode": "safe"}


def test_document_delivery_is_per_invocation_and_repeats_after_content_changes() -> None:
    current = {"fingerprint": "one", "text": "Read version one."}

    @tool(required_documents=("operations",))
    def run(ctx: object) -> str:
        """Run work."""
        return "done"

    [definition] = _tools(("work", "run", run))

    def load(names: tuple[str, ...], _tool: Any) -> list[Documentation]:
        assert names == ("operations",)
        return [Documentation("operations", current["text"], current["fingerprint"])]

    adapter = HierarchicalToolAdapter((definition,), document_loader=load)
    first_state = HierarchicalState()
    payload = {"command_path": "run", "arguments": {}}

    first = adapter.prepare("work", payload, first_state)
    assert isinstance(first, dict)
    assert "Read version one." in first["documentation"]
    assert first_state.document_fingerprints == {"operations": "one"}
    assert isinstance(adapter.prepare("work", payload, first_state), PreparedToolCall)

    current.update(fingerprint="two", text="Read version two.")
    changed = adapter.prepare("work", payload, first_state)
    assert isinstance(changed, dict)
    assert "Read version two." in changed["documentation"]
    assert first_state.document_fingerprints == {"operations": "two"}

    second_state = HierarchicalState()
    assert isinstance(adapter.prepare("work", payload, second_state), dict)


def test_loader_failure_does_not_record_delivery() -> None:
    @tool(required_documents=("operations",))
    def run(ctx: object) -> str:
        return "done"

    [definition] = _tools(("work", "run", run))

    def fail(_names: tuple[str, ...], _tool: Any) -> list[Documentation]:
        raise OSError("document unavailable")

    state = HierarchicalState()
    adapter = HierarchicalToolAdapter((definition,), document_loader=fail)

    with pytest.raises(OSError, match="document unavailable"):
        adapter.prepare("work", {"command_path": "run", "arguments": {}}, state)
    assert state.delivered_tools == set()
    assert state.document_fingerprints == {}


def test_preloaded_document_is_not_repeated_and_application_controls_its_heading() -> None:
    @tool(required_documents=("operations",))
    def run(ctx: object) -> str:
        return "done"

    [definition] = _tools(("work", "run", run))
    document = Documentation("operations", "Read this.", "current")
    adapter = HierarchicalToolAdapter(
        (definition,),
        document_loader=lambda _names, _tool: (document,),
        document_renderer=lambda loaded: f"## Required guide: {loaded.name}\n{loaded.text}",
    )
    payload = {"command_path": "run", "arguments": {}}

    preloaded = HierarchicalState(document_fingerprints={"operations": "current"})
    first = adapter.prepare("work", payload, preloaded)
    assert isinstance(first, dict)
    assert "Read this." not in first["documentation"]

    fresh = adapter.prepare("work", payload, HierarchicalState())
    assert isinstance(fresh, dict)
    assert "## Required guide: operations\nRead this." in fresh["documentation"]


def test_required_tool_bypasses_gate_after_prompt_delivery() -> None:
    @tool(first_use_guidance="Confirm the effect.")
    def run(ctx: object) -> str:
        return "done"

    [definition] = _tools(("work", "run", run))
    adapter = HierarchicalToolAdapter((definition,))
    state = HierarchicalState()

    prepared = adapter.prepare(
        "work",
        {"command_path": "run", "arguments": {}},
        state,
        required_tool_ids=(definition.dotted,),
    )

    assert isinstance(prepared, PreparedToolCall)
    assert definition.dotted in state.guidance_seen


def test_post_execution_documentation_and_terminal_boundary() -> None:
    @tool(post_execution_first_use_documentation=True, terminal=True)
    def finish(ctx: object) -> str:
        """Finish work."""
        return "done"

    [definition] = _tools(("work", "finish", finish))
    adapter = HierarchicalToolAdapter((definition,))
    state = HierarchicalState()
    prepared = adapter.prepare("work", {"command_path": "finish", "arguments": {}}, state)
    assert isinstance(prepared, PreparedToolCall)

    failed = adapter.complete(prepared, {"error": "failed"}, state, succeeded=False)
    assert failed.terminal is False
    assert state.delivered_tools == set()

    completed = adapter.complete(prepared, "done", state)
    assert completed.terminal is True
    assert completed.result.startswith("done\n\n## First-use documentation")
    assert definition.dotted in state.delivered_tools


def test_post_execution_mode_cannot_bypass_explicit_guidance() -> None:
    @tool(post_execution_first_use_documentation=True, first_use_guidance="Confirm the effect.")
    def run(ctx: object) -> str:
        return "done"

    [definition] = _tools(("work", "run", run))
    adapter = HierarchicalToolAdapter((definition,))
    state = HierarchicalState()
    payload = {"command_path": "run", "arguments": {}}

    with pytest.raises(HierarchicalInputError, match="Confirm the effect"):
        adapter.prepare("work", payload, state)
    assert isinstance(adapter.prepare("work", payload, state), PreparedToolCall)


def test_concurrent_first_use_calls_wait_until_document_packet_is_ready() -> None:
    @tool()
    def run(ctx: object) -> str:
        return "done"

    [definition] = _tools(("work", "run", run))
    rendering = Event()
    release = Event()

    def slow_packet(tool_definition: Any, command_invoked: bool) -> str:
        rendering.set()
        assert release.wait(timeout=5)
        return f"{tool_definition.dotted}:{command_invoked}"

    adapter = HierarchicalToolAdapter((definition,), packet_renderer=slow_packet)
    state = HierarchicalState()
    payload = {"command_path": "run", "arguments": {}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(adapter.prepare, "work", payload, state)
        assert rendering.wait(timeout=5)
        second = pool.submit(adapter.prepare, "work", payload, state)
        with pytest.raises(FutureTimeoutError):
            second.result(timeout=0.1)
        release.set()
        assert isinstance(first.result(timeout=5), dict)
        assert isinstance(second.result(timeout=5), PreparedToolCall)
