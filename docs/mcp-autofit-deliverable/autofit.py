"""VTable 列宽自适应:计划生成 → trusted 拖拽 → 每步校验闭环(含列序熔断与僵尸态自愈)。

放置路径: Qa-Automation-MCP/qa_automation/components/vtable/autifit.py

设计对应《vtable-autofit-技术分析总结.md》4.5 节三条不变量:
- I1 坐标即时性:每次拖拽起点都来自上一次 probe 的实时边框坐标,绝不跨步缓存;
- I2 误差止步:单列误差(容差 2px)在本列重试消化,绝不带进下一列;
- I3 顺序先行:检测到列换序先修序、后调宽,绝不带伤推进。
"""

from __future__ import annotations

import math
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

# ── 闭环控制常量(2026-08-30 产线管理页实测校准) ──────────────────
TOLERANCE_PX = 2          # VTable 拖拽步进粒度内的误差视为命中
DRAG_STEPS = 28           # ≥28 步细密轨迹才能触发 VTable 调宽阈值
DRAG_HOLD_MS = 150
DRAG_SETTLE_MS = 350
DEFAULT_MIN_WIDTH = 60
ALLOWANCE_DEFAULT = 96    # 排序16 + 冻结22 + 筛选12 + 间距 + 2*8 内边距
ALLOWANCE_DROPDOWN = 116  # 上述 + 下拉图标12
ALLOWANCE_OPERATION = 40  # 仅筛选图标的操作列
TS_THRESHOLD = 1e12       # 毫秒时间戳判定阈值
DATE_RENDER_SAMPLE = "2026-08-23 10:23:45"  # 时间戳的渲染文本估测样本
MAX_DISPLAY_ROWS = 200    # getCellValue 扫描上限(性能保护)
MAX_RECORD_ROWS = 2000    # records 扫描上限

# ── 页面内求值脚本:一次返回 全量状态+测量计划(自适应重算的数据源) ──
# 坐标语义:getCellRelativeRect 为 canvas 相对坐标,脚本内已加 canvas rect
# 换算为 frame 本地视口坐标;Python 侧再叠加 _frame_page_offset 得到顶层坐标。
AUTOFIT_PROBE = r"""
(cfg) => {
  const t = window._vtable;
  if (!t || !t.scenegraph) return { ok: false, reason: 'vtable-gone' };

  // ① 僵尸态检测:画布必须真实渲染(非零可见矩形)
  const canvasEl = (t.canvas) || document.querySelector('.vtable canvas');
  if (!canvasEl) return { ok: false, reason: 'canvas-dead' };
  const cr = canvasEl.getBoundingClientRect();
  if (!cr || cr.width <= 0 || cr.height <= 0) return { ok: false, reason: 'canvas-dead' };

  const cols = (t.options && t.options.columns) || [];
  if (!cols.length) return { ok: false, reason: 'no-columns' };
  const recs = Array.isArray(t.records) ? t.records : [];

  // ② 文本测量基准(读主题,读不到退回实测默认)
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
    // 时间戳防御:records 里是原始数值,渲染另有格式化 → 按渲染样本估测
    if (typeof v === 'number' && v > 1e12) return '2026-08-23 10:23:45';
    return (v === null || v === undefined) ? '' : String(v);
  };

  // ③ 右冻结区总宽(边框拖拽禁区)
  let rightFrozenW = 0;
  const rfCount = Number(t.rightFrozenColCount || 0);
  for (let i = cols.length - rfCount; i < cols.length; i++) {
    try { rightFrozenW += t.getColWidth(i); } catch (e) { /* 防御 */ }
  }

  // ④ 表头垂直中线:取任一表头单元格实测(兜底画布顶+14)
  let headerMidY = cr.top + 14;
  try {
    const hr = t.getCellRelativeRect(Math.min(2, cols.length - 1), 0);
    if (hr && hr.height > 0) headerMidY = cr.top + hr.top + hr.height / 2;
  } catch (e) { /* 防御 */ }

  const dropdowns = new Set(cfg.dropdownFields || []);
  const items = [];
  for (let i = 0; i < cols.length; i++) {
    const cd = cols[i] || {};
    const field = String(cd.field || '');
    const title = String(cd.title || '');
    const width = Math.round(Number(t.getColWidth(i)) || 0);

    // 表头需要宽度:加粗标题 + 图标区经验值(操作列40/下拉列116/常规96)
    const isOp = field === '_op' || title === '操作';
    const allowance = isOp ? 40 : (dropdowns.has(field) ? 116 : 96);
    const headerNeed = Math.ceil(measure(title, boldFont)) + allowance;

    // 内容需要宽度:显示值(可见行)与 records 原始值取最大
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

    // 边框坐标(frame 本地):VTable 自身 API 取右缘,天然规避冻结/滚动/浮点误差
    let border = null;
    try {
      const rect = t.getCellRelativeRect(i, 0);
      const x = cr.left + rect.x + rect.width;
      border = {
        x: Math.round(x * 100) / 100,
        y: Math.round(headerMidY * 100) / 100,
        draggable: x < cr.right - rightFrozenW - 1,
      };
    } catch (e) { /* 边框不可得 → 该列跳过 */ }
    // 表头单元格中心(frame 本地,列序修复用:拖中心触发换序而非调宽)
    let center = null;
    try {
      const rect = t.getCellRelativeRect(i, 0);
      center = {
        x: Math.round((cr.left + rect.x + rect.width / 2) * 100) / 100,
        y: Math.round(headerMidY * 100) / 100,
      };
    } catch (e) { /* 防御 */ }

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
    fields: items.map(function (it) { return it.field; }),
    items: items,
  };
}
"""


