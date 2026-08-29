"""FastMCP contract checks for the browser-facing tool surface."""

from __future__ import annotations

import unittest

from fastmcp import Client

import server


class ServerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_and_interact_tools_are_registered(self) -> None:
        async with Client(server.mcp) as client:
            tools = await client.list_tools()

        by_name = {tool.name: tool for tool in tools}
        self.assertIn("ui_page_context", by_name)
        self.assertIn("ui_analyze_scope", by_name)
        self.assertIn("browser_pages", by_name)
        self.assertIn("browser_select_page", by_name)
        self.assertIn("browser_start", by_name)
        self.assertIn("browser_connect", by_name)
        self.assertIn("browser_session", by_name)
        self.assertIn("ui_click", by_name)
        self.assertIn("ui_interact", by_name)
        self.assertIn("ui_screenshot", by_name)
        self.assertIn("overlay_scan", by_name)
        self.assertIn("overlay_observe", by_name)
        self.assertIn("ui_profile", by_name)
        self.assertIn("automation_metrics", by_name)
        self.assertIn("vtable_analysis", by_name)
        self.assertIn("vtable_cell_resolve", by_name)
        self.assertIn("vtable_cell_click_by_field", by_name)
        self.assertNotIn("vtable_checkbox_click", by_name)
        self.assertNotIn("vtable_dom_click", by_name)
        self.assertNotIn("vtable_dom_click_and_observe", by_name)
        self.assertNotIn("vtable_page_interact", by_name)
        self.assertNotIn("vtable_table_analyze", by_name)
        self.assertNotIn("vtable_header_icons_scan", by_name)
        self.assertNotIn("vtable_header_icon_click", by_name)

        interact_schema = by_name["ui_interact"].inputSchema
        self.assertIn("action", interact_schema["required"])
        self.assertIn("in_iframe", interact_schema["properties"])
        self.assertIn("x", interact_schema["properties"])
        self.assertIn("y", interact_schema["properties"])
        self.assertIn("max_results", interact_schema["properties"])
        self.assertIn("analysis_id", interact_schema["properties"])
        self.assertIn("expect_input", interact_schema["properties"])
        self.assertIn("description", interact_schema["properties"])
        screenshot_schema = by_name["ui_screenshot"].inputSchema
        self.assertIn("width", screenshot_schema["properties"])
        self.assertIn("height", screenshot_schema["properties"])
        self.assertIn("image_format", screenshot_schema["properties"])
        self.assertIn("css", by_name["ui_click"].inputSchema["properties"])
        self.assertIn("max_controls", by_name["ui_analyze_scope"].inputSchema["properties"])
        self.assertIn("record_index", by_name["vtable_cell_resolve"].inputSchema["required"])
        self.assertIn("max_columns", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("sample_rows", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("mode", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("fields", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("include_values", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("visible_only", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("port", by_name["browser_start"].inputSchema["properties"])
        self.assertIn("user_data_dir", by_name["browser_start"].inputSchema["properties"])
        self.assertIn("port", by_name["browser_connect"].inputSchema["properties"])
        session_schema = by_name["browser_session"].inputSchema
        self.assertIn("action", session_schema["properties"])
        self.assertIn("session_id", session_schema["properties"])
        self.assertIn("storage_state_path", session_schema["properties"])

    async def test_overlay_scan_defaults_to_active_scope(self) -> None:
        async with Client(server.mcp) as client:
            tools = await client.list_tools()

        schema = next(
            tool.inputSchema
            for tool in tools
            if tool.name == "overlay_scan"
        )
        self.assertEqual(
            schema["properties"]["scope"]["default"],
            "active",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
