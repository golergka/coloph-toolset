from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent

from coloph_toolset import GroupNode, Leaf, ToolContext, ToolRuntime, build_index, tool
from coloph_toolset.pydantic_ai import PydanticAIAdapter


@dataclass
class Inventory:
    quantities: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDeps:
    inventory: Inventory


@tool(model_hidden_args=("audit_source",))
def add_stock(ctx: ToolContext[Inventory, object, object], sku: str, quantity: int, audit_source: str = "agent") -> int:
    """Add a positive quantity of stock."""
    if quantity < 1:
        raise ValueError("quantity must be positive")
    ctx.dependencies.quantities[sku] = ctx.dependencies.quantities.get(sku, 0) + quantity
    return ctx.dependencies.quantities[sku]


@tool()
async def count_stock(ctx: ToolContext[Inventory, object, object], sku: str) -> int:
    """Return the available quantity."""
    return ctx.dependencies.quantities.get(sku, 0)


CATALOG = build_index(
    GroupNode(
        "root",
        "Inventory tools",
        children=(GroupNode("inventory", "Inventory", children=(Leaf("add", add_stock), Leaf("count", count_stock))),),
    )
)


def build_agent(model: Any) -> Agent[AgentDeps, str]:
    agent = Agent(model, deps_type=AgentDeps, system_prompt="Maintain the requested inventory.")

    def context_factory(deps: AgentDeps, _tool: object) -> ToolRuntime[Inventory, object, object]:
        return ToolRuntime(dependencies=deps.inventory, state_factory=object)

    async def invoke(
        tool_definition: Any, runtime: ToolRuntime[Inventory, object, object], args: dict[str, Any]
    ) -> Any:
        return (await runtime.invoke(tool_definition.tool, args)).raw

    adapter = PydanticAIAdapter(
        AgentDeps,
        context_factory,
        invoke,
        input_error=lambda error: str(error.__cause__) if isinstance(error.__cause__, ValueError) else None,
    )
    adapter.register(agent, CATALOG.select_tools(["inventory.*"]))
    return agent
