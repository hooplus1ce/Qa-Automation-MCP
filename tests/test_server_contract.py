"""FastMCP contract checks for the browser-facing tool surface."""

from __future__ import annotations

import unittest

from fastmcp import Client

from qa_automation.mcp import server
from qa_automation.mcp.servers import browser, tencent_docs, ui, vtable


class ServerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_domain_servers_compose_without_tool_prefixes(self) -> None:
        async def tool_names(mcp) -> set[str]:
            async with Client(mcp) as client:
                return {tool.name for tool in await client.list_tools()}

        browser_names = await tool_names(browser.create_server())
        ui_names = await tool_names(ui.create_server())
        vtable_names = await tool_names(vtable.create_server())
        tencent_docs_names = await tool_names(tencent_docs.create_server())
        root_names = await tool_names(server.mcp)

        self.assertIn("browser_start", browser_names)
        self.assertIn("browser_login", browser_names)
        self.assertIn("ui_interact", ui_names)
        self.assertIn("vtable_analysis", vtable_names)
        self.assertIn("update_test_case_result", tencent_docs_names)
        self.assertTrue(browser_names <= root_names)
        self.assertTrue(ui_names <= root_names)
        self.assertTrue(tencent_docs_names <= root_names)
        self.assertTrue(vtable_names <= root_names)
        self.assertNotIn("browser.browser_start", root_names)

    async def test_app_ui_and_resources_survive_composition(self) -> None:
        async with Client(server.mcp) as client:
            result = await client.call_tool("case_execution_panel", {})
            resources = await client.list_resources()
            templates = await client.list_resource_templates()

        self.assertIsNotNone(result.structured_content)
        self.assertIn("vtable://js/index", {str(item.uri) for item in resources})
        self.assertIn(
            "vtable://js/{name}",
            {str(item.uriTemplate) for item in templates},
        )

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
        self.assertIn("ui_mouse_drag", by_name)
        self.assertIn("ui_screenshot", by_name)
        self.assertIn("overlay_scan", by_name)
        self.assertIn("overlay_observe", by_name)
        self.assertIn("ui_profile", by_name)
        self.assertIn("automation_metrics", by_name)
        self.assertIn("vtable_discover", by_name)
        self.assertIn("vtable_analysis", by_name)
        self.assertIn("vtable_cell_resolve", by_name)
        self.assertIn("vtable_cell_click_by_field", by_name)
        self.assertIn("frame", by_name["vtable_discover"].inputSchema["properties"])
        for name in (
            "vtable_cell_info",
            "vtable_cell_click",
            "vtable_cell_resolve",
            "vtable_cell_click_by_field",
            "vtable_meta",
            "vtable_analysis",
            "vtable_read_cells",
            "vtable_drop_files",
        ):
            schema = by_name[name].inputSchema
            self.assertIn("frame", schema["properties"], name)
            self.assertIn("table_index", schema["properties"], name)
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
        self.assertIn("filename", screenshot_schema["properties"])
        drag_schema = by_name["ui_mouse_drag"].inputSchema
        self.assertIn("start_x", drag_schema["required"])
        self.assertIn("start_y", drag_schema["required"])
        self.assertIn("end_x", drag_schema["required"])
        self.assertIn("steps", drag_schema["properties"])
        self.assertIn("css", by_name["ui_click"].inputSchema["properties"])
        self.assertIn(
            "max_controls", by_name["ui_analyze_scope"].inputSchema["properties"]
        )
        self.assertIn(
            "record_index", by_name["vtable_cell_resolve"].inputSchema["required"]
        )
        self.assertIn(
            "max_columns", by_name["vtable_analysis"].inputSchema["properties"]
        )
        self.assertIn(
            "sample_rows", by_name["vtable_analysis"].inputSchema["properties"]
        )
        self.assertIn("mode", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn("fields", by_name["vtable_analysis"].inputSchema["properties"])
        self.assertIn(
            "include_values", by_name["vtable_analysis"].inputSchema["properties"]
        )
        self.assertIn(
            "visible_only", by_name["vtable_analysis"].inputSchema["properties"]
        )
        self.assertIn("port", by_name["browser_start"].inputSchema["properties"])
        self.assertIn(
            "user_data_dir", by_name["browser_start"].inputSchema["properties"]
        )
        self.assertIn("port", by_name["browser_connect"].inputSchema["properties"])
        session_schema = by_name["browser_session"].inputSchema
        self.assertIn("action", session_schema["properties"])
        self.assertIn("session_id", session_schema["properties"])
        self.assertIn("storage_state_path", session_schema["properties"])

    async def test_overlay_scan_defaults_to_active_scope(self) -> None:
        async with Client(server.mcp) as client:
            tools = await client.list_tools()

        schema = next(tool.inputSchema for tool in tools if tool.name == "overlay_scan")
        self.assertEqual(
            schema["properties"]["scope"]["default"],
            "active",
        )

    async def test_ui_snapshot_dispatches_cleanly_via_mcp_client(self) -> None:
        from unittest.mock import AsyncMock, patch

        import qa_automation as automation
        from qa_automation.interaction import snapshot

        with patch.object(
            snapshot,
            "_dom_snapshot_impl",
            AsyncMock(return_value={"status": "ok", "snapshot": "- document"}),
        ) as mock_impl:
            # 1. 位置参数调用
            snap1 = await automation.dom_snapshot("div", depth=2)
            # 2. 关键字参数调用
            snap2 = await automation.dom_snapshot(selector="div", depth=2)
            # 3. 无参调用
            snap3 = await automation.dom_snapshot()

            self.assertEqual(snap1["status"], "ok")
            self.assertEqual(snap2["status"], "ok")
            self.assertEqual(snap3["status"], "ok")
            self.assertEqual(mock_impl.call_count, 3)

            # 4. 通过 FastMCP Client 真实调用 ui_snapshot 工具
            async with Client(server.mcp) as client:
                call_default = await client.call_tool("ui_snapshot", {})
                call_with_selector = await client.call_tool(
                    "ui_snapshot", {"selector": "div"}
                )
                self.assertIsNotNone(call_default.structured_content)
                self.assertIsNotNone(call_with_selector.structured_content)
                self.assertEqual(call_default.structured_content["status"], "ok")
                self.assertEqual(
                    call_with_selector.structured_content["status"], "ok"
                )

    async def test_vtable_analysis_accepts_frame_argument_via_mcp_client(self) -> None:
        from unittest.mock import AsyncMock, patch

        from qa_automation.components.vtable import analysis

        with patch.object(
            analysis,
            "_vtable_analysis_impl",
            AsyncMock(return_value={"status": "ok", "analysis": {"columns": []}}),
        ) as mock_impl:
            async with Client(server.mcp) as client:
                result = await client.call_tool(
                    "vtable_analysis",
                    {"frame": "active", "max_columns": 5},
                )

            self.assertIsNotNone(result.structured_content)
            self.assertEqual(result.structured_content["status"], "ok")
            mock_impl.assert_awaited_once()
            kwargs = mock_impl.await_args.kwargs
            self.assertEqual(kwargs["frame"], "active")
            self.assertEqual(kwargs["max_columns"], 5)

    async def test_ui_mouse_drag_dispatches_cleanly_via_mcp_client(self) -> None:
        from unittest.mock import AsyncMock, patch

        import qa_automation as automation

        with patch.object(
            automation,
            "mouse_drag",
            AsyncMock(
                return_value={
                    "status": "dragged",
                    "start": {"x": 100.0, "y": 150.0},
                    "end": {"x": 300.0, "y": 150.0},
                    "distance": 200.0,
                    "steps": 24,
                    "button": "left",
                    "channel": "cdp",
                }
            ),
        ) as mock_drag:
            async with Client(server.mcp) as client:
                result = await client.call_tool(
                    "ui_mouse_drag",
                    {
                        "start_x": 100.0,
                        "start_y": 150.0,
                        "end_x": 300.0,
                        "end_y": 150.0,
                        "steps": 24,
                    },
                )

            self.assertIsNotNone(result.structured_content)
            self.assertEqual(result.structured_content["status"], "dragged")
            mock_drag.assert_awaited_once()
            kwargs = mock_drag.await_args.kwargs
            self.assertEqual(kwargs["start_x"], 100.0)
            self.assertEqual(kwargs["end_x"], 300.0)
            self.assertEqual(kwargs["steps"], 24)

    async def test_update_test_case_result_schema_and_dispatch(self) -> None:
        from unittest.mock import AsyncMock, patch

        async with Client(server.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            self.assertIn("update_test_case_result", tools)
            schema = tools["update_test_case_result"].inputSchema
            props = schema["properties"]
            self.assertEqual(
                set(props.keys()),
                {"file_id", "sheet_id", "case_id", "test_result", "executor", "execution_date"},
            )
            self.assertEqual(
                schema["required"],
                ["file_id", "sheet_id", "case_id", "test_result", "executor"],
            )

        mock_call = AsyncMock()
        mock_call.side_effect = [
            {"csv_data": "用例编号,级别,测试结果,执行人,执行时间"},
            {"csv_data": "用例编号\nAPS_JCPZ_0001\nAPS_JCPZ_0758\nAPS_JCPZ_0759"},
            {"error": ""},
        ]

        with patch("qa_automation.mcp.servers.tencent_docs._call_mcp_tool", mock_call):
            async with Client(server.mcp) as client:
                result = await client.call_tool(
                    "update_test_case_result",
                    {
                        "file_id": "mock_file",
                        "sheet_id": "mock_sheet",
                        "case_id": "APS_JCPZ_0758",
                        "test_result": "通过",
                        "executor": "Hoo",
                    },
                )

            self.assertIsNotNone(result.structured_content)
            data = result.structured_content
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["case_id"], "APS_JCPZ_0758")
            self.assertEqual(data["row_index"], 2)
            self.assertEqual(data["row_number"], 3)
            self.assertEqual(data["updated_fields"]["测试结果"], "通过")
            self.assertEqual(data["updated_fields"]["执行人"], "Hoo")
            self.assertTrue(data["updated_fields"]["执行时间"])
            self.assertEqual(data["columns_resolved"]["用例编号"], 0)
            self.assertEqual(data["columns_resolved"]["测试结果"], 2)
            self.assertEqual(data["columns_resolved"]["执行人"], 3)
            self.assertEqual(data["columns_resolved"]["执行时间"], 4)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
