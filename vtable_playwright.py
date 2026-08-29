"""
Playwright 驱动的 VTable 浏览器交互(新版点击工具)
==================================================

基于 Playwright 1.62(2026-08 最新稳定版)重写"点击工具",设计遵循官方
Playwright MCP(@playwright/mcp)的交互范式,解决旧 click_by_js 的三个硬伤:

1. **语义目标输入,确定性坐标解析**
   AI 描述"点哪个单元格"(col/row),坐标由 VTable 内部 API(getCellRelativeRect)
   计算,而不是猜测像素或依赖 elementFromPoint 的瞬时命中。表格外 DOM 元素用
   getByRole 语义定位 —— 对应 Playwright MCP 的"accessibility tree, not
   pixel-based input"原则。

2. **trusted 真实输入**
   用 page.mouse.click / locator.click 走浏览器真实输入管道(isTrusted=true),
   React/Ant Design 的 hover/focus/click 行为与真人一致;不再 dispatchEvent
   派发合成事件。

3. **可操作性等待 + 验证回路**
   等 .vtable 挂载、等 window._vtable 绑定、滚动到目标单元格、等渲染帧后才点;
   点击后回读选中区间/编辑器状态与点击前对比,未命中自动重试一次并如实上报,
   给 AI 可信的执行反馈。

4. **AI 语义之眼 + 批量读表(Playwright 1.60/1.62 新特性)**
   `dom_snapshot` 用 aria_snapshot(mode='ai', boxes=True) 把页面 accessibility 树
   (含 [ref=xx] 引用与 [box=x,y,w,h] 坐标)喂给 AI —— 官方 Playwright MCP 的核心范式;
   `table_meta` / `cells_read` 先读表格规模与区域值,让 AI "先看全局再动手";
   `drop_files` 用 Locator.drop(payload, position=) 精确拖放文件到目标单元格,
   position 由 VTable getCellRelativeRect 换算成容器相对坐标(1.60 新增);`click_dom`
   支持 get_by_role 的 description 参数(1.60 新增)消除多命中歧义。

5. **浏览器生命周期与会话隔离**
   `launch_chrome(port)` 启动带 CDP 的受管 Chrome 并等待端点就绪;
   `connect_browser(port)` 接管外部实例;`browser_session` 管理隔离
   BrowserContext 和 storage state,支持不同账号 Cookie 环境切换。
   `close_browser` 只终止本服务启动的 Chrome,外部实例只断开连接。

6. **CDP 连接复用外部浏览器**
   `connect_browser(cdp_url)` 用 connectOverCDP 连到已运行的浏览器
   (如 `--remote-debugging-port=9222` 启动的实例),AI 工具直接驱动其中已打开的
   VTable 页面,无需重新导航;`close_browser` 对外部浏览器只断开不杀进程。

依赖: playwright>=1.62(可选依赖,见 pyproject.toml 的 browser extra)。
未安装时工具会返回清晰报错而不是崩掉整个服务器。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
import urllib.error
import urllib.request
import weakref
from collections import OrderedDict
from pathlib import Path
from typing import Any

from automation_profiles import (
    LOCATOR_STRATEGY,
    VTABLE_VERIFICATION_STRATEGY,
    active_profile,
)

try:  # 可选依赖:未安装时给出可操作的报错
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

from vtable_js import (
    BIND_BFS_FALLBACK,
    CELL_RELATIVE_LOC,
    CELL_VISUAL_STATE,
    CLASSIFY_CELL,
    DROP_TARGET_BOXES,
    FAST_BIND,
    IS_CELL_VISIBLE,
    READ_CELLS,
    RESOLVE_CELL,
    TABLE_META,
    VTABLE_ANALYSIS,
    VTABLE_ROOTS,
    VTABLE_SUMMARY,
    WAIT_RENDER,
)

PLAYWRIGHT_INSTALL_HINT = (
    "未安装 playwright(可选依赖)。请执行: uv sync --extra browser,"
    "或 pip install 'playwright>=1.62'"
)

BIND_TIMEOUT_MS = 10_000          # 等 VTable 实例绑定
NAV_TIMEOUT_MS = 30_000           # 页面加载超时
SCROLL_WAIT_RAF = 2               # 滚动后多等几帧
ACTIVE_PROFILE = active_profile()
ACTIVE_IFRAME_SELECTOR = ACTIVE_PROFILE.active_iframe_selector


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


VTABLE_SHOW_CURSOR = _env_bool("VTABLE_SHOW_CURSOR", True)

OVERLAY_RESULT_LIMIT = _env_int("VTABLE_OVERLAY_RESULT_LIMIT", 20)

_browser: Browser | None = None
_pw: Any = None
_cdp: bool = False  # True 表示经 connectOverCDP 连到外部浏览器(关闭时只断开不杀进程)
_chrome_process: subprocess.Popen[Any] | None = None
_chrome_port: int | None = None
_chrome_profile: str | None = None
_chrome_profile_owned = False
_action_lock = asyncio.Lock()  # MCP calls share one page/observer state.
_selected_page: Page | None = None
_selected_context: Any | None = None
_page_ids: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_page_id_counter = 0
_page_frame_ids: weakref.WeakKeyDictionary[Any, weakref.WeakKeyDictionary[Any, str]] = (
    weakref.WeakKeyDictionary()
)
_page_frame_counters: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()
_fallback_frame_ids: dict[tuple[int, int], str] = {}
_fallback_frame_counters: dict[int, int] = {}
_context_ids: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_fallback_context_ids: dict[int, str] = {}
_context_id_counter = 0
_context_names: dict[str, str] = {}
_owned_contexts: dict[str, Any] = {}
_analysis_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_analysis_counter = 0
ANALYSIS_CACHE_LIMIT = 32
ANALYSIS_MAX_AGE_SECONDS = 120


def _page_id(page: Page) -> str:
    global _page_id_counter
    try:
        known = _page_ids.get(page)
        if known:
            return known
        _page_id_counter += 1
        value = f"page-{_page_id_counter}"
        _page_ids[page] = value
        return value
    except TypeError:  # test doubles that cannot be weak-referenced
        return f"page-object-{id(page)}"


def _context_id(context: Any, *, name: str | None = None) -> str:
    """Return a stable session ID even for test doubles that cannot be weak-referenced."""
    global _context_id_counter
    try:
        known = _context_ids.get(context)
        if known:
            if name:
                _context_names[known] = name
            return known
        _context_id_counter += 1
        value = "session-default" if not _context_ids else f"session-{_context_id_counter}"
        _context_ids[context] = value
    except TypeError:
        key = id(context)
        known = _fallback_context_ids.get(key)
        if known:
            if name:
                _context_names[known] = name
            return known
        _context_id_counter += 1
        value = "session-default" if not _fallback_context_ids else f"session-{_context_id_counter}"
        _fallback_context_ids[key] = value
    _context_names[value] = name or ("default" if value == "session-default" else value)
    return value


def _context_by_id(session_id: str | None) -> Any | None:
    if _browser is None:
        return None
    for context in _browser.contexts:
        if _context_id(context) == session_id:
            return context
    return None


def _session_summary(context: Any, index: int, selected: bool) -> dict[str, Any]:
    session_id = _context_id(context)
    return {
        "session_id": session_id,
        "name": _context_names.get(session_id, session_id),
        "context_index": index,
        "page_count": len(getattr(context, "pages", []) or []),
        "selected": selected,
        "managed": session_id in _owned_contexts,
    }


def _select_page_object(page: Page) -> Page:
    global _selected_page
    _selected_page = page
    _page_id(page)
    try:
        page.set_default_timeout(3_000)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    except Exception:
        pass
    return page


# Ant Design renders most overlays through a Portal attached to document.body.
# Keep the selector list deliberately conservative: role/aria-live catches
# custom wrappers, while the class names cover the stable AntD v4/v5 roots.
ANTD_OVERLAY_SELECTOR = ",".join(ACTIVE_PROFILE.overlay_selectors)
OVERLAY_OBSERVER_KEY = "__vtable_mcp_overlay_observer__"
OVERLAY_EVENT_LIMIT = 100
OVERLAY_SETTLE_LIMIT_MS = 2_000

_last_mouse_point: tuple[float, float] | None = None

# Solidified Windows 11 Dark HD high-definition pointer cursor (32x32, hotspot at (5, 10))
_EMBEDDED_CURSOR_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAB8klEQVR42u2WTUsCURSGy68srSZF6Z"
    "OIIiho1zJCw7XQOgjFH+BP0HLVbjYRtHFb0CzCHyC4aycJg7kRgtnoQnDET2Q6dzgTw6Bpee/QYg"
    "684L0jvM85Z+bcOzdnhRVW/OOYN8g8Y1EU/YVCIQC/XSgnyG4KyHA45BWMVqt1D1srIC9ogTnEYD"
    "C4JMY8z6si0Wg0nuFRELQKcjOFgOxviCn8VJVOp1WIer3+AutNEMcUwgigh6jVagJziFEApkLAO3"
    "A7CmAChIMaxE8AYyDWQIvUICYB6CHa7baYSCSOqEJMA0AUi8W+IeLx+DHs+ahATAugh2g2m2+w3j"
    "K8mOwB9O3I5XJXsF7Hien8cxV+C5BMJlUAQRCuYb2N05KMbBsTgFAopKRSKSWfzyvValU173a7Ej"
    "w7xDawASDGxFQLWZY/JEl6LRaLd9Fo9BT+s4vnhRdPT3otIBmT6Pf7cqlUeoxEImewTz6/AzTeAP"
    "lByzOfmNoo5jhONc9ms6p5pVJ5CofD57BHPrl97HcQZwAxXsLMZxvNnU7nghiScmslL5fLD/DoBP"
    "u8ozuaPZixdlmx0ZiGtl6vl4FWvIM+IfMMZryHpfahsUtnSv0e6MCSksESwIz9eDOie/iMqwKW1Y"
    "3ZenDMuky7FyKEHbN10OyxFcb4AvzesBnJB6WlAAAAAElFTkSuQmCC"
)
_EMBEDDED_CURSOR_DATA_URL = f"data:image/png;base64,{_EMBEDDED_CURSOR_PNG_BASE64}"
_CURSOR_HOT_X = 5
_CURSOR_HOT_Y = 10
_CURSOR_WIDTH = 32
_CURSOR_HEIGHT = 32


def _build_cursor_helper_script() -> str:
    cursor_css = f"""
    position: fixed;
    left: 0;
    top: 0;
    width: {_CURSOR_WIDTH}px;
    height: {_CURSOR_HEIGHT}px;
    pointer-events: none !important;
    z-index: 2147483647 !important;
    background-image: url("{_EMBEDDED_CURSOR_DATA_URL}");
    background-size: {_CURSOR_WIDTH}px {_CURSOR_HEIGHT}px;
    background-repeat: no-repeat;
    transform: translate(-100px, -100px);
    opacity: 0;
    transition: opacity 0.15s ease-out, transform 0.02s linear;
    transform-origin: {_CURSOR_HOT_X}px {_CURSOR_HOT_Y}px;
    filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.4));
"""
    inner_content = "''"
    update_pos = f"""
    const scale = isMouseDown ? ' scale(0.92)' : ' scale(1)';
    cursor.style.transform = `translate(${{x - {_CURSOR_HOT_X}}}px, ${{y - {_CURSOR_HOT_Y}}}px)${{scale}}`;
