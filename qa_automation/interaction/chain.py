"""Compact interaction chain engine: batch-execute UI actions in one round trip.

The chain layer turns N MCP round trips into one: a compact ``actions`` list is
executed sequentially by the public facade primitives (``dom_interact``,
``mouse_drag``, ``click_cell``, ``click_vtable_cell_by_field``), each result
trimmed to a small evidence summary. Planning stays with the client AI: when no
``actions`` are supplied, the tool returns a fresh compact page analysis so the
AI can hand back explicit actions on the next call. No server-side LLM calls.

This module deliberately has no ``fastmcp`` dependency; it is wired up by
:mod:`qa_automation.mcp.servers.chain`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import qa_automation as automation

ACTION_KINDS = frozenset(
    {
        "click",
        "dblclick",
        "rightclick",
        "hover",
        "fill",
        "type",
        "press",
        "check",
        "uncheck",
        "select",
        "drag",
        "cell_click",
        "cell_click_field",
        "wait",
    }
)

#: Locator fields forwarded verbatim to ``dom_interact``; unknown keys never leak.
_LOCATOR_KEYS = (
    "role",
    "name",
    "description",
    "css",
    "xpath",
    "text",
    "placeholder",
    "x",
    "y",
    "analysis_id",
)

#: Optional value/key plumbing for fill/type/press plus frame steering.
_DOM_EXTRA_KEYS = ("value", "key", "frame", "timeout_ms", "expect_input", "in_iframe")

_SUCCESS_STATUSES = frozenset({"acted", "clicked", "ok", "selected", "dragged", "waited"})


async def analyze_page_compact() -> dict:
    """Single-pass compact page analysis for client-side planning.

    Combines page context, the actionable control list, and the VTable catalog
    in one gather. A failing VTable scan degrades to ``{"tables": []}`` and never
    breaks the analysis.
    """
    async def _vtable_catalog() -> dict:
        try:
            return await automation.vtable_analysis(
                mode="interactive", max_columns=12, sample_rows=1
            )
        except Exception:
            return {"tables": []}

    page_ctx, controls, vtable = await asyncio.gather(
        automation.page_context(max_results=10),
        automation.analyze_scope(max_controls=40, max_overlays=10),
        _vtable_catalog(),
    )
    return {
        "page_context": page_ctx,
        "controls": controls,
        "vtable": vtable,
    }


def _trim_result(action: str, raw: dict) -> dict:
    """Shrink a primitive result to the stable chain summary.

    Keeps status, ok, resolved target/locator/point, evidence_count and error;
    drops overlay dumps, before/after state and other verbose payloads.
    """
    status = str(raw.get("status") or "")
    ok = status in _SUCCESS_STATUSES
    result: dict[str, Any] = {"action": action, "ok": ok, "status": status or "unknown"}
    interaction = raw.get("interaction") or {}
    if isinstance(interaction, dict):
        target = interaction.get("target") or {}
        if isinstance(target, dict):
            clean = {k: v for k, v in target.items() if v is not None}
            if clean:
                result["target"] = clean
        locator = interaction.get("locator")
        if isinstance(locator, dict) and locator:
            result["locator"] = locator
        if interaction.get("coordinate"):
            result["point"] = interaction.get("coordinate")
        confidence = interaction.get("confidence")
        if confidence:
            result["confidence"] = confidence
        evidence = interaction.get("evidence") or []
        if evidence:
            result["evidence_count"] = len(evidence)
    if raw.get("point") and not result.get("point"):
        result["point"] = raw.get("point")
    if raw.get("reason") and not ok:
        result["error"] = str(raw["reason"])[:500]
    elif not ok and raw.get("error") and not result.get("error"):
        result["error"] = str(raw["error"])[:500]
    if action == "wait" and "ms" in raw:
        result["ms"] = raw["ms"]
    elif action == "wait":
        result["ms"] = 0
    return result


def _failed(action: str, error: str) -> dict:
    return {"action": action, "ok": False, "status": "failed", "error": error}


def _locator_kwargs(item: dict) -> dict:
    kwargs = {k: item[k] for k in _LOCATOR_KEYS if item.get(k) is not None}
    for key in _DOM_EXTRA_KEYS:
        if item.get(key) is not None:
            kwargs[key] = item[key]
    return kwargs


async def _page_url() -> str | None:
    try:
        page = await automation.current_page()
        return page.url
    except Exception:
        return None


def _trim_overlay(item: dict) -> dict:
    return {
        "kind": item.get("kind"),
        "text": item.get("text"),
        "visible": bool(item.get("visible", True)),
    }


async def _chain_observation(url_before: str | None) -> dict:
    """链尾统一观察一次:极速等待后扫描浮层 + URL 变化(对齐参考实现)。"""
    await asyncio.sleep(0.15)
    overlays: list[dict] = []
    try:
        res = await automation.scan_overlays(max_results=15)
        overlays = [
            _trim_overlay(item)
            for item in (res.get("overlays") or [])
            if isinstance(item, dict)
        ]
    except Exception:
        pass
    url_after = await _page_url()
    return {
        "url_before": url_before,
        "url": url_after,
        "url_changed": bool(url_before and url_after and url_before != url_after),
        "overlays": overlays,
    }


async def _execute_one(action: str, item: dict) -> dict:
    if action == "wait":
        ms = max(0, min(int(item.get("ms", 100)), 30_000))
        await asyncio.sleep(ms / 1000)
        return {"action": "wait", "ok": True, "status": "waited", "ms": ms}

    if action == "drag":
        required = ("start_x", "start_y", "end_x", "end_y")
        missing = [name for name in required if item.get(name) is None]
        if missing:
            return _failed(action, f"missing required coordinates: {', '.join(missing)}")
        raw = await automation.mouse_drag(
            float(item["start_x"]),
            float(item["start_y"]),
            float(item["end_x"]),
            float(item["end_y"]),
            steps=int(item.get("steps", 24)),
            button=str(item.get("button", "left")),
            hold_ms=int(item.get("hold_ms", 80)),
            observe_after=False,
        )
        return _trim_result(action, raw)

    if action == "cell_click":
        try:
            col = int(item["col"])
            row = int(item["row"])
        except (KeyError, TypeError, ValueError):
            return _failed(action, "cell_click requires integer col and row")
        raw = await automation.click_cell(
            col,
            row,
            double_click=bool(item.get("double_click", False)),
            button=str(item.get("button", "left")),
            verify=bool(item.get("verify", True)),
            observe_after=False,
        )
        return _trim_result(action, raw)

    if action == "cell_click_field":
        field = item.get("field")
        if not isinstance(field, str) or not field:
            return _failed(action, "cell_click_field requires a string field")
        record_index = item.get("record_index", 0)
        if not isinstance(record_index, int) and not (
            isinstance(record_index, list)
            and all(isinstance(i, int) for i in record_index)
        ):
            return _failed(action, "cell_click_field requires integer record_index")
        raw = await automation.click_vtable_cell_by_field(
            field,
            record_index,
            double_click=bool(item.get("double_click", False)),
            button=str(item.get("button", "left")),
            verify=bool(item.get("verify", True)),
            observe_after=False,
        )
        return _trim_result(action, raw)

    # Generic DOM actions via dom_interact; coordinate fallback is delegated
    # inside the primitive (click/dblclick/rightclick/hover/fill/type support it).
    kwargs = _locator_kwargs(item)
    raw = await automation.dom_interact(
        action, observe_after=False, settle_ms=200, **kwargs
    )
    return _trim_result(action, raw)


async def execute_chain(
    actions: list[dict],
    *,
    stop_on_error: bool = True,
    max_actions: int = 10,
    step_timeout_ms: int = 5_000,
) -> dict:
    """Execute batched actions sequentially against the live page.

    Each step is hard-bounded by ``step_timeout_ms`` (0 disables); after the
    chain, one unifying observation records visible overlays and URL change
    (sleep 150ms, then scan). Returns ``{"results": [...], "executed": n,
    "truncated": bool, "observation": {...}}`` where ``executed`` counts
    successful actions. With ``stop_on_error=True`` the first failure stops the
    chain; with ``False`` remaining actions still run.
    """
    if not isinstance(actions, list):
        raise ValueError("actions must be a list of dicts")
    max_actions = max(1, min(int(max_actions), 100))
    step_timeout_ms = max(0, int(step_timeout_ms or 0))
    truncated = len(actions) > max_actions
    url_before = await _page_url()
    results: list[dict] = []
    executed = 0
    for raw_item in actions[:max_actions]:
        if not isinstance(raw_item, dict):
            results.append(_failed("?", "action entry must be a dict"))
            if stop_on_error:
                break
            continue
        action = str(raw_item.get("action") or "").strip().lower()
        if not action or action not in ACTION_KINDS:
            results.append(_failed(action or "?", f"unsupported action: {action!r}"))
            if stop_on_error:
                break
            continue
        try:
            if step_timeout_ms:
                result = await asyncio.wait_for(
                    _execute_one(action, raw_item), timeout=step_timeout_ms / 1000
                )
            else:
                result = await _execute_one(action, raw_item)
        except TimeoutError:
            result = _failed(action, f"step timeout after {step_timeout_ms}ms")
        except Exception as exc:  # noqa: BLE001 - per-action guard, chain must survive
            result = _failed(action, f"{type(exc).__name__}: {exc}")
        results.append(result)
        if result.get("ok"):
            executed += 1
        elif stop_on_error:
            break
    observation = await _chain_observation(url_before)
    return {
        "results": results,
        "executed": executed,
        "truncated": truncated,
        "observation": observation,
    }


__all__ = ["ACTION_KINDS", "analyze_page_compact", "execute_chain"]
