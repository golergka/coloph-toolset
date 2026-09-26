from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from annotated_types import Gt
from pydantic_ai import Agent

from coloph_toolset import Documentation, GroupNode, HierarchicalState, Leaf, build_index, tool
from coloph_toolset.pydantic_ai import PydanticAIHierarchicalAdapter


@dataclass
class AgentDeps:
    state: HierarchicalState = field(default_factory=HierarchicalState)
    calls: list[str] = field(default_factory=list)
    terminal_result: str | None = None


@tool(required_documents=("orders",), model_example={"query": "A-100", "limit": 2})
def search_orders(ctx: AgentDeps, query: str, limit: Annotated[int, Gt(0)] = 10) -> list[str]:
    """Search visible orders."""
    ctx.calls.append(f"search:{query}:{limit}")
    return [query]


@tool(required_documents=("orders",), terminal=True)
def close_order(ctx: AgentDeps, order_id: str) -> str:
    """Close one order."""
    ctx.calls.append(f"close:{order_id}")
    return f"closed:{order_id}"


@tool()
def delete_all_orders(ctx: AgentDeps) -> str:
    """Delete every order."""
    ctx.calls.append("delete-all")
    return "deleted"


CATALOG = build_index(
    GroupNode(
        "root",
        "Order tools",
        children=(
            GroupNode(
                "orders",
                "Order operations",
                children=(
                    Leaf("search", search_orders),
                    Leaf("close", close_order),
                    Leaf("delete-all", delete_all_orders),
                ),
            ),
        ),
    )
)


def load_documents(names: tuple[str, ...], _tool: Any) -> list[Documentation]:
    documents = []
    for name in names:
        text = (Path(__file__).parent / "docs" / f"{name}.md").read_text()
        documents.append(Documentation(name, text, hashlib.sha256(text.encode()).hexdigest()))
    return documents


def build_agent(model: Any) -> Agent[AgentDeps, str]:
    agent = Agent(model, deps_type=AgentDeps, system_prompt="Inspect and update orders.")

    async def invoke(tool_definition: Any, deps: AgentDeps, arguments: dict[str, Any]) -> Any:
        result = tool_definition.callable_fn(deps, **arguments)
        return await result if inspect.isawaitable(result) else result

    def terminal(deps: AgentDeps, _tool: Any, result: Any) -> None:
        deps.terminal_result = str(result)

    adapter = PydanticAIHierarchicalAdapter(
        AgentDeps,
        lambda deps, _tool: deps,
        invoke,
        state_factory=lambda deps: deps.state,
        document_loader=load_documents,
        on_terminal=terminal,
    )
    selection = CATALOG.select_tools(["orders.search", "orders.close"])
    adapter.register(agent, selection)
    return agent
