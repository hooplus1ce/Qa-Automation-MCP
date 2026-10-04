"""Download interception and artifact persistence.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

try:
    from playwright.async_api import Browser, Frame, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    Frame = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]
from ..workspace import artifact_dir, artifact_file
from .state import _FALLBACK_REGISTRY_LIMIT, _state

logger = logging.getLogger("qa_automation.browser")


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


_DOWNLOAD_PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")


def _scan_download_candidates(
    directory: Path,
    *,
    cutoff_ns: int,
    filename_contains: str | None,
) -> dict[str, dict[str, Any]]:
    """List plausible completed download files newer than the cutoff.

    Chromium 中间态文件(.crdownload/.part/.tmp)一律跳过；只统计
    ``mtime_ns >= cutoff_ns`` 的普通文件，等待中的调用方负责用两次
    扫描的 size/mtime 对比确认文件已写完。
    """
    candidates: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return candidates
    for entry in sorted(directory.iterdir()):
        try:
            if not entry.is_file():
                continue
            name = entry.name
            if name.endswith(_DOWNLOAD_PARTIAL_SUFFIXES):
                continue
            if filename_contains and filename_contains.lower() not in name.lower():
                continue
            stat = entry.stat()
            if stat.st_mtime_ns < cutoff_ns:
                continue
            candidates[name] = {
                "path": str(entry),
                "name": name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        except OSError:
            continue
    return candidates


def _drain_download_failures() -> list[str]:
    failures = list(_state.download_failures)
    _state.download_failures.clear()
    return failures


async def _wait_download_impl(
    *,
    timeout_ms: int = 30_000,
    filename_contains: str | None = None,
    include_recent_seconds: int = 60,
) -> dict[str, Any]:
    """等待下载落盘并返回文件路径。

    下载本身由 _persist_download / CDP setDownloadBehavior 自动写入
    工作区 downloads 目录；本工具只负责"等到文件写完"并回报路径。
    判定完成 = 连续两次扫描 size 与 mtime 均未变化。
    """
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    if include_recent_seconds < 0:
        raise ValueError("include_recent_seconds must be non-negative")
    directory = artifact_dir("downloads")
    started_monotonic = time.monotonic()
    start_wall_ns = time.time_ns()
    cutoff_ns = start_wall_ns - int(include_recent_seconds * 1_000_000_000)
    timeout_seconds = timeout_ms / 1000
    previous: dict[str, dict[str, Any]] = {}
    deadline_message = "no matching completed download"
    while True:
        current = _scan_download_candidates(
            directory,
            cutoff_ns=cutoff_ns,
            filename_contains=filename_contains,
        )
        stable = [
            info
            for name, info in current.items()
            if name in previous
            and previous[name]["size"] == info["size"]
            and previous[name]["mtime_ns"] == info["mtime_ns"]
        ]
        if stable:
            stable.sort(key=lambda item: item["mtime_ns"], reverse=True)
            return {
                "status": "completed",
                "download_dir": str(directory),
                "files": stable,
                "elapsed_ms": int((time.monotonic() - started_monotonic) * 1000),
                "download_failures": _drain_download_failures(),
            }
        previous = current
        elapsed = time.monotonic() - started_monotonic
        if elapsed >= timeout_seconds:
            return {
                "status": "timeout",
                "download_dir": str(directory),
                "elapsed_ms": int(elapsed * 1000),
                "recent_files": sorted(current),
                "message": deadline_message,
                "download_failures": _drain_download_failures(),
            }
        await asyncio.sleep(min(0.25, max(0.05, timeout_seconds - elapsed)))


async def wait_download(
    *,
    timeout_ms: int = 30_000,
    filename_contains: str | None = None,
    include_recent_seconds: int = 60,
) -> dict[str, Any]:
    return await _wait_download_impl(
        timeout_ms=timeout_ms,
        filename_contains=filename_contains,
        include_recent_seconds=include_recent_seconds,
    )
