"""VTable column interaction operations: reliable drag-to-reorder and divider drag-to-resize.

Ensures mouse coordinates accurately target:
1. Column reordering: initial mousedown strictly hits the column header TEXT content,
   avoiding any interactive icons (sort, filter, dropdown, freeze pin) that would
   intercept clicks.
2. Column resizing: initial cursor lands precisely on the column divider line (right boundary)
   and hovers to activate VTable's `col-resize` state before dragging.
"""

from __future__ import annotations

from typing import Any

from ...browser import (
    _action_lock,
    _current_page_impl,
    _frame_context_details,
    _frame_page_offset,
    _page_id,
    _page_viewport_size,
)
from ...mouse import _mouse_drag_impl
from .binding import ensure_cell_visible, ensure_vtable, resolve_frame, vtable_frame

REORDER_PROBE_JS = r"""
(args) => {
  const t = window._vtable;
  if (!t || !t.scenegraph) return { ok: false, reason: 'vtable-not-bound' };

  const canvas = t.canvas || document.querySelector('.vtable canvas') || document.querySelector('.vtable');
  if (!canvas) return { ok: false, reason: 'canvas-dead' };
  const cr = canvas.getBoundingClientRect();
  if (!cr || cr.width <= 0 || cr.height <= 0) return { ok: false, reason: 'canvas-dead' };

  const cols = (t.options && t.options.columns) || [];
  const fields = cols.map(c => String((c && (c.field || c.key)) || ''));
  const titles = cols.map((c, i) => {
    let title = String((c && (c.title || c.header || c.caption)) || '');
    if (!title && t.getCellValue) {
      try { title = String(t.getCellValue(i, 0) || ''); } catch (_) {}
    }
    return title;
  });

  const colCount = Math.max(Number(t.colCount || 0), cols.length);
  if (!colCount) return { ok: false, reason: 'no-columns' };

  let srcCol = -1;
  if (args.from_field) {
    srcCol = fields.indexOf(String(args.from_field));
  } else if (Number.isInteger(args.from_col)) {
    srcCol = Number(args.from_col);
  }

  let dstCol = -1;
  if (args.to_field) {
    dstCol = fields.indexOf(String(args.to_field));
  } else if (Number.isInteger(args.to_col)) {
    dstCol = Number(args.to_col);
  }

  if (srcCol < 0 || srcCol >= colCount) {
    return { ok: false, reason: 'source-column-not-found', srcCol, fields };
  }
  if (dstCol < 0 || dstCol >= colCount) {
    return { ok: false, reason: 'target-column-not-found', dstCol, fields };
  }
  if (srcCol === dstCol) {
    return { ok: true, status: 'already-in-place', srcCol, dstCol, fields };
  }

  const hRows = Math.max(1, Number(t.columnHeaderLevelCount ?? t.headerRowCount ?? 1));
  const hRow = hRows - 1;

  const pick = (...vals) => vals.find(v => v !== undefined && v !== null);
  const relativeBox = (c, r) => {
    try {
      const rect = t.getCellRelativeRect && t.getCellRelativeRect(c, r);
      if (!rect) return null;
      const left = Number(pick(rect.left, rect.x1, rect.bounds && rect.bounds.x1));
      const top = Number(pick(rect.top, rect.y1, rect.bounds && rect.bounds.y1));
      const right = Number(pick(rect.right, rect.x2, rect.bounds && rect.bounds.x2));
      const bottom = Number(pick(rect.bottom, rect.y2, rect.bounds && rect.bounds.y2));
      if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return null;
      return { box: { x: left, y: top, width: right - left, height: bottom - top }, center: { x: (left + right) / 2, y: (top + bottom) / 2 } };
    } catch (_) { return null; }
  };

  const childrenOf = (node) => {
    if (!node) return [];
    if (Array.isArray(node.children)) return node.children;
    try { const ch = node.getChildren && node.getChildren(); if (Array.isArray(ch)) return ch; } catch (_) {}
    const ch = [];
    try { if (typeof node.forEachChildren === 'function') node.forEachChildren(c => ch.push(c)); } catch (_) {}
    return ch;
  };

  const collectIcons = (cell) => {
    const queue = [{ node: cell, depth: 0 }], seen = new Set(), icons = [];
    let visited = 0;
    while (queue.length && visited < 128) {
      const item = queue.shift(), node = item.node;
      if (!node || seen.has(node) || item.depth > 6) continue;
      seen.add(node); visited++;
      if (item.depth > 0) {
        const attr = node.attribute || {};
        const type = String(node.type || '').toLowerCase();
        const cursor = String(attr.cursor || node.cursor || '').toLowerCase();
        const isIcon = (cursor === 'pointer') || ['button', 'checkbox', 'radio', 'switch'].includes(type) ||
                       (node.name && String(node.name).match(/icon|sort|filter|dropdown|freeze/i));
        const b = node.globalAABBBounds;
        if (isIcon && b) {
          const x1 = Number(b.x1), y1 = Number(b.y1), x2 = Number(b.x2), y2 = Number(b.y2);
          if ([x1, y1, x2, y2].every(Number.isFinite) && x2 > x1 && y2 > y1) {
            icons.push({ name: String(node.name || ''), box: { x: x1, y: y1, width: x2 - x1, height: y2 - y1 } });
          }
        }
      }
      for (const child of childrenOf(node)) queue.push({ node: child, depth: item.depth + 1 });
    }
    return icons;
  };

  let srcCell = null;
  try { srcCell = t.scenegraph.getCell(srcCol, hRow); } catch (_) {}
  const srcRel = relativeBox(srcCol, hRow);
  if (!srcRel) return { ok: false, reason: 'source-cell-geometry-unavailable' };

  const srcIcons = srcCell ? collectIcons(srcCell) : [];
  let srcTextNode = null;
  if (srcCell) {
    const queue = [{ node: srcCell, depth: 0 }], seen = new Set();
    let visited = 0;
    while (queue.length && visited < 128) {
      const item = queue.shift(), node = item.node;
      if (!node || seen.has(node) || item.depth > 6) continue;
      seen.add(node); visited++;
      if (item.depth > 0) {
        const attr = node.attribute || {};
        const txt = String(attr.text ?? node.text ?? '').trim();
        const type = String(node.type || '').toLowerCase();
        const b = node.globalAABBBounds;
        if ((type === 'text' || node.name === 'text' || txt) && b) {
          const x1 = Number(b.x1), y1 = Number(b.y1), x2 = Number(b.x2), y2 = Number(b.y2);
          if ([x1, y1, x2, y2].every(Number.isFinite) && x2 > x1 && y2 > y1) {
            srcTextNode = { text: txt, box: { x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, center: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 } };
            if (titles[srcCol] && txt.includes(titles[srcCol])) break;
          }
        }
      }
      for (const child of childrenOf(node)) queue.push({ node: child, depth: item.depth + 1 });
    }
  }

  let srcClickX, srcClickY;
  if (srcTextNode) {
    srcClickX = srcTextNode.center.x;
    srcClickY = srcTextNode.center.y;
  } else {
    let minIconX = srcRel.box.x + srcRel.box.width;
    for (const ic of srcIcons) {
      if (ic.box && ic.box.x < minIconX) minIconX = ic.box.x;
    }
    const safeLeft = srcRel.box.x + 8;
    const safeRight = Math.max(safeLeft + 16, minIconX - 8);
    srcClickX = (safeLeft + safeRight) / 2;
    srcClickY = srcRel.center.y;
  }

  for (const ic of srcIcons) {
    if (ic.box) {
      if (srcClickX >= ic.box.x - 4 && srcClickX <= ic.box.x + ic.box.width + 4) {
        srcClickX = Math.max(srcRel.box.x + 8, ic.box.x - 12);
      }
    }
  }

  const dstRel = relativeBox(dstCol, hRow);
  if (!dstRel) return { ok: false, reason: 'target-cell-geometry-unavailable' };

  let dstDropX;
  if (dstCol > srcCol) {
    dstDropX = dstRel.box.x + dstRel.box.width - Math.min(10, dstRel.box.width / 4);
  } else {
    dstDropX = dstRel.box.x + Math.min(10, dstRel.box.width / 4);
  }
  const dstDropY = dstRel.center.y;

  return {
    ok: true,
    canvas: { left: cr.left, top: cr.top, width: cr.width, height: cr.height },
    srcCol,
    dstCol,
    srcField: fields[srcCol],
    dstField: fields[dstCol],
    srcTitle: titles[srcCol],
    dstTitle: titles[dstCol],
    beforeFields: fields,
    start: { x: srcClickX, y: srcClickY },
    end: { x: dstDropX, y: dstDropY },
  };
}
"""

