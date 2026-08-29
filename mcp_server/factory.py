"""FastMCP composition root."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.apps.approval import Approval
from fastmcp.apps.choice import Choice
from fastmcp.apps.file_upload import FileUpload
from fastmcp.apps.generative import GenerativeUI

from .app_ui import create_app
from . import browser_tools, demo_tools, resources, system_tools, ui_tools, vtable_tools


def create_server() -> FastMCP:
    """Build the server from focused local modules without changing public names."""
    mcp = FastMCP("vtable-mcp", providers=[create_app()])
    mcp.add_provider(Approval(title="确认执行该测试用例?"))
    mcp.add_provider(Choice())
    mcp.add_provider(FileUpload())
    mcp.add_provider(GenerativeUI())

    for module in (
        resources,
        demo_tools,
        browser_tools,
        ui_tools,
        vtable_tools,
        system_tools,
    ):
        mcp.mount(module.create_server())
    return mcp
