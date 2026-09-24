"""真机视口护栏测试(默认跳过,需 QA_AUTOMATION_LIVE_CDP=1)。

复现并回归真机上真实发生过的两个缺陷:
  A. ui_screenshot 的 clip 截图会临时下发 device-metrics override;截图被中断时
     override 残留,页面视口被锁成裁剪框尺寸(实测 900x383),之后所有坐标错位。
  B. Chromium 不接受 minimized -> maximized 的直接跳变,窗口停在最小化时复位失效。

运行方式(需要已用 --remote-debugging-port 启动的 Chrome):
    QA_AUTOMATION_LIVE_CDP=1 uv run --with pytest --with playwright \
        python -m pytest tests/test_viewport_guard_browser.py -q
"""

from __future__ import annotations

import asyncio
import os
import unittest

import pytest

from qa_automation.browser import _restore_window_and_viewport

LIVE = os.getenv("QA_AUTOMATION_LIVE_CDP") == "1"
CDP_URL = os.getenv("QA_AUTOMATION_CDP_URL", "http://127.0.0.1:9222")
CLIP = (900.0, 383.0)


async def _read_size(page) -> dict:
    raw = await page.evaluate("() => ({w: innerWidth, h: innerHeight})")
    return {"width": int(raw["w"]), "height": int(raw["h"])}


async def _set_window_state(page, state: str) -> None:
    cdp = await page.context.new_cdp_session(page)
    try:
        info = await cdp.send("Target.getTargetInfo")
        target_id = (info or {}).get("targetInfo", {}).get("targetId")
        window = await cdp.send("Browser.getWindowForTarget", {"targetId": target_id})
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": window["windowId"], "bounds": {"windowState": state}},
        )
    finally:
        await cdp.detach()


async def _force_maximize(page) -> None:
    await _set_window_state(page, "normal")
    await _set_window_state(page, "maximized")


@pytest.mark.skipif(not LIVE, reason="需要 QA_AUTOMATION_LIVE_CDP=1 与在线 CDP 浏览器")
class LiveViewportGuardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.connect_over_cdp(CDP_URL)
        self.page = None
        for context in self.browser.contexts:
            for page in context.pages:
                if page.url and "about:blank" not in page.url:
                    self.page = page
                    break
            if self.page is not None:
                break
        if self.page is None:
            await self._pw.stop()
            self.skipTest("CDP 浏览器里没有可用页面")
        await _force_maximize(self.page)

    async def asyncTearDown(self) -> None:
        if self.page is not None:
            try:
                await _force_maximize(self.page)
            except Exception:
                pass
        try:
            await self.browser.close()
        except Exception:
            pass
        await self._pw.stop()

    async def test_residual_clip_override_is_cleared(self) -> None:
        natural = await _read_size(self.page)

        cdp = await self.page.context.new_cdp_session(self.page)
        await cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": int(CLIP[0]),
                "height": int(CLIP[1]),
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await cdp.detach()
        locked = await _read_size(self.page)
        self.assertEqual(locked["width"], int(CLIP[0]), "override 未生效,前置条件不成立")

        report = await _restore_window_and_viewport(
            self.page, clip_size=CLIP, expected_size=(natural["width"], natural["height"])
        )

        self.assertFalse(report["clip_lock_detected"], f"仍判定为残留: {report}")
        self.assertEqual(await _read_size(self.page), natural)

    async def test_minimized_window_is_recovered(self) -> None:
        natural = await _read_size(self.page)

        await _set_window_state(self.page, "minimized")
        await asyncio.sleep(0.4)

        report = await _restore_window_and_viewport(self.page)

        self.assertEqual(report["window_state"], "maximized", f"窗口未复位: {report}")
        self.assertEqual(await _read_size(self.page), natural)
