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
    if table_index is not None:
        if not isinstance(table_index, int) or table_index < 0:
            raise ValueError("table_index must be a non-negative integer")
        selected = await frame.evaluate(
            """(index) => {
              const root = document.querySelectorAll('.vtable')[index];
              if (!root) return {exists: false, visible: false};
              const style = getComputedStyle(root);
              const rect = root.getBoundingClientRect();
              return {
                exists: true,
                visible: style.display !== 'none' && style.visibility !== 'hidden' &&
                  rect.width > 0 && rect.height > 0,
              };
            }""",
            table_index,
        )
        if not selected.get("exists"):
            raise ValueError(f"unknown table_index: {table_index}")
        if not selected.get("visible"):
            raise ValueError(f"table_index is not visible: {table_index}")
    else:
        selection = await frame.evaluate(
            """() => Array.from(document.querySelectorAll('.vtable')).map((root, index) => {
              const style = getComputedStyle(root);
              const rect = root.getBoundingClientRect();
              const modal = root.closest('.ant-modal[role="document"], .ant-modal-wrap[role="dialog"]');
              return {
                index,
                visible: style.display !== 'none' && style.visibility !== 'hidden' &&
                  rect.width > 0 && rect.height > 0,
                modal: !!modal,
              };
            })"""
        )
        visible = [item for item in selection if item.get("visible")]
        if len(visible) > 1 and not any(item.get("modal") for item in visible):
            raise ValueError("multiple visible VTables; table_index is required")
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
            const visible = style.display !== 'none' && style.visibility !== 'hidden' &&
              rect.width > 0 && rect.height > 0;
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
        if not item.get("visible"):
            continue
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


async def _vtable_discover_impl(frame: str | None = None) -> dict:
    """List visible VTable roots in one frame or across all current-page frames."""
    page = await _current_page_impl()
    frames = [await resolve_frame(page, frame)] if frame is not None else list(page.frames)
    tables: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for candidate in frames:
        try:
            details = _frame_details(page, candidate)
            frame_selector = (
                details["frame_name"]
                or details["frame_url"]
                or (None if candidate == page.main_frame else "vtable")
            )
            for table in await _vtable_directory(page, candidate):
                tables.append(
                    {
                        "frame": frame_selector,
                        "frame_details": details,
                        "table": table,
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "frame_id": _frame_details(page, candidate)["frame_id"],
                    "reason": str(exc)[:200],
                }
            )
    result: dict[str, Any] = {
        "status": "ok",
        "page_id": _page_id(page),
        "tables": tables,
        "count": len(tables),
    }
    if errors:
        result["errors"] = errors
    return result


async def discover_vtables(frame: str | None = None) -> dict:
    async with _action_lock:
        return await _vtable_discover_impl(frame=frame)




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
    raw_x = rel.get("x") if "x" in rel else (rel.get("left", 0) + rel.get("right", 0)) / 2
    raw_y = rel.get("y") if "y" in rel else (rel.get("top", 0) + rel.get("bottom", 0)) / 2
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
        """([col, row]) => {
            const t = window._vtable;
            if (!t) return false;
            if (typeof t.scrollToCell === 'function') {
                t.scrollToCell(col, row);
                t.scrollToCell({col, row});
            }
            const rect = t.getCellRelativeRect ? t.getCellRelativeRect(col, row) : null;
            if (rect) {
                const pick = (a, b) => (a !== undefined && a !== null ? a : b);
                const left = pick(rect.left, pick(rect.x1, rect.bounds && rect.bounds.x1));
                const right = pick(rect.right, pick(rect.x2, rect.bounds && rect.bounds.x2));
                const canvasRect = t.canvas ? t.canvas.getBoundingClientRect() : {width: 1000};
                const cw = canvasRect.width;
                const rightFrozenW = (t.rightFrozenColCount && t.getRightFrozenColsWidth) ? t.getRightFrozenColsWidth() : 0;
                const frozenW = (t.frozenColCount && t.getFrozenColsWidth) ? t.getFrozenColsWidth() : 0;
                const isRightFrozen = col >= (t.colCount - (t.rightFrozenColCount || 0));
                const isLeftFrozen = col < (t.frozenColCount || 0);
                if (!isRightFrozen && !isLeftFrozen && left !== undefined && right !== undefined) {
                    const cx = (left + right) / 2;
                    if (cx > (cw - rightFrozenW)) {
                        const delta = cx - (cw - rightFrozenW) + 60;
                        if (t.setScrollLeft) t.setScrollLeft(t.scrollLeft + delta);
                        else t.scrollLeft += delta;
                    } else if (cx < frozenW) {
                        const delta = frozenW - cx + 60;
                        if (t.setScrollLeft) t.setScrollLeft(Math.max(0, t.scrollLeft - delta));
                        else t.scrollLeft = Math.max(0, t.scrollLeft - delta);
                    }
                }
            }
            if (t.render) t.render();
            return true;
        }""",
        [col, row],
    )
    for _ in range(SCROLL_WAIT_RAF):
        await frame.evaluate(_wrap(WAIT_RENDER))
    return await cell_visible(frame, col, row)

async def _resolve_vtable_cell_impl(
    field: str,
    record_index: int | list[int],
    *,
    frame: str | None = None,
    table_index: int | None = None,
) -> dict:
    """Resolve a semantic VTable target without reading canvas DOM geometry."""
    page = await _current_page_impl()
    frame_obj = (
        await resolve_frame(page, frame)
        if frame is not None
        else await vtable_frame(page)
    )
    try:
        await ensure_vtable(frame_obj, table_index)
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-not-bound: {exc}",
            "field": field,
            "record_index": record_index,
            "frame": _frame_details(page, frame_obj),
            "table_index": table_index,
        }
    try:
        resolved = await frame_obj.evaluate(
            _wrap2(RESOLVE_CELL), [field, record_index]
        )
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-address-error: {exc}",
            "field": field,
            "record_index": record_index,
            "table_index": table_index,
        }
    if not resolved or not resolved.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": (resolved or {}).get("reason", "vtable-address-unavailable"),
            "field": field,
            "record_index": record_index,
            "address": (resolved or {}).get("address"),
            "table_index": table_index,
        }
    col, row = int(resolved["col"]), int(resolved["row"])
    in_viewport = await cell_visible(frame_obj, col, row)
    center = cell_center(page, frame_obj, col, row) if in_viewport else None
    if center is not None:
        center = await center
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
        "frame": _frame_details(page, frame_obj),
        "table_index": table_index,
    }


async def resolve_vtable_cell(
    field: str,
    record_index: int | list[int],
    *,
    frame: str | None = None,
    table_index: int | None = None,
) -> dict:
    async with _action_lock:
        return await _resolve_vtable_cell_impl(
            field,
            record_index,
            frame=frame,
            table_index=table_index,
        )


