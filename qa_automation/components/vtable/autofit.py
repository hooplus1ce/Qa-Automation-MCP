"""VTable 列宽自适应:计划生成 → trusted 拖拽 → 每步校验闭环(含列序熔断与僵尸态自愈)。

设计对应三条不变量:
- I1 坐标即时性: 每次拖拽起点都来自上一次 probe 的实时边框坐标, 绝不跨步缓存;
- I2 误差止步: 单列误差(容差 2px)在本列重试消化, 绝不带进下一列;
- I3 顺序先行: 检测到列换序先修序、后调宽, 且拖拽起点严格命中表头文本避开图标。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ...browser import (
    _action_lock,
    _current_page_impl,
    _frame_context_details,
    _frame_page_offset,
    _page_id,
    _page_viewport_size,
)
from ...config import BIND_TIMEOUT_MS
from ...mouse import _mouse_drag_impl
from .binding import ensure_vtable, resolve_frame, vtable_frame

TOLERANCE_PX = 2
DRAG_STEPS = 28
DRAG_HOLD_MS = 150
DRAG_SETTLE_MS = 350
DEFAULT_MIN_WIDTH = 60
ALLOWANCE_DEFAULT = 96
ALLOWANCE_DROPDOWN = 116
ALLOWANCE_OPERATION = 40
TS_THRESHOLD = 1e12
DATE_RENDER_SAMPLE = "2026-08-23 10:23:45"
MAX_DISPLAY_ROWS = 200
MAX_RECORD_ROWS = 2000

AUTOFIT_PROBE = r"""
(cfg) => {
  const t = window._vtable;
  if (!t || !t.scenegraph) return { ok: false, reason: 'vtable-gone' };

  const canvasEl = (t.canvas) || document.querySelector('.vtable canvas');
  if (!canvasEl) return { ok: false, reason: 'canvas-dead' };
  const cr = canvasEl.getBoundingClientRect();
  if (!cr || cr.width <= 0 || cr.height <= 0) return { ok: false, reason: 'canvas-dead' };

  const cols = (t.options && t.options.columns) || [];
  if (!cols.length) return { ok: false, reason: 'no-columns' };
  const recs = Array.isArray(t.records) ? t.records : [];

  const theme = t.theme || {};
  const hStyle = (theme.headerStyle || theme._header || {});
  const bStyle = (theme.bodyStyle || theme._body || {});
  const fontSize = (hStyle.fontSize || 12);
  const fontFamily = (hStyle.fontFamily || 'Arial,sans-serif');
  const cv = document.createElement('canvas');
  const ctx = cv.getContext('2d');
  const boldFont = '600 ' + fontSize + 'px ' + fontFamily;
  const bodyFont = (bStyle.fontSize || fontSize) + 'px ' + (bStyle.fontFamily || fontFamily);

  const measure = (s, font) => { ctx.font = font; return ctx.measureText(String(s)).width; };
  const textOf = (v) => {
    if (typeof v === 'number' && v > 1e12) return '2026-08-23 10:23:45';
    return (v === null || v === undefined) ? '' : String(v);
  };

  let rightFrozenW = 0;
  const rfCount = Number(t.rightFrozenColCount || 0);
  for (let i = cols.length - rfCount; i < cols.length; i++) {
    try { rightFrozenW += t.getColWidth(i); } catch (e) {}
  }

  let headerMidY = cr.top + 14;
  try {
    const hr = t.getCellRelativeRect(Math.min(2, cols.length - 1), 0);
    if (hr && hr.height > 0) {
      const top = (hr.top !== undefined) ? hr.top : (hr.y1 || 0);
      const height = (hr.height !== undefined) ? hr.height : ((hr.bottom || 0) - top);
      headerMidY = cr.top + top + height / 2;
    }
  } catch (e) {}

  const dropdowns = new Set(cfg.dropdownFields || []);
  const items = [];
  const pick = (...vals) => vals.find(v => v !== undefined && v !== null);

  for (let i = 0; i < cols.length; i++) {
    const cd = cols[i] || {};
    const field = String(cd.field || cd.key || '');
    const title = String(cd.title || cd.header || cd.caption || '');
    const width = Math.round(Number(t.getColWidth(i)) || 0);

    const isOp = field === '_op' || title === '操作';
    const allowance = isOp ? 40 : (dropdowns.has(field) ? 116 : 96);
    const headerNeed = Math.ceil(measure(title, boldFont)) + allowance;

    let bodyW = 0, sample = '';
    const rowsTotal = Number(t.rowCount || 0);
    const rowCap = Math.min(rowsTotal, 1 + cfg.maxDisplayRows);
    for (let r = 1; r < rowCap; r++) {
      let v; try { v = t.getCellValue(i, r); } catch (e) { break; }
      const s = textOf(v); if (!s) continue;
      const w = measure(s, bodyFont);
      if (w > bodyW) { bodyW = w; sample = s; }
    }
    const recCap = Math.min(recs.length, cfg.maxRecordRows);
    for (let r = 0; r < recCap; r++) {
      const s = textOf((recs[r] || {})[field]); if (!s) continue;
      const w = measure(s, bodyFont);
      if (w > bodyW) { bodyW = w; sample = s; }
    }
    const bodyNeed = Math.ceil(bodyW) + 16;

    let border = null;
    try {
      const rect = t.getCellRelativeRect(i, 0);
      const left = Number(pick(rect.left, rect.x1, rect.bounds && rect.bounds.x1));
      const right = Number(pick(rect.right, rect.x2, rect.bounds && rect.bounds.x2));
      const colRight = (right !== undefined && !Number.isNaN(right)) ? right : (left + Number(rect.width || 0));
      const x = cr.left + colRight;
      border = {
        x: Math.round(x * 100) / 100,
        y: Math.round(headerMidY * 100) / 100,
        draggable: x < cr.right - rightFrozenW - 1,
      };
    } catch (e) {}

    let center = null;
    try {
      const cell = t.scenegraph ? t.scenegraph.getCell(i, 0) : null;
      let textClickX = null;
      if (cell) {
        const queue = [{ node: cell, depth: 0 }], seen = new Set();
        let visited = 0;
        while (queue.length && visited < 64) {
          const it = queue.shift(), node = it.node;
          if (!node || seen.has(node) || it.depth > 5) continue;
          seen.add(node); visited++;
          const attr = node.attribute || {};
          const txt = String(attr.text ?? node.text ?? '').trim();
          const type = String(node.type || '').toLowerCase();
          const b = node.globalAABBBounds;
          if ((type === 'text' || node.name === 'text' || txt) && b) {
            const x1 = Number(b.x1), x2 = Number(b.x2);
            if (Number.isFinite(x1) && Number.isFinite(x2) && x2 > x1) {
              textClickX = cr.left + (x1 + x2) / 2;
              break;
            }
          }
          if (Array.isArray(node.children)) {
            for (const child of node.children) queue.push({ node: child, depth: it.depth + 1 });
          }
        }
      }
      if (textClickX === null) {
        const rect = t.getCellRelativeRect(i, 0);
        const left = Number(pick(rect.left, rect.x1, rect.bounds && rect.bounds.x1));
        const w = Number(rect.width || 60);
        textClickX = cr.left + (left || 0) + Math.min(24, (w || 60) / 3);
      }
      center = {
        x: Math.round(textClickX * 100) / 100,
        y: Math.round(headerMidY * 100) / 100,
      };
    } catch (e) {}

    items.push({
      col: i, field: field, title: title, width: width,
      headerNeed: headerNeed, bodyNeed: bodyNeed, allowance: allowance,
      sample: sample.slice(0, 24), border: border, center: center,
    });
  }
  return {
    ok: true,
    canvas: { left: cr.left, top: cr.top, right: cr.right, width: cr.width },
    rightFrozenW: Math.round(rightFrozenW),
    scrollLeft: Number(t.scrollLeft || 0),
    fields: items.map(it => it.field),
    items: items,
  };
}
"""


def _abs_point(frame_offset: dict[str, float], point: dict[str, float]) -> dict[str, float]:
    return {
        "x": frame_offset["x"] + float(point["x"]),
        "y": frame_offset["y"] + float(point["y"]),
    }


async def _probe(frame: Frame, cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = await frame.evaluate(AUTOFIT_PROBE, cfg)
    except Exception as exc:
        return {"ok": False, "reason": f"probe-evaluate-error: {exc}"}
    if not isinstance(raw, dict):
        return {"ok": False, "reason": f"probe-unexpected: {raw!r}"[:200]}
    return raw


def _find(items: list[dict[str, Any]], col: int) -> dict[str, Any] | None:
    return next((it for it in items if it["col"] == col), None)


def _order_changed(before: list[str], after: list[str]) -> bool:
    return list(before) != list(after)


def _first_order_diff(baseline: list[str], current: list[str]) -> tuple[int, str] | None:
    for i, expected in enumerate(baseline):
        if i >= len(current) or current[i] != expected:
            return i, expected
    return None


class _AutofitError(Exception):
    pass


async def _scroll_to_origin(frame: Frame) -> None:
    try:
        await frame.evaluate(
            "() => { const t = window._vtable;"
            " if (t && t.scrollToCell) t.scrollToCell({col: 0, row: 0}); return true; }"
        )
        await frame.wait_for_timeout(120)
    except Exception:
        pass


async def _restore_column_order(
    page: Page,
    frame: Frame,
    frame_offset: dict[str, float],
    baseline: list[str],
    current: list[str],
    steps_log: list[dict[str, Any]],
) -> list[str]:
    diff = _first_order_diff(baseline, current)
    if diff is None:
        return current
    expect_idx, expect_field = diff
    if expect_field not in current:
        raise _AutofitError(f"order-restore-field-missing: {expect_field!r}")
    from_idx = current.index(expect_field)

    probe = await _probe(frame, {"dropdownFields": [], "maxDisplayRows": 1, "maxRecordRows": 1})
    if not probe.get("ok"):
        raise _AutofitError(f"order-restore-probe-failed: {probe.get('reason')}")
    src = _find(probe["items"], from_idx)
    dst = _find(probe["items"], expect_idx)
    if not src or not dst or not src.get("center") or not dst.get("center"):
        raise _AutofitError("order-restore-center-unavailable")

    s = _abs_point(frame_offset, src["center"])
    d = _abs_point(frame_offset, dst["center"])
    await _mouse_drag_impl(
        page,
        s["x"],
        s["y"],
        d["x"],
        d["y"],
        steps=DRAG_STEPS,
        hold_ms=200,
        settle_ms=400,
    )
    steps_log.append({
        "action": "order-restore",
        "field": expect_field,
        "from_col": from_idx,
        "to_col": expect_idx,
        "from": s,
        "to": d,
    })
    verify = await _probe(frame, {"dropdownFields": [], "maxDisplayRows": 1, "maxRecordRows": 1})
    if not verify.get("ok"):
        raise _AutofitError(f"order-restore-verify-failed: {verify.get('reason')}")
    return verify["fields"]


async def _heal_and_restart(
    page: Page, frame_hint: str | None, reason: str
) -> tuple[Page, Frame]:
    await page.reload()
    frame = (
        await resolve_frame(page, frame_hint)
        if frame_hint
        else await vtable_frame(page)
    )
    await frame.wait_for_selector(".vtable", timeout=BIND_TIMEOUT_MS)
    await frame.wait_for_timeout(500)
    await ensure_vtable(frame)
    return page, frame


async def _autofit_columns_impl(
    *,
    frame: str | None = None,
    mode: str = "both",
    columns: list[str] | None = None,
    dropdown_fields: list[str] | None = None,
    extra_padding: int = 0,
    min_width: int = DEFAULT_MIN_WIDTH,
    dry_run: bool = False,
    max_retries: int = 2,
) -> dict[str, Any]:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"header", "content", "both"}:
        return {"status": "failed", "reason": "mode must be header, content or both"}
    cfg = {
        "dropdownFields": [str(f) for f in (dropdown_fields or [])],
        "maxDisplayRows": MAX_DISPLAY_ROWS,
        "maxRecordRows": MAX_RECORD_ROWS,
    }
    page = await _current_page_impl()
    frame_hint = frame
    try:
        frame_obj = (
            await resolve_frame(page, frame)
            if frame is not None
            else await vtable_frame(page)
        )
        await ensure_vtable(frame_obj)
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-not-bound: {exc}",
        }

    steps_log: list[dict[str, Any]] = []
    healed = False
    try:
        state = await _probe(frame_obj, cfg)
        if not state.get("ok") and state.get("reason") == "canvas-dead" and not healed:
            healed = True
            page, frame_obj = await _heal_and_restart(page, frame_hint, state["reason"])
            state = await _probe(frame_obj, cfg)
        if not state.get("ok"):
            return {
                "status": "failed",
                "page_id": _page_id(page),
                "reason": f"probe-failed: {state.get('reason')}",
            }

        if state.get("scrollLeft", 0):
            await _scroll_to_origin(frame_obj)
            state = await _probe(frame_obj, cfg)
            if not state.get("ok"):
                return {
                    "status": "failed",
                    "page_id": _page_id(page),
                    "reason": f"probe-failed-after-scroll: {state.get('reason')}",
                }

        baseline_fields: list[str] = list(state["fields"])
        frame_offset = await _frame_page_offset(page, frame_obj)
        viewport = await _page_viewport_size(page)

        plan: list[dict[str, Any]] = []
        for it in state["items"]:
            field, cur = it["field"], it["width"]
            internal = field.startswith("_vtable_") or not field
            basis_need = (
                it["headerNeed"]
                if normalized_mode == "header"
                else (
                    it["bodyNeed"]
                    if normalized_mode == "content"
                    else max(it["headerNeed"], it["bodyNeed"])
                )
            )
            target = max(basis_need + extra_padding, min_width)
            basis = (
                ("header" if it["headerNeed"] >= it["bodyNeed"] else "content")
                if normalized_mode == "both"
                else normalized_mode
            )
            if internal:
                status = "skipped-internal"
            elif columns is not None and field not in columns:
                status = "skipped-unselected"
            elif abs(cur - target) <= TOLERANCE_PX:
                status = "already-fit"
            elif not (it.get("border") or {}).get("draggable", False):
                status = "skipped-frozen"
            else:
                status = "pending"
            plan.append({
                "col": it["col"],
                "field": field,
                "title": it["title"],
                "before": cur,
                "target": target,
                "basis": basis,
                "sample": it["sample"],
                "border": it.get("border"),
                "center": it.get("center"),
                "status": status,
            })

        if dry_run:
            return {
                "status": "dry-run",
                "page_id": _page_id(page),
                "frame": await _frame_context_details(page, frame_obj),
                "baseline_fields": baseline_fields,
                "right_frozen_width": state.get("rightFrozenW"),
                "plan": [{k: v for k, v in p.items() if k != "center"} for p in plan],
            }

        pending = [p for p in plan if p["status"] == "pending"]
        for item in sorted(pending, key=lambda p: p["col"], reverse=True):
            col, target = item["col"], item["target"]
            attempts = 0
            cur_item = _find(state["items"], col)

            while attempts < max(1, max_retries):
                attempts += 1
                border = (cur_item or {}).get("border")
                if not border or not border.get("draggable"):
                    item["status"] = "skipped-frozen"
                    break
                cur_width = cur_item["width"]
                delta = target - cur_width
                start = _abs_point(frame_offset, border)
                end = {"x": start["x"] + delta, "y": start["y"]}
                clamped = False
                if end["x"] >= viewport["width"] - 1:
                    end["x"] = viewport["width"] - 2
                    clamped = True

                try:
                    await _mouse_drag_impl(
                        page,
                        start["x"],
                        start["y"],
                        end["x"],
                        end["y"],
                        steps=DRAG_STEPS,
                        hold_ms=DRAG_HOLD_MS,
                        settle_ms=DRAG_SETTLE_MS,
                    )
                except Exception as exc:
                    steps_log.append({
                        "col": col,
                        "field": item["field"],
                        "action": "drag",
                        "status": "error",
                        "reason": str(exc)[:160],
                        "from": start,
                        "to": end,
                        "retries": attempts,
                    })
                    item["status"] = "degraded-drag-error"
                    break

                after = await _probe(frame_obj, cfg)
                if (
                    not after.get("ok")
                    and after.get("reason") == "canvas-dead"
                    and not healed
                ):
                    healed = True
                    page, frame_obj = await _heal_and_restart(page, frame_hint, after["reason"])
                    after = await _probe(frame_obj, cfg)
                    frame_offset = await _frame_page_offset(page, frame_obj)
                if not after.get("ok"):
                    item["status"] = "failed-probe-after-drag"
                    steps_log.append({
                        "col": col,
                        "action": "probe",
                        "status": "error",
                        "reason": after.get("reason"),
                    })
                    break

                if _order_changed(state["fields"], after["fields"]):
                    try:
                        fixed = await _restore_column_order(
                            page,
                            frame_obj,
                            frame_offset,
                            baseline_fields,
                            after["fields"],
                            steps_log,
                        )
                    except _AutofitError as exc:
                        return {
                            "status": "failed",
                            "page_id": _page_id(page),
                            "reason": str(exc),
                            "steps": steps_log,
                            "plan": plan,
                        }
                    if _order_changed(baseline_fields, fixed):
                        item["status"] = "failed-order-restore"
                        break
                    after = await _probe(frame_obj, cfg)
                    if not after.get("ok"):
                        item["status"] = "failed-probe-after-restore"
                        break

                new_item = _find(after["items"], col)
                steps_log.append({
                    "col": col,
                    "field": item["field"],
                    "action": "drag",
                    "status": "dragged",
                    "from": start,
                    "to": end,
                    "width_before": cur_width,
                    "width_after": (new_item or {}).get("width"),
                    "clamped": clamped,
                    "retries": attempts,
                })
                state = after

                if new_item and abs(new_item["width"] - target) <= TOLERANCE_PX:
                    item["status"] = "ok"
                    item["after"] = new_item["width"]
                    break
                cur_item = new_item
            else:
                if item.get("status") in (None, "pending"):
                    item["status"] = "degraded-unreachable"
                item.setdefault("after", (cur_item or {}).get("width"))

        final = await _probe(frame_obj, cfg)
        final_ok = final.get("ok") is True
        final_widths = (
            {it["field"]: it["width"] for it in (final.get("items") or [])}
            if final_ok
            else {}
        )
        order_intact = bool(final_ok) and not _order_changed(
            baseline_fields, final.get("fields") or []
        )

        for p in plan:
            if p["status"] == "ok" and p["field"] in final_widths:
                p["after"] = final_widths[p["field"]]
            elif p["status"] == "pending":
                p["status"] = "ok" if p["field"] in final_widths else "degraded-unreachable"

        adjusted = sum(1 for p in plan if p["status"] == "ok")
        skipped = sum(1 for p in plan if p["status"].startswith("skipped"))
        degraded = [
            p["field"] for p in plan if p["status"].startswith(("degraded", "failed"))
        ]

        return {
            "status": (
                "ok"
                if not degraded and order_intact
                else ("partial" if adjusted else "failed")
            ),
            "page_id": _page_id(page),
            "frame": await _frame_context_details(page, frame_obj),
            "adjusted": adjusted,
            "skipped": skipped,
            "order_intact": order_intact,
            "degraded": degraded,
            "right_frozen_width": state.get("rightFrozenW"),
            "columns": [
                {k: v for k, v in p.items() if k not in ("border", "center")}
                for p in plan
            ],
            "steps": steps_log,
        }
    except _AutofitError as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": str(exc),
            "steps": steps_log,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"autofit-error: {exc}",
            "steps": steps_log,
        }


async def autofit_columns(
    *,
    frame: str | None = None,
    mode: str = "both",
    columns: list[str] | None = None,
    dropdown_fields: list[str] | None = None,
    extra_padding: int = 0,
    min_width: int = DEFAULT_MIN_WIDTH,
    dry_run: bool = False,
    max_retries: int = 2,
) -> dict[str, Any]:
    async with _action_lock:
        return await _autofit_columns_impl(
            frame=frame,
            mode=mode,
            columns=columns,
            dropdown_fields=dropdown_fields,
            extra_padding=extra_padding,
            min_width=min_width,
            dry_run=dry_run,
            max_retries=max_retries,
        )
