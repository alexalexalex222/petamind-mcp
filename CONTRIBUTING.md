# Contributing

Thanks for helping improve Petamind MCP.

This repo contains:

- **Petamind MCP** (agentic coding loop exposed over MCP stdio)
- A **synthetic UI dataset factory** (Next.js/Tailwind website generation + gating)

## Development setup

### 1) Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install deps

```bash
pip install -e '.[dev,mcp]'
```

### 3) Install Playwright browsers (required for vision)

```bash
playwright install chromium
```

## Running tests

```bash
pytest -q
```

## Linting / formatting

```bash
ruff check src tests
ruff format src tests
```

## MCP smoke test

See `docs/TROUBLESHOOTING.md` for a copy/paste stdio `list_tools` check.

## Pull request guidelines

- Keep PRs small and focused.
- Add/adjust tests when behavior changes.
- Avoid reformatting unrelated code.
- Do not commit secrets (`.env`, service account keys, etc.).
- Do not commit large run artifacts (`out/`, screenshots, DBs).
