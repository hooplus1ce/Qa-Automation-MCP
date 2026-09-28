"""Network packet listener and idle detection for Playwright pages.

Provides high-precision API interception, payload inspection, and network silence
assertion, specifically tuned for enterprise admin QA (APS/SCM API verification).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page, Request, Response

from .browser import current_page

BODY_LIMIT = 4000
HEADER_LIMIT = 40
TRUNCATED = "…(已截断)"


def _truncate_text(text: str, limit: int = BODY_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATED


def _truncate_body(raw: Any, limit: int = BODY_LIMIT) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        dumped = json.dumps(raw, ensure_ascii=False)
        if len(dumped) <= limit:
            return raw
        return dumped[:limit] + TRUNCATED
    text = str(raw)
    return _truncate_text(text, limit=limit)


@dataclass
class PacketRecord:
    """Captured HTTP network transaction."""

    url: str
    method: str
    resource_type: str
    status: int | None = None
    status_text: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: Any = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: Any = None
    duration_ms: float | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "resource_type": self.resource_type,
            "status": self.status,
            "status_text": self.status_text,
            "request_headers": self.request_headers,
            "request_body": self.request_body,
            "response_headers": self.response_headers,
            "response_body": self.response_body,
            "duration_ms": self.duration_ms,
            "start_time": round(self.start_time, 3),
            "end_time": round(self.end_time, 3) if self.end_time else None,
        }


class PageNetworkListener:
    """Manages request/response interception on a Playwright Page."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.active: bool = False
        self.url_patterns: list[re.Pattern[str]] = []
        self.methods: set[str] = set()
        self.res_types: set[str] = set()
        self.in_flight: set[Request] = set()
        self.in_flight_lock = asyncio.Lock()
        self.packets: deque[PacketRecord] = deque(maxlen=200)
        self.new_packet_event = asyncio.Event()
        self._request_start_times: dict[Request, float] = {}

    def configure(
        self,
        urls: list[str] | str | None = None,
        methods: list[str] | str | None = None,
        res_types: list[str] | str | None = None,
        clear_queue: bool = True,
    ) -> None:
        if clear_queue:
            self.packets.clear()
            self.new_packet_event.clear()

        # URL 过滤
        patterns: list[re.Pattern[str]] = []
        if urls:
            raw_list = [urls] if isinstance(urls, str) else list(urls)
            for p in raw_list:
                clean = p.strip()
                if clean:
                    patterns.append(re.compile(clean, re.IGNORECASE))
        self.url_patterns = patterns

        # Method 过滤
        if methods:
            raw_m = [methods] if isinstance(methods, str) else list(methods)
            self.methods = {m.strip().upper() for m in raw_m if m.strip()}
        else:
            self.methods = set()

        # Resource type 过滤（默认关注 fetch/xhr）
        if res_types:
            raw_r = [res_types] if isinstance(res_types, str) else list(res_types)
            self.res_types = {r.strip().lower() for r in raw_r if r.strip()}
        else:
            self.res_types = {"fetch", "xhr"}

    def attach(self) -> None:
        if self.active:
            return
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_request_failed)
        self.active = True

    def detach(self) -> None:
        if not self.active:
            return
        try:
            self.page.remove_listener("request", self._on_request)
            self.page.remove_listener("response", self._on_response)
            self.page.remove_listener("requestfailed", self._on_request_failed)
        except Exception:
            pass
        self.active = False
        self.in_flight.clear()
        self._request_start_times.clear()

    def _matches_filter(self, url: str, method: str, res_type: str) -> bool:
        if self.methods and method.upper() not in self.methods:
            return False
        if self.res_types and res_type.lower() not in self.res_types:
            return False
        if self.url_patterns:
            matched = any(p.search(url) for p in self.url_patterns)
            if not matched:
                return False
        return True

    def _on_request(self, request: Request) -> None:
        now = time.time()
        self._request_start_times[request] = now
        self.in_flight.add(request)

    async def _on_response(self, response: Response) -> None:
        request = response.request
        self.in_flight.discard(request)
        start_time = self._request_start_times.pop(request, time.time())
        end_time = time.time()
        duration_ms = round((end_time - start_time) * 1000, 2)

        url = request.url
        method = request.method
        res_type = request.resource_type

        if not self._matches_filter(url, method, res_type):
            return

        # 提取请求载荷
        post_data = request.post_data
        parsed_req_body: Any = post_data
        if post_data:
            try:
                parsed_req_body = json.loads(post_data)
            except Exception:
                parsed_req_body = post_data

        # 提取响应载荷
        parsed_res_body: Any = None
        try:
            raw_text = await response.text()
            try:
                parsed_res_body = json.loads(raw_text)
            except Exception:
                parsed_res_body = raw_text
        except Exception as exc:
            parsed_res_body = f"<{type(exc).__name__}: {exc}>"

        record = PacketRecord(
            url=url,
            method=method,
            resource_type=res_type,
            status=response.status,
            status_text=response.status_text,
            request_headers=dict(list(request.headers.items())[:HEADER_LIMIT]),
            request_body=_truncate_body(parsed_req_body),
            response_headers=dict(list(response.headers.items())[:HEADER_LIMIT]),
            response_body=_truncate_body(parsed_res_body),
            duration_ms=duration_ms,
            start_time=start_time,
            end_time=end_time,
        )

        self.packets.append(record)
        self.new_packet_event.set()

    def _on_request_failed(self, request: Request) -> None:
        self.in_flight.discard(request)
        start_time = self._request_start_times.pop(request, time.time())
        end_time = time.time()

        url = request.url
        method = request.method
        res_type = request.resource_type

        if not self._matches_filter(url, method, res_type):
            return

        failure = request.failure
        record = PacketRecord(
            url=url,
            method=method,
            resource_type=res_type,
            status=0,
            status_text=failure or "Request Failed",
            request_headers=dict(list(request.headers.items())[:HEADER_LIMIT]),
            request_body=_truncate_body(request.post_data),
            response_headers={},
            response_body=f"<Failed: {failure}>",
            duration_ms=round((end_time - start_time) * 1000, 2),
            start_time=start_time,
            end_time=end_time,
        )
        self.packets.append(record)
        self.new_packet_event.set()


