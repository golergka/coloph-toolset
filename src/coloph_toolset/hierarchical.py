"""Authorized hierarchical discovery and first-use tool dispatch."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol

from ._call_codec import tool_call_template
from ._compiled_tree import CompiledToolTree, CompiledToolTreeNode
from ._index import ToolDefinition


class HierarchicalInputError(ValueError):
    """An input error that a tool caller can correct."""


@dataclass(frozen=True)
class Documentation:
    """One loaded document and the fingerprint of its effective content."""

    name: str
    text: str
    fingerprint: str


class DocumentationLoader(Protocol):
    """Load the current versions of named documents for one visible tool."""

    def __call__(self, names: tuple[str, ...], tool: ToolDefinition) -> Sequence[Documentation]: ...


@dataclass
class HierarchicalState:
    """Mutable documentation state owned by one agent invocation."""

    lock: Any = field(default_factory=RLock, repr=False)
    delivered_tools: set[str] = field(default_factory=set)
    guidance_seen: set[str] = field(default_factory=set)
    document_fingerprints: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedToolCall:
    """One resolved and validated leaf call ready for application dispatch."""

    tool: ToolDefinition
    arguments: dict[str, Any]
    append_documentation: bool = False
    documents: tuple[Documentation, ...] = ()


@dataclass(frozen=True)
class HierarchicalCompletion:
    """A finished leaf result and whether it may terminate the invocation."""

    result: Any
    terminal: bool


DocumentNames = Callable[[ToolDefinition], Sequence[str]]
PacketRenderer = Callable[[ToolDefinition, bool], str]
ArgumentModel = Callable[[ToolDefinition], Any]


def root_dispatch_schema() -> dict[str, Any]:
    """Return the advertised root envelope while leaf validation stays diagnostic."""
    return {
        "type": "object",
        "properties": {
            "command_path": {
                "type": "string",
                "description": (
                    "Relative dotted path within this tool group, for example `node.show`. "
                    "Omit it to list the top-level subcommands."
                ),
            },
            "arguments": {
                "type": "object",
                "description": (
                    "Arguments for the selected leaf command. Use an empty object when browsing a command group. "
                    "A leaf's accepted fields are provided in its first-use documentation."
                ),
                "additionalProperties": True,
            },
        },
        "additionalProperties": False,
    }


def root_call_template(tool: ToolDefinition) -> str:
    """Render the literal argument object for a hierarchical root call."""
    template = tool_call_template(tool)
    return json.dumps(
        {"command_path": ".".join(tool.path[1:]), "arguments": template.arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def first_use_packet(tool: ToolDefinition, command_invoked: bool = False) -> str:
    """Render copyable leaf help without exposing a full JSON Schema."""
    hidden = set(tool.tool.model_hidden_args)
    required = [parameter for parameter in tool.params if parameter.required and parameter.name not in hidden]
    optional = [parameter for parameter in tool.params if not parameter.required and parameter.name not in hidden]
    details = ""
    if required:
        details += "\n\nRequired arguments:\n" + "\n".join(
            f"- {parameter.name}: {parameter.description}" for parameter in required
        )
    if optional:
        details += "\n\nOptional arguments:\n" + "\n".join(
            f"- {parameter.name}: {parameter.description}" for parameter in optional
        )
    status = (
        "This command has run. Use its documented command path and arguments if you call it again."
        if command_invoked
        else "Read this first-use documentation. The command did not run. Call the same command again."
    )
    return (
        f"{status}\n\n{tool.description}\n\nCommand path: {'.'.join(tool.path[1:])}{details}\n\n"
        f"Call example:\n{root_call_template(tool)}"
    )


def argument_validation_error(tool: ToolDefinition, error: Any) -> str:
    """Render canonical Pydantic errors with a valid retry template."""
    errors = json.dumps(error.errors(include_url=False), ensure_ascii=False, sort_keys=True, default=str)
    return (
        f"Invalid arguments for {tool.dotted}.\nValidation errors:\n{errors}\n\n"
        f"Call tool: {tool.path[0]}\nArguments:\n{root_call_template(tool)}"
    )


def terminal_tool_succeeded(result: Any) -> bool:
    """Return whether a result represents successful business execution."""
    return not (isinstance(result, Mapping) and (result.get("command_invoked") is False or "error" in result))


def _path_help(tree: CompiledToolTree, path: tuple[str, ...], *, invalid_segment: str | None = None) -> str:
    suggestion = ""
    if invalid_segment:
        alias_path = tree.alias_path(invalid_segment)
        if alias_path is not None:
            suggestion = f" Did you mean command_path={'.'.join(alias_path[1:])!r}?"
    return suggestion + "\n" + tree.render_help(path)


def _separator_note(raw_path: str) -> str:
    return " Command paths use space-separated segments." if "_" in raw_path or "." in raw_path else ""


def _resolve_segments(
    tree: CompiledToolTree,
    *,
    root_path: tuple[str, ...],
    segments: Sequence[str],
) -> tuple[tuple[str, ...], CompiledToolTreeNode]:
    path = root_path
    for index, segment in enumerate(segments, start=1):
        parent = tree.resolve(path)
        assert parent is not None
        if parent.tool is not None:
            raise HierarchicalInputError(
                f"{'.'.join(path)} is a command and has no subcommands. Unexpected path segment {index}: {segment!r}."
                + _path_help(tree, path, invalid_segment=segment)
            )
        if not segment or segment not in parent.children:
            raise HierarchicalInputError(
                f"Invalid command path segment {index}: {segment!r}." + _path_help(tree, path, invalid_segment=segment)
            )
        path = (*path, segment)
    node = tree.resolve(path)
    assert node is not None
    return path, node


def resolve_root_path(
    tree: CompiledToolTree,
    *,
    root_name: str,
    payload: Mapping[str, Any],
) -> tuple[tuple[str, ...], CompiledToolTreeNode]:
    """Resolve a relative path and identify the first invalid segment."""
    supplied_path = "command_path" in payload
    unexpected = sorted(set(payload) - {"command_path", "arguments"})
    if unexpected:
        prefix = "command_path is required. " if not supplied_path else ""
        raise HierarchicalInputError(
            prefix + f"Unrecognized root fields: {', '.join(unexpected)}. Expected fields: command_path, arguments."
        )
    raw_path = payload.get("command_path")
    arguments = payload.get("arguments")
    root_path = (root_name,)
    root_node = tree.resolve(root_path)
    if root_node is None:
        raise ValueError(f"unknown root tool {root_name!r}")
    if not supplied_path or raw_path is None:
        if arguments not in (None, {}) and root_node.tool is None:
            raise HierarchicalInputError(
                "command_path is required when arguments are supplied. " + _path_help(tree, root_path)
            )
        return root_path, root_node
    if not isinstance(raw_path, str):
        raise HierarchicalInputError(
            f"command_path must be a string, got {type(raw_path).__name__}. " + _path_help(tree, root_path)
        )
    if raw_path == ".":
        return root_path, root_node
    if not raw_path:
        if arguments not in (None, {}) and root_node.tool is None:
            raise HierarchicalInputError(
                "command_path is required when arguments are supplied. " + _path_help(tree, root_path)
            )
        return root_path, root_node
    try:
        return _resolve_segments(tree, root_path=root_path, segments=raw_path.split("."))
    except HierarchicalInputError as direct_error:
        fallback = tuple(segment for segment in re.split(r"[\s._]+", raw_path) if segment)
        if fallback != (raw_path,):
            try:
                return _resolve_segments(tree, root_path=root_path, segments=fallback)
            except HierarchicalInputError:
                pass
        raise HierarchicalInputError(f"{direct_error}{_separator_note(raw_path)}") from direct_error


class HierarchicalToolAdapter:
    """Prepare and finish calls against one authorized tool selection."""

    def __init__(
        self,
        tools: Sequence[ToolDefinition],
        *,
        document_names: DocumentNames | None = None,
        document_loader: DocumentationLoader | None = None,
        packet_renderer: PacketRenderer = first_use_packet,
        argument_model: ArgumentModel | None = None,
    ) -> None:
        self.tools = tuple(tools)
        self.tree = CompiledToolTree(self.tools)
        model_for = argument_model or (lambda tool: getattr(tool.tool, "declaration", tool.tool).argument_model())
        self._argument_models = {tool.dotted: model_for(tool) for tool in self.tools}
        self._document_names = document_names or (lambda tool: tuple(getattr(tool.tool, "required_documents", ())))
        self._document_loader = document_loader
        self._packet_renderer = packet_renderer

    @property
    def root_names(self) -> tuple[str, ...]:
        return self.tree.root_names

    def root_description(self, root_name: str) -> str:
        root = self.tree.resolve((root_name,))
        if root is None:
            raise ValueError(f"unknown root tool {root_name!r}")
        behavior = (
            "Browse available subcommands or execute one. Calling a command-group path with empty arguments "
            "lists its subcommands. The first call to a leaf returns its required documentation without executing "
            "it. Read the documentation, then call the leaf again."
        )
        return f"{root.tool.description}\n\n{behavior}" if root.tool is not None else behavior

    def _documents(self, tool: ToolDefinition) -> tuple[Documentation, ...]:
        names = tuple(self._document_names(tool))
        if not names:
            return ()
        if self._document_loader is None:
            raise RuntimeError(f"{tool.dotted} requires documentation but no loader is configured")
        documents = tuple(self._document_loader(names, tool))
        returned = [document.name for document in documents]
        missing = [name for name in names if name not in returned]
        if missing:
            raise RuntimeError("documentation loader omitted: " + ", ".join(missing))
        return documents

    @staticmethod
    def _documents_changed(state: HierarchicalState, documents: Sequence[Documentation]) -> bool:
        return any(state.document_fingerprints.get(document.name) != document.fingerprint for document in documents)

    @staticmethod
    def _record_delivery(
        state: HierarchicalState,
        tool: ToolDefinition,
        documents: Sequence[Documentation],
    ) -> None:
        state.delivered_tools.add(tool.dotted)
        state.document_fingerprints.update({document.name: document.fingerprint for document in documents})

    @staticmethod
    def _document_sections(documents: Sequence[Documentation]) -> str:
        return "\n\n".join(f"## Required document: {document.name}\n{document.text}" for document in documents)

    def _packet(
        self,
        tool: ToolDefinition,
        *,
        command_invoked: bool,
        documents: Sequence[Documentation],
        guidance: str,
    ) -> str:
        packet = self._packet_renderer(tool, command_invoked)
        sections = self._document_sections(documents)
        if sections:
            packet += "\n\n" + sections
        if guidance:
            packet += "\n\n## Required first-use guidance\n" + guidance
        return packet

    def _group_documentation(self, path: tuple[str, ...], node: CompiledToolTreeNode) -> dict[str, Any]:
        lines = [self.tree.render_help(path), "", "Available subcommands:"]
        for child_name in node.children:
            child = self.tree.resolve((*path, child_name))
            assert child is not None
            relative = ".".join((*path[1:], child_name))
            if child.tool is None:
                example = {"command_path": relative, "arguments": {}}
                instruction = "Open"
            else:
                example = {"command_path": relative, "arguments": tool_call_template(child.tool).arguments}
                instruction = "Example call"
            lines.append(f"- {child_name}")
            lines.append(f"  {instruction}: " + json.dumps(example, sort_keys=True, separators=(",", ":")))
        return {"documentation": "\n".join(lines), "command_invoked": False}

    def prepare(
        self,
        root_name: str,
        payload: Mapping[str, Any],
        state: HierarchicalState,
        *,
        required_tool_ids: Collection[str] = (),
    ) -> dict[str, Any] | PreparedToolCall:
        """Browse a group, deliver required documentation, or prepare a leaf call."""
        path, node = resolve_root_path(self.tree, root_name=root_name, payload=payload)
        arguments = payload.get("arguments")
        if node.tool is None:
            if arguments not in (None, {}):
                raise HierarchicalInputError(
                    f"{'.'.join(path)} is a command group, not a command.\n" + self.tree.render_help(path)
                )
            return self._group_documentation(path, node)

        tool = node.tool
        with state.lock:
            documents = self._documents(tool)
            changed = self._documents_changed(state, documents)
            guidance = str(getattr(tool.tool, "first_use_guidance", None) or "").strip()
            post_execution = bool(getattr(tool.tool, "post_execution_first_use_documentation", False))
            needs_documentation = tool.dotted not in required_tool_ids and (
                tool.dotted not in state.delivered_tools or changed
            )
            if guidance and tool.dotted not in state.guidance_seen:
                state.guidance_seen.add(tool.dotted)
                if post_execution:
                    raise HierarchicalInputError(
                        f"TOOL_GUIDANCE_REQUIRED for {tool.dotted}: {guidance}\n\n"
                        "No tool side effect happened. Read this guidance, then call the same tool again "
                        "only if it is still the correct action."
                    )
            if needs_documentation and not post_execution:
                packet = self._packet(tool, command_invoked=False, documents=documents, guidance=guidance)
                self._record_delivery(state, tool, documents)
                return {"documentation": packet, "command_invoked": False}

            try:
                validated = (
                    self._argument_models[tool.dotted]
                    .model_validate({} if arguments is None else arguments)
                    .model_dump()
                )
            except Exception as error:
                from pydantic import ValidationError

                if not isinstance(error, ValidationError):
                    raise
                raise HierarchicalInputError(argument_validation_error(tool, error)) from error
            return PreparedToolCall(
                tool=tool,
                arguments=validated,
                append_documentation=needs_documentation and post_execution,
                documents=documents,
            )

    def complete(
        self,
        call: PreparedToolCall,
        result: Any,
        state: HierarchicalState,
        *,
        succeeded: bool = True,
    ) -> HierarchicalCompletion:
        """Finish a prepared call after application dispatch and resource finalization."""
        if not succeeded:
            return HierarchicalCompletion(result=result, terminal=False)
        rendered = result
        if call.append_documentation:
            with state.lock:
                packet = self._packet(call.tool, command_invoked=True, documents=call.documents, guidance="")
                self._record_delivery(state, call.tool, call.documents)
            rendered = (
                f"{result}\n\n## First-use documentation\n{packet}"
                if isinstance(result, str)
                else {"result": result, "documentation": packet, "command_invoked": True}
            )
        terminal = bool(getattr(call.tool.tool, "terminal", False)) and terminal_tool_succeeded(rendered)
        return HierarchicalCompletion(result=rendered, terminal=terminal)


__all__ = [
    "Documentation",
    "DocumentationLoader",
    "HierarchicalCompletion",
    "HierarchicalInputError",
    "HierarchicalState",
    "HierarchicalToolAdapter",
    "PreparedToolCall",
    "argument_validation_error",
    "first_use_packet",
    "resolve_root_path",
    "root_call_template",
    "root_dispatch_schema",
    "terminal_tool_succeeded",
]
