"""CDP endpoint probing, managed Chrome launch, and executable discovery.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

logger = logging.getLogger("qa_automation.browser")


def _normalize_cdp_url(cdp_url: str) -> str:
    value = str(cdp_url).strip()
    if not value:
        raise ValueError("cdp_url must not be empty")
    return value.rstrip("/")


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