def _abs_point(frame_offset: dict[str, float], point: dict[str, float]) -> dict[str, float]:
    """frame 本地坐标 → 顶层视口坐标(修复 vtable_analysis 漏加 iframe 偏移的陷阱)。"""
    return {
        "x": frame_offset["x"] + float(point["x"]),
        "y": frame_offset["y"] + float(point["y"]),
    }


async def _probe(frame: Frame, cfg: dict[str, Any]) -> dict[str, Any]:
    """每步拖拽前后的唯一数据源:一次求值拿宽度+列序+边框坐标+表头中线。"""
    try:
        raw = await frame.evaluate(AUTOFIT_PROBE, cfg)
    except Exception as exc:  # frame 已失效等 → 结构化收敛
        return {"ok": False, "reason": f"probe-evaluate-error: {exc}"}
    if not isinstance(raw, dict):
        return {"ok": False, "reason": f"probe-unexpected: {raw!r}"[:200]}
    return raw


def _find(items: list[dict[str, Any]], col: int) -> dict[str, Any] | None:
    return next((it for it in items if it["col"] == col), None)


def _order_changed(before: list[str], after: list[str]) -> bool:
    return list(before) != list(after)


def _first_order_diff(baseline: list[str], current: list[str]) -> tuple[int, str] | None:
    """定位第一个错位:返回 (期望索引, 应在该索引的 field)。"""
    for i, expected in enumerate(baseline):
        if i >= len(current) or current[i] != expected:
            return i, expected
    return None


class _AutofitError(Exception):
    """工具内部结构化错误,绝不让 MCP 进程崩溃。"""


async def _scroll_to_origin(frame: Frame) -> None:
    """横向滚回原点,保证边框坐标落在可视区。"""
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
    """列序熔断修复:把误移列的表头【单元格中心】拖回期望位置(拖中心=换序)。"""
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
        page, s["x"], s["y"], d["x"], d["y"],
        steps=DRAG_STEPS, hold_ms=200, settle_ms=400,
    )
    steps_log.append({
        "action": "order-restore", "field": expect_field,
        "from_col": from_idx, "to_col": expect_idx, "from": s, "to": d,
    })
    verify = await _probe(frame, {"dropdownFields": [], "maxDisplayRows": 1, "maxRecordRows": 1})
    if not verify.get("ok"):
        raise _AutofitError(f"order-restore-verify-failed: {verify.get('reason')}")
    return verify["fields"]


