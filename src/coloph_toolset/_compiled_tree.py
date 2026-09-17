"""Visibility-filtered hierarchical view of one compiled tool selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ._index import ToolDefinition, validate_catalog, validate_path


@dataclass(frozen=True)
class CompiledToolTreeNode:
    """One visible command node; leaf data is absent for groups."""

    path: tuple[str, ...]
    children: tuple[str, ...]
    tool: ToolDefinition | None = None


class CompiledToolTree:
    """A tree containing only the tools compiled for the current invocation."""

    def __init__(self, tools: Sequence[ToolDefinition]) -> None:
        by_path: dict[tuple[str, ...], ToolDefinition | None] = {(): None}
        child_names: dict[tuple[str, ...], set[str]] = {(): set()}
        for tool in tools:
            validate_path(tool.path)
            for index, segment in enumerate(tool.path):
                parent = tool.path[:index]
                path = tool.path[: index + 1]
                child_names.setdefault(parent, set()).add(segment)
                child_names.setdefault(path, set())
                by_path.setdefault(path, None)
            by_path[tool.path] = tool
        validate_catalog(tuple(tools))
        self._nodes = {
            path: CompiledToolTreeNode(path=path, children=tuple(sorted(child_names[path])), tool=by_path[path])
            for path in by_path
        }

    @property
    def root_names(self) -> tuple[str, ...]:
        return self._nodes[()].children

    def resolve(self, path: tuple[str, ...]) -> CompiledToolTreeNode | None:
        return self._nodes.get(path)

    def render_help(self, path: tuple[str, ...] = ()) -> str:
        """Render all visible leaf commands below one compiled path."""
        node = self.resolve(path)
        if node is None:
            raise ValueError("unknown compiled tool path: " + ".".join(path))
        title = ".".join(path) if path else "root"
        lines = [f"Available commands under {title}:"]
        leaves = sorted(
            (
                child
                for child_path, child in self._nodes.items()
                if child_path[: len(path)] == path and child.tool is not None
            ),
            key=lambda child: child.path,
        )
        for child in leaves:
            relative = ".".join(child.path[len(path) :]) or "."
            tool = child.tool
            if tool is not None:
                lines.append(f"- {relative}: {(tool.description.splitlines() or [tool.dotted])[0]}")
        return "\n".join(lines)

    def alias_path(self, alias: str) -> tuple[str, ...] | None:
        """Return one visible leaf path whose flat native name matches ``alias``."""
        matches = [
            node.path
            for node in self._nodes.values()
            if node.tool is not None and alias in {node.tool.agent_name, node.tool.dotted.replace(".", "_")}
        ]
        return matches[0] if len(matches) == 1 else None
