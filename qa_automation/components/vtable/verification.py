"""VTable visual verification via in-memory canvas slice and scenegraph states."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ...browser import _page_viewport_size
from ...config import VTABLE_VERIFICATION_STRATEGY
from .binding import _wrap2, cell_center
from .scripts import CELL_VISUAL_STATE

_CELL_CANVAS_SLICE_JS = r"""([col, row, size]) => {
  // 优先使用已绑定实例的 canvas:多表页面中 querySelector 第一个 canvas 可能属于另一张表
  const vt = window._vtable;
  let canvas = vt && vt.canvas ? vt.canvas : null;
  if (!canvas) {
    const el = document.querySelector('.vtable');
    canvas = el ? el.querySelector('canvas') : document.querySelector('canvas');
  }
  if (!canvas) return null;
  let localX = 0, localY = 0;
  if (vt && vt.getCellRelativeRect) {
    const r = vt.getCellRelativeRect(col, row);
    if (r) {
      localX = r.left !== undefined ? r.left : (r.bounds ? r.bounds.x1 : 0);
      localY = r.top !== undefined ? r.top : (r.bounds ? r.bounds.y1 : 0);
    }
  }
  try {
    const s = Math.max(20, Math.min(100, Number(size) || 40));
    // getCellRelativeRect 是 CSS 像素,位图坐标需按 devicePixelRatio 缩放,否则高 DPI 下取样错位
    const rect = canvas.getBoundingClientRect();
    const scale = rect.width > 0 ? (canvas.width / rect.width) : 1;
    const smallCanvas = document.createElement('canvas');
    smallCanvas.width = s;
    smallCanvas.height = s;
    const sctx = smallCanvas.getContext('2d');
    if (!sctx) return null;
    sctx.drawImage(
      canvas,
      Math.max(0, localX * scale),
      Math.max(0, localY * scale),
      s * scale,
      s * scale,
      0, 0, s, s
    );
    return smallCanvas.toDataURL('image/png');
  } catch (_) {
    return null;
  }
}"""


async def _cell_visual_state(frame: Frame, col: int, row: int) -> dict[str, Any] | None:
    try:
        state = await frame.evaluate(_wrap2(CELL_VISUAL_STATE), [col, row])
        return state if isinstance(state, dict) else None
    except Exception:
        return None


async def _cell_screenshot(
    page: Page,
    x: float,
    y: float,
    *,
    frame: Frame | None = None,
    col: int | None = None,
    row: int | None = None,
) -> dict[str, Any] | None:
    """Hash a visual snapshot of the cell region.

    Prefers in-memory canvas slice (0ms GPU flush, 0 flicker) when frame and col/row
    are provided; gracefully falls back to page.screenshot only if canvas is unavailable.
    """
    size = float(VTABLE_VERIFICATION_STRATEGY.screenshot_size)
    if frame is not None and col is not None and row is not None:
        try:
            slice_data = await frame.evaluate(_CELL_CANVAS_SLICE_JS, [col, row, size])
            if slice_data and isinstance(slice_data, str):
                digest = hashlib.sha256(slice_data.encode("utf-8")).hexdigest()[:16]
                return {
                    "digest": digest,
                    "clip": {"x": round(x, 2), "y": round(y, 2), "width": size, "height": size},
                }
        except Exception:
            pass
    try:
        viewport = await _page_viewport_size(page)
        left = max(0.0, min(float(x) - size / 2, viewport["width"] - size))
        top = max(0.0, min(float(y) - size / 2, viewport["height"] - size))
        image = await page.screenshot(clip={"x": left, "y": top, "width": size, "height": size})
        return {"digest": hashlib.sha256(image).hexdigest()[:16], "clip": {"x": round(left, 2), "y": round(top, 2), "width": size, "height": size}}
    except Exception:
        return None


async def _verify_landed(
    page: Page,
    frame: Frame,
    before_sel: Any,
    col: int,
    row: int,
    before_visual: dict[str, Any] | None,
    before_screenshot: dict[str, Any] | None,
) -> tuple[bool, dict]:
    try:
        after = await frame.evaluate(
            "() => { const t = window._vtable; return t ? {"
            " sel: t.getSelectedCellRanges(),"
            " editing: !!(t.editorManager && t.editorManager.editingEditor),"
            " targetSelected: (() => { const ranges = t.getSelectedCellRanges?.() || [];"
            " return ranges.some(range => { const start = range.start || range.startCell || range;"
            " const end = range.end || range.endCell || start;"
            " const minCol = Math.min(Number(start.col), Number(end.col));"
            " const maxCol = Math.max(Number(start.col), Number(end.col));"
            " const minRow = Math.min(Number(start.row), Number(end.row));"
            " const maxRow = Math.max(Number(start.row), Number(end.row));"
            f" return {col} >= minCol && {col} <= maxCol && {row} >= minRow && {row} <= maxRow;"
            " }); })()"
            " } : null; }"
        )
    except Exception:
        # 页面导航/实例销毁等导致回读失败:返回不可验证而非抛错
        return False, {"landed": False, "reason": "verification-unavailable"}
    if not after:
        return False, {"landed": False, "reason": "vtable-gone"}
    sel_changed = after["sel"] != before_sel
    editor_open = bool(after["editing"])
    target_selected = bool(after.get("targetSelected"))
    after_visual = await _cell_visual_state(frame, col, row)
    scenegraph_changed = bool(
        before_visual
        and after_visual
        and before_visual.get("signature") != after_visual.get("signature")
    )
    after_screenshot = None
    screenshot_changed = False
    if not (sel_changed or editor_open or target_selected or scenegraph_changed):
        after_center = await cell_center(page, frame, col, row)
        if after_center:
            after_screenshot = await _cell_screenshot(
                page, after_center["x"], after_center["y"], frame=frame, col=col, row=row
            )
        screenshot_changed = bool(
            before_screenshot
            and after_screenshot
            and before_screenshot.get("digest") != after_screenshot.get("digest")
        )
    landed = sel_changed or editor_open or target_selected or scenegraph_changed or screenshot_changed
    evidence: dict[str, Any] = {
        "landed": landed,
        "selection_changed": sel_changed,
        "editor_open": editor_open,
        "target_selected": target_selected,
        "scenegraph_changed": scenegraph_changed,
    }
    if before_visual or after_visual:
        evidence["scenegraph"] = {
            "before_paints": (before_visual or {}).get("paints", []),
            "after_paints": (after_visual or {}).get("paints", []),
        }
    if before_screenshot or after_screenshot:
        evidence["screenshot"] = {
            "before_digest": (before_screenshot or {}).get("digest"),
            "after_digest": (after_screenshot or {}).get("digest"),
            "changed": screenshot_changed,
        }
    return landed, evidence
