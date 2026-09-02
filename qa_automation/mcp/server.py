"""FastMCP composition root."""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.apps.approval import Approval
from fastmcp.apps.choice import Choice
from fastmcp.apps.file_upload import FileUpload
from fastmcp.apps.generative import GenerativeUI
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from qa_automation.mcp.apps.provider import create_app
from qa_automation.mcp.resources import vtable as vtable_resources
from qa_automation.mcp.servers import (
    browser,
    chain,
    demos,
    diagnostics,
    tencent_docs,
    ui,
    vtable,
)


def create_server() -> FastMCP:
    """Compose focused local servers while preserving the public tool names."""
    server = FastMCP("qa-automation", providers=[create_app()])
    server.add_provider(Approval(title="确认执行该测试用例?"))
    server.add_provider(Choice())
    server.add_provider(FileUpload())
    server.add_provider(GenerativeUI())

    skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
    server.add_provider(SkillsDirectoryProvider(roots=skills_dir, reload=True))

    for module in (
        vtable_resources,
        demos,
        browser,
        ui,
        vtable,
        chain,
        diagnostics,
        tencent_docs,
    ):
        server.mount(module.create_server())
    return server


mcp = create_server()


def main() -> None:
    """Run the composed MCP server over protocol-clean stdio."""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
