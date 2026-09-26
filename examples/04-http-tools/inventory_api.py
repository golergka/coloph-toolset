"""A small authenticated HTTP tool service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any

from annotated_types import Ge
from starlette.applications import Starlette

from coloph_toolset import GroupNode, Leaf, ToolContext, ToolRuntime, build_index, tool
from coloph_toolset.http import HttpAuthenticationError, StarletteToolAdapter


@dataclass
class Inventory:
    stock: dict[str, int]


@tool()
def available(ctx: ToolContext[Inventory, None, object], sku: str) -> str:
    """Return the available quantity."""
    return f"{sku}: {ctx.dependencies.stock.get(sku, 0)} available"


@tool()
async def reserve(
    ctx: ToolContext[Inventory, None, object],
    sku: str,
    quantity: Annotated[int, Ge(1)],
) -> str:
    """Reserve an in-memory quantity."""
    await asyncio.sleep(0)
    remaining = ctx.dependencies.stock.get(sku, 0) - quantity
    if remaining < 0:
        raise ValueError("insufficient inventory")
    ctx.dependencies.stock[sku] = remaining
    return f"reserved {quantity} {sku}; {remaining} remain"


@tool()
def reset(ctx: ToolContext[Inventory, None, object]) -> None:
    """Reset all inventory. This tool is not exposed by the service."""
    ctx.dependencies.stock.clear()


def build_app(inventory: Inventory, *, token: str = "Bearer local-demo") -> Starlette:
    root = GroupNode(
        "root",
        "Inventory",
        (Leaf("available", available), Leaf("reserve", reserve), Leaf("reset", reset)),
        exposure={"http": None},
        flatten=True,
    )
    catalog = build_index(root)
    selected = catalog.select_tools(["available", "reserve"])

    def authenticate(request: Any) -> str:
        supplied = request.headers.get("authorization")
        if supplied != token:
            raise HttpAuthenticationError()
        return supplied

    def context_factory(_request: Any, _authority: str, _tool: Any, _envelope: Any) -> ToolRuntime:
        return ToolRuntime(dependencies=inventory, state_factory=lambda: None)

    def invoke(definition: Any, runtime: ToolRuntime, arguments: dict[str, Any]) -> Any:
        return runtime.invoke_sync(definition.tool, arguments)

    adapter = StarletteToolAdapter(
        authenticate=authenticate,
        authorize=lambda *_args: True,
        context_factory=context_factory,
        invoke=invoke,
        prefix="/api/tools/",
    )
    return Starlette(routes=adapter.routes(selected))
