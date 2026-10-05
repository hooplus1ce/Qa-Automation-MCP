"""APS unified login: profile resolution, API fast-path, captcha two-phase, UI fallback.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]
from ..auth import recognize_captcha_digits, scm_api_login
from ..config import NAV_TIMEOUT_MS, credential_missing_message, resolve_login_credentials
from ..workspace import artifact_dir
from .core import (
    _connect_browser_impl,
    _current_page_impl,
    _inject_cookies_impl,
    _last_cdp_endpoint,
    _start_browser_impl,
)
from .state import _action_lock, _page_id, _state
from .viewport import (
    _capture_window_bounds,
    _maximize_and_fill_viewport,
    _restore_window_and_viewport,
)

logger = logging.getLogger("qa_automation.browser")


def _recognize_captcha_with_ai(image_bytes: bytes) -> str | None:
    """Attempt to recognize 4-character graphical captcha using available vision APIs or local OCR."""
    import base64
    import re

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
    profile: str | None = None,
    force: bool = False,
    max_retries: int = 3,
) -> dict[str, Any]:
    """登录 APS：账号档案预配置 + 登录态缓存复用 + 验证码两段式。

    凭据解析优先级（都不写死为默认参数，避免随 inputSchema 每轮下发给模型）：
      1. 显式传参 username / password / url
      2. 账号档案（profiles.toml，按 profile 名取用；缺省取 QA_AUTOMATION_ACCOUNT）
      3. 环境变量 QA_AUTOMATION_LOGIN_USER / _PASSWORD / QA_AUTOMATION_APS_URL

    流程：档案命中且会话缓存有效 → 直接注入 cookies 秒级恢复（免验证码）；
          否则走接口登录（首次不带 captcha 返回验证码图片，带 captcha 再调一次完成），
          成功后把 token + cookies 写入会话缓存，供后续调用直接复用。
    """
    import base64

    from ..auth_profiles import (
        clear_session,
        list_profiles,
        load_session,
        resolve_profile,
        save_session,
        session_info,
    )

    account = None
    explicit_profile = (profile or "").strip()
    explicit_credentials = bool((username or "").strip() and (password or "").strip())
    explicit_account_request = bool(
        force or explicit_profile or explicit_credentials or (captcha or "").strip()
    )
    if not explicit_credentials:
        try:
            account = resolve_profile(profile)
        except Exception as exc:
            if explicit_profile:
                available_profiles: list[dict[str, Any]] = []
                try:
                    available_profiles = list_profiles()
                except Exception:
                    available_profiles = []
                return {
                    "status": "profile_not_found",
                    "profile": explicit_profile,
                    "reason": str(exc),
                    "profiles": available_profiles,
                }
            account = None
        if account is not None:
            username = username or account.username
            password = password or account.password
            url = url or account.admin_url
    username, password, url = resolve_login_credentials(username, password, url)
    if not username or not password:
        reason = credential_missing_message(user=bool(username), password=bool(password))
        available: list[str] = []
        try:
            available = [str(item.get("profile")) for item in list_profiles()]
        except Exception:
            available = []
        if available:
            reason += (
                " 也可改用账号档案登录：browser_login(profile=\"<档案名>\")；"
                f"当前可用档案：{', '.join(available)}。"
            )
        return {"status": "config_missing", "reason": reason, "profiles": available}
    if not url:
        return {
            "status": "config_missing",
            "reason": "未配置目标站点：请设置 QA_AUTOMATION_APS_URL、配置账号档案，或在调用时传 url。",
        }

    # 站点归属判定从配置推导，不再硬编码某个环境的域名
    target_host = urlsplit(url).netloc
    if _state.browser is None or not _state.browser.is_connected():
        # Try connecting to port 9222 first; if not available, launch a new browser
        try:
            await _connect_browser_impl(_last_cdp_endpoint() or "http://127.0.0.1:9222")
        except Exception:
            await _start_browser_impl(headless=False)

    page = await _current_page_impl()
    await _maximize_and_fill_viewport(page)

    # A) 账号档案 + 会话缓存：直接注入 cookies 秒级恢复（免验证码）
    # 注意：必须用不带锁的 _inject_cookies_impl —— 公共 inject_cookies 会再次获取
    # _action_lock，而本函数由已持锁的 browser_login 调用，会造成自死锁。
    if account is not None and not force:
        cached = load_session(account)
        if cached and cached.get("token"):
            try:
                await page.context.clear_cookies()
                await _inject_cookies_impl(
                    cookies=cached.get("cookies") or None,
                    token=str(cached["token"]),
                    navigate_to=url,
                    domain=account.cookie_domain,
                )
            except Exception as exc:
                # 注入失败不中断:下方的 landed 校验会判定缓存无效并走正常登录,
                # 但原因必须留痕,否则"缓存明明在却没生效"无从排查。
                logger.warning(
                    "session-cache inject failed for profile %r: %r", account.name, exc
                )
            page = await _current_page_impl()
            landed = page.url or ""
            if target_host in (urlsplit(landed).netloc or "") and "login" not in landed.lower():
                return {
                    "status": "logged-in",
                    "method": "session-cache",
                    "profile": account.name,
                    "username": account.username,
                    "role": account.role,
                    "page_id": _page_id(page),
                    "url": landed,
                    "title": (await page.title())[:200],
                    "token_tail": str(cached["token"])[-4:],
                    "session": session_info(account),
                }
            # 缓存已被服务端拒绝：清掉后走正常登录
            clear_session(account)

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

    # Check if already logged in (only when caller did not explicitly request switching/forcing account)
    if (
        not explicit_account_request
        and "login" not in page.url
        and await page.locator("input[placeholder='请输入账号']").count() == 0
    ):
        return {
            "status": "already-logged-in",
            "page_id": _page_id(page),
            "url": page.url,
            "title": (await page.title())[:200],
        }
    # Fast path: Try direct SCM API authentication & cookie injection
    api_fast_path_error: str | None = None
    try:
        api_kwargs: dict[str, Any] = {}
        if account is not None:
            api_kwargs = {
                "timeout_sec": account.timeout,
                "captcha_path": account.captcha_path,
                "captcha_key": account.captcha_key,
                "login_path": account.login_path,
                "username_field": account.username_field,
                "password_field": account.password_field,
                "captcha_field": account.captcha_field,
                "success_field": account.success_field,
                "message_field": account.message_field,
                "token_field": account.token_field,
                "user_agent": account.user_agent,
            }
        api_res = await scm_api_login(
            url,
            username,
            password,
            captcha=captcha,
            max_retries=max_retries,
            **api_kwargs,
        )
        if api_res.get("status") == "captcha-needed":
            return {
                "status": "captcha-needed",
                "page_id": _page_id(page),
                "captcha_image_path": api_res.get("captcha_image_path"),
                "captcha_image_base64": api_res.get("captcha_image_base64"),
                "username": username,
                "profile": account.name if account else None,
                "message": api_res.get("message"),
            }

        if api_res.get("ok") and api_res.get("cookies_to_inject"):
            if account is not None:
                try:
                    save_session(
                        account,
                        token=str(api_res.get("token")),
                        cookies=api_res.get("cookies_to_inject") or [],
                    )
                except Exception as exc:
                    # 会话缓存写失败只影响下次冷启动的秒级恢复,不应让本次登录降级失败
                    logger.warning(
                        "login session cache save failed for profile %r: %r",
                        account.name,
                        exc,
                    )
            ctx = page.context
            await ctx.clear_cookies()
            host_cookies = [
                c
                for c in api_res["cookies_to_inject"]
                if not target_host or c.get("domain", "").lstrip(".") == target_host.lstrip(".")
            ]
            await ctx.add_cookies(host_cookies)
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
                    "profile": account.name if account else None,
                    "role": account.role if account else None,
                    # 只回传尾 4 位做核对用：完整 token 会随 MCP 响应进入对话日志
                    # 与模型上下文（与会话缓存路径的 token_tail 口径一致）。
                    "token_tail": str(api_res.get("token") or "")[-4:],
                    "session": session_info(account) if account else None,
                    "url": page.url,
                    "title": (await page.title())[:200],
                }
    except Exception as exc:
        # 快路径失败不能静默:排障时必须能看出为什么没走 API 直登而降级到 UI 填表。
        api_fast_path_error = f"{type(exc).__name__}: {exc}"
        logger.warning("browser_login API fast-path failed, falling back to UI: %s", api_fast_path_error)



    # Wait for login form inputs to be ready (if currently on an authenticated page, clear cookies & go to login page)
    if "login" not in page.url and await page.locator("input[placeholder='请输入账号']").count() == 0:
        login_target = (
            account.login_page
            if account is not None and account.login_page
            else f"{urlsplit(url).scheme}://{target_host}/static/admin/login"
        )
        await page.context.clear_cookies()
        await page.goto(login_target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(600)

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
                # to_thread 移出事件循环：本地 OCR(1.5s×2) + 视觉 API(8s) 是同步
                # urllib 调用，直接在 async 里跑会冻结整个 MCP 事件循环（与
                # auth.py 对 ddddocr 的处理口径一致）。
                resolved_code = await asyncio.to_thread(_recognize_captcha_with_ai, img_bytes)

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

    failed: dict[str, Any] = {
        "status": "failed",
        "page_id": _page_id(page),
        "url": page.url,
        "reason": f"登录失败，已尝试 {attempts} 次，请检查账号密码或验证码。",
    }
    if api_fast_path_error:
        failed["api_fast_path_error"] = api_fast_path_error
    return failed


async def browser_login(
    username: str | None = None,
    password: str | None = None,
    *,
    url: str | None = None,
    captcha: str | None = None,
    profile: str | None = None,
    force: bool = False,
    max_retries: int = 3,
) -> dict[str, Any]:
    """统一登录工具：登录 / 恢复 APS 会话，支持账号档案、登录态缓存与验证码两段式。

    凭据来自显式传参、账号档案（profiles.toml）或环境变量
    （QA_AUTOMATION_LOGIN_USER/_PASSWORD/_APS_URL），不支持代码内默认值——
    工具签名的默认值会进 inputSchema 并每轮下发给模型。
    """
    async with _action_lock:
        return await _browser_login_impl(
            username=username,
            password=password,
            url=url,
            captcha=captcha,
            profile=profile,
            force=force,
            max_retries=max_retries,
        )
