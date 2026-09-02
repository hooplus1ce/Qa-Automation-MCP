"""Unified evidence contract and compact response shaping for DOM/VTable interactions."""

from __future__ import annotations

from typing import Any


def _interaction_contract(
    response: dict[str, Any],
    *,
    action: str,
    target: dict[str, Any],
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    """Attach one stable evidence model while retaining legacy result fields."""
    items = evidence or []
    succeeded = response.get("status") in {"acted", "clicked", "ok"}
    strong = any(item.get("matched") for item in items)
    coordinate = response.get("point")
    raw_coordinate = bool(coordinate and not target.get("analysis_id"))
    confidence = "none"
    if succeeded:
        confidence = "medium" if raw_coordinate and not strong else "high"
    clean_target = {k: v for k, v in target.items() if v is not None}
    clean_locator = {k: v for k, v in (response.get("locator") or {}).items() if v is not None}
    response["interaction"] = {
        "target": clean_target,
        "frame": response.get("frame"),
        "locator": clean_locator if clean_locator else None,
        "coordinate": coordinate,
        "action": action,
        "before_state": before_state or {},
        "after_state": after_state or {},
        "evidence": items,
        "confidence": confidence,
    }
    # Attach an ultra-clean semantic delta summary for high-signal test assertions
    changes = []
    raw_changes = response.get("overlays") or response.get("ui_events") or []
    for ch in raw_changes:
        summary: dict[str, Any] = {
            "kind": ch.get("kind"),
            "text": ch.get("text"),
            "visible": ch.get("visible", True),
        }
        if ch.get("event"):
            summary["event"] = ch.get("event")
        if ch.get("page_box"):
            summary["page_box"] = ch.get("page_box")
        if ch.get("selector") and ch.get("kind") in {"dialog", "drawer", "dropdown"}:
            summary["selector"] = ch.get("selector")
        changes.append(summary)
    inline_editor = response.get("inline_editor")
    if inline_editor and isinstance(inline_editor, dict):
        for el in inline_editor.get("elements") or []:
            changes.append({
                "kind": f"editor_{el.get('kind', 'element')}",
                "name": el.get("name"),
                "event": "mounted",
                "visible": True,
                "page_box": el.get("box"),
                "point": el.get("point"),
                "class_name": el.get("class_name"),
            })
    if changes:
        response["changes"] = changes
    if compact:
        compact_res: dict[str, Any] = {
            "status": response.get("status"),
            "action": action,
            "target": clean_target,
            "confidence": confidence,
        }
        if changes:
            compact_res["changes"] = changes
        focus = (response.get("context") or {}).get("focus_layer")
        if focus:
            compact_res["focus_layer"] = focus
        if inline_editor:
            compact_res["inline_editor"] = inline_editor
        return compact_res
    return response
