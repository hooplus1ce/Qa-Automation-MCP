"""Automation profile and observability tools."""

from __future__ import annotations

from fastmcp import FastMCP

from ...profiles import profile_contract
from ..metrics import metrics_snapshot, reset_metrics
from ..protocol import ensure_response_metadata


def create_server() -> FastMCP:
    mcp = FastMCP("Automation Diagnostics")

    @mcp.tool()
    async def ui_profile() -> dict:
        """返回当前页面 Profile、定位顺序和 VTable 点击验证顺序。"""
        return ensure_response_metadata({"status": "ok", **profile_contract()})

    @mcp.tool()
    async def automation_metrics(limit: int = 50) -> dict:
        """返回浏览器侧工具的近期耗时、响应体积和上下文 token 估算。

        Args:
            limit: recent 里返回的最近调用条数（默认 50，钳到 1–200）；summary 不受它影响
        """
        return metrics_snapshot(limit)

    @mcp.tool()
    async def automation_metrics_reset() -> dict:
        """清空当前进程内的近期工具指标与聚合统计。"""
        reset_metrics()
        return ensure_response_metadata({"status": "ok", "reset": True})

    return mcp
