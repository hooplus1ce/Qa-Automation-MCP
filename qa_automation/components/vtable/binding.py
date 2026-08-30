"""VTable instance binding, frame resolution, and coordinate calculations."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ...browser import (
    _action_lock,
    _current_page_impl,
    _frame_details,
    _frame_page_offset,
    _page_id,
)
from ...config import (
    ACTIVE_IFRAME_SELECTOR,
    BIND_TIMEOUT_MS,
    SCROLL_WAIT_RAF,
)
from .scripts import (
    BIND_BFS_FALLBACK,
    CELL_RELATIVE_LOC,
    FAST_BIND,
    IS_CELL_VISIBLE,
    RESOLVE_CELL,
    WAIT_RENDER,
)


def _wrap(script: str) -> str:
    """无参脚本:() => { ... }。Playwright 识别为箭头函数表达式并自动调用。"""
    return f"() => {{ {script} }}"


def _wrap2(script: str) -> str:
    """Adapt a two-argument component script for Playwright evaluation."""
    return f"([c, r]) => {{ {script.replace('(arguments[0], arguments[1])', '(c, r)')} }}"


def _wrap4(script: str) -> str:
    """四参脚本(批量读值):(arguments[0..3]) 改写为 ([a, b, c, d]) => { ... }。"""
    return (
        f"([a, b, c, d]) => {{ "
        f"{script.replace('(arguments[0], arguments[1], arguments[2], arguments[3])', '(a, b, c, d)')} }}"
    )

async def active_application_frame(page: Page) -> Frame | None:
    if ACTIVE_IFRAME_SELECTOR is None:
        return None
    try:
        container = page.locator(ACTIVE_IFRAME_SELECTOR).first
        if await container.count():
            handle = await container.element_handle()
            if handle is not None:
                content = await handle.content_frame()
                if content is not None:
                    return content
    except Exception:
        pass
    return None


async def vtable_frame(page: Page) -> Frame:
    active = await active_application_frame(page)
    if active is not None:
        try:
            if await active.locator(".vtable").count():
                return active
        except Exception:
            pass
    for frame in list(page.frames):
        try:
            if await frame.locator(".vtable").count():
                return frame
        except Exception:
            continue
    if active is not None:
        return active
    return page.main_frame


async def resolve_frame(page: Page, frame: str | None) -> Frame:
    if frame is None:
        return page.main_frame
    if frame == "vtable":
        return await vtable_frame(page)
    if frame in {"active", "application"}:
        active = await active_application_frame(page)
        if active is not None:
            return active
    for fr in list(page.frames):
        if fr.name == frame:
            return fr
    for fr in list(page.frames):
        if fr.name and frame in fr.name:
            return fr
    for fr in list(page.frames):
        if frame in fr.url:
            return fr
    raise ValueError(f"未找到目标 frame: {frame!r}。可用 frame: {[f.name for f in page.frames]}")


async def ensure_vtable(frame: Frame, table_index: int | None = None) -> None:
    await frame.wait_for_selector(".vtable", timeout=BIND_TIMEOUT_MS)
    await frame.evaluate(
        """(targetIndex) => {
          if (Number.isInteger(targetIndex)) window.__vtable_target_index = targetIndex;
          else delete window.__vtable_target_index;
          delete window._vtable;
        }""",
        table_index,
    )
    if not await frame.evaluate(_wrap(FAST_BIND)):
        await frame.evaluate(_wrap(BIND_BFS_FALLBACK))
    await frame.wait_for_function("() => !!window._vtable", timeout=BIND_TIMEOUT_MS)


async def _vtable_directory(page: Page, frame: Frame) -> list[dict[str, Any]]:
    payload = await frame.evaluate(
        """() => {
          const roots = Array.from(document.querySelectorAll('.vtable'));
          return roots.map((root, index) => {
            const canvas = root.querySelector('canvas');
            const rect = (canvas || root).getBoundingClientRect();
            const modal = root.closest('.ant-modal[role="document"], .ant-modal-wrap[role="dialog"]');
            const style = window.getComputedStyle(root);
            const visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            return {
              table_index: index,
              table_id: `vtable-${index}`,
              context: modal ? 'modal' : 'page',
              canvas_box: {
                x: Math.round(rect.x * 100) / 100,
                y: Math.round(rect.y * 100) / 100,
                width: Math.round(rect.width * 100) / 100,
                height: Math.round(rect.height * 100) / 100,
              },
              visible,
            };
          });
        }"""
    )
    offset = await _frame_page_offset(page, frame)
    directory = []
    for item in payload:
        box = item["canvas_box"]
        directory.append(
            {
                **item,
                "page_box": {
                    "x": round(box["x"] + offset["x"], 2),
                    "y": round(box["y"] + offset["y"], 2),
                    "width": box["width"],
                    "height": box["height"],
                },
            }
        )
    return directory


async def cell_offset(frame: Frame) -> dict[str, float]:
    offset = await frame.evaluate(
        "() => { const t = window._vtable;"
        " const el = (t && t.canvas) || document.querySelector('.vtable canvas') || document.querySelector('.vtable');"
        " const r = el.getBoundingClientRect();"
        " return { left: r.left, top: r.top }; }"
    )
    return offset or {"left": 0.0, "top": 0.0}


def _cell_visible_js(col: int, row: int) -> str:
    body = IS_CELL_VISIBLE.replace("{col}", str(col)).replace("{row}", str(row)).strip()
    return f"() => {{ return {body} }}"


async def cell_center(page: Page, frame: Frame, col: int, row: int) -> dict[str, float] | None:
    rel = await frame.evaluate(_wrap2(CELL_RELATIVE_LOC), [col, row])
    if not rel:
        return None
    raw_x = (rel.get("left", 0) + rel.get("right", 0)) / 2
    raw_y = (rel.get("top", 0) + rel.get("bottom", 0)) / 2
    offset = await cell_offset(frame)
    x = offset["left"] + raw_x
    y = offset["top"] + raw_y
    if frame != page.main_frame:
        frame_offset = await _frame_page_offset(page, frame)
        x += frame_offset["x"]
        y += frame_offset["y"]
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return {"x": x, "y": y}


async def cell_visible(frame: Frame, col: int, row: int) -> bool:
    return bool(await frame.evaluate(_cell_visible_js(col, row)))


async def ensure_cell_visible(page: Page, frame: Frame, col: int, row: int) -> bool:
    if await cell_visible(frame, col, row):
        return True
    await frame.evaluate(
        f"() => {{ const t = window._vtable; "
        f"if (t && t.scrollToCell) t.scrollToCell({{col: {col}, row: {row}}}); return true; }}"
    )
    for _ in range(SCROLL_WAIT_RAF):
        await frame.evaluate(_wrap(WAIT_RENDER))
    return await cell_visible(frame, col, row)


async def _resolve_vtable_cell_impl(
    field: str, record_index: int | list[int]
) -> dict:
    """Resolve a semantic VTable target without reading canvas DOM geometry."""
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-not-bound: {exc}",
            "field": field,
            "record_index": record_index,
        }
    try:
        resolved = await frame.evaluate(
            _wrap2(RESOLVE_CELL), [field, record_index]
        )
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-address-error: {exc}",
            "field": field,
            "record_index": record_index,
        }
    if not resolved or not resolved.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": (resolved or {}).get("reason", "vtable-address-unavailable"),
            "field": field,
            "record_index": record_index,
            "address": (resolved or {}).get("address"),
        }
    col, row = int(resolved["col"]), int(resolved["row"])
    in_viewport = await cell_visible(frame, col, row)
    center = await cell_center(page, frame, col, row) if in_viewport else None
    return {
        "status": "ok",
        "page_id": _page_id(page),
        "field": field,
        "record_index": record_index,
        "address": {"col": col, "row": row},
        "value": resolved.get("value"),
        "type": resolved.get("type"),
        "header_paths": resolved.get("headerPaths"),
        "resolved_by": resolved.get("method"),
        "in_viewport": in_viewport,
        "center": center,
        "frame": _frame_details(page, frame),
    }


async def resolve_vtable_cell(
    field: str, record_index: int | list[int]
) -> dict:
    async with _action_lock:
        return await _resolve_vtable_cell_impl(field, record_index)
