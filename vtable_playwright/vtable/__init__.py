"""VTable domain tools, cell interactions, metadata, and data reading."""

from __future__ import annotations

import asyncio
import json
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from vtable_js import (
    CLASSIFY_CELL,
    READ_CELLS,
    TABLE_META,
    WAIT_RENDER,
)
from ..browser import (
    _action_lock,
    _current_page_impl,
    _frame_context_details,
    _page_id,
    _page_viewport_size,
)
from ..config import (
    OVERLAY_RESULT_LIMIT,
    OVERLAY_SETTLE_LIMIT_MS,
    VTABLE_SHOW_CURSOR,
    VTABLE_VERIFICATION_STRATEGY,
)
from ..mouse import _ensure_cursor_helper, _smooth_mouse_move_to
from .binding import (
    _wrap,
    _wrap2,
    _wrap4,
    cell_center,
    ensure_cell_visible,
    ensure_vtable,
    _resolve_vtable_cell_impl,
    resolve_vtable_cell,
    vtable_frame,
)
from .verification import (
    _cell_screenshot,
    _cell_visual_state,
    _verify_landed,
)


async def _trusted_viewport_click(
    page: Page,
    x: float,
    y: float,
    *,
    double_click: bool = False,
    button: str = "left",
) -> dict:
    try:
        x, y = float(x), float(y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite numbers")
        if button not in {"left", "middle", "right"}:
            raise ValueError("button must be left, middle or right")
        viewport = await _page_viewport_size(page)
        if not (0 <= x < viewport["width"] and 0 <= y < viewport["height"]):
            raise ValueError(
                f"point ({x:g}, {y:g}) is outside viewport "
                f"({viewport['width']:g} x {viewport['height']:g})"
            )
        await _ensure_cursor_helper(page)
        await _smooth_mouse_move_to(page, x, y)
        if VTABLE_SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__vtable_update_cursor && window.__vtable_update_cursor({x:.1f}, {y:.1f}, true, true)"
                )
            except Exception:
                pass
        if double_click:
            await page.mouse.dblclick(x, y, button=button)
        else:
            await page.mouse.click(x, y, button=button)
        if VTABLE_SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__vtable_update_cursor && window.__vtable_update_cursor({x:.1f}, {y:.1f}, false, false)"
                )
            except Exception:
                pass
        return {
            "status": "ok",
            "point": {"x": x, "y": y},
            "button": button,
            "double_click": double_click,
            "coordinate_space": "top-page-viewport-css-pixels",
        }
    except Exception as e:
        return {"status": "failed", "reason": f"click-error: {e}"}


async def _do_click(page: Page, frame: Frame, x: float, y: float, *, double_click: bool, button: str) -> dict:
    result = await _trusted_viewport_click(
        page, x, y, double_click=double_click, button=button
    )
    if result["status"] == "failed":
        return result
    try:
        await frame.evaluate(_wrap(WAIT_RENDER))
        return result
    except Exception as e:
        return {"status": "failed", "reason": f"click-error: {e}"}


