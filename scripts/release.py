"""Publish tested artifacts only when the tag matches package metadata."""

import os
import subprocess
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
tag = os.environ["RELEASE_TAG"]
if tag != f"v{version}":
    raise SystemExit(f"Tag {tag!r} does not match package version {version!r}")
artifacts = sorted((root / "dist").iterdir())
if {file.name for file in artifacts} != {
    f"coloph_toolset-{version}-py3-none-any.whl",
    f"coloph_toolset-{version}.tar.gz",
}:
    raise SystemExit("The release must contain exactly the matching wheel and source archive")
subprocess.run(
    [
        "gh",
        "release",
        "create",
        tag,
        *map(str, artifacts),
        "--verify-tag",
        "--title",
        f"coloph-toolset {version}",
        "--notes-file",
        str(root / "CHANGELOG.md"),
    ],
    check=True,
)
