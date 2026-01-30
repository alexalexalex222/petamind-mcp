"""Backwards-compatibility wrapper for older `terra_mind_mcp.mcp_server` imports.

Public name is now **Petamind MCP**. Prefer `petamind_mcp.mcp_server`.
"""

from __future__ import annotations


def main() -> None:
    from petamind_mcp.mcp_server import main as _main

    _main()


if __name__ == "__main__":
    main()
