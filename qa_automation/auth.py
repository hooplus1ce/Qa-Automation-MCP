"""Authentication helpers for APS/SCM: fast API login, OCR captcha solving, and cookie injection."""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .workspace import artifact_dir

logger = logging.getLogger("qa_automation.auth")

_OCR_INSTANCE: Any = None
_OCR_INITIALIZED: bool = False


@dataclass
class PendingCaptchaSession:
    """Cached SCM session waiting for multimodal captcha resolution."""

    origin: str
    host: str
    username: str
    password: str
    cookies: dict[str, str]
    captcha_image_path: str
    captcha_image_base64: str
    timestamp: float


_PENDING_SESSION: PendingCaptchaSession | None = None
SESSION_EXPIRY_SECONDS: float = 300.0


def get_pending_captcha_session(username: str | None = None) -> PendingCaptchaSession | None:
    """Return pending session if valid and not expired."""
    global _PENDING_SESSION
    if _PENDING_SESSION is None:
        return None
    if time.time() - _PENDING_SESSION.timestamp > SESSION_EXPIRY_SECONDS:
        _PENDING_SESSION = None
        return None
    if username and _PENDING_SESSION.username != username:
        return None
    return _PENDING_SESSION


def clear_pending_captcha_session() -> None:
    """Clear pending captcha session cache."""
    global _PENDING_SESSION
    _PENDING_SESSION = None


def get_local_ocr_engine() -> Any | None:
    """Lazy-load and cache the local ddddocr instance restricted to numeric digits."""
    global _OCR_INSTANCE, _OCR_INITIALIZED
    if _OCR_INITIALIZED:
        return _OCR_INSTANCE

    try:
        import ddddocr  # type: ignore[import-untyped]

        ocr = ddddocr.DdddOcr(show_ad=False)
        ocr.set_ranges("0123456789")
        _OCR_INSTANCE = ocr
    except Exception as exc:
        logger.debug("Local ddddocr is not available: %s", exc)
        _OCR_INSTANCE = None
    finally:
        _OCR_INITIALIZED = True

    return _OCR_INSTANCE


def recognize_captcha_digits(image_bytes: bytes) -> str | None:
    """Recognize 4-digit numeric verification code using local OCR.

    Returns the 4-digit code if successfully recognized, or None.
    """
    ocr = get_local_ocr_engine()
    if ocr is None:
        return None

    try:
        raw_res = ocr.classification(image_bytes)
        cleaned = re.sub(r"\D", "", str(raw_res)).strip()
        if len(cleaned) == 4:
            return cleaned
    except Exception as exc:
        logger.debug("Local OCR classification failed: %s", exc)

    return None


def extract_parent_domain(host: str) -> str:
    """Extract top-level / second-level cookie domain from a hostname (e.g. .hoolinks.com)."""
    parts = host.split(".")
    if len(parts) >= 2:
        return "." + ".".join(parts[-2:])
    return host


def build_cookies_to_inject(
    cookies_dict: dict[str, str],
    token: str | None = None,
    target_host: str = "",
) -> list[dict[str, Any]]:
    """Build Playwright-compatible cookie dictionaries for target domains and access tokens."""
    domains = set()
    if target_host:
        domains.add(target_host)
        parent = extract_parent_domain(target_host)
        if parent:
            domains.add(parent)

    cookies_map: dict[str, str] = dict(cookies_dict)
    if token:
        for key in ("HL-Access-Token", "cookie_token", "UCTOKEN"):
            cookies_map[key] = token

    result: list[dict[str, Any]] = []
    for d in domains:
        for name, value in cookies_map.items():
            result.append(
                {
                    "name": name,
                    "value": str(value),
                    "domain": d,
                    "path": "/",
                }
            )

    return result


