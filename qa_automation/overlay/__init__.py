"""Ant Design Portal and ARIA overlay observation engine."""

from __future__ import annotations

import time
import weakref
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ..browser import (
    _action_lock,
    _current_page_impl,
    _frame_details,
    _page_id,
)
from ..config import (
    OVERLAY_RESULT_LIMIT,
    OVERLAY_SETTLE_LIMIT_MS,
)
from .enrichment import (
    _dedupe_overlays,
    _enrich_overlay_items,
    _filter_overlay_scope,
    _frame_geometry,
    _new_overlays,
    _overlay_context,
    _scope_frame_ids,
)
from .listener import (
    _acquire_overlay_frame_listener,
    _OverlayFrameListener,
    _release_overlay_frame_listener,
)
from .scripts import (
    _OVERLAY_DEADLINE_VAR,
    _OVERLAY_DRAIN_TEMPLATE,
    _OVERLAY_OBSERVER_TEMPLATE,
    _overlay_arm_script,
    _overlay_script,
)

# 已安装常驻 arm 脚本的页面;后续交互只刷新 deadline 变量
_armed_pages: weakref.WeakSet[Any] = weakref.WeakSet()


def _page_frame_count(page: Page) -> int:
    try:
        return len(page.frames)
    except Exception:
        return 1


async def _arm_overlay_init_script(
    page: Page, *, settle_ms: int = 300, extra_ms: int = 0, persistent: bool = False
) -> None:
    duration = (settle_ms + extra_ms + 2_000) if not persistent else 86_400_000
    deadline_ms = int(time.time() * 1000) + duration
    script = _overlay_arm_script()
    try:
        if page not in _armed_pages:
            await page.add_init_script(script)
            _armed_pages.add(page)
        # 刷新当前文档的 deadline 并立即武装;后续导航由 init script 读取同一变量
        await page.evaluate(
            "(args) => { window[args[0]] = args[1]; }",
            [_OVERLAY_DEADLINE_VAR, deadline_ms],
        )
        await page.evaluate(script)
    except Exception:
        pass


async def _install_overlay_observer_in_frame(
    page: Page, frame: Frame, *, reset: bool = True
) -> dict[str, Any]:
    result = await frame.evaluate(_overlay_script(_OVERLAY_OBSERVER_TEMPLATE, reset=reset))
    items = [
        {**_frame_details(page, frame), **item}
        for item in result.get("baseline", [])
    ]
    return {
        "reused": bool(result.get("reused", False)),
        "baseline": items,
    }


async def _install_overlay_observers(page: Page, *, reset: bool = False) -> dict[str, Any]:
    baseline: list[dict[str, Any]] = []
    new_frame_baseline: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    had_existing_observer = False
    try:
        frames = list(page.frames)
    except Exception as exc:
        return {
            "baseline": [],
            "all_baseline": [],
            "new_frame_baseline": [],
            "errors": [{"reason": f"observer-frame-list-error: {exc}"}],
        }
    for frame in frames:
        try:
            result = await _install_overlay_observer_in_frame(page, frame, reset=reset)
            had_existing_observer = had_existing_observer or result["reused"]
            frame_items = result["baseline"]
            baseline.extend(frame_items)
            if not reset and not result["reused"]:
                new_frame_baseline.extend(frame_items)
        except Exception as exc:
            try:
                details = _frame_details(page, frame)
            except Exception:
                details = {"frame_id": f"frame-error:{id(frame)}", "frame_url": "", "frame_name": ""}
            errors.append({**details, "reason": str(exc)[:500]})
    all_baseline = _dedupe_overlays(baseline)
    new_frame_baseline = _dedupe_overlays(new_frame_baseline)
    if reset or not had_existing_observer:
        comparison_baseline = all_baseline
    else:
        new_keys = {
            (item.get("frame_id", ""), item.get("fingerprint", ""))
            for item in new_frame_baseline
        }
        comparison_baseline = [
            item
            for item in all_baseline
            if (item.get("frame_id", ""), item.get("fingerprint", "")) not in new_keys
        ]
    return {
        "baseline": comparison_baseline,
        "all_baseline": all_baseline,
        "new_frame_baseline": new_frame_baseline if had_existing_observer and not reset else [],
        "errors": errors,
    }


