"""Browser process lifecycle, CDP connection, session isolation, and page registry."""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import weakref
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
    VTABLE_SHOW_CURSOR,
)
from .mouse import _WIN_CURSOR_HELPER_SCRIPT, _reset_last_mouse_point

_browser: Browser | None = None
_pw: Any = None
_cdp: bool = False
_chrome_process: subprocess.Popen[Any] | None = None
_chrome_port: int | None = None
_chrome_profile: str | None = None
_chrome_profile_owned = False
_action_lock = asyncio.Lock()
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
    except TypeError:
        return f"page-object-{id(page)}"


def _context_id(context: Any, *, name: str | None = None) -> str:
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
        value = f"session-object-{_context_id_counter}"
        _fallback_context_ids[key] = value
    if name:
        _context_names[value] = name
    return value


def _frame_id(page: Page, frame: Frame) -> str:
    if frame == page.main_frame:
        return "frame-0:unnamed" if not frame.name else f"frame-0:{frame.name}"
    try:
        page_frames = _page_frame_ids.setdefault(page, weakref.WeakKeyDictionary())
        if frame in page_frames:
            return page_frames[frame]
        counter = _page_frame_counters.get(page, 0) + 1
        _page_frame_counters[page] = counter
        name_part = frame.name if frame.name else "unnamed"
        value = f"frame-{counter}:{name_part}"
        page_frames[frame] = value
        return value
    except TypeError:
        key = (id(page), id(frame))
        if key in _fallback_frame_ids:
            return _fallback_frame_ids[key]
        counter = _fallback_frame_counters.get(id(page), 0) + 1
        _fallback_frame_counters[id(page)] = counter
        name_part = frame.name if frame.name else "unnamed"
        value = f"frame-object-{counter}:{name_part}"
        _fallback_frame_ids[key] = value
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
        "name": _context_names.get(session_id, f"context-{index}"),
        "context_index": index,
        "page_count": len(getattr(context, "pages", [])),
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
    global _browser, _pw, _cdp, _selected_page, _selected_context
    if _browser is not None and _browser.is_connected():
        return {"status": "already-open", "browser": "chromium", "headless": headless}
    if async_playwright is None:
        raise RuntimeError(PLAYWRIGHT_INSTALL_HINT)

    _pw = await async_playwright().start()
    try:
        _browser = await _pw.chromium.launch(headless=headless)
    except Exception as first_error:
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
    async with _action_lock:
        return await _start_browser_impl(headless=headless)


async def _connect_browser_impl(cdp_url: str = "http://127.0.0.1:9222") -> dict:
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
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return f"http://127.0.0.1:{port}"
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
    global _chrome_process, _chrome_port, _chrome_profile, _chrome_profile_owned
    if _chrome_process is not None and _chrome_process.poll() is None:
        return {"status": "already-running", "port": _chrome_port, "managed": True}
    exe = _chrome_executable(executable_path)
    profile = user_data_dir
    owned_profile = False
    if not profile:
        profile = tempfile.mkdtemp(prefix="vtable_chrome_")
        owned_profile = True
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
    _chrome_process = proc
    _chrome_port = port
    _chrome_profile = profile
    _chrome_profile_owned = owned_profile
    try:
        cdp_url = await _wait_for_cdp(port, timeout_ms)
    except Exception:
        proc.kill()
        proc.wait()
        _chrome_process = None
        _chrome_port = None
        if owned_profile and profile:
            shutil.rmtree(profile, ignore_errors=True)
        _chrome_profile = None
        _chrome_profile_owned = False
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
    global _browser, _pw, _cdp, _selected_page, _selected_context, _last_mouse_point
    global _chrome_process, _chrome_port, _chrome_profile, _chrome_profile_owned
    _reset_last_mouse_point()
    errors: list[str] = []
    # Clean listeners will be called by overlay subpackage
    try:
        if _browser is not None:
            for context in list(_owned_contexts.values()):
                try:
                    await context.close()
                except Exception as exc:
                    errors.append(f"context-close: {exc}")
            if not _cdp:
                await _browser.close()
    except Exception as exc:
        errors.append(f"browser-close: {exc}")
    finally:
        _browser = None
        _selected_page = None
        _selected_context = None
        _page_ids.clear()
        _page_frame_ids.clear()
        _page_frame_counters.clear()
        _fallback_frame_ids.clear()
        _fallback_frame_counters.clear()
        _context_ids.clear()
        _fallback_context_ids.clear()
        _context_names.clear()
        _owned_contexts.clear()
        if _pw is not None:
            try:
                await _pw.stop()
            except Exception as exc:
                errors.append(f"playwright-stop: {exc}")
            finally:
                _pw = None
        _cdp = False
    killed_process = False
    proc = _chrome_process
    profile = _chrome_profile
    owned_profile = _chrome_profile_owned
    _chrome_process = None
    _chrome_port = None
    _chrome_profile = None
    _chrome_profile_owned = False
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
        # Import lazily to avoid circular dependencies
        from .vtable.binding import active_application_frame, vtable_frame
        for p in pages:
            try:
                if await active_application_frame(p) is not None:
                    return _select_page_object(p)
            except Exception:
                continue
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
    async with _action_lock:
        return await _current_page_impl()


