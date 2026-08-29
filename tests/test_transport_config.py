"""Deployment transport contract for the distributable MCP server."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastmcp.utilities.mcp_server_config import MCPServerConfig


ROOT = Path(__file__).resolve().parents[1]


class TransportConfigTests(unittest.TestCase):
    def test_fastmcp_config_uses_stdio_without_http_bindings(self) -> None:
        config_path = ROOT / "fastmcp.json"
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        deployment = raw["deployment"]

        self.assertEqual(deployment["transport"], "stdio")
        for http_only_key in ("host", "port", "path"):
            self.assertNotIn(http_only_key, deployment)

    def test_fastmcp_config_is_accepted_by_the_installed_cli_model(self) -> None:
        config = MCPServerConfig.from_file(ROOT / "fastmcp.json")

        self.assertEqual(config.deployment.transport, "stdio")
        self.assertEqual(config.source.path, "server.py")
        self.assertEqual(config.source.entrypoint, "mcp")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
