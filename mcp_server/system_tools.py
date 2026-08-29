"""Automation profile and observability tools."""

from __future__ import annotations

from fastmcp import FastMCP

from automation_profiles import profile_contract
from tool_metrics import metrics_snapshot


def create_server() -> FastMCP:
    mcp = FastMCP("Automation Diagnostics")

    @mcp.tool()
    async def ui_profile() -> dict:
        """返回当前页面 Profile、定位顺序和 VTable 点击验证顺序。"""
        return {"status": "ok", **profile_contract()}

    @mcp.tool()
    async def automation_metrics(limit: int = 50) -> dict:
        """返回浏览器侧工具的近期耗时、响应体积和上下文 token 估算。"""
        return metrics_snapshot(limit)

    return mcp
