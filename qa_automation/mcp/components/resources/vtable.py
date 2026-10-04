"""VTable JavaScript resources exposed through MCP resource URIs.

This module follows FastMCP's filesystem-provider convention: resources use
the standalone ``@resource`` decorator and are discovered from the components
directory by the composition root.
"""

from __future__ import annotations

import json

from fastmcp.resources import resource

from qa_automation.components.vtable.scripts import VTABLE_SCRIPTS, inventory


@resource("vtable://js/index")
def vtable_js_inventory() -> str:
    """VTable JS 脚本目录:所有脚本名与说明(JSON)。"""
    return json.dumps(inventory(), ensure_ascii=False, indent=2)


@resource("vtable://js/{name}")
def vtable_js_script(name: str) -> str:
    """按名称读取内化的 VTable JS 脚本。"""
    script = VTABLE_SCRIPTS.get(name)
    if script is None:
        raise ValueError(f"未知脚本: {name}。可用: {', '.join(VTABLE_SCRIPTS)}")
    return script


__all__ = ["vtable_js_inventory", "vtable_js_script"]
