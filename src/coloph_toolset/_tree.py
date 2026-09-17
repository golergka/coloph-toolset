"""Declared groups, deferred groups, leaves, and presentation inheritance."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable

Exposure = dict[str, None]


@dataclass(frozen=True)
class Leaf:
    """A leaf tool in the tree, wrapping a `@tool`-decorated function."""

    name: str
    fn: Callable[..., Any]
    exposure: Exposure | None = None
    render: Callable[..., str] | None = None
    help: str | None = None
    alias: str | None = None
    parser_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupNode:
    """A non-leaf node containing other groups and leaves.

    When `flatten=True`, the node's children are spliced into the parent level
    and the node contributes no path segment.
    """

    name: str
    help: str
    children: tuple["TreeNode", ...]
    exposure: Exposure | None = None
    flatten: bool = False
    # argparse subparsers `dest` override (e.g. "review_dedup_command"). Consumed
    # by `build_parser_from_tree` so namespace attribute names stay
    # stable; defaults to f"{name.replace('-', '_')}_command" when None. Dispatch
    # derives command ids from tree position (ToolDefinition.dotted), not from `dest`.
    dest: str | None = None


@dataclass(frozen=True)
class DeferredGroup:
    """A group whose children are built once, when a consumer needs them.

    The group header remains a normal declarative tree node.  CLI path parsing
    can therefore name and describe the group without importing its subtree;
    callers that need a complete catalog simply read ``children`` and receive
    the same node sequence as an eager ``GroupNode``.
    """

    name: str
    help: str
    load_children: Callable[[], tuple["TreeNode", ...]]
    exposure: Exposure | None = None
    flatten: bool = False
    dest: str | None = None
    _loaded_children: tuple["TreeNode", ...] | None = field(default=None, init=False, repr=False, compare=False)

    _load_lock: Any = field(default_factory=RLock, init=False, repr=False, compare=False)

    @property
    def children(self) -> tuple["TreeNode", ...]:
        with self._load_lock:
            children = self._loaded_children
            if children is None:
                children = tuple(self.load_children())
                object.__setattr__(self, "_loaded_children", children)
            return children


TreeNode = Leaf | GroupNode | DeferredGroup


def effective_exposure(node_exposure: Exposure | None, parent_exposure: Exposure | None) -> Exposure:
    """Resolve a node's effective exposure, inheriting from the parent.

    A node's own exposure REPLACES the inherited exposure entirely when set
    (not merged).
    """
    if node_exposure is not None:
        return node_exposure
    if parent_exposure is not None:
        return parent_exposure
    return {}
