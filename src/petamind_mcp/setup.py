"""Petamind MCP setup helper.

This exists primarily to make `pipx install petamind-mcp` feel "product-like":
users can run a single command to install Playwright browser binaries required
for the mandatory vision loop.

Why this is needed:
- Installing the Python dependency `playwright` is not enough.
- The Chromium browser binaries are downloaded separately via `playwright install chromium`.

This command runs that installer using the *current* Python environment, so it
works correctly inside pipx-managed virtualenvs.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


if __name__ == "__main__":
    main()

