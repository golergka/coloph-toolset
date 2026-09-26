# Hierarchical order agent

This project exposes a restricted order catalog through one `orders` root
tool. The model can browse the selected commands without seeing the excluded
`delete-all` sibling.

The deterministic test loads first-use documentation from `docs/orders.md`,
rejects an invalid leaf argument, retries the validated call, and records a
terminal result only after the close operation succeeds. It needs no host application
checkout, database, provider account, or credentials.

Run it with `uv run pytest`.
