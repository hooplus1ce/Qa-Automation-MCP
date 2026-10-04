"""Regression contracts for the shared MCP response protocol and metrics."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastmcp import Client

from qa_automation.mcp import server
from qa_automation.mcp.metrics import instrument_tool, metrics_snapshot, reset_metrics
from qa_automation.mcp.protocol import (
    PROTOCOL_VERSION,
    ErrorCode,
    ensure_response_metadata,
    failure_response,
    success_response,
)
from qa_automation.mcp.servers.diagnostics import create_server
from qa_automation.tencent_sheet.models import SheetConnectResult


class ResponseProtocolTests(unittest.TestCase):
    def test_success_response_uses_stable_metadata(self) -> None:
        result = success_response({"rows": [1, 2]}, trace_id="trace-fixed", total=2)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["code"], "OK")
        self.assertIsNone(result["error"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(result["trace_id"], "trace-fixed")
        self.assertEqual(result["data"], {"rows": [1, 2]})
        self.assertEqual(result["total"], 2)

    def test_failure_response_uses_error_code_and_action(self) -> None:
        result = failure_response(
            ErrorCode.SESSION_NOT_FOUND,
            "Session 'ops' does not exist",
            trace_id="trace-failure",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "SESSION_NOT_FOUND")
        self.assertEqual(result["message"], "Session 'ops' does not exist")
        self.assertIn("browser_session", result["next_action"])
        self.assertFalse(result["ok"])

    def test_ensure_metadata_preserves_legacy_domain_fields(self) -> None:
        result = ensure_response_metadata(
            {
                "status": "failed",
                "reason": "vtable-analysis-error: instance detached",
                "page_id": "page-1",
            },
            trace_id="trace-legacy",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["page_id"], "page-1")
        self.assertEqual(result["code"], "VTABLE_NOT_FOUND")
        self.assertIn("vtable", result["message"])
        self.assertIn("vtable", result["next_action"])
        self.assertEqual(result["trace_id"], "trace-legacy")

    def test_timeout_status_maps_to_a_stable_code(self) -> None:
        result = ensure_response_metadata({"status": "timeout", "reason": "timed out"})

        self.assertEqual(result["code"], "TIMEOUT")
        self.assertEqual(result["error"], "timed out")

    def test_specific_profile_error_is_not_downgraded_to_generic_not_found(self) -> None:
        result = ensure_response_metadata(
            {"status": "failed", "reason": "profile not found: operator"}
        )

        self.assertEqual(result["code"], "PROFILE_NOT_FOUND")


class CompletenessInstructionTests(unittest.TestCase):
    def test_server_instructions_prohibit_negative_conclusions_from_partial_results(self) -> None:
        from qa_automation.mcp.instructions import SERVER_INSTRUCTIONS

        self.assertIn("结果完整性与覆盖范围", SERVER_INSTRUCTIONS)
        self.assertIn("has_more=true", SERVER_INSTRUCTIONS)
        self.assertIn("绝不能把", SERVER_INSTRUCTIONS)
        self.assertIn("has_more=false", SERVER_INSTRUCTIONS)


class ToolAnnotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_key_tools_expose_read_and_mutation_hints(self) -> None:
        async with Client(server.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}

        annotations = {
            name: tools[name].annotations
            for name in (
                "ui_snapshot",
                "vtable_read_cells",
                "browser_close",
                "browser_login",
                "run_js",
                "ui_click",
                "interaction_chain",
                "scenario_run",
                "x6_delete_node",
                "tencent_sheet_update_row",
            )
        }

        self.assertTrue(annotations["ui_snapshot"].read_only_hint)
        self.assertTrue(annotations["vtable_read_cells"].read_only_hint)
        self.assertFalse(annotations["browser_close"].read_only_hint)
        self.assertTrue(annotations["browser_close"].destructive_hint)
        self.assertFalse(annotations["browser_login"].read_only_hint)
        self.assertTrue(annotations["browser_login"].destructive_hint)
        self.assertFalse(annotations["run_js"].read_only_hint)
        self.assertTrue(annotations["run_js"].open_world_hint)
        self.assertTrue(annotations["ui_click"].destructive_hint)
        self.assertTrue(annotations["interaction_chain"].destructive_hint)
        self.assertTrue(annotations["scenario_run"].destructive_hint)
        self.assertTrue(annotations["x6_delete_node"].destructive_hint)
        self.assertTrue(annotations["tencent_sheet_update_row"].destructive_hint)


class InstrumentedProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_instrumented_tool_attaches_trace_and_failure_code(self) -> None:
        reset_metrics()

        @instrument_tool
        async def sample() -> dict:
            return {"status": "failed", "reason": "vtable-not-bound: missing table"}

        result = await sample()
        snapshot = metrics_snapshot(limit=1)

        self.assertEqual(result["code"], "VTABLE_NOT_FOUND")
        self.assertEqual(result["metrics"]["trace_id"], result["trace_id"])
        self.assertEqual(snapshot["recent"][-1]["trace_id"], result["trace_id"])

    async def test_sync_tool_preserves_pydantic_model_and_adds_metadata(self) -> None:
        reset_metrics()

        @instrument_tool
        def sample(ok: bool = True) -> SheetConnectResult:
            return SheetConnectResult(
                ok=ok,
                file_id="file-1",
                message="missing token" if not ok else "connected",
            )

        result = sample(ok=False)

        self.assertIsInstance(result, SheetConnectResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "UPSTREAM_AUTH_FAILED")
        self.assertEqual(result.error, "missing token")
        self.assertTrue(result.trace_id.startswith("qa_"))
        self.assertEqual(result.metrics["trace_id"], result.trace_id)
        self.assertEqual(metrics_snapshot()["summary"]["sample"]["failures"], 1)

    async def test_nested_instrumented_tools_share_trace_and_count_once(self) -> None:
        reset_metrics()

        @instrument_tool
        def inner() -> dict:
            return {"status": "ok", "inner": True}

        @instrument_tool
        def outer() -> dict:
            return inner()

        result = outer()
        summary = metrics_snapshot()["summary"]

        self.assertEqual(result["trace_id"], result["metrics"]["trace_id"])
        self.assertIn("outer", summary)
        self.assertNotIn("inner", summary)
        self.assertEqual(summary["outer"]["calls"], 1)

    async def test_metrics_reset_clears_state(self) -> None:
        reset_metrics()

        @instrument_tool
        async def sample() -> dict:
            return {"status": "ok"}

        await sample()
        self.assertEqual(metrics_snapshot()["summary"]["sample"]["calls"], 1)
        reset_metrics()
        self.assertEqual(metrics_snapshot()["summary"], {})


class TencentStructuredResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_pydantic_tool_result_exposes_protocol_metadata_over_mcp(self) -> None:
        import qa_automation.mcp.servers.tencent_docs as tencent_docs_module

        with patch.object(
            tencent_docs_module.tencent_sheet_manager,
            "connect",
            return_value={
                "ok": True,
                "file_id": "file-123",
                "message": "connected",
            },
        ):
            async with Client(tencent_docs_module.create_server()) as client:
                result = await client.call_tool("tencent_sheet_connect", {})

        payload = result.structured_content
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["code"], "OK")
        self.assertEqual(payload["file_id"], "file-123")
        self.assertTrue(payload["trace_id"].startswith("qa_"))
        self.assertEqual(payload["metrics"]["trace_id"], payload["trace_id"])


class DiagnosticsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_metrics_reset_tool_is_registered_and_dispatches(self) -> None:
        async with Client(create_server()) as client:
            tools = {tool.name for tool in await client.list_tools()}
            result = await client.call_tool("automation_metrics_reset", {})

        self.assertIn("automation_metrics", tools)
        self.assertIn("automation_metrics_reset", tools)
        self.assertIsNotNone(result.structured_content)
        self.assertEqual(result.structured_content["status"], "ok")
        self.assertTrue(result.structured_content["reset"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
