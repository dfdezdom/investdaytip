---
name: release
description: Publish a new PyPI version via version bump, changelog update, git tag, and CI-triggered GitHub Release
license: MIT
---

# Release Workflow

Do **not** run `twine upload` — CI handles PyPI publish + GitHub Release.

## Steps

```bash
# 1. Update version in pyproject.toml and src/investdaytip/__init__.py
# 2. Add changelog entry in CHANGELOG.md
# 3. Commit
git add -A && git commit -m "Bump version to 0.X.0"
# 4. Tag
git tag v0.X.0
# 5. Push tag (triggers CI)
git push && git push origin v0.X.0
```

## What CI does

`.github/workflows/release.yml`:
1. Builds the distribution
2. Publishes to PyPI via trusted publishing
3. Creates the GitHub Release with auto-generated notes

## Pre-flight

- Run `investdaytip check` (lint + types + tests)
- Update version in both `pyproject.toml` and `src/investdaytip/__init__.py`
- Verify `CHANGELOG.md` is up to date
