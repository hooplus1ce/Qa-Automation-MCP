"""Network monitoring and packet inspection MCP tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ... import net as net_ops
from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Network Monitoring")

    @mcp.tool(
        name="net_listen_start",
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": False},
    )
    @instrument_tool
    async def net_listen_start(
        urls: list[str] | str | None = None,
        methods: list[str] | str | None = None,
        res_types: list[str] | str | None = None,
        clear_queue: bool = True,
    ) -> dict[str, Any]:
        """启动当前页面的网络请求监听器并设置捕获特征（用于接口载荷断言与缺陷定位）。

        在触发前端交互前调用本工具。抓取的数据包进入队列，动作之后用 net_listen_wait 取包。

        Args:
            urls: URL 关键字或正则（如 "approverOptions" 或 "/api/v1/"），支持单个或列表
            methods: HTTP 方法（如 "GET"、"POST"），省略表示不限制
            res_types: 资源类型（如 "fetch"、"xhr"、"document"），默认仅监听 fetch/xhr
            clear_queue: 是否清空此前捕获的残留队列（默认 True）
        """
        return await net_ops.net_listen_start(
            urls=urls,
            methods=methods,
            res_types=res_types,
            clear_queue=clear_queue,
        )

    @mcp.tool(
        name="net_listen_wait",
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def net_listen_wait(
        pattern: str | None = None,
        method: str | None = None,
        timeout: float = 10.0,
        drain: bool = True,
    ) -> dict[str, Any]:
        """等待匹配指定 pattern 或 HTTP 方法的网络数据包到达，并返回出入参载荷。

        命中时返回完整 URL、状态码、耗时、请求体/响应体（自动格式化为 JSON 且支持防爆截断）。

        Args:
            pattern: 正则或关键字子串（如 "approverOptions" 或 "save"）
            method: HTTP 方法（如 "POST"）
            timeout: 最长等待秒数（默认 10.0）。为 0 时为即时快照模式（立刻出队已有数据包，不等待）
            drain: 命中后是否从队列中移除（默认 True，防止多轮断言重复读到同一个请求）
        """
        return await net_ops.net_listen_wait(
            pattern=pattern,
            method=method,
            timeout=timeout,
            drain=drain,
        )

    @mcp.tool(
        name="net_listen_wait_silent",
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def net_listen_wait_silent(
        timeout: float = 5.0,
        min_silent_ms: int = 400,
    ) -> dict[str, Any]:
        """等待页面所有网络请求处于静止状态（等待所有正在进行中的异步 AJAX/Fetch 请求全部完成）。

        Args:
            timeout: 最长等待秒数（默认 5.0）
            min_silent_ms: 连续无在途请求的静止时长阈值毫秒数（默认 400ms）
        """
        return await net_ops.net_listen_wait_silent(
            timeout=timeout,
            min_silent_ms=min_silent_ms,
        )

    @mcp.tool(
        name="net_listen_snapshot",
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def net_listen_snapshot(
        pattern: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """获取当前已捕获网络数据包的只读快照列表（不阻塞、不出队）。

        Args:
            pattern: URL 正则或关键字过滤
            limit: 每页最多返回的数据包数量（默认 20，最大 500）
            offset: 匹配包的分页偏移量（默认 0）；按 newest_first 顺序读取，结果返回 next_offset
        """
        return await net_ops.net_listen_snapshot(
            pattern=pattern,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(
        name="net_listen_stop",
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": True},
    )
    @instrument_tool
    async def net_listen_stop() -> dict[str, Any]:
        """停止当前页面的网络请求监听器并释放事件资源。"""
        return await net_ops.net_listen_stop()

    return mcp