async def _stop_overlay_observers_best_effort(page: Page) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for frame in list(page.frames):
        try:
            await frame.evaluate(_overlay_script(_OVERLAY_DRAIN_TEMPLATE, stop=True))
        except Exception as exc:
            errors.append({**_frame_details(page, frame), "reason": str(exc)[:500]})
    return errors


async def _drain_overlay_observers(
    page: Page,
    *,
    stop: bool = False,
    frame_listener: _OverlayFrameListener | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    stop_errors: list[dict[str, Any]] = []
    events_truncated = False
    dropped_event_count = 0
    if frame_listener is not None:
        await frame_listener.wait_pending()
        listener_events, listener_errors = frame_listener.take_buffers()
        events.extend(listener_events)
        errors.extend(listener_errors)
    try:
        late_install = await _install_overlay_observers(page, reset=False)
        events.extend({**item, "event": "added"} for item in late_install["new_frame_baseline"])
        errors.extend(late_install["errors"])
    except Exception as exc:
        errors.append({"reason": f"observer-late-install-error: {exc}"})
    if frame_listener is not None:
        await frame_listener.wait_pending()
        listener_events, listener_errors = frame_listener.take_buffers()
        events.extend(listener_events)
        errors.extend(listener_errors)
    try:
        frames = list(page.frames)
    except Exception as exc:
        errors.append({"reason": f"observer-frame-list-error: {exc}"})
        frames = []
    for frame in frames:
        try:
            result = await frame.evaluate(_overlay_script(_OVERLAY_DRAIN_TEMPLATE, stop=stop))
            for item in result.get("events", []):
                events.append({**_frame_details(page, frame), **item})
            for item in result.get("current", []):
                current.append({**_frame_details(page, frame), **item})
            for item in result.get("baseline", []):
                baseline.append({**_frame_details(page, frame), **item})
            events_truncated = events_truncated or bool(result.get("events_truncated"))
            dropped_event_count += int(result.get("dropped_event_count", 0) or 0)
        except Exception as exc:
            error = {**_frame_details(page, frame), "reason": str(exc)[:500]}
            errors.append(error)
            if stop:
                stop_errors.append(error)
    return {
        "events": _dedupe_overlays(events),
        "current": _dedupe_overlays(current),
        "baseline": _dedupe_overlays(baseline),
        "errors": errors,
        "stop_errors": stop_errors,
        "events_truncated": events_truncated,
        "dropped_event_count": dropped_event_count,
    }


async def _finalize_overlay_observation(
    page: Page,
    installed: dict[str, Any],
    response: dict[str, Any],
    *,
    settle_ms: int,
    max_results: int = OVERLAY_RESULT_LIMIT,
    keep_listener: bool = False,
) -> None:
    if installed is None:
        return
    frame_listener = installed.get("frame_listener")
    try:
        drained = await _drain_overlay_observers(
            page, stop=True, frame_listener=frame_listener
        )
    except Exception as exc:
        cleanup_errors = await _stop_overlay_observers_best_effort(page)
        cleanup_errors.extend(
            await _release_overlay_frame_listener(
                page, frame_listener, persistent=keep_listener
            )
        )
        response.update(
            {
                "settle_ms": settle_ms,
                "baseline": installed.get("baseline", []),
                "ui_events": [],
                "overlays": [],
                "visible_overlays": [],
                "frame_count": _page_frame_count(page),
                "observer_errors": [
                    *installed.get("errors", []),
                    {"reason": f"observer-drain-error: {exc}"},
                    *cleanup_errors,
                ],
                "events_truncated": False,
                "dropped_event_count": 0,
                "observer_cleanup_failed": bool(cleanup_errors),
            }
        )
        return
    listener_errors = await _release_overlay_frame_listener(
        page, frame_listener, persistent=keep_listener
    )
    baseline = installed.get("baseline", [])
    raw_events = drained["events"]
    raw_current = drained["current"]
    raw_overlays = _new_overlays(baseline, raw_events, raw_current)
    # 一次几何计算供全部 enrich 共享,避免对每个 frame 重复做 CDP 往返
    geometry = await _frame_geometry(page)
    ui_events = await _enrich_overlay_items(
        page, raw_events, max_results=max_results, geometry=geometry
    )
    overlays = await _enrich_overlay_items(
        page, raw_overlays, max_results=max_results, geometry=geometry
    )
    visible_overlays = await _enrich_overlay_items(
        page, raw_current, max_results=max_results, geometry=geometry
    )
    response.update(
        {
            "settle_ms": settle_ms,
            "baseline": (
                await _enrich_overlay_items(
                    page, baseline, max_results=min(2, max_results), geometry=geometry
                )
                if max_results > 20 else []
            ),
            "ui_events": ui_events,
            "overlays": overlays,
            "visible_overlays": visible_overlays,
            "context": await _overlay_context(page, [*overlays, *visible_overlays]),
            "frame_count": _page_frame_count(page),
            "observer_errors": [
                *installed.get("errors", []),
                *drained["errors"],
                *listener_errors,
            ],
            "events_truncated": bool(drained["events_truncated"]),
            "dropped_event_count": int(drained["dropped_event_count"]),
            "observer_cleanup_failed": bool(drained["stop_errors"] or listener_errors),
        }
    )


async def _scan_overlays_impl(
    *, max_results: int = OVERLAY_RESULT_LIMIT, scope: str = "active"
) -> dict:
    page = await _current_page_impl()
    allowed_frame_ids = await _scope_frame_ids(page, scope)
    raw_items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for frame in list(page.frames):
        try:
            res = await frame.evaluate(
                _overlay_script(_OVERLAY_OBSERVER_TEMPLATE, observe=False)
            )
            for item in res.get("current", []):
                raw_items.append({**_frame_details(page, frame), **item})
        except Exception as exc:
            errors.append({**_frame_details(page, frame), "reason": str(exc)[:500]})
    filtered_raw = _filter_overlay_scope(raw_items, allowed_frame_ids)
    items = await _enrich_overlay_items(page, filtered_raw, max_results=max_results)
    return {
        "status": "ok",
        "page_id": _page_id(page),
        "scope": scope,
        "overlays": items,
        "count": len(items),
        "frame_count": _page_frame_count(page),
        "context": await _overlay_context(page, items),
        "errors": errors,
    }


async def scan_overlays(
    *, max_results: int = OVERLAY_RESULT_LIMIT, scope: str = "active"
) -> dict:
    async with _action_lock:
        return await _scan_overlays_impl(max_results=max_results, scope=scope)


async def _observe_overlays_impl(
    *,
    settle_ms: int = 300,
    stop: bool = True,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    if not 0 <= settle_ms <= OVERLAY_SETTLE_LIMIT_MS:
        raise ValueError(f"settle_ms must be between 0 and {OVERLAY_SETTLE_LIMIT_MS}")
    page = await _current_page_impl()
    installed: dict[str, Any] | None = None
    frame_listener: _OverlayFrameListener | None = None
    try:
        frame_listener, listener_reused = await _acquire_overlay_frame_listener(
            page, persistent=not stop
        )
        await _arm_overlay_init_script(
            page, settle_ms=settle_ms, persistent=not stop
        )
        installed = await _install_overlay_observers(page, reset=False)
        installed["frame_listener"] = frame_listener
        installed["reused"] = installed.get("reused", False) or listener_reused
        if settle_ms:
            await page.wait_for_timeout(settle_ms)
        drained = await _drain_overlay_observers(
            page, stop=stop, frame_listener=frame_listener
        )
        listener_errors = await _release_overlay_frame_listener(
            page, frame_listener, persistent=not stop
        )
        baseline = installed["baseline"]
        detected = _new_overlays(baseline, drained["events"], drained["current"])
        geometry = await _frame_geometry(page)
        events = await _enrich_overlay_items(
            page, drained["events"], max_results=max_results, geometry=geometry
        )
        overlays = await _enrich_overlay_items(
            page, detected, max_results=max_results, geometry=geometry
        )
        visible_overlays = await _enrich_overlay_items(
            page, drained["current"], max_results=max_results, geometry=geometry
        )
        return {
            "status": "ok",
            "settle_ms": settle_ms,
            "baseline": await _enrich_overlay_items(
                page, baseline, max_results=max_results, geometry=geometry
            ),
            "events": events,
            "overlays": overlays,
            "visible_overlays": visible_overlays,
            "context": await _overlay_context(page, [*overlays, *visible_overlays]),
            "frame_count": _page_frame_count(page),
            "observer_errors": [
                *installed["errors"],
                *drained["errors"],
                *listener_errors,
            ],
            "events_truncated": bool(drained["events_truncated"]),
            "dropped_event_count": int(drained["dropped_event_count"]),
            "stopped": stop,
            "observer_cleanup_failed": bool(
                stop and (drained["stop_errors"] or listener_errors)
            ),
        }
    except Exception as exc:
        cleanup_errors = await _stop_overlay_observers_best_effort(page)
        cleanup_errors.extend(
            await _release_overlay_frame_listener(
                page, frame_listener, persistent=False
            )
        )
        return {
            "status": "failed",
            "reason": f"overlay-observe-error: {exc}",
            "page_id": _page_id(page),
            "observer_errors": cleanup_errors,
            "events_truncated": False,
            "dropped_event_count": 0,
            "observer_cleanup_failed": bool(cleanup_errors),
        }


async def observe_overlays(
    *,
    settle_ms: int = 300,
    stop: bool = True,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    async with _action_lock:
        return await _observe_overlays_impl(
            settle_ms=settle_ms, stop=stop, max_results=max_results
        )


async def _click_dom_and_observe_impl(
    role: str,
    name: str | None = None,
    description: str | None = None,
    *,
    frame: str | None = None,
    settle_ms: int = 300,
    timeout_ms: float = 10_000,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    """Click an accessible control and immediately collect Portal/ARIA changes."""
    if not 0 <= settle_ms <= OVERLAY_SETTLE_LIMIT_MS:
        raise ValueError(f"settle_ms must be between 0 and {OVERLAY_SETTLE_LIMIT_MS}")
    page = await _current_page_impl()
    installed: dict[str, Any] | None = None
    frame_listener: _OverlayFrameListener | None = None
    response: dict[str, Any] = {
        "status": "failed",
        "role": role,
        "name": name,
        "description": description,
        "frame": frame,
    }
    try:
        frame_listener, _ = await _acquire_overlay_frame_listener(
            page, persistent=False
        )
        await _arm_overlay_init_script(
            page,
            settle_ms=settle_ms,
            extra_ms=max(0, int(timeout_ms)),
            persistent=False,
        )
        installed = await _install_overlay_observers(page, reset=True)
        await frame_listener.wait_pending()
        frame_listener.take_buffers()
        installed["frame_listener"] = frame_listener

        import qa_automation as _pkg
        click_fn = getattr(_pkg, "_click_dom_impl", None)
        if click_fn is not None:
            response = await click_fn(
                role,
                name=name,
                description=description,
                frame=frame,
                timeout_ms=timeout_ms,
                page=page,
            )
        else:
            from ..interaction import _click_dom_impl
            response = await _click_dom_impl(
                role,
                name=name,
                description=description,
                frame=frame,
                timeout_ms=timeout_ms,
                page=page,
            )
        if response.get("status") == "clicked" and settle_ms:
            await page.wait_for_timeout(settle_ms)
    except Exception as exc:
        response = {
            "status": "failed",
            "reason": f"dom-click-observe-error: {exc}",
            "role": role,
            "name": name,
            "description": description,
            "frame": frame,
        }
    finally:
        if installed is not None:
            await _finalize_overlay_observation(
                page, installed, response, settle_ms=settle_ms, max_results=max_results
            )
        elif frame_listener is not None:
            cleanup_errors = await _stop_overlay_observers_best_effort(page)
            cleanup_errors.extend(
                await _release_overlay_frame_listener(
                    page, frame_listener, persistent=False
                )
            )
            if cleanup_errors:
                response["observer_errors"] = cleanup_errors
                response["observer_cleanup_failed"] = True
    return response


async def click_dom_and_observe(
    role: str,
    name: str | None = None,
    description: str | None = None,
    *,
    frame: str | None = None,
    settle_ms: int = 300,
    timeout_ms: float = 10_000,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    async with _action_lock:
        return await _click_dom_and_observe_impl(
            role,
            name=name,
            description=description,
            frame=frame,
            settle_ms=settle_ms,
            timeout_ms=timeout_ms,
            max_results=max_results,
        )
