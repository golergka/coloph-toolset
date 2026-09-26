"""Typed application context for one tool invocation.

This file began as Coloph's ``core/tools/_ctx.py``. The public form keeps the
per-call ownership model and removes Coloph's database, worker, workflow, and
pipeline fields.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Generic, TypeVar

DependenciesT = TypeVar("DependenciesT")
StateT = TypeVar("StateT")
ResourceT = TypeVar("ResourceT")


@dataclass(frozen=True)
class ToolContext(Generic[DependenciesT, StateT, ResourceT]):
    """Application-owned values visible to one tool call.

    ``dependencies`` are long-lived services. ``state`` is fresh for each call
    unless the caller explicitly supplies a shared state object. ``resource``
    is populated only after the configured resource lifecycle acquires it.
    """

    dependencies: DependenciesT
    state: StateT
    resource: ResourceT | None = None

    def with_resource(self, resource: ResourceT) -> ToolContext[DependenciesT, StateT, ResourceT]:
        """Return the call context that owns ``resource``."""
        return replace(self, resource=resource)


__all__ = ["ToolContext"]
