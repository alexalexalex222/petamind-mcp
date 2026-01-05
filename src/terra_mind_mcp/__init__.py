"""Backwards-compatibility wrapper for older `terra_mind_mcp` imports.

Public name is now **Petamind MCP**. Prefer importing `petamind_mcp` instead.
"""

from petamind_mcp import __version__  # noqa: F401

__all__ = ["__version__"]