async def _list_pages_impl() -> dict:
    current = await _current_page_impl()
    assert _browser is not None
    items: list[dict[str, Any]] = []
    selected_page_id = _page_id(current)
    selected_session_id = _context_id(current.context)
    sessions: list[dict[str, Any]] = []
    for c_idx, ctx in enumerate(_browser.contexts):
        ctx_selected = ctx == current.context
        sessions.append(_session_summary(ctx, c_idx, ctx_selected))
        for t_idx, page in enumerate(ctx.pages):
            try:
                url = page.url
                title = await page.title()
                visible = await page.evaluate("() => document.visibilityState == 'visible'")
            except Exception:
                url = ""
                title = ""
                visible = False
            page_item_id = _page_id(page)
            session_id = _context_id(ctx)
            items.append(
                {
                    "page_id": page_item_id,
                    "session_id": session_id,
                    "session_name": _context_names.get(session_id, f"context-{c_idx}"),
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
    global _selected_page, _selected_context
    if _browser is None or not _browser.is_connected():
        raise RuntimeError("浏览器未连接，无法选择标签页")
    candidates: list[tuple[Page, str, int, int]] = []
    for c_idx, ctx in enumerate(_browser.contexts):
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
    _selected_page = chosen
    _selected_context = chosen.context
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
    global _selected_context, _selected_page
    current = await _current_page_impl()
    assert _browser is not None
    if action == "list":
        return await _list_pages_impl()
    if action == "create":
        kwargs: dict[str, Any] = {}
        if storage_state_path and os.path.exists(storage_state_path):
            kwargs["storage_state"] = storage_state_path
        ctx = await _browser.new_context(**kwargs)
        created_id = _context_id(ctx, name=name)
        _owned_contexts[created_id] = ctx
        _selected_context = ctx
        _selected_page = None
        page = await ctx.new_page()
        _select_page_object(page)
        return {
            "status": "created",
            "session_id": created_id,
            "name": name or created_id,
            "page_id": _page_id(page),
            "storage_state_loaded": bool(storage_state_path and os.path.exists(storage_state_path)),
        }
    if action == "select":
        target = session_id or name
        if not target:
            raise ValueError("session_id or name is required for select")
        for c_idx, ctx in enumerate(_browser.contexts):
            cur_id = _context_id(ctx)
            cur_name = _context_names.get(cur_id)
            if target in {cur_id, cur_name, str(c_idx)}:
                _selected_context = ctx
                _selected_page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                _select_page_object(_selected_page)
                return {
                    "status": "selected",
                    "session_id": cur_id,
                    "name": cur_name or cur_id,
                    "page_id": _page_id(_selected_page),
                }
        raise ValueError(f"Session not found: {target!r}")
    if action == "save":
        if not storage_state_path:
            raise ValueError("storage_state_path is required for save")
        ctx = _selected_context or current.context
        os.makedirs(os.path.dirname(os.path.abspath(storage_state_path)), exist_ok=True)
        await ctx.storage_state(path=storage_state_path)
        cur_id = _context_id(ctx)
        return {
            "status": "saved",
            "session_id": cur_id,
            "path": storage_state_path,
        }
    if action == "close":
        target = session_id or name
        if not target:
            raise ValueError("session_id or name is required for close")
        for ctx in list(_browser.contexts):
            cur_id = _context_id(ctx)
            cur_name = _context_names.get(cur_id)
            if target in {cur_id, cur_name}:
                if len(_browser.contexts) <= 1:
                    raise ValueError("Cannot close the last remaining browser session")
                await ctx.close()
                _owned_contexts.pop(cur_id, None)
                _context_names.pop(cur_id, None)
                if _selected_context == ctx:
                    _selected_context = None
                    _selected_page = None
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
    async with _action_lock:
        return await _open_url_impl(url, headless=headless)
