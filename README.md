# coloph-toolset

Declare Python tools once. Export their schema, organize them in catalogs, and run validated synchronous or asynchronous calls.

Version 0.6 adds authorized hierarchical discovery, first-use documentation gates, and terminal completion signals while preserving the declaration, catalog, CLI, runtime, HTTP, and direct Pydantic AI APIs.
The application owns authorization, business dependencies, resource behavior, and model providers.
The [roadmap](docs/roadmap.md) describes later adapters. The package remains on `0.x` while its interfaces settle.

## Install

```sh
uv add coloph-toolset
```

The package requires Python 3.11 or later and Pydantic 2.12 or later within major version 2.
No Coloph installation, database, credentials, HTTP framework, or agent framework is required.
Install the optional HTTP adapter with `uv add "coloph-toolset[http]"`.
Install the optional Pydantic AI adapter with `uv add "coloph-toolset[pydantic-ai]"`.

## Declare and validate

```python
from typing import Annotated
from pydantic import Field, ValidationError
from coloph_toolset import tool, tool_for


@tool()
def shipping_quote(
    ctx,
    quantity: Annotated[int, "Number of parcels", Field(gt=0)],
    destination: Annotated[str | None, "Country code, or null for collection"],
    insured: Annotated[bool, "Include insurance"] = False,
) -> dict[str, object]:
    return {"quantity": quantity, "destination": destination, "insured": insured}


declaration = tool_for(shipping_quote)
schema = declaration.json_schema()
arguments = declaration.validate_arguments({"quantity": "2", "destination": None})
result = shipping_quote(None, **arguments)

try:
    declaration.validate_arguments({"quantity": 0, "destination": None})
except ValidationError as error:
    print(error.errors(include_url=False))
```

Validation never calls the function. Calling the Python function directly does not apply validation.
The application owns execution and must use validated arguments at its dispatch boundary.

## Run tools

```python
from dataclasses import dataclass
from coloph_toolset import ToolContext, ToolRuntime


@dataclass
class Session:
    calls: int = 0


runtime = ToolRuntime(dependencies={"region": "eu"}, state_factory=Session)
result = runtime.invoke_sync(shipping_quote, {"quantity": 2, "destination": None})
print(result.raw)
print(result.output)
```

Use `await runtime.invoke(...)` in asynchronous code. The runtime awaits synchronous and asynchronous tools exactly once.
Add a `ResourceLifecycle` when each call needs a transaction, client, or other resource.

## Context and restricted arguments

The declaration expects a required first parameter named `ctx`.
Its type is application-owned and its annotation is not evaluated. Context never appears in the argument schema.

```python
@tool(model_hidden_args=("internal",))
def inspect_order(ctx, order: str, internal: bool = False):
    return ctx.lookup(order, internal=internal)


public = tool_for(inspect_order)
public.validate_arguments({"order": "demo"})
# An external "internal" argument raises ValidationError.
operator_schema = public.json_schema(include_hidden=True)
```

Hidden arguments require valid defaults. Only trusted application code can select `include_hidden=True`.
Argument projection is not an authorization system.

## Contracts and examples

- [Argument contract and API](docs/contracts.md)
- [Catalog and CLI contract](docs/catalogs-cli.md)
- [Runtime contract](docs/runtime.md)
- [Starlette HTTP adapter](docs/http.md)
- [Pydantic AI adapter](docs/pydantic-ai.md)
- [Hierarchical discovery and dispatch](docs/hierarchical.md)
- [Standalone shipping-quote project](examples/01-tool-declarations/README.md)
- [Standalone task CLI](examples/02-task-cli/README.md)
- [Standalone inventory runtime](examples/03-tool-runtime/README.md)
- [Standalone authenticated HTTP tools](examples/04-http-tools/README.md)
- [Standalone deterministic Pydantic AI agent](examples/05-pydantic-agent/README.md)
- [Standalone hierarchical order agent](examples/06-hierarchical-agent/README.md)
- [Development and release procedure](CONTRIBUTING.md)
- [Changes](CHANGELOG.md)

Every milestone adds a project under `examples/`. CI runs all preserved examples against the current library.

## Development

```sh
uv sync --locked
uv run pytest
uv run mypy
uv run python -m ruff check .
uv run python -m ruff format --check .
uv build
uv run python scripts/smoke_wheel.py
```

MIT licensed.
