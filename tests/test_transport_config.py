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
        self.assertEqual(
            Path(config.source.path),
            ROOT / "qa_automation" / "mcp" / "server.py",
        )
        self.assertEqual(config.source.entrypoint, "mcp")

    def test_host_uv_environment_is_not_relaunched_by_fastmcp(self) -> None:
        config = MCPServerConfig.from_file(ROOT / "fastmcp.json")

        self.assertEqual(config.environment.build_command(["probe"]), ["probe"])

    def test_agent_config_separates_mcp_project_from_workspace(self) -> None:
        raw = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = raw["mcpServers"]["qa-automation"]
        args = server["args"]

        self.assertNotIn("cwd", server)
        self.assertIn(
            server["command"],
            {"uv", "D:/Developer/ScoopApps/apps/uv/current/uv.exe"},
        )
        self.assertEqual(args[args.index("--project") + 1], ROOT.as_posix())
        self.assertEqual(
            args[args.index("--env-file") + 1],
            (ROOT / ".env.qa-automation").as_posix(),
        )
        self.assertEqual(
            args[args.index("fastmcp") :],
            [
                "fastmcp",
                "run",
                (ROOT / "fastmcp.json").as_posix(),
                "--no-banner",
            ],
        )

    def test_environment_example_keeps_artifacts_workspace_relative(self) -> None:
        lines = (
            (ROOT / ".env.qa-automation.example")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assignments = [
            (index, line)
            for index, line in enumerate(lines)
            if line and not line.startswith("#")
        ]
        for index, assignment in assignments:
            self.assertGreater(index, 0)
            self.assertTrue(
                lines[index - 1].startswith("# "),
                f"{assignment} must have an immediately preceding description",
            )
        values = dict(line.split("=", 1) for _, line in assignments)

        self.assertEqual(values["QA_AUTOMATION_WORKSPACE_ROOT"], ".")
        self.assertEqual(
            values["QA_AUTOMATION_ARTIFACT_ROOT"],
            ".qa-automation",
        )
        self.assertEqual(
            values["QA_AUTOMATION_DATA_DIR"],
            ".qa-automation/data",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
