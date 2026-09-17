"""Generate named-argument parsers from declared command trees."""

from __future__ import annotations

from argparse import ArgumentParser, _SubParsersAction
from typing import Any, Callable

from ._call_codec import ToolArgumentParser, configure_leaf_parser
from ._index import ToolCatalog, ToolDefinition, TreeEntry, build_index, resolve_tool
from ._tree import (
    DeferredGroup,
    Exposure,
    GroupNode,
    Leaf,
    effective_exposure,
)


def build_parser_from_tree(
    root: GroupNode,
    surface: str,
    *,
    parser: ArgumentParser,
    index: ToolCatalog | None = None,
    allowed_dotted: frozenset[str] | None = None,
    requested_path: tuple[str, ...] | None = None,
    bind: Callable[[TreeEntry], Any] | None = None,
    leaf_defaults: Callable[[Any], dict[str, Any]] | None = None,
    leaf_visible: Callable[[Any], bool] | None = None,
    command_metavar: str = "command",
) -> ArgumentParser:
    """Attach visible commands and their resolved definitions to a parser.

    The application supplies the executable name, root flags, and optional
    binding/presentation callbacks. An exact selection overrides exposure.
    """
    if index is None and allowed_dotted is not None:
        index = ToolCatalog(
            {dotted: resolve_tool(root, tuple(dotted.split(".")), bind=bind) for dotted in sorted(allowed_dotted)}
        )
    elif index is None and requested_path is None:
        index = build_index(root, bind=bind)
    if index is not None and allowed_dotted is None:
        allowed_dotted = frozenset(
            dotted
            for dotted, bound in index.by_dotted.items()
            if surface in bound.exposure and (leaf_visible is None or leaf_visible(bound.leaf))
        )
    if index is not None and allowed_dotted is not None:
        unknown = allowed_dotted - index.by_dotted.keys()
        if unknown:
            raise ValueError(f"selection contains unknown commands: {sorted(unknown)}")
    parser_type = type(parser) if isinstance(parser, ToolArgumentParser) else ToolArgumentParser
    subparsers = parser.add_subparsers(dest="command", required=True, metavar=command_metavar, parser_class=parser_type)
    root_exposure = effective_exposure(root.exposure, None)
    for child in _visible_children(root, root_exposure, surface, allowed_dotted, (), leaf_visible):
        remaining = None
        if isinstance(child, (GroupNode, DeferredGroup)):
            if child.flatten:
                remaining = requested_path
            elif requested_path and child.name == requested_path[0]:
                remaining = requested_path[1:]
        _attach(
            subparsers,
            child,
            (),
            root_exposure,
            surface,
            root,
            index,
            allowed_dotted,
            bind,
            leaf_defaults,
            leaf_visible,
            requested_path is None,
            remaining,
        )
    return parser


def _attach(
    subparsers: _SubParsersAction[Any],
    node: Leaf | GroupNode | DeferredGroup,
    parent_path: tuple[str, ...],
    parent_exposure: Exposure,
    surface: str,
    root: GroupNode,
    index: ToolCatalog | None,
    allowed_dotted: frozenset[str] | None,
    bind: Callable[[TreeEntry], Any] | None,
    leaf_defaults: Callable[[Any], dict[str, Any]] | None,
    leaf_visible: Callable[[Any], bool] | None,
    full_tree: bool,
    remaining: tuple[str, ...] | None,
) -> None:
    node_exposure = effective_exposure(node.exposure, parent_exposure)
    if isinstance(node, Leaf):
        _attach_leaf(subparsers, node, parent_path, root, index, bind, leaf_defaults, leaf_visible)
    elif node.flatten:
        for child in _visible_children(node, node_exposure, surface, allowed_dotted, parent_path, leaf_visible):
            child_remaining = None
            if isinstance(child, (GroupNode, DeferredGroup)):
                if child.flatten:
                    child_remaining = remaining
                elif remaining and child.name == remaining[0]:
                    child_remaining = remaining[1:]
            _attach(
                subparsers,
                child,
                parent_path,
                node_exposure,
                surface,
                root,
                index,
                allowed_dotted,
                bind,
                leaf_defaults,
                leaf_visible,
                full_tree,
                child_remaining,
            )
    else:
        _attach_group(
            subparsers,
            node,
            parent_path,
            node_exposure,
            surface,
            root,
            index,
            allowed_dotted,
            bind,
            leaf_defaults,
            leaf_visible,
            full_tree,
            remaining,
        )


