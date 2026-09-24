"""Browser process lifecycle, CDP connection, session isolation, and page registry.

全部浏览器运行态收敛在单一 :data:`_state`(:class:`_BrowserState`)实例中,
关闭/重置只调用 ``_state.reset()``,不再手工清理十余个模块级全局变量。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import weakref
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

from .config import (
    NAV_TIMEOUT_MS,
    PLAYWRIGHT_INSTALL_HINT,
    SHOW_CURSOR,
    credential_missing_message,
    resolve_login_credentials,
)
from .mouse import _WIN_CURSOR_HELPER_SCRIPT, _reset_last_mouse_point
from .workspace import artifact_dir, artifact_file, resolve_workspace_path

from .auth import (
    build_cookies_to_inject,
    extract_parent_domain,
    recognize_captcha_digits,
    scm_api_login,
)

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
# 页面偏好探测钩子:async (page) -> bool。通用层不依赖任何组件适配器,
# 由组合层(见 qa_automation/__init__.py)注入 VTable/Profile 感知的实现。
_page_preference_probe: Any | None = None


def set_page_preference_probe(probe: Any) -> None:
    global _page_preference_probe
    _page_preference_probe = probe


async def _persist_download(download: Any) -> None:
    try:
        failure = await download.failure()
        if failure:
            raise RuntimeError(failure)
        target = artifact_file(
            "downloads",
            download.suggested_filename,
            fallback="download",
        )
        await download.save_as(str(target))
    except Exception as exc:
        _state.download_failures.append(str(exc))


def _schedule_download(download: Any) -> None:
    task = asyncio.create_task(_persist_download(download))
    _state.download_tasks.add(task)
    task.add_done_callback(_state.download_tasks.discard)


# 不可 weakref 对象的 id() 兜底注册表容量上限,防止地址键无限增长
_FALLBACK_REGISTRY_LIMIT = 4096


def _prune_fallback_registry(registry: dict[Any, Any]) -> None:
    """fallback 注册表超限时丢弃最旧的一半(保序 dict),防 id() 键无限增长。"""
    if len(registry) < _FALLBACK_REGISTRY_LIMIT:
        return
    for key in list(registry)[: len(registry) // 2]:
        del registry[key]


def _watch_download_page(page: Page) -> None:
    try:
        if page in _state.download_pages:
            return
        _state.download_pages.add(page)
    except TypeError:
        # 不可 weakref 的对象:退化为 id() 去重,超限时整体清空(该场景仅测试 mock)
        key = id(page)
        if key in _state.download_page_ids:
            return
        if len(_state.download_page_ids) >= _FALLBACK_REGISTRY_LIMIT:
            _state.download_page_ids.clear()
        _state.download_page_ids.add(key)
    page.on("download", _schedule_download)


def _watch_download_context(context: Any) -> None:
    try:
        if context in _state.download_contexts:
            return
        _state.download_contexts.add(context)
    except TypeError:
        key = id(context)
        if key in _state.download_context_ids:
            return
        if len(_state.download_context_ids) >= _FALLBACK_REGISTRY_LIMIT:
            _state.download_context_ids.clear()
        _state.download_context_ids.add(key)
    context.on("page", _watch_download_page)
    for page in context.pages:
        _watch_download_page(page)


async def _configure_browser_downloads(browser: Browser) -> dict[str, Any]:
    directory = artifact_dir("downloads")
    result: dict[str, Any] = {"download_dir": str(directory)}
    try:
        session = await browser.new_browser_cdp_session()
        try:
            await session.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(directory),
                    "eventsEnabled": True,
                },
            )
        finally:
            await session.detach()
        result["download_behavior"] = "workspace"
    except Exception as exc:
        result["download_behavior"] = "listener-fallback"
        result["download_configuration_error"] = str(exc)
    return result


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


async def _start_browser_impl(headless: bool = True) -> dict:
    if _state.browser is not None and _state.browser.is_connected():
        return {"status": "already-open", "browser": "chromium", "headless": headless}
    if async_playwright is None:
        raise RuntimeError(PLAYWRIGHT_INSTALL_HINT)

    _state.pw = await async_playwright().start()
    try:
        _state.browser = await _state.pw.chromium.launch(headless=headless)
    except Exception as first_error:
        try:
            _state.browser = await _state.pw.chromium.launch(channel="chrome", headless=headless)
        except Exception as fallback_error:
            await _state.pw.stop()
            _state.pw = None
            raise first_error from fallback_error
    download_config = await _configure_browser_downloads(_state.browser)
    _state.cdp = False
    _state.selected_page = None
    _state.selected_context = _state.browser.contexts[0] if _state.browser.contexts else None
    if _state.selected_context is not None:
        _context_id(_state.selected_context, name="default")
        _watch_download_context(_state.selected_context)
        if SHOW_CURSOR:
            try:
                await _state.selected_context.add_init_script(_WIN_CURSOR_HELPER_SCRIPT)
            except Exception:
                pass
    return {
        "status": "opened",
        "browser": "chromium",
        "headless": headless,
        **download_config,
    }


async def start_browser(headless: bool = True) -> dict:
    async with _action_lock:
        return await _start_browser_impl(headless=headless)
def _normalize_cdp_url(cdp_url: str) -> str:
    value = str(cdp_url).strip()
    if not value:
        raise ValueError("cdp_url must not be empty")
    return value.rstrip("/")



async def _connect_browser_impl(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    cdp_url = _normalize_cdp_url(cdp_url)

    if _state.browser is not None and _state.browser.is_connected():
        if _state.cdp and _state.cdp_url == cdp_url:
            tabs = [
                page.url[:120]
                for context in _state.browser.contexts
                for page in context.pages
            ]
            return {
                "status": "already-connected",
                "cdp": cdp_url,
                "browser": _state.browser.version,
                "contexts": len(_state.browser.contexts),
                "tabs": tabs,
            }
        # 切换端点前释放当前连接；受管 Chrome 仍按 close 的既有语义清理。
        await _close_browser_impl()
    elif _state.pw is not None:
        # 处理浏览器已断开但 Playwright driver 尚未释放的半连接状态。
        try:
            await _state.pw.stop()
        except Exception:
            pass
        _state.reset()

    if async_playwright is None:
        raise RuntimeError(PLAYWRIGHT_INSTALL_HINT)

    _state.pw = await async_playwright().start()
    try:
        _state.browser = await _state.pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        try:
            await _state.pw.stop()
        finally:
            _state.pw = None
            _state.browser = None
        raise RuntimeError(
            f"无法连接 CDP 浏览器 {cdp_url!r}。请确认 Chrome 已使用 "
            "--remote-debugging-port 启动，且端口可访问。原始错误: "
            f"{exc}"
        ) from exc

    download_config = await _configure_browser_downloads(_state.browser)
    _state.cdp = True
    _state.cdp_url = cdp_url
    _state.selected_page = None
    _state.selected_context = (
        _state.browser.contexts[0] if _state.browser.contexts else None
    )
    for index, context in enumerate(_state.browser.contexts):
        _context_id(context, name="default" if index == 0 else None)
        _watch_download_context(context)
        if not SHOW_CURSOR:
            continue
        try:
            await context.add_init_script(_WIN_CURSOR_HELPER_SCRIPT)
            for page in context.pages:
                if page.url and page.url != "about:blank":
                    try:
                        await asyncio.wait_for(
                            page.evaluate(_WIN_CURSOR_HELPER_SCRIPT), timeout=1.0
                        )
                    except Exception:
                        pass
        except Exception:
            pass
    if _state.selected_context and _state.selected_context.pages:
        for p in _state.selected_context.pages:
            if p.url and p.url != "about:blank":
                try:
                    await _maximize_and_fill_viewport(p)
                    break
                except Exception:
                    pass
    tabs = [
        page.url[:120]
        for context in _state.browser.contexts
        for page in context.pages
    ]
    return {
        "status": "connected",
        "cdp": cdp_url,
        "port": int(cdp_url.rsplit(":", 1)[-1])
        if ":" in cdp_url and cdp_url.rsplit(":", 1)[-1].isdigit()
        else None,
        "managed": False,
        "browser": _state.browser.version,
        "contexts": len(_state.browser.contexts),
        "tabs": tabs,
        **download_config,
    }


async def connect_browser(
    cdp_url: str | None = None, *, port: int = 9222
) -> dict:
    async with _action_lock:
        target = cdp_url or f"http://127.0.0.1:{port}"
        return await _connect_browser_impl(target)


def _chrome_executable(explicit: str | None = None) -> str:
    candidates: list[str | None] = [explicit, os.getenv("CHROME_EXECUTABLE")]
    # Windows 常见安装位置(Chrome 优先,回退 Edge 内核)
    for base in (
        os.getenv("PROGRAMFILES", r"C:\Program Files"),
        os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.getenv("LOCALAPPDATA", ""),
    ):
        if not base:
            continue
        candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    candidates += ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("找不到 Chrome/Chromium 可执行文件，请传入 executable_path 或设置 CHROME_EXECUTABLE")


class _PortHeldByOtherService(Exception):
    """端口有 HTTP 响应但不是 Chrome CDP 端点(如被 nginx/开发服务器占用)。"""


def _probe_cdp(port: int) -> dict[str, Any]:
    """探测端口是否为可用的 Chrome CDP 端点,返回 /json/version 载荷。

    抛出 _PortHeldByOtherService 表示端口被非 CDP 服务占用;
    其他异常(连接拒绝/超时)表示端口当前无响应。
    """
    url = f"http://127.0.0.1:{port}/json/version"
    with urllib.request.urlopen(url, timeout=0.5) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        raw = resp.read().decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise _PortHeldByOtherService(raw[:120]) from None
    if not isinstance(payload, dict) or "Browser" not in payload:
        raise _PortHeldByOtherService(raw[:120])
    return payload


async def _wait_for_cdp(
    port: int, timeout_ms: int, proc: subprocess.Popen[Any] | None = None
) -> str:
    deadline = time.monotonic() + max(1_000, timeout_ms) / 1000
    last_error = ""
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            # Chrome 启动即退出:常见于 profile 被其他 Chrome 实例锁定,
            # 或可执行文件无效。快速失败,不再空等整个超时窗口。
            raise RuntimeError(
                f"Chrome 进程启动后立即退出(exit code {proc.returncode})。"
                "请检查 executable_path 是否有效、user_data_dir 是否被"
                "其他 Chrome 实例锁定,或换一个端口重试。"
            )
        try:
            # 线程中探测,避免同步 HTTP 阻塞事件循环导致整个 MCP 服务冻结
            await asyncio.to_thread(_probe_cdp, port)
            return f"http://127.0.0.1:{port}"
        except _PortHeldByOtherService:
            raise RuntimeError(
                f"端口 {port} 已被非 Chrome CDP 服务占用(响应不是 "
                "/json/version)。请释放该端口或更换端口后重试。"
            ) from None
        except Exception as e:
            last_error = str(e)
        await asyncio.sleep(0.1)
    raise RuntimeError(f"Chrome CDP 端口 {port} 未在 {timeout_ms}ms 内就绪: {last_error}")



_CLIP_LOCK_TOLERANCE_PX = 1.0


async def _read_viewport_or_none(page: Page) -> dict[str, float] | None:
    """读取 window.innerWidth/innerHeight;取不到或非正数时返回 None(不抛错)。

    与 _page_viewport_size 的区别:后者在视口不可用时会抛 ValueError,用于业务坐标
    换算;这里只服务于复位校验,失败必须降级成 None 而不是打断调用方。
    """
    try:
        size = await page.evaluate(
            "() => ({w: Number(window.innerWidth), h: Number(window.innerHeight)})"
        )
    except Exception:
        return None
    try:
        width = float(size["w"])
        height = float(size["h"])
    except Exception:
        return None
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        return None
    return {"w": width, "h": height}


async def _cdp_window_bounds(cdp: Any) -> tuple[int | None, dict[str, Any] | None]:
    """读出该 page 所属窗口的 windowId 与 bounds;任一步失败都返回 (None, None)。"""
    try:
        target_info = await cdp.send("Target.getTargetInfo")
        target_id = (target_info or {}).get("targetInfo", {}).get("targetId")
        if not target_id:
            return None, None
        window = await cdp.send("Browser.getWindowForTarget", {"targetId": target_id})
    except Exception:
        return None, None
    window_id = (window or {}).get("windowId")
    if window_id is None:
        return None, None
    bounds = (window or {}).get("bounds")
    return int(window_id), dict(bounds) if isinstance(bounds, dict) else None


def _same_size(
    measured: dict[str, float] | None,
    other: tuple[float, float] | None,
    tolerance: float = _CLIP_LOCK_TOLERANCE_PX,
) -> bool:
    """两个尺寸是否在 Chromium 回读舍入容差内相等。"""
    if not measured or not other:
        return False
    try:
        width = float(other[0])
        height = float(other[1])
    except Exception:
        return False
    return (
        abs(measured["w"] - width) <= tolerance
        and abs(measured["h"] - height) <= tolerance
    )


def _looks_like_clip_lock(
    measured: dict[str, float] | None,
    clip_size: tuple[float, float] | None,
    expected_size: tuple[float, float] | None = None,
) -> bool:
    """视口是否仍等于「刚才那次截图的 clip 尺寸」——命中即说明 emulation 残留没清掉。

    必须传 expected_size(截图前的视口尺寸):全视口截图的 clip 本来就等于自然视口,
    只看 clip 会把正常结果误判成残留,并白白多跑一轮复位。
    """
    if not _same_size(measured, clip_size):
        return False
    if not clip_size:
        return False
    try:
        if float(clip_size[0]) <= 0 or float(clip_size[1]) <= 0:
            return False
    except Exception:
        return False
    return not _same_size(measured, expected_size)


async def _capture_window_bounds(page: Page) -> dict[str, Any] | None:
    """截图前留档窗口 bounds(含 windowState),供异常中断后原样还原。"""
    if page is None or not hasattr(page, "context"):
        return None
    try:
        cdp = await page.context.new_cdp_session(page)
    except Exception:
        return None
    try:
        _window_id, bounds = await _cdp_window_bounds(cdp)
        return bounds
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


async def _relayout_after_viewport_change(page: Page) -> None:
    """向顶层与全部 iframe 广播 resize,并顺带唤醒 VTable 自适应布局。"""
    relayout_js = """() => {
        try { window.dispatchEvent(new Event('resize')); } catch (e) {}
        if (window._vtable && typeof window._vtable.resize === 'function') {
            try { window._vtable.resize(); } catch (e) {}
        }
    }"""
    try:
        await page.evaluate(relayout_js)
    except Exception:
        pass
    for frame in getattr(page, "frames", []):
        if frame == getattr(page, "main_frame", None):
            continue
        try:
            await frame.evaluate(relayout_js)
        except Exception:
            pass


async def _restore_window_and_viewport(
    page: Page,
    *,
    restore_bounds: dict[str, Any] | None = None,
    clip_size: tuple[float, float] | None = None,
    expected_size: tuple[float, float] | None = None,
    attempts: int = 2,
) -> dict[str, Any]:
    """把窗口 + 视口从「窗口被最小化 / 截图临时 emulation 残留」里救回来。

    真机 APS 复现的两条动因(必须同时处理,缺一不可):

    1. 截图 clip 的 emulation 残留。Playwright 的 page.screenshot(clip=...) 会让
       Chromium 临时下发 Emulation.setDeviceMetricsOverride 把视口撑到 clip 尺寸;
       正常结束 Chromium 会自行还原,但该次截图一旦在默认 3s 超时处被中断
       (渲染器卡住 / 窗口最小化),override 就残留在 RenderWidgetHost 上,
       页面视口被锁成元素尺寸(实测 900x383、715x270),此后 vtable / 浮层 / 点击
       坐标全部错位。

    2. 最小化窗口无法直接最大化。Chromium 不接受 minimized -> maximized 的直接
       跳变,只发 maximized 是 no-op,必须 minimized -> normal -> maximized。
       窗口停在最小化时 innerWidth/innerHeight 也是不可信值。

    因此这里显式补 normal 中转,并在清理 emulation 后用 innerWidth/innerHeight
    回读校验:一旦发现视口仍等于 clip_size,判定复位失败并重试一轮。

    Args:
        restore_bounds: 截图前留档的窗口 bounds,normal 中转时用于避免窗口跳位
        clip_size: 本次截图的 (width, height),用于识别 emulation 残留
        expected_size: 截图前的视口尺寸;与 clip_size 相等时不再判为残留
            (全视口截图本就如此,否则会误报)
        attempts: 最大尝试轮数(默认 2)

    Returns:
        {"attempts", "window_state", "viewport", "clip_lock_detected"}
    """
    report: dict[str, Any] = {
        "attempts": 0,
        "window_state": None,
        "viewport": None,
        "clip_lock_detected": False,
    }
    if page is None:
        return report
    try:
        if page.is_closed():
            return report
    except Exception:
        return report
    try:
        if not hasattr(page, "context") or not hasattr(page.context, "new_cdp_session"):
            return report
    except Exception:
        return report

    measured: dict[str, float] | None = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        report["attempts"] = attempt
        try:
            cdp = await page.context.new_cdp_session(page)
        except Exception:
            break
        try:
            window_id, bounds = await _cdp_window_bounds(cdp)
            state = (bounds or {}).get("windowState")

            # minimized -> normal -> maximized:直接跳 maximized 在 Chromium 上是 no-op
            if window_id is not None and state == "minimized":
                normal_bounds: dict[str, Any] = {"windowState": "normal"}
                for key in ("left", "top", "width", "height"):
                    if restore_bounds and restore_bounds.get(key) is not None:
                        normal_bounds[key] = restore_bounds[key]
                try:
                    await cdp.send(
                        "Browser.setWindowBounds",
                        {"windowId": window_id, "bounds": normal_bounds},
                    )
                    await asyncio.sleep(0.05)
                    state = "normal"
                except Exception:
                    pass

            if window_id is not None and state != "maximized":
                try:
                    await cdp.send(
                        "Browser.setWindowBounds",
                        {"windowId": window_id, "bounds": {"windowState": "maximized"}},
                    )
                    state = "maximized"
                except Exception:
                    pass
            report["window_state"] = state

            # Chromium EmulationHandler::ClearDeviceMetricsOverride 是 session-scoped 的。
            # 若直接调 clearDeviceMetricsOverride，在未设置 override 的新 session 中是 no-op。
            # 必须先调用 setDeviceMetricsOverride(width=0, height=0) 将 RenderWidgetHost
            # 的尺寸重置回宿主窗口自然尺寸，再调用 clearDeviceMetricsOverride 彻底抹除。
            try:
                await cdp.send(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 0,
                        "height": 0,
                        "deviceScaleFactor": 0,
                        "mobile": False,
                    },
                )
            except Exception:
                pass
            try:
                await cdp.send("Emulation.clearDeviceMetricsOverride")
            except Exception:
                pass
        finally:
            try:
                await cdp.detach()
            except Exception:
                pass

        measured = await _read_viewport_or_none(page)
        if measured and getattr(page, "viewport_size", None) is not None:
            # 只在 Playwright 自己托管视口时回写,并重新采样(override 可能刚被抹掉)
            try:
                await page.set_viewport_size(
                    {"width": int(measured["w"]), "height": int(measured["h"])}
                )
            except Exception:
                pass
            measured = await _read_viewport_or_none(page)
        report["viewport"] = (
            {"width": int(measured["w"]), "height": int(measured["h"])} if measured else None
        )
        if not _looks_like_clip_lock(measured, clip_size, expected_size):
            break
        if attempt < attempts:
            await asyncio.sleep(0.15)

    report["clip_lock_detected"] = _looks_like_clip_lock(
        measured, clip_size, expected_size
    )
    await _relayout_after_viewport_change(page)
    return report


async def _maximize_and_fill_viewport(page: Page) -> dict[str, Any]:
    """Maximize the browser window, clear emulated device metrics, and trigger relayout.

    Returns:
        复位报告,见 :func:`_restore_window_and_viewport`。
    """
    return await _restore_window_and_viewport(page)

async def _launch_chrome_impl(
    port: int = 9222,
    headless: bool = False,
    executable_path: str | None = None,
    user_data_dir: str | None = None,
    timeout_ms: int = 15_000,
) -> dict:
    if _state.chrome_process is not None and _state.chrome_process.poll() is None:
        return {"status": "already-running", "port": _state.chrome_port, "managed": True}
    # 端口预检:已有可用 CDP 端点或端口被非 Chrome 服务占用时,
    # 不盲目拉起第二个实例,直接给出可操作指引。
    try:
        await asyncio.to_thread(_probe_cdp, port)
    except _PortHeldByOtherService:
        raise RuntimeError(
            f"端口 {port} 已被非 Chrome CDP 服务占用,无法启动受管 Chrome。"
            "请释放该端口或更换端口后重试。"
        ) from None
    except Exception:
        pass  # 端口无响应,视为空闲,继续启动
    else:
        raise RuntimeError(
            f"端口 {port} 已有可用的 Chrome CDP 端点。要复用该浏览器请调用 "
            f"browser_connect(port={port});要另起实例请更换端口。"
        )
    exe = _chrome_executable(executable_path)
    if user_data_dir:
        profile_path = resolve_workspace_path(user_data_dir)
        owned_profile = False
    else:
        profile_path = artifact_dir("browser-profile") / f"chrome-{port}"
        owned_profile = True
    profile_path.mkdir(parents=True, exist_ok=True)
    profile = str(profile_path)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
    else:
        args.append("--start-maximized")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _state.chrome_process = proc
    _state.chrome_port = port
    _state.chrome_profile = profile
    _state.chrome_profile_owned = owned_profile
    try:
        cdp_url = await _wait_for_cdp(port, timeout_ms, proc=proc)
    except Exception:
        proc.kill()
        proc.wait()
        _state.reset_chrome()
        if owned_profile and profile:
            shutil.rmtree(profile, ignore_errors=True)
        raise
    conn = await _connect_browser_impl(cdp_url)
    try:
        init_page = await _current_page_impl()
        await _maximize_and_fill_viewport(init_page)
    except Exception:
        pass
    return {
        "status": "launched",
        "port": port,
        "cdp": cdp_url,
        "managed": True,
        "user_data_dir": profile,
        "browser": conn.get("browser"),
    }


async def launch_chrome(
    port: int = 9222,
    headless: bool = False,
    executable_path: str | None = None,
    user_data_dir: str | None = None,
    timeout_ms: int = 15_000,
) -> dict:
    async with _action_lock:
        return await _launch_chrome_impl(
            port=port,
            headless=headless,
            executable_path=executable_path,
            user_data_dir=user_data_dir,
            timeout_ms=timeout_ms,
        )


async def _close_browser_impl() -> dict:
    _reset_last_mouse_point()
    if _state.download_tasks:
        await asyncio.gather(*list(_state.download_tasks))
    errors: list[str] = list(_state.download_failures)
    # Clean listeners will be called by overlay subpackage
    try:
        if _state.browser is not None:
            for context in list(_state.owned_contexts.values()):
                try:
                    await context.close()
                except Exception as exc:
                    errors.append(f"context-close: {exc}")
            if not _state.cdp:
                await _state.browser.close()
    except Exception as exc:
        errors.append(f"browser-close: {exc}")
    finally:
        if _state.pw is not None:
            try:
                await _state.pw.stop()
            except Exception as exc:
                errors.append(f"playwright-stop: {exc}")
    proc = _state.chrome_process
    profile = _state.chrome_profile
    owned_profile = _state.chrome_profile_owned
    _state.reset()
    _state.reset_chrome()
    killed_process = False
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        killed_process = True
    if owned_profile and profile:
        try:
            shutil.rmtree(profile, ignore_errors=True)
        except Exception as exc:
            errors.append(f"profile-rm: {exc}")
    result = {"status": "closed"}
    if killed_process:
        result["killed_managed_chrome"] = True
    if errors:
        result["errors"] = errors
    return result


async def close_browser() -> dict:
    async with _action_lock:
        return await _close_browser_impl()


async def _current_page_impl() -> Page:
    if _state.browser is None or not _state.browser.is_connected():
        # 优先探测受管 Chrome 或本地 9222 端口，避免盲目拉起无头浏览器导致操作不可见
        cdp_target = f"http://127.0.0.1:{_state.chrome_port}" if _state.chrome_port else "http://127.0.0.1:9222"
        connected = False
        try:
            await _connect_browser_impl(cdp_target)
            connected = True
        except Exception:
            pass
        if not connected:
            await _start_browser_impl()
    assert _state.browser is not None
    if _state.selected_page is not None:
        try:
            if not _state.selected_page.is_closed():
                return _select_page_object(_state.selected_page)
        except Exception:
            pass
        _state.selected_page = None
    ctx = _state.selected_context
    if ctx is None or ctx not in _state.browser.contexts:
        ctx = (
            _state.browser.contexts[0]
            if _state.browser.contexts
            else await _state.browser.new_context(no_viewport=True)
        )
        _state.selected_context = ctx
        _context_id(
            ctx,
            name="default"
            if not _state.browser.contexts or ctx == _state.browser.contexts[0]
            else None,
        )
    _watch_download_context(ctx)
    pages = ctx.pages
    if not pages:
        page = await ctx.new_page()
        page.set_default_timeout(3_000)
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        _select_page_object(page)
        await _maximize_and_fill_viewport(page)
        return page
    if len(pages) > 1:
        # 页面偏好探测由组合层注入(通用层不感知 VTable/Profile)
        if _page_preference_probe is not None:
            for p in pages:
                if not p.url or p.url == "about:blank":
                    continue
                try:
                    if await asyncio.wait_for(_page_preference_probe(p), timeout=1.0):
                        return _select_page_object(p)
                except Exception:
                    continue
        for p in pages:
            if not p.url or p.url == "about:blank":
                continue
            try:
                if (
                    await asyncio.wait_for(
                        p.evaluate("() => document.visibilityState"), timeout=0.5
                    )
                    != "visible"
                ):
                    continue
                return _select_page_object(p)
            except Exception:
                continue
    return _select_page_object(pages[0])


async def current_page() -> Page:
    async with _action_lock:
        return await _current_page_impl()


async def _list_pages_impl() -> dict:
    current = await _current_page_impl()
    assert _state.browser is not None
    items: list[dict[str, Any]] = []
    selected_page_id = _page_id(current)
    selected_session_id = _context_id(current.context)
    sessions: list[dict[str, Any]] = []
    for c_idx, ctx in enumerate(_state.browser.contexts):
        ctx_selected = ctx == current.context
        sessions.append(_session_summary(ctx, c_idx, ctx_selected))
        for t_idx, page in enumerate(ctx.pages):
            url = page.url or ""
            title = ""
            visible = False
            if url and url != "about:blank":
                try:
                    title = await asyncio.wait_for(page.title(), timeout=1.0)
                    visible = await asyncio.wait_for(page.evaluate("() => document.visibilityState == 'visible'"), timeout=1.0)
                except Exception:
                    pass
            elif url == "about:blank":
                # set_content 创建的页面 URL 仍是 about:blank,需读取真实文档标题
                title = "about:blank"
                visible = True
                try:
                    real_title = await asyncio.wait_for(page.title(), timeout=1.0)
                    if real_title:
                        title = real_title
                except Exception:
                    pass
            page_item_id = _page_id(page)
            session_id = _context_id(ctx)
            items.append(
                {
                    "page_id": page_item_id,
                    "session_id": session_id,
                    "session_name": _state.context_names.get(session_id, f"context-{c_idx}"),
                    "context_index": c_idx,
                    "tab_index": t_idx,
                    "url": url,
                    "title": title,
                    "visible": visible,
                    "selected": page_item_id == selected_page_id,
                }
            )
    return {
        "status": "ok",
        "pages": items,
        "sessions": sessions,
        "selected_page_id": selected_page_id,
        "selected_session_id": selected_session_id,
    }


async def list_pages() -> dict:
    async with _action_lock:
        return await _list_pages_impl()


async def _select_page_impl(target: str | int) -> dict:
    if _state.browser is None or not _state.browser.is_connected():
        raise RuntimeError("浏览器未连接，无法选择标签页")
    candidates: list[tuple[Page, str, int, int]] = []
    for c_idx, ctx in enumerate(_state.browser.contexts):
        for t_idx, page in enumerate(ctx.pages):
            candidates.append((page, _page_id(page), c_idx, t_idx))
    chosen: Page | None = None
    target_text = str(target).strip()
    if target_text.isdigit():
        index = int(target_text)
        if 0 <= index < len(candidates):
            chosen = candidates[index][0]
    if chosen is None:
        for page, page_id_value, _, _ in candidates:
            if page_id_value == target_text:
                chosen = page
                break
    if chosen is None:
        for page, _, _, _ in candidates:
            try:
                if target_text in page.url or target_text in (await page.title()):
                    chosen = page
                    break
            except Exception:
                continue
    if chosen is None:
        valid_ids = [item[1] for item in candidates]
        raise ValueError(
            f"未找到目标标签页 {target!r}。当前可用 page_id: {valid_ids}"
        )
    _state.selected_page = chosen
    _state.selected_context = chosen.context
    _select_page_object(chosen)
    try:
        await chosen.bring_to_front()
    except Exception:
        pass
    try:
        await _maximize_and_fill_viewport(chosen)
    except Exception:
        pass
    return {
        "status": "selected",
        "page_id": _page_id(chosen),
        "session_id": _context_id(chosen.context),
        "url": chosen.url,
        "title": await chosen.title(),
    }


async def select_page(target: str | int) -> dict:
    async with _action_lock:
        return await _select_page_impl(target)


async def _browser_session_impl(
    action: Literal["list", "create", "select", "save", "close", "reset_viewport"] = "list",
    *,
    session_id: str | None = None,
    name: str | None = None,
    storage_state_path: str | None = None,
) -> dict:
    if action == "reset_viewport":
        return await _reset_viewport_impl()
    current = await _current_page_impl()
    assert _state.browser is not None
    if action == "list":
        return await _list_pages_impl()
    if action == "create":
        kwargs: dict[str, Any] = {}
        resolved_storage_state = None
        if storage_state_path:
            resolved_storage_state = resolve_workspace_path(
                storage_state_path,
                must_exist=True,
                require_file=True,
            )
            kwargs["storage_state"] = str(resolved_storage_state)
        if "no_viewport" not in kwargs and "viewport" not in kwargs:
            kwargs["no_viewport"] = True
        ctx = await _state.browser.new_context(**kwargs)
        _watch_download_context(ctx)
        created_id = _context_id(ctx, name=name)
        _state.owned_contexts[created_id] = ctx
        _state.selected_context = ctx
        _state.selected_page = None
        page = await ctx.new_page()
        _select_page_object(page)
        await _maximize_and_fill_viewport(page)
        return {
            "status": "created",
            "session_id": created_id,
            "name": name or created_id,
            "page_id": _page_id(page),
            "storage_state_loaded": resolved_storage_state is not None,
        }
    if action == "select":
        target = session_id or name
        if not target:
            raise ValueError("session_id or name is required for select")
        for c_idx, ctx in enumerate(_state.browser.contexts):
            cur_id = _context_id(ctx)
            cur_name = _state.context_names.get(cur_id)
            if target in {cur_id, cur_name, str(c_idx)}:
                _state.selected_context = ctx
                _state.selected_page = (
                    ctx.pages[0] if ctx.pages else await ctx.new_page()
                )
                _select_page_object(_state.selected_page)
                try:
                    await _maximize_and_fill_viewport(_state.selected_page)
                except Exception:
                    pass
                return {
                    "status": "selected",
                    "session_id": cur_id,
                    "name": cur_name or cur_id,
                    "page_id": _page_id(_state.selected_page),
                }
        raise ValueError(f"Session not found: {target!r}")
    if action == "save":
        if not storage_state_path:
            raise ValueError("storage_state_path is required for save")
        ctx = _state.selected_context or current.context
        resolved_storage_state = resolve_workspace_path(storage_state_path)
        resolved_storage_state.parent.mkdir(parents=True, exist_ok=True)
        await ctx.storage_state(path=str(resolved_storage_state))
        cur_id = _context_id(ctx)
        return {
            "status": "saved",
            "session_id": cur_id,
            "path": str(resolved_storage_state),
        }
    if action == "close":
        target = session_id or name
        if not target:
            raise ValueError("session_id or name is required for close")
        for ctx in list(_state.browser.contexts):
            cur_id = _context_id(ctx)
            cur_name = _state.context_names.get(cur_id)
            if target in {cur_id, cur_name}:
                if len(_state.browser.contexts) <= 1:
                    raise ValueError("Cannot close the last remaining browser session")
                await ctx.close()
                _state.owned_contexts.pop(cur_id, None)
                _state.context_names.pop(cur_id, None)
                if _state.selected_context == ctx:
                    _state.selected_context = None
                    _state.selected_page = None
                return {"status": "closed", "session_id": cur_id}
        raise ValueError(f"Session not found: {target!r}")
    raise ValueError(f"Unsupported session action: {action!r}")


async def browser_session(
    action: Literal["list", "create", "select", "save", "close", "reset_viewport"] = "list",
    *,
    session_id: str | None = None,
    name: str | None = None,
    storage_state_path: str | None = None,
) -> dict:
    async with _action_lock:
        return await _browser_session_impl(
            action=action,
            session_id=session_id,
            name=name,
            storage_state_path=storage_state_path,
        )


async def _reset_viewport_impl() -> dict[str, Any]:
    page = await _current_page_impl()
    report = await _maximize_and_fill_viewport(page)
    if not isinstance(report, dict):
        report = {}
    viewport = await _page_viewport_size(page)
    return {
        "status": "viewport-reset",
        "page_id": _page_id(page),
        "viewport": viewport,
        "window_state": report.get("window_state"),
        "attempts": report.get("attempts"),
        # restored=False 表示清理后视口仍停在上次截图 clip 的尺寸上,调用方应重试
        "restored": not report.get("clip_lock_detected", False),
    }


async def reset_viewport() -> dict[str, Any]:
    """Reset browser viewport to full natural window and clear any emulated metrics."""
    async with _action_lock:
        return await _reset_viewport_impl()


async def _open_url_impl(url: str, *, headless: bool = True) -> dict:
    page = await _current_page_impl()
    await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
    await _maximize_and_fill_viewport(page)
    return {
        "status": "opened",
        "page_id": _page_id(page),
        "url": page.url,
        "title": (await page.title())[:200],
    }


async def open_url(url: str, *, headless: bool = True) -> dict:
    async with _action_lock:
        return await _open_url_impl(url, headless=headless)


def _recognize_captcha_with_ai(image_bytes: bytes) -> str | None:
    """Attempt to recognize 4-character graphical captcha using available vision APIs or local OCR."""
    import base64
    import json
    import re
    import urllib.request

    b64_img = base64.b64encode(image_bytes).decode("ascii")
    # 0. Local ddddocr fast check (0-latency, 100% offline for 4-digit numeric captchas)
    local_code = recognize_captcha_digits(image_bytes)
    if local_code:
        return local_code


    # 1. Check local OCR service if running (e.g. localhost:17521)
    for ocr_url in ("http://127.0.0.1:17521/ocr", "http://localhost:17521/ocr"):
        try:
            req = urllib.request.Request(
                ocr_url,
                data=json.dumps({"image": b64_img}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.load(resp)
                code = data.get("result") or data.get("code") or data.get("text") or ""
                cleaned = re.sub(r"[^a-zA-Z0-9]", "", str(code)).strip()
                if len(cleaned) == 4:
                    return cleaned
        except Exception:
            pass

    # 2. Check OpenAI-compatible vision endpoint
    api_key = os.getenv("QA_AUTOMATION_VISION_KEY") or os.getenv("OPENAI_API_KEY")
    api_url = os.getenv("QA_AUTOMATION_VISION_URL")
    if not api_url and api_key:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        api_url = f"{base_url}/chat/completions"
    model = os.getenv("QA_AUTOMATION_VISION_MODEL") or os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")

    if api_key and api_url:
        try:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Identify the 4 characters (letters or digits) in this verification code image. Output ONLY the 4 characters, nothing else."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                        ],
                    }
                ],
                "max_tokens": 10,
                "temperature": 0.1,
            }
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                res = json.load(resp)
                raw_text = res["choices"][0]["message"]["content"].strip()
                cleaned = re.sub(r"[^a-zA-Z0-9]", "", raw_text)
                if len(cleaned) == 4:
                    return cleaned
        except Exception:
            pass

    return None


async def _browser_login_impl(
    username: str | None = None,
    password: str | None = None,
    *,
    url: str | None = None,
    captcha: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Log in to the APS system, automatically handling login state, expired dialog, and captcha.

    凭据不再写死为默认参数：显式传参优先，否则回落 QA_AUTOMATION_LOGIN_USER /
    _PASSWORD / QA_AUTOMATION_APS_URL（见 config.resolve_login_credentials）。
    """
    import base64
    from urllib.parse import urlsplit

    username, password, url = resolve_login_credentials(username, password, url)
    if not username or not password:
        return {
            "status": "config_missing",
            "reason": credential_missing_message(user=bool(username), password=bool(password)),
        }
    if not url:
        return {
            "status": "config_missing",
            "reason": "未配置目标站点：请设置 QA_AUTOMATION_APS_URL 或在调用时传 url。",
        }

    # 站点归属判定从配置推导，不再硬编码某个环境的域名
    target_host = urlsplit(url).netloc
    if _state.browser is None or not _state.browser.is_connected():
        # Try connecting to port 9222 first; if not available, launch a new browser
        try:
            await _connect_browser_impl("http://127.0.0.1:9222")
        except Exception:
            await _start_browser_impl(headless=False)

    page = await _current_page_impl()
    await _maximize_and_fill_viewport(page)

    # Check if we need to navigate
    curr_url = page.url or ""
    on_target = bool(target_host) and target_host in (urlsplit(curr_url).netloc or "")
    if not curr_url or curr_url == "about:blank" or curr_url.startswith("chrome://") or not on_target:
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(1000)

    # Check if expired modal "登录状态已过期，请重新登录" is present
    try:
        relogin_btn = page.locator("button:has-text('重新登录')")
        if await relogin_btn.count() > 0 and await relogin_btn.first.is_visible():
            await relogin_btn.first.click()
            await page.wait_for_timeout(800)
    except Exception:
        pass

    # Check if already logged in (on /static/admin and no login inputs present)
    if "login" not in page.url and await page.locator("input[placeholder='请输入账号']").count() == 0:
        return {
            "status": "already-logged-in",
            "page_id": _page_id(page),
            "url": page.url,
            "title": (await page.title())[:200],
        }
    # Fast path: Try direct SCM API authentication & cookie injection
    try:
        api_res = await scm_api_login(
            url,
            username,
            password,
            captcha=captcha,
            max_retries=max_retries,
        )
        if api_res.get("status") == "captcha-needed":
            return {
                "status": "captcha-needed",
                "page_id": _page_id(page),
                "captcha_image_path": api_res.get("captcha_image_path"),
                "captcha_image_base64": api_res.get("captcha_image_base64"),
                "username": username,
                "message": api_res.get("message"),
            }

        if api_res.get("ok") and api_res.get("cookies_to_inject"):
            ctx = page.context
            await ctx.add_cookies(api_res["cookies_to_inject"])
            admin_url = url
            if "/login" in admin_url:
                admin_url = admin_url.split("/login")[0]
            if not admin_url.endswith("/"):
                admin_url += "/"
            if "static/admin" not in admin_url and "scm" not in admin_url:
                admin_url = f"{urlsplit(url).scheme}://{target_host}/static/admin/"

            await page.goto(admin_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await page.wait_for_timeout(600)
            if "login" not in page.url:
                return {
                    "status": "logged-in",
                    "method": "api-fast-path" if not captcha else "api-pending-captcha-resolved",
                    "page_id": _page_id(page),
                    "username": username,
                    "token": api_res.get("token"),
                    "url": page.url,
                    "title": (await page.title())[:200],
                }
    except Exception:
        pass



    # Wait for login form inputs to be ready
    user_input = page.locator("input[placeholder='请输入账号']").first
    pwd_input = page.locator("input[placeholder='请输入密码']").first
    captcha_input = page.locator("input[placeholder='请输入图形验证码']").first
    login_btn = page.locator("button:has-text('登 录')").first

    await user_input.wait_for(state="visible", timeout=10_000)

    attempts = 0
    current_captcha = captcha

    while attempts < max(1, max_retries):
        attempts += 1

        # Fill credentials
        await user_input.fill(username)
        await pwd_input.fill(password)

        # Resolve captcha
        resolved_code = current_captcha
        captcha_file = None
        b64_captcha = ""

        if not resolved_code:
            img_loc = page.locator("img[src*='validateCode']")
            if await img_loc.count() == 0:
                img_loc = page.locator("input[placeholder='请输入图形验证码'] + img, input[placeholder='请输入图形验证码'] ~ img")

            if await img_loc.count() > 0:
                captcha_file = artifact_dir("screenshots") / "captcha_login.png"
                captcha_file.parent.mkdir(parents=True, exist_ok=True)
                # 元素截图同样走 clip;登录前若把视口锁成验证码图尺寸,后续填表全错位
                window_before = await _capture_window_bounds(page)
                try:
                    img_bytes = await img_loc.first.screenshot(path=str(captcha_file))
                finally:
                    await _restore_window_and_viewport(page, restore_bounds=window_before)
                b64_captcha = base64.b64encode(img_bytes).decode("ascii")

                # Attempt AI vision recognition
                resolved_code = _recognize_captcha_with_ai(img_bytes)

            if not resolved_code:
                # Cannot automatically recognize without AI vision model / OCR, return captcha artifact for inspect_image
                return {
                    "status": "captcha-needed",
                    "page_id": _page_id(page),
                    "captcha_image_path": str(captcha_file) if captcha_file else None,
                    "captcha_image_base64": b64_captcha,
                    "username": username,
                    "message": "本地 OCR 识别验证码失败，已将验证码图片发送到平台。当前 Agent 的多模态模型可直接观察识别此验证码，然后调用 browser_login(username=..., password=..., captcha='...') 完成登录。",
                }

        # Fill captcha
        await captcha_input.fill(resolved_code)
        await login_btn.click()

        # Wait for outcome: either URL navigates to /static/admin or an error notification appears
        start_t = time.monotonic()
        login_ok = False

        while time.monotonic() - start_t < 4.0:
            if "login" not in page.url and ("static/admin" in page.url or "scm" in page.url):
                login_ok = True
                break

            # Check for error notice/message
            err_msg = page.locator(".ant-message-error, .ant-notification-notice-error, .ant-form-explain")
            if await err_msg.count() > 0 and await err_msg.first.is_visible():
                err_text = await err_msg.first.inner_text()
                if "验证码" in err_text or "错误" in err_text:
                    break
            await page.wait_for_timeout(300)

        if login_ok:
            return {
                "status": "logged-in",
                "page_id": _page_id(page),
                "username": username,
                "url": page.url,
                "title": (await page.title())[:200],
            }

        # Captcha or login failed, refresh captcha image and retry
        if attempts < max_retries:
            try:
                img_loc = page.locator("img[src*='validateCode']")
                if await img_loc.count() > 0:
                    await img_loc.first.click()
                    await page.wait_for_timeout(600)
            except Exception:
                pass
            current_captcha = None

    return {
        "status": "failed",
        "page_id": _page_id(page),
        "url": page.url,
        "reason": f"登录失败，已尝试 {attempts} 次，请检查账号密码或验证码。",
    }


async def browser_login(
    username: str | None = None,
    password: str | None = None,
    *,
    url: str | None = None,
    captcha: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """统一登录工具: 针对新建浏览器会话/登录过期自动登录 APS 系统。

    账号密码来自显式传参或环境变量（QA_AUTOMATION_LOGIN_USER/_PASSWORD），
    不支持代码内默认值——工具签名的默认值会进 inputSchema 并每轮下发给模型。
    """
    async with _action_lock:
        return await _browser_login_impl(
            username=username,
            password=password,
            url=url,
            captcha=captcha,
            max_retries=max_retries,
        )


async def _inject_cookies_impl(
    cookies: list[dict[str, Any]] | None = None,
    token: str | None = None,
    *,
    navigate_to: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """向当前浏览器上下文快速注入 Cookies / Access-Token 凭据，并可按需跳转或刷新目标页面。"""
    page = await _current_page_impl()
    ctx = page.context

    current_url = page.url or ""
    effective_host = ""
    if navigate_to:
        effective_host = urlsplit(navigate_to).netloc
    elif current_url and current_url != "about:blank":
        effective_host = urlsplit(current_url).netloc

    cookies_to_add: list[dict[str, Any]] = []

    # 1. 处理传入的 cookies 列表
    if cookies:
        for c in cookies:
            cookie_dict = dict(c)
            if not cookie_dict.get("domain"):
                if domain:
                    cookie_dict["domain"] = domain
                elif effective_host:
                    cookie_dict["domain"] = effective_host
            if not cookie_dict.get("path"):
                cookie_dict["path"] = "/"
            cookies_to_add.append(cookie_dict)

    # 2. 处理传入的 token
    if token:
        token_cookies = build_cookies_to_inject(
            cookies_dict={},
            token=token,
            target_host=effective_host or domain or "",
        )
        cookies_to_add.extend(token_cookies)

    if not cookies_to_add:
        return {
            "status": "error",
            "page_id": _page_id(page),
            "reason": "未提供任何有效的 cookies 或 token",
        }

    await ctx.add_cookies(cookies_to_add)

    # 3. 按需导航或刷新
    if navigate_to:
        await page.goto(navigate_to, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(500)
    elif current_url and current_url != "about:blank":
        await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(500)

    return {
        "status": "ok",
        "page_id": _page_id(page),
        "injected_count": len(cookies_to_add),
        "url": page.url,
        "title": (await page.title())[:200],
    }


async def inject_cookies(
    cookies: list[dict[str, Any]] | None = None,
    token: str | None = None,
    *,
    navigate_to: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """向当前浏览器上下文快速注入 Cookies / Access-Token 凭据，并可按需跳转或刷新目标页面。"""
    async with _action_lock:
        return await _inject_cookies_impl(
            cookies=cookies,
            token=token,
            navigate_to=navigate_to,
            domain=domain,
        )
