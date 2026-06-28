---
description: Release workflow for publishing a new version to PyPI + GitHub. Use when the user asks to publish, release, bump version, or tag.
---

# Release Workflow

Do **not** run `twine upload` manually — CI handles PyPI publish + GitHub Release.

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

The workflow (`.github/workflows/release.yml`):
1. Builds the distribution
2. Publishes to PyPI via trusted publishing
3. Creates the GitHub Release with auto-generated notes

## Before releasing

- Run `investdaytip check` to ensure lint + types + tests pass
- Update version in both `pyproject.toml` and `src/investdaytip/__init__.py`
- Verify the changelog is up-to-date in `CHANGELOG.md`
