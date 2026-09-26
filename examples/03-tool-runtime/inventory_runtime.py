"""Standalone inventory runtime with an application-owned transaction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated

from coloph_toolset import Finalization, ResourceLifecycle, ToolContext, ToolRuntime, tool


@dataclass
class Inventory:
    stock: dict[str, int]


@dataclass
class Session:
    calls: list[str] = field(default_factory=list)


@dataclass
class Transaction:
    inventory: Inventory
    pending: dict[str, int] = field(default_factory=dict)
    fail_commit: bool = False

    def available(self, sku: str) -> int:
        return self.inventory.stock.get(sku, 0) + self.pending.get(sku, 0)

    def reserve(self, sku: str, quantity: int) -> int:
        if self.available(sku) < quantity:
            raise ValueError("insufficient stock")
        self.pending[sku] = self.pending.get(sku, 0) - quantity
        return self.available(sku)

    def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("commit failed")
        for sku, delta in self.pending.items():
            self.inventory.stock[sku] = self.inventory.stock.get(sku, 0) + delta


Context = ToolContext[Inventory, Session, Transaction]


@tool()
def reserve(ctx: Context, sku: Annotated[str, "Inventory SKU"], quantity: Annotated[int, "Units"]) -> dict:
    """Reserve stock."""
    ctx.state.calls.append(f"reserve:{sku}")
    assert ctx.resource is not None
    remaining = ctx.resource.reserve(sku, quantity)
    return {"sku": sku, "reserved": quantity, "remaining": remaining}


@tool()
async def available(ctx: Context, sku: Annotated[str, "Inventory SKU"]) -> dict:
    """Read stock asynchronously."""
    await asyncio.sleep(0)
    ctx.state.calls.append(f"available:{sku}")
    assert ctx.resource is not None
    return {"sku": sku, "available": ctx.resource.available(sku)}


def build_runtime(inventory: Inventory, *, fail_commit: bool = False) -> ToolRuntime[Inventory, Session, Transaction]:
    def acquire(_ctx: Context) -> Transaction:
        return Transaction(inventory, fail_commit=fail_commit)

    def finalize(_ctx: Context, transaction: Transaction, error: BaseException | None) -> Finalization:
        if error is not None:
            return Finalization(committed=False, metadata={"transaction": "rolled_back"})
        transaction.commit()
        return Finalization(committed=True, metadata={"transaction": "committed"})

    return ToolRuntime(
        dependencies=inventory,
        state_factory=Session,
        resources=ResourceLifecycle(acquire=acquire, finalize=finalize),
    )


def reserve_text(_ctx: Context, result: dict) -> str:
    return f"Reserved {result['reserved']} {result['sku']}; {result['remaining']} remain."
