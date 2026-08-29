"""Compatibility entrypoint for the composed FastMCP server.

The implementation lives in ``mcp_server``. Keeping this module makes existing
FastMCP configs and clients that import ``server:mcp`` continue to work.
"""

from __future__ import annotations

from mcp_server.factory import create_server

mcp = create_server()


def main() -> None:
    """Run the project over protocol-clean stdio."""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
