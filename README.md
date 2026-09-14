# coloph-toolset

Declare Python tools once. Export their JSON Schema and validate arguments before your application calls the function.

Version 0.1 provides declarations and validation. It does not manage execution, transactions, authorization, or model providers.
Later releases add catalogs and adapters. The [roadmap](docs/roadmap.md) describes that sequence.

## Install

Install the wheel from the [v0.1.0 release](https://github.com/golergka/coloph-toolset/releases/tag/v0.1.0):

```sh
uv pip install https://github.com/golergka/coloph-toolset/releases/download/v0.1.0/coloph_toolset-0.1.0-py3-none-any.whl
```

The package requires Python 3.11 or later and Pydantic 2.12 or later within major version 2.
No Coloph installation, database, credentials, HTTP framework, or agent framework is required.

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
- [Standalone shipping-quote project](examples/01-tool-declarations/README.md)
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
