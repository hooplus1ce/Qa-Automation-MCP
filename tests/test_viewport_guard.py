"""视口护栏回归测试。

真机 APS 复现的缺陷:ui_screenshot 走 Playwright clip 截图时,Chromium 会临时把
视口撑到裁剪框尺寸;该次截图一旦被中断(超时 / 窗口最小化),override 会残留,
页面视口被锁成元素尺寸(实测 900x383、715x270),此后 vtable、浮层、点击的坐标
全部错位。同时 Chromium 不接受 minimized -> maximized 的直接跳变,只发 maximized
是 no-op,导致原有的复位逻辑在窗口最小化时彻底失效。

这里用假 page/CDP session 覆盖两条修复路径,不依赖真实浏览器。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import qa_automation.browser as browser
from qa_automation.interaction import snapshot


class _FakeCdpSession:
    """记录所有 CDP 指令,并把 setWindowBounds 生效到本地 bounds 上。"""

    def __init__(self, bounds: dict) -> None:
        self.bounds = dict(bounds)
        self.sent: list[tuple[str, dict | None]] = []
        self.detached = False

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, params))
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"targetId": "target-1"}}
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7, "bounds": dict(self.bounds)}
        if method == "Browser.setWindowBounds":
            self.bounds.update((params or {}).get("bounds") or {})
            return {}
        return {}

    async def detach(self) -> None:
        self.detached = True


class _FakePage:
    """只实现 _restore_window_and_viewport 触达的最小面。"""

    def __init__(self, sizes: list[tuple[float, float]], bounds: dict) -> None:
        self._sizes = list(sizes)
        self._last = self._sizes[-1] if self._sizes else (0.0, 0.0)
        self.session = _FakeCdpSession(bounds)
        self.context = SimpleNamespace(
            new_cdp_session=AsyncMock(side_effect=self._new_session)
        )
        self.viewport_size = None
        self.set_viewport_size = AsyncMock()

    async def _new_session(self, _page):  # pragma: no cover - 由 mock 调用
        return self.session

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, expression: str):
        if "innerWidth" not in expression:
            return None
        if self._sizes:
            self._last = self._sizes.pop(0)
        return {"w": self._last[0], "h": self._last[1]}


class ClipLockDetectionTest(unittest.TestCase):
    def test_exact_clip_size_is_detected(self) -> None:
        self.assertTrue(browser._looks_like_clip_lock({"w": 900.0, "h": 383.0}, (900.0, 383.0)))

    def test_tolerance_covers_at_most_one_pixel_of_rounding(self) -> None:
        # Chromium 回读 innerWidth 会有小数舍入,1px 以内仍算命中
        self.assertTrue(browser._looks_like_clip_lock({"w": 900.6, "h": 383.0}, (900.0, 383.0)))
        self.assertTrue(browser._looks_like_clip_lock({"w": 901.0, "h": 382.0}, (900.0, 383.0)))
        self.assertFalse(browser._looks_like_clip_lock({"w": 902.0, "h": 383.0}, (900.0, 383.0)))

    def test_different_size_is_not_a_lock(self) -> None:
        self.assertFalse(browser._looks_like_clip_lock({"w": 1920.0, "h": 1000.0}, (900.0, 383.0)))

    def test_full_viewport_clip_is_not_a_lock(self) -> None:
        # 全视口截图时 clip 本来就等于自然视口,不能误报成残留
        measured = {"w": 1920.0, "h": 1000.0}
        self.assertFalse(
            browser._looks_like_clip_lock(measured, (1920.0, 1000.0), (1920.0, 1000.0))
        )
        # 但同样尺寸下,若截图前视口本来更小,就确实是残留
        self.assertTrue(
            browser._looks_like_clip_lock(measured, (1920.0, 1000.0), (900.0, 383.0))
        )

    def test_missing_inputs_are_not_a_lock(self) -> None:
        self.assertFalse(browser._looks_like_clip_lock(None, (900.0, 383.0)))
        self.assertFalse(browser._looks_like_clip_lock({"w": 900.0, "h": 383.0}, None))
        self.assertFalse(browser._looks_like_clip_lock({"w": 900.0, "h": 383.0}, (0.0, 0.0)))


class RestoreWindowAndViewportTest(unittest.IsolatedAsyncioTestCase):
    async def test_minimized_window_goes_through_normal_before_maximized(self) -> None:
        page = _FakePage([(1920.0, 1000.0)], {"windowState": "minimized", "width": 1390, "height": 1032})

        report = await browser._restore_window_and_viewport(
            page, restore_bounds={"left": 281, "top": 0, "width": 1390, "height": 1032}
        )

        commands = [method for method, _ in page.session.sent]
        self.assertIn("Browser.setWindowBounds", commands)
        state_calls = [
            params
            for method, params in page.session.sent
            if method == "Browser.setWindowBounds"
        ]
        # 第一条必须是 normal 中转,否则 Chromium 会静默忽略 maximized
        self.assertEqual(state_calls[0]["bounds"]["windowState"], "normal")
        self.assertEqual(state_calls[0]["bounds"]["width"], 1390)
        self.assertEqual(state_calls[1]["bounds"]["windowState"], "maximized")
        self.assertEqual(report["window_state"], "maximized")
        self.assertFalse(report["clip_lock_detected"])

    async def test_metrics_are_cleared_with_zero_override_then_clear(self) -> None:
        page = _FakePage([(1920.0, 1000.0)], {"windowState": "maximized"})

        await browser._restore_window_and_viewport(page)

        metrics = [
            (method, params)
            for method, params in page.session.sent
            if method.startswith("Emulation.")
        ]
        self.assertEqual(metrics[0][0], "Emulation.setDeviceMetricsOverride")
        self.assertEqual(metrics[0][1]["width"], 0)
        self.assertEqual(metrics[0][1]["height"], 0)
        self.assertEqual(metrics[1][0], "Emulation.clearDeviceMetricsOverride")
        self.assertTrue(page.session.detached)

    async def test_clip_size_lock_is_retried_and_reported(self) -> None:
        # 两轮测得的视口都等于 clip 尺寸 -> 判定残留未清掉,重试一轮后如实上报
        page = _FakePage([(715.0, 270.0), (715.0, 270.0)], {"windowState": "maximized"})

        report = await browser._restore_window_and_viewport(page, clip_size=(715.0, 270.0))

        self.assertTrue(report["clip_lock_detected"])
        self.assertEqual(report["attempts"], 2)

    async def test_retry_succeeds_when_second_measurement_is_natural(self) -> None:
        page = _FakePage([(715.0, 270.0), (1920.0, 1000.0)], {"windowState": "maximized"})

        report = await browser._restore_window_and_viewport(page, clip_size=(715.0, 270.0))

        self.assertFalse(report["clip_lock_detected"])
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(report["viewport"], {"width": 1920, "height": 1000})

    async def test_natural_size_matching_clip_is_not_retried(self) -> None:
        # clip 与自然视口同为 1920x1000:不应触发重试,attempts 保持 1
        page = _FakePage([(1920.0, 1000.0)], {"windowState": "maximized"})

        report = await browser._restore_window_and_viewport(
            page,
            clip_size=(1920.0, 1000.0),
            expected_size=(1920.0, 1000.0),
        )

        self.assertFalse(report["clip_lock_detected"])
        self.assertEqual(report["attempts"], 1)

    async def test_closed_or_cdp_less_page_is_a_noop(self) -> None:
        report = await browser._restore_window_and_viewport(None)
        self.assertEqual(report["attempts"], 0)

        closed = SimpleNamespace(is_closed=lambda: True)
        report = await browser._restore_window_and_viewport(closed)
        self.assertEqual(report["attempts"], 0)

        plain = SimpleNamespace(is_closed=lambda: False)
        report = await browser._restore_window_and_viewport(plain)
        self.assertEqual(report["attempts"], 0)


class ScreenshotViewportGuardTest(unittest.IsolatedAsyncioTestCase):
    def _patch_common(self, page):
        # _read_viewport_or_none 会读这个;给一个自然视口尺寸便于断言 expected_size
        page.evaluate = AsyncMock(return_value={"w": 1920.0, "h": 1000.0})
        target = SimpleNamespace(
            wait_for=AsyncMock(return_value=None),
            bounding_box=AsyncMock(
                return_value={"x": 10.0, "y": 20.0, "width": 900.0, "height": 383.0}
            ),
        )
        return [
            patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=page)),
            patch.object(
                snapshot,
                "_find_interaction_locator",
                AsyncMock(return_value=(SimpleNamespace(first=target), page.main_frame, "css")),
            ),
            patch.object(snapshot, "_frame_details", lambda *a, **kw: {"frame_id": "f1"}),
            patch.object(snapshot, "_page_id", lambda *a, **kw: "page_test"),
        ]

    async def test_timeout_restores_viewport_and_raises_actionable_error(self) -> None:
        page = MagicMock()
        page.main_frame = object()
        page.screenshot = AsyncMock(side_effect=TimeoutError())
        capture = AsyncMock(return_value={"windowState": "minimized", "width": 1390})
        restore = AsyncMock(return_value={"clip_lock_detected": False, "window_state": "maximized"})

        with tempfile.TemporaryDirectory() as tmp:
            patches = self._patch_common(page)
            patches += [
                patch.object(snapshot, "artifact_file", lambda *a, **kw: Path(tmp) / "shot.png"),
                patch.object(snapshot, "_capture_window_bounds", capture),
                patch.object(snapshot, "_restore_window_and_viewport", restore),
            ]
            for item in patches:
                item.start()
            try:
                with self.assertRaises(TimeoutError) as ctx:
                    await snapshot._screenshot_element_impl(
                        css="div.ant-modal", screenshot_timeout_ms=250
                    )
            finally:
                for item in reversed(patches):
                    item.stop()

        self.assertIn("复位", str(ctx.exception))
        capture.assert_awaited_once()
        restore.assert_awaited_once()
        kwargs = restore.await_args.kwargs
        self.assertEqual(kwargs["clip_size"], (900.0, 383.0))
        self.assertEqual(kwargs["expected_size"], (1920.0, 1000.0))
        self.assertEqual(kwargs["restore_bounds"], {"windowState": "minimized", "width": 1390})

    async def test_success_returns_viewport_guard_report(self) -> None:
        page = MagicMock()
        page.main_frame = object()
        page.screenshot = AsyncMock(return_value=b"png-bytes")
        capture = AsyncMock(return_value={"windowState": "maximized"})
        restore = AsyncMock(
            return_value={
                "clip_lock_detected": False,
                "window_state": "maximized",
                "viewport": {"width": 1920, "height": 1000},
                "attempts": 1,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            patches = self._patch_common(page)
            patches += [
                patch.object(snapshot, "artifact_file", lambda *a, **kw: Path(tmp) / "shot.png"),
                patch.object(snapshot, "_capture_window_bounds", capture),
                patch.object(snapshot, "_restore_window_and_viewport", restore),
            ]
            for item in patches:
                item.start()
            try:
                result = await snapshot._screenshot_element_impl(css="div.ant-modal")
            finally:
                for item in reversed(patches):
                    item.stop()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["byte_size"], len(b"png-bytes"))
        guard = result["viewport_guard"]
        self.assertTrue(guard["restored"])
        self.assertEqual(guard["viewport"], {"width": 1920, "height": 1000})

    async def test_locked_viewport_is_reported_as_not_restored(self) -> None:
        page = MagicMock()
        page.main_frame = object()
        page.screenshot = AsyncMock(return_value=b"png-bytes")
        restore = AsyncMock(
            return_value={
                "clip_lock_detected": True,
                "window_state": "maximized",
                "viewport": {"width": 900, "height": 383},
                "attempts": 2,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            patches = self._patch_common(page)
            patches += [
                patch.object(snapshot, "artifact_file", lambda *a, **kw: Path(tmp) / "shot.png"),
                patch.object(snapshot, "_capture_window_bounds", AsyncMock(return_value=None)),
                patch.object(snapshot, "_restore_window_and_viewport", restore),
            ]
            for item in patches:
                item.start()
            try:
                result = await snapshot._screenshot_element_impl(css="div.ant-modal")
            finally:
                for item in reversed(patches):
                    item.stop()

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["viewport_guard"]["restored"])
