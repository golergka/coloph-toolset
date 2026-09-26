# Pydantic AI inventory agent

This project registers a restricted catalog with a Pydantic AI agent. Typed
dependencies provide an in-memory inventory service. One synchronous tool has
an application-owned hidden default, and one tool is asynchronous.

The test uses Pydantic AI's deterministic `FunctionModel`. It first sends an
invalid quantity, observes the normal model retry, corrects the call, awaits
the asynchronous count tool, and finishes without provider credentials.

Run it with `uv run pytest`.
