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


def _sanitize_generative_doc() -> None:
    """Strip double-brace template examples from prefab_ui.generative docs.

    GenerativeUI builds the `generate_prefab_ui` tool description from
    `prefab_ui.generative.execute.__doc__`, which contains literal examples
    like `{{ threshold }}` and `{{ balance | currency }}`. dsh (DeepSeek
    Harness) renders every visible tool description into its `tools:sdk`
    prompt section and treats any `{{ ... }}` there as a prompt variable
    whose name must match /^[a-z][a-z0-9_]*$/, so the spaces/pipes make it
    throw `malformed prompt variable reference "{{ threshold }}" in section
    "tools:sdk"` on every turn of a Code Mode session. The double braces are
    only illustrative (the Python API emits them via `.rx`/`Rx()`), so a
    single-brace rendering keeps the description intact without breaking the
    harness interpolation.
    """
    try:
        import prefab_ui.generative as _gen_ui

        doc = _gen_ui.execute.__doc__
        if doc and "{{" in doc:
            _gen_ui.execute.__doc__ = doc.replace("{{", "{").replace("}}", "}")
    except Exception:
        # Description stays as-is if prefab_ui cannot be imported; the
        # failure mode above only matters while the generative provider loads.
        pass


def create_server() -> FastMCP:
    """Compose focused local servers while preserving the public tool names."""
    server = FastMCP("qa-automation", providers=[create_app()])
    server.add_provider(Approval(title="确认执行该测试用例?"))
    server.add_provider(Choice())
    server.add_provider(FileUpload())
    _sanitize_generative_doc()
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
