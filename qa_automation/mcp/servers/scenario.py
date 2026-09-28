"""Declarative scenario runner MCP tool."""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP

from ... import scenario as scenario_ops
from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Scenario Runner")

    @mcp.tool(name="scenario_run")
    @instrument_tool
    async def scenario_run(
        scenario: str | dict[str, Any],
        stop_on_error: bool = True,
    ) -> dict[str, Any]:
        """运行声明式测试回归场景（支持 YAML/JSON 字典、文件路径或内联文本）。

        闭环场景流：支持多步骤自动编排、上下文变量传递（如 ${tab_id}、${page_id}）、
        结果断言（status_ok、message_contains）及失败原因定位。

        Args:
            scenario: 场景定义，可为文件路径（如 "scenarios/demo18.yaml"）、场景字典结构、或包含 YAML 文本的字符串
            stop_on_error: 遇到单步断言或执行失败时是否立即终止场景（默认 True）
        """
        from ..server import mcp as root_server

        async with Client(root_server) as client:
            return await scenario_ops.run_scenario(scenario, client, stop_on_error=stop_on_error)

    return mcp
