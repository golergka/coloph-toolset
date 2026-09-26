# Starlette HTTP adapter

Install the adapter with `uv add "coloph-toolset[http]"`.

`StarletteToolAdapter` creates one POST endpoint for each supplied `ToolDefinition`. The application supplies the exact tool selection and all authority and resource behavior.

For each request, the adapter uses this order:

1. Authenticate the request.
2. Decode the strict JSON envelope.
3. Normalize tool arguments with the declaration validator.
4. Authorize the selected tool for this request.
5. Create the application request context.
6. Invoke the tool once.
7. Build the response.

The default envelope contains `args`. Use `context_fields` for application values such as a tenant name. Unknown envelope fields and unknown tool arguments are rejected. Enriched application definitions can supply `normalize_arguments`; the callback must return the canonical validated argument mapping.

Raise `HttpAuthenticationError`, `HttpAuthorizationError`, or `HttpUserError` for expected public failures. Validation failures use HTTP 422. Unexpected exceptions use a generic HTTP 500 response. `observe_internal_error` receives the original exception for diagnostics.

The adapter runs synchronous callbacks in a worker thread. It awaits asynchronous callbacks on the request loop. Cancellation propagates to the invocation callback. The adapter does not retry a call or claim that cancellation reverses completed side effects.

The invocation callback owns `ToolRuntime` and its `ResourceLifecycle`. Return `InvocationResult` to expose its transport output after finalization. The core package does not import Starlette; only `coloph_toolset.http` requires the optional dependency.