def _attach_group(
    subparsers: _SubParsersAction[Any],
    group: GroupNode | DeferredGroup,
    parent_path: tuple[str, ...],
    group_exposure: Exposure,
    surface: str,
    root: GroupNode,
    index: ToolCatalog | None,
    allowed_dotted: frozenset[str] | None,
    bind: Callable[[TreeEntry], Any] | None,
    leaf_defaults: Callable[[Any], dict[str, Any]] | None,
    leaf_visible: Callable[[Any], bool] | None,
    full_tree: bool,
    remaining: tuple[str, ...] | None,
) -> None:
    kwargs: dict[str, Any] = {}
    if group.help:
        kwargs["help"] = group.help
    group_p = subparsers.add_parser(group.name, **kwargs)
    if not full_tree and remaining is None:
        return
    dest = group.dest or f"{group.name.replace('-', '_')}_command"
    group_sub = group_p.add_subparsers(dest=dest, required=True)
    child_path = parent_path + (group.name,)
    for child in _visible_children(group, group_exposure, surface, allowed_dotted, child_path, leaf_visible):
        child_remaining: tuple[str, ...] | None = None
        if full_tree:
            child_remaining = ()
        elif isinstance(child, (GroupNode, DeferredGroup)) and child.flatten:
            child_remaining = remaining
        elif remaining and isinstance(child, (GroupNode, DeferredGroup)) and child.name == remaining[0]:
            child_remaining = remaining[1:]
        _attach(
            group_sub,
            child,
            child_path,
            group_exposure,
            surface,
            root,
            index,
            allowed_dotted,
            bind,
            leaf_defaults,
            leaf_visible,
            full_tree,
            child_remaining,
        )


def _attach_leaf(
    subparsers: _SubParsersAction[Any],
    leaf: Leaf,
    parent_path: tuple[str, ...],
    root: GroupNode,
    index: ToolCatalog | None,
    bind: Callable[[TreeEntry], Any] | None,
    leaf_defaults: Callable[[Any], dict[str, Any]] | None,
    leaf_visible: Callable[[Any], bool] | None,
) -> None:
    dotted = ".".join(parent_path + (leaf.name,))
    bound: ToolDefinition = (
        index.by_dotted[dotted] if index is not None else resolve_tool(root, parent_path + (leaf.name,), bind=bind)
    )

    tool = bound.tool
    help_text = (leaf.help if leaf.help is not None else tool.summary) or leaf.name
    kwargs: dict[str, Any] = dict(leaf.parser_kwargs)
    if help_text and "help" not in kwargs:
        kwargs["help"] = help_text
    p = subparsers.add_parser(leaf.name, **kwargs)
    configure_leaf_parser(tool, p)

    defaults: dict[str, Any] = {"_bound": bound, "func": tool.fn}
    if leaf_defaults is not None:
        defaults.update(leaf_defaults(bound))
    p.set_defaults(**defaults)


def _visible_children(
    group: GroupNode | DeferredGroup,
    group_exposure: Exposure,
    surface: str,
    allowed_dotted: frozenset[str] | None,
    child_prefix: tuple[str, ...],
    leaf_visible: Callable[[Any], bool] | None,
) -> list[Leaf | GroupNode | DeferredGroup]:
    """Children of `group` visible on `surface`, in declaration order.

    `child_prefix` is the dotted-path prefix under which the children live (it
    excludes the group's own name for `flatten` groups, matching how `_attach`
    splices them). It is used only to test membership against `allowed_dotted`.
    """
    visible: list[Leaf | GroupNode | DeferredGroup] = []
    for child in group.children:
        if _is_visible(child, group_exposure, surface, allowed_dotted, child_prefix, leaf_visible):
            visible.append(child)
    return visible


def _is_visible(
    node: Leaf | GroupNode | DeferredGroup,
    parent_exposure: Exposure,
    surface: str,
    allowed_dotted: frozenset[str] | None,
    node_prefix: tuple[str, ...],
    leaf_visible: Callable[[Any], bool] | None,
) -> bool:
    node_exposure = effective_exposure(node.exposure, parent_exposure)
    if isinstance(node, Leaf):
        dotted = ".".join(node_prefix + (node.name,))
        if allowed_dotted is not None:
            return dotted in allowed_dotted
        if leaf_visible is not None and not leaf_visible(node):
            return False
        return surface in node_exposure
    if allowed_dotted is not None:
        prefix = node_prefix if node.flatten else node_prefix + (node.name,)
        return any(tuple(dotted.split("."))[: len(prefix)] == prefix for dotted in allowed_dotted)
    if isinstance(node, DeferredGroup):
        return surface in node_exposure
    # A group is visible iff it has at least one visible descendant. `flatten`
    # groups contribute no path segment, so their children stay at `node_prefix`.
    child_prefix = node_prefix if node.flatten else node_prefix + (node.name,)
    return any(
        _is_visible(child, node_exposure, surface, allowed_dotted, child_prefix, leaf_visible)
        for child in node.children
    )
