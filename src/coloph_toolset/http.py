"""Generated Starlette routes for an authorized tool selection.

This file began as Coloph's ``api/_tool_routes.py``. Applications provide
authentication, per-request authorization, context construction, invocation,
and response enrichment. The adapter owns HTTP decoding and error mapping.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ._index import ToolDefinition
from ._invoke import InvocationResult, ToolUnavailableError

AuthorityT = TypeVar("AuthorityT")
ContextT = TypeVar("ContextT")


class HttpToolError(RuntimeError):
    """An expected public HTTP failure."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.public_message = message
        super().__init__(message)


class HttpAuthenticationError(HttpToolError):
    """The request did not establish an authenticated authority."""

    def __init__(self, message: str = "authentication required") -> None:
        super().__init__(401, "tool_unauthenticated", message)


class HttpAuthorizationError(HttpToolError):
    """The authenticated authority cannot call this tool."""

    def __init__(self, message: str = "tool is unavailable") -> None:
        super().__init__(403, "tool_forbidden", message)


class HttpUserError(HttpToolError):
    """The tool rejected correctable caller input."""

    def __init__(self, message: str) -> None:
        super().__init__(400, "tool_user_error", message)


@dataclass(frozen=True)
class HttpToolRequest:
    """Validated tool arguments and application-owned envelope values."""

    arguments: Mapping[str, Any]
    context: Mapping[str, Any]


