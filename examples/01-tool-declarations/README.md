# Shipping quote

This standalone project uses only the public package and its Pydantic dependency.
It exports a schema, normalizes valid arguments, calls a function, and displays structured validation errors.

From this directory:

```sh
uv run shipping_quote.py
```

The project installs the public wheel. No Coloph source, database, credentials, or environment file is needed.
The valid example returns a quote of 1,000 cents for two parcels.
The rejected examples show a quantity constraint, a missing nullable argument, and an attempt to supply a hidden argument.

To use a locally built current wheel:

```sh
uv venv .venv
uv pip install --python .venv/bin/python ../../dist/coloph_toolset-0.1.0-py3-none-any.whl
uv run --no-project --python .venv/bin/python shipping_quote.py
```

The repository's `scripts/smoke_wheel.py` installs the current wheel into a temporary environment and runs this project outside the checkout.
Repository CI also runs this project's tests against the current library, so the release URL cannot hide a compatibility regression.
