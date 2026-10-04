"""Compatibility imports for the pre-filesystem-provider resource path."""

from fastmcp import FastMCP

from qa_automation.mcp.components.resources.vtable import (
    vtable_js_inventory,
    vtable_js_script,
)


def create_server() -> FastMCP:
    """Return the legacy standalone resource server for external callers."""
    server = FastMCP("VTable Resources")
    server.add_resource(vtable_js_inventory)
    server.add_resource(vtable_js_script)
    return server


__all__ = ["create_server", "vtable_js_inventory", "vtable_js_script"]
