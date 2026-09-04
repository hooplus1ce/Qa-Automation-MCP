"""Unit and integration tests for cursor settling behavior in ui_click/dom_interact."""

from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock

import qa_automation as automation
from qa_automation.mouse import _smooth_mouse_move_to


class CursorSettleTests(unittest.IsolatedAsyncioTestCase):
    async def test_smooth_mouse_move_to_settles_before_returning(self) -> None:
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock()
        mock_page.mouse = AsyncMock()

        start = time.monotonic()
        await _smooth_mouse_move_to(mock_page, 200, 200, settle_ms=80)
        elapsed = time.monotonic() - start

        self.assertGreaterEqual(elapsed, 0.075)
        # Should have updated cursor to target_x, target_y at stationary state
        eval_calls = [
            call.args[0]
            for call in mock_page.evaluate.call_args_list
            if isinstance(call.args[0], str) and "update_cursor" in call.args[0]
        ]
        self.assertTrue(any("200.0, 200.0, false, false" in c for c in eval_calls))

    async def test_dom_interact_click_settles_cursor_and_applies_press_delay(self) -> None:
        try:
            await automation.start_browser(headless=True)
        except Exception as exc:
            raise unittest.SkipTest(f"Playwright browser unavailable: {exc}") from exc

        page = await automation.current_page()
        await page.set_content(
            "<button id='btn' style='position:absolute;left:50px;top:50px;width:100px;height:40px;'>Click Me</button>"
            "<script>"
            "window.__events = [];"
            "const b = document.querySelector('#btn');"
            "['mouseenter', 'mouseover', 'mousedown', 'mouseup', 'click'].forEach(evt => {"
            "  b.addEventListener(evt, e => window.__events.push({type: e.type, t: performance.now()}));"
            "});"
            "</script>"
        )

        result = await automation.dom_interact(
            "click",
            css="#btn",
            observe_after=False,
        )

        self.assertEqual(result["status"], "acted")
        events = await page.evaluate("() => window.__events")
        types = [e["type"] for e in events]
        self.assertIn("click", types)
        self.assertIn("mousedown", types)
        self.assertIn("mouseup", types)

        # Verify mouseenter / mouseover happened before click
        click_idx = types.index("click")
        if "mouseenter" in types:
            self.assertLess(types.index("mouseenter"), click_idx)

        # Verify duration between mousedown and mouseup (press delay >= 25ms)
        md = next(e for e in events if e["type"] == "mousedown")
        mu = next(e for e in events if e["type"] == "mouseup")
        press_duration = mu["t"] - md["t"]
        self.assertGreaterEqual(press_duration, 20)

        await automation.close_browser()
    async def test_dom_interact_rechecks_target_after_hover_layout_shift(self) -> None:
        try:
            await automation.start_browser(headless=True)
        except Exception as exc:
            raise unittest.SkipTest(f"Playwright browser unavailable: {exc}") from exc

        page = await automation.current_page()
        await page.set_content(
            "<button id='moving' style='position:absolute;left:50px;top:50px;width:100px;height:40px'>"
            "Click Me</button>"
            "<script>"
            "window.__clicks = 0;"
            "const b = document.querySelector('#moving');"
            "b.addEventListener('mouseenter', () => { b.style.left = '180px'; });"
            "b.addEventListener('click', () => window.__clicks++);"
            "</script>"
        )

        result = await automation.dom_interact(
            "click",
            css="#moving",
            observe_after=False,
        )

        self.assertEqual(result["status"], "acted")
        self.assertEqual(await page.evaluate("window.__clicks"), 1)
        await automation.close_browser()


if __name__ == "__main__":
    unittest.main()