async def scm_api_login(
    base_url: str,
    username: str,
    password: str,
    *,
    captcha: str | None = None,
    max_retries: int = 4,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    """Perform fast API login to APS/SCM backend via /scmpsm/login endpoints.

    Flow:
    1. If `captcha` is provided, reuse pending session (from a previous fallback) to submit login.
    2. Otherwise, request captcha image via /scmpsm/login/validateCode?key=regValidateCode.
    3. Solve captcha with local OCR.
    4. If local OCR succeeds, POST /scmpsm/login/signin and extract tokens.
    5. If local OCR fails across retries, cache the active session and return the captcha image
       (file path + base64) to the agent platform for multimodal visual recognition.
    """
    global _PENDING_SESSION

    parsed = urlsplit(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
        ),
    }

    # Branch 1: Captcha is explicitly provided (resolving a pending multimodal captcha)
    if captcha and captcha.strip():
        resolved_code = captcha.strip()
        pending = get_pending_captcha_session(username)
        if pending and pending.origin == origin:
            logger.info("Resuming pending SCM session with resolved captcha: %s", resolved_code)
            async with httpx.AsyncClient(
                base_url=origin,
                cookies=pending.cookies,
                timeout=timeout_sec,
            ) as client:
                signin_payload = {
                    "userName": username,
                    "userPwd": password,
                    "vcode": resolved_code,
                }
                try:
                    resp_signin = await client.post(
                        "/scmpsm/login/signin",
                        json=signin_payload,
                        headers=headers,
                    )
                    res_data = resp_signin.json() if resp_signin.status_code == 200 else {}
                    if res_data.get("ok") and res_data.get("data"):
                        token = str(res_data["data"])
                        cookies_dict = dict(client.cookies)
                        cookies_to_inject = build_cookies_to_inject(
                            cookies_dict=cookies_dict,
                            token=token,
                            target_host=host,
                        )
                        clear_pending_captcha_session()
                        return {
                            "ok": True,
                            "token": token,
                            "message": res_data.get("msg") or "登录成功！",
                            "cookies": cookies_dict,
                            "cookies_to_inject": cookies_to_inject,
                            "attempts": 1,
                        }
                    else:
                        msg = res_data.get("msg") or resp_signin.text[:100]
                        logger.warning("Pending session signin rejected: %s", msg)
                except Exception as exc:
                    logger.warning("Pending session signin request failed: %s", exc)

            clear_pending_captcha_session()

    # Branch 2: Normal flow - pull captcha and try local OCR
    last_img_bytes: bytes = b""
    last_client_cookies: dict[str, str] = {}
    last_error = ""

    async with httpx.AsyncClient(base_url=origin, timeout=timeout_sec) as client:
        for attempt in range(1, max(1, max_retries) + 1):
            rnd = random.random()
            code_url = f"/scmpsm/login/validateCode?key=regValidateCode&random={rnd}"
            try:
                resp_img = await client.get(code_url, headers={"Accept": "image/*,*/*;q=0.8"})
            except Exception as exc:
                last_error = f"请求验证码失败: {exc}"
                await asyncio.sleep(0.2)
                continue

            if resp_img.status_code != 200:
                last_error = f"验证码接口返回状态码 {resp_img.status_code}"
                await asyncio.sleep(0.2)
                continue

            last_img_bytes = resp_img.content
            last_client_cookies = dict(client.cookies)

            vcode = await asyncio.to_thread(recognize_captcha_digits, last_img_bytes)
            if not vcode:
                last_error = "本地 OCR 识别验证码失败"
                await asyncio.sleep(0.2)
                continue

            signin_payload = {
                "userName": username,
                "userPwd": password,
                "vcode": vcode,
            }

            try:
                resp_signin = await client.post(
                    "/scmpsm/login/signin",
                    json=signin_payload,
                    headers=headers,
                )
            except Exception as exc:
                last_error = f"请求登录接口失败: {exc}"
                await asyncio.sleep(0.2)
                continue

            if resp_signin.status_code != 200:
                last_error = f"登录接口返回状态码 {resp_signin.status_code}"
                await asyncio.sleep(0.2)
                continue

            try:
                res_data = resp_signin.json()
            except Exception:
                res_data = {}

            if res_data.get("ok") and res_data.get("data"):
                token = str(res_data["data"])
                cookies_dict = dict(client.cookies)
                cookies_to_inject = build_cookies_to_inject(
                    cookies_dict=cookies_dict,
                    token=token,
                    target_host=host,
                )
                clear_pending_captcha_session()
                return {
                    "ok": True,
                    "token": token,
                    "message": res_data.get("msg") or "登录成功！",
                    "cookies": cookies_dict,
                    "cookies_to_inject": cookies_to_inject,
                    "attempts": attempt,
                }
            else:
                msg = res_data.get("msg") or resp_signin.text[:100]
                last_error = f"接口登录未通过: {msg}"
                await asyncio.sleep(0.25)

    # Branch 3: Local OCR failed across retries -> Fallback to Agent Multimodal Vision
    if last_img_bytes and last_client_cookies:
        captcha_file = artifact_dir("screenshots") / "captcha_login.png"
        captcha_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            captcha_file.write_bytes(last_img_bytes)
        except Exception as write_err:
            logger.warning("Failed to persist captcha artifact: %s", write_err)

        b64_captcha = base64.b64encode(last_img_bytes).decode("ascii")
        _PENDING_SESSION = PendingCaptchaSession(
            origin=origin,
            host=host,
            username=username,
            password=password,
            cookies=last_client_cookies,
            captcha_image_path=str(captcha_file),
            captcha_image_base64=b64_captcha,
            timestamp=time.time(),
        )

        return {
            "ok": False,
            "status": "captcha-needed",
            "captcha_image_path": str(captcha_file),
            "captcha_image_base64": b64_captcha,
            "username": username,
            "message": (
                "本地 OCR 识别验证码失败，已将验证码图片发送到平台。"
                "当前 Agent 的默认多模态模型可直接观察识别此验证码，"
                "然后调用 browser_login(username=..., password=..., captcha='...') 完成登录。"
            ),
        }

    return {
        "ok": False,
        "token": None,
        "reason": last_error or "重试次数耗尽，登录失败",
        "attempts": max_retries,
    }