RESIZE_PROBE_JS = r"""
(args) => {
  const t = window._vtable;
  if (!t) return { ok: false, reason: 'vtable-not-bound' };

  const canvas = t.canvas || document.querySelector('.vtable canvas') || document.querySelector('.vtable');
  if (!canvas) return { ok: false, reason: 'canvas-dead' };
  const cr = canvas.getBoundingClientRect();
  if (!cr || cr.width <= 0 || cr.height <= 0) return { ok: false, reason: 'canvas-dead' };

  const cols = (t.options && t.options.columns) || [];
  const fields = cols.map(c => String((c && (c.field || c.key)) || ''));
  const colCount = Math.max(Number(t.colCount || 0), cols.length);

  let targetCol = -1;
  if (args.field) {
    targetCol = fields.indexOf(String(args.field));
  } else if (Number.isInteger(args.col)) {
    targetCol = Number(args.col);
  }

  if (targetCol < 0 || targetCol >= colCount) {
    return { ok: false, reason: 'column-not-found', targetCol, fields };
  }

  const hRows = Math.max(1, Number(t.columnHeaderLevelCount ?? t.headerRowCount ?? 1));
  const hRow = hRows - 1;

  const pick = (...vals) => vals.find(v => v !== undefined && v !== null);
  const rect = t.getCellRelativeRect ? t.getCellRelativeRect(targetCol, hRow) : null;
  if (!rect) return { ok: false, reason: 'cell-relative-rect-unavailable' };

  const left = Number(pick(rect.left, rect.x1, rect.bounds && rect.bounds.x1));
  const top = Number(pick(rect.top, rect.y1, rect.bounds && rect.bounds.y1));
  const right = Number(pick(rect.right, rect.x2, rect.bounds && rect.bounds.x2));
  const bottom = Number(pick(rect.bottom, rect.y2, rect.bounds && rect.bounds.y2));

  const colRight = right !== undefined ? right : (left + Number(rect.width || 0));
  const headerMidY = (top !== undefined && bottom !== undefined) ? (top + bottom) / 2 : cr.top + 14;

  let rightFrozenW = 0;
  try {
    rightFrozenW = (t.rightFrozenColCount && t.getRightFrozenColsWidth) ? t.getRightFrozenColsWidth() : 0;
  } catch (_) {}

  const isDraggable = (colRight < cr.width - rightFrozenW - 1);
  const curWidth = Math.round(Number(t.getColWidth(targetCol)) || 0);

  return {
    ok: true,
    canvas: { left: cr.left, top: cr.top, width: cr.width, height: cr.height },
    col: targetCol,
    field: fields[targetCol] || '',
    curWidth,
    border: { x: colRight, y: headerMidY, draggable: isDraggable },
  };
}
"""


