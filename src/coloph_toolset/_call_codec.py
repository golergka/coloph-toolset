"""Shared semantic codec for CLI literals and native model tool calls."""

from __future__ import annotations

import argparse
import json
import re
import shlex
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ._decorator import ToolParam
from ._index import ToolCatalog, ToolDefinition


class PromptToolReferenceError(RuntimeError):
    """A prompt's executable command literal is invalid or unavailable."""


@dataclass(frozen=True, eq=False)
class ToolCall:
    """One validated tool operation, independent of CLI/provider wire syntax."""

    tool: ToolDefinition
    arguments: dict[str, Any]

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ToolCall)
            and self.tool.stable_id == other.tool.stable_id
            and self.arguments == other.arguments
        )

    def __hash__(self) -> int:
        return hash((self.tool.stable_id, _freeze(self.arguments)))


@dataclass(frozen=True, eq=False)
class ToolCallTemplate(ToolCall):
    """A schema example derived from a non-executable CLI documentation literal."""


def tool_call_template(tool: ToolDefinition) -> ToolCallTemplate:
    """Build the smallest visible-argument example for an already-resolved tool."""
    example = getattr(tool.tool, "model_example", None)
    if example is not None:
        return ToolCallTemplate(tool, dict(example))
    hidden = set(tool.tool.model_hidden_args)
    return ToolCallTemplate(
        tool,
        {
            param.name: _template_default(param)
            for param in tool.tool.params
            if param.required and param.name not in hidden
        },
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _flags(param: ToolParam) -> list[str]:
    primary = param.cli.flag if param.cli is not None and param.cli.flag is not None else None
    aliases = list(param.cli.aliases) if param.cli is not None else []
    return [*aliases, primary or "--" + param.name.replace("_", "-")]


def _boolean(value: str) -> bool:
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def parameter_flags(param: ToolParam) -> list[str]:
    flags = _flags(param)
    if param.type is bool:
        flags += ["--no-" + flag[2:] for flag in flags if flag.startswith("--")]
    return flags


def validate_cli(tool: Any) -> None:
    seen = {"-h", "--help"}
    for param in tool.params:
        flags = parameter_flags(param)
        for flag in flags:
            if not re.fullmatch(r"--[a-zA-Z0-9][a-zA-Z0-9_-]*|-[a-zA-Z0-9]", flag):
                raise ValueError(f"invalid CLI flag {flag!r}")
            if flag in seen:
                raise ValueError(f"conflicting CLI flag {flag!r}")
            seen.add(flag)


def _add_param(parser: ArgumentParser, param: ToolParam) -> None:
    flags = _flags(param)
    kwargs: dict[str, Any] = {"dest": param.name, "help": param.description or None, "default": argparse.SUPPRESS}
    if param.cli is not None and param.cli.metavar is not None:
        kwargs["metavar"] = param.cli.metavar
    if param.type is bool:
        group = parser.add_mutually_exclusive_group(required=param.required)
        group.add_argument(*flags, nargs="?", const=True, type=_boolean, **kwargs)
        negative = ["--no-" + flag[2:] for flag in flags if flag.startswith("--")]
        if negative:
            group.add_argument(*negative, action="store_false", **kwargs)
        return
    if param.repeated:
        kwargs["action"] = "append"
    if param.choices is not None:
        kwargs["choices"] = list(param.choices)
    if param.type in (int, float):
        kwargs["type"] = param.type
    kwargs["required"] = param.required
    parser.add_argument(*flags, **kwargs)


def configure_leaf_parser(tool: Any, parser: ArgumentParser, *, help_override: str | None = None) -> None:
    validate_cli(tool)
    parser.description = help_override or tool.description or None
    for param in tool.params:
        _add_param(parser, param)


def declaration_for(tool: Any) -> Any:
    """Application wrappers retain their public declaration in ``declaration``."""
    return getattr(tool, "declaration", tool)


def kwargs_from_args(
    tool: Any, args: Namespace, *, argument_decoder: Callable[[Any], Any] | None = None
) -> dict[str, Any]:
    values = {param.name: getattr(args, param.name) for param in tool.params if hasattr(args, param.name)}
    if argument_decoder is not None:
        values = {name: argument_decoder(value) for name, value in values.items()}
    result: dict[str, Any] = declaration_for(tool).validate_arguments(values, include_hidden=True)
    return result


class ToolArgumentParser(ArgumentParser):
    """An argparse parser that validates its resolved leaf before dispatch."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def validate_tool_arguments(self, tool: Any, args: Namespace) -> dict[str, Any]:
        """Validate a leaf; adapters may decode their transport before validation."""
        return kwargs_from_args(tool, args)

    def parse_known_args(self, args: Any = None, namespace: Any = None) -> tuple[Any, list[str]]:
        from pydantic import ValidationError

        parsed, unknown = super().parse_known_args(args, namespace)
        bound = getattr(parsed, "_bound", None)
        if bound is not None and not unknown:
            try:
                values = self.validate_tool_arguments(bound.tool, parsed)
            except ValidationError as exc:
                self.error(str(exc))
            for name, value in values.items():
                setattr(parsed, name, value)
        return parsed, unknown


def _cli_tokens(text: str, executable: str) -> list[str]:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise PromptToolReferenceError(f"malformed command quoting: {exc}") from exc
    if not tokens or tokens[0] != executable:
        raise PromptToolReferenceError(f"command literal must start with {executable}")
    return tokens


def _resolve_cli_tool(tokens: list[str], catalog: ToolCatalog) -> tuple[ToolDefinition, list[str]]:
    if any(token in {"--help", "-h"} for token in tokens[1:]):
        raise PromptToolReferenceError("CLI help references cannot be represented as native calls")
    path_tokens = tokens[1:]
    candidates = [bound for bound in catalog.by_dotted.values() if list(bound.path) == path_tokens[: len(bound.path)]]
    if not candidates:
        raise PromptToolReferenceError("unknown command")
    bound = max(candidates, key=lambda item: len(item.path))
    remaining = path_tokens[len(bound.path) :]
    return bound, remaining


def decode_cli_call(
    text: str,
    *,
    catalog: ToolCatalog,
    executable: str = "tool",
    argument_decoder: Callable[[Any], Any] | None = None,
) -> ToolCall:
    """Decode one complete, named-only executable command literal."""
    from pydantic import ValidationError

    tokens = _cli_tokens(text, executable)
    bound, remaining = _resolve_cli_tool(tokens, catalog)
    parser = ArgumentParser(
        prog=executable + " " + " ".join(bound.path), add_help=False, exit_on_error=False, allow_abbrev=False
    )
    configure_leaf_parser(bound.tool, parser)
    try:
        args = parser.parse_args(remaining)
    except (argparse.ArgumentError, SystemExit) as exc:
        raise PromptToolReferenceError(f"invalid arguments for {bound.dotted}: {exc}") from exc
    try:
        kwargs = kwargs_from_args(bound.tool, args, argument_decoder=argument_decoder)
    except ValidationError as exc:
        raise PromptToolReferenceError(f"invalid arguments for {bound.dotted}: {exc}") from exc
    hidden = set(bound.tool.model_hidden_args)
    explicitly_supplied = set(vars(args))
    if hidden & explicitly_supplied:
        raise PromptToolReferenceError(f"{bound.dotted} has arguments unavailable to native models")
    return ToolCall(
        bound,
        {
            param.name: kwargs[param.name]
            for param in bound.tool.params
            if param.name not in hidden and (param.required or param.name in explicitly_supplied)
        },
    )


def _template_default(param: ToolParam) -> Any:
    if param.required:
        if param.repeated:
            return [f"<{param.name}>"]
        if param.choices:
            return "<" + "|".join(str(choice) for choice in param.choices) + ">"
        return f"<{param.name}>"
    return [] if param.repeated else param.default


def decode_cli_template(text: str, *, catalog: ToolCatalog, executable: str = "tool") -> ToolCallTemplate:
    """Decode documentation syntax into the native example for the real tool schema.

    A template is never dispatched. Its placeholder values exist only in prompt
    documentation, while its action name and parameter keys are produced from
    the same catalog record as a real call.
    """
    tokens = _cli_tokens(text, executable)
    bound, remaining = _resolve_cli_tool(tokens, catalog)
    hidden = set(bound.tool.model_hidden_args)
    values = tool_call_template(bound).arguments
    by_flag = {flag: param for param in bound.tool.params for flag in parameter_flags(param)}
    index = 0
    while index < len(remaining):
        raw_flag = remaining[index]
        if raw_flag == "...":
            index += 1
            continue
        optional = raw_flag.startswith("[") or raw_flag.endswith("]")
        flag = raw_flag.strip("[]")
        if flag in {"--help", "-h"}:
            raise PromptToolReferenceError("help references are not executable tool calls")
        if not flag.startswith("-"):
            raise PromptToolReferenceError(f"invalid template argument {raw_flag!r} for {bound.dotted}")
        value_from_equals: str | None = None
        if "=" in flag:
            flag, value_from_equals = flag.split("=", 1)
        param = by_flag.get(flag)
        if param is None:
            raise PromptToolReferenceError(f"unknown template argument {flag!r} for {bound.dotted}")
        if param.name in hidden:
            raise PromptToolReferenceError(f"{bound.dotted} has arguments unavailable to native models")
        if param.type is bool:
            if not optional:
                values[param.name] = not flag.startswith("--no-")
            index += 1
            continue
        if value_from_equals is None:
            index += 1
            if index >= len(remaining):
                raise PromptToolReferenceError(f"missing template value for {flag!r}")
            raw_value = remaining[index]
        else:
            raw_value = value_from_equals
        if optional or raw_value.startswith("[") or raw_value.endswith("]"):
            index += 1
            continue
        value = raw_value.strip("[]")
        if param.repeated:
            values[param.name] = [*(values.get(param.name) or []), value]
        else:
            values[param.name] = value
        index += 1
    return ToolCallTemplate(bound, values)


def encode_cli_call(call: ToolCall, *, executable: str = "tool") -> str:
    if not executable or any(character.isspace() for character in executable):
        raise ValueError("executable must be one non-empty token")
    if not isinstance(call, ToolCallTemplate):
        _validate_native_arguments(call.tool, call.arguments)
    tokens = [executable, *call.tool.path]
    for param in call.tool.params:
        if param.name not in call.arguments:
            continue
        value = call.arguments[param.name]
        flag = _flags(param)[-1]
        if value is None or (param.repeated and not value):
            raise PromptToolReferenceError(
                f"{param.name}: null and empty lists have no CLI representation; omit to use the default"
            )
        if param.type is bool:
            if value:
                tokens.append(flag)
            elif flag.startswith("--"):
                tokens.append("--no-" + flag[2:])
            else:
                tokens.extend((flag, "false"))
        elif param.repeated:
            for item in value:
                tokens.extend([f"{flag}={item}"] if str(item).startswith("-") else [flag, str(item)])
        else:
            tokens.extend([f"{flag}={value}"] if str(value).startswith("-") else [flag, str(value)])
    return shlex.join(tokens)


def encode_native_call(call: ToolCall, *, hierarchical: bool = False) -> dict[str, Any]:
    """Encode the exact logical native call form selected by the adapter."""
    if hierarchical:
        return {
            "action": call.tool.path[0],
            "parameters": {
                "command_path": ".".join(call.tool.path[1:]),
                "arguments": call.arguments,
            },
        }
    return {"action": call.tool.agent_name, "parameters": call.arguments}


def encode_native_group_call(root_name: str, command_path: str) -> dict[str, Any]:
    """Encode navigation to a non-executable group in hierarchical native mode."""
    if not root_name or not command_path:
        raise ValueError("native group call requires a root action and relative command path")
    return {
        "action": root_name,
        "parameters": {"command_path": command_path, "arguments": {}},
    }


def native_call_json(call: ToolCall, *, hierarchical: bool = False) -> str:
    return json.dumps(
        encode_native_call(call, hierarchical=hierarchical), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _validate_native_arguments(bound: ToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate concrete values while retaining the codec's explicit-argument representation."""
    from pydantic import ValidationError

    try:
        validated = declaration_for(bound.tool).validate_arguments(arguments)
    except ValidationError as exc:
        raise PromptToolReferenceError(f"invalid arguments for {bound.dotted}: {exc}") from exc
    return {name: validated[name] for name in arguments}


def decode_native_call(
    value: dict[str, Any], *, tools: Iterable[ToolDefinition], hierarchical: bool = False
) -> ToolCall:
    action = value.get("action")
    parameters = value.get("parameters")
    if not isinstance(action, str) or not isinstance(parameters, dict):
        raise PromptToolReferenceError("native call requires action and object parameters")
    available = tuple(tools)
    if hierarchical:
        command_path = parameters.get("command_path")
        arguments = parameters.get("arguments")
        if not isinstance(command_path, str) or not isinstance(arguments, dict):
            raise PromptToolReferenceError("hierarchical native call requires command_path and object arguments")
        path = (action, *tuple(part for part in command_path.split(".") if part))
        matches = [bound for bound in available if bound.path == path]
        if len(matches) != 1:
            raise PromptToolReferenceError(f"native command {'.'.join(path)!r} is unavailable")
        bound = matches[0]
        visible = {param.name for param in bound.tool.params} - set(bound.tool.model_hidden_args)
        required = {param.name for param in bound.tool.params if param.required} - set(bound.tool.model_hidden_args)
        if not required <= set(arguments) <= visible:
            raise PromptToolReferenceError(f"native parameters for {action} do not match its schema")
        return ToolCall(bound, _validate_native_arguments(bound, arguments))
    matches = [bound for bound in available if bound.agent_name == action]
    if len(matches) != 1:
        raise PromptToolReferenceError(f"native action {action!r} is unavailable")
    bound = matches[0]
    visible = {param.name for param in bound.tool.params} - set(bound.tool.model_hidden_args)
    required = {param.name for param in bound.tool.params if param.required} - set(bound.tool.model_hidden_args)
    if not required <= set(parameters) <= visible:
        raise PromptToolReferenceError(f"native parameters for {action} do not match its schema")
    return ToolCall(bound, _validate_native_arguments(bound, parameters))


__all__ = [
    "PromptToolReferenceError",
    "ToolCall",
    "ToolCallTemplate",
    "configure_leaf_parser",
    "decode_cli_call",
    "decode_cli_template",
    "decode_native_call",
    "encode_cli_call",
    "encode_native_call",
    "kwargs_from_args",
    "native_call_json",
]
