"""AntV X6 flowchart & workflow canvas MCP tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ...components import x6 as x6_ops
from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("AntV X6 Workflow")

    @mcp.tool(name="x6_nodes")
    @instrument_tool
    async def x6_nodes(
        frame: str | None = None,
        auto_fit: bool = True,
    ) -> dict[str, Any]:
        """一键读取当前 AntV X6 流程图画布的拓扑结构、全部节点及端口信息。

        返回数据顶层包含：node_count / edge_count / nodes / edges / zoom / translate / container_viewport。
        每个节点包含：cellId、显示文本、业务分类 (kind/data)、视口绝对坐标 (viewport_center/viewport_rect) 及端口桩坐标列表。

        Args:
            frame: 所在 frame（默认自动查找 active 模块 iframe 或带 .x6-graph 的 frame）
            auto_fit: 是否在读取前自适应居中回正视口并激活平移缩放（针对前端未居中缺陷做自动补偿，默认 True）
        """
        session = await x6_ops.bind_x6(frame=frame, auto_fit=auto_fit)
        return await x6_ops.get_topology(session, auto_fit=False)

    @mcp.tool(name="x6_move_node")
    @instrument_tool
    async def x6_move_node(
        node: str,
        dx: int,
        dy: int,
        frame: str | None = None,
    ) -> dict[str, Any]:
        """真实鼠标拖拽位移指定的流程图节点（基于 Playwright 平滑插值轨迹）。

        Args:
            node: 目标节点 cellId（如 "n2"）或节点显示名称（如 "审批A"）
            dx: X 轴位移像素差（正向右，负向左）
            dy: Y 轴位移像素差（正向下，负向上）
            frame: 所在 frame
        """
        session = await x6_ops.bind_x6(frame=frame, auto_fit=False)
        return await x6_ops.move_node(session, node=node, dx=dx, dy=dy)

    @mcp.tool(name="x6_connect")
    @instrument_tool
    async def x6_connect(
        from_node: str,
        to_node: str,
        from_port: str = "out-0",
        to_port: str = "in-0",
        frame: str | None = None,
    ) -> dict[str, Any]:
        """从源节点的出口桩拖拽连接至目标节点的入口桩（真实物理拉线）。

        Args:
            from_node: 源节点 cellId 或节点名称（如 "开始"）
            to_node: 目标节点 cellId 或节点名称（如 "部门审批"）
            from_port: 源节点出口桩 id（如 "out-0" 或 "out-right"）
            to_port: 目标节点入口桩 id（如 "in-0" 或 "in-left"）
            frame: 所在 frame
        """
        session = await x6_ops.bind_x6(frame=frame, auto_fit=False)
        return await x6_ops.connect_nodes(
            session,
            from_node=from_node,
            to_node=to_node,
            from_port=from_port,
            to_port=to_port,
        )

    @mcp.tool(name="x6_click_node")
    @instrument_tool
    async def x6_click_node(
        node: str,
        double: bool = False,
        frame: str | None = None,
    ) -> dict[str, Any]:
        """单击选中或双击打开 X6 流程图节点属性配置面板。

        Args:
            node: 目标节点 cellId 或节点显示名称
            double: True=双击（呼出配置抽屉/弹窗）；False=单击选中（默认 False）
            frame: 所在 frame
        """
        session = await x6_ops.bind_x6(frame=frame, auto_fit=False)
        return await x6_ops.click_node(session, node=node, double=double)

    @mcp.tool(name="x6_delete_node")
    @instrument_tool
    async def x6_delete_node(
        node: str,
        frame: str | None = None,
    ) -> dict[str, Any]:
        """选中并删除指定的流程图节点（自动定位、单击选中并派发键盘删除键）。

        Args:
            node: 目标节点 cellId 或节点显示名称
            frame: 所在 frame
        """
        session = await x6_ops.bind_x6(frame=frame, auto_fit=False)
        return await x6_ops.delete_node(session, node=node)

    return mcp