"""
    return f"""(() => {{
  if (document.getElementById('__vtable_win_cursor__')) return 'already-installed';

  const cursor = document.createElement('div');
  cursor.id = '__vtable_win_cursor__';
  cursor.innerHTML = {inner_content};
  cursor.style.cssText = `{cursor_css}`;

  const ripple = document.createElement('div');
  ripple.id = '__vtable_win_ripple__';
  ripple.style.cssText = `
    position: fixed;
    left: 0;
    top: 0;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 2px solid rgba(0, 120, 215, 0.7);
    background: rgba(0, 120, 215, 0.2);
    pointer-events: none !important;
    z-index: 2147483646 !important;
    transform: translate(-50%, -50%) scale(0);
    opacity: 0;
    transition: transform 0.25s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.25s ease-out;
  `;

  document.documentElement.appendChild(cursor);
  document.documentElement.appendChild(ripple);

  let hideTimer = null;
  let isMouseDown = false;

  const scheduleHide = () => {{
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {{
      if (!isMouseDown) {{
        cursor.style.opacity = '0';
      }}
    }}, 150);
  }};

  const showCursor = () => {{
    cursor.style.opacity = '1';
    scheduleHide();
  }};

  const updatePos = (x, y) => {{
    {update_pos}
  }};

  window.__vtable_update_cursor = (x, y, down = false, clickRipple = false) => {{
    isMouseDown = down;
    showCursor();
    updatePos(x, y);
    if (clickRipple) {{
      ripple.style.transition = 'none';
      ripple.style.left = x + 'px';
      ripple.style.top = y + 'px';
      ripple.style.transform = 'translate(-50%, -50%) scale(0.3)';
      ripple.style.opacity = '1';
      requestAnimationFrame(() => {{
        ripple.style.transition = 'transform 0.28s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.28s ease-out';
        ripple.style.transform = 'translate(-50%, -50%) scale(1.5)';
        ripple.style.opacity = '0';
      }});
    }}
  }};

  window.addEventListener('mousemove', e => {{
    showCursor();
    updatePos(e.clientX, e.clientY);
  }}, true);

  window.addEventListener('mousedown', e => {{
    isMouseDown = true;
    showCursor();
    updatePos(e.clientX, e.clientY);
    ripple.style.transition = 'none';
    ripple.style.transform = `translate(${{e.clientX}}px, ${{e.clientY}}px) scale(0.4)`;
    ripple.style.opacity = '1';
    requestAnimationFrame(() => {{
      ripple.style.transition = 'transform 0.28s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.28s ease-out';
      ripple.style.transform = `translate(${{e.clientX}}px, ${{e.clientY}}px) scale(1.4)`;
      ripple.style.opacity = '0';
    }});
  }}, true);

  window.addEventListener('mouseup', e => {{
    isMouseDown = false;
    showCursor();
    updatePos(e.clientX, e.clientY);
  }}, true);

  return 'installed-win-cursor';
}})()"""


_WIN_CURSOR_HELPER_SCRIPT = _build_cursor_helper_script()


async def _ensure_cursor_helper(page: Page) -> None:
    """Ensure the Windows-style virtual mouse cursor helper is installed on the page."""
    if not VTABLE_SHOW_CURSOR:
        return
    try:
        await page.evaluate(_WIN_CURSOR_HELPER_SCRIPT)
    except Exception:
        pass


async def _smooth_mouse_move_to(
    page: Page, target_x: float, target_y: float
) -> None:
    """Smoothly glide the mouse from its last known position to (target_x, target_y) at >= 60fps."""
    global _last_mouse_point
    target_x, target_y = float(target_x), float(target_y)
    start_x, start_y = (
        _last_mouse_point
        if _last_mouse_point
        else (max(0.0, target_x - 60), max(0.0, target_y - 40))
    )
    dx = target_x - start_x
    dy = target_y - start_y
    dist = math.hypot(dx, dy)

    steps = max(10, min(35, int(dist / 22)))
    for step in range(1, steps + 1):
        t = step / steps
        ease_t = 1 - math.pow(1 - t, 3)
        curr_x = start_x + dx * ease_t
        curr_y = start_y + dy * ease_t
        if VTABLE_SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__vtable_update_cursor && window.__vtable_update_cursor({curr_x:.1f}, {curr_y:.1f})"
                )
            except Exception:
                pass
        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(0.016)
    _last_mouse_point = (target_x, target_y)


# Scan only actionable controls. Canvas cells are intentionally absent: VTable
# targets must be resolved through the instance API helpers below.
_COMPACT_CONTROL_SCAN = r"""
({scopeSelector, maxResults, customControlSelector}) => {
  const trim = (value, limit = 120) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const escape = value => {
    try { return CSS.escape(String(value)); }
    catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&'); }
  };
  const resolveScope = selector => {
    if (!selector) return document;
    const match = String(selector).match(/^(.*?)\s*>>\s*nth=(\d+)$/);
    if (match) return document.querySelectorAll(match[1])[Number(match[2])] || null;
    try { return document.querySelector(selector); } catch (_) { return null; }
  };
  const scope = resolveScope(scopeSelector);
  if (!scope) return { controls: [], scope_found: false, truncated: false };
  const visible = element => {
    if (!element || element.nodeType !== 1) return false;
    for (let current = element; current && current.nodeType === 1; current = current.parentElement) {
      if (current.hidden || current.getAttribute('aria-hidden') === 'true') return false;
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity || 1) <= 0) return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const implicitRole = element => {
    const explicit = element.getAttribute('role');
    if (explicit) return explicit;
    const tag = element.tagName.toLowerCase();
    if (tag === 'button' || (tag === 'input' && ['button', 'submit', 'reset', 'image'].includes((element.type || '').toLowerCase()))) return 'button';
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea' || element.isContentEditable) return 'textbox';
    if (tag === 'input') {
      const type = (element.type || 'text').toLowerCase();
      if (type === 'hidden') return null;
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    const cls = String(element.className || '').toLowerCase();
    if (/ant-(?:select|cascader|tree-select)(?:\b|-)/.test(cls)) return 'combobox';
    if (/ant-picker(?:\b|-)/.test(cls)) return 'textbox';
    if (/ant-switch(?:\b|-)/.test(cls)) return 'switch';
    if (/ant-(?:btn|button)(?:\b|-)/.test(cls)) return 'button';
    if (/ant-checkbox(?:\b|-)/.test(cls)) return 'checkbox';
    if (/ant-radio(?:\b|-)/.test(cls)) return 'radio';
    return null;
  };
  const nameOf = element => {
    const labelledBy = trim(element.getAttribute('aria-labelledby'));
    if (labelledBy) {
      const label = labelledBy.split(/\s+/).map(id => trim(document.getElementById(id)?.innerText)).filter(Boolean).join(' ');
      if (label) return trim(label);
    }
    const aria = trim(element.getAttribute('aria-label'));
    if (aria) return aria;
    if (element.id) {
      try {
        const label = document.querySelector(`label[for="${escape(element.id)}"]`);
        if (label && trim(label.innerText)) return trim(label.innerText);
      } catch (_) {}
    }
    const wrappingLabel = element.closest('label');
    if (wrappingLabel && trim(wrappingLabel.innerText)) return trim(wrappingLabel.innerText);
    const formLabel = element.closest('.ant-form-item')?.querySelector('.ant-form-item-label label, .ant-form-item-label');
    if (formLabel && trim(formLabel.innerText)) return trim(formLabel.innerText);
    const placeholder = trim(element.getAttribute('placeholder'));
    if (placeholder) return placeholder;
    if (element.tagName.toLowerCase() === 'input' && ['button', 'submit', 'reset'].includes((element.type || '').toLowerCase())) return trim(element.value);
    return trim(element.innerText || element.textContent || element.getAttribute('title'));
  };
  const selectorFor = element => {
    const unique = selector => {
      try { return document.querySelectorAll(selector).length === 1; } catch (_) { return false; }
    };
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'data-cy']) {
      const value = element.getAttribute(attr);
      if (value) {
        const selector = `[${attr}="${escape(value)}"]`;
        if (unique(selector)) return selector;
      }
    }
    if (element.id) return `#${escape(element.id)}`;
    if (element.getAttribute('name')) {
      const selector = `[name="${escape(element.getAttribute('name'))}"]`;
      if (unique(selector)) return selector;
    }
    const dynamic = /^(?:active|focus|hover|checked|selected|disabled|loading|open|hidden)$|ant-wave|css-dev-only/i;
    const parts = [];
    for (let current = element; current && current !== document.body && parts.length < 4; current = current.parentElement) {
      let part = current.tagName.toLowerCase();
      const classes = Array.from(current.classList || []).filter(value => !dynamic.test(value)).slice(0, 2);
      if (classes.length) part += classes.map(value => `.${escape(value)}`).join('');
      const siblings = current.parentElement ? Array.from(current.parentElement.children).filter(child => child.tagName === current.tagName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      parts.unshift(part);
      const selector = parts.join(' > ');
      if (unique(selector)) return selector;
    }
    const fallback = parts.join(' > ');
    try {
      const matches = Array.from(document.querySelectorAll(fallback));
      const index = matches.indexOf(element);
      if (matches.length > 1 && index >= 0) return `${fallback} >> nth=${index}`;
    } catch (_) {}
    return fallback;
  };
  const nativeSelector = [
    'button', 'input:not([type="hidden"])', 'select', 'textarea', 'a[href]', 'summary', '[contenteditable="true"]',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]', '[role="switch"]',
    '[role="tab"]', '[role="menuitem"]', '[role="option"]', '[role="combobox"]', '[role="textbox"]',
    '[role="searchbox"]', '[role="spinbutton"]', '[role="slider"]', '[role="treeitem"]'
  ].join(',');
  const customSelector = customControlSelector;
  const candidates = Array.from(scope.querySelectorAll(`${nativeSelector},${customSelector}`));
  const controls = [];
  const seen = new Set();
  for (const element of candidates) {
    if (!visible(element)) continue;
    if (element.matches(customSelector) && element.querySelector(nativeSelector)) continue;
    const role = implicitRole(element);
    if (!role) continue;
    const rect = element.getBoundingClientRect();
    const key = `${role}|${Math.round(rect.x)}|${Math.round(rect.y)}|${Math.round(rect.width)}|${Math.round(rect.height)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const type = element.tagName.toLowerCase() === 'input' ? (element.type || 'text').toLowerCase() : '';
    const state = {};
    if ('checked' in element || element.hasAttribute('aria-checked')) state.checked = 'checked' in element ? !!element.checked : element.getAttribute('aria-checked') === 'true';
    if (element.hasAttribute('aria-expanded')) state.expanded = element.getAttribute('aria-expanded') === 'true';
    if (element.hasAttribute('aria-selected')) state.selected = element.getAttribute('aria-selected') === 'true';
    if (type !== 'password' && 'value' in element && trim(element.value)) state.value = trim(element.value);
    controls.push({
      role,
      name: nameOf(element),
      description: trim(element.getAttribute('aria-description') || element.getAttribute('title')) || null,
      css: selectorFor(element),
      tag: element.tagName.toLowerCase(),
      input_type: type,
      disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true',
      readonly: !!element.readOnly || element.getAttribute('aria-readonly') === 'true',
      state,
      box: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    });
    if (controls.length >= Math.max(1, Number(maxResults) || 1)) break;
  }
  return { controls, scope_found: true, truncated: candidates.length > controls.length };
}
"""


class _OverlayFrameListener:
    """Keep newly attached/navigated iframe documents covered during an action."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.events: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active = False

    def attach(self) -> None:
        if self._active:
            return
        self._active = True
        self.page.on("frameattached", self._on_frame)
        self.page.on("framenavigated", self._on_frame)

    def _on_frame(self, frame: Frame) -> None:
        if not self._active:
            return
        try:
            task = asyncio.create_task(self._install(frame))
        except RuntimeError as exc:  # pragma: no cover - loop shutdown race
            self.errors.append({"reason": f"observer-frame-task-error: {exc}"})
            return
        self._tasks.add(task)

    async def _install(self, frame: Frame) -> None:
        try:
            result = await _install_overlay_observer_in_frame(self.page, frame, reset=False)
            if not result["reused"]:
                self.events.extend({**item, "event": "added"} for item in result["baseline"])
        except Exception as exc:
            try:
                details = _frame_details(self.page, frame)
            except Exception:
                details = {"frame_id": "", "frame_url": "", "frame_name": ""}
            self.errors.append({**details, "reason": str(exc)[:500]})

    async def wait_pending(self) -> None:
        if not self._tasks:
            return
        pending = tuple(self._tasks)
        await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.difference_update(pending)

    def take_buffers(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        events, errors = self.events, self.errors
        self.events = []
        self.errors = []
        return events, errors

    async def close(self) -> None:
        if not self._active:
            return
        self._active = False
        try:
            self.page.remove_listener("frameattached", self._on_frame)
            self.page.remove_listener("framenavigated", self._on_frame)
        except Exception as exc:
            self.errors.append({"reason": f"observer-listener-remove-error: {exc}"})
        await self.wait_pending()


_overlay_frame_listeners: weakref.WeakKeyDictionary[Any, _OverlayFrameListener] = (
    weakref.WeakKeyDictionary()
)


async def _acquire_overlay_frame_listener(
    page: Page, *, persistent: bool
) -> tuple[_OverlayFrameListener, bool]:
    """Acquire the page listener, replacing a prior action-scoped listener."""
    existing = _overlay_frame_listeners.get(page)
    if existing is not None and existing._active:
        if persistent:
            return existing, False
        await existing.close()
        _overlay_frame_listeners.pop(page, None)
    listener = _OverlayFrameListener(page)
    listener.attach()
    if persistent:
        _overlay_frame_listeners[page] = listener
    return listener, True


async def _release_overlay_frame_listener(
    page: Page, listener: _OverlayFrameListener | None, *, persistent: bool
) -> list[dict[str, Any]]:
    if listener is None or persistent:
        return []
    errors: list[dict[str, Any]] = []
    try:
        await listener.close()
    except Exception as exc:
        errors.append({"reason": f"observer-listener-close-error: {exc}"})
    if _overlay_frame_listeners.get(page) is listener:
        _overlay_frame_listeners.pop(page, None)
    return [*listener.errors, *errors]


def _overlay_init_script(deadline_ms: int) -> str:
    """Bootstrap observers in documents created after arm (notably srcdoc frames)."""
    observer = _overlay_script(_OVERLAY_OBSERVER_TEMPLATE, reset=False)
    key = json.dumps(OVERLAY_OBSERVER_KEY)
    return f"""(() => {{
  const deadline = {int(deadline_ms)};
  if (Date.now() >= deadline) return null;
  const result = {observer};
  const remaining = Math.max(0, deadline - Date.now() + 25);
  setTimeout(() => {{
    if (Date.now() < deadline) return;
    const state = window[{key}];
    if (state && state.observer) state.observer.disconnect();
    if (state && window[{key}] === state) delete window[{key}];
  }}, remaining);
  return result;
}})()"""


async def _arm_overlay_init_script(
    page: Page, *, settle_ms: int, persistent: bool, extra_ms: int = 0
) -> None:
    # A finite deadline keeps the future-frame bootstrap bounded. Persistent
    # streams get a long lease and are still explicitly stopped by drain().
    duration = (settle_ms + extra_ms + 250) if not persistent else 86_400_000
    deadline = int(time.time() * 1000) + duration
    await page.add_init_script(script=_overlay_init_script(deadline))


_OVERLAY_OBSERVER_TEMPLATE = r"""
(() => {
  const key = __KEY__;
  const selector = __SELECTOR__;
  const maxEvents = __MAX_EVENTS__;
  const reset = __RESET__;

  const trimText = (value, limit = 1000) => String(value || "")
    .replace(/\s+/g, " ").trim().slice(0, limit);
  const isElement = node => node && node.nodeType === 1;
  const isVisible = el => {
    if (!isElement(el)) return false;
    // AntD may keep a matching child mounted under a hidden Portal wrapper.
    // Walk ancestors so a stale, non-interactive node is not reported as live.
    for (let current = el; isElement(current); current = current.parentElement) {
      if (current.getAttribute("aria-hidden") === "true" || current.hidden) return false;
      const style = getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) <= 0) return false;
      if (String(current.className || "").toLowerCase().split(/\s+/).some(token =>
        /(?:dropdown|modal|mask|drawer|popover|tooltip|message|notification).*hidden$/.test(token) ||
        token === "hidden" || token === "--hidden"
      )) return false;
    }
    const rect = el.getBoundingClientRect();
    if (Number(getComputedStyle(el).zIndex) < 0) return false;
    return rect.width > 0 && rect.height > 0;
  };
  const classText = el => String(el.className || "").toLowerCase();
  const kindFor = el => {
    const cls = classText(el);
    const role = String(el.getAttribute("role") || "").toLowerCase();
    if (role === "dialog" || role === "alertdialog" || /modal|dialog/.test(cls)) return "dialog";
    if (/drawer/.test(cls)) return "drawer";
    if (role === "listbox" || role === "menu" || /dropdown|select|picker|cascader|tree-select|mentions|menu|vtable.*popup|filter-menu|virtual-option/.test(cls) || el.querySelector?.(".virtual-option")) return "dropdown";
    if (role === "alert" || role === "status" || el.hasAttribute("aria-live") || /message|notification|alert/.test(cls)) return "notification";
    if (/popover|popconfirm|tooltip|tour|bubble-tooltip/.test(cls)) return "popover";
    return "overlay";
  };
  const cssEscape = value => {
    try { return CSS.escape(String(value)); } catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); }
  };
  const selectorFor = el => {
    for (const attr of ["data-testid", "data-test", "data-qa", "data-cy"]) {
      const value = el.getAttribute(attr);
      if (value) return `[${attr}="${cssEscape(value)}"]`;
    }
    if (el.id) return `#${cssEscape(el.id)}`;
    const stateClasses = new Set([
      "active", "focus", "hover", "checked", "selected", "disabled", "loading", "animating", "hidden",
      "move-up-enter", "move-up-enter-active", "move-up-leave", "move-up-leave-active",
      "zoom-appear", "zoom-appear-active", "zoom-leave", "zoom-leave-active", "zoom-enter", "zoom-enter-active",
      "fade-enter", "fade-enter-active", "fade-leave", "fade-leave-active",
      "ant-zoom-appear", "ant-zoom-appear-active", "ant-zoom-enter", "ant-zoom-leave",
    ]);
    const classes = classText(el).split(/\s+/).filter(Boolean)
      .filter(value => !stateClasses.has(value) && !/(?:-enter|-leave|-appear|zoom-|move-up|fade-)/.test(value)).slice(0, 4);
    const role = el.getAttribute("role");
    const own = `${el.tagName.toLowerCase()}${classes.map(value => `.${cssEscape(value)}`).join("")}${role ? `[role="${cssEscape(role)}"]` : ""}`;
    if (document.querySelectorAll(own).length === 1) return own;
    let current = el;
    const parts = [];
    for (let depth = 0; current && depth < 3; depth++, current = current.parentElement) {
      const part = current.id ? `#${cssEscape(current.id)}` : `${current.tagName.toLowerCase()}${classText(current).split(/\s+/).filter(Boolean).filter(value => !stateClasses.has(value) && !/(?:-enter|-leave|-appear|zoom-|move-up|fade-)/.test(value)).slice(0, 2).map(value => `.${cssEscape(value)}`).join("")}`;
      parts.unshift(part || current.tagName.toLowerCase());
      const candidate = parts.join(" > ");
      if (document.querySelectorAll(candidate).length === 1) return candidate;
    }
    const matches = Array.from(document.querySelectorAll(own));
    const index = matches.indexOf(el);
    return index > 0 ? `${own} >> nth=${index}` : own;
  };
  const canonicalOverlay = element => {
    if (!isElement(element)) return null;
    if (element.matches(".virtual-option")) {
      const parent = element.parentElement;
      return parent && parent.querySelectorAll(".virtual-option").length > 1 ? parent : element;
    }
    if (element.matches && element.matches(".ant-modal-root, .ant-modal-wrap, .ant-drawer-root")) {
      const inner = element.querySelector(".ant-modal, .ant-drawer-content-wrapper, .ant-drawer");
      if (inner) return inner;
    }
    if (element.matches && element.matches(".ant-modal-content")) {
      const dialog = element.closest(".ant-modal");
      if (dialog) return dialog;
    }
    return element;
  };
  const describe = (el, event) => {
    if (!isElement(el)) return null;
    const visible = isVisible(el);
    // A very short-lived message can be removed before MutationObserver runs.
    // Retain its text as an event even though it no longer has a useful box.
    if (!visible && event !== "added" && event !== "removed") return null;
    const rect = el.getBoundingClientRect();
    const role = el.getAttribute("role") || null;
    const options = el.querySelectorAll ? Array.from(el.querySelectorAll(".virtual-option")) : [];
    const text = trimText(options.length > 1
      ? options.slice(0, 3).map(option => option.innerText || option.textContent).join(" | ")
      : (el.innerText || el.textContent), 320);
    const label = trimText(el.getAttribute("aria-label") || el.getAttribute("title"), 300) || null;
    const kind = kindFor(el);
    const selectorText = selectorFor(el);
    const identity = [kind, selectorText, role || "", label || ""].join("|");
    const result = {
      event: event || "visible",
      kind,
      tag: el.tagName.toLowerCase(),
      role,
      selector: selectorText,
      class_name: String(el.className || "").slice(0, 500),
      text,
      label,
      identity,
      fingerprint: `${identity}|${text}`,
      visible,
      box: visible ? {
        x: Math.round(rect.x * 100) / 100,
        y: Math.round(rect.y * 100) / 100,
        width: Math.round(rect.width * 100) / 100,
        height: Math.round(rect.height * 100) / 100,
      } : null,
      timestamp: Date.now(),
    };
    if (options.length > 1) {
      result.option_count = options.length;
      result.option_preview = options.slice(0, 3).map(option => trimText(option.innerText || option.textContent, 100));
    }
    return result;
  };
  const candidateRoots = (node, includeDescendants = false, event = "") => {
    let root = node;
    if (!isElement(root)) root = root && root.parentElement;
    if (!isElement(root)) return [];
    // This custom editor renders each suggestion as a `.virtual-option` with
    // no semantic listbox role. Treat the shared options container as one
    // dropdown so an AI receives one actionable layer instead of N siblings.
    if (event === "removed" && root.matches(".virtual-option") && !root.parentElement) return [];
    const roots = [];
    if (root.matches(selector)) roots.push(canonicalOverlay(root));
    else {
      const closest = root.closest(selector);
      if (closest) roots.push(canonicalOverlay(closest));
    }
    // Only walk an added subtree. Attribute/text updates already point at the
    // changed candidate (or its closest matching ancestor), and avoiding a
    // full-body query here keeps React's frequent mutations cheap.
    if (includeDescendants) {
      for (const child of root.querySelectorAll(selector)) {
        const canonicalChild = canonicalOverlay(child);
        if (!canonicalChild) continue;
        if (!roots.some(parent => parent === canonicalChild || parent.contains(canonicalChild) || canonicalChild.contains(parent))) roots.push(canonicalChild);
      }
    }
    return roots;
  };
  const reused = !!(window[key] && window[key].version === 1);
  const state = reused
    ? window[key]
    : { version: 1, events: [], seen: {}, seenOrder: [], selector, observer: null, collect: null, baseline: [] };
  if (!state.seenOrder) state.seenOrder = [];
  if (!state.collect) {
    state.collect = () => {
      const roots = [];
      for (const candidate of document.querySelectorAll(selector)) {
        const root = canonicalOverlay(candidate);
        if (!root || roots.some(parent => parent === root || parent.contains(root))) continue;
        roots.push(root);
      }
      return roots.map(el => describe(el, "visible")).filter(Boolean);
    };
  }
  if (reset && state.observer) {
    // Drop records queued before this action and rebuild the observer so an
    // old React mutation cannot leak into the next click's event window.
    state.observer.takeRecords();
    state.observer.disconnect();
    state.observer = null;
  }
  if (reset || !reused) {
    state.events = [];
    state.seen = {};
    state.seenOrder = [];
    state.baseline = state.collect();
  }
  if (!state.observer) {
    state.record = (node, event, includeDescendants = false) => {
      for (const root of candidateRoots(node, includeDescendants, event)) {
        const item = describe(root, event);
        if (!item) continue;
        const dedupeKey = `${item.event}|${item.fingerprint}`;
        const previous = state.seen[dedupeKey];
        if (previous && item.timestamp - previous < 40) continue;
        state.seen[dedupeKey] = item.timestamp;
        state.seenOrder.push(dedupeKey);
        state.events.push(item);
        if (state.events.length > maxEvents) state.events.splice(0, state.events.length - maxEvents);
        while (state.seenOrder.length > maxEvents * 4) {
          const staleKey = state.seenOrder.shift();
          if (staleKey) delete state.seen[staleKey];
        }
      }
    };
    state.observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        if (mutation.type === "attributes") state.record(mutation.target, "changed");
        else for (const node of mutation.addedNodes) state.record(node, "added", true);
        // React can update a notice and remove it in the same commit. By the
        // time the callback runs the text node has no parent, so retain the
        // detached candidate from removedNodes as the final event evidence.
        for (const node of mutation.removedNodes) state.record(node, "removed", true);
        if (mutation.type === "characterData") state.record(mutation.target, "changed");
      }
    });
    state.observer.observe(document.documentElement || document, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
      attributeFilter: [
        "class", "style", "hidden", "aria-hidden", "aria-live", "role", "open",
        "data-state", "data-open", "data-visible", "aria-expanded", "aria-haspopup",
      ],
    });
    window[key] = state;
  }
  return { installed: true, reused, baseline: state.baseline || state.collect() };
})()
"""

_OVERLAY_DRAIN_TEMPLATE = r"""
(() => {
  const key = __KEY__;
  const state = window[key];
  if (!state || !state.collect) return { events: [], current: [], baseline: [], installed: false };
  const current = state.collect();
  const result = {
    events: state.events.splice(0), current, baseline: state.baseline || [], installed: true,
  };
  if (__STOP__) {
    if (state.observer) state.observer.disconnect();
    delete window[key];
  } else {
    // Advance the stream baseline after returning the old one, so repeated
    // drains report only changes since the previous drain.
    state.baseline = current;
  }
  return result;
})()
"""


# ============================================================================
#  JS 包装:vtable_js.py 的脚本是函数体风格,统一包成 Playwright 可调用的箭头函数
# ============================================================================


def _wrap(script: str) -> str:
    """无参脚本:() => { ... }。Playwright 识别为箭头函数表达式并自动调用。"""
    return f"() => {{ {script} }}"


def _wrap2(script: str) -> str:
    """双参脚本:把 vtable_js.py 的 (function(col,row){...})(arguments[0],arguments[1])
    原地改写为 ([c, r]) => { ... },配合 page.evaluate(js, [col, row]) 传参。"""
    return f"([c, r]) => {{ {script.replace('(arguments[0], arguments[1])', '(c, r)')} }}"


def _wrap4(script: str) -> str:
    """四参脚本(批量读值):(arguments[0..3]) 改写为 ([a, b, c, d]) => { ... }。"""
    return (
        f"([a, b, c, d]) => {{ "
        f"{script.replace('(arguments[0], arguments[1], arguments[2], arguments[3])', '(a, b, c, d)')} }}"
    )


def _cell_visible_js(col: int, row: int) -> str:
    """IS_CELL_VISIBLE 含 {col}/{row} 占位符;脚本里还有 JS 花括号,须用 replace 而非 format。

    原脚本是裸表达式(无 return 前缀),且带前导换行 —— 直接在其前面拼 return 会触发
    ASI(return 后换行 → 返回 undefined),必须先 strip 再包成同行的 return 表达式。
    """
    body = IS_CELL_VISIBLE.replace("{col}", str(col)).replace("{row}", str(row)).strip()
    return f"() => {{ return {body} }}"


def _overlay_script(template: str, *, stop: bool = False, reset: bool = False) -> str:
    """Render an observer script without interpolating untrusted page data."""
    return (
        template.replace("__KEY__", json.dumps(OVERLAY_OBSERVER_KEY))
        .replace("__SELECTOR__", json.dumps(ANTD_OVERLAY_SELECTOR))
        .replace("__MAX_EVENTS__", str(OVERLAY_EVENT_LIMIT))
        .replace("__STOP__", "true" if stop else "false")
        .replace("__RESET__", "true" if reset else "false")
    )


def _frame_id(page: Page, frame: Frame) -> str:
    """Return a frame-lifetime identifier that does not change on reordering."""
    try:
        name = frame.name or "unnamed"
    except Exception:
        name = "unnamed"
    try:
        frame_map = _page_frame_ids.get(page)
        if frame_map is None:
            frame_map = weakref.WeakKeyDictionary()
            _page_frame_ids[page] = frame_map
        existing = frame_map.get(frame)
        if existing:
            return existing
        index = _page_frame_counters.get(page, 0)
        _page_frame_counters[page] = index + 1
        value = f"frame-{index}:{name}"
        frame_map[frame] = value
        return value
    except Exception:
        # Playwright's objects are weak-referenceable, but keep diagnostics
        # useful for test doubles and alternate bindings too.
        page_key = id(page)
        frame_key = (page_key, id(frame))
        existing = _fallback_frame_ids.get(frame_key)
        if existing:
            return existing
        index = _fallback_frame_counters.get(page_key, 0)
        _fallback_frame_counters[page_key] = index + 1
        value = f"frame-{index}:{name}"
        _fallback_frame_ids[frame_key] = value
        return value


def _frame_details(page: Page, frame: Frame) -> dict[str, str]:
    try:
        frame_id = _frame_id(page, frame)
    except Exception:
        frame_id = f"frame-error:{id(frame)}"
    try:
        frame_url = frame.url
    except Exception:
        frame_url = ""
    try:
        frame_name = frame.name or ""
    except Exception:
        frame_name = ""
    return {
        "frame_id": frame_id,
        "frame_url": frame_url,
        "frame_name": frame_name,
    }


def _page_frame_count(page: Page) -> int:
    """Best-effort frame count for results while a page may be closing."""
    try:
        return len(page.frames)
    except Exception:
        return 0


def _frame_name_url(frame: Frame) -> tuple[str, str]:
    """Read frame diagnostics without letting a detach race mask the action."""
    try:
        name = frame.name or ""
    except Exception:
        name = ""
    try:
        url = frame.url
    except Exception:
        url = ""
    return name, url


def _dedupe_overlays(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest and most settled signal for each overlay fingerprint."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        text = str(item.get("text", "")).strip()
        kind = str(item.get("kind", ""))
        fid = str(item.get("frame_id", ""))
        # Group by frame, kind, and non-empty text if available, else identity/fingerprint
        key = (fid, f"{kind}|{text}") if text else (fid, str(item.get("fingerprint", "")))
        previous = latest.get(key)
        if previous is None:
            latest[key] = item
            continue
        # Prefer visible item over hidden transitional item
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
        # Internal dedupe fields are useful inside the server but waste AI context.
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


async def _scope_frame_ids(page: Page, scope: str) -> set[str] | None:
    """Resolve the low-token scan scope without changing action observation."""
    if scope == "all":
        return None
    active = await active_application_frame(page)
    # Pages without the configured AntD tab layout retain the generic
    # all-frame fallback instead of silently hiding a valid module.
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


async def _overlay_context(page: Page, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return only the page/frame focus state needed for the next AI action."""
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

