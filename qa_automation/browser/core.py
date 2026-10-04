"""Browser lifecycle: start/connect/close, page selection, sessions, navigation, cookies.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import Any, Literal
from urllib.parse import urlsplit

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]
from ..auth import build_cookies_to_inject
from ..config import NAV_TIMEOUT_MS, PLAYWRIGHT_INSTALL_HINT, SHOW_CURSOR
from ..mouse import _WIN_CURSOR_HELPER_SCRIPT, _reset_last_mouse_point
from ..workspace import artifact_dir, resolve_workspace_path
from .cdp import (
    _chrome_executable,
    _normalize_cdp_url,
    _PortHeldByOtherService,
    _probe_cdp,
    _wait_for_cdp,
)
from .downloads import _configure_browser_downloads, _watch_download_context
from .state import (
    _action_lock,
    _context_id,
    _get_page_preference_probe,
    _page_id,
    _page_viewport_size,
    _select_page_object,
    _session_summary,
    _state,
)
from .viewport import (
    _maximize_and_fill_viewport,
)

logger = logging.getLogger("qa_automation.browser")


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


async def _close_browser_impl() -> dict:
    _reset_last_mouse_point()
    errors: list[str] = list(_state.download_failures)
    if _state.download_tasks:
        # 持有 _action_lock 期间无限等待停滞下载会饿死所有后续浏览器工具,
        # 与 run_js 的 wait_for 防御同理:给一个上界,超时取消并记入 errors。
        pending = [t for t in _state.download_tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=10.0)
            except TimeoutError:
                for t in pending:
                    if not t.done():
                        t.cancel()
                errors.append(
                    f"download-wait: cancelled {len(pending)} stuck download(s) after 10s"
                )
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
            await asyncio.to_thread(proc.wait, 3)
        except Exception:
            try:
                proc.kill()
                await asyncio.to_thread(proc.wait, 2)
            except Exception:
                pass
        killed_process = True
    if owned_profile and profile:
        try:
            # Chrome profile 含数千文件,Windows 上删除可达数秒,不能阻塞事件循环
            await asyncio.to_thread(shutil.rmtree, profile, ignore_errors=True)
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


def _last_cdp_endpoint() -> str | None:
    """上次成功使用的 CDP 端点:显式 connect 的地址优先,其次受管 Chrome 端口。

    自动重连必须优先复用它——硬编码 9222 会把原本连在 9223 等自定义端点的
    多账号会话静默串台到另一台浏览器。
    """
    if _state.cdp_url:
        return _state.cdp_url
    if _state.chrome_port:
        return f"http://127.0.0.1:{_state.chrome_port}"
    return None


async def _current_page_impl() -> Page:
    if _state.browser is None or not _state.browser.is_connected():
        # 优先探测上次使用的端点(受管 Chrome / 显式 CDP 地址)，避免盲目拉起
        # 无头浏览器导致操作不可见，也避免默认 9222 串台到别的浏览器实例。
        cdp_target = _last_cdp_endpoint() or "http://127.0.0.1:9222"
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
        if _get_page_preference_probe() is not None:
            for p in pages:
                if not p.url or p.url == "about:blank":
                    continue
                try:
                    if await asyncio.wait_for(_get_page_preference_probe()(p), timeout=1.0):
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
