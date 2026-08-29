"""Overlay deduplication, viewport geometry enrichment, and focus context extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

from ..browser import (
    _frame_context_details,
    _frame_id,
)
from ..config import OVERLAY_RESULT_LIMIT


_OVERLAY_PRIORITY = {
    "dialog": 0,
    "drawer": 1,
    "dropdown": 2,
    "popover": 3,
    "notification": 4,
    "overlay": 5,
}


def _overlay_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        _OVERLAY_PRIORITY.get(str(item.get("kind", "overlay")), 9),
        0 if item.get("visible", True) else 1,
        str(item.get("timestamp", 0)),
    )


def _dedupe_overlays(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest and most settled signal for each overlay fingerprint."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        text = str(item.get("text", "")).strip()
        kind = str(item.get("kind", ""))
        fid = str(item.get("frame_id", ""))
        key = (fid, f"{kind}|{text}") if text else (fid, str(item.get("fingerprint", "")))
        previous = latest.get(key)
        if previous is None:
            latest[key] = item
            continue
        if item.get("visible", False) and not previous.get("visible", False) and item.get("event") != "removed":
            latest[key] = item
        elif item.get("event") == "removed":
            latest[key] = item
        elif item.get("timestamp", 0) >= previous.get("timestamp", 0):
            prev_sel = str(previous.get("selector", ""))
            transitional = any(tok in prev_sel for tok in ("-enter", "-appear", "-leave"))
            if transitional or (item.get("timestamp", 0) > previous.get("timestamp", 0)):
                latest[key] = item
    return sorted(latest.values(), key=lambda item: item.get("timestamp", 0))


def _new_overlays(
    baseline: list[dict[str, Any]],
    events: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return new / changed overlays without hiding observer-only transient toast events."""
    before = {
        (str(item.get("frame_id", "")), str(item.get("fingerprint", "")))
        for item in baseline
    }
    candidates = [*events, *current]
    detected = [
        item
        for item in candidates
        if (str(item.get("frame_id", "")), str(item.get("fingerprint", ""))) not in before
    ]
    return _dedupe_overlays(detected)


async def _enrich_overlay_items(
    page: Page,
    items: list[dict[str, Any]],
    *,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    """Add compact scope and top-level viewport geometry to overlay records."""
    if not items:
        return []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    from ..vtable.binding import active_application_frame
    active = await active_application_frame(page)
    active_id = _frame_id(page, active) if active is not None else ""
    offsets: dict[str, dict[str, float]] = {}
    frame_elements: dict[str, dict[str, str]] = {}
    for frame in frames:
        frame_id = _frame_id(page, frame)
        if frame == page.main_frame:
            offsets[frame_id] = {"x": 0.0, "y": 0.0}
            continue
        try:
            element = await frame.frame_element()
            attrs = await element.evaluate(
                "(el) => ({id: el.id || '', name: el.getAttribute('name') || '', src: el.getAttribute('src') || ''})"
            )
            frame_elements[frame_id] = attrs
            box = await element.bounding_box()
            if box:
                offsets[frame_id] = {"x": float(box["x"]), "y": float(box["y"])}
        except Exception:
            continue

    enriched: list[dict[str, Any]] = []
    for item in sorted(_dedupe_overlays(items), key=_overlay_sort_key):
        record = dict(item)
        frame_id = str(record.get("frame_id", ""))
        record["scope"] = (
            "active_iframe" if frame_id == active_id and frame_id else
            "top_document" if frame_id == _frame_id(page, page.main_frame) else
            "iframe"
        )
        if frame_id in frame_elements:
            elem = frame_elements[frame_id]
            record["iframe"] = {"id": elem.get("id", ""), "name": elem.get("name", "")}
        box = record.get("box")
        offset = offsets.get(frame_id)
        if box and offset:
            record["page_box"] = {
                "x": round(float(box["x"]) + offset["x"], 2),
                "y": round(float(box["y"]) + offset["y"], 2),
                "width": round(float(box["width"]), 2),
                "height": round(float(box["height"]), 2),
            }
        else:
            record["page_box"] = None
        record.pop("identity", None)
        record.pop("fingerprint", None)
        record.pop("class_name", None)
        record.pop("timestamp", None)
        if not record.get("label"):
            record.pop("label", None)
        if not record.get("role"):
            record.pop("role", None)
        if record.get("page_box") is None:
            record.pop("page_box", None)
        if record.get("box") is None:
            record.pop("box", None)
        enriched.append(record)
    return enriched[: max(1, int(max_results))]


async def _overlay_context(page: Page, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return only the page/frame focus state needed for the next AI action."""
    from ..vtable.binding import active_application_frame
    active = await active_application_frame(page)
    active_info = await _frame_context_details(page, active) if active is not None else None
    active_id = active_info.get("frame_id", "") if active_info else ""
    visible = [item for item in items if item.get("visible", True)]
    focus_candidates = [
        item for item in visible
        if item.get("kind") in {"dialog", "drawer", "dropdown", "popover", "notification"}
    ]
    focus = min(
        focus_candidates,
        key=lambda item: (
            0 if item.get("frame_id") == active_id else 1,
            _OVERLAY_PRIORITY.get(str(item.get("kind")), 9),
        ),
        default=None,
    )
    if focus is not None:
        compact_focus = dict(focus)
        compact_focus.pop("iframe", None)
        compact_focus.pop("box", None)
        focus = compact_focus
    return {
        "active_iframe": active_info,
        "focus_layer": focus,
    }


async def _scope_frame_ids(page: Page, scope: str) -> set[str] | None:
    if scope == "all":
        return None
    from ..vtable.binding import active_application_frame
    active = await active_application_frame(page)
    if active is None:
        return None
    allowed = {_frame_id(page, page.main_frame)}
    allowed.add(_frame_id(page, active))
    if scope not in {"active", "focused"}:
        raise ValueError("scope must be 'active' or 'all'")
    return allowed


def _filter_overlay_scope(
    items: list[dict[str, Any]], allowed_frame_ids: set[str] | None
) -> list[dict[str, Any]]:
    if allowed_frame_ids is None:
        return items
    return [item for item in items if item.get("frame_id") in allowed_frame_ids]