# 全局绑定映射（Page 弱引用）
_page_listeners: dict[int, PageNetworkListener] = {}


async def get_or_create_listener(page: Page) -> PageNetworkListener:
    page_id = id(page)
    listener = _page_listeners.get(page_id)
    if listener is None or listener.page != page:
        listener = PageNetworkListener(page)
        _page_listeners[page_id] = listener
    return listener


async def net_listen_start(
    urls: list[str] | str | None = None,
    methods: list[str] | str | None = None,
    res_types: list[str] | str | None = None,
    clear_queue: bool = True,
) -> dict[str, Any]:
    """启动当前页面的网络请求监听器并设置捕获特征。"""
    page = await current_page()
    listener = await get_or_create_listener(page)
    listener.configure(
        urls=urls,
        methods=methods,
        res_types=res_types,
        clear_queue=clear_queue,
    )
    listener.attach()
    return {
        "status": "listening",
        "url_patterns": [p.pattern for p in listener.url_patterns],
        "methods": list(listener.methods),
        "res_types": list(listener.res_types),
        "queued_packets": len(listener.packets),
    }


async def net_listen_wait(
    pattern: str | None = None,
    method: str | None = None,
    timeout: float = 10.0,
    drain: bool = True,
) -> dict[str, Any]:
    """等待匹配指定 pattern 或方法的网络数据包到达。

    Args:
        pattern: 正则或关键字子串（如 "approverOptions" 或 "/api/v1/"）
        method: HTTP 方法（GET/POST/PUT 等）
        timeout: 等待秒数。为 0 时立即返回已有队列中最合适的一个包，不阻塞。
        drain: 是否从队列中移除命中包（默认 True，防止重复消费）
    """
    page = await current_page()
    listener = await get_or_create_listener(page)
    if not listener.active:
        listener.attach()

    regex = re.compile(pattern, re.IGNORECASE) if pattern else None
    target_method = method.strip().upper() if method else None

    def _find_matching_index() -> int | None:
        for idx, pkt in enumerate(listener.packets):
            if target_method and pkt.method.upper() != target_method:
                continue
            if regex and not regex.search(pkt.url):
                continue
            return idx
        return None

    start_t = time.time()
    deadline = start_t + max(timeout, 0.0)

    # 1. 先检查队列中是否已有现成命中的数据包
    idx = _find_matching_index()
    if idx is not None:
        pkt = listener.packets[idx]
        if drain:
            del listener.packets[idx]
        return {
            "ok": True,
            "packet": pkt.to_dict(),
            "elapsed_seconds": round(time.time() - start_t, 3),
        }

    if timeout <= 0:
        return {
            "ok": False,
            "packet": None,
            "message": f"未在队列中检索到匹配 [{pattern or '*'}] 的网络数据包（即时快照模式）",
            "elapsed_seconds": 0.0,
            "queued_count": len(listener.packets),
        }

    # 2. 异步等待新数据包到达
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        listener.new_packet_event.clear()
        try:
            await asyncio.wait_for(listener.new_packet_event.wait(), timeout=min(remaining, 0.5))
        except TimeoutError:
            pass

        idx = _find_matching_index()
        if idx is not None:
            pkt = listener.packets[idx]
            if drain:
                del listener.packets[idx]
            return {
                "ok": True,
                "packet": pkt.to_dict(),
                "elapsed_seconds": round(time.time() - start_t, 3),
            }

    elapsed = round(time.time() - start_t, 3)
    return {
        "ok": False,
        "packet": None,
        "message": f"在 {elapsed}s 内未捕获到匹配 [{pattern or '*'}] 的网络请求",
        "elapsed_seconds": elapsed,
        "queued_count": len(listener.packets),
    }


