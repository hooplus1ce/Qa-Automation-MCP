"""Aria snapshotting, scoped control analysis, and element screenshotting."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

from ..browser import (
    _action_lock,
    _capture_window_bounds,
    _current_page_impl,
    _frame_context_details,
    _frame_details,
    _frame_id,
    _frame_page_offset,
    _page_id,
    _page_viewport_size,
    _read_viewport_or_none,
    _restore_window_and_viewport,
)
from ..components.vtable.binding import (
    active_application_frame,
    resolve_frame,
)
from ..config import ACTIVE_PROFILE
from ..workspace import artifact_file
from .locator import _find_interaction_locator

_COMPACT_CONTROL_SCAN = r"""
({scopeSelector, maxResults, customControlSelector}) => {
  const trim = (value, limit = 120) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const escape = value => {
    try { return CSS.escape(String(value)); }
    catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&'); }
  };
  const resolveScope = selector => {
    if (!selector) return document.body;
    let node = document.querySelector(selector);
    if (!node) return null;
    if (node.matches('.ant-modal-root, .ant-drawer-root')) {
      node = node.querySelector('.ant-modal[role="document"], .ant-drawer-content-wrapper, .ant-drawer') || node;
    }
    return node;
  };
  const scopeRoot = resolveScope(scopeSelector);
  if (!scopeRoot) return { controls: [], truncated: false, messages: ['scope-root-not-found'] };

  const isVisible = el => {
    if (!el || el.nodeType !== 1) return false;
    for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) {
      if (cur.getAttribute('aria-hidden') === 'true' || cur.hidden) return false;
      const style = window.getComputedStyle(cur);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) <= 0) return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const nameFor = el => {
    const aria = trim(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder'));
    if (aria) return aria;
    const tagged = el.closest ? el.closest('[data-label], [data-title]') : null;
    if (tagged) {
      const labelAttr = trim(tagged.getAttribute('data-label') || tagged.getAttribute('data-title'));
      if (labelAttr) return labelAttr;
    }
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const labelEl = document.getElementById(labelledBy);
      const text = trim(labelEl ? (labelEl.innerText || labelEl.textContent) : '');
      if (text) return text;
    }
    if (el.labels && el.labels.length) {
      const text = trim(Array.from(el.labels).map(item => item.innerText || item.textContent).join(' '));
      if (text) return text;
    }
    const item = el.closest ? el.closest('.ant-form-item, .ant-row, .legions-pro-quick-filter-row') : null;
    if (item) {
      const label = item.querySelector('label, .ant-form-item-label');
      const text = trim(label ? (label.innerText || label.textContent) : '');
      if (text) return text;
    }
    const treeNode = el.closest ? el.closest('.ant-tree-treenode, li, .ant-checkbox-wrapper, label') : null;
    if (treeNode) {
      const titleEl = treeNode.querySelector('.ant-tree-title, .ant-checkbox + span');
      const text = trim(titleEl ? (titleEl.innerText || titleEl.textContent) : '');
      if (text) return text;
      const nodeText = trim(treeNode.innerText || treeNode.textContent);
      if (nodeText) return nodeText;
    }
    return trim(el.innerText || el.textContent);
  };

  const selectorFor = (el, root) => {
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'data-cy']) {
      const value = el.getAttribute(attr);
      if (value) return `[${attr}="${escape(value)}"]`;
    }
    if (el.id) return `#${escape(el.id)}`;
    const tag = el.tagName.toLowerCase();
    const classes = Array.from(el.classList || [])
      .filter(item => !['ant-btn-clicked', 'active', 'focus', 'hover', 'selected'].includes(item))
      .slice(0, 3)
      .map(item => `.${escape(item)}`)
      .join('');
    const base = `${tag}${classes}`;
    const scopeDoc = root || document;
    const sameBase = Array.from(scopeDoc.querySelectorAll(base));
    if (sameBase.length === 1) return base;

    let path = [];
    for (let cur = el; cur && cur !== root && cur !== document.body && path.length < 3; cur = cur.parentElement) {
      const curTag = cur.tagName.toLowerCase();
      const curClasses = Array.from(cur.classList || []).slice(0, 2).map(item => `.${escape(item)}`).join('');
      const parent = cur.parentElement;
      const index = parent ? Array.from(parent.children).indexOf(cur) + 1 : 1;
      path.unshift(`${curTag}${curClasses}:nth-of-type(${index})`);
    }
    const candidate = path.join(' > ');
    if (scopeDoc.querySelectorAll(candidate).length === 1) return candidate;
    const index = sameBase.indexOf(el);
    return index >= 0 ? `${base} >> nth=${index}` : base;
  };

  const baseQuery = [
    'button',
    'input',
    'textarea',
    'select',
    'a[href]',
    '[role="button"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="combobox"]',
    '[role="textbox"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="treeitem"]',
    '.ant-checkbox',
    '.ant-tree-checkbox',
    '.ant-switch',
    '.ant-select-selection',
    '.ant-pagination-item',
    '.ant-pagination-prev',
    '.ant-pagination-next',
  ].join(',');
  const query = customControlSelector ? `${baseQuery},${customControlSelector}` : baseQuery;
  const candidates = Array.from(scopeRoot.querySelectorAll(query));
  const controls = [];
  let truncated = false;

  for (const el of candidates) {
    if (el.closest('.vtable, .vtable-canvas, canvas')) continue;
    if (!isVisible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const explicitRole = el.getAttribute('role');
    const isAntCheckbox = el.classList.contains('ant-checkbox') || el.classList.contains('ant-tree-checkbox');
    const isAntSwitch = el.classList.contains('ant-switch');
    const role = explicitRole || (
      isAntCheckbox ? 'checkbox' :
      isAntSwitch ? 'switch' :
      tag === 'button' ? 'button' :
      tag === 'select' ? 'combobox' :
      tag === 'textarea' ? 'textbox' :
      tag === 'input' ? (
        ['checkbox', 'radio'].includes(el.type) ? el.type :
        ['button', 'submit', 'reset'].includes(el.type) ? 'button' : 'textbox'
      ) :
      el.classList.contains('ant-select-selection') ? 'combobox' : 'control'
    );
    const rect = el.getBoundingClientRect();
    controls.push({
      role,
      name: nameFor(el),
      description: trim(el.getAttribute('aria-description') || el.getAttribute('title')) || null,
      css: selectorFor(el, scopeRoot === document.body ? null : scopeRoot),
      tag,
      input_type: el.getAttribute('type') || '',
      disabled: Boolean(
        el.disabled || el.getAttribute('aria-disabled') === 'true' ||
        el.classList.contains('ant-btn-disabled') ||
        el.classList.contains('ant-checkbox-disabled') ||
        el.classList.contains('ant-tree-checkbox-disabled') ||
        el.classList.contains('ant-switch-disabled') ||
        el.classList.contains('ant-dropdown-menu-item-disabled') ||
        el.classList.contains('ant-select-item-option-disabled') ||
        el.classList.contains('ant-cascader-menu-item-disabled') ||
        el.classList.contains('ant-select-tree-treenode-disabled') ||
        Boolean(el.closest && el.closest('.ant-checkbox-wrapper-disabled, .ant-tree-treenode-disabled'))
      ),
      readonly: Boolean(el.readOnly || el.getAttribute('aria-readonly') === 'true'),
      state: {
        ...(role === 'checkbox' || role === 'radio' || role === 'switch' ? {
          checked: Boolean(
            el.checked ||
            el.getAttribute('aria-checked') === 'true' ||
            el.classList.contains('ant-checkbox-checked') ||
            el.classList.contains('ant-tree-checkbox-checked') ||
            el.classList.contains('ant-switch-checked') ||
            (el.closest && (
              el.closest('.ant-checkbox-checked') ||
              el.closest('.ant-tree-checkbox-checked') ||
              el.closest('.ant-checkbox-wrapper-checked')
            ))
          )
        } : {}),
        ...(role === 'combobox' ? { expanded: Boolean(el.getAttribute('aria-expanded') === 'true' || el.closest('.ant-select-open')) } : {}),
      },
      box: {
        x: Math.round(rect.x * 100) / 100,
        y: Math.round(rect.y * 100) / 100,
        width: Math.round(rect.width * 100) / 100,
        height: Math.round(rect.height * 100) / 100,
      },
    });
    if (controls.length >= maxResults) {
      truncated = true;
      break;
    }
  }
  return { controls, truncated, messages: [] };
}
"""


async def _focused_editable(page: Page) -> dict[str, Any] | None:
    for frame in page.frames:
        try:
            item = await frame.evaluate(
                """() => {
                  const el = document.activeElement;
                  if (!el || el === document.body) return null;
                  const tag = el.tagName.toLowerCase();
                  const editable = el.isContentEditable || ['input', 'textarea'].includes(tag);
                  if (!editable) return null;
                  return {
                    tag,
                    role: el.getAttribute('role') || (tag === 'textarea' ? 'textbox' : el.type || 'textbox'),
                    name: el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('placeholder') || null,
                    selector: el.id ? `#${CSS.escape(el.id)}` : (el.name ? `${tag}[name="${CSS.escape(el.name)}"]` : tag),
                    value: el.value !== undefined ? String(el.value).slice(0, 300) : (el.innerText || '').slice(0, 300),
                  };
                }"""
            )
            if item:
                return item
        except Exception:
            continue
    return None


_MAX_SNAPSHOT_DEPTH = 8
_MAX_SNAPSHOT_CHARACTERS = 24_000

# aria 树里不承担语义的纯结构节点。它们在 AntD/React 页面上数量极大，
# 且没有可访问名时对模型判断"能点什么"零贡献。
_ANONYMOUS_ROLES = frozenset(
    {
        "generic",
        "list",
        "listitem",
        "paragraph",
        "emphasis",
        "strong",
        "superscript",
        "subscript",
        "presentation",
        "none",
        "group",
    }
)

_NODE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<dash>-)\s*(?P<role>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:\s+\"(?P<name>[^\"]*)\")?(?P<rest>.*)$"
)


def prune_anonymous_branches(snapshot: str) -> tuple[str, dict[str, int]]:
    """删掉"自己没名字、后代也全都没名字"的结构包装子树。

    实测动因（真机 APS 角色详情页，frame=null 的顶层快照）：
        nodes=181  matched=164  NAMED=37  unnamed=127  chars=12,601
        角色分布 generic=103  listitem=44  —— 127 个无名节点换来的只有 37 个有名字
    即 3.4k token 里约七成是 `- listitem [ref=e15] [cursor=pointer] [box=0,50,170,38]`
    这种重复骨架。判据只删"整棵子树都没有可访问名"的节点，因此带名字的
    叶子（按钮/链接/输入框）必然保留，不会把可行动目标剪掉。

    Returns:
        (裁剪后的快照, {"removed_nodes": n, "kept_named": n, "kept_total": n})
    """
    lines = snapshot.split("\n")
    parsed: list[tuple[int, int, str, str, bool]] = []  # indent, 行号, role, name, 是否节点行
    for idx, line in enumerate(lines):
        if not line.strip():
            parsed.append((-1, idx, "", "", False))
            continue
        m = _NODE_RE.match(line)
        if m:
            indent = len(m.group("indent").expandtabs(2))
            parsed.append((indent, idx, m.group("role").lower(), m.group("name") or "", True))
        else:
            # 非节点行（如 `text:` 续行）跟随其父节点的去留
            parsed.append((-2, idx, "", "", False))

    # 每行的子树结束位置：下一个 indent <= 本行 indent 的行
    n = len(parsed)
    subtree_end = [n] * n
    stack: list[int] = []
    for i, (indent, _idx, _role, _name, _is_node) in enumerate(parsed):
        if indent < 0:
            continue
        while stack and parsed[stack[-1]][0] >= indent:
            stack.pop()
        if stack:
            subtree_end[stack[-1]] = i
        stack.append(i)
    for i in range(len(parsed) - 1, -1, -1):
        if parsed[i][0] >= 0:
            subtree_end[i] = min(subtree_end[i], n)

    # 自顶向下决定去留：一个节点可剪 <=> 角色属于噪声集 且 自身无名 且 子树内无任何有名节点
    def subtree_has_name(start: int, end: int) -> bool:
        return any(parsed[j][0] >= 0 and parsed[j][3] for j in range(start, end))

    drop = [False] * n
    out_lines: list[str] = []
    removed = kept_named = kept_total = 0
    i = 0
    while i < n:
        if drop[i]:
            i += 1
            continue
        indent, _idx, role, name, is_node = parsed[i]
        if not is_node:
            out_lines.append(lines[i])
            i += 1
            continue
        if name:
            kept_named += 1
        kept_total += 1
        if role in _ANONYMOUS_ROLES and not name and not subtree_has_name(i, subtree_end[i]):
            removed += subtree_end[i] - i
            for j in range(i, subtree_end[i]):
                drop[j] = True
            i += 1
            continue
        out_lines.append(lines[i])
        i += 1

    stats = {"removed_nodes": removed, "kept_named": kept_named, "kept_total": kept_total}
    if removed == 0:
        return snapshot, stats
    return "\n".join(out_lines), stats


async def _dom_snapshot_impl(
    *,
    selector: str | None = None,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
    nth: int | None = None,
    visible_only: bool = False,
    max_elements: int = 5,
    timeout: float = 3.0,
    prune_noise: bool = True,
) -> dict:
    page = await _current_page_impl()
    target_frame = await resolve_frame(page, frame)
    scope_resolved = "main_document" if frame is None else frame
    # 默认作用域改为"激活的业务 iframe"，而非顶层文档。
    # 实测（iframe 套壳的 APS，同页同刻 A/B）：默认拍顶层文档 1,734 tok 里 106 行仅
    # 14 行有可访问名，全是侧边栏骨架；改拍激活模块后 452 tok、有名行占比反而从
    # 13% 升到 39% —— 更省且更有用。显式 frame='main' 仍可回顶层。
    # 仍先经 resolve_frame 再升级，保留它作为可替换接缝。
    if frame is None:
        active = await active_application_frame(page)
        if active is not None:
            target_frame = active
            scope_resolved = "active_iframe"
    try:
        snapshot_frame_ref = _frame_details(page, target_frame).get("frame_id")
    except Exception:
        # frame 元信息只是响应里的定位辅助字段，取不到不该让整次快照失败
        snapshot_frame_ref = None
    try:
        await target_frame.evaluate("""() => {
            const checkboxes = document.querySelectorAll('.ant-checkbox, .ant-tree-checkbox');
            for (const el of checkboxes) {
                if (!el.getAttribute('role')) el.setAttribute('role', 'checkbox');
                const checked = el.classList.contains('ant-checkbox-checked') ||
                                el.classList.contains('ant-tree-checkbox-checked') ||
                                Boolean(el.closest && el.closest('.ant-checkbox-wrapper-checked'));
                el.setAttribute('aria-checked', checked ? 'true' : 'false');
                if (el.classList.contains('ant-checkbox-disabled') || el.classList.contains('ant-tree-checkbox-disabled')) {
                    el.setAttribute('aria-disabled', 'true');
                }
                if (!el.getAttribute('aria-label')) {
                    const parent = el.closest('.ant-tree-treenode, li, .ant-checkbox-wrapper, label');
                    if (parent) {
                        const title = parent.querySelector('.ant-tree-title, .ant-checkbox + span');
                        const text = (title ? (title.innerText || title.textContent) : (parent.innerText || parent.textContent) || '').trim();
                        if (text) el.setAttribute('aria-label', text);
                    }
                }
            }
            const switches = document.querySelectorAll('.ant-switch');
            for (const el of switches) {
                if (!el.getAttribute('role')) el.setAttribute('role', 'switch');
                const checked = el.classList.contains('ant-switch-checked');
                el.setAttribute('aria-checked', checked ? 'true' : 'false');
            }
        }""")
    except Exception:
        pass

    kwargs: dict[str, Any] = {"mode": "ai" if ai_mode else "default", "boxes": bool(boxes)}
    requested_depth = depth if depth is not None and depth > 0 else None
    if requested_depth is not None:
        kwargs["depth"] = min(requested_depth, _MAX_SNAPSHOT_DEPTH)

    # 1. No selector: snapshot frame root
    if not selector:
        target = target_frame.locator(":root")
        snapshot = await target.aria_snapshot(**kwargs)
        raw_chars = len(snapshot)
        prune_stats: dict[str, int] = {}
        if prune_noise:
            snapshot, prune_stats = prune_anonymous_branches(snapshot)
        truncated = len(snapshot) > _MAX_SNAPSHOT_CHARACTERS
        if truncated:
            snapshot = snapshot[:_MAX_SNAPSHOT_CHARACTERS] + "\n… [snapshot truncated]"
        return {
            "status": "ok",
            "selector": selector,
            "frame": frame,
            "scope": scope_resolved,
            "frame_id": snapshot_frame_ref,
            "mode": kwargs["mode"],
            "boxes": kwargs["boxes"],
            "depth": kwargs.get("depth"),
            "depth_clamped": requested_depth is not None and requested_depth > _MAX_SNAPSHOT_DEPTH,
            "truncated": truncated,
            "character_limit": _MAX_SNAPSHOT_CHARACTERS,
            "noise_pruned": prune_stats.get("removed_nodes", 0) > 0,
            "prune_stats": {**prune_stats, "raw_chars": raw_chars, "chars": len(snapshot)}
            if prune_noise
            else None,
            "snapshot": snapshot,
            "hint": None
            if not prune_noise
            else "已剪除无名结构包装子树；需要原始 aria 树时传 prune_noise=false，"
                 "需要业务表格数据请改用 vtable_*（canvas 不进 aria 树）",
        }

    # 2. Selector provided: construct locator with error handling
    try:
        target = target_frame.locator(selector)
    except Exception as exc:
        return {
            "status": "error",
            "selector": selector,
            "frame": frame,
            "error": f"Invalid selector syntax: {exc}",
            "snapshot": "",
        }

    # Wait briefly for dynamic elements if timeout > 0
    if timeout > 0:
        try:
            await target.first.wait_for(state="attached", timeout=int(timeout * 1000))
        except Exception:
            pass

    try:
        total_count = await target.count()
    except Exception as exc:
        return {
            "status": "error",
            "selector": selector,
            "frame": frame,
            "error": f"Failed evaluating selector count: {exc}",
            "snapshot": "",
        }

    if total_count == 0:
        return {
            "status": "not_found",
            "selector": selector,
            "frame": frame,
            "match_count": 0,
            "snapshot": "",
            "message": f"Selector '{selector}' did not match any elements in frame '{frame or 'active'}'.",
        }

    # 3. Explicit nth target requested
    if nth is not None:
        matched_loc = target.nth(nth)
        try:
            vis = await matched_loc.is_visible()
        except Exception:
            vis = False

        try:
            snapshot = await matched_loc.aria_snapshot(**kwargs)
        except Exception as exc:
            return {
                "status": "error",
                "selector": selector,
                "nth": nth,
                "frame": frame,
                "match_count": total_count,
                "error": f"Failed capturing snapshot for '{selector} >> nth={nth}': {exc}",
                "snapshot": "",
            }

        truncated = len(snapshot) > _MAX_SNAPSHOT_CHARACTERS
        if truncated:
            snapshot = snapshot[:_MAX_SNAPSHOT_CHARACTERS] + "\n… [snapshot truncated]"

        return {
            "status": "ok",
            "selector": selector,
            "nth": nth,
            "frame": frame,
            "match_count": total_count,
            "visible": vis,
            "mode": kwargs["mode"],
            "boxes": kwargs["boxes"],
            "depth": kwargs.get("depth"),
            "depth_clamped": requested_depth is not None and requested_depth > _MAX_SNAPSHOT_DEPTH,
            "truncated": truncated,
            "character_limit": _MAX_SNAPSHOT_CHARACTERS,
            "snapshot": snapshot,
        }

    # 4. Single element match
    if total_count == 1:
        matched_loc = target.first
        try:
            vis = await matched_loc.is_visible()
        except Exception:
            vis = False

        if visible_only and not vis:
            return {
                "status": "not_visible",
                "selector": selector,
                "frame": frame,
                "match_count": 1,
                "visible": False,
                "snapshot": "",
                "message": f"Selector '{selector}' matched 1 element, but it is not visible (visible_only=True).",
            }

        try:
            snapshot = await matched_loc.aria_snapshot(**kwargs)
        except Exception as exc:
            return {
                "status": "error",
                "selector": selector,
                "frame": frame,
                "match_count": 1,
                "error": f"Failed capturing snapshot: {exc}",
                "snapshot": "",
            }

        truncated = len(snapshot) > _MAX_SNAPSHOT_CHARACTERS
        if truncated:
            snapshot = snapshot[:_MAX_SNAPSHOT_CHARACTERS] + "\n… [snapshot truncated]"

        return {
            "status": "ok",
            "selector": selector,
            "frame": frame,
            "match_count": 1,
            "visible": vis,
            "mode": kwargs["mode"],
            "boxes": kwargs["boxes"],
            "depth": kwargs.get("depth"),
            "depth_clamped": requested_depth is not None and requested_depth > _MAX_SNAPSHOT_DEPTH,
            "truncated": truncated,
            "character_limit": _MAX_SNAPSHOT_CHARACTERS,
            "snapshot": snapshot,
        }

    # 5. Multiple elements match (gracefully handle without strict mode violation)
    matched_items: list[dict[str, Any]] = []
    for i in range(total_count):
        loc = target.nth(i)
        try:
            vis = await loc.is_visible()
        except Exception:
            vis = False
        matched_items.append({"index": i, "locator": loc, "visible": vis})

    if visible_only:
        candidates = [item for item in matched_items if item["visible"]]
        if not candidates:
            return {
                "status": "not_visible",
                "selector": selector,
                "frame": frame,
                "match_count": total_count,
                "visible_count": 0,
                "snapshot": "",
                "message": f"Selector '{selector}' matched {total_count} elements, but none are visible (visible_only=True).",
            }
    else:
        candidates = matched_items

    cap = max(1, max_elements)
    elements_to_snapshot = candidates[:cap]
    snapshot_parts: list[str] = []
    metadata_elements: list[dict[str, Any]] = []

    for item in elements_to_snapshot:
        idx = item["index"]
        vis = item["visible"]
        loc = item["locator"]
        nth_selector = f"{selector} >> nth={idx}"
        vis_tag = "visible" if vis else "hidden"
        try:
            part = await loc.aria_snapshot(**kwargs)
            header = f"/* Match {idx + 1} of {total_count} ({vis_tag}): {nth_selector} */\n"
            snapshot_parts.append(header + part)
            metadata_elements.append({
                "index": idx,
                "visible": vis,
                "selector": nth_selector,
            })
        except Exception as exc:
            snapshot_parts.append(f"/* Match {idx + 1} of {total_count} ({vis_tag}): error capturing snapshot ({exc}) */")
            metadata_elements.append({
                "index": idx,
                "visible": vis,
                "selector": nth_selector,
                "error": str(exc),
            })

    if total_count > len(elements_to_snapshot):
        remaining = total_count - len(elements_to_snapshot)
        snapshot_parts.append(
            f"/* Note: Selector matched {total_count} elements. Displayed {len(elements_to_snapshot)}. "
            f"Use 'nth' parameter or append '>> nth=X' to inspect remaining {remaining} elements. */"
        )

    combined_snapshot = "\n\n".join(snapshot_parts)
    truncated = len(combined_snapshot) > _MAX_SNAPSHOT_CHARACTERS
    if truncated:
        combined_snapshot = combined_snapshot[:_MAX_SNAPSHOT_CHARACTERS] + "\n… [snapshot truncated]"

    return {
        "status": "ok",
        "selector": selector,
        "frame": frame,
        "match_count": total_count,
        "shown_count": len(elements_to_snapshot),
        "matched_elements": metadata_elements,
        "mode": kwargs["mode"],
        "boxes": kwargs["boxes"],
        "depth": kwargs.get("depth"),
        "depth_clamped": requested_depth is not None and requested_depth > _MAX_SNAPSHOT_DEPTH,
        "truncated": truncated,
        "character_limit": _MAX_SNAPSHOT_CHARACTERS,
        "snapshot": combined_snapshot,
    }


async def dom_snapshot(
    selector: str | None = None,
    *,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
    nth: int | None = None,
    visible_only: bool = False,
    max_elements: int = 5,
    timeout: float = 3.0,
    prune_noise: bool = True,
) -> dict:
    async with _action_lock:
        return await _dom_snapshot_impl(
            selector=selector,
            frame=frame,
            depth=depth,
            boxes=boxes,
            ai_mode=ai_mode,
            nth=nth,
            visible_only=visible_only,
            max_elements=max_elements,
            timeout=timeout,
            prune_noise=prune_noise,
        )


async def _analyze_scope_impl(
    *,
    max_controls: int = 40,
    max_overlays: int = 10,
) -> dict:
    from ..overlay import _scan_overlays_impl
    page = await _current_page_impl()
    active_frame = await active_application_frame(page)
    overlays_resp = await _scan_overlays_impl(max_results=max_overlays, scope="active")
    focus = (overlays_resp.get("context") or {}).get("focus_layer")

    target_frame = active_frame or page.main_frame
    scope_selector = None
    mode = "active_application"
    if focus is not None:
        target_frame = await resolve_frame(
            page, focus.get("frame_id") or focus.get("frame_name")
        )
        scope_selector = focus.get("selector")
        mode = "focus_layer"

    scan_result = await target_frame.evaluate(
        _COMPACT_CONTROL_SCAN,
        {
            "scopeSelector": scope_selector,
            "maxResults": max_controls,
            "customControlSelector": ACTIVE_PROFILE.custom_control_selector,
        },
    )

    offset = await _frame_page_offset(page, target_frame)
    controls = []
    for idx, c in enumerate(scan_result.get("controls") or []):
        b = c["box"]
        controls.append(
            {
                "role": c["role"],
                "name": c["name"],
                "description": c["description"],
                "css": c["css"],
                "tag": c["tag"],
                "input_type": c["input_type"],
                "disabled": c["disabled"],
                "readonly": c["readonly"],
                "state": c["state"],
                "ref": f"c{idx+1}",
                "frame": "active" if target_frame == active_frame else "top",
                "frame_id": _frame_id(page, target_frame),
                "scope": mode,
                "page_box": {
                    "x": round(b["x"] + offset["x"], 2),
                    "y": round(b["y"] + offset["y"], 2),
                    "width": b["width"],
                    "height": b["height"],
                },
            }
        )

    title = await page.title()
    url = page.url
    breadcrumb: list[str] = []
    active_tab: str | None = None
    try:
        breadcrumb = await page.evaluate(
            """() => Array.from(
                 document.querySelectorAll(
                   '.ant-breadcrumb li, .ant-breadcrumb span, '
                   '[class*="breadcrumb"] a, [class*="breadcrumb"] span'
                 )
               )
               .map(el => (el.innerText || '').trim())
               .filter(Boolean)
               .slice(0, 8)"""
        )
    except Exception:
        pass
    try:
        active_tab = await page.evaluate(
            """() => {
              const el = document.querySelector(
                '.ant-tabs-tab-active, .ant-tabs-tab .ant-tabs-tab-btn-active'
              );
              return el ? (el.innerText || '').trim() || null : null;
            }"""
        )
    except Exception:
        pass
    return {
        "status": "ok",
        "profile": ACTIVE_PROFILE.name,
        "page": {
            "page_id": _page_id(page),
            "url": url,
            "title": title,
            "breadcrumb": breadcrumb,
            "active_tab": active_tab,
        },
        "scope": {
            "mode": mode,
            "kind": focus.get("kind") if focus else None,
            "selector": scope_selector,
            "frame": _frame_details(page, target_frame),
        } if focus else {
            "mode": mode,
            "active_iframe": await _frame_context_details(page, active_frame) if active_frame else None,
        },
        "focus_layer": focus,
        "messages": scan_result.get("messages") or [],
        "controls": controls,
        "control_count": len(controls),
        "truncated": bool(scan_result.get("truncated")),
        "errors": [],
    }


async def analyze_scope(
    *, max_controls: int = 40, max_overlays: int = 10
) -> dict:
    async with _action_lock:
        return await _analyze_scope_impl(
            max_controls=max_controls, max_overlays=max_overlays
        )


async def _screenshot_element_impl(
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
    width: float | None = None,
    height: float | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    padding: float = 0,
    image_format: str = "png",
    filename: str | None = None,
    quality: int | None = None,
    timeout_ms: float = 3_000,
    screenshot_timeout_ms: float = 15_000,
    max_bytes: int = 2_000_000,
    include_base64: bool = False,
) -> dict[str, Any]:
    if image_format not in {"png", "jpeg"}:
        raise ValueError("image_format must be 'png' or 'jpeg'")
    if padding < 0 or padding > 200:
        raise ValueError("padding must be between 0 and 200 CSS pixels")
    if max_bytes < 1_024 or max_bytes > 20_000_000:
        raise ValueError("max_bytes must be between 1024 and 20000000")

    page = await _current_page_impl()
    locator = target_frame = locator_source = None
    locator_supplied = any([role, text, placeholder, css, xpath])
    coordinate_supplied = any(value is not None for value in (x, y, width, height))
    if coordinate_supplied and not all(value is not None for value in (x, y, width, height)):
        raise ValueError("viewport screenshot requires x, y, width and height")
    if locator_supplied:
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
    elif coordinate_supplied and frame not in {None, "top", "main", "main_frame"}:
        raise ValueError("viewport screenshot coordinates are top-page CSS pixels; omit frame")

    if locator is not None:
        target = locator.first
        await target.wait_for(state="visible", timeout=timeout_ms)
        box = await target.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            raise ValueError("target element has no visible bounding box")
        clip = {
            "x": max(0.0, float(box["x"]) - padding),
            "y": max(0.0, float(box["y"]) - padding),
            "width": float(box["width"]) + padding * 2,
            "height": float(box["height"]) + padding * 2,
        }
        frame_details = _frame_details(page, target_frame)
    elif coordinate_supplied:
        clip = {
            "x": max(0.0, float(x)),
            "y": max(0.0, float(y)),
            "width": float(width),
            "height": float(height),
        }
        if clip["width"] <= 0 or clip["height"] <= 0:
            raise ValueError("viewport screenshot width and height must be positive")
        frame_details = _frame_details(page, page.main_frame)
    else:
        # 既未提供定位器也未提供坐标时，默认捕获顶层视口完整画面，避免模型盲猜坐标导致视口失调
        vp = await _page_viewport_size(page)
        clip = {
            "x": 0.0,
            "y": 0.0,
            "width": vp["width"],
            "height": vp["height"],
        }
        frame_details = _frame_details(page, page.main_frame)

    screenshot_kwargs: dict[str, Any] = {"clip": clip, "type": image_format}
    if image_format == "jpeg" and quality is not None:
        if not 1 <= quality <= 100:
            raise ValueError("quality must be between 1 and 100")
        screenshot_kwargs["quality"] = quality
    # 截图前留档窗口态。Chromium 会为 clip 截图临时下发 device-metrics override 把视口
    # 撑到 clip 尺寸，正常结束会自行还原；但该次截图一旦被中断（超时 / 窗口最小化），
    # override 会残留在 RenderWidgetHost 上，页面视口被锁成元素尺寸（实测 900x383、715x270），
    # 之后 vtable、浮层、点击的坐标全部错位。所以这里给截图本身套一层硬超时，
    # 并在 finally 里无条件按 clip 尺寸做复位校验（命中残留会重试一轮）。
    window_before = await _capture_window_bounds(page)
    viewport_before = await _read_viewport_or_none(page)
    clip_size = (float(clip["width"]), float(clip["height"]))
    expected_size = (
        (viewport_before["w"], viewport_before["h"]) if viewport_before else None
    )
    viewport_guard: dict[str, Any] | None = None
    try:
        image = await asyncio.wait_for(
            page.screenshot(**screenshot_kwargs),
            timeout=max(0.5, float(screenshot_timeout_ms) / 1000),
        )
    except TimeoutError as exc:
        raise TimeoutError(
            "截图在 "
            f"{float(screenshot_timeout_ms):.0f}ms 内未返回"
            f"(裁剪框 {clip_size[0]:.0f}x{clip_size[1]:.0f})。"
            "已执行窗口与视口复位，可重试或缩小截图范围。"
        ) from exc
    finally:
        try:
            viewport_guard = await _restore_window_and_viewport(
                page,
                restore_bounds=window_before,
                clip_size=clip_size,
                expected_size=expected_size,
            )
        except Exception:
            viewport_guard = None
    expected_extensions = {".png"} if image_format == "png" else {".jpg", ".jpeg"}
    requested_filename = filename
    if requested_filename:
        suffix = "." + requested_filename.rsplit(".", 1)[-1].lower() if "." in requested_filename else ""
        if suffix and suffix not in expected_extensions:
            raise ValueError(
                f"filename extension must match image_format={image_format!r}"
            )
        if not suffix:
            requested_filename = f"{requested_filename}.{image_format}"
    output_path = artifact_file(
        "screenshots",
        requested_filename,
        fallback=f"screenshot.{image_format}",
        unique=filename is None,
    )
    output_path.write_bytes(image)
    if len(image) > max_bytes:
        return {
            "status": "failed",
            "reason": "screenshot-too-large",
            "byte_size": len(image),
            "max_bytes": max_bytes,
            "clip": {key: round(value, 2) for key, value in clip.items()},
            "path": str(output_path),
        }
    result: dict[str, Any] = {
        "status": "ok",
        "mime_type": f"image/{image_format}",
        "byte_size": len(image),
        "digest": hashlib.sha256(image).hexdigest()[:16],
        "path": str(output_path),
        "clip": {key: round(value, 2) for key, value in clip.items()},
        "page_id": _page_id(page),
        "frame": frame_details,
        "locator": {
            "resolved_by": locator_source,
            "css": css,
            "role": role,
            "name": name,
        } if locator_source else None,
    }
    if viewport_guard:
        result["viewport_guard"] = {
            # False = 清理后视口仍等于本次 clip 尺寸，说明 emulation 残留没清掉
            "restored": not viewport_guard.get("clip_lock_detected", False),
            "window_state": viewport_guard.get("window_state"),
            "viewport": viewport_guard.get("viewport"),
            "attempts": viewport_guard.get("attempts"),
        }
    if include_base64:
        result["image_base64"] = base64.b64encode(image).decode("ascii")
    return result


async def screenshot_element(**kwargs: Any) -> dict[str, Any]:
    async with _action_lock:
        return await _screenshot_element_impl(**kwargs)

async def _page_context_impl(*, max_results: int = 10) -> dict:
    from ..overlay import _scan_overlays_impl
    page = await _current_page_impl()
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    active = await active_application_frame(page)
    frames = list(page.frames)
    frame_items = []
    for frame in frames[: max(1, int(max_results))]:
        is_active = active is not None and frame == active
        if is_active:
            # 活动 frame 由下面的 active_iframe 完整描述一次即可；这里只留引用，
            # 否则同一坨 frame_url 会在一次 ui_page_context 里重复出现两次。
            frame_items.append(
                {**_frame_details(page, frame), "scope": "active_iframe", "described_in": "active_iframe"}
            )
            continue
        detail = await _frame_context_details(page, frame)
        detail["scope"] = "top_document" if frame == page.main_frame else "iframe"
        frame_items.append(detail)
    overlays = await _scan_overlays_impl(max_results=max_results, scope="active")
    return {
        "status": "ok",
        "profile": ACTIVE_PROFILE.name,
        "page_id": _page_id(page),
        "url": page.url,
        "title": title,
        "frame_count": len(frames),
        "active_iframe": (
            await _frame_context_details(page, active, full_url=True)
            if active is not None
            else None
        ),
        "frames": frame_items,
        "focus_layer": overlays.get("context", {}).get("focus_layer"),
        "visible_overlays": overlays.get("overlays", []),
        "observer_errors": overlays.get("observer_errors", []),
    }


async def page_context(*, max_results: int = 10) -> dict:
    async with _action_lock:
        return await _page_context_impl(max_results=max_results)
