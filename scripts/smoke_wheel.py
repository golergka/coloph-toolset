"""Run every example outside the checkout against the built, installed wheel."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", help="Local wheel path or published wheel URL")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheels = list((root / "dist").glob("coloph_toolset-*.whl"))
    if args.wheel is None and len(wheels) != 1:
        parser.error("build exactly one current wheel or supply --wheel")
    wheel = args.wheel or str(wheels[0])
    with tempfile.TemporaryDirectory(prefix="toolset-example-") as directory:
        temporary = Path(directory)
        environment = temporary / "environment"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), wheel, "pytest"], check=True
        )
        for example in sorted((root / "examples").iterdir()):
            if not example.is_dir():
                continue
            target = temporary / example.name
            shutil.copytree(
                example,
                target,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
            )
            subprocess.run(
                [str(python), "-I", "-m", "pytest", "-q", str(target)], cwd=temporary, check=True
            )
            print(f"Installed-wheel example passed: {example.name}", flush=True)


if __name__ == "__main__":
    main()
