"""Executed in an isolated subprocess to observe first-import side effects."""

import importlib
import sys

before_stdout = sys.stdout
before_stderr = sys.stderr
coloph_toolset = importlib.import_module("coloph_toolset")

assert sys.stdout is before_stdout
assert sys.stderr is before_stderr
assert "pydantic" not in sys.modules
assert not any(name == "core" or name.startswith("core.") for name in sys.modules)
assert not any(name in sys.modules for name in ("psycopg", "pydantic_ai", "starlette", "envvar"))
assert coloph_toolset.Tool
