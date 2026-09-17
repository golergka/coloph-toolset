import json
import subprocess
import sys
from pathlib import Path


def run(*args):
    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("tasks.py")), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_workflow(tmp_path):
    store = tmp_path / "tasks.json"
    added = run("--store", store, "task", "add", "-t", "Publish release", "--tags", "code", "--tags", "docs", "--done")
    assert added.returncode == 0, added.stderr
    assert json.loads(added.stdout)["tags"] == ["code", "docs"]
    listed = run("--store", store, "--restricted", "task", "list")
    assert json.loads(listed.stdout)[0]["title"] == "Publish release"
    assert json.loads(run("--store", store, "task", "list", "--no-done").stdout) == []
    assert json.loads(run("--store", store, "archive", "stats").stdout) == {"completed": 1}


def test_help_and_restricted_commands(tmp_path):
    help_result = run("--restricted", "--help")
    assert help_result.returncode == 0
    assert "archive" not in help_result.stdout
    rejected = run("--store", tmp_path / "tasks.json", "--restricted", "task", "add", "--title", "Denied")
    assert rejected.returncode == 2
    assert not (tmp_path / "tasks.json").exists()
