# Runtime contract

`ToolRuntime` runs a declared tool through one ordered lifecycle. It accepts a decorated function or a `Tool` declaration.

## Context ownership

`ToolContext` contains application dependencies, invocation state, and one optional resource. The application selects the types for all three values.

The runtime calls `state_factory` for each invocation. Pass `state=` to `invoke()` only when multiple calls must share state.

The application owns the dependency lifetime. A `ResourceLifecycle` owns one resource for one invocation.

## Lifecycle order

The runtime uses this order:

1. Validate the external argument object.
2. Run the optional availability callback.
3. Acquire the optional call resource.
4. Resolve application argument references.
5. Run each before-call hook.
6. Run the tool body.
7. Run each after-success hook with the raw result.
8. Finalize the call resource.
9. Present the raw result.
10. Return `InvocationResult`.

Invalid or unavailable calls do not acquire a resource. The runtime finalizes an acquired resource after success, failure, or cancellation.

If cleanup also fails, `InvocationError` keeps the primary error in `cause`. The cleanup error remains available in `cleanup_error`.

## Resources and commit state

`ResourceLifecycle.acquire()` returns the resource for `context.resource`. `ResourceLifecycle.finalize()` receives the primary error, if one exists.

The finalizer returns `Finalization`. Set `committed=True` only after durable work is complete.

Presentation starts after finalization. A presentation failure includes the commit state in `InvocationError.committed`.

## Hooks and observation

A hook can implement `before_call()` and `after_success()`. The before-call return value becomes the state for the matching after-success call.

The after-success hook receives the raw business result. Hook metadata and finalization metadata become `InvocationResult.metadata`.

An optional observer receives `InvocationEvent` values. Each event identifies the phase, status, error, and known commit state.

## Results and limits

`InvocationResult.raw` retains the business result. `InvocationResult.output` contains the transport presentation.

Strings, integers, floats, booleans, and null keep their scalar types by default. Other values use deterministic JSON text.

If an output limit truncates the presentation, `truncated` is true. `original_output_chars` contains the original presentation length.

The runtime does not capture stdout. Wrap a legacy callable explicitly if an application must collect printed text.

## Sync and async calls

Use `await runtime.invoke(...)` in asynchronous code. This method supports synchronous and asynchronous tools, callbacks, hooks, and resources.

Use `runtime.invoke_sync(...)` outside an event loop. This method runs asynchronous tools and waits for completion.
It runs synchronous tool bodies without an active event loop, so a synchronous body can own an event loop when required.

`invoke_sync()` raises an error inside an active event loop. This rule prevents coroutine strings and nested event loops.
