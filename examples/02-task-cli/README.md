# Task CLI

This standalone example uses a JSON file. It needs no database or credentials.
The application supplies storage and calls the selected function after validation.

```sh
uv run tasks.py task add -t "Publish release" --tags code --tags docs --done
uv run tasks.py task list --no-done
uv run tasks.py archive stats
uv run tasks.py --restricted --help
uv run --group dev pytest
```

Use `--store PATH` before the command to select another JSON file.
The `archive` group imports its module only when that subtree is needed.
The `-t` flag aliases `--title`. The `tasks` catalog alias identifies `task.list` in native calls.
Repeated `--tags` flags replace the default list and preserve argument order.
The restricted catalog contains only `task.list`; help and parsing use that same selection.

CI also runs this project against the current wheel in a separate environment.
