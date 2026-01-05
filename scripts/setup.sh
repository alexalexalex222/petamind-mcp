#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-python3}"

echo "[petamind-mcp] repo: $REPO_ROOT"
echo "[petamind-mcp] python: $PY"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+ and re-run." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[petamind-mcp] creating venv (.venv)…"
  "$PY" -m venv .venv
fi

echo "[petamind-mcp] installing editable package…"
.venv/bin/pip install -e . -q

echo "[petamind-mcp] installing Playwright Chromium (required for screenshots)…"
if .venv/bin/python -m playwright --help >/dev/null 2>&1; then
  .venv/bin/python -m playwright install chromium
else
  # Some environments expose playwright as a top-level command.
  playwright install chromium
fi

echo
echo "[petamind-mcp] done."
echo
echo "Next:"
echo "1) Add the MCP server to Claude Code (recommended: user scope):"
echo "   claude mcp add-json --scope user petamind-mcp '{\"command\":\"$REPO_ROOT/.venv/bin/python\",\"args\":[\"-m\",\"petamind_mcp.mcp_server\"],\"env\":{\"TITAN_CONFIG_PATH\":\"$REPO_ROOT/config/config.yaml\"}}'"
echo
echo "2) Run a local smoke test (MCP stdio):"
echo "   $REPO_ROOT/.venv/bin/python scripts/smoke_mcp_stdio.py"
