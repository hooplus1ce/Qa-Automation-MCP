"""Unit tests for visual drag ghost in _mouse_drag_impl and mouse_drag."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from qa_automation.mouse import (
    _GHOST_CLEANUP_SCRIPT,
    _GHOST_FINISH_SCRIPT,
    _GHOST_START_SCRIPT,
    _GHOST_UPDATE_SCRIPT,
    _mouse_drag_impl,
)


def _mock_page_evaluate(script, *args):
    if "window.innerWidth" in str(script):
        return {"width": 1920.0, "height": 1080.0}
    return True


class MouseDragGhostTests(unittest.IsolatedAsyncioTestCase):
    async def test_mouse_drag_with_visual_ghost(self) -> None:
        mock_page = MagicMock()
        mock_page.context = MagicMock()
        mock_page.context.new_cdp_session = AsyncMock(side_effect=Exception("no cdp"))
        mock_page.evaluate = AsyncMock(side_effect=_mock_page_evaluate)
        mock_page.mouse = AsyncMock()
        mock_page.url = "https://example.com"

        main_frame = MagicMock()
        main_frame.evaluate = AsyncMock(return_value=True)
        mock_page.main_frame = main_frame
        mock_page.frames = [main_frame]

        result = await _mouse_drag_impl(
            mock_page,
            100.0,
            100.0,
            300.0,
            100.0,
            steps=10,
            hold_ms=10,
            settle_ms=10,
            visual_ghost=True,
        )

        self.assertEqual(result["status"], "dragged")
        self.assertTrue(result["visual_ghost"])
        self.assertEqual(result["distance"], 200.0)

        # Verify ghost scripts were evaluated on frame
        eval_scripts = [call.args[0] for call in main_frame.evaluate.call_args_list]
        self.assertIn(_GHOST_START_SCRIPT, eval_scripts)
        self.assertIn(_GHOST_UPDATE_SCRIPT, eval_scripts)
        self.assertIn(_GHOST_FINISH_SCRIPT, eval_scripts)
        self.assertIn(_GHOST_CLEANUP_SCRIPT, eval_scripts)

    async def test_mouse_drag_with_visual_ghost_disabled(self) -> None:
        mock_page = MagicMock()
        mock_page.context = MagicMock()
        mock_page.context.new_cdp_session = AsyncMock(side_effect=Exception("no cdp"))
        mock_page.evaluate = AsyncMock(side_effect=_mock_page_evaluate)
        mock_page.mouse = AsyncMock()
        mock_page.url = "https://example.com"

        main_frame = MagicMock()
        main_frame.evaluate = AsyncMock(return_value=True)
        mock_page.main_frame = main_frame
        mock_page.frames = [main_frame]

        result = await _mouse_drag_impl(
            mock_page,
            100.0,
            100.0,
            300.0,
            100.0,
            steps=10,
            hold_ms=10,
            settle_ms=10,
            visual_ghost=False,
        )

        self.assertEqual(result["status"], "dragged")
        self.assertFalse(result["visual_ghost"])

        eval_scripts = [call.args[0] for call in main_frame.evaluate.call_args_list]
        self.assertNotIn(_GHOST_START_SCRIPT, eval_scripts)


if __name__ == "__main__":
    unittest.main()
