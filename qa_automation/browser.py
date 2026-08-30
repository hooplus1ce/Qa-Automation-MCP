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
)
from .mouse import _WIN_CURSOR_HELPER_SCRIPT, _reset_last_mouse_point
from .workspace import artifact_dir, artifact_file, resolve_workspace_path


@dataclass
class _BrowserState:
    """Browser lifecycle, session/page registry, and download bookkeeping."""

    browser: Browser | None = None
    pw: Any = None
    cdp: bool = False
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


def _frame_details(page: Page, frame: Frame) -> dict[str, Any]:
    name, url = _frame_name_url(frame)
    return {
        "frame_id": _frame_id(page, frame),
        "frame_url": url,
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


async def _frame_context_details(page: Page, frame: Frame) -> dict[str, Any]:
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


async def _connect_browser_impl(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    if _state.browser is not None and _state.browser.is_connected():
        return {"status": "already-connected", "cdp": cdp_url}
    if async_playwright is None:
        raise RuntimeError(PLAYWRIGHT_INSTALL_HINT)

    _state.pw = await async_playwright().start()
    try:
        _state.browser = await _state.pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        await _state.pw.stop()
        _state.pw = None
        raise RuntimeError(
            f"无法连接 CDP 浏览器 {cdp_url!r}。请确认 Chrome 已使用 "
            "--remote-debugging-port 启动，且端口可访问。原始错误: "
            f"{exc}"
        ) from exc
    download_config = await _configure_browser_downloads(_state.browser)
    _state.cdp = True
    _state.selected_page = None
    _state.selected_context = _state.browser.contexts[0] if _state.browser.contexts else None
    if _state.selected_context is not None:
        _context_id(_state.selected_context, name="default")
        _watch_download_context(_state.selected_context)
        if SHOW_CURSOR:
            try:
                await _state.selected_context.add_init_script(_WIN_CURSOR_HELPER_SCRIPT)
                for p in _state.selected_context.pages:
                    if not p.url or p.url == "about:blank":
                        continue
                    try:
                        await asyncio.wait_for(p.evaluate(_WIN_CURSOR_HELPER_SCRIPT), timeout=1.0)
                    except Exception:
                        pass
            except Exception:
                pass
    tabs = []
    try:
        if _state.browser.contexts:
            tabs = [p.url[:80] for p in _state.browser.contexts[0].pages]
    except Exception:
        pass
    return {
        "status": "connected",
        "cdp": cdp_url,
        "port": int(cdp_url.rsplit(":", 1)[-1].rstrip("/")) if ":" in cdp_url and cdp_url.rsplit(":", 1)[-1].rstrip("/").isdigit() else None,
        "managed": False,
        "browser": _state.browser.version,
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
            else await _state.browser.new_context()
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
        return _select_page_object(page)
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
    action: Literal["list", "create", "select", "save", "close"] = "list",
    *,
    session_id: str | None = None,
    name: str | None = None,
    storage_state_path: str | None = None,
) -> dict:
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
        ctx = await _state.browser.new_context(**kwargs)
        _watch_download_context(ctx)
        created_id = _context_id(ctx, name=name)
        _state.owned_contexts[created_id] = ctx
        _state.selected_context = ctx
        _state.selected_page = None
        page = await ctx.new_page()
        _select_page_object(page)
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
    action: Literal["list", "create", "select", "save", "close"] = "list",
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


async def _open_url_impl(url: str, *, headless: bool = True) -> dict:
    if _state.browser is None or not _state.browser.is_connected():
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
    async with _action_lock:
        return await _open_url_impl(url, headless=headless)
