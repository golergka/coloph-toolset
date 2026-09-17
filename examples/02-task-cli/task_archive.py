"""Imported only when the archive subtree is needed."""

import json

from coloph_toolset import tool


@tool()
def stats(ctx):
    """Count completed tasks."""
    tasks = json.loads(ctx.read_text()) if ctx.exists() else []
    return {"completed": sum(task["done"] for task in tasks)}
