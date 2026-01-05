"""Petamind MCP stdio server entrypoint.

We keep this thin on purpose: the full implementation lives in
`titan_factory.mcp_code_server` so it can reuse the existing pipeline utilities.
"""

from __future__ import annotations

import os


def main() -> None:
    # MCP stdio transport requires stdout cleanliness. Ensure any Rich-based logs
    # end up on stderr, even if downstream imports log warnings.
    os.environ.setdefault("TITAN_STDIO_MCP", "1")

    from titan_factory.mcp_code_server import main as _main

    _main()


if __name__ == "__main__":
    main()

