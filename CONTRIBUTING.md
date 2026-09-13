# Contributing

## Local checks

```sh
uv sync --locked
uv run pytest
uv run mypy
uv run python -m ruff check .
uv run python -m ruff format --check .
uv build
uv run python scripts/smoke_wheel.py
```

Keep fixes and tests within the current milestone. Add contract tests for changes in public behavior.
Preserve every project under `examples/`. Each project needs source, a README, dependency metadata, and automated smoke coverage.
Examples must use public imports and work without a Coloph checkout or private services.

## Release

This package initially distributes wheels and source archives through GitHub Releases. It does not require a PyPI publishing credential.

1. Update the version and changelog.
2. Run all local checks and the clean wheel smoke.
3. Review and commit the changes.
4. Push the reviewed commit to the public default branch.
5. Wait for CI on the exact release commit.
6. Create an immutable `vX.Y.Z` tag on that commit.
7. Push the tag. The release workflow reruns tests and publishes the wheel and source archive.
8. Validate the published artifacts in a clean environment.

The package version, tag, and release notes must match. Never move an existing release tag.
Coloph can adopt a release only after the public default branch and release contain its tested commit.
Record the release and Coloph dependency pin in the owning Coloph issue.

When PyPI distribution is introduced, the release process must publish the same tested artifacts and version there.
