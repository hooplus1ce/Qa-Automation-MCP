"""Composition root for the FastMCP application."""

from __future__ import annotations

import os

from fastmcp import FastMCP

from qa_automation.mcp.instructions import SERVER_INSTRUCTIONS
from qa_automation.mcp.providers import build_providers
from qa_automation.mcp.servers import (
    antd,
    browser,
    chain,
    demos,
    diagnostics,
    net,
    scenario,
    tencent_docs,
    ui,
    vtable,
    x6,
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def create_server(
    include_demos: bool | None = None,
    include_prefab_ui: bool | None = None,
) -> FastMCP:
    """Create the composed server while preserving the public tool names."""
    if include_demos is None:
        include_demos = _env_flag("QA_AUTOMATION_ENABLE_DEMOS")
    if include_prefab_ui is None:
        include_prefab_ui = _env_flag("QA_AUTOMATION_ENABLE_PREFAB_UI")

    server = FastMCP(
        "qa-automation",
        instructions=SERVER_INSTRUCTIONS,
        providers=build_providers(
            include_demos=include_demos,
            include_prefab_ui=include_prefab_ui,
        ),
    )

    child_modules = [
        vtable,
        browser,
        ui,
        antd,
        net,
        scenario,
        x6,
        chain,
        diagnostics,
        tencent_docs,
    ]
    if include_demos:
        child_modules.insert(1, demos)

    for module in child_modules:
        server.mount(module.create_server())
    return server


__all__ = ["create_server"]