async def net_listen_wait_silent(
    timeout: float = 5.0,
    min_silent_ms: int = 400,
) -> dict[str, Any]:
    """等待页面所有网络请求处于静止状态（无进行中的异步请求）。"""
    page = await current_page()
    listener = await get_or_create_listener(page)
    if not listener.active:
        listener.attach()

    start_t = time.time()
    deadline = start_t + max(timeout, 0.2)
    silent_duration = min_silent_ms / 1000.0

    silent_start: float | None = None
    while time.time() < deadline:
        if len(listener.in_flight) == 0:
            if silent_start is None:
                silent_start = time.time()
            elif (time.time() - silent_start) >= silent_duration:
                return {
                    "ok": True,
                    "silent": True,
                    "in_flight_count": 0,
                    "elapsed_seconds": round(time.time() - start_t, 3),
                }
        else:
            silent_start = None

        await asyncio.sleep(0.05)

    return {
        "ok": False,
        "silent": False,
        "in_flight_count": len(listener.in_flight),
        "elapsed_seconds": round(time.time() - start_t, 3),
        "message": f"等待 {timeout}s 后仍有 {len(listener.in_flight)} 个进行中的网络请求",
    }


async def net_listen_snapshot(
    pattern: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """获取当前已捕获网络数据包的只读快照列表（不等待、不出队）。"""
    page = await current_page()
    listener = await get_or_create_listener(page)
    regex = re.compile(pattern, re.IGNORECASE) if pattern else None

    matches: list[dict[str, Any]] = []
    for pkt in reversed(listener.packets):
        if regex and not regex.search(pkt.url):
            continue
        matches.append(pkt.to_dict())
        if len(matches) >= limit:
            break

    return {
        "total_queued": len(listener.packets),
        "matched_count": len(matches),
        "packets": matches,
    }


async def net_listen_stop() -> dict[str, Any]:
    """停止当前页面的网络请求监听并释放资源。"""
    page = await current_page()
    page_id = id(page)
    listener = _page_listeners.pop(page_id, None)
    if listener:
        listener.detach()
        packet_count = len(listener.packets)
        listener.packets.clear()
        return {"status": "stopped", "cleared_packets": packet_count}
    return {"status": "not_active", "cleared_packets": 0}
