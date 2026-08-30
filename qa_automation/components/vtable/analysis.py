"""VTable scenegraph analysis, column header icon scanning, and layout signatures."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from ...browser import (
    _action_lock,
    _current_page_impl,
    _frame_context_details,
    _frame_page_offset,
    _page_id,
    _page_viewport_size,
)
from ...config import (
    ACTIVE_PROFILE,
    ANALYSIS_CACHE_LIMIT,
)
from .binding import (
    _vtable_directory,
    _wrap2,
    ensure_vtable,
    resolve_frame,
    vtable_frame,
)
from .scripts import VTABLE_ANALYSIS

_analysis_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_analysis_counter = 0


def _analysis_geometry(
    geometry: Any,
    *,
    frame_offset: dict[str, float],
    canvas_box: dict[str, float],
    viewport: dict[str, float],
    source: str,
) -> dict[str, Any] | None:
    if not isinstance(geometry, dict):
        return None
    try:
        box = geometry["box"]
        center = geometry["center"]
        local_x = float(box["x"])
        local_y = float(box["y"])
        width = float(box["width"])
        height = float(box["height"])
        center_x = float(center["x"])
        center_y = float(center["y"])
        numbers = (local_x, local_y, width, height, center_x, center_y)
        if not all(math.isfinite(value) for value in numbers) or width <= 0 or height <= 0:
            return None
        page_x = frame_offset["x"] + canvas_box["x"] + center_x
        page_y = frame_offset["y"] + canvas_box["y"] + center_y
        box_x = frame_offset["x"] + canvas_box["x"] + local_x
        box_y = frame_offset["y"] + canvas_box["y"] + local_y
        if not all(math.isfinite(value) for value in (page_x, page_y, box_x, box_y)):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "point": {"x": round(page_x, 2), "y": round(page_y, 2)},
        "page_box": {
            "x": round(box_x, 2),
            "y": round(box_y, 2),
            "width": round(width, 2),
            "height": round(height, 2),
        },
        "in_viewport": 0 <= page_x < viewport["width"] and 0 <= page_y < viewport["height"],
        "source": source,
    }


def _round_box(val: Any) -> Any:
    if isinstance(val, dict):
        return {
            k: round(float(v), 1) if isinstance(v, (int, float)) and math.isfinite(v) else v
            for k, v in val.items()
        }
    return val


def _analysis_layout_signature(
    raw: dict[str, Any], frame_id: str, table_index: int | None = None
) -> str:
    meta = raw.get("meta") or {}
    columns = []
    for column in raw.get("columns") or []:
        columns.append(
            {
                "col": column.get("col"),
                "field": column.get("field"),
                "header": [
                    {
                        "row": item.get("row"),
                        "geometry": _round_box(item.get("geometry")),
                        "icons": [
                            {"name": icon.get("name"), "box": _round_box(icon.get("box"))}
                            for icon in item.get("icons") or []
                        ],
                    }
                    for item in column.get("header") or []
                ],
                "cells": [
                    {
                        "row": item.get("row"),
                        "geometry": _round_box(item.get("geometry")),
                        "targets": [
                            {"name": target.get("name"), "box": _round_box(target.get("box"))}
                            for target in item.get("targets") or []
                        ],
                    }
                    for item in column.get("sample_cells") or []
                ],
            }
        )
    payload = {
        "frame_id": frame_id,
        "table_index": table_index,
        "rowCount": meta.get("rowCount"),
        "colCount": meta.get("colCount"),
        "scrollLeft": round(float(meta.get("scrollLeft") or 0), 1),
        "scrollTop": round(float(meta.get("scrollTop") or 0), 1),
        "canvas_box": _round_box(meta.get("canvas_box")),
        "columns": columns,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]

def _remember_analysis(
    *,
    page_id: str,
    frame_id: str,
    signature: str,
    options: dict[str, Any],
    table_index: int | None = None,
) -> str:
    global _analysis_counter
    _analysis_counter += 1
    analysis_id = f"analysis-{_analysis_counter}-{signature[:8]}"
    _analysis_cache[analysis_id] = {
        "page_id": page_id,
        "frame_id": frame_id,
        "table_index": table_index,
        "signature": signature,
        "options": options,
        "created": time.monotonic(),
    }
    _analysis_cache.move_to_end(analysis_id)
    while len(_analysis_cache) > ANALYSIS_CACHE_LIMIT:
        _analysis_cache.popitem(last=False)
    return analysis_id


async def _vtable_analysis_impl(
    *,
    max_columns: int = 20,
    sample_rows: int = 2,
    mode: str = "interactive",
    fields: list[str] | None = None,
    include_values: bool = False,
    visible_only: bool = True,
    table_index: int | None = None,
    frame: str | None = None,
) -> dict:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"interactive", "full"}:
        return {"status": "failed", "reason": "mode must be interactive or full"}
    options = {
        "max_columns": max(1, min(100, int(max_columns))),
        "sample_rows": max(0, min(8, int(sample_rows))),
        "fields": [str(value)[:160] for value in (fields or [])[:30] if str(value)],
        "include_values": bool(include_values),
        "table_index": table_index,
    }
    page = await _current_page_impl()
    try:
        frame_obj = (
            await resolve_frame(page, frame)
            if frame is not None
            else await vtable_frame(page)
        )
        tables = await _vtable_directory(page, frame_obj)
        frame_details = await _frame_context_details(page, frame_obj)
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-directory-error: {exc}",
        }
    if not tables:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": "no-visible-vtable",
            "frame": frame_details,
        }

    selected: dict[str, Any] | None = None
    if table_index is not None:
        selected = next(
            (item for item in tables if item["table_index"] == int(table_index)), None
        )
        if selected is None:
            return {
                "status": "failed",
                "page_id": _page_id(page),
                "reason": "unknown-table-index",
                "table_index": table_index,
                "frame": frame_details,
                "tables": tables,
            }
    else:
        modal_tables = [item for item in tables if item["context"] == "modal"]
        if modal_tables:
            selected = modal_tables[-1]
        elif len(tables) == 1:
            selected = tables[0]
        else:
            return {
                "status": "needs_table_selection",
                "page_id": _page_id(page),
                "reason": "multiple-visible-vtables",
                "frame": frame_details,
                "tables": tables,
                "hint": "Call vtable_analysis again with one table_index from tables.",
            }

    selected_index = int(selected["table_index"])
    options["table_index"] = selected_index
    try:
        await ensure_vtable(frame_obj, selected_index)
        raw = await frame_obj.evaluate(_wrap2(VTABLE_ANALYSIS), [options, None])
        if not raw:
            raise ValueError("scenegraph or canvas unavailable")
        raw_meta = raw.get("meta") or {}
        raw_canvas = raw_meta["canvas_box"]
        canvas_box = {key: float(raw_canvas[key]) for key in ("x", "y", "width", "height")}
        if not all(math.isfinite(value) for value in canvas_box.values()):
            raise ValueError("non-finite canvas geometry")
        frame_offset = await _frame_page_offset(page, frame_obj)
        viewport = await _page_viewport_size(page)
    except Exception as exc:
        return {"status": "failed", "page_id": _page_id(page), "reason": f"vtable-analysis-error: {exc}"}

    def converted(geometry: Any, source: str) -> dict[str, Any] | None:
        return _analysis_geometry(
            geometry,
            frame_offset=frame_offset,
            canvas_box=canvas_box,
            viewport=viewport,
            source=source,
        )

    def output_geometry(value: dict[str, Any]) -> dict[str, Any]:
        if normalized_mode == "full":
            return value
        result = {"point": value["point"]}
        if not visible_only:
            result["in_viewport"] = value["in_viewport"]
        return result

    columns: list[dict[str, Any]] = []
    for raw_column in raw.get("columns") or []:
        if not isinstance(raw_column, dict):
            continue
        header: list[dict[str, Any]] = []
        for raw_header in raw_column.get("header") or []:
            if not isinstance(raw_header, dict):
                continue
            icons = []
            for raw_icon in raw_header.get("icons") or []:
                geometry = converted(raw_icon, "vtable-scenegraph.globalAABBBounds")
                if not geometry or (visible_only and not geometry["in_viewport"]):
                    continue
                icon = {
                    "name": str(raw_icon.get("name") or "")[:120],
                    "function": str(raw_icon.get("function") or "custom")[:80],
                    "geometry": output_geometry(geometry),
                }
                if normalized_mode == "full":
                    icon["evidence"] = raw_icon.get("evidence") or []
                icons.append(icon)
            item: dict[str, Any] = {"row": raw_header.get("row"), "icons": icons}
            if normalized_mode == "full":
                item["geometry"] = converted(raw_header.get("geometry"), "VTable.getCellRelativeRect")
            if icons or normalized_mode == "full":
                header.append(item)

        sample_cells: list[dict[str, Any]] = []
        for raw_cell in raw_column.get("sample_cells") or []:
            if not isinstance(raw_cell, dict):
                continue
            geometry = converted(raw_cell.get("geometry"), "VTable.getCellRelativeRect")
            if visible_only and geometry and not geometry["in_viewport"]:
                continue
            targets = []
            for raw_target in raw_cell.get("targets") or []:
                target_geometry = converted(raw_target, "vtable-scenegraph.globalAABBBounds")
                if not target_geometry or (visible_only and not target_geometry["in_viewport"]):
                    continue
                targets.append(
                    {
                        "name": str(raw_target.get("name") or "")[:120],
                        "function": str(raw_target.get("function") or "custom")[:80],
                        "confidence": raw_target.get("confidence") or "confirmed",
                        "evidence": raw_target.get("evidence") or [],
                        "geometry": output_geometry(target_geometry),
                    }
                )
            interaction = dict(raw_cell.get("interaction") or {})
            if interaction.get("kind") == "scenegraph-target" and not targets:
                interaction.update({"kind": "none", "confidence": "none", "clickable": False})
            if normalized_mode == "interactive" and interaction.get("confidence") == "none":
                continue
            sample = {
                "row": raw_cell.get("row"),
                "record_index": raw_cell.get("record_index"),
                "type": raw_cell.get("type"),
                "interaction": interaction,
                "geometry": output_geometry(geometry) if geometry else None,
            }
            editor = raw_cell.get("editor") or {}
            if editor.get("available"):
                sample["editor"] = editor if normalized_mode == "full" else {
                    "opens_dom_input_on": interaction.get("activation"),
                    "click_opens_dom_input": bool(editor.get("click_opens_dom_input")),
                    "expected_dom_tags": editor.get("expected_dom_tags") or [],
                }
            if targets:
                sample["targets"] = targets
            if include_values and "value" in raw_cell:
                sample["value"] = raw_cell.get("value")
            sample_cells.append(sample)

        column = {
            "col": raw_column.get("col"),
            "field": str(raw_column.get("field") or "")[:160],
            "title": str(raw_column.get("title") or "")[:160],
        }
        if header:
            if normalized_mode == "full":
                column["header"] = header
            else:
                column["header_icons"] = [icon for item in header for icon in item["icons"]]
        if sample_cells:
            column["sample_cells"] = sample_cells
        columns.append(column)

    meta = {
        key: raw_meta.get(key)
        for key in (
            "rowCount", "colCount", "headerRowCount", "frozenRowCount", "frozenColCount",
            "editCellTrigger", "scrollLeft", "scrollTop",
        )
    }
    meta["scannedColumns"] = len(columns)
    meta["sampleRowsPerColumn"] = options["sample_rows"]
    frame_id = str(frame_details.get("frame_id") or "")
    signature = _analysis_layout_signature(raw, frame_id, selected_index)
    analysis_id = _remember_analysis(
        page_id=_page_id(page),
        frame_id=frame_id,
        signature=signature,
        options=options,
        table_index=selected_index,
    )
    return {
        "status": "ok",
        "profile": ACTIVE_PROFILE.name,
        "page_id": _page_id(page),
        "frame": frame_details,
        "table": selected,
        "analysis_id": analysis_id,
        "layout_signature": signature,
        "coordinate_space": "top-page-viewport-css-pixels",
        "coordinate_policy": "VTable APIs and rendered scenegraph only; no DOM guessing",
        "geometry_sources": {
            "cell": "VTable.getCellRelativeRect",
            "target": "vtable-scenegraph.globalAABBBounds",
        },
        "analysis": {"meta": meta, "columns": columns, "truncated": raw.get("truncated") or {}},
    }


async def vtable_analysis(
    *,
    max_columns: int = 20,
    sample_rows: int = 2,
    mode: str = "interactive",
    fields: list[str] | None = None,
    include_values: bool = False,
    visible_only: bool = True,
    table_index: int | None = None,
    frame: str | None = None,
) -> dict:
    """Serialize the current VTable's compact interaction model."""
    async with _action_lock:
        return await _vtable_analysis_impl(
            max_columns=max_columns,
            sample_rows=sample_rows,
            mode=mode,
            fields=fields,
            include_values=include_values,
            visible_only=visible_only,
            table_index=table_index,
            frame=frame,
        )
