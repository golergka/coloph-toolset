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
expected_names = {
    f"coloph_toolset-{version}-py3-none-any.whl",
    f"coloph_toolset-{version}.tar.gz",
}
dist_files = sorted((root / "dist").iterdir())
unexpected = {
    file.name
    for file in dist_files
    if file.name not in expected_names and file.name != ".gitignore" and not file.name.endswith(".publish.attestation")
}
if not expected_names <= {file.name for file in dist_files} or unexpected:
    raise SystemExit("The release must contain exactly the matching wheel and source archive")
artifacts = [root / "dist" / name for name in sorted(expected_names)]
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