async def _reorder_column_impl(
    *,
    from_col: int | None = None,
    to_col: int | None = None,
    from_field: str | None = None,
    to_field: str | None = None,
    frame: str | None = None,
    table_index: int | None = None,
) -> dict[str, Any]:
    """拖拽列改变其顺序。起始点击严格锁定在表头文本区，彻底规避排序/筛选/下拉图标。"""
    if from_col is None and not from_field:
        raise ValueError("reorder_column: 必须提供 from_col 或 from_field")
    if to_col is None and not to_field:
        raise ValueError("reorder_column: 必须提供 to_col 或 to_field")

    page = await _current_page_impl()
    frame_obj = (
        await resolve_frame(page, frame)
        if frame is not None
        else await vtable_frame(page)
    )

    try:
        await ensure_vtable(frame_obj, table_index)
    except Exception as exc:
        return {"status": "failed", "page_id": _page_id(page), "reason": f"vtable-not-bound: {exc}"}

    probe = await frame_obj.evaluate(
        REORDER_PROBE_JS,
        {
            "from_col": from_col,
            "to_col": to_col,
            "from_field": from_field,
            "to_field": to_field,
        },
    )
    if not probe or not probe.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"reorder-probe-failed: {(probe or {}).get('reason')}",
        }

    if probe.get("status") == "already-in-place":
        return {
            "status": "already-in-place",
            "page_id": _page_id(page),
            "col": probe["srcCol"],
            "fields": probe.get("fields", []),
        }

    src_col = probe["srcCol"]
    dst_col = probe["dstCol"]
    await ensure_cell_visible(page, frame_obj, src_col, 0)
    await ensure_cell_visible(page, frame_obj, dst_col, 0)

    # 滚动后重新探查一次坐标，保证视口绝对最新
    probe = await frame_obj.evaluate(
        REORDER_PROBE_JS,
        {"from_col": src_col, "to_col": dst_col},
    )
    if not probe or not probe.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"reorder-probe-after-scroll-failed: {(probe or {}).get('reason')}",
        }

    frame_offset = await _frame_page_offset(page, frame_obj)
    canvas = probe["canvas"]
    start_pt = probe["start"]
    end_pt = probe["end"]

    start_x = round(frame_offset["x"] + canvas["left"] + start_pt["x"], 2)
    start_y = round(frame_offset["y"] + canvas["top"] + start_pt["y"], 2)
    end_x = round(frame_offset["x"] + canvas["left"] + end_pt["x"], 2)
    end_y = round(frame_offset["y"] + canvas["top"] + end_pt["y"], 2)

    viewport = await _page_viewport_size(page)
    start_x = max(2.0, min(viewport["width"] - 2.0, start_x))
    start_y = max(2.0, min(viewport["height"] - 2.0, start_y))
    end_x = max(2.0, min(viewport["width"] - 2.0, end_x))
    end_y = max(2.0, min(viewport["height"] - 2.0, end_y))

    drag_result = await _mouse_drag_impl(
        page,
        start_x,
        start_y,
        end_x,
        end_y,
        steps=28,
        hold_ms=150,
        settle_ms=350,
    )

    # 检查重排后列顺序
    after_fields = await frame_obj.evaluate(
        "() => (window._vtable && window._vtable.options && window._vtable.options.columns || []).map(c => String(c.field || ''))"
    )
    before_fields = probe.get("beforeFields", [])
    order_changed = bool(after_fields and after_fields != before_fields)

    return {
        "status": "reordered" if order_changed else "unverified",
        "page_id": _page_id(page),
        "from_col": src_col,
        "to_col": dst_col,
        "from_field": probe.get("srcField"),
        "to_field": probe.get("dstField"),
        "from_title": probe.get("srcTitle"),
        "to_title": probe.get("dstTitle"),
        "start_point": {"x": start_x, "y": start_y},
        "end_point": {"x": end_x, "y": end_y},
        "before_fields": before_fields,
        "after_fields": after_fields,
        "drag": drag_result,
        "frame": await _frame_context_details(page, frame_obj),
    }