async def _click_cell_impl(
    col: int,
    row: int,
    *,
    double_click: bool = False,
    button: str = "left",
    verify: bool = True,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
    headless: bool = True,
) -> dict:
    if not 0 <= settle_ms <= OVERLAY_SETTLE_LIMIT_MS:
        raise ValueError(f"settle_ms must be between 0 and {OVERLAY_SETTLE_LIMIT_MS}")
    page = await _current_page_impl()
    installed = None
    frame_listener = None
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    if not await ensure_cell_visible(page, frame, col, row):
        return {
            "status": "failed",
            "reason": "cell-not-in-viewport-after-scroll",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    center = await cell_center(page, frame, col, row)
    if not center:
        return {
            "status": "failed",
            "reason": "cell-rect-unavailable",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    x, y = center["x"], center["y"]
    classify = await frame.evaluate(_wrap2(CLASSIFY_CELL), [col, row])
    before_sel = await frame.evaluate(
        "() => { const t = window._vtable; return t ? t.getSelectedCellRanges() : null; }"
    )
    before_visual = None
    before_screenshot = None
    if verify:
        before_visual = await _cell_visual_state(frame, col, row)
        before_screenshot = await _cell_screenshot(page, x, y, frame=frame, col=col, row=row)
    if observe_after:
        from ..overlay import (
            _acquire_overlay_frame_listener,
            _arm_overlay_init_script,
            _install_overlay_observers,
            _release_overlay_frame_listener,
            _stop_overlay_observers_best_effort,
        )
        try:
            frame_listener, _ = await _acquire_overlay_frame_listener(
                page, persistent=False
            )
            await _arm_overlay_init_script(
                page, settle_ms=settle_ms, persistent=False
            )
            installed = await _install_overlay_observers(page, reset=True)
            await frame_listener.wait_pending()
            frame_listener.take_buffers()
            installed["frame_listener"] = frame_listener
        except Exception as exc:
            cleanup_errors = await _stop_overlay_observers_best_effort(page)
            cleanup_errors.extend(
                await _release_overlay_frame_listener(
                    page, frame_listener, persistent=False
                )
            )
            return {
                "status": "failed",
                "reason": f"overlay-arm-error: {exc}",
                "page_id": _page_id(page),
                "col": col,
                "row": row,
                "observer_errors": cleanup_errors,
                "observer_cleanup_failed": bool(cleanup_errors),
            }

    response: dict[str, Any]
    try:
        result = await _do_click(page, frame, x, y, double_click=double_click, button=button)
        if result["status"] == "failed":
            response = {**result, "col": col, "row": row}
        else:
            landed = True
            evidence: dict[str, Any] = {}
            if verify:
                landed, evidence = await _verify_landed(
                    page, frame, before_sel, col, row, before_visual, before_screenshot
                )
                retries_used = 0
                if not landed and VTABLE_VERIFICATION_STRATEGY.retry_count:
                    await _do_click(page, frame, x, y, double_click=double_click, button=button)
                    retries_used = 1
                    landed, evidence = await _verify_landed(
                        page, frame, before_sel, col, row, before_visual, before_screenshot
                    )
                evidence["retries_used"] = retries_used

            response = {
                "status": "clicked" if landed else "unverified",
                "col": col,
                "row": row,
                "point": {"x": x, "y": y},
                "cell": {
                    "behavior": (classify or {}).get("behavior"),
                    "editable": bool((classify or {}).get("editable")),
                },
                "double_click": double_click,
                "button": button,
                "verification": evidence,
            }
        if observe_after and settle_ms:
            await page.wait_for_timeout(settle_ms)
    except Exception as exc:
        response = {
            "status": "failed",
            "reason": f"cell-click-error: {exc}",
            "col": col,
            "row": row,
        }
    finally:
        if observe_after:
            from ..overlay import _finalize_overlay_observation
            await _finalize_overlay_observation(
                page, installed, response, settle_ms=settle_ms, max_results=max_results
            )
    response.setdefault("page_id", _page_id(page))
    frame_details = await _frame_context_details(page, frame)
    response["frame"] = frame_details
    verification = response.get("verification") or {}
    proof = []
    if verify:
        proof = [
            {"type": name, "matched": bool(verification.get(key))}
            for name, key in (
                ("selection-changed", "selection_changed"),
                ("target-selected", "target_selected"),
                ("editor-open", "editor_open"),
                ("scenegraph-changed", "scenegraph_changed"),
                ("screenshot-changed", "screenshot_changed"),
            )
        ]
        if verification.get("screenshot"):
            proof[-1]["matched"] = bool(verification["screenshot"].get("changed"))
    before_state: dict[str, Any] = {"selection": before_sel}
    if before_visual is not None:
        before_state["scenegraph_paints"] = before_visual.get("paints", [])
    if before_screenshot is not None:
        before_state["screenshot_digest"] = before_screenshot.get("digest")

    from ..interaction.contract import _interaction_contract
    return _interaction_contract(
        response,
        action="dblclick" if double_click else "click",
        target={"kind": "vtable-cell", "col": col, "row": row},
        before_state=before_state,
        after_state={
            "scenegraph_paints": (verification.get("scenegraph") or {}).get(
                "after_paints", []
            ),
            "visible_overlay_count": len(response.get("visible_overlays") or []),
        },
        evidence=proof,
    )


async def click_cell(
    col: int,
    row: int,
    *,
    double_click: bool = False,
    button: str = "left",
    verify: bool = True,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
    headless: bool = True,
) -> dict:
    async with _action_lock:
        return await _click_cell_impl(
            col,
            row,
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            headless=headless,
        )


async def _cell_info_impl(col: int, row: int) -> dict:
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    value = await frame.evaluate(
        f"() => {{ const t = window._vtable; return t ? t.getCellValue({col}, {row}) : null; }}"
    )
    center = await cell_center(page, frame, col, row)
    visible = await cell_visible(frame, col, row)
    classify = await frame.evaluate(_wrap2(CLASSIFY_CELL), [col, row])
    return {
        "status": "ok",
        "page_id": _page_id(page),
        "frame": await _frame_context_details(page, frame),
        "col": col,
        "row": row,
        "value": value,
        "behavior": (classify or {}).get("behavior", "none"),
        "editable": bool((classify or {}).get("editable", False)),
        "center": center,
        "in_viewport": visible,
    }


async def cell_info(col: int, row: int) -> dict:
    async with _action_lock:
        return await _cell_info_impl(col, row)


async def click_vtable_cell_by_field(
    field: str,
    record_index: int | list[int],
    *,
    double_click: bool = False,
    button: str = "left",
    verify: bool = True,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    """Resolve a business field/record through VTable APIs, then trusted-click it."""
    async with _action_lock:
        resolved = await _resolve_vtable_cell_impl(field, record_index)
        if resolved.get("status") != "ok":
            return resolved
        address = resolved["address"]
        result = await _click_cell_impl(
            int(address["col"]),
            int(address["row"]),
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
        )
        result["target"] = {
            "field": field,
            "record_index": record_index,
            "col": int(address["col"]),
            "row": int(address["row"]),
            "resolved_by": resolved.get("resolved_by"),
        }
        if result.get("interaction"):
            result["interaction"]["target"].update(result["target"])
        return result


async def _table_meta_impl() -> dict:
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "page_id": _page_id(page),
        }
    meta = await frame.evaluate(_wrap(TABLE_META))
    return {
        "status": "ok",
        "page_id": _page_id(page),
        "frame": await _frame_context_details(page, frame),
        "meta": meta,
    }


async def table_meta() -> dict:
    async with _action_lock:
        return await _table_meta_impl()


async def _cells_read_impl(col0: int, row0: int, col1: int, row1: int) -> dict:
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "page_id": _page_id(page),
        }
    result = await frame.evaluate(
        _wrap4(READ_CELLS),
        [col0, row0, col1, row1],
    )
    return {
        "status": "ok",
        "page_id": _page_id(page),
        "frame": await _frame_context_details(page, frame),
        "range": result,
        "rows": len((result or {}).get("values", [])),
    }


async def cells_read(col0: int, row0: int, col1: int, row1: int) -> dict:
    async with _action_lock:
        return await _cells_read_impl(col0, row0, col1, row1)


async def _drop_files_impl(
    col: int, row: int, files: list[str], *, data: dict[str, str] | None = None
) -> dict:
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }
    if not await ensure_cell_visible(page, frame, col, row):
        return {
            "status": "failed",
            "reason": "cell-not-in-viewport-after-scroll",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }
    rel = await frame.evaluate(_wrap2(CELL_RELATIVE_LOC), [col, row])
    if not rel:
        return {
            "status": "failed",
            "reason": "cell-relative-rect-unavailable",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }
    x = (rel.get("left", 0) + rel.get("right", 0)) / 2
    y = (rel.get("top", 0) + rel.get("bottom", 0)) / 2
    locator = frame.locator(".vtable canvas").first
    if not await locator.count():
        locator = frame.locator(".vtable").first
    payload = files[0] if len(files) == 1 else files
    await locator.drop(payload, position={"x": x, "y": y})
    return {
        "status": "dropped",
        "page_id": _page_id(page),
        "frame": await _frame_context_details(page, frame),
        "col": col,
        "row": row,
        "files": files,
        "position": {"x": x, "y": y},
    }


async def drop_files(
    col: int, row: int, files: list[str], *, data: dict[str, str] | None = None
) -> dict:
    async with _action_lock:
        return await _drop_files_impl(col, row, files, data=data)
