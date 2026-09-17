"""A generated CLI whose application owns storage and execution."""

import json
import sys
from pathlib import Path
from typing import Annotated

from coloph_toolset import (
    Cli,
    DeferredGroup,
    GroupNode,
    Leaf,
    ToolArgumentParser,
    build_index,
    build_parser_from_tree,
    kwargs_from_args,
    tool,
)


def read_tasks(path: Path) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []


@tool()
def add(
    ctx: Path,
    title: Annotated[str, "Task title", Cli(aliases=("-t",))],
    tags: Annotated[list[str], "Repeat for each tag"] = [],
    done: bool = False,
) -> dict:
    """Add a task to the local file."""
    tasks = read_tasks(ctx)
    task = {"id": len(tasks) + 1, "title": title, "tags": tags, "done": done}
    tasks.append(task)
    ctx.write_text(json.dumps(tasks))
    return task


@tool()
def list_tasks(ctx: Path, done: bool | None = None) -> list[dict]:
    """List tasks, optionally filtered by completion."""
    return [task for task in read_tasks(ctx) if done is None or task["done"] is done]


def archive_children():
    from task_archive import stats

    return (Leaf("stats", stats),)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    settings = ToolArgumentParser(add_help=False)
    settings.add_argument("--store", type=Path, default=Path("tasks.json"))
    settings.add_argument("--restricted", action="store_true")
    options, command = settings.parse_known_args(args)
    root = GroupNode(
        "root",
        "Tasks",
        (
            GroupNode(
                "task",
                "Task commands",
                (
                    Leaf("add", add),
                    Leaf("list", list_tasks, alias="tasks"),
                ),
            ),
            DeferredGroup("archive", "Archive reports", archive_children, exposure={"cli": None}),
        ),
        exposure={"cli": None},
    )
    allowed = None
    if options.restricted:
        # The application chooses the exact catalog available to this invocation.
        selected = build_index(root).select_tools(["task.list"])
        allowed = frozenset(bound.stable_id for bound in selected)
    parser = ToolArgumentParser(prog="tasks", parents=[settings])
    path = tuple(token for token in command[:2] if not token.startswith("-"))
    build_parser_from_tree(root, "cli", parser=parser, requested_path=path, allowed_dotted=allowed)
    parsed = parser.parse_args(args)
    bound = parsed._bound
    result = bound.callable_fn(parsed.store, **kwargs_from_args(bound.tool, parsed))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
