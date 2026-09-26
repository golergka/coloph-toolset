# Tool runtime

This standalone example executes synchronous and asynchronous inventory tools.
The application supplies an inventory service, per-call or shared session state,
and a fake transaction resource. The runtime validates arguments, awaits the
tool once, finalizes the transaction, and then presents the result.

```sh
uv run --group dev pytest
```

The tests also show the distinct outcomes for invalid input, body failure,
commit failure, and presentation failure after committed work. The example
needs no host application checkout, database, credentials, or private service.
