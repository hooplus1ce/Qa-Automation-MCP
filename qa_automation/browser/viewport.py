"""Window/viewport repair: clip-lock detection, emulation-residue recovery, maximized fill.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

logger = logging.getLogger("qa_automation.browser")


async def _page_pixel_ratio(page: Page) -> dict[str, float]:
    """读取内容栅格比例:1 DOM CSS 像素对应多少物理像素。

    真机 APS 复现的缺陷(浏览器缩放过 100%):

    * ``page.screenshot(clip=...)`` 的出图画布按**显示器缩放系数**放大
      (实测 2.2),而页面内容按 **devicePixelRatio** 栅格化(实测 1.76)。
      两者只在页面缩放为 100% 时相等,不等时画布右侧/底部会多出
      ``1 - dpr/画布系数`` 的空白(实测 20%),元素在图片里的位置也比 DOM 坐标小。
    * **不要用 ``visualViewport.zoom`` 去推算**:同一台机器上它在 0.8 与 1.0 之间
      反复横跳(Chrome 的 per-origin zoom 与 CDP 的 visual viewport zoom 不是一回事),
      但几何完全没变。可靠的做法是拿真实截图回读画布尺寸自校验,
      见 ``interaction.snapshot._capture_correction``。

    Returns:
        {"dpr": 设备像素比(内容栅格比例), "zoom_hint": visualViewport.zoom(仅供参考),
         "vw"/"vh": 同一时刻的 innerWidth/innerHeight(供调用方做缓存键)}
        取不到时退化为 1.0,不会把调用方带崩。
    """
    fallback = {"dpr": 1.0, "zoom_hint": 1.0, "vw": 0.0, "vh": 0.0}
    try:
        raw = await page.evaluate(
            "() => ({"
            "  zoom: (window.visualViewport && window.visualViewport.zoom) || 1,"
            "  dpr: window.devicePixelRatio || 1,"
            "  vw: Number(window.innerWidth) || 0,"
            "  vh: Number(window.innerHeight) || 0"
            "})"
        )
    except Exception:
        return fallback
    try:
        dpr = float(raw["dpr"])
        zoom_hint = float(raw["zoom"])
    except Exception:
        return fallback
    if not math.isfinite(dpr) or not 0.1 <= dpr <= 10:
        dpr = 1.0
    if not math.isfinite(zoom_hint) or not 0.1 <= zoom_hint <= 10:
        zoom_hint = 1.0
    vw = float(raw.get("vw") or 0.0)
    vh = float(raw.get("vh") or 0.0)
    return {
        "dpr": dpr,
        "zoom_hint": zoom_hint,
        "vw": vw if math.isfinite(vw) and vw > 0 else 0.0,
        "vh": vh if math.isfinite(vh) and vh > 0 else 0.0,
    }


_CLIP_LOCK_TOLERANCE_PX = 1.0


async def _read_viewport_or_none(page: Page) -> dict[str, float] | None:
    """读取 window.innerWidth/innerHeight;取不到或非正数时返回 None(不抛错)。

    与 _page_viewport_size 的区别:后者在视口不可用时会抛 ValueError,用于业务坐标
    换算;这里只服务于复位校验,失败必须降级成 None 而不是打断调用方。
    """
    try:
        size = await page.evaluate(
            "() => ({w: Number(window.innerWidth), h: Number(window.innerHeight)})"
        )
    except Exception:
        return None
    try:
        width = float(size["w"])
        height = float(size["h"])
    except Exception:
        return None
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        return None
    return {"w": width, "h": height}


async def _cdp_window_bounds(cdp: Any) -> tuple[int | None, dict[str, Any] | None]:
    """读出该 page 所属窗口的 windowId 与 bounds;任一步失败都返回 (None, None)。"""
    try:
        target_info = await cdp.send("Target.getTargetInfo")
        target_id = (target_info or {}).get("targetInfo", {}).get("targetId")
        if not target_id:
            return None, None
        window = await cdp.send("Browser.getWindowForTarget", {"targetId": target_id})
    except Exception:
        return None, None
    window_id = (window or {}).get("windowId")
    if window_id is None:
        return None, None
    bounds = (window or {}).get("bounds")
    return int(window_id), dict(bounds) if isinstance(bounds, dict) else None


def _same_size(
    measured: dict[str, float] | None,
    other: tuple[float, float] | None,
    tolerance: float = _CLIP_LOCK_TOLERANCE_PX,
) -> bool:
    """两个尺寸是否在 Chromium 回读舍入容差内相等。"""
    if not measured or not other:
        return False
    try:
        width = float(other[0])
        height = float(other[1])
    except Exception:
        return False
    return (
        abs(measured["w"] - width) <= tolerance
        and abs(measured["h"] - height) <= tolerance
    )


def _size_candidates(clip_size: Any) -> list[tuple[float, float]]:
    """把 clip_size 归一成候选尺寸列表。

    截图在浏览器缩放过 100% 时会有两套尺寸:DOM CSS 空间(视口回读所在的
    空间)与裁剪框空间(已乘 zoom)。两者都可能是 emulation 残留时视口被锁成
    的值,所以复位校验要同时认这两套,否则会漏判。
    """
    if not clip_size:
        return []
    if isinstance(clip_size[0], (list, tuple)):
        return [tuple(item) for item in clip_size if item]
    return [tuple(clip_size)]


def _looks_like_clip_lock(
    measured: dict[str, float] | None,
    clip_size: Any,
    expected_size: tuple[float, float] | None = None,
) -> bool:
    """视口是否仍等于「刚才那次截图的 clip 尺寸」——命中即说明 emulation 残留没清掉。

    必须传 expected_size(截图前的视口尺寸):全视口截图的 clip 本来就等于自然视口,
    只看 clip 会把正常结果误判成残留,并白白多跑一轮复位。
    clip_size 可以是单个 (w, h),也可以是候选尺寸列表。
    """
    if not measured:
        return False
    candidates = _size_candidates(clip_size)
    if not candidates:
        return False
    matched = any(_same_size(measured, item) for item in candidates)
    if not matched:
        return False
    for item in candidates:
        try:
            if float(item[0]) <= 0 or float(item[1]) <= 0:
                return False
        except Exception:
            return False
    return not _same_size(measured, expected_size)


async def _capture_window_bounds(page: Page) -> dict[str, Any] | None:
    """截图前留档窗口 bounds(含 windowState),供异常中断后原样还原。"""
    if page is None or not hasattr(page, "context"):
        return None
    try:
        cdp = await page.context.new_cdp_session(page)
    except Exception:
        return None
    try:
        _window_id, bounds = await _cdp_window_bounds(cdp)
        return bounds
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


async def _relayout_after_viewport_change(page: Page) -> None:
    """向顶层与全部 iframe 广播 resize,并顺带唤醒 VTable 自适应布局。"""
    relayout_js = """() => {
        try { window.dispatchEvent(new Event('resize')); } catch (e) {}
        if (window._vtable && typeof window._vtable.resize === 'function') {
            try { window._vtable.resize(); } catch (e) {}
        }
    }"""
    try:
        await page.evaluate(relayout_js)
    except Exception:
        pass
    for frame in getattr(page, "frames", []):
        if frame == getattr(page, "main_frame", None):
            continue
        try:
            await frame.evaluate(relayout_js)
        except Exception:
            pass


async def _restore_window_and_viewport(
    page: Page,
    *,
    restore_bounds: dict[str, Any] | None = None,
    clip_size: Any = None,
    expected_size: tuple[float, float] | None = None,
    attempts: int = 2,
) -> dict[str, Any]:
    """把窗口 + 视口从「窗口被最小化 / 截图临时 emulation 残留」里救回来。

    真机 APS 复现的两条动因(必须同时处理,缺一不可):

    1. 截图 clip 的 emulation 残留。Playwright 的 page.screenshot(clip=...) 会让
       Chromium 临时下发 Emulation.setDeviceMetricsOverride 把视口撑到 clip 尺寸;
       正常结束 Chromium 会自行还原,但该次截图一旦在默认 3s 超时处被中断
       (渲染器卡住 / 窗口最小化),override 就残留在 RenderWidgetHost 上,
       页面视口被锁成元素尺寸(实测 900x383、715x270),此后 vtable / 浮层 / 点击
       坐标全部错位。

    2. 最小化窗口无法直接最大化。Chromium 不接受 minimized -> maximized 的直接
       跳变,只发 maximized 是 no-op,必须 minimized -> normal -> maximized。
       窗口停在最小化时 innerWidth/innerHeight 也是不可信值。

    因此这里显式补 normal 中转,并在清理 emulation 后用 innerWidth/innerHeight
    回读校验:一旦发现视口仍等于 clip_size,判定复位失败并重试一轮。

    Args:
        restore_bounds: 截图前留档的窗口 bounds,normal 中转时用于避免窗口跳位
        clip_size: 本次截图的裁剪框尺寸,用于识别 emulation 残留。可以是单个
            (width, height),也可以是候选列表——浏览器缩放过 100% 时 DOM CSS 空间
            与裁剪框空间尺寸不同,两套都要认
        expected_size: 截图前的视口尺寸;与 clip_size 相等时不再判为残留
            (全视口截图本就如此,否则会误报)
        attempts: 最大尝试轮数(默认 2)

    Returns:
        {"attempts", "window_state", "viewport", "clip_lock_detected"}
    """
    report: dict[str, Any] = {
        "attempts": 0,
        "window_state": None,
        "viewport": None,
        "clip_lock_detected": False,
    }
    if page is None:
        return report
    try:
        if page.is_closed():
            return report
    except Exception:
        return report
    try:
        if not hasattr(page, "context") or not hasattr(page.context, "new_cdp_session"):
            return report
    except Exception:
        return report

    measured: dict[str, float] | None = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        report["attempts"] = attempt
        try:
            cdp = await page.context.new_cdp_session(page)
        except Exception:
            break
        try:
            window_id, bounds = await _cdp_window_bounds(cdp)
            state = (bounds or {}).get("windowState")

            # minimized -> normal -> maximized:直接跳 maximized 在 Chromium 上是 no-op
            if window_id is not None and state == "minimized":
                normal_bounds: dict[str, Any] = {"windowState": "normal"}
                for key in ("left", "top", "width", "height"):
                    if restore_bounds and restore_bounds.get(key) is not None:
                        normal_bounds[key] = restore_bounds[key]
                try:
                    await cdp.send(
                        "Browser.setWindowBounds",
                        {"windowId": window_id, "bounds": normal_bounds},
                    )
                    await asyncio.sleep(0.05)
                    state = "normal"
                except Exception:
                    pass

            if window_id is not None and state != "maximized":
                try:
                    await cdp.send(
                        "Browser.setWindowBounds",
                        {"windowId": window_id, "bounds": {"windowState": "maximized"}},
                    )
                    state = "maximized"
                except Exception:
                    pass
            report["window_state"] = state

            # Chromium EmulationHandler::ClearDeviceMetricsOverride 是 session-scoped 的。
            # 若直接调 clearDeviceMetricsOverride，在未设置 override 的新 session 中是 no-op。
            # 必须先调用 setDeviceMetricsOverride(width=0, height=0) 将 RenderWidgetHost
            # 的尺寸重置回宿主窗口自然尺寸，再调用 clearDeviceMetricsOverride 彻底抹除。
            try:
                await cdp.send(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 0,
                        "height": 0,
                        "deviceScaleFactor": 0,
                        "mobile": False,
                    },
                )
            except Exception:
                pass
            try:
                await cdp.send("Emulation.clearDeviceMetricsOverride")
            except Exception:
                pass
        finally:
            try:
                await cdp.detach()
            except Exception:
                pass

        measured = await _read_viewport_or_none(page)
        if measured and getattr(page, "viewport_size", None) is not None:
            # 只在 Playwright 自己托管视口时回写,并重新采样(override 可能刚被抹掉)
            try:
                await page.set_viewport_size(
                    {"width": int(measured["w"]), "height": int(measured["h"])}
                )
            except Exception:
                pass
            measured = await _read_viewport_or_none(page)
        report["viewport"] = (
            {"width": int(measured["w"]), "height": int(measured["h"])} if measured else None
        )
        if not _looks_like_clip_lock(measured, clip_size, expected_size):
            break
        if attempt < attempts:
            await asyncio.sleep(0.15)

    report["clip_lock_detected"] = _looks_like_clip_lock(
        measured, clip_size, expected_size
    )
    await _relayout_after_viewport_change(page)
    return report


async def _maximize_and_fill_viewport(page: Page) -> dict[str, Any]:
    """Maximize the browser window, clear emulated device metrics, and trigger relayout.

    Returns:
        复位报告,见 :func:`_restore_window_and_viewport`。
    """
    return await _restore_window_and_viewport(page)