async def _heal_and_restart(
    page: Page, frame_hint: str | None, reason: str
) -> tuple[Page, Frame]:
    """僵尸态自愈:Page.reload → 等 .vtable 可见 → 重新绑定实例(仅允许一次)。"""
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
) -> dict:
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
        # ── 阶段1:全量状态采集 + 计划生成(单次页面内求值) ──
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

        # ── 阶段2:目标宽度与执行计划 ──
        plan: list[dict[str, Any]] = []
        for it in state["items"]:
            field, cur = it["field"], it["width"]
            internal = field.startswith("_vtable_") or not field
            basis_need = (
                it["headerNeed"]
                if normalized_mode == "header"
                else (it["bodyNeed"] if normalized_mode == "content" else max(it["headerNeed"], it["bodyNeed"]))
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
                "col": it["col"], "field": field, "title": it["title"],
                "before": cur, "target": target, "basis": basis,
                "sample": it["sample"], "border": it.get("border"),
                "center": it.get("center"), "status": status,
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

        # ── 阶段3:串行执行(从右往左;右侧先定型,左侧边框不受影响) ──
        pending = [p for p in plan if p["status"] == "pending"]
        for item in sorted(pending, key=lambda p: p["col"], reverse=True):
            col, target = item["col"], item["target"]
            attempts = 0
            cur_item = _find(state["items"], col)

            while attempts < max(1, max_retries):
                attempts += 1
                # I1 坐标即时性:起点 = probe 实时边框,终点 = 起点 + 实时差值
                border = (cur_item or {}).get("border")
                if not border or not border.get("draggable"):
                    item["status"] = "skipped-frozen"
                    break
                cur_width = cur_item["width"]
                delta = target - cur_width
                start = _abs_point(frame_offset, border)
                end = {"x": start["x"] + delta, "y": start["y"]}
                clamped = False  # 视口边界钳制:终点越界时拖到极限并标记
                if end["x"] >= viewport["width"] - 1:
                    end["x"] = viewport["width"] - 2
                    clamped = True

                try:
                    await _mouse_drag_impl(
                        page, start["x"], start["y"], end["x"], end["y"],
                        steps=DRAG_STEPS, hold_ms=DRAG_HOLD_MS, settle_ms=DRAG_SETTLE_MS,
                    )
                except Exception as exc:
                    steps_log.append({
                        "col": col, "field": item["field"], "action": "drag",
                        "status": "error", "reason": str(exc)[:160],
                        "from": start, "to": end, "retries": attempts,
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
                        "col": col, "action": "probe", "status": "error",
                        "reason": after.get("reason"),
                    })
                    break

                # I3 顺序先行:列序被误动 → 立即修复,再以新布局重读本列
                if _order_changed(state["fields"], after["fields"]):
                    try:
                        fixed = await _restore_column_order(
                            page, frame_obj, frame_offset,
                            baseline_fields, after["fields"], steps_log,
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
                    "col": col, "field": item["field"], "action": "drag",
                    "status": "dragged", "from": start, "to": end,
                    "width_before": cur_width,
                    "width_after": (new_item or {}).get("width"),
                    "clamped": clamped, "retries": attempts,
                })
                state = after

                # I2 误差止步:命中容差 → 下一列;否则用最新状态重算差值重试
                if new_item and abs(new_item["width"] - target) <= TOLERANCE_PX:
                    item["status"] = "ok"
                    item["after"] = new_item["width"]
                    break
                cur_item = new_item
            else:
                # 重试耗尽:记录 degraded,误差止步于此列,继续后续列
                if item.get("status") in (None, "pending"):
                    item["status"] = "degraded-unreachable"
                item.setdefault("after", (cur_item or {}).get("width"))

        # ── 阶段4:终检(全列宽度 + 列序完整性) ──
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
    except Exception as exc:  # 结构化收敛,绝不让 MCP 进程崩溃
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
) -> dict:
    """公开入口:与既有工具一致,全程持有 _action_lock 串行化。"""
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