async def _frame_context_details(page: Page, frame: Frame) -> dict[str, Any]:
    """Add iframe element identity without making detached-frame errors fatal."""
    details: dict[str, Any] = _frame_details(page, frame)
    if frame == page.main_frame:
        return details
    try:
        element = await frame.frame_element()
        attrs = await element.evaluate(
            "(el) => ({id: el.id || '', name: el.getAttribute('name') || '', src: el.getAttribute('src') || ''})"
        )
        details["iframe"] = attrs
    except Exception:
        details["iframe"] = None
    return details


async def _frame_page_offset(page: Page, frame: Frame) -> dict[str, float]:
    """Return the frame content origin in the top-page viewport."""
    if frame == page.main_frame:
        return {"x": 0.0, "y": 0.0}
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
        if box:
            border = await element.evaluate(
                "el => ({x: Number(el.clientLeft || 0), y: Number(el.clientTop || 0)})"
            )
            return {
                "x": float(box["x"]) + float(border["x"]),
                "y": float(box["y"]) + float(border["y"]),
            }
    except Exception:
        pass
    return {"x": 0.0, "y": 0.0}


def _find_frame_by_id(page: Page, frame_id: str) -> Frame | None:
    try:
        frames = list(page.frames)
    except Exception:
        return None
    for frame in frames:
        if _frame_id(page, frame) == frame_id:
            return frame
    return None


async def _scan_controls_in_frame(
    page: Page,
    frame: Frame,
    *,
    scope_selector: str | None,
    scope_name: str,
    max_results: int,
    ref_start: int = 1,
) -> dict[str, Any]:
    """Return a compact semantic control list from one explicit DOM scope."""
    raw = await frame.evaluate(
        _COMPACT_CONTROL_SCAN,
        {
            "scopeSelector": scope_selector,
            "maxResults": max(1, int(max_results)),
            "customControlSelector": ACTIVE_PROFILE.custom_control_selector,
        },
    )
    offset = await _frame_page_offset(page, frame)
    details = _frame_details(page, frame)
    active = await active_application_frame(page)
    frame_hint: str | None = "top"
    if frame != page.main_frame:
        if active is not None and frame == active:
            frame_hint = "active"
        else:
            frame_hint = details.get("frame_name") or details.get("frame_url") or None
    controls: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("controls", []), start=ref_start):
        record = dict(item)
        local_box = record.pop("box", None) or {}
        record["ref"] = f"c{index}"
        record["frame"] = frame_hint
        record["frame_id"] = details["frame_id"]
        record["scope"] = scope_name
        if local_box:
            record["page_box"] = {
                "x": round(float(local_box.get("x", 0)) + offset["x"], 2),
                "y": round(float(local_box.get("y", 0)) + offset["y"], 2),
                "width": round(float(local_box.get("width", 0)), 2),
                "height": round(float(local_box.get("height", 0)), 2),
            }
        else:
            record["page_box"] = None
        controls.append(record)
    return {
        "controls": controls,
        "scope_found": bool(raw.get("scope_found", False)),
        "truncated": bool(raw.get("truncated", False)),
    }


async def _navigation_context(page: Page) -> dict[str, Any]:
    """Read only the active navigation labels that disambiguate the module."""
    try:
        return await page.evaluate(
            """() => ({
              breadcrumb: Array.from(document.querySelectorAll('.ant-breadcrumb .ant-breadcrumb-link'))
                .map(node => (node.innerText || '').replace(/\\s+/g, ' ').trim()).filter(Boolean).slice(0, 8),
              active_tab: (document.querySelector('.ant-tabs-nav .ant-tabs-tab-active')?.innerText || '')
                .replace(/\\s+/g, ' ').trim().slice(0, 120),
            })"""
        )
    except Exception:
        return {"breadcrumb": [], "active_tab": ""}


async def _install_overlay_observer_in_frame(
    page: Page, frame: Frame, *, reset: bool = False
) -> dict[str, Any]:
    """Install one frame observer and enrich its baseline with frame identity."""
    result = await frame.evaluate(_overlay_script(_OVERLAY_OBSERVER_TEMPLATE, reset=reset))
    items = [
        {**_frame_details(page, frame), **item}
        for item in result.get("baseline", [])
    ]
    return {
        "reused": bool(result.get("reused", False)),
        "baseline": items,
    }


# ============================================================================
#  浏览器生命周期(singleton,跨工具调用复用)
# ============================================================================


async def _start_browser_impl(headless: bool = True) -> dict:
    """启动/复用 Chromium。标准 chromium 不可用时自动退化为系统 Chrome(channel='chrome')。"""
    global _browser, _pw, _cdp, _selected_page, _selected_context, _context_id_counter
    if _browser is not None and _browser.is_connected():
        return {"status": "already-open", "browser": "chromium", "headless": headless}
    if async_playwright is None:
        raise RuntimeError(PLAYWRIGHT_INSTALL_HINT)

    _pw = await async_playwright().start()
    try:
        _browser = await _pw.chromium.launch(headless=headless)
    except Exception as first_error:
        # 常见环境未下载匹配版本的 chromium 二进制,退而用系统安装的 Chrome
        try:
            _browser = await _pw.chromium.launch(channel="chrome", headless=headless)
        except Exception:
            await _pw.stop()
            _pw = None
            raise first_error
    _cdp = False
    _selected_page = None
    _selected_context = _browser.contexts[0] if _browser.contexts else None
    if _selected_context is not None:
        _context_id(_selected_context, name="default")
        if VTABLE_SHOW_CURSOR:
            try:
                await _selected_context.add_init_script(_WIN_CURSOR_HELPER_SCRIPT)
            except Exception:
                pass
    return {"status": "opened", "browser": "chromium", "headless": headless}