async def reorder_column(
    *,
    from_col: int | None = None,
    to_col: int | None = None,
    from_field: str | None = None,
    to_field: str | None = None,
    frame: str | None = None,
    table_index: int | None = None,
) -> dict[str, Any]:
    async with _action_lock:
        return await _reorder_column_impl(
            from_col=from_col,
            to_col=to_col,
            from_field=from_field,
            to_field=to_field,
            frame=frame,
            table_index=table_index,
        )


async def _resize_column_impl(
    *,
    col: int | None = None,
    field: str | None = None,
    target_width: int | float | None = None,
    delta_width: int | float | None = None,
    frame: str | None = None,
    table_index: int | None = None,
) -> dict[str, Any]:
    """拖拽列与列之间的分界线改变列宽。光标精确对齐分界线并提供悬停准备，避免误触列重排。"""
    if col is None and not field:
        raise ValueError("resize_column: 必须提供 col 或 field")
    if target_width is None and delta_width is None:
        raise ValueError("resize_column: 必须提供 target_width 或 delta_width")

    page = await _current_page_impl()
    frame_obj = (
        await resolve_frame(page, frame)
        if frame is not None
        else await vtable_frame(page)
    )

    try:
        await ensure_vtable(frame_obj, table_index)
    except Exception as exc:
        return {"status": "failed", "page_id": _page_id(page), "reason": f"vtable-not-bound: {exc}"}

    probe = await frame_obj.evaluate(
        RESIZE_PROBE_JS,
        {"col": col, "field": field},
    )
    if not probe or not probe.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"resize-probe-failed: {(probe or {}).get('reason')}",
        }

    target_col = probe["col"]
    cur_width = probe["curWidth"]

    if delta_width is not None:
        delta = float(delta_width)
    else:
        delta = float(target_width) - cur_width

    if abs(delta) <= 1.0:
        return {
            "status": "already-fit",
            "page_id": _page_id(page),
            "col": target_col,
            "field": probe.get("field"),
            "width": cur_width,
        }

    border = probe["border"]
    if not border.get("draggable"):
        return {
            "status": "skipped-frozen",
            "page_id": _page_id(page),
            "col": target_col,
            "field": probe.get("field"),
            "reason": "border-in-frozen-deadzone",
        }

    await ensure_cell_visible(page, frame_obj, target_col, 0)
    # 滚动后重探最新边框
    probe = await frame_obj.evaluate(
        RESIZE_PROBE_JS,
        {"col": target_col},
    )
    if not probe or not probe.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"resize-probe-after-scroll-failed: {(probe or {}).get('reason')}",
        }

    border = probe["border"]
    frame_offset = await _frame_page_offset(page, frame_obj)
    canvas = probe["canvas"]

    start_x = round(frame_offset["x"] + canvas["left"] + border["x"], 2)
    start_y = round(frame_offset["y"] + canvas["top"] + border["y"], 2)
    end_x = round(start_x + delta, 2)
    end_y = start_y

    viewport = await _page_viewport_size(page)
    start_x = max(2.0, min(viewport["width"] - 2.0, start_x))
    start_y = max(2.0, min(viewport["height"] - 2.0, start_y))
    end_x = max(2.0, min(viewport["width"] - 2.0, end_x))
    end_y = max(2.0, min(viewport["height"] - 2.0, end_y))

    drag_result = await _mouse_drag_impl(
        page,
        start_x,
        start_y,
        end_x,
        end_y,
        steps=28,
        hold_ms=150,
        settle_ms=350,
    )

    new_width = await frame_obj.evaluate(
        "([c]) => (window._vtable ? Math.round(Number(window._vtable.getColWidth(c)) || 0) : null)",
        [target_col],
    )

    width_changed = (new_width is not None) and (abs(new_width - cur_width) >= 1)

    return {
        "status": "resized" if width_changed else "unverified",
        "page_id": _page_id(page),
        "col": target_col,
        "field": probe.get("field"),
        "before_width": cur_width,
        "after_width": new_width,
        "delta_requested": delta,
        "delta_applied": (new_width - cur_width) if new_width is not None else 0,
        "separator_point": {"x": start_x, "y": start_y},
        "end_point": {"x": end_x, "y": end_y},
        "drag": drag_result,
        "frame": await _frame_context_details(page, frame_obj),
    }


async def resize_column(
    *,
    col: int | None = None,
    field: str | None = None,
    target_width: int | float | None = None,
    delta_width: int | float | None = None,
    frame: str | None = None,
    table_index: int | None = None,
) -> dict[str, Any]:
    async with _action_lock:
        return await _resize_column_impl(
            col=col,
            field=field,
            target_width=target_width,
            delta_width=delta_width,
            frame=frame,
            table_index=table_index,
        )
