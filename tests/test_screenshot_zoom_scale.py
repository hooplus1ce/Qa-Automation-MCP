"""截图缩放(页面 zoom ≠ 100%)回归测试。

真机 APS 复现的缺陷:出图画布按**显示器缩放系数**(实测 2.2)放大,而页面内容按
**devicePixelRatio**(实测 1.76)栅格化,两者不等:

* 传 clip=(0,0,1637,928) 会拿到 3601x2042 的图,页面只占左上 80%,
  右侧与底部各多一条 20% 的空白;
* 同一个 DOM 元素在图片里的位置比 DOM 坐标小 1/0.8 = 1.25 倍,
  拿截图去校准坐标会整体跑偏。

修复:截图后用真实出图尺寸回读自校验,再把裁剪框按实测系数重拍一次,
使画布恰好等于 ``clip(DOM CSS) × dpr``。**不依赖 visualViewport.zoom**
——实测它在 0.8/1.0 之间横跳而几何毫无变化。

这里用假 page 覆盖换算、重拍、响应字段与复位护栏,不依赖真实浏览器。
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import qa_automation.browser as browser
from qa_automation.interaction import snapshot


def _png_bytes(width: int, height: int) -> bytes:
    """构造只够 _image_size 读头的假 PNG。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def _jpeg_bytes(width: int, height: int) -> bytes:
    """构造只够 _image_size 读 SOF0 的假 JPEG。"""
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )


class FakeChromiumPage:
    """模拟 Chromium 的 clip 出图:画布 = clip × canvas_factor。

    内容栅格始终是 dpr,所以 canvas_factor > dpr 时画布就会带空白边。
    """

    def __init__(
        self,
        *,
        dpr: float = 1.76,
        canvas_factor: float = 2.2,
        zoom_hint: float = 1.0,
        viewport=(1637.0, 929.0),
    ) -> None:
        self.dpr = dpr
        self.canvas_factor = canvas_factor
        self.zoom_hint = zoom_hint
        self.viewport = viewport
        self.evaluate = AsyncMock(side_effect=self._evaluate)
        self.main_frame = object()
        self.shot_clips: list[dict] = []
        self.screenshot = AsyncMock(side_effect=self._screenshot)

    async def _evaluate(self, expression: str):
        if "visualViewport" in expression:
            return {
                "zoom": self.zoom_hint,
                "dpr": self.dpr,
                "vw": self.viewport[0],
                "vh": self.viewport[1],
            }
        if "innerWidth" in expression:
            return {"w": self.viewport[0], "h": self.viewport[1]}
        return None

    async def _screenshot(self, **kwargs):
        clip = kwargs["clip"]
        self.shot_clips.append(dict(clip))
        return _png_bytes(
            round(clip["width"] * self.canvas_factor),
            round(clip["height"] * self.canvas_factor),
        )


class PagePixelRatioTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_dpr_and_viewport(self) -> None:
        ratios = await browser._page_pixel_ratio(FakeChromiumPage(dpr=1.76))

        self.assertAlmostEqual(ratios["dpr"], 1.76)
        self.assertAlmostEqual(ratios["vw"], 1637.0)
        self.assertAlmostEqual(ratios["vh"], 929.0)

    async def test_falls_back_when_evaluate_fails(self) -> None:
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("detached"))

        ratios = await browser._page_pixel_ratio(page)

        self.assertEqual(ratios["dpr"], 1.0)
        self.assertEqual(ratios["vw"], 0.0)


class ImageSizeTest(unittest.TestCase):
    def test_reads_png_header(self) -> None:
        self.assertEqual(
            snapshot._image_size(_png_bytes(2881, 1633)), {"width": 2881, "height": 1633}
        )

    def test_reads_jpeg_sof0(self) -> None:
        self.assertEqual(snapshot._image_size(_jpeg_bytes(800, 600)), {"width": 800, "height": 600})

    def test_garbage_returns_none_instead_of_raising(self) -> None:
        self.assertIsNone(snapshot._image_size(b"not-an-image"))
        self.assertIsNone(snapshot._image_size(b""))


class CaptureCorrectionTest(unittest.TestCase):
    def test_no_correction_when_canvas_matches_device_pixels(self) -> None:
        clip = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 50.0}
        # 画布 = 100 × 1.76 = 176,正好等于 clip × dpr
        factor = snapshot._capture_correction({"width": 176, "height": 88}, clip, 1.76)
        self.assertAlmostEqual(factor, 1.0, places=6)

    def test_returns_padding_factor_when_canvas_is_bloated(self) -> None:
        clip = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 50.0}
        # 画布 = 100 × 2.2 = 220,需要把裁剪框缩到 0.8 倍
        factor = snapshot._capture_correction({"width": 220, "height": 110}, clip, 1.76)
        self.assertAlmostEqual(factor, 0.8, places=6)

    def test_unreadable_size_yields_none(self) -> None:
        clip = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 50.0}
        self.assertIsNone(snapshot._capture_correction(None, clip, 1.76))


class ClipLockWithMultipleCandidatesTest(unittest.TestCase):
    def test_either_space_counts_as_a_lock(self) -> None:
        # DOM CSS 空间 1637x929、裁剪框空间 1310x743,两套都可能是残留值
        candidates = [(1310.0, 743.0), (1637.0, 929.0)]
        self.assertTrue(
            browser._looks_like_clip_lock({"w": 1637.0, "h": 929.0}, candidates, (1920.0, 1000.0))
        )
        self.assertTrue(
            browser._looks_like_clip_lock({"w": 1310.0, "h": 743.0}, candidates, (1920.0, 1000.0))
        )
        self.assertFalse(
            browser._looks_like_clip_lock({"w": 1920.0, "h": 1000.0}, candidates, (1920.0, 1000.0))
        )


class ScreenshotScaleCorrectionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        snapshot._CAPTURE_CORRECTION_CACHE.clear()

    def _patch(self, page, tmp, restore=None):
        target = SimpleNamespace(
            wait_for=AsyncMock(return_value=None),
            bounding_box=AsyncMock(
                return_value={"x": 100.0, "y": 200.0, "width": 900.0, "height": 383.0}
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
            patch.object(snapshot, "artifact_file", lambda *a, **kw: Path(tmp) / "shot.png"),
            patch.object(snapshot, "_capture_window_bounds", AsyncMock(return_value=None)),
            patch.object(
                snapshot,
                "_restore_window_and_viewport",
                restore
                or AsyncMock(
                    return_value={"clip_lock_detected": False, "window_state": "maximized"}
                ),
            ),
        ]

    async def _run(self, page, restore=None):
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._patch(page, tmp, restore)
            for item in patches:
                item.start()
            try:
                return await snapshot._screenshot_element_impl(css="div.ant-modal")
            finally:
                for item in reversed(patches):
                    item.stop()

    async def test_bloated_canvas_is_retaken_at_device_scale(self) -> None:
        page = FakeChromiumPage(dpr=1.76, canvas_factor=2.2)

        result = await self._run(page)

        # 第一次用 DOM 坐标(900x383 → 画布 1980x843),第二次按 0.8 收缩
        self.assertEqual(len(page.shot_clips), 2)
        first, second = page.shot_clips
        self.assertAlmostEqual(first["width"], 900.0)
        self.assertAlmostEqual(second["width"], 720.0, places=4)
        self.assertAlmostEqual(second["height"], 306.4, places=4)
        # 最终出图正好是 clip(DOM 900x383) × dpr
        self.assertEqual(result["image_size"], {"width": 1584, "height": 674})
        self.assertAlmostEqual(result["image_scale"]["px_per_css_px"], 1.76, places=4)
        self.assertTrue(result["image_scale"]["matches_device_pixels"])

    async def test_dom_clip_is_reported_to_caller(self) -> None:
        page = FakeChromiumPage(dpr=1.76, canvas_factor=2.2)

        result = await self._run(page)

        # 对调用方仍然汇报 DOM CSS 空间的裁剪框,免得使用方自己换算
        self.assertEqual(
            result["clip"], {"x": 100.0, "y": 200.0, "width": 900.0, "height": 383.0}
        )

    async def test_no_padding_means_single_capture(self) -> None:
        # 画布系数与 dpr 相等(未缩放的普通环境)时不应多拍一次
        page = FakeChromiumPage(dpr=2.2, canvas_factor=2.2)

        result = await self._run(page)

        self.assertEqual(len(page.shot_clips), 1)
        self.assertAlmostEqual(page.shot_clips[0]["width"], 900.0)
        self.assertEqual(result["image_size"], {"width": 1980, "height": 843})

    async def test_correction_is_cached_for_next_call(self) -> None:
        page = FakeChromiumPage(dpr=1.76, canvas_factor=2.2)

        await self._run(page)
        page.shot_clips.clear()
        await self._run(page)

        # 第二次直接命中缓存系数,只拍一次
        self.assertEqual(len(page.shot_clips), 1)
        self.assertAlmostEqual(page.shot_clips[0]["width"], 720.0, places=4)

    async def test_guard_receives_both_coordinate_spaces(self) -> None:
        page = FakeChromiumPage(dpr=1.76, canvas_factor=2.2)
        restore = AsyncMock(
            return_value={"clip_lock_detected": False, "window_state": "maximized"}
        )

        await self._run(page, restore=restore)

        sizes = restore.await_args.kwargs["clip_size"]
        self.assertEqual(len(sizes), 2)
        expected = [(900.0, 383.0), (720.0, 306.4)]
        for got, want in zip(sizes, expected, strict=True):
            self.assertAlmostEqual(got[0], want[0], places=4)
            self.assertAlmostEqual(got[1], want[1], places=4)

    async def test_failed_retake_keeps_first_image(self) -> None:
        page = FakeChromiumPage(dpr=1.76, canvas_factor=2.2)
        original = page.screenshot.side_effect

        async def flaky(**kwargs):
            if len(page.shot_clips) >= 1:
                page.shot_clips.append(dict(kwargs["clip"]))
                raise RuntimeError("renderer busy")
            return await original(**kwargs)

        page.shot_clips.clear()
        page.screenshot = AsyncMock(side_effect=flaky)

        result = await self._run(page)

        # 首图保留,状态仍然是 ok,只是如实反映未对齐
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["image_size"], {"width": 1980, "height": 843})
        self.assertFalse(result["image_scale"]["matches_device_pixels"])

    async def test_unparsable_image_still_returns_ok(self) -> None:
        page = FakeChromiumPage(dpr=1.76, canvas_factor=2.2)
        page.screenshot = AsyncMock(return_value=b"not-an-image")

        result = await self._run(page)

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("image_scale", result)


if __name__ == "__main__":
    unittest.main()
