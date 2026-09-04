"""VTable-specific inspection and trusted-input tools."""

from __future__ import annotations

from fastmcp import FastMCP

import qa_automation as automation

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("VTable Automation")

    @mcp.tool()
    @instrument_tool
    async def vtable_discover(frame: str | None = None) -> dict:
        """识别当前页面所有可见 VTable，并返回 frame 与 table_index。

        后续所有 VTable 工具都应复用返回的 frame/table_index，避免多 iframe 或多表
        页面误绑定到第一张表。
        """
        return await automation.discover_vtables(frame=frame)

    @mcp.tool()
    @instrument_tool
    async def vtable_cell_info(
        col: int,
        row: int,
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """读取指定 VTable 单元格值/行为分类/编辑能力/中心点/视口状态。"""
        return await automation.cell_info(
            col, row, frame=frame, table_index=table_index
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_cell_click(
        col: int,
        row: int,
        double_click: bool = False,
        button: str = "left",
        verify: bool = True,
        observe_after: bool = False,
        settle_ms: int = 300,
        max_results: int = 20,
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """稳定点击指定 VTable 单元格。

        流程:显式绑定目标表 → 滚动到视口 → API 取中心点 → 等待鼠标悬停后的几何
        稳定 → trusted 鼠标点击 → 回读选中区间/编辑器状态验证。
        """
        return await automation.click_cell(
            col,
            row,
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            frame=frame,
            table_index=table_index,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_cell_resolve(
        field: str,
        record_index: int | list[int],
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """用目标 VTable 内部 API 将业务字段和记录索引解析为单元格地址。"""
        return await automation.resolve_vtable_cell(
            field,
            record_index,
            frame=frame,
            table_index=table_index,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_cell_click_by_field(
        field: str,
        record_index: int | list[int],
        double_click: bool = False,
        button: str = "left",
        verify: bool = True,
        observe_after: bool = False,
        settle_ms: int = 300,
        max_results: int = 20,
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """按目标 VTable 的业务字段 + 记录索引稳定点击单元格。"""
        return await automation.click_vtable_cell_by_field(
            field,
            record_index,
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            frame=frame,
            table_index=table_index,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_meta(
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """读取目标 VTable 规模/冻结行列/主题等元数据。"""
        return await automation.table_meta(frame=frame, table_index=table_index)

    @mcp.tool()
    @instrument_tool
    async def vtable_analysis(
        max_columns: int = 20,
        sample_rows: int = 2,
        mode: str = "interactive",
        fields: list[str] | None = None,
        include_values: bool = False,
        visible_only: bool = True,
        table_index: int | None = None,
        frame: str | None = None,
    ) -> dict:
        """扫描目标 VTable 的列头、交互图标和有限值单元格交互证据。"""
        return await automation.vtable_analysis(
            max_columns=max_columns,
            sample_rows=sample_rows,
            mode=mode,
            fields=fields,
            include_values=include_values,
            visible_only=visible_only,
            table_index=table_index,
            frame=frame,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_read_cells(
        col0: int,
        row0: int,
        col1: int,
        row1: int,
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """读取目标 VTable 矩形区域单元格值(行优先)。"""
        return await automation.cells_read(
            col0,
            row0,
            col1,
            row1,
            frame=frame,
            table_index=table_index,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_drop_files(
        col: int,
        row: int,
        files: list[str],
        data: dict[str, str] | None = None,
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """把工作区文件拖放到目标 VTable 单元格。"""
        return await automation.drop_files(
            col,
            row,
            files,
            data=data,
            frame=frame,
            table_index=table_index,
        )

    return mcp
