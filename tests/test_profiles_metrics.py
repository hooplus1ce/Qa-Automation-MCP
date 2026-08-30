"""Contracts for profiles, strategies, and low-overhead tool metrics."""

from __future__ import annotations

import unittest

from qa_automation.mcp.metrics import instrument_tool, metrics_snapshot
from qa_automation.profiles import profile_contract


class ProfileTests(unittest.TestCase):
    def test_aps_profile_exposes_ordered_strategies(self) -> None:
        contract = profile_contract()

        self.assertEqual(contract["profile"]["name"], "aps-antd")
        self.assertEqual(
            contract["locator_strategy"]["order"][:3],
            ("css", "ax-role", "xpath"),
        )
        self.assertEqual(
            contract["vtable_verification_strategy"]["order"][-2:],
            ("scenegraph-changed", "screenshot-changed"),
        )


class MetricTests(unittest.IsolatedAsyncioTestCase):
    async def test_instrumented_tool_attaches_context_cost(self) -> None:
        @instrument_tool
        async def sample(value: str) -> dict:
            return {"status": "ok", "controls": [value]}

        result = await sample("abc")
        snapshot = metrics_snapshot()

        self.assertGreater(result["metrics"]["response_bytes"], 0)
        self.assertGreater(result["metrics"]["estimated_context_tokens"], 0)
        self.assertEqual(result["metrics"]["result_count"], 1)
        self.assertEqual(snapshot["summary"]["sample"]["calls"], 1)

    async def test_instrumented_tool_records_exceptions(self) -> None:
        @instrument_tool
        async def failing_tool() -> dict:
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await failing_tool()
        snapshot = metrics_snapshot()
        self.assertEqual(snapshot["summary"]["failing_tool"]["failures"], 1)
        self.assertEqual(snapshot["recent"][-1]["error"], "RuntimeError")