async def start_browser(headless: bool = True) -> dict:
    """Serialize browser startup against page actions and teardown."""
    async with _action_lock:
        return await _start_browser_impl(headless=headless)


async def _connect_browser_impl(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    """经 CDP 连接一个已运行的浏览器(如 --remote-debugging-port=9222 启动的实例)。

    connectOverCDP 复用外部浏览器与其已打开的页面(含页面里的 VTable 实例),
    AI 工具直接驱动现有页面,无需重新导航。关闭时仅断开连接,不关闭外部进程。
    """
    global _browser, _pw, _cdp, _selected_page, _selected_context
    if _browser is not None and _browser.is_connected():
        return {"status": "already-connected", "cdp": cdp_url}
    if async_playwright is None:
        raise RuntimeError(PLAYWRIGHT_INSTALL_HINT)

    _pw = await async_playwright().start()
    try:
        _browser = await _pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        await _pw.stop()
        _pw = None
        raise RuntimeError(
            f"无法连接 CDP 浏览器 {cdp_url!r}。请确认 Chrome 已使用 "
            "--remote-debugging-port 启动，且端口可访问。原始错误: "
            f"{exc}"
        ) from exc
    _cdp = True
    _selected_page = None
    _selected_context = _browser.contexts[0] if _browser.contexts else None
    if _selected_context is not None:
        _context_id(_selected_context, name="default")
        if VTABLE_SHOW_CURSOR:
            try:
                await _selected_context.add_init_script(_WIN_CURSOR_HELPER_SCRIPT)
                for p in _selected_context.pages:
                    try:
                        await p.evaluate(_WIN_CURSOR_HELPER_SCRIPT)
                    except Exception:
                        pass
            except Exception:
                pass
    tabs = []
    try:
        if _browser.contexts:
            tabs = [p.url[:80] for p in _browser.contexts[0].pages]
    except Exception:
        pass
    return {
        "status": "connected",
        "cdp": cdp_url,
        "port": int(cdp_url.rsplit(":", 1)[-1].rstrip("/")) if ":" in cdp_url and cdp_url.rsplit(":", 1)[-1].rstrip("/").isdigit() else None,
        "managed": False,
        "browser": _browser.version,
        "tabs": tabs,
    }


async def connect_browser(
    cdp_url: str | None = None, *, port: int = 9222
) -> dict:
    """Serialize a CDP connection against page actions and teardown."""
    async with _action_lock:
        target = cdp_url or f"http://127.0.0.1:{port}"
        return await _connect_browser_impl(target)


def _chrome_executable(explicit: str | None = None) -> str:
    candidates = [explicit, os.getenv("CHROME_EXECUTABLE"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    for candidate in candidates:
        if candidate and (os.path.isabs(candidate) or shutil.which(candidate)):
            return candidate
    raise RuntimeError("找不到 Chrome/Chromium 可执行文件，请传入 executable_path 或设置 CHROME_EXECUTABLE")


async def _wait_for_cdp(port: int, timeout_ms: int) -> str:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + max(1_000, timeout_ms) / 1000
    last_error = ""
    while time.monotonic() < deadline:
        try:
            def read_endpoint() -> dict[str, Any]:
                with urllib.request.urlopen(url, timeout=1.0) as response:
                    return json.loads(response.read().decode("utf-8"))
            info = await asyncio.to_thread(read_endpoint)
            websocket = info.get("webSocketDebuggerUrl")
            if websocket:
                return websocket
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        await asyncio.sleep(0.1)
    raise RuntimeError(f"Chrome CDP 端口 {port} 未在 {timeout_ms}ms 内就绪: {last_error}")


async def _launch_chrome_impl(
    *,
    port: int = 9222,
    headless: bool = False,
    executable_path: str | None = None,
    user_data_dir: str | None = None,
    timeout_ms: int = 15_000,
) -> dict[str, Any]:
    """Launch a dedicated Chrome process, wait for CDP, then attach Playwright."""
    global _chrome_process, _chrome_port, _chrome_profile, _chrome_profile_owned
    if not 1 <= int(port) <= 65535:
        raise ValueError("port 必须在 1-65535 范围内")
    if _browser is not None and _browser.is_connected():
        return {"status": "already-open", "port": _chrome_port, "managed": _chrome_process is not None}
    executable = _chrome_executable(executable_path)
    profile = user_data_dir or tempfile.mkdtemp(prefix=f"vtable-mcp-chrome-{port}-")
    owned_profile = user_data_dir is None
    args = [
        executable,
        f"--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={int(port)}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]
    if headless:
        args.append("--headless=new")
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _chrome_process = process
        _chrome_port = int(port)
        _chrome_profile = profile
        _chrome_profile_owned = owned_profile
        await _wait_for_cdp(int(port), timeout_ms)
        if process.poll() is not None:
            raise RuntimeError(f"Chrome 进程提前退出，端口 {port} 可能已被其他浏览器占用")
        result = await _connect_browser_impl(f"http://127.0.0.1:{int(port)}")
        result.update({"status": "started", "managed": True, "port": int(port), "headless": headless, "user_data_dir": profile})
        return result
    except Exception:
        if _chrome_process is not None:
            try:
                _chrome_process.kill()
            except Exception:
                pass
        _chrome_process = None
        _chrome_port = None
        if owned_profile:
            shutil.rmtree(profile, ignore_errors=True)
        _chrome_profile = None
        _chrome_profile_owned = False
        raise


async def launch_chrome(
    *,
    port: int = 9222,
    headless: bool = False,
    executable_path: str | None = None,
    user_data_dir: str | None = None,
    timeout_ms: int = 15_000,
) -> dict[str, Any]:
    async with _action_lock:
        return await _launch_chrome_impl(
            port=port,
            headless=headless,
            executable_path=executable_path,
            user_data_dir=user_data_dir,
            timeout_ms=timeout_ms,
        )


async def _close_browser_impl() -> dict:
    """关闭浏览器并释放驱动资源；外部 CDP 浏览器只断开，受管进程会终止。"""
    global _browser, _pw, _cdp, _selected_page, _selected_context, _last_mouse_point
    global _chrome_process, _chrome_port, _chrome_profile, _chrome_profile_owned
    _last_mouse_point = None
    errors: list[str] = []
    listeners = list(_overlay_frame_listeners.values())
    for listener in listeners:
        try:
            await listener.close()
        except Exception as exc:
            errors.append(f"observer-listener-close: {exc}")
    _overlay_frame_listeners.clear()
    try:
        if _browser is not None:
            for context in list(_owned_contexts.values()):
                try:
                    await context.close()
                except Exception as exc:
                    errors.append(f"context-close: {exc}")
            if not _cdp:  # 自己 launch 的才关进程;外部浏览器保持运行
                await _browser.close()
    except Exception as exc:
        errors.append(f"browser-close: {exc}")
    finally:
        _browser = None
        if _pw is not None:
            try:
                await _pw.stop()
            except Exception as exc:
                errors.append(f"playwright-stop: {exc}")
        _pw = None
        _cdp = False
        _selected_page = None
        _selected_context = None
        _owned_contexts.clear()
        _context_ids.clear()
        _fallback_context_ids.clear()
        _context_names.clear()
        _context_id_counter = 0
        process = _chrome_process
        profile = _chrome_profile
        profile_owned = _chrome_profile_owned
        _chrome_process = None
        _chrome_port = None
        _chrome_profile = None
        _chrome_profile_owned = False
        if process is not None:
            try:
                process.terminate()
                await asyncio.to_thread(process.wait, 5)
            except Exception:
                try:
                    process.kill()
                except Exception as exc:
                    errors.append(f"chrome-process-close: {exc}")
        if profile_owned and profile:
            shutil.rmtree(profile, ignore_errors=True)
    result: dict[str, Any] = {"status": "closed"}
    if errors:
        result["errors"] = errors
    return result


async def close_browser() -> dict:
    """Serialize browser teardown against page actions and navigation."""
    async with _action_lock:
        return await _close_browser_impl()


async def _current_page_impl() -> Page:
    """取当前页面;无可用浏览器则先启动(CDP 连接优先复用),无页面则新建。

    多页签(尤其是 CDP 连到已有浏览器)时,优先复用带 .vtable 的页面 ——
    本项目的交互目标就是 VTable 页面,避免拿到无关页签。
    """
    global _selected_page, _selected_context
    if _browser is None or not _browser.is_connected():
        await _start_browser_impl()
    assert _browser is not None
    if _selected_page is not None:
        try:
            if not _selected_page.is_closed():
                return _select_page_object(_selected_page)
        except Exception:
            pass
        _selected_page = None
    ctx = _selected_context
    if ctx is None or ctx not in _browser.contexts:
        ctx = _browser.contexts[0] if _browser.contexts else await _browser.new_context()
        _selected_context = ctx
        _context_id(ctx, name="default" if not _browser.contexts or ctx == _browser.contexts[0] else None)
    pages = ctx.pages
    if not pages:
        page = await ctx.new_page()
        page.set_default_timeout(3_000)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        return _select_page_object(page)
    if len(pages) > 1:
        # First honor the application's active Tab iframe invariant.
        for p in pages:
            try:
                if await active_application_frame(p) is not None:
                    return _select_page_object(p)
            except Exception:
                continue
        # Then prefer a visible page with a VTable, rather than an unrelated tab.
        for p in pages:
            try:
                if await p.evaluate("() => document.visibilityState") != "visible":
                    continue
                fr = await vtable_frame(p)
                if fr != p.main_frame or await fr.locator(".vtable").count():
                    return _select_page_object(p)
            except Exception:
                continue
    return _select_page_object(pages[0])


async def current_page() -> Page:
    """Return the shared page while serializing browser lifecycle access."""
    async with _action_lock:
        return await _current_page_impl()


async def _list_pages_impl() -> dict:
    current = await _current_page_impl()
    assert _browser is not None
    pages: list[dict[str, Any]] = []
    for context_index, context in enumerate(_browser.contexts):
        session_id = _context_id(context, name="default" if context_index == 0 else None)
        for tab_index, page in enumerate(context.pages):
            try:
                title = (await page.title())[:160]
            except Exception:
                title = ""
            try:
                visible = await page.evaluate(
                    "() => document.visibilityState === 'visible'"
                )
            except Exception:
                visible = False
            pages.append(
                {
                    "page_id": _page_id(page),
                    "session_id": session_id,
                    "session_name": _context_names.get(session_id, session_id),
                    "context_index": context_index,
                    "tab_index": tab_index,
                    "url": page.url,
                    "title": title,
                    "visible": bool(visible),
                    "selected": page == current,
                }
            )
    selected_context_id = _context_id(_selected_context) if _selected_context is not None else None
    return {
        "status": "ok",
        "pages": pages,
        "sessions": [_session_summary(ctx, i, ctx == _selected_context) for i, ctx in enumerate(_browser.contexts)],
        "selected_page_id": _page_id(current),
        "selected_session_id": selected_context_id,
    }


async def list_pages() -> dict:
    """List explicit page IDs so AI never has to infer a CDP tab by position."""
    async with _action_lock:
        return await _list_pages_impl()


async def _select_page_impl(page_id: str) -> dict:
    global _selected_context
    if _browser is None or not _browser.is_connected():
        await _start_browser_impl()
    assert _browser is not None
    available: list[str] = []
    for context in _browser.contexts:
        for page in context.pages:
            candidate_id = _page_id(page)
            available.append(candidate_id)
            if candidate_id != page_id:
                continue
            if page.is_closed():
                break
            _selected_context = context
            _select_page_object(page)
            try:
                await page.bring_to_front()
            except Exception:
                pass
            return {
                "status": "selected",
                "page_id": candidate_id,
                "session_id": _context_id(context),
                "url": page.url,
                "title": (await page.title())[:160],
            }
    return {
        "status": "failed",
        "reason": f"page not found: {page_id!r}",
        "available_page_ids": available,
    }


async def select_page(page_id: str) -> dict:
    """Keep one explicit page selected for every subsequent MCP action."""
    async with _action_lock:
        return await _select_page_impl(page_id)


async def _browser_session_impl(
    action: str = "list",
    session_id: str | None = None,
    name: str | None = None,
    storage_state_path: str | None = None,
) -> dict[str, Any]:
    """List/create/select/save/close isolated BrowserContext sessions."""
    global _selected_context, _selected_page
    if _browser is None or not _browser.is_connected():
        await _start_browser_impl()
    assert _browser is not None
    contexts = list(_browser.contexts)
    if action == "list":
        return {
            "status": "ok",
            "sessions": [_session_summary(ctx, i, ctx == _selected_context) for i, ctx in enumerate(contexts)],
            "selected_session_id": _context_id(_selected_context) if _selected_context is not None else None,
        }
    if action == "create":
        kwargs: dict[str, Any] = {}
        if storage_state_path and os.path.exists(storage_state_path):
            kwargs["storage_state"] = storage_state_path
        try:
            context = await _browser.new_context(**kwargs)
        except Exception as exc:
            return {
                "status": "failed",
                "reason": "context-unsupported",
                "message": f"当前 CDP 浏览器不支持创建新上下文: {exc}",
                "hint": "请使用 browser_start 启动受管 Chrome，或为每个账号使用独立 user_data_dir/端口。",
            }
        sid = _context_id(context, name=name or f"session-{len(contexts)}")
        _context_names[sid] = name or sid
        _owned_contexts[sid] = context
        _selected_context = context
        _selected_page = None
        return {"status": "created", **_session_summary(context, len(contexts), True), "storage_state_path": storage_state_path}
    context = _context_by_id(session_id)
    if context is None:
        return {"status": "failed", "reason": f"session not found: {session_id!r}", "sessions": [_session_summary(ctx, i, ctx == _selected_context) for i, ctx in enumerate(contexts)]}
    sid = _context_id(context)
    if action == "select":
        _selected_context = context
        _selected_page = None
        page = context.pages[0] if context.pages else await context.new_page()
        _select_page_object(page)
        return {"status": "selected", **_session_summary(context, contexts.index(context), True), "page_id": _page_id(page)}
    if action == "save":
        if not storage_state_path:
            return {"status": "failed", "reason": "storage_state_path is required for save"}
        Path(storage_state_path).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=storage_state_path)
        return {"status": "saved", "session_id": sid, "storage_state_path": storage_state_path}
    if action == "close":
        if context == contexts[0]:
            return {"status": "failed", "reason": "default-session-cannot-close"}
        if storage_state_path:
            Path(storage_state_path).parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=storage_state_path)
        await context.close()
        _owned_contexts.pop(sid, None)
        if _selected_context == context:
            _selected_context = _browser.contexts[0] if _browser.contexts else None
            _selected_page = None
        return {"status": "closed", "session_id": sid, "storage_state_path": storage_state_path}
    return {"status": "failed", "reason": f"unsupported action: {action!r}", "allowed_actions": ["list", "create", "select", "save", "close"]}


async def browser_session(
    action: str = "list",
    session_id: str | None = None,
    name: str | None = None,
    storage_state_path: str | None = None,
) -> dict[str, Any]:
    async with _action_lock:
        return await _browser_session_impl(action, session_id, name, storage_state_path)


# ============================================================================
#  VTable 绑定与单元格解析
# ============================================================================


async def vtable_frame(page: Page) -> Frame:
    """找到包含 .vtable 的 frame(主 frame 或子 frame)。

    VTable 常渲染在 iframe 里(如 demo18-scm 的 /static/old/scm-spo),而
    page.mouse 用的是主页面视口坐标 —— 这里返回那个真正持有表格的 frame,
    供 evaluate / 选择器定位使用;坐标换算见 cell_center。
    """
    # The real application keeps each secondary module in the active AntD tab
    # iframe. Prefer that frame so a hidden tab cannot win the lookup merely
    # because it still has a mounted VTable instance.
    active = await active_application_frame(page)
    if active is not None:
        try:
            if await active.locator(".vtable").count():
                return active
        except Exception:
            pass
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        try:
            if await fr.locator(".vtable").count():
                return fr
        except Exception:
            continue
    return page.main_frame


async def active_application_frame(page: Page) -> Frame | None:
    """Return the iframe hosted by the currently visible AntD tab panel.

    This is an application-level invariant, not a generic iframe heuristic.
    A full frame scan remains the fallback for pages without this layout.
    """
    try:
        candidates = page.locator(ACTIVE_IFRAME_SELECTOR)
        for index in range(await candidates.count()):
            handle = await candidates.nth(index).element_handle(timeout=200)
            if handle is None:
                continue
            frame = await handle.content_frame()
            if frame is not None:
                return frame
    except Exception:
        return None
    return None


async def resolve_frame(page: Page, frame: str | None) -> Frame:
    """把工具调用的 frame 参数解析成 Frame。

    frame=None → 主 frame;"vtable" → 自动定位含 .vtable 的 frame;其它按
    iframe name 或 URL 子串匹配(如 "application" / "scm-spo")。显式指定
    frame 却未命中时抛错,避免在多 iframe 页面中静默把操作落到主文档。
    """
    if frame is None:
        return page.main_frame
    if frame in {"top", "main", "main_frame"}:
        return page.main_frame
    if frame == "vtable":
        return await vtable_frame(page)
    if frame in {"active", "active_iframe"}:
        active = await active_application_frame(page)
        if active is None:
            raise ValueError(
                f"active application iframe not found using {ACTIVE_IFRAME_SELECTOR!r}"
            )
        return active
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        frame_name, frame_url = _frame_name_url(fr)
        if frame == frame_name or frame in frame_url:
            return fr
    available = [
        f"{frame_name or 'unnamed'} ({frame_url[:120]})"
        for frame_name, frame_url in (_frame_name_url(fr) for fr in frames)
    ]
    raise ValueError(f"frame not found: {frame!r}; available frames: {available}")


async def ensure_vtable(frame: Frame, table_index: int | None = None) -> None:
    """绑定指定 VTable；未指定时优先可见弹窗内表格，再回退首个可见表格。"""
    await frame.wait_for_selector(".vtable", timeout=BIND_TIMEOUT_MS)
    await frame.evaluate(
        """index => {
          if (Number.isInteger(index)) window.__vtable_target_index = index;
          else delete window.__vtable_target_index;
          delete window._vtable;
        }""",
        table_index,
    )
    if not await frame.evaluate(_wrap(FAST_BIND)):
        await frame.evaluate(_wrap(BIND_BFS_FALLBACK))
    await frame.wait_for_function("() => !!window._vtable", timeout=BIND_TIMEOUT_MS)


async def _vtable_directory(page: Page, frame: Frame) -> list[dict[str, Any]]:
    """Return a compact, API-derived directory of visible VTable roots in one frame."""
    raw_roots = await frame.evaluate(_wrap(VTABLE_ROOTS))
    frame_offset = await _frame_page_offset(page, frame)
    viewport = await _page_viewport_size(page)
    tables: list[dict[str, Any]] = []
    for raw_root in raw_roots or []:
        if not isinstance(raw_root, dict):
            continue
        try:
            table_index = int(raw_root["table_index"])
            raw_box = raw_root["box"]
            box = {
                "x": round(frame_offset["x"] + float(raw_box["x"]), 2),
                "y": round(frame_offset["y"] + float(raw_box["y"]), 2),
                "width": round(float(raw_box["width"]), 2),
                "height": round(float(raw_box["height"]), 2),
            }
        except (KeyError, TypeError, ValueError):
            continue
        summary: dict[str, Any] | None = None
        try:
            await ensure_vtable(frame, table_index)
            summary = await frame.evaluate(_wrap(VTABLE_SUMMARY))
        except Exception:
            summary = None
        tables.append(
            {
                "table_index": table_index,
                "table_id": f"vtable-{table_index}",
                "context": "modal" if raw_root.get("in_modal") else "page",
                "page_box": box,
                "in_viewport": (
                    box["x"] < viewport["width"]
                    and box["y"] < viewport["height"]
                    and box["x"] + box["width"] > 0
                    and box["y"] + box["height"] > 0
                ),
                "summary": summary or {},
            }
        )
    return tables


async def cell_center(page: Page, frame: Frame, col: int, row: int) -> dict[str, float] | None:
    """单元格中心点,换算为主页面视口 CSS 像素(page.mouse 使用)。

    VTable 的 getCellRelativeRect 返回 canvas 相对坐标;加上 canvas 在 frame
    内的偏移,再补上 iframe 元素在主页面视口的偏移,即得 page.mouse 坐标。
    """
    rel = await frame.evaluate(_wrap2(CELL_RELATIVE_LOC), [col, row])
    if not rel:
        return None
    offset = await frame.evaluate(
        "() => { const t = window._vtable;"
        " const el = (t && t.canvas) || document.querySelector('.vtable canvas') || document.querySelector('.vtable');"
        " const r = el.getBoundingClientRect();"
        " return { left: r.left, top: r.top }; }"
    )
    try:
        x = float(rel["x"]) + float(offset["left"])
        y = float(rel["y"]) + float(offset["top"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    if frame != page.main_frame:
        frame_offset = await _frame_page_offset(page, frame)
        x += frame_offset["x"]
        y += frame_offset["y"]
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return {"x": x, "y": y}


async def cell_visible(frame: Frame, col: int, row: int) -> bool:
    return bool(await frame.evaluate(_cell_visible_js(col, row)))


async def ensure_cell_visible(page: Page, frame: Frame, col: int, row: int) -> bool:
    """保证目标单元格在视口内(含冻结行列):不可见则用 VTable scrollToCell 滚动。"""
    if await cell_visible(frame, col, row):
        return True
    await frame.evaluate(
        f"() => {{ const t = window._vtable; "
        f"if (t && t.scrollToCell) t.scrollToCell({{col: {col}, row: {row}}}); return true; }}"
    )
    for _ in range(SCROLL_WAIT_RAF):
        await frame.evaluate(_wrap(WAIT_RENDER))
    return await cell_visible(frame, col, row)


# ============================================================================
#  交互工具
# ============================================================================


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
        return compact_res
    return response


async def _open_url_impl(url: str, *, headless: bool = True) -> dict:
    """打开浏览器并导航到目标页面。"""
    if _browser is None or not _browser.is_connected():
        await _start_browser_impl(headless=headless)
    page = await _current_page_impl()
    await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
    return {
        "status": "opened",
        "page_id": _page_id(page),
        "url": page.url,
        "title": (await page.title())[:200],
    }


async def open_url(url: str, *, headless: bool = True) -> dict:
    """Serialize navigation against clicks, observers, and teardown."""
    async with _action_lock:
        return await _open_url_impl(url, headless=headless)


async def _cell_info_impl(col: int, row: int) -> dict:
    """读取单元格完整信息:值/行为分类/编辑能力/中心点/可见性,供交互前后确认。"""
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {"status": "failed", "reason": f"vtable-not-bound: {e}", "col": col, "row": row}

    classify = await frame.evaluate(_wrap2(CLASSIFY_CELL), [col, row])
    value = await frame.evaluate(
        f"() => {{ const t = window._vtable; return t ? t.getCellValue({col}, {row}) : null; }}"
    )
    center = await cell_center(page, frame, col, row)
    visible = await cell_visible(frame, col, row)
    return {
        "status": "ok",
        "col": col,
        "row": row,
        "value": value,
        "behavior": (classify or {}).get("behavior"),
        "editable": bool((classify or {}).get("editable")),
        "center": center,
        "in_viewport": visible,
    }


async def cell_info(col: int, row: int) -> dict:
    """Serialize a cell read against the shared browser page."""
    async with _action_lock:
        return await _cell_info_impl(col, row)


async def _click_cell_impl(
    col: int,
    row: int,
    *,
    double_click: bool = False,
    button: str = "left",
    verify: bool = True,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
    headless: bool = True,
) -> dict:
    """点击 VTable 单元格。

    流程:绑定实例 → 滚动到视口 → 取确定性中心点 → trusted 鼠标移动+点击 →
    回读选中区间/编辑器状态验证。返回结构化结果供 AI 确认,未命中自动重试一次。
    """
    if not 0 <= settle_ms <= OVERLAY_SETTLE_LIMIT_MS:
        raise ValueError(f"settle_ms must be between 0 and {OVERLAY_SETTLE_LIMIT_MS}")
    page = await _current_page_impl()
    installed = None
    frame_listener: _OverlayFrameListener | None = None
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    if not await ensure_cell_visible(page, frame, col, row):
        return {
            "status": "failed",
            "reason": "cell-not-in-viewport-after-scroll",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    center = await cell_center(page, frame, col, row)
    if not center:
        return {
            "status": "failed",
            "reason": "cell-rect-unavailable",
            "page_id": _page_id(page),
            "col": col,
            "row": row,
        }

    x, y = center["x"], center["y"]
    classify = await frame.evaluate(_wrap2(CLASSIFY_CELL), [col, row])
    before_sel = await frame.evaluate(
        "() => { const t = window._vtable; return t ? t.getSelectedCellRanges() : null; }"
    )
    # Visual evidence is optional. In particular, verify=False must remain a
    # pure trusted-input path and must not trigger a compositor screenshot.
    before_visual = None
    before_screenshot = None
    if verify:
        before_visual = await _cell_visual_state(frame, col, row)
        before_screenshot = await _cell_screenshot(page, x, y, frame=frame, col=col, row=row)
    # Arm immediately before the trusted input so setup/scroll mutations are
    # not attributed to the user action being measured.
    if observe_after:
        try:
            frame_listener, _ = await _acquire_overlay_frame_listener(
                page, persistent=False
            )
            await _arm_overlay_init_script(
                page, settle_ms=settle_ms, persistent=False
            )
            installed = await _install_overlay_observers(page, reset=True)
            await frame_listener.wait_pending()
            frame_listener.take_buffers()
            installed["frame_listener"] = frame_listener
        except Exception as exc:
            cleanup_errors = await _stop_overlay_observers_best_effort(page)
            cleanup_errors.extend(
                await _release_overlay_frame_listener(
                    page, frame_listener, persistent=False
                )
            )
            return {
                "status": "failed",
                "reason": f"overlay-arm-error: {exc}",
                "page_id": _page_id(page),
                "col": col,
                "row": row,
                "observer_errors": cleanup_errors,
                "observer_cleanup_failed": bool(cleanup_errors),
            }

    response: dict[str, Any]
    try:
        result = await _do_click(page, frame, x, y, double_click=double_click, button=button)
        if result["status"] == "failed":
            response = {**result, "col": col, "row": row}
        else:
            landed = True
            evidence: dict[str, Any] = {}
            if verify:
                landed, evidence = await _verify_landed(
                    page, frame, before_sel, col, row, before_visual, before_screenshot
                )
                retries_used = 0
                if not landed and VTABLE_VERIFICATION_STRATEGY.retry_count:
                    await _do_click(page, frame, x, y, double_click=double_click, button=button)
                    retries_used = 1
                    landed, evidence = await _verify_landed(
                        page, frame, before_sel, col, row, before_visual, before_screenshot
                    )
                evidence["retries_used"] = retries_used

            response = {
                "status": "clicked" if landed else "unverified",
                "col": col,
                "row": row,
                "point": {"x": x, "y": y},
                "cell": {
                    "behavior": (classify or {}).get("behavior"),
                    "editable": bool((classify or {}).get("editable")),
                },
                "double_click": double_click,
                "button": button,
                "verification": evidence,
            }
        if observe_after and settle_ms:
            await page.wait_for_timeout(settle_ms)
    except Exception as exc:
        response = {
            "status": "failed",
            "reason": f"cell-click-error: {exc}",
            "col": col,
            "row": row,
        }
    finally:
        if observe_after:
            await _finalize_overlay_observation(
                page, installed, response, settle_ms=settle_ms, max_results=max_results
            )
    response.setdefault("page_id", _page_id(page))
    frame_details = await _frame_context_details(page, frame)
    response["frame"] = frame_details
    verification = response.get("verification") or {}
    proof = []
    if verify:
        proof = [
            {"type": name, "matched": bool(verification.get(key))}
            for name, key in (
                ("selection-changed", "selection_changed"),
                ("target-selected", "target_selected"),
                ("editor-open", "editor_open"),
                ("scenegraph-changed", "scenegraph_changed"),
                ("screenshot-changed", "screenshot_changed"),
            )
        ]
        if verification.get("screenshot"):
            proof[-1]["matched"] = bool(verification["screenshot"].get("changed"))
    before_state: dict[str, Any] = {"selection": before_sel}
    if before_visual is not None:
        before_state["scenegraph_paints"] = before_visual.get("paints", [])
    if before_screenshot is not None:
        before_state["screenshot_digest"] = before_screenshot.get("digest")
    return _interaction_contract(
        response,
        action="dblclick" if double_click else "click",
        target={"kind": "vtable-cell", "col": col, "row": row},
        before_state=before_state,
        after_state={
            "scenegraph_paints": (verification.get("scenegraph") or {}).get(
                "after_paints", []
            ),
            "visible_overlay_count": len(response.get("visible_overlays") or []),
        },
        evidence=proof,
    )


async def click_cell(
    col: int,
    row: int,
    *,
    double_click: bool = False,
    button: str = "left",
    verify: bool = True,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
    headless: bool = True,
) -> dict:
    """Serialize a VTable click against the shared browser/observer state."""
    async with _action_lock:
        return await _click_cell_impl(
            col,
            row,
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            headless=headless,
        )


async def _page_viewport_size(page: Page) -> dict[str, float]:
    """Return the top page CSS viewport used by page.mouse coordinates."""
    size = await page.evaluate(
        "() => ({width: Number(window.innerWidth), height: Number(window.innerHeight)})"
    )
    width = float(size["width"])
    height = float(size["height"])
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("page viewport is unavailable")
    return {"width": width, "height": height}


async def _trusted_viewport_click(
    page: Page,
    x: float,
    y: float,
    *,
    double_click: bool = False,
    button: str = "left",
) -> dict:
    """Use one validated Playwright mouse primitive for all coordinate clicks."""
    try:
        x, y = float(x), float(y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite numbers")
        if button not in {"left", "middle", "right"}:
            raise ValueError("button must be left, middle or right")
        viewport = await _page_viewport_size(page)
        if not (0 <= x < viewport["width"] and 0 <= y < viewport["height"]):
            raise ValueError(
                f"point ({x:g}, {y:g}) is outside viewport "
                f"({viewport['width']:g} x {viewport['height']:g})"
            )
        await _ensure_cursor_helper(page)
        await _smooth_mouse_move_to(page, x, y)
        if VTABLE_SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__vtable_update_cursor && window.__vtable_update_cursor({x:.1f}, {y:.1f}, true, true)"
                )
            except Exception:
                pass
        if double_click:
            await page.mouse.dblclick(x, y, button=button)
        else:
            await page.mouse.click(x, y, button=button)
        if VTABLE_SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__vtable_update_cursor && window.__vtable_update_cursor({x:.1f}, {y:.1f}, false, false)"
                )
            except Exception:
                pass
        return {
            "status": "ok",
            "point": {"x": x, "y": y},
            "button": button,
            "double_click": double_click,
            "coordinate_space": "top-page-viewport-css-pixels",
        }
    except Exception as e:
        return {"status": "failed", "reason": f"click-error: {e}"}


async def _do_click(page: Page, frame: Frame, x: float, y: float, *, double_click: bool, button: str) -> dict:
    """执行一次 trusted VTable 点击,并等待表格下一次渲染。"""
    result = await _trusted_viewport_click(
        page, x, y, double_click=double_click, button=button
    )
    if result["status"] == "failed":
        return result
    try:
        await frame.evaluate(_wrap(WAIT_RENDER))
        return result
    except Exception as e:
        return {"status": "failed", "reason": f"click-error: {e}"}


async def _cell_visual_state(frame: Frame, col: int, row: int) -> dict[str, Any] | None:
    """Read the target cell's rendered scenegraph state without exposing its tree."""
    try:
        state = await frame.evaluate(_wrap2(CELL_VISUAL_STATE), [col, row])
        return state if isinstance(state, dict) else None
    except Exception:
        return None


_CELL_CANVAS_SLICE_JS = r"""([col, row, size]) => {
  const el = document.querySelector('.vtable');
  const canvas = el ? el.querySelector('canvas') : document.querySelector('canvas');
  if (!canvas) return null;
  const vt = window._vtable;
  let localX = 0, localY = 0;
  if (vt && vt.getCellRelativeRect) {
    const r = vt.getCellRelativeRect(col, row);
    if (r) {
      localX = r.left !== undefined ? r.left : (r.bounds ? r.bounds.x1 : 0);
      localY = r.top !== undefined ? r.top : (r.bounds ? r.bounds.y1 : 0);
    }
  }
  try {
    const s = Math.max(20, Math.min(100, Number(size) || 40));
    const smallCanvas = document.createElement('canvas');
    smallCanvas.width = s;
    smallCanvas.height = s;
    const sctx = smallCanvas.getContext('2d');
    if (!sctx) return null;
    sctx.drawImage(canvas, Math.max(0, localX), Math.max(0, localY), s, s, 0, 0, s, s);
    return smallCanvas.toDataURL('image/png');
  } catch (_) {
    return null;
  }
}"""


async def _cell_screenshot(
    page: Page,
    x: float,
    y: float,
    *,
    frame: Frame | None = None,
    col: int | None = None,
    row: int | None = None,
) -> dict[str, Any] | None:
    """Hash a visual snapshot of the cell region.

    Prefers in-memory canvas slice (0ms GPU flush, 0 flicker) when frame and col/row
    are provided; gracefully falls back to page.screenshot only if canvas is unavailable.
    """
    size = float(VTABLE_VERIFICATION_STRATEGY.screenshot_size)
    if frame is not None and col is not None and row is not None:
        try:
            slice_data = await frame.evaluate(_CELL_CANVAS_SLICE_JS, [col, row, size])
            if slice_data and isinstance(slice_data, str):
                digest = hashlib.sha256(slice_data.encode("utf-8")).hexdigest()[:16]
                return {
                    "digest": digest,
                    "clip": {"x": round(x, 2), "y": round(y, 2), "width": size, "height": size},
                }
        except Exception:
            pass
    try:
        viewport = await _page_viewport_size(page)
        left = max(0.0, min(float(x) - size / 2, viewport["width"] - size))
        top = max(0.0, min(float(y) - size / 2, viewport["height"] - size))
        image = await page.screenshot(clip={"x": left, "y": top, "width": size, "height": size})
        return {"digest": hashlib.sha256(image).hexdigest()[:16], "clip": {"x": round(left, 2), "y": round(top, 2), "width": size, "height": size}}
    except Exception:
        return None


async def _verify_landed(
    page: Page,
    frame: Frame,
    before_sel: Any,
    col: int,
    row: int,
    before_visual: dict[str, Any] | None,
    before_screenshot: dict[str, Any] | None,
) -> tuple[bool, dict]:
    """Verify VTable input through state, scenegraph, then screenshot evidence."""
    after = await frame.evaluate(
        "() => { const t = window._vtable; return t ? {"
        " sel: t.getSelectedCellRanges(),"
        " editing: !!(t.editorManager && t.editorManager.editingEditor),"
        " targetSelected: (() => { const ranges = t.getSelectedCellRanges?.() || [];"
        " return ranges.some(range => { const start = range.start || range.startCell || range;"
        " const end = range.end || range.endCell || start;"
        " const minCol = Math.min(Number(start.col), Number(end.col));"
        " const maxCol = Math.max(Number(start.col), Number(end.col));"
        " const minRow = Math.min(Number(start.row), Number(end.row));"
        " const maxRow = Math.max(Number(start.row), Number(end.row));"
        f" return {col} >= minCol && {col} <= maxCol && {row} >= minRow && {row} <= maxRow;"
        " }); })()"
        " } : null; }"
    )
    if not after:
        return False, {"landed": False, "reason": "vtable-gone"}
    sel_changed = after["sel"] != before_sel
    editor_open = bool(after["editing"])
    target_selected = bool(after.get("targetSelected"))
    after_visual = await _cell_visual_state(frame, col, row)
    scenegraph_changed = bool(
        before_visual
        and after_visual
        and before_visual.get("signature") != after_visual.get("signature")
    )
    after_screenshot = None
    screenshot_changed = False
    if not (sel_changed or editor_open or target_selected or scenegraph_changed):
        after_center = await cell_center(page, frame, col, row)
        if after_center:
            after_screenshot = await _cell_screenshot(
                page, after_center["x"], after_center["y"], frame=frame, col=col, row=row
            )
        screenshot_changed = bool(
            before_screenshot
            and after_screenshot
            and before_screenshot.get("digest") != after_screenshot.get("digest")
        )
    landed = sel_changed or editor_open or target_selected or scenegraph_changed or screenshot_changed
    evidence: dict[str, Any] = {
        "landed": landed,
        "selection_changed": sel_changed,
        "editor_open": editor_open,
        "target_selected": target_selected,
        "scenegraph_changed": scenegraph_changed,
    }
    if before_visual or after_visual:
        evidence["scenegraph"] = {
            "before_paints": (before_visual or {}).get("paints", []),
            "after_paints": (after_visual or {}).get("paints", []),
        }
    if before_screenshot or after_screenshot:
        evidence["screenshot"] = {
            "before_digest": (before_screenshot or {}).get("digest"),
            "after_digest": (after_screenshot or {}).get("digest"),
            "changed": screenshot_changed,
        }
    return landed, evidence


async def _click_dom_impl(
    role: str,
    name: str | None = None,
    description: str | None = None,
    *,
    frame: str | None = None,
    timeout_ms: float = 10_000,
    page: Page | None = None,
) -> dict:
    """按 ARIA role 点击表格外的 DOM 元素(工具栏/弹窗/按钮)。

    用 get_by_role(role, name=...) —— Playwright 语义定位 + 自动等待 +
    strict mode(多命中即报错)。Playwright 1.60 起支持 description 参数:
    当同 role 多个候选命中时,传一句额外可访问性描述即可消歧(strict 不再报错)。

    frame=None → 主页面;frame="vtable" → 自动定位含表格的 iframe;
    其它值按 iframe name 或 URL 子串匹配(如 "application" / "scm-spo")。
    """
    if page is None:
        page = await _current_page_impl()
    try:
        fr = await resolve_frame(page, frame)
        kwargs = {"name": name} if name else {}
        if description:
            kwargs["description"] = description
        locator = fr.get_by_role(role, **kwargs)
        await locator.click(timeout=timeout_ms)
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
    """Serialize a semantic DOM click against the shared browser state."""
    async with _action_lock:
        return await _click_dom_impl(
            role,
            name=name,
            description=description,
            frame=frame,
            timeout_ms=timeout_ms,
        )


async def _find_interaction_locator(
    page: Page,
    *,
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    timeout_ms: float = 3_000,
) -> tuple[Any, Frame, str]:
    """Resolve analysis-derived selectors in CSS, AX, XPath, text order."""
    if not any([role, text, placeholder, css, xpath]):
        raise ValueError("one locator is required: role, text, placeholder, css or xpath")
    if frame is not None:
        frames = [await resolve_frame(page, frame)]
    else:
        frames = []
        if in_iframe:
            active = await active_application_frame(page)
            if active is not None:
                frames.append(active)
        frames.append(page.main_frame)

    for candidate in frames:
        available: dict[str, Any] = {}
        if css:
            available["css"] = candidate.locator(css)
        if role:
            kwargs = {"name": name} if name else {}
            if description:
                kwargs["description"] = description
            available["ax-role"] = candidate.get_by_role(role, **kwargs)
        if xpath:
            available["xpath"] = candidate.locator(f"xpath={xpath}")
        if text:
            available["text"] = candidate.get_by_text(text, exact=True)
        if placeholder:
            available["placeholder"] = candidate.get_by_placeholder(placeholder, exact=True)
        locators = [
            (source, available[source])
            for source in LOCATOR_STRATEGY.order
            if source in available
        ]
        for source, locator in locators:
            try:
                if await locator.count():
                    return locator, candidate, source
            except Exception:
                continue
    raise ValueError("target control not found in the selected page/frame scope")


async def _visible_antd_dropdown(target: Any) -> Any | None:
    selector = ",".join(ACTIVE_PROFILE.dropdown_selectors)
    locator = target.locator(selector)
    try:
        for index in range(await locator.count() - 1, -1, -1):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                return candidate
    except Exception:
        return None
    return None


async def _click_unique_antd_option(
    dropdown: Any, option_text: str, *, timeout_ms: float
) -> dict[str, Any] | None:
    """Click one unambiguous visible option and report its semantic match."""
    for role in ("option", "menuitem", "treeitem"):
        locator = dropdown.get_by_role(role, name=option_text, exact=True)
        try:
            visible = [
                locator.nth(index)
                for index in range(await locator.count())
                if await locator.nth(index).is_visible()
            ]
        except Exception:
            visible = []
        if len(visible) == 1:
            await visible[0].click(timeout=timeout_ms)
            return {"match": "exact-role", "role": role, "text": option_text}
        if len(visible) > 1:
            raise ValueError(
                f"AntD option {option_text!r} has {len(visible)} exact {role} matches"
            )

    candidates = dropdown.locator(ACTIVE_PROFILE.dropdown_option_selector)
    visible_candidates: list[tuple[Any, str]] = []
    try:
        count = min(await candidates.count(), 200)
        for index in range(count):
            candidate = candidates.nth(index)
            if not await candidate.is_visible():
                continue
            text_value = " ".join((await candidate.inner_text()).split())
            if text_value:
                visible_candidates.append((candidate, text_value))
    except Exception:
        return None

    exact = [item for item in visible_candidates if item[1] == option_text]
    if len(exact) == 1:
        await exact[0][0].click(timeout=timeout_ms)
        return {"match": "exact-text", "text": exact[0][1]}
    if len(exact) > 1:
        raise ValueError(
            f"AntD option {option_text!r} has multiple exact matches: "
            f"{sorted({text for _, text in exact})[:20]}"
        )
    partial = [item for item in visible_candidates if option_text in item[1]]
    if len(partial) == 1:
        await partial[0][0].click(timeout=timeout_ms)
        return {"match": "unique-substring", "text": partial[0][1]}
    if len(partial) > 1:
        raise ValueError(
            f"AntD option {option_text!r} is ambiguous; candidates: "
            f"{sorted({text for _, text in partial})[:20]}"
        )
    return None


async def _perform_antd_select(
    page: Page,
    target_frame: Frame,
    locator: Any,
    option_text: str,
    *,
    timeout_ms: float,
) -> dict[str, Any]:
    """Open an AntD Portal select and choose one deterministic option."""
    first = locator.first
    component = await first.evaluate(
        """element => {
          const root = element.closest('.ant-select, .ant-cascader, .ant-tree-select');
          if (!root) return null;
          if (root.classList.contains('ant-cascader')) return 'antd-cascader';
          if (root.classList.contains('ant-tree-select')) return 'antd-tree-select';
          return 'antd-select';
        }"""
    )
    if not component:
        raise ValueError("target is not an Ant Design select component")
    before_text = ""
    try:
        before_text = " ".join((await first.inner_text()).split())[:200]
    except Exception:
        pass
    await first.click(timeout=timeout_ms)

    targets: list[Any] = [target_frame]
    if target_frame != page.main_frame:
        targets.append(page.main_frame)
    deadline = time.monotonic() + max(0.2, timeout_ms / 1000)
    while time.monotonic() < deadline:
        for target in targets:
            dropdown = await _visible_antd_dropdown(target)
            if dropdown is None:
                continue
            matched = await _click_unique_antd_option(
                dropdown, option_text, timeout_ms=max(200, timeout_ms)
            )
            if matched:
                after_text = ""
                try:
                    after_text = " ".join((await first.inner_text()).split())[:200]
                except Exception:
                    pass
                return {
                    "component": component,
                    "option": option_text,
                    "match": matched,
                    "trigger_text_before": before_text,
                    "trigger_text_after": after_text,
                    "portal_frame": _frame_details(page, target),
                }
        await page.wait_for_timeout(100)
    raise ValueError(f"AntD option not found: {option_text!r}")


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
    if action in {"click", "dblclick", "rightclick", "hover"}:
        try:
            target = locator.first
            box = await target.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                offset = await _frame_page_offset(page, target_frame)
                center_x = offset["x"] + box["x"] + box["width"] / 2
                center_y = offset["y"] + box["y"] + box["height"] / 2
                await _ensure_cursor_helper(page)
                await _smooth_mouse_move_to(page, center_x, center_y)
                if VTABLE_SHOW_CURSOR and action in {"click", "dblclick", "rightclick"}:
                    try:
                        await page.evaluate(
                            f"window.__vtable_update_cursor && window.__vtable_update_cursor({center_x:.1f}, {center_y:.1f}, true, true)"
                        )
                    except Exception:
                        pass
        except Exception:
            pass
    if action == "click":
        await locator.click(**kwargs)
    elif action == "dblclick":
        await locator.dblclick(**kwargs)
    elif action == "rightclick":
        await locator.click(button="right", **kwargs)
    elif action == "hover":
        await locator.hover(**kwargs)
    if VTABLE_SHOW_CURSOR and action in {"click", "dblclick", "rightclick"}:
        try:
            await page.evaluate(
                "window.__vtable_update_cursor && window.__vtable_update_cursor(window.__vtable_last_x || 0, window.__vtable_last_y || 0, false, false)"
            )
        except Exception:
            pass
    elif action == "fill":
        if value is None:
            raise ValueError("fill requires value")
        await locator.fill(value, **kwargs)
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
                await locator.first.evaluate(
                    "element => !!element.closest('.ant-select, .ant-cascader, .ant-tree-select')"
                )
            )
        except Exception:
            pass
        if is_antd:
            return await _perform_antd_select(
                page,
                target_frame,
                locator,
                value,
                timeout_ms=timeout_ms,
            )
        await locator.select_option(value, **kwargs)
        return {"component": "native-select", "option": value}
    else:
        raise ValueError(
            "unsupported action; use click, dblclick, rightclick, hover, fill, press, check, uncheck or select"
        )


async def _validate_analysis_reference(page: Page, analysis_id: str) -> dict[str, Any]:
    entry = _analysis_cache.get(analysis_id)
    if entry is None:
        return {"ok": False, "reason": "unknown-analysis-id"}
    if time.monotonic() - float(entry["created"]) > ANALYSIS_MAX_AGE_SECONDS:
        _analysis_cache.pop(analysis_id, None)
        return {"ok": False, "reason": "expired-analysis-id"}
    if entry["page_id"] != _page_id(page):
        return {"ok": False, "reason": "analysis-page-changed"}
    try:
        frame = await vtable_frame(page)
        table_index = (entry.get("options") or {}).get("table_index")
        await ensure_vtable(frame, table_index)
        details = await _frame_context_details(page, frame)
        if entry["frame_id"] != details.get("frame_id"):
            return {"ok": False, "reason": "analysis-frame-changed"}
        raw = await frame.evaluate(_wrap2(VTABLE_ANALYSIS), [entry["options"], None])
        signature = _analysis_layout_signature(
            raw or {}, str(details.get("frame_id") or ""), table_index
        )
    except Exception as exc:
        return {"ok": False, "reason": f"analysis-validation-error: {exc}"}
    if signature != entry["signature"]:
        return {
            "ok": False,
            "reason": "stale-coordinate",
            "expected_signature": entry["signature"],
            "current_signature": signature,
        }
    return {"ok": True, "analysis_id": analysis_id, "layout_signature": signature}


async def _focused_editable(page: Page) -> dict[str, Any] | None:
    for frame in page.frames:
        try:
            item = await frame.evaluate(
                """() => {
                  const element = document.activeElement;
                  if (!element || !element.matches('input:not([type="hidden"]),textarea,[contenteditable="true"]')) return null;
                  const rect = element.getBoundingClientRect();
                  const tag = element.tagName.toLowerCase();
                  const id = element.id ? `#${CSS.escape(element.id)}` : null;
                  const name = element.getAttribute('name');
                  return {
                    tag, input_type: tag === 'input' ? (element.type || 'text') : null,
                    selector: id || (name ? `${tag}[name="${CSS.escape(name)}"]` : tag),
                    placeholder: String(element.getAttribute('placeholder') || '').slice(0, 160) || null,
                    readonly: !!element.readOnly, disabled: !!element.disabled,
                    box: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                  };
                }"""
            )
            if not item:
                continue
            offset = await _frame_page_offset(page, frame)
            box = item.pop("box")
            item["page_box"] = {
                "x": round(offset["x"] + float(box["x"]), 2),
                "y": round(offset["y"] + float(box["y"]), 2),
                "width": round(float(box["width"]), 2),
                "height": round(float(box["height"]), 2),
            }
            item["frame"] = _frame_details(page, frame)
            return item
        except Exception:
            continue
    return None


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
    listener: _OverlayFrameListener | None = None
    response: dict[str, Any] = {"status": "failed", "action": action}
    focused_before: dict[str, Any] | None = None
    focused_after: dict[str, Any] | None = None
    try:
        coordinate_supplied = x is not None or y is not None
        locator_supplied = any([role, text, placeholder, css, xpath])
        if coordinate_supplied:
            if x is None or y is None:
                raise ValueError("coordinate click requires both x and y")
        if expect_input:
            focused_before = await _focused_editable(page)
        if observe_after:
            listener, _ = await _acquire_overlay_frame_listener(page, persistent=False)
            await _arm_overlay_init_script(
                page,
                settle_ms=settle_ms,
                extra_ms=max(0, int(timeout_ms)),
                persistent=False,
            )
            installed = await _install_overlay_observers(page, reset=True)
            installed["frame_listener"] = listener
            await listener.wait_pending()
            listener.take_buffers()
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
            except ValueError:
                if not coordinate_supplied:
                    raise
        if locator is None:
            if not coordinate_supplied:
                raise ValueError("one locator or x/y coordinates is required")
            if frame not in {None, "top", "main", "main_frame"}:
                raise ValueError("x/y are absolute top-page viewport coordinates; frame must be omitted")
            if action.lower().strip() not in {"click", "dblclick", "rightclick"}:
                raise ValueError("x/y support click, dblclick or rightclick only")
            if analysis_id:
                validation = await _validate_analysis_reference(page, analysis_id)
                if not validation.get("ok"):
                    raise ValueError(str(validation.get("reason") or "stale-coordinate"))
            normalized_action = action.lower().strip()
            clicked = await _trusted_viewport_click(
                page,
                float(x),
                float(y),
                double_click=normalized_action == "dblclick",
                button="right" if normalized_action == "rightclick" else "left",
            )
            if clicked["status"] != "ok":
                raise ValueError(clicked.get("reason", "coordinate click failed"))
            response = {
                "status": "acted",
                "page_id": _page_id(page),
                "action": action,
                "frame": _frame_details(page, page.main_frame),
                "point": clicked["point"],
                "coordinate_space": clicked["coordinate_space"],
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
            await _finalize_overlay_observation(
                page, installed, response, settle_ms=settle_ms, max_results=max_results
            )
        elif listener is not None:
            cleanup_errors = await _stop_overlay_observers_best_effort(page)
            cleanup_errors.extend(
                await _release_overlay_frame_listener(
                    page, listener, persistent=False
                )
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
            "visible_overlay_count": len(installed.get("baseline") or []) if installed else len(response.get("baseline") or []),
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
    """Perform one semantic DOM action in the active application scope."""
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


# ============================================================================
#  Ant Design Portal / overlay observation
# ============================================================================


async def _install_overlay_observers(
    page: Page, *, reset: bool = False
) -> dict[str, Any]:
    """Install a short-lived event buffer in every currently reachable frame.

    AntD normally mounts a Portal into the document body of the iframe that
    owns the React tree. Scanning all frames also captures applications that
    deliberately configure `getPopupContainer` to the top document.
    """
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
            # A frame can be attached while the click is in progress. Its
            # current overlays did not exist in the pre-click baseline, so
            # drain() turns these baseline entries into synthetic "added"
            # events after installing the observer.
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
    """Disconnect observers in every currently reachable frame.

    This fallback deliberately avoids installing new observers. It is used
    after a partially failed arm/drain so cleanup never expands the scope of
    the failed operation.
    """
    errors: list[dict[str, Any]] = []
    try:
        frames = list(page.frames)
    except Exception as exc:
        return [{"reason": f"observer-frame-list-error: {exc}"}]
    for frame in frames:
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
    """Read buffered mutations and current visible overlays from every frame."""
    events: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    stop_errors: list[dict[str, Any]] = []
    if frame_listener is not None:
        await frame_listener.wait_pending()
        listener_events, listener_errors = frame_listener.take_buffers()
        events.extend(listener_events)
        errors.extend(listener_errors)
    # Re-scan before draining so an iframe created during the action is
    # observed too. Its already-visible overlays become synthetic additions.
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
    }


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


async def _finalize_overlay_observation(
    page: Page,
    installed: dict[str, Any] | None,
    response: dict[str, Any],
    *,
    settle_ms: int,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> None:
    """Stop an observer and attach its result even when the action failed."""
    if installed is None:
        return
    frame_listener = installed.get("frame_listener")
    keep_listener = bool(installed.get("keep_listener", False))
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
                "observer_cleanup_failed": bool(cleanup_errors),
            }
        )
        return
    listener_errors = await _release_overlay_frame_listener(
        page, frame_listener, persistent=keep_listener
    )
    # Use the arm-time comparison baseline. The drain may discover a newly
    # attached iframe and include its current nodes in its diagnostic baseline;
    # those nodes must remain eligible as post-click additions.
    baseline = installed.get("baseline", [])
    raw_events = drained["events"]
    raw_current = drained["current"]
    raw_overlays = _new_overlays(baseline, raw_events, raw_current)
    ui_events = await _enrich_overlay_items(page, raw_events, max_results=max_results)
    overlays = await _enrich_overlay_items(page, raw_overlays, max_results=max_results)
    visible_overlays = await _enrich_overlay_items(
        page, raw_current, max_results=max_results
    )
    response.update(
        {
            "settle_ms": settle_ms,
            "baseline": (
                await _enrich_overlay_items(page, baseline, max_results=min(2, max_results))
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
            # A frame evaluation error while stop=True means that cleanup of
            # at least one frame could not be proven.
            "observer_cleanup_failed": bool(drained["stop_errors"] or listener_errors),
        }
    )


async def _scan_overlays_impl(
    *, max_results: int = OVERLAY_RESULT_LIMIT, scope: str = "active"
) -> dict:
    """Scan visible AntD/ARIA overlays across the top page and every iframe.

    This is intentionally a snapshot operation. For messages that may appear
    and disappear within a few hundred milliseconds, use `click_dom_and_observe`
    or `observe_overlays` before the action that produces them.
    """
    page = await _current_page_impl()
    installed: dict[str, Any] | None = None
    frame_listener: _OverlayFrameListener | None = None
    try:
        frame_listener, _ = await _acquire_overlay_frame_listener(
            page, persistent=False
        )
        installed = await _install_overlay_observers(page, reset=True)
        await frame_listener.wait_pending()
        frame_listener.take_buffers()
        drained = await _drain_overlay_observers(
            page, stop=True, frame_listener=frame_listener
        )
        listener_errors = await _release_overlay_frame_listener(
            page, frame_listener, persistent=False
        )
        allowed = await _scope_frame_ids(page, scope)
        visible_overlays = await _enrich_overlay_items(
            page,
            _filter_overlay_scope(drained["current"], allowed),
            max_results=max_results,
        )
        return {
            "status": "ok",
            "overlays": visible_overlays,
            "context": await _overlay_context(page, visible_overlays),
            "frame_count": _page_frame_count(page),
            "observer_errors": [
                *installed["errors"],
                *drained["errors"],
                *listener_errors,
            ],
            "observer_cleanup_failed": bool(drained["stop_errors"] or listener_errors),
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
            "reason": f"overlay-scan-error: {exc}",
            "overlays": [],
            "frame_count": _page_frame_count(page),
            "observer_errors": [
                *(installed or {}).get("errors", []),
                *cleanup_errors,
            ],
            "observer_cleanup_failed": bool(cleanup_errors),
        }


async def scan_overlays(
    *, max_results: int = OVERLAY_RESULT_LIMIT, scope: str = "active"
) -> dict:
    """Serialize a static overlay scan against the shared page."""
    async with _action_lock:
        return await _scan_overlays_impl(max_results=max_results, scope=scope)


async def _page_context_impl(*, max_results: int = 10) -> dict:
    """Return a compact page/frame/focus summary for the next AI action."""
    page = await _current_page_impl()
    try:
        title = (await page.title())[:200]
    except Exception:
        title = ""
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    active = await active_application_frame(page)
    active_id = _frame_id(page, active) if active is not None else ""
    frame_items = []
    for frame in frames[: max(1, int(max_results))]:
        detail = await _frame_context_details(page, frame)
        detail["scope"] = (
            "active_iframe" if detail["frame_id"] == active_id
            else "top_document" if frame == page.main_frame
            else "iframe"
        )
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
            await _frame_context_details(page, active) if active is not None else None
        ),
        "frames": frame_items,
        "focus_layer": overlays.get("context", {}).get("focus_layer"),
        "visible_overlays": overlays.get("overlays", []),
        "observer_errors": overlays.get("observer_errors", []),
    }


async def page_context(*, max_results: int = 10) -> dict:
    """Serialize a compact current-page context for MCP clients."""
    async with _action_lock:
        return await _page_context_impl(max_results=max_results)


async def _analyze_scope_impl(
    *, max_controls: int = 40, max_overlays: int = 10
) -> dict:
    """Analyze only the current application scope or its interactive focus layer."""
    page = await _current_page_impl()
    control_limit = max(1, min(100, int(max_controls)))
    overlay_limit = max(1, min(30, int(max_overlays)))
    try:
        title = (await page.title())[:200]
    except Exception:
        title = ""
    active = await active_application_frame(page)
    overlays = await _scan_overlays_impl(
        max_results=overlay_limit, scope="active"
    )
    focus = overlays.get("context", {}).get("focus_layer")
    interactive_focus = bool(
        focus
        and focus.get("visible", True)
        and focus.get("selector")
        and focus.get("kind") in {"dialog", "drawer", "dropdown", "popover"}
    )
    controls: list[dict[str, Any]] = []
    truncated = False
    scan_errors: list[dict[str, str]] = []
    scope: dict[str, Any]

    if interactive_focus:
        focus_frame = _find_frame_by_id(page, str(focus.get("frame_id", "")))
        if focus_frame is None:
            scan_errors.append({"reason": "focus-layer-frame-detached"})
            interactive_focus = False
        else:
            try:
                scanned = await _scan_controls_in_frame(
                    page,
                    focus_frame,
                    scope_selector=str(focus["selector"]),
                    scope_name="focus_layer",
                    max_results=control_limit,
                )
                controls = scanned["controls"]
                truncated = scanned["truncated"]
                scope = {
                    "mode": "focus_layer",
                    "kind": focus.get("kind"),
                    "selector": focus.get("selector"),
                    "frame": _frame_details(page, focus_frame),
                }
            except Exception as exc:
                scan_errors.append({"reason": f"focus-layer-scan-error: {exc}"})
                interactive_focus = False

    if not interactive_focus:
        targets: list[tuple[Frame, str]] = []
        if active is not None:
            targets.append((active, "active_iframe"))
        else:
            targets.append((page.main_frame, "top_document"))
        seen_frames: set[str] = set()
        collected: list[dict[str, Any]] = []
        for target, scope_name in targets:
            frame_id = _frame_id(page, target)
            if frame_id in seen_frames:
                continue
            seen_frames.add(frame_id)
            try:
                scanned = await _scan_controls_in_frame(
                    page,
                    target,
                    scope_selector=None,
                    scope_name=scope_name,
                    max_results=control_limit,
                )
                collected.extend(scanned["controls"])
                truncated = truncated or scanned["truncated"]
            except Exception as exc:
                scan_errors.append(
                    {"reason": f"control-scan-error: {exc}", "frame_id": frame_id}
                )
        controls = collected[:control_limit]
        truncated = truncated or len(collected) > control_limit
        scope = {
            "mode": "active_application" if active is not None else "top_document",
            "active_iframe": (
                await _frame_context_details(page, active) if active is not None else None
            ),
        }

    for index, control in enumerate(controls, start=1):
        control["ref"] = f"c{index}"
    return {
        "status": "ok",
        "profile": ACTIVE_PROFILE.name,
        "page": {
            "page_id": _page_id(page),
            "url": page.url,
            "title": title,
            **await _navigation_context(page),
        },
        "scope": scope,
        "focus_layer": focus,
        "messages": [
            item
            for item in overlays.get("overlays", [])
            if item.get("kind") == "notification"
        ],
        "controls": controls,
        "control_count": len(controls),
        "truncated": truncated,
        "errors": [*overlays.get("observer_errors", []), *scan_errors],
    }


async def analyze_scope(
    *, max_controls: int = 40, max_overlays: int = 10
) -> dict:
    """Serialize compact focus-aware control analysis for MCP clients."""
    async with _action_lock:
        return await _analyze_scope_impl(
            max_controls=max_controls, max_overlays=max_overlays
        )


async def _observe_overlays_impl(
    *,
    settle_ms: int = 300,
    stop: bool = True,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    """Collect overlay mutations after an observer has been armed.

    Calling this also arms the observer if needed, so it is useful as a
    standalone diagnostic tool. `settle_ms` is bounded to avoid turning an MCP
    tool call into an unbounded wait.
    """
    if not 0 <= settle_ms <= OVERLAY_SETTLE_LIMIT_MS:
        raise ValueError(f"settle_ms must be between 0 and {OVERLAY_SETTLE_LIMIT_MS}")
    page = await _current_page_impl()
    installed: dict[str, Any] | None = None
    frame_listener: _OverlayFrameListener | None = None
    try:
        frame_listener, _ = await _acquire_overlay_frame_listener(
            page, persistent=not stop
        )
        await _arm_overlay_init_script(
            page, settle_ms=settle_ms, persistent=not stop
        )
        installed = await _install_overlay_observers(page)
        installed["frame_listener"] = frame_listener
        installed["keep_listener"] = not stop
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
        events = await _enrich_overlay_items(page, drained["events"], max_results=max_results)
        overlays = await _enrich_overlay_items(page, detected, max_results=max_results)
        visible_overlays = await _enrich_overlay_items(
            page, drained["current"], max_results=max_results
        )
        return {
            "status": "ok",
            "settle_ms": settle_ms,
            "baseline": await _enrich_overlay_items(page, baseline, max_results=max_results),
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
            "stopped": stop,
            "observer_cleanup_failed": bool(
                stop and (drained["stop_errors"] or listener_errors)
            ),
        }
    except Exception as exc:
        cleanup_errors: list[dict[str, Any]] = []
        if installed is not None or frame_listener is not None:
            cleanup_errors = await _stop_overlay_observers_best_effort(page)
        cleanup_errors.extend(
            await _release_overlay_frame_listener(
                page, frame_listener, persistent=False
            )
        )
        return {
            "status": "failed",
            "reason": f"overlay-observe-error: {exc}",
            "baseline": (installed or {}).get("baseline", []),
            "events": [],
            "overlays": [],
            "visible_overlays": [],
            "frame_count": _page_frame_count(page),
            "observer_errors": [
                *(installed or {}).get("errors", []),
                *cleanup_errors,
            ],
            "stopped": True,
            "observer_cleanup_failed": bool(cleanup_errors),
        }


async def observe_overlays(
    *,
    settle_ms: int = 300,
    stop: bool = True,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    """Serialize an overlay observation against the shared page."""
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
        # The locator may wait for the control for up to timeout_ms. Keep the
        # future-frame bootstrap alive for that whole action plus the settle
        # window, otherwise a late iframe could outlive the init-script lease.
        await _arm_overlay_init_script(
            page,
            settle_ms=settle_ms,
            extra_ms=max(0, int(timeout_ms)),
            persistent=False,
        )
        installed = await _install_overlay_observers(page, reset=True)
        await frame_listener.wait_pending()
        frame_listener.take_buffers()  # discard arm-time frame events
        installed["frame_listener"] = frame_listener
        response = await _click_dom_impl(
            role,
            name=name,
            description=description,
            frame=frame,
            timeout_ms=timeout_ms,
            page=page,
        )
        if settle_ms:
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
                page,
                installed,
                response,
                settle_ms=settle_ms,
                max_results=max_results,
            )
        else:
            cleanup_errors = await _stop_overlay_observers_best_effort(page)
            cleanup_errors.extend(
                await _release_overlay_frame_listener(
                    page, frame_listener, persistent=False
                )
            )
            if cleanup_errors:
                response["observer_cleanup_failed"] = True
                response["observer_errors"] = cleanup_errors
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
    """Serialize a semantic click and its immediate overlay observation."""
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


# ============================================================================
#  新特性工具:AI 语义之眼(aria snapshot)+ 批量读表 + 表格元数据 + 文件拖放
# ============================================================================

# drop() 把 drag 事件派发在 locator 命中的元素上(非 position 下的元素),
# 因此须把 drop 落在真正持有 dragover 处理器的 canvas 上。临时属性打标定位。
_DROP_ATTR = "data-vtable-drop"

_DROP_TARGET_TAG = r"""
return (function(){
  const t = window._vtable;
  if (!t) return null;
  const canvas = t.canvas || document.querySelector('.vtable canvas');
  const el = canvas || document.querySelector('.vtable');
  if (!el) return null;
  const tag = 'pw' + String(Math.floor(Math.random() * 1e9));
  el.setAttribute('data-vtable-drop', tag);
  return { tag, target: canvas ? 'canvas' : 'container' };
})();
"""

_DROP_TARGET_UNTAG = r"""
(function(){
  document.querySelectorAll('[data-vtable-drop]').forEach(el => el.removeAttribute('data-vtable-drop'));
  return true;
})()
"""


async def _dom_snapshot_impl(
    selector: str | None = None,
    *,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
) -> dict:
    """抓取页面的 aria 快照(Playwright 1.60 起 mode='ai' / boxes 可选)。

    官方 Playwright MCP 正是把 accessibility tree 喂给 AI 实现"语义之眼"。
    VTable 本体是 canvas(单元格不在 a11y 树里,仍走确定性几何定位),
    但表格外的工具栏/弹窗/编辑器输入框全在树里 —— AI 交互前先读快照,
    拿到 [ref=xx] 元素引用与 [box=x,y,w,h] 视口坐标,再决定点哪个。
    mode='ai' 的元素引用可直接用于 get_by_role(role, name=..., description=...)。

    frame=None → 主 frame;frame="vtable" → 自动定位含表格的 iframe;
    其它值按 iframe name 或 URL 子串匹配(如 "application" / "scm-spo")。selector 非空时只快照该
    CSS 选择器在目标 frame 内命中的子树(默认整 frame)。
    """
    page = await _current_page_impl()
    try:
        fr = await resolve_frame(page, frame)
        kw = {"depth": depth, "boxes": boxes}
        if ai_mode:
            kw["mode"] = "ai"
        if selector:
            snap = await fr.locator(selector).aria_snapshot(**kw)
        else:
            # Frame 无 aria_snapshot 方法,用 :root 元素把快照范围圈定在 frame 文档内
            snap = await fr.locator(":root").aria_snapshot(**kw)
        return {"status": "ok", "selector": selector, "frame": frame, "snapshot": snap}
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"aria-snapshot-error: {e}",
            "selector": selector,
            "frame": frame,
        }


async def dom_snapshot(
    selector: str | None = None,
    *,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
) -> dict:
    """Serialize an accessibility snapshot against the shared page."""
    async with _action_lock:
        return await _dom_snapshot_impl(
            selector,
            frame=frame,
            depth=depth,
            boxes=boxes,
            ai_mode=ai_mode,
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
    quality: int | None = None,
    timeout_ms: float = 3_000,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    """Capture one resolved element or a validated top-page viewport rectangle."""
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
    elif not coordinate_supplied:
        raise ValueError("one locator or x/y/width/height viewport rectangle is required")
    elif frame not in {None, "top", "main", "main_frame"}:
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
    else:
        clip = {
            "x": max(0.0, float(x)),
            "y": max(0.0, float(y)),
            "width": float(width),
            "height": float(height),
        }
        if clip["width"] <= 0 or clip["height"] <= 0:
            raise ValueError("viewport screenshot width and height must be positive")
        frame_details = _frame_details(page, page.main_frame)

    screenshot_kwargs: dict[str, Any] = {"clip": clip, "type": image_format}
    if image_format == "jpeg" and quality is not None:
        if not 1 <= quality <= 100:
            raise ValueError("quality must be between 1 and 100")
        screenshot_kwargs["quality"] = quality
    image = await page.screenshot(**screenshot_kwargs)
    if len(image) > max_bytes:
        return {
            "status": "failed",
            "reason": "screenshot-too-large",
            "byte_size": len(image),
            "max_bytes": max_bytes,
            "clip": {key: round(value, 2) for key, value in clip.items()},
        }
    return {
        "status": "ok",
        "image_base64": base64.b64encode(image).decode("ascii"),
        "mime_type": f"image/{image_format}",
        "byte_size": len(image),
        "digest": hashlib.sha256(image).hexdigest()[:16],
        "clip": {key: round(value, 2) for key, value in clip.items()},
        "frame": frame_details,
        "locator": css or xpath or text or placeholder or role,
        "locator_source": locator_source,
        "coordinate_space": "top-page-viewport-css-pixels",
    }


async def screenshot_element(**kwargs: Any) -> dict[str, Any]:
    """Serialize a bounded screenshot operation against the shared browser state."""
    async with _action_lock:
        return await _screenshot_element_impl(**kwargs)


def _analysis_geometry(
    geometry: Any,
    *,
    frame_offset: dict[str, float],
    canvas_box: dict[str, float],
    viewport: dict[str, float],
    source: str,
) -> dict[str, Any] | None:
    """Convert VTable canvas-local geometry to a safe top-page viewport point."""
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


def _analysis_layout_signature(
    raw: dict[str, Any], frame_id: str, table_index: int | None = None
) -> str:
    """Hash layout and target geometry without hashing business cell values."""
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
                        "geometry": item.get("geometry"),
                        "icons": [
                            {"name": icon.get("name"), "box": icon.get("box")}
                            for icon in item.get("icons") or []
                        ],
                    }
                    for item in column.get("header") or []
                ],
                "cells": [
                    {
                        "row": item.get("row"),
                        "geometry": item.get("geometry"),
                        "targets": [
                            {"name": target.get("name"), "box": target.get("box")}
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
        "scrollLeft": meta.get("scrollLeft"),
        "scrollTop": meta.get("scrollTop"),
        "canvas_box": meta.get("canvas_box"),
        "columns": columns,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _remember_analysis(
    *, page_id: str, frame_id: str, signature: str, options: dict[str, Any]
) -> str:
    global _analysis_counter
    _analysis_counter += 1
    analysis_id = f"analysis-{_analysis_counter}-{signature[:8]}"
    _analysis_cache[analysis_id] = {
        "page_id": page_id,
        "frame_id": frame_id,
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
) -> dict:
    """Build a compact, read-only VTable interaction model for the current page."""
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
    frame = await vtable_frame(page)
    try:
        tables = await _vtable_directory(page, frame)
        frame_details = await _frame_context_details(page, frame)
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
        await ensure_vtable(frame, selected_index)
        raw = await frame.evaluate(_wrap2(VTABLE_ANALYSIS), [options, None])
        if not raw:
            raise ValueError("scenegraph or canvas unavailable")
        raw_meta = raw.get("meta") or {}
        raw_canvas = raw_meta["canvas_box"]
        canvas_box = {key: float(raw_canvas[key]) for key in ("x", "y", "width", "height")}
        if not all(math.isfinite(value) for value in canvas_box.values()):
            raise ValueError("non-finite canvas geometry")
        frame_offset = await _frame_page_offset(page, frame)
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
        page_id=_page_id(page), frame_id=frame_id, signature=signature, options=options
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
        )


async def _resolve_vtable_cell_impl(
    field: str, record_index: int | list[int]
) -> dict:
    """Resolve a semantic VTable target without reading canvas DOM geometry."""
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-not-bound: {exc}",
            "field": field,
            "record_index": record_index,
        }
    try:
        resolved = await frame.evaluate(
            _wrap2(RESOLVE_CELL), [field, record_index]
        )
    except Exception as exc:
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": f"vtable-address-error: {exc}",
            "field": field,
            "record_index": record_index,
        }
    if not resolved or not resolved.get("ok"):
        return {
            "status": "failed",
            "page_id": _page_id(page),
            "reason": (resolved or {}).get("reason", "vtable-address-unavailable"),
            "field": field,
            "record_index": record_index,
            "address": (resolved or {}).get("address"),
        }
    col, row = int(resolved["col"]), int(resolved["row"])
    in_viewport = await cell_visible(frame, col, row)
    center = await cell_center(page, frame, col, row) if in_viewport else None
    return {
        "status": "ok",
        "page_id": _page_id(page),
        "field": field,
        "record_index": record_index,
        "address": {"col": col, "row": row},
        "value": resolved.get("value"),
        "type": resolved.get("type"),
        "header_paths": resolved.get("headerPaths"),
        "resolved_by": resolved.get("method"),
        "in_viewport": in_viewport,
        "center": center,
        "frame": _frame_details(page, frame),
    }


async def resolve_vtable_cell(
    field: str, record_index: int | list[int]
) -> dict:
    """Serialize semantic field/record address resolution."""
    async with _action_lock:
        return await _resolve_vtable_cell_impl(field, record_index)


async def click_vtable_cell_by_field(
    field: str,
    record_index: int | list[int],
    *,
    double_click: bool = False,
    button: str = "left",
    verify: bool = True,
    observe_after: bool = False,
    settle_ms: int = 300,
    max_results: int = OVERLAY_RESULT_LIMIT,
) -> dict:
    """Resolve a business field/record through VTable APIs, then trusted-click it."""
    async with _action_lock:
        resolved = await _resolve_vtable_cell_impl(field, record_index)
        if resolved.get("status") != "ok":
            return resolved
        address = resolved["address"]
        result = await _click_cell_impl(
            int(address["col"]),
            int(address["row"]),
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
        )
        result["target"] = {
            "field": field,
            "record_index": record_index,
            "col": int(address["col"]),
            "row": int(address["row"]),
            "resolved_by": resolved.get("resolved_by"),
        }
        if result.get("interaction"):
            result["interaction"]["target"].update(result["target"])
        return result


async def _table_meta_impl() -> dict:
    """读取表格规模/冻结行列/主题等元数据(VTable 内部 API,防御性取值)。

    AI 拿到 rowCount/colCount/frozenRowCount 后,才能规划批量读取范围、
    判断冻结列偏移、决定点击是否要先滚动 —— 是"先看全局再动手"的第一步。
    """
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {"status": "failed", "reason": f"vtable-not-bound: {e}"}
    meta = await frame.evaluate(_wrap(TABLE_META))
    return {"status": "ok", "meta": meta or {}}


async def table_meta() -> dict:
    """Serialize a table metadata read against the shared browser page."""
    async with _action_lock:
        return await _table_meta_impl()


async def _cells_read_impl(col0: int, row0: int, col1: int, row1: int) -> dict:
    """批量读取矩形区域(col0,row0)-(col1,row1)单元格值,行优先返回。"""
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {
            "status": "failed",
            "reason": f"vtable-not-bound: {e}",
            "range": [col0, row0, col1, row1],
        }
    data = await frame.evaluate(_wrap4(READ_CELLS), [col0, row0, col1, row1])
    if not data:
        return {"status": "failed", "reason": "read-cells-failed", "range": [col0, row0, col1, row1]}
    return {"status": "ok", "range": data, "rows": len(data["values"])}


async def cells_read(col0: int, row0: int, col1: int, row1: int) -> dict:
    """Serialize a table range read against the shared browser page."""
    async with _action_lock:
        return await _cells_read_impl(col0, row0, col1, row1)


async def _drop_files_impl(
    col: int,
    row: int,
    files: list[str],
    data: dict[str, str] | None = None,
) -> dict:
    """把文件拖放到指定单元格(Playwright 1.60 的 Locator.drop)。

    VTable 单元格拖放上传(图片/文件列)走 native dragenter/dragover/drop。
    drop() 把 drag 事件派发在 locator 命中的元素上,故先给真正持有 dragover
    处理器的 canvas 打临时标记、以其为落点,position 直接用 getCellRelativeRect
    的 canvas 相对中心点;canvas 不可用或未接受时回退 .vtable 容器。
    iframe 内 VTable:落点元素与相对坐标都取自身所在 frame。
    """
    page = await _current_page_impl()
    frame = await vtable_frame(page)
    base = {"col": col, "row": row}
    try:
        await ensure_vtable(frame)
    except Exception as e:
        return {"status": "failed", "reason": f"vtable-not-bound: {e}", **base}

    if not await ensure_cell_visible(page, frame, col, row):
        return {"status": "failed", "reason": "cell-not-in-viewport-after-scroll", **base}

    rel = await frame.evaluate(_wrap2(CELL_RELATIVE_LOC), [col, row])
    if not rel:
        return {"status": "failed", "reason": "cell-rect-unavailable", **base}

    payload: dict = {"files": files}
    if data:
        payload["data"] = data

    errors: list[str] = []
    try:
        # 1) 首选 canvas(持有 VTable dragover 处理器);getCellRelativeRect 即相对 canvas
        tagged = await frame.evaluate(_wrap(_DROP_TARGET_TAG))
        if tagged and tagged["target"] == "canvas":
            try:
                await frame.locator(f'[{_DROP_ATTR}="{tagged["tag"]}"]').drop(
                    payload, position={"x": rel["x"], "y": rel["y"]}
                )
                return await _drop_done(frame, base, rel, files, data)
            except Exception as e:
                errors.append(f"canvas-drop: {e}")
            finally:
                await frame.evaluate(_wrap(_DROP_TARGET_UNTAG))

        # 2) 回退 .vtable 容器:换算 position 为容器相对坐标(同在 frame 文档内)
        boxes = await frame.evaluate(_wrap(DROP_TARGET_BOXES))
        if boxes:
            pos = {
                "x": boxes["canvas"]["left"] + rel["x"] - boxes["vtable"]["left"],
                "y": boxes["canvas"]["top"] + rel["y"] - boxes["vtable"]["top"],
            }
            try:
                await frame.locator(".vtable").drop(payload, position=pos)
                return await _drop_done(frame, base, rel, files, data)
            except Exception as e:
                errors.append(f"container-drop: {e}")
    except Exception as e:
        errors.append(f"drop-plumbing: {e}")

    return {
        "status": "failed",
        "reason": " | ".join(errors) or "drop-target-unavailable",
        **base,
    }


async def drop_files(
    col: int,
    row: int,
    files: list[str],
    data: dict[str, str] | None = None,
) -> dict:
    """Serialize a file drop against the shared browser page."""
    async with _action_lock:
        return await _drop_files_impl(col, row, files, data)


async def _drop_done(
    frame: Frame, base: dict, rel: dict[str, float], files: list[str], data: dict | None
) -> dict:
    """drop 成功后回读编辑器状态,给 AI 验证证据。"""
    editing = await frame.evaluate(
        "() => { const t = window._vtable; "
        "return !!(t && t.editorManager && t.editorManager.editingEditor); }"
    )
    return {
        "status": "dropped",
        "point_rel": {"x": rel["x"], "y": rel["y"]},
        "files": files,
        "data": data,
        "editor_open": editing,
        **base,
    }
