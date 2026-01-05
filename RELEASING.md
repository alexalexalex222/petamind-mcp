# Releasing Petamind MCP

This repo ships an MCP server intended for **Claude Code**.

There are two “publishes”:

1) **GitHub release** (source + docs)  
2) **PyPI release** (so users can do `pipx install petamind-mcp`)

## 0) Preflight (required)

From the repo root:

```bash
git status --porcelain
python -m pytest -q
python scripts/preflight_check.py
```

## 1) Version bump

Edit `pyproject.toml`:

- `[project].version = "X.Y.Z"`

Update `CHANGELOG.md` accordingly.

## 2) Build artifacts (local)

Use a clean venv if possible:

```bash
python -m venv .venv-release
source .venv-release/bin/activate
python -m pip install -U pip
python -m pip install -U build twine

python -m build
python -m twine check dist/*
```

Sanity install:

```bash
python -m pip install dist/*.whl
petamind-mcp --help
petamind-setup --help
```

## 3) Publish to PyPI

Create a PyPI API token and keep it private. **Do not commit or paste it into issues.**

Recommended (temporary env vars in your own terminal session):

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-REDACTED"
python -m twine upload dist/*
```

Or use the helper script:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-REDACTED"
./scripts/release_pypi.sh
```

## 4) GitHub tagging

After PyPI upload succeeds:

```bash
git tag "vX.Y.Z"
git push origin "vX.Y.Z"
```

Optional: create a GitHub Release from the tag.

## 5) CI workflow note

If you push `.github/workflows/*` using an OAuth token that lacks the `workflow` scope,
GitHub may reject the push. In that case:

- Re-auth with workflow scope, or
- Add workflows via the GitHub web UI.
