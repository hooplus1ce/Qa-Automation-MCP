"""VTable-specific inspection and trusted-input tools."""

from __future__ import annotations

from fastmcp import FastMCP

import qa_automation as automation

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("VTable Automation")

    @mcp.tool()
    @instrument_tool
    async def vtable_cell_info(col: int, row: int) -> dict:
        """读取 VTable 单元格信息:值/行为分类/编辑能力/中心点/是否在视口。

        交互前后各调一次,让 AI 确认目标与结果(Playwright MCP 的验证回路思想)。
        """
        return await automation.cell_info(col, row)

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
    ) -> dict:
        """点击 VTable 指定单元格(col/row,0 起)。

        流程:绑定实例 → 滚动到视口 → VTable API 取确定性中心点 → trusted 鼠标点击
        (真实输入管道,isTrusted=true)→ 回读选中区间/编辑器状态验证,未命中自动重试。
        `observe_after=True` 时会在点击前监听主页面和全部 iframe,并在点击后立即
        返回 Ant Design Portal/消息/下拉浮层的新增事件与可见状态。
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
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_cell_resolve(field: str, record_index: int | list[int]) -> dict:
        """用 VTable 内部 API 将业务字段和记录索引解析为单元格地址。

        优先调用 getCellAddrByFieldRecord，旧版本实例才回退到
        getTableIndexByField + getTableIndexByRecordIndex。不会扫描 DOM 或猜测坐标。
        """
        return await automation.resolve_vtable_cell(field, record_index)

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
    ) -> dict:
        """按业务字段 + 记录索引点击 VTable 单元格。

        地址、滚动和中心点全部由 VTable 内部 API 解析，然后使用 Playwright trusted
        mouse 输入；AI 不需要也不能为该工具提供像素坐标。
        """
        return await automation.click_vtable_cell_by_field(
            field,
            record_index,
            double_click=double_click,
            button=button,
            verify=verify,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_meta(frame: str | None = None) -> dict:
        """读取 VTable 规模/冻结行列/主题等元数据(防御性取值)。

        AI 先拿到 rowCount/colCount/frozenRowCount,再规划批量读取范围与滚动策略。
        frame 可指定目标 iframe 名称、URL 子串或 'active' / 'vtable'；省略时自动定位。
        """
        return await automation.table_meta(frame=frame)
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
        """扫描当前 VTable 的列头、交互图标和有限值单元格的交互证据。

        结果只来自 VTable API 和已渲染 scenegraph，不会点击或展开 canvas DOM。每个
        表头/单元格及其图标都附带顶层页面 viewport 绝对坐标；editor 中的
        click_opens_dom_input 表示该单元格已有 editor，且 VTable 配置允许单击触发
        原生输入控件。把其中 geometry.point 原样交给 ui_interact 即可执行。
        同一 iframe 内有多张可见 VTable 时，首次调用只返回极简表格目录；从中选择
        table_index 再分析，避免把坐标落到另一张表。默认 interactive 模式只展开有
        交互证据且当前可见的样本，不返回业务值；可用 fields 缩小字段范围。诊断时才
        使用 mode="full"、include_values=True 或 visible_only=False，以控制 MCP token。

        frame 可指定目标 iframe 名称、URL 子串或 'active' / 'vtable'；省略时自动定位。
        """
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
    ) -> dict:
        """批量读取矩形区域(col0,row0)-(col1,row1)单元格值(行优先)。

        frame 可指定目标 iframe 名称、URL 子串或 'active' / 'vtable'；省略时自动定位。
        """
        return await automation.cells_read(col0, row0, col1, row1, frame=frame)

    @mcp.tool()
    @instrument_tool
    async def vtable_drop_files(
        col: int, row: int, files: list[str], data: dict[str, str] | None = None
    ) -> dict:
        """把服务端本地文件拖放到指定 VTable 单元格(Playwright 1.60 Locator.drop)。

        落点由 VTable getCellRelativeRect 换算成 .vtable 容器相对坐标,精确命中目标格。
        files 支持使用方项目工作区内的相对或绝对路径；工作区外路径会被拒绝。
        """
        return await automation.drop_files(col, row, files, data=data)

    return mcp
