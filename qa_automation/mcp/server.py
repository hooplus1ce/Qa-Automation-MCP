"""Stable FastMCP entrypoint used by ``fastmcp.json`` and the CLI script."""

from __future__ import annotations

from qa_automation.mcp.composition import create_server
from qa_automation.mcp.instructions import SERVER_INSTRUCTIONS

# Backward-compatible name for callers that imported the old composition root.
INSTRUCTIONS = SERVER_INSTRUCTIONS

mcp = create_server()


def main() -> None:
    """Run the composed MCP server over protocol-clean stdio."""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()


__all__ = ["INSTRUCTIONS", "create_server", "main", "mcp"]
