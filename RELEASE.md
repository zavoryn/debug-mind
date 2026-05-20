# Release Process

## Prerequisites

- Maintainer access to the [debug-mind PyPI project](https://pypi.org/project/debug-mind/)
- `TESTPYPI_TOKEN` and `PYPI_TOKEN` secrets configured in GitHub repository settings → Environments → `pypi`

## Step-by-step

1. **Update CHANGELOG.md** — move Unreleased entries to a new version section.

2. **Bump version** (updates `pyproject.toml` and `src/debug_mind/__init__.py`):
   ```bash
   python scripts/bump_version.py 0.2.0
   ```

3. **Commit and tag**:
   ```bash
   git add pyproject.toml src/debug_mind/__init__.py CHANGELOG.md
   git commit -m "chore: bump version to 0.2.0"
   git tag v0.2.0
   git push origin master --tags
   ```

4. **Publish to TestPyPI**:
   - Go to Actions → release → Run workflow
   - Select `target: test`
   - Verify at https://test.pypi.org/project/debug-mind/

5. **Verify TestPyPI install**:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ debug-mind
   debug-mind --version
   ```

6. **Publish to PyPI**:
   - Go to Actions → release → Run workflow
   - Select `target: prod`

7. **Verify PyPI install**:
   ```bash
   pip install debug-mind
   debug-mind --version
   ```

## Manual build check

Before publishing, you can verify the build locally:
```bash
pip install build twine
python -m build
twine check dist/*
```
