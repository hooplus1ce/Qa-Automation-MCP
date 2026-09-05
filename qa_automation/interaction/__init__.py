"""Unified semantic DOM action pipeline and interaction dispatch."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ..browser import (
    _action_lock,
    _current_page_impl,
    _frame_details,
    _frame_id,
    _page_id,
)
from ..components.vtable.binding import resolve_frame
from ..config import (
    ANALYSIS_MAX_AGE_SECONDS,
    OVERLAY_RESULT_LIMIT,
    OVERLAY_SETTLE_LIMIT_MS,
)
from ..mouse import _ensure_cursor_helper, _smooth_mouse_move_to, _stable_viewport_click
from .contract import _interaction_contract
from .locator import _find_interaction_locator, _perform_antd_select
from .snapshot import _focused_editable
from .typewriter import (
    calculate_typewriter_delay,
    typewriter_fill,
    typewriter_keyboard_type,
    typewriter_type,
)


async def _stable_locator_click(
    page: Page,
    target: Any,
    *,
    double_click: bool = False,
    button: str = "left",
    timeout_ms: float = 3_000,
) -> None:
    """Hover a visible locator, remeasure it, then click its final center."""
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000)
    initial: dict[str, float] | None = None
    while time.monotonic() < deadline:
        box = await target.bounding_box()
        if box and box["width"] > 0 and box["height"] > 0:
            initial = {key: float(box[key]) for key in ("x", "y", "width", "height")}
            break
        await asyncio.sleep(0.016)
    if initial is None:
        raise RuntimeError("target did not reach a visible position")
    if hasattr(target, "is_enabled") and not await target.is_enabled():
        raise RuntimeError("target is disabled")

    async def final_point() -> dict[str, float]:
        box = await target.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            raise RuntimeError("target became unavailable after hover")
        return {
            "x": float(box["x"]) + float(box["width"]) / 2,
            "y": float(box["y"]) + float(box["height"]) / 2,
        }

    await _stable_viewport_click(
        page,
        initial["x"] + initial["width"] / 2,
        initial["y"] + initial["height"] / 2,
        double_click=double_click,
        button=button,
        point_resolver=final_point,
        move_to=_smooth_mouse_move_to,
    )


async def _perform_dom_action(
    page: Page,
    target_frame: Frame,
    locator: Any,
    *,
    action: str,
    value: str | None,
    key: str | None,
    timeout_ms: float,
) -> dict[str, Any] | None:
    action = action.lower().strip()
    kwargs = {"timeout": timeout_ms}
    if action in {"click", "dblclick", "rightclick"}:
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
        await asyncio.sleep(0.04)
        await _stable_locator_click(
            page,
            locator,
            double_click=action == "dblclick",
            button="right" if action == "rightclick" else "left",
            timeout_ms=timeout_ms,
        )
        return None
    if action == "hover":
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
        await asyncio.sleep(0.04)
        box = await locator.bounding_box()
        if box and box["width"] > 0 and box["height"] > 0:
            center_x = float(box["x"]) + float(box["width"]) / 2
            center_y = float(box["y"]) + float(box["height"]) / 2
            await _ensure_cursor_helper(page)
            await _smooth_mouse_move_to(page, center_x, center_y)
        await locator.hover(**kwargs)
        return None
    if action in {"fill", "type"}:
        if value is None:
            raise ValueError(f"{action} requires value")
        if action == "fill":
            await typewriter_fill(locator, value, timeout_ms=timeout_ms)
        else:
            await typewriter_type(locator, value, timeout_ms=timeout_ms)
    elif action == "press":
        if key is None:
            raise ValueError("press requires key")
        await locator.press(key, **kwargs)
    elif action == "check":
        await locator.check(**kwargs)
    elif action == "uncheck":
        await locator.uncheck(**kwargs)
    elif action == "select":
        if value is None:
            raise ValueError("select requires value")
        is_antd = False
        try:
            is_antd = bool(
                await locator.evaluate(
                    "element => !!element.closest('.ant-select, .ant-cascader, .ant-tree-select')"
                )
            )
        except Exception:
            pass
        if is_antd:
            return await _perform_antd_select(
                page, target_frame, locator, value, timeout_ms=timeout_ms
            )
        await locator.select_option(value, **kwargs)
        return {"component": "native-select", "option": value}
    else:
        raise ValueError(
            f"Unsupported DOM action: {action!r}. Expected click, dblclick, "
            "rightclick, hover, fill, type, press, check, uncheck or select."
        )
    return None


async def _click_dom_impl(
    role: str,
    name: str | None = None,
    description: str | None = None,
    *,
    frame: str | None = None,
    timeout_ms: float = 10_000,
    page: Page | None = None,
) -> dict:
    if page is None:
        page = await _current_page_impl()
    try:
        fr = await resolve_frame(page, frame)
        kwargs: dict[str, Any] = {"name": name, "exact": True} if name else {}
        if description:
            kwargs["description"] = description
        from .locator import _unique_visible_locator

        locator = await _unique_visible_locator(fr.get_by_role(role, **kwargs), "ax-role")
        if locator is None:
            raise ValueError("target control not found in the selected page/frame scope")
        await _stable_locator_click(page, locator, timeout_ms=timeout_ms)
        return {
            "status": "clicked",
            "page_id": _page_id(page),
            "role": role,
            "name": name,
            "description": description,
            "frame": frame,
        }
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"dom-click-error: {e}",
            "page_id": _page_id(page),
            "role": role,
            "name": name,
            "description": description,
            "frame": frame,
        }


async def click_dom(
    role: str,
    name: str | None = None,
    description: str | None = None,
    *,
    frame: str | None = None,
    timeout_ms: float = 10_000,
) -> dict:
    async with _action_lock:
        return await _click_dom_impl(
            role,
            name=name,
            description=description,
            frame=frame,
            timeout_ms=timeout_ms,
        )


async def _dom_interact_impl(
    action: str,
    *,
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    value: str | None = None,
    key: str | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    timeout_ms: float = 3_000,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
    analysis_id: str | None = None,
    expect_input: bool = False,
    compact: bool = False,
) -> dict:
    if not 0 <= settle_ms <= OVERLAY_SETTLE_LIMIT_MS:
        raise ValueError(f"settle_ms must be between 0 and {OVERLAY_SETTLE_LIMIT_MS}")
    page = await _current_page_impl()
    installed: dict[str, Any] | None = None
    listener = None
    response: dict[str, Any] = {"status": "failed", "action": action}
    focused_before: dict[str, Any] | None = None
    focused_after: dict[str, Any] | None = None
    try:
        coordinate_supplied = x is not None or y is not None
        locator_supplied = any([role, text, placeholder, css, xpath])
        if coordinate_supplied and (x is None or y is None):
            raise ValueError("coordinate fallback requires both x and y")
        if not locator_supplied and not coordinate_supplied:
            raise ValueError("provide at least one locator candidate or an explicit x/y coordinate")
        if expect_input:
            focused_before = await _focused_editable(page)

        from ..components.vtable.analysis import _analysis_cache

        if analysis_id is not None:
            cached = _analysis_cache.get(analysis_id)
            if cached is None:
                raise ValueError(f"analysis_id {analysis_id!r} is unknown or expired")
            if time.monotonic() - cached["created"] > ANALYSIS_MAX_AGE_SECONDS:
                raise ValueError(f"analysis_id {analysis_id!r} has expired")
            cached_frame_id = cached.get("frame_id")
            target_frame_obj = None
            for fr in page.frames:
                if _frame_id(page, fr) == cached_frame_id:
                    target_frame_obj = fr
                    break
            if target_frame_obj is None:
                from ..components.vtable.binding import vtable_frame

                target_frame_obj = await vtable_frame(page)
            table_idx = cached.get("table_index")
            if table_idx is None and "table_index" in cached.get("options", {}):
                table_idx = cached["options"]["table_index"]
            from ..components.vtable.binding import _wrap2, ensure_vtable

            try:
                await ensure_vtable(target_frame_obj, table_idx)
            except Exception:
                pass
            from ..components.vtable.scripts import VTABLE_ANALYSIS

            raw = await target_frame_obj.evaluate(
                _wrap2(VTABLE_ANALYSIS), [cached["options"], None]
            )
            if not raw or "error" in raw:
                raise ValueError("scenegraph or canvas unavailable")
            from ..components.vtable.analysis import _analysis_layout_signature

            sig = _analysis_layout_signature(raw, cached["frame_id"], table_idx)
            if sig != cached["signature"]:
                response = {
                    "status": "failed",
                    "reason": "stale-coordinate",
                    "page_id": _page_id(page),
                    "action": action,
                }
                return response

        if observe_after:
            from ..overlay import (
                _acquire_overlay_frame_listener,
                _arm_overlay_init_script,
                _install_overlay_observers,
            )

            listener, _ = await _acquire_overlay_frame_listener(page, persistent=False)
            await _arm_overlay_init_script(page, settle_ms=settle_ms, persistent=False)
            installed = await _install_overlay_observers(page, reset=True)

        locator = target_frame = locator_source = None
        if locator_supplied:
            try:
                locator, target_frame, locator_source = await _find_interaction_locator(
                    page,
                    role=role,
                    name=name,
                    description=description,
                    text=text,
                    placeholder=placeholder,
                    css=css,
                    xpath=xpath,
                    frame=frame,
                    in_iframe=in_iframe,
                    timeout_ms=timeout_ms,
                )
            except Exception:
                if not coordinate_supplied:
                    raise

        if locator is None:
            # 坐标回退支持点击类动作与打字机输入动作
            if action in {"fill", "type"}:
                if value is None:
                    raise ValueError(f"{action} requires value")
                from ..components.vtable import _trusted_viewport_click

                clicked = await _trusted_viewport_click(page, float(x), float(y))
                if clicked["status"] != "ok":
                    raise ValueError(clicked.get("reason", "coordinate click to focus failed"))
                await typewriter_keyboard_type(page, value, clear_first=(action == "fill"))
                response = {
                    "status": "acted",
                    "page_id": _page_id(page),
                    "action": action,
                    "frame": _frame_details(page, page.main_frame),
                    "point": {"x": float(x), "y": float(y)},
                    "coordinate_space": "top-page-viewport-css-pixels",
                    "input": "playwright-keyboard-typewriter",
                }
            elif action not in {"click", "dblclick", "rightclick", "hover"}:
                raise ValueError(
                    f"action {action!r} requires a locator; coordinate fallback "
                    "only supports click, dblclick, rightclick, hover, fill and type"
                )
            elif action == "hover":
                from ..components.vtable import _trusted_viewport_hover

                act_res = await _trusted_viewport_hover(page, float(x), float(y))
                if act_res["status"] != "ok":
                    raise ValueError(act_res.get("reason", "coordinate hover failed"))
                response = {
                    "status": "acted",
                    "page_id": _page_id(page),
                    "action": action,
                    "frame": _frame_details(page, page.main_frame),
                    "point": {"x": float(x), "y": float(y)},
                    "coordinate_space": "top-page-viewport-css-pixels",
                    "input": "playwright-mouse",
                }
            else:
                from ..components.vtable import _trusted_viewport_click

                clicked = await _trusted_viewport_click(
                    page,
                    float(x),
                    float(y),
                    double_click=(action == "dblclick"),
                    button="right" if action == "rightclick" else "left",
                )
                if clicked["status"] != "ok":
                    raise ValueError(clicked.get("reason", "coordinate click failed"))
                response = {
                    "status": "acted",
                    "page_id": _page_id(page),
                    "action": action,
                    "frame": _frame_details(page, page.main_frame),
                    "point": {"x": float(x), "y": float(y)},
                    "coordinate_space": "top-page-viewport-css-pixels",
                    "input": "playwright-mouse",
                }
        else:
            action_detail = await _perform_dom_action(
                page,
                target_frame,
                locator,
                action=action,
                value=value,
                key=key,
                timeout_ms=timeout_ms,
            )
            response = {
                "status": "acted",
                "page_id": _page_id(page),
                "action": action,
                "frame": _frame_details(page, target_frame),
                "locator": {
                    "resolved_by": locator_source,
                    "role": role,
                    "name": name,
                    "description": description,
                    "text": text,
                    "placeholder": placeholder,
                    "css": css,
                    "xpath": xpath,
                },
            }
            if action_detail:
                response["action_detail"] = action_detail
        if observe_after and settle_ms:
            await page.wait_for_timeout(settle_ms)
        if expect_input:
            focused_after = await _focused_editable(page)
            verified = bool(focused_after and focused_after != focused_before)
            response["activation"] = {
                "expected": "editable-dom-control",
                "verified": verified,
                "element": focused_after if verified else None,
            }
    except Exception as exc:
        response = {
            "status": "failed",
            "page_id": _page_id(page),
            "action": action,
            "reason": str(exc)[:500],
        }
    finally:
        if installed is not None:
            from ..overlay import _finalize_overlay_observation

            await _finalize_overlay_observation(
                page, installed, response, settle_ms=settle_ms, max_results=max_results
            )
        elif listener is not None:
            from ..overlay import (
                _release_overlay_frame_listener,
                _stop_overlay_observers_best_effort,
            )

            cleanup_errors = await _stop_overlay_observers_best_effort(page)
            cleanup_errors.extend(
                await _release_overlay_frame_listener(page, listener, persistent=False)
            )
            if cleanup_errors:
                response["observer_errors"] = cleanup_errors
                response["observer_cleanup_failed"] = True
    locator_result = response.get("locator") or {}
    proof = [
        {
            "type": "locator-resolved",
            "matched": bool(locator_result.get("resolved_by")),
            "source": locator_result.get("resolved_by"),
        },
        {
            "type": "trusted-coordinate",
            "matched": response.get("input") == "playwright-mouse",
        },
        {
            "type": "editable-focused",
            "matched": bool((response.get("activation") or {}).get("verified")),
        },
        {
            "type": "overlay-event",
            "matched": bool(response.get("ui_events") or response.get("overlays")),
            "count": len(response.get("ui_events") or response.get("overlays") or []),
        },
    ]
    return _interaction_contract(
        response,
        action=action,
        target={
            "role": role,
            "name": name,
            "description": description,
            "text": text,
            "placeholder": placeholder,
            "css": css,
            "xpath": xpath,
            "x": x,
            "y": y,
            "analysis_id": analysis_id,
        },
        before_state={
            "focused_editable": focused_before,
            "visible_overlay_count": len(installed.get("baseline") or [])
            if installed
            else len(response.get("baseline") or []),
        },
        after_state={
            "focused_editable": focused_after,
            "visible_overlay_count": len(response.get("visible_overlays") or []),
        },
        evidence=proof,
        compact=compact,
    )


async def dom_interact(
    action: str,
    *,
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    value: str | None = None,
    key: str | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    timeout_ms: float = 3_000,
    observe_after: bool = True,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
    analysis_id: str | None = None,
    expect_input: bool = False,
    compact: bool = False,
) -> dict:
    async with _action_lock:
        return await _dom_interact_impl(
            action,
            role=role,
            name=name,
            description=description,
            text=text,
            placeholder=placeholder,
            css=css,
            xpath=xpath,
            x=x,
            y=y,
            value=value,
            key=key,
            frame=frame,
            in_iframe=in_iframe,
            timeout_ms=timeout_ms,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            analysis_id=analysis_id,
            expect_input=expect_input,
            compact=compact,
        )


async def mouse_drag(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    *,
    steps: int = 24,
    button: str = "left",
    hold_ms: int = 80,
    settle_ms: int = 200,
    observe_after: bool = False,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict[str, Any]:
    """Execute a realistic physical mouse drag from (start_x, start_y) to (end_x, end_y)."""
    async with _action_lock:
        page = await _current_page_impl()
        installed = None
        listener = None
        if observe_after:
            from ..overlay import (
                _acquire_overlay_frame_listener,
                _arm_overlay_init_script,
                _install_overlay_observers,
            )

            listener, _ = await _acquire_overlay_frame_listener(page, persistent=False)
            await _arm_overlay_init_script(page, settle_ms=settle_ms, persistent=False)
            installed = await _install_overlay_observers(page, reset=True)
        result: dict[str, Any] = {}
        try:
            from ..mouse import _mouse_drag_impl

            result = await _mouse_drag_impl(
                page,
                start_x,
                start_y,
                end_x,
                end_y,
                steps=steps,
                button=button,
                hold_ms=hold_ms,
                settle_ms=settle_ms,
            )
            result["page_id"] = _page_id(page)
        finally:
            if installed is not None:
                from ..overlay import _finalize_overlay_observation

                await _finalize_overlay_observation(
                    page, installed, result, settle_ms=settle_ms, max_results=max_results
                )
            elif listener is not None:
                from ..overlay import (
                    _release_overlay_frame_listener,
                    _stop_overlay_observers_best_effort,
                )

                await _stop_overlay_observers_best_effort(page)
                await _release_overlay_frame_listener(page, listener, persistent=False)
        return result


__all__ = [
    "calculate_typewriter_delay",
    "click_dom",
    "dom_interact",
    "mouse_drag",
    "typewriter_fill",
    "typewriter_keyboard_type",
    "typewriter_type",
]