Authenticate = Callable[[Request], AuthorityT | Awaitable[AuthorityT]]
Authorize = Callable[[AuthorityT, ToolDefinition, Mapping[str, Any]], bool | Awaitable[bool]]
ContextFactory = Callable[
    [Request, AuthorityT, ToolDefinition, Mapping[str, Any]],
    ContextT | Awaitable[ContextT],
]
Invoke = Callable[[ToolDefinition, ContextT, Mapping[str, Any]], Any | Awaitable[Any]]
NormalizeArguments = Callable[[ToolDefinition, Mapping[str, Any]], Mapping[str, Any]]
ResponseContext = Callable[
    [ToolDefinition, ContextT, Mapping[str, Any], Any],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
ErrorObserver = Callable[[BaseException, Request, ToolDefinition], None | Awaitable[None]]


def endpoint_for_tool(tool: ToolDefinition, *, prefix: str = "/tools/") -> str:
    """Return the deterministic endpoint for one stable dotted tool ID."""
    normalized_prefix = "/" + prefix.strip("/") + "/"
    parts = tool.dotted.split(".")
    if not parts or any(not part or "/" in part or "{" in part or "}" in part for part in parts):
        raise ValueError(f"invalid HTTP tool id: {tool.dotted!r}")
    return normalized_prefix + "/".join(parts)


def decode_tool_request(
    tool: ToolDefinition,
    body: object,
    *,
    context_fields: frozenset[str] = frozenset(),
    include_hidden: bool = False,
    normalize_arguments: NormalizeArguments | None = None,
) -> HttpToolRequest:
    """Decode one strict JSON envelope and normalize its argument object."""
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    allowed = {"args", *context_fields}
    if unknown := set(body) - allowed:
        raise ValueError("unknown request fields: " + ", ".join(sorted(unknown)))
    args = body.get("args")
    if not isinstance(args, dict):
        raise ValueError("args must be a JSON object")
    normalized = (
        tool.tool.validate_arguments(args, include_hidden=include_hidden)
        if normalize_arguments is None
        else dict(normalize_arguments(tool, args))
    )
    context = {name: body.get(name) for name in context_fields}
    return HttpToolRequest(arguments=normalized, context=context)


def _error_response(error: HttpToolError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": error.code, "message": error.public_message}},
        status_code=error.status_code,
    )


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    """Await async callbacks and keep sync callbacks off the event-loop thread."""
    if inspect.iscoroutinefunction(callback):
        return await callback(*args)
    result = await asyncio.to_thread(callback, *args)
    return await result if inspect.isawaitable(result) else result


@dataclass(frozen=True)
class StarletteToolAdapter(Generic[AuthorityT, ContextT]):
    """Application callbacks for generated Starlette tool routes."""

    authenticate: Authenticate[AuthorityT]
    context_factory: ContextFactory[AuthorityT, ContextT]
    invoke: Invoke[ContextT]
    authorize: Authorize[AuthorityT] | None = None
    response_context: ResponseContext[ContextT] | None = None
    observe_internal_error: ErrorObserver | None = None
    context_fields: frozenset[str] = frozenset()
    include_hidden_arguments: bool = False
    normalize_arguments: NormalizeArguments | None = None
    prefix: str = "/tools/"

    async def _internal_error(self, error: BaseException, request: Request, tool: ToolDefinition) -> Response:
        if self.observe_internal_error is not None:
            try:
                await _call(self.observe_internal_error, error, request, tool)
            except Exception:
                pass
        return _error_response(HttpToolError(500, "tool_internal_error", "tool execution failed"))

    async def handle(self, request: Request, tool: ToolDefinition) -> Response:
        """Authenticate, decode, authorize, invoke, and map one request."""
        try:
            authority = cast(AuthorityT, await _call(self.authenticate, request))
        except HttpToolError as exc:
            return _error_response(exc)
        except Exception as exc:
            return await self._internal_error(exc, request, tool)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error_response(HttpToolError(400, "invalid_tool_request", "request body must be valid JSON"))
        except Exception as exc:
            return await self._internal_error(exc, request, tool)

        try:
            decoded = decode_tool_request(
                tool,
                body,
                context_fields=self.context_fields,
                include_hidden=self.include_hidden_arguments,
                normalize_arguments=self.normalize_arguments,
            )
        except (ValidationError, ValueError) as exc:
            return _error_response(HttpToolError(422, "invalid_tool_args", str(exc)))

        try:
            if self.authorize is not None and not await _call(self.authorize, authority, tool, decoded.context):
                raise HttpAuthorizationError()
            context = cast(ContextT, await _call(self.context_factory, request, authority, tool, decoded.context))
            invoked = await _call(self.invoke, tool, context, decoded.arguments)
            result = invoked.output if isinstance(invoked, InvocationResult) else invoked
            additions: Mapping[str, Any] = {}
            if self.response_context is not None:
                additions = cast(
                    Mapping[str, Any],
                    await _call(self.response_context, tool, context, decoded.context, result),
                )
            return JSONResponse({**additions, "tool": tool.dotted, "result": result})
        except HttpToolError as exc:
            return _error_response(exc)
        except ToolUnavailableError:
            return _error_response(HttpAuthorizationError())
        except Exception as exc:
            return await self._internal_error(exc, request, tool)

    def routes(self, tools: Sequence[ToolDefinition]) -> list[Route]:
        """Build one collision-checked POST route per selected tool."""
        routes: list[Route] = []
        seen_paths: set[str] = set()
        seen_names: set[str] = set()
        for tool in tools:
            path = endpoint_for_tool(tool, prefix=self.prefix)
            name = f"tool:{tool.dotted}"
            if path in seen_paths:
                raise ValueError(f"duplicate generated HTTP tool path: {path}")
            if name in seen_names:
                raise ValueError(f"duplicate generated HTTP tool name: {name}")
            seen_paths.add(path)
            seen_names.add(name)

            async def handler(request: Request, _tool: ToolDefinition = tool) -> Response:
                return await self.handle(request, _tool)

            routes.append(Route(path, handler, methods=["POST"], name=name))
        return routes


def build_starlette_tool_routes(
    tools: Sequence[ToolDefinition],
    *,
    authenticate: Authenticate[AuthorityT],
    context_factory: ContextFactory[AuthorityT, ContextT],
    invoke: Invoke[ContextT],
    authorize: Authorize[AuthorityT] | None = None,
    response_context: ResponseContext[ContextT] | None = None,
    observe_internal_error: ErrorObserver | None = None,
    context_fields: frozenset[str] = frozenset(),
    include_hidden_arguments: bool = False,
    normalize_arguments: NormalizeArguments | None = None,
    prefix: str = "/tools/",
) -> list[Route]:
    """Build generated Starlette routes from explicit application callbacks."""
    adapter = StarletteToolAdapter(
        authenticate=authenticate,
        context_factory=context_factory,
        invoke=invoke,
        authorize=authorize,
        response_context=response_context,
        observe_internal_error=observe_internal_error,
        context_fields=context_fields,
        include_hidden_arguments=include_hidden_arguments,
        normalize_arguments=normalize_arguments,
        prefix=prefix,
    )
    return adapter.routes(tools)


__all__ = [
    "HttpAuthenticationError",
    "HttpAuthorizationError",
    "HttpToolError",
    "HttpToolRequest",
    "HttpUserError",
    "StarletteToolAdapter",
    "build_starlette_tool_routes",
    "decode_tool_request",
    "endpoint_for_tool",
]
