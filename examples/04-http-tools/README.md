# Authenticated HTTP tools

This example exposes a restricted tool catalog as generated Starlette endpoints.

The application owns the local bearer token, inventory service, request context, and `ToolRuntime`. The adapter authenticates before it creates that context. It validates the same arguments as direct invocation.

Run `pytest` in this directory. The tests use an in-memory service and local credentials.
