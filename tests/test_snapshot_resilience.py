from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp import Client

from qa_automation.interaction import snapshot
from qa_automation.mcp import server


class SnapshotResilienceTest(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_root_when_no_selector(self) -> None:
        mock_frame = MagicMock()
        mock_root = MagicMock()
        mock_root.aria_snapshot = AsyncMock(return_value="- document: root content")
        mock_frame.locator = MagicMock(return_value=mock_root)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector=None)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["snapshot"], "- document: root content")
            self.assertIsNone(res["selector"])
            mock_frame.locator.assert_called_with(":root")

    async def test_snapshot_single_element_match(self) -> None:
        mock_frame = MagicMock()
        mock_target = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock(return_value=None)
        mock_first.is_visible = AsyncMock(return_value=True)
        mock_first.aria_snapshot = AsyncMock(return_value="- button \"Submit\"")
        mock_target.first = mock_first
        mock_target.count = AsyncMock(return_value=1)
        mock_frame.locator = MagicMock(return_value=mock_target)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector="button.ant-btn")
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["match_count"], 1)
            self.assertTrue(res["visible"])
            self.assertEqual(res["snapshot"], "- button \"Submit\"")

    async def test_snapshot_zero_elements_not_found(self) -> None:
        mock_frame = MagicMock()
        mock_target = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock(side_effect=Exception("Timeout waiting for element"))
        mock_target.first = mock_first
        mock_target.count = AsyncMock(return_value=0)
        mock_frame.locator = MagicMock(return_value=mock_target)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector="nonexistent-selector", timeout=0.1)
            self.assertEqual(res["status"], "not_found")
            self.assertEqual(res["match_count"], 0)
            self.assertEqual(res["snapshot"], "")
            self.assertIn("nonexistent-selector", res["message"])

    async def test_snapshot_multiple_elements_avoids_strict_mode_violation(self) -> None:
        mock_frame = MagicMock()
        mock_target = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock(return_value=None)
        mock_target.first = mock_first
        mock_target.count = AsyncMock(return_value=2)

        # First element (hidden modal)
        mock_el0 = MagicMock()
        mock_el0.is_visible = AsyncMock(return_value=False)
        mock_el0.aria_snapshot = AsyncMock(return_value="- dialog: hidden modal")

        # Second element (visible modal)
        mock_el1 = MagicMock()
        mock_el1.is_visible = AsyncMock(return_value=True)
        mock_el1.aria_snapshot = AsyncMock(return_value="- dialog: visible active modal")

        def nth_mock(n: int) -> MagicMock:
            return mock_el0 if n == 0 else mock_el1

        mock_target.nth = MagicMock(side_effect=nth_mock)
        mock_frame.locator = MagicMock(return_value=mock_target)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector="div.ant-modal")
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["match_count"], 2)
            self.assertEqual(res["shown_count"], 2)
            self.assertIn("/* Match 1 of 2 (hidden): div.ant-modal >> nth=0 */", res["snapshot"])
            self.assertIn("- dialog: hidden modal", res["snapshot"])
            self.assertIn("/* Match 2 of 2 (visible): div.ant-modal >> nth=1 */", res["snapshot"])
            self.assertIn("- dialog: visible active modal", res["snapshot"])

    async def test_snapshot_multiple_elements_with_visible_only(self) -> None:
        mock_frame = MagicMock()
        mock_target = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock(return_value=None)
        mock_target.first = mock_first
        mock_target.count = AsyncMock(return_value=2)

        mock_el0 = MagicMock()
        mock_el0.is_visible = AsyncMock(return_value=False)
        mock_el0.aria_snapshot = AsyncMock(return_value="- hidden")

        mock_el1 = MagicMock()
        mock_el1.is_visible = AsyncMock(return_value=True)
        mock_el1.aria_snapshot = AsyncMock(return_value="- active dialog")

        mock_target.nth = MagicMock(side_effect=lambda n: mock_el0 if n == 0 else mock_el1)
        mock_frame.locator = MagicMock(return_value=mock_target)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector="div.ant-modal", visible_only=True)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["match_count"], 2)
            self.assertEqual(res["shown_count"], 1)
            self.assertEqual(res["matched_elements"][0]["index"], 1)
            self.assertTrue(res["matched_elements"][0]["visible"])
            self.assertNotIn("Match 1 of 2", res["snapshot"])
            self.assertIn("/* Match 2 of 2 (visible): div.ant-modal >> nth=1 */", res["snapshot"])

    async def test_snapshot_explicit_nth(self) -> None:
        mock_frame = MagicMock()
        mock_target = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock(return_value=None)
        mock_target.first = mock_first
        mock_target.count = AsyncMock(return_value=3)

        mock_el1 = MagicMock()
        mock_el1.is_visible = AsyncMock(return_value=True)
        mock_el1.aria_snapshot = AsyncMock(return_value="- dialog #1")

        mock_target.nth = MagicMock(return_value=mock_el1)
        mock_frame.locator = MagicMock(return_value=mock_target)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector="div.ant-modal", nth=1)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["nth"], 1)
            self.assertEqual(res["match_count"], 3)
            self.assertEqual(res["snapshot"], "- dialog #1")
            mock_target.nth.assert_called_with(1)

    async def test_snapshot_max_elements_capping(self) -> None:
        mock_frame = MagicMock()
        mock_target = MagicMock()
        mock_first = MagicMock()
        mock_first.wait_for = AsyncMock(return_value=None)
        mock_target.first = mock_first
        mock_target.count = AsyncMock(return_value=5)

        def make_el(i: int) -> MagicMock:
            el = MagicMock()
            el.is_visible = AsyncMock(return_value=True)
            el.aria_snapshot = AsyncMock(return_value=f"- item {i}")
            return el

        mock_target.nth = MagicMock(side_effect=make_el)
        mock_frame.locator = MagicMock(return_value=mock_target)
        mock_frame.evaluate = AsyncMock(return_value=None)

        with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=MagicMock())), \
             patch.object(snapshot, "resolve_frame", AsyncMock(return_value=mock_frame)):
            res = await snapshot._dom_snapshot_impl(selector="li", max_elements=2)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["match_count"], 5)
            self.assertEqual(res["shown_count"], 2)
            self.assertIn("Displayed 2", res["snapshot"])
            self.assertIn("inspect remaining 3 elements", res["snapshot"])

    async def test_snapshot_via_mcp_client_with_new_parameters(self) -> None:
        with patch.object(
            snapshot,
            "_dom_snapshot_impl",
            AsyncMock(return_value={"status": "ok", "snapshot": "- dialog #1", "match_count": 2}),
        ) as mock_impl:
            async with Client(server.mcp) as client:
                call_res = await client.call_tool(
                    "ui_snapshot",
                    {
                        "selector": "div.ant-modal",
                        "nth": 1,
                        "visible_only": True,
                        "max_elements": 3,
                        "timeout": 1.5,
                    },
                )
                self.assertIsNotNone(call_res.structured_content)
                self.assertEqual(call_res.structured_content["status"], "ok")
                mock_impl.assert_called_once()
                _, kwargs = mock_impl.call_args
                self.assertEqual(kwargs["selector"], "div.ant-modal")
                self.assertEqual(kwargs["nth"], 1)
                self.assertTrue(kwargs["visible_only"])
                self.assertEqual(kwargs["max_elements"], 3)
                self.assertEqual(kwargs["timeout"], 1.5)


if __name__ == "__main__":
    unittest.main()
