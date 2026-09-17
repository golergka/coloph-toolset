"""Tree walking, exposure resolution, catalog validation, and exact selection."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable, Union, overload

from ._decorator import Tool, ToolParam, tool_for
from ._tree import DeferredGroup, Exposure, GroupNode, Leaf, TreeNode, effective_exposure


@dataclass(frozen=True)
class ToolDefinition:
    """One mounted declaration with its stable identity and effective exposure."""

    path: tuple[str, ...]
    leaf: Leaf
    tool: Tool
    exposure: Exposure

    @property
    def dotted(self) -> str:
        return ".".join(self.path)

    @property
    def stable_id(self) -> str:
        return self.dotted

    @property
    def callable_fn(self) -> Callable[..., Any]:
        return self.tool.fn

    @property
    def description(self) -> str:
        return self.tool.description

    @property
    def params(self) -> tuple[ToolParam, ...]:
        return self.tool.params

    @property
    def callable_signature(self) -> Any:
        from ._decorator import signature_with_tool_params

        return signature_with_tool_params(self.tool)

    @property
    def agent_name(self) -> str:
        if self.leaf.alias is not None:
            return self.leaf.alias
        return self.dotted.replace(".", "_").replace("-", "_")


# Compatibility name for callers still importing the former projection type.
# It is an alias, not a second record or availability mechanism.
BoundTool = ToolDefinition


@dataclass(frozen=True)
class ToolSelection(Sequence[ToolDefinition]):
    """An immutable sequence of definitions and the patterns that selected them."""

    patterns: tuple[str, ...]
    tools: tuple[ToolDefinition, ...]
    # Exact leaves whose full first-use documentation is included in the
    # invocation prompt. This is prompt metadata only; it does not enforce a
    # workflow outcome or change tool dispatch.
    required_tool_ids: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.tools)

    @overload
    def __getitem__(self, index: int) -> ToolDefinition: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ToolDefinition, ...]: ...

    def __getitem__(self, index: Union[int, slice]) -> Union[ToolDefinition, tuple[ToolDefinition, ...]]:
        return self.tools[index]

    def excluding(self, *stable_ids: str) -> ToolSelection:
        """Return the same selection without the named tools.

        Filtering retains the selection's semantic identity. The returned exact patterns describe only the surviving
        tools, so later adapter compilation cannot restore an excluded leaf
        through an earlier wildcard.
        """
        excluded = frozenset(stable_ids)
        remaining = tuple(tool for tool in self.tools if tool.dotted not in excluded)
        return ToolSelection(
            patterns=tuple(tool.dotted for tool in remaining),
            tools=remaining,
            required_tool_ids=tuple(tool_id for tool_id in self.required_tool_ids if tool_id not in excluded),
        )

    def with_required_tools(self, *stable_ids: str) -> ToolSelection:
        """Attach exact required leaves after checking this compiled selection."""
        required_tool_ids = tuple(dict.fromkeys(stable_ids))
        available = {tool.dotted for tool in self.tools}
        missing = [tool_id for tool_id in required_tool_ids if tool_id not in available]
        if missing:
            raise ValueError("required tools are unavailable: " + ", ".join(missing))
        return ToolSelection(
            patterns=self.patterns,
            tools=self.tools,
            required_tool_ids=required_tool_ids,
        )


@dataclass(frozen=True)
class TreeEntry:
    path: tuple[str, ...]
    leaf: Leaf
    exposure: Exposure
    ancestors: tuple[GroupNode | DeferredGroup, ...]


class ToolCatalog:
    """The canonical mapping from stable dotted IDs to tool definitions."""

    def __init__(self, by_dotted: dict[str, ToolDefinition]) -> None:
        self.by_dotted = by_dotted

    def select_tools(self, patterns: list[str]) -> ToolSelection:
        """Resolve exact IDs and prefix wildcards, rejecting unmatched patterns.

        Results are sorted and deduplicated. Selection never grants authority.
        """
        selected: dict[str, ToolDefinition] = {}
        for pattern in patterns:
            if pattern.endswith(".*"):
                prefix = pattern[:-1]  # keep trailing dot: "graph.cites."
                matches = [b for b in self.by_dotted.values() if b.dotted.startswith(prefix)]
                if not matches:
                    raise ValueError("tool pattern {pattern!r} matched no tools".format(pattern=pattern))
                for bound in matches:
                    selected[bound.dotted] = bound
                continue
            exact = self.by_dotted.get(pattern)
            if exact is None:
                raise ValueError("tool pattern {pattern!r} matched no tools".format(pattern=pattern))
            selected[exact.dotted] = exact
        return ToolSelection(
            patterns=tuple(patterns),
            tools=tuple(selected[key] for key in sorted(selected)),
        )


# Compatibility name for callers still importing the former lookup type.
ToolIndex = ToolCatalog


def validate_path(path: tuple[str, ...]) -> None:
    if not path or any(
        not isinstance(part, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", part) for part in path
    ):
        raise ValueError(f"invalid command path: {path!r}")


def validate_catalog(tools: tuple[ToolDefinition, ...]) -> None:
    paths: set[tuple[str, ...]] = set()
    aliases: dict[str, str] = {}
    for bound in tools:
        validate_path(bound.path)
        if bound.path in paths:
            raise ValueError(f"duplicate dotted id {bound.dotted!r}")
        paths.add(bound.path)
        for alias in {bound.agent_name, bound.dotted.replace(".", "_")}:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", alias):
                raise ValueError(f"invalid alias {alias!r}")
            if alias in aliases and aliases[alias] != bound.dotted:
                raise ValueError(f"duplicate agent name {alias!r}")
            aliases[alias] = bound.dotted
    for path in paths:
        if any(path[:length] in paths for length in range(1, len(path))):
            raise ValueError(f"command/group collision at {'.'.join(path)!r}")


def _bind(entry: TreeEntry) -> ToolDefinition:
    try:
        attached = tool_for(entry.leaf.fn)
    except TypeError as exc:
        raise ValueError(f"{'.'.join(entry.path)}: leaf.fn is not @tool-decorated") from exc
    attached.argument_model(include_hidden=True)
    return ToolDefinition(entry.path, entry.leaf, attached, entry.exposure)


def _bound(entry: TreeEntry, bind: Callable[[TreeEntry], Any] | None) -> Any:
    bound = (bind or _bind)(entry)
    validate_catalog((bound,))
    from ._call_codec import validate_cli

    validate_cli(bound.tool)
    return bound


def _siblings(children: tuple[TreeNode, ...], path: tuple[str, ...]) -> tuple[TreeNode, ...]:
    names: set[str] = set()

    def visit(node: TreeNode) -> None:
        validate_path((node.name,))
        if isinstance(node, (GroupNode, DeferredGroup)) and node.flatten:
            for child in node.children:
                visit(child)
        else:
            if node.name in names:
                raise ValueError(f"duplicate dotted id or command/group path: {'.'.join((*path, node.name))!r}")
            names.add(node.name)

    for node in children:
        visit(node)
    return children


def build_index(root: GroupNode, *, bind: Callable[[TreeEntry], Any] | None = None) -> ToolCatalog:
    entries: list[TreeEntry] = []
    root_exposure = effective_exposure(root.exposure, None)
    for child in _siblings(root.children, ()):
        _walk(child, (), root_exposure, (root,), entries)
    tools = tuple(_bound(entry, bind) for entry in entries)
    validate_catalog(tools)
    return ToolCatalog({bound.dotted: bound for bound in tools})


def resolve_tool(root: GroupNode, path: tuple[str, ...], *, bind: Callable[[TreeEntry], Any] | None = None) -> Any:
    validate_path(path)
    entry = _resolve_entry(root.children, path, (), effective_exposure(root.exposure, None), (root,))
    if entry is None:
        raise ValueError(f"tool path {'.'.join(path)!r} matched no tool")
    return _bound(entry, bind)


def _resolve_entry(
    children: tuple[TreeNode, ...],
    remaining: tuple[str, ...],
    path: tuple[str, ...],
    parent_exposure: Exposure,
    ancestors: tuple[GroupNode | DeferredGroup, ...],
) -> TreeEntry | None:
    name = remaining[0]
    for node in _siblings(children, path):
        resolved = effective_exposure(node.exposure, parent_exposure)
        if isinstance(node, Leaf):
            if node.name == name and len(remaining) == 1:
                return TreeEntry(path + (node.name,), node, resolved, ancestors)
            continue
        child_path = path if node.flatten else path + (node.name,)
        child_remaining = remaining if node.flatten else remaining[1:]
        if node.flatten or node.name == name:
            if not child_remaining:
                return None
            entry = _resolve_entry(node.children, child_remaining, child_path, resolved, (*ancestors, node))
            if entry is not None:
                return entry
    return None


def _walk(
    node: TreeNode,
    path: tuple[str, ...],
    parent_exposure: Exposure,
    ancestors: tuple[GroupNode | DeferredGroup, ...],
    out: list[TreeEntry],
) -> None:
    resolved = effective_exposure(node.exposure, parent_exposure)
    if isinstance(node, Leaf):
        out.append(TreeEntry(path + (node.name,), node, resolved, ancestors))
        return
    child_path = path if node.flatten else path + (node.name,)
    for child in _siblings(node.children, child_path):
        _walk(child, child_path, resolved, (*ancestors, node), out)
