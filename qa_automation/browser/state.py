"""Browser runtime state registry: single _BrowserState, page/frame registries, id helpers.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import logging
import math
import subprocess
import weakref
from dataclasses import dataclass, field
from typing import Any

from ..config import NAV_TIMEOUT_MS

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

logger = logging.getLogger("qa_automation.browser")


@dataclass
class _BrowserState:
    """Browser lifecycle, session/page registry, and download bookkeeping."""

    browser: Browser | None = None
    pw: Any = None
    cdp: bool = False
    cdp_url: str | None = None
    chrome_process: subprocess.Popen[Any] | None = None
    chrome_port: int | None = None
    chrome_profile: str | None = None
    chrome_profile_owned: bool = False
    selected_page: Page | None = None
    selected_context: Any | None = None
    page_id_counter: int = 0
    page_ids: weakref.WeakKeyDictionary[Any, str] = field(
        default_factory=weakref.WeakKeyDictionary
    )
    page_frame_ids: weakref.WeakKeyDictionary[Any, weakref.WeakKeyDictionary[Any, str]] = (
        field(default_factory=weakref.WeakKeyDictionary)
    )
    page_frame_counters: weakref.WeakKeyDictionary[Any, int] = field(
        default_factory=weakref.WeakKeyDictionary
    )
    fallback_frame_ids: dict[tuple[int, int], str] = field(default_factory=dict)
    fallback_frame_counters: dict[int, int] = field(default_factory=dict)
    context_ids: weakref.WeakKeyDictionary[Any, str] = field(
        default_factory=weakref.WeakKeyDictionary
    )
    fallback_context_ids: dict[int, str] = field(default_factory=dict)
    context_id_counter: int = 0
    context_names: dict[str, str] = field(default_factory=dict)
    owned_contexts: dict[str, Any] = field(default_factory=dict)
    # 下载监听去重:弱引用集合,对象回收后条目自动消失,
    # 避免 id() 地址复用导致新页面被误判为已注册(下载丢失)。
    download_pages: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    download_contexts: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    # 仅不可 weakref 对象(如部分测试 mock)走 id() 兜底
    download_page_ids: set[int] = field(default_factory=set)
    download_context_ids: set[int] = field(default_factory=set)
    download_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    download_failures: list[str] = field(default_factory=list)

    def reset(self) -> None:
        """Close-browser 复位:清空连接/会话/缓存,计数器保留保证 id 全局唯一。"""
        self.browser = None
        self.pw = None
        self.cdp = False
        self.cdp_url = None
        self.selected_page = None
        self.selected_context = None
        self.page_ids.clear()
        self.page_frame_ids.clear()
        self.page_frame_counters.clear()
        self.fallback_frame_ids.clear()
        self.fallback_frame_counters.clear()
        self.context_ids.clear()
        self.fallback_context_ids.clear()
        self.context_names.clear()
        self.owned_contexts.clear()
        self.download_pages.clear()
        self.download_contexts.clear()
        self.download_page_ids.clear()
        self.download_context_ids.clear()
        self.download_tasks.clear()
        self.download_failures.clear()

    def reset_chrome(self) -> None:
        self.chrome_process = None
        self.chrome_port = None
        self.chrome_profile = None
        self.chrome_profile_owned = False


_state = _BrowserState()


_action_lock = asyncio.Lock()


_page_preference_probe: Any | None = None


def set_page_preference_probe(probe: Any) -> None:
    global _page_preference_probe
    _page_preference_probe = probe

def _get_page_preference_probe() -> Any:
    """读取当前探测钩子。

    必须走函数而非直接 import 名字:跨模块 ``from .state import _page_preference_probe``
    只会绑定 import 时的值(None),组合层后注入的钩子会读不到。
    """
    return _page_preference_probe



_FALLBACK_REGISTRY_LIMIT = 4096


def _prune_fallback_registry(registry: dict[Any, Any]) -> None:
    """fallback 注册表超限时丢弃最旧的一半(保序 dict),防 id() 键无限增长。"""
    if len(registry) < _FALLBACK_REGISTRY_LIMIT:
        return
    for key in list(registry)[: len(registry) // 2]:
        del registry[key]


def _page_id(page: Page) -> str:
    try:
        known = _state.page_ids.get(page)
        if known:
            return known
        _state.page_id_counter += 1
        value = f"page-{_state.page_id_counter}"
        _state.page_ids[page] = value
        return value
    except TypeError:
        return f"page-object-{id(page)}"


def _context_id(context: Any, *, name: str | None = None) -> str:
    try:
        known = _state.context_ids.get(context)
        if known:
            if name:
                _state.context_names[known] = name
            return known
        _state.context_id_counter += 1
        value = (
            "session-default" if not _state.context_ids else f"session-{_state.context_id_counter}"
        )
        _state.context_ids[context] = value
    except TypeError:
        key = id(context)
        known = _state.fallback_context_ids.get(key)
        if known:
            if name:
                _state.context_names[known] = name
            return known
        _prune_fallback_registry(_state.fallback_context_ids)
        _state.context_id_counter += 1
        value = f"session-object-{_state.context_id_counter}"
        _state.fallback_context_ids[key] = value
    if name:
        _state.context_names[value] = name
    return value


def _frame_id(page: Page, frame: Frame) -> str:
    if frame == page.main_frame:
        return "frame-0:unnamed" if not frame.name else f"frame-0:{frame.name}"
    try:
        page_frames = _state.page_frame_ids.setdefault(page, weakref.WeakKeyDictionary())
        if frame in page_frames:
            return page_frames[frame]
        counter = _state.page_frame_counters.get(page, 0) + 1
        _state.page_frame_counters[page] = counter
        name_part = frame.name if frame.name else "unnamed"
        value = f"frame-{counter}:{name_part}"
        page_frames[frame] = value
        return value
    except TypeError:
        key = (id(page), id(frame))
        if key in _state.fallback_frame_ids:
            return _state.fallback_frame_ids[key]
        _prune_fallback_registry(_state.fallback_frame_ids)
        _prune_fallback_registry(_state.fallback_frame_counters)
        counter = _state.fallback_frame_counters.get(id(page), 0) + 1
        _state.fallback_frame_counters[id(page)] = counter
        name_part = frame.name if frame.name else "unnamed"
        value = f"frame-object-{counter}:{name_part}"
        _state.fallback_frame_ids[key] = value
        return value


def _frame_name_url(frame: Frame) -> tuple[str, str]:
    try:
        name = frame.name
    except Exception:
        name = ""
    try:
        url = frame.url
    except Exception:
        url = ""
    return name, url


def _compact_frame_url(url: str, *, limit: int = 140) -> str:
    """砍掉查询串并按需截断路径。

    为什么要砍：APS 这类 iframe 套壳应用把整坨业务参数挂在 src 上，实测单条
    `frame_url` 达 380 字符 ≈ 110 token（15 个 id + 15 个 roleCode）。而它是
    **每个**工具的响应都要回显一遍的字段——实测占识别轮总 token 的 22%
    （`ui_page_context` 单工具 44%、`vtable_cell_info` 查一个单元格 43%）。

    消费侧只需要路径来判定"这是哪个功能模块"（tests/e2e 就是按
    `/cleanChangeover` 这类路径子串判定的），查询串无任何代码读取。
    """
    if not url:
        return ""
    base = url.split("?", 1)[0].split("#", 1)[0]
    return base if len(base) <= limit else base[:limit] + "…"


def _frame_details(page: Page, frame: Frame, *, full_url: bool = False) -> dict[str, Any]:
    """frame 的紧凑描述。默认只回路径；`full_url=True` 才带完整 src。"""
    name, url = _frame_name_url(frame)
    return {
        "frame_id": _frame_id(page, frame),
        "frame_url": url if full_url else _compact_frame_url(url),
        "frame_name": name,
    }


def _session_summary(context: Any, index: int, selected: bool) -> dict[str, Any]:
    session_id = _context_id(context)
    return {
        "session_id": session_id,
        "name": _state.context_names.get(session_id, f"context-{index}"),
        "context_index": index,
        "page_count": len(getattr(context, "pages", [])),
        "selected": selected,
        "managed": session_id in _state.owned_contexts,
    }


def _select_page_object(page: Page) -> Page:
    _state.selected_page = page
    # 函数内导入切断 state<->downloads 的模块级循环
    from .downloads import _watch_download_page

    _watch_download_page(page)
    _page_id(page)
    try:
        page.set_default_timeout(3_000)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    except Exception:
        pass
    return page


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


async def _frame_context_details(
    page: Page, frame: Frame, *, full_url: bool = False
) -> dict[str, Any]:
    details: dict[str, Any] = _frame_details(page, frame, full_url=full_url)
    if frame == page.main_frame:
        return details
    try:
        element = await frame.frame_element()
        attrs = await element.evaluate(
            "(el) => ({id: el.id || '', name: el.getAttribute('name') || ''})"
        )
        details["iframe"] = attrs
    except Exception:
        details["iframe"] = None
    return details


async def _frame_page_offset(page: Page, frame: Frame) -> dict[str, float]:
    if frame == page.main_frame:
        return {"x": 0.0, "y": 0.0}
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
        if box:
            border = await element.evaluate(
                """el => {
                  const s = window.getComputedStyle(el);
                  return {
                    left: parseFloat(s.borderLeftWidth) || 0,
                    top: parseFloat(s.borderTopWidth) || 0,
                  };
                }"""
            )
            return {
                "x": float(box["x"]) + float(border.get("left", 0.0)),
                "y": float(box["y"]) + float(border.get("top", 0.0)),
            }
    except Exception:
        pass
    return {"x": 0.0, "y": 0.0}
