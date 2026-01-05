#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -z "${TWINE_PASSWORD:-}" ]; then
  echo "ERROR: TWINE_PASSWORD is not set." >&2
  echo "Set it to your PyPI token (recommended: export in your shell), then re-run." >&2
  echo "Example:" >&2
  echo "  export TWINE_USERNAME=__token__" >&2
  echo "  export TWINE_PASSWORD=pypi-REDACTED" >&2
  exit 2
fi

python -m pip install -U pip
python -m pip install -U build twine

rm -rf dist build
python -m build
python -m twine check dist/*

echo "Uploading to PyPI..."
python -m twine upload dist/*

