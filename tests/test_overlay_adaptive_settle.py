"""Unit coverage for adaptive differential settling of overlay observation.

迁移自 DrissionPage-MCP overlays.py 的自适应收敛,必须在以下三点上可回归:
  1. 真收敛(变更+安静+无 loading)时提前收口,不空等满窗口;
  2. 没看到变更 / 探针不可用时,退回固定等待,观察窗口一秒不少;
  3. settle_ms 永远是硬上限,任何分支都不会等得比改造前更久。
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from qa_automation.overlay import _await_overlay_settle
from qa_automation.overlay import scripts as overlay_scripts


def _fake_page(results, *, frames=None):
    """按 frame 结果构造假页面;results 中的 Exception 表示该 frame evaluate 失败。"""
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    if frames is None:
        frames = [MagicMock() for _ in results]
    for frame, result in zip(frames, results, strict=False):
        if isinstance(result, Exception):
            frame.evaluate = AsyncMock(side_effect=result)
        else:
            frame.evaluate = AsyncMock(return_value=result)
    page.frames = frames
    return page


class AdaptiveSettleTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_settle_is_disabled_and_touches_nothing(self) -> None:
        page = _fake_page([{"mode": "converged"}])
        info = await _await_overlay_settle(page, 0)

        self.assertEqual(info["settle_ms"], 0)
        self.assertEqual(info["settle_elapsed_ms"], 0)
        self.assertEqual(info["settle_mode"], "disabled")
        page.wait_for_timeout.assert_not_awaited()
        page.frames[0].evaluate.assert_not_awaited()

    async def test_converged_window_exits_without_fixed_wait(self) -> None:
        page = _fake_page(
            [{"mode": "converged", "observed_mutations": True, "elapsed_ms": 42, "idle_ms": 30}]
        )
        info = await _await_overlay_settle(page, 300)

        self.assertEqual(info["settle_mode"], "converged")
        self.assertTrue(info["settle_observed_mutations"])
        self.assertLess(info["settle_elapsed_ms"], 300)
        page.wait_for_timeout.assert_not_awaited()

    async def test_settled_window_consumes_the_requested_budget(self) -> None:
        page = _fake_page([{"mode": "settled", "observed_mutations": False, "elapsed_ms": 300}])
        info = await _await_overlay_settle(page, 300)

        self.assertEqual(info["settle_mode"], "settled")
        self.assertFalse(info["settle_observed_mutations"])
        # 窗口被真正消费:绝不允许"探针说等满了,实际却提前返回"
        self.assertGreaterEqual(info["settle_elapsed_ms"], 300)
        page.wait_for_timeout.assert_not_awaited()

    async def test_any_frame_converging_wins(self) -> None:
        results = [
            {"mode": "settled", "observed_mutations": False, "elapsed_ms": 300},
            {"mode": "converged", "observed_mutations": True, "elapsed_ms": 51},
            {"mode": "unobserved", "observed_mutations": False, "elapsed_ms": 0},
        ]
        page = _fake_page(results)
        info = await _await_overlay_settle(page, 300)

        self.assertEqual(info["settle_mode"], "converged")
        page.wait_for_timeout.assert_not_awaited()

    async def test_unobserved_frames_fall_back_to_fixed_wait(self) -> None:
        page = _fake_page([{"mode": "unobserved", "observed_mutations": False, "elapsed_ms": 0}])
        info = await _await_overlay_settle(page, 120)

        self.assertEqual(info["settle_mode"], "fixed")
        self.assertEqual(info["settle_elapsed_ms"], 120)
        page.wait_for_timeout.assert_awaited_once_with(120)

    async def test_frame_list_failure_falls_back_to_fixed_wait(self) -> None:
        page = MagicMock()
        page.wait_for_timeout = AsyncMock()
        type(page).frames = property(lambda self: (_ for _ in ()).throw(RuntimeError("detached")))

        info = await _await_overlay_settle(page, 90)

        self.assertEqual(info["settle_mode"], "fixed")
        page.wait_for_timeout.assert_awaited_once_with(90)

    async def test_probe_exception_falls_back_to_fixed_wait(self) -> None:
        page = _fake_page([RuntimeError("frame detached")])
        info = await _await_overlay_settle(page, 150)

        self.assertEqual(info["settle_mode"], "fixed")
        self.assertEqual(info["settle_elapsed_ms"], 150)
        page.wait_for_timeout.assert_awaited_once_with(150)

    async def test_env_switch_disables_adaptive_path(self) -> None:
        page = _fake_page([{"mode": "converged", "observed_mutations": True, "elapsed_ms": 5}])
        with patch.object(overlay_scripts, "OVERLAY_QUIET_MS", 25), patch(
            "qa_automation.overlay.OVERLAY_ADAPTIVE_SETTLE", False
        ):
            info = await _await_overlay_settle(page, 200)

        self.assertEqual(info["settle_mode"], "fixed")
        page.wait_for_timeout.assert_awaited_once_with(200)
        page.frames[0].evaluate.assert_not_awaited()

    async def test_adaptive_script_substitutes_every_placeholder(self) -> None:
        script = overlay_scripts._adaptive_settle_script(420, 25, 30)

        # 大写下划线占位符必须全部被替换掉(观察者 key 自身含双下划线,故用正则而非子串判断)
        self.assertIsNone(re.search(r"__[A-Z_]+__", script))
        self.assertIn("420", script)
        self.assertIn("25", script)
        self.assertIn("30", script)
        self.assertIn(overlay_scripts.OVERLAY_OBSERVER_KEY, script)
        # 收敛判据必须同时包含静默期与 loading 兜底,缺一条都会提前收口丢事件
        self.assertIn("quietMs", script)
        self.assertIn("loading()", script)


if __name__ == "__main__":
    unittest.main()
