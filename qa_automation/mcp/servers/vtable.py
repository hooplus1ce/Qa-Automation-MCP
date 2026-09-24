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
        """识别当前页面所有可见 VTable，并返回稳定 frame_id 及 table_index。

        后续所有 VTable 工具都应复用返回的 frame/table_index，避免多 iframe 或多表
        页面误绑定到第一张表。

        Args:
            frame: 只扫该 frame：省略=遍历页面全部 iframe；可传 main/top/active/vtable、frame_id 或 name/URL 子串
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
        """读取指定 VTable 单元格值/行为分类/编辑能力/中心点/视口状态。

        Args:
            col: 全表列号，0-based，含左/右冻结列；不支持负数，越界时中心点为空
            row: 全表行号，0-based，含表头行与冻结行；第 0 行通常是列头而非数据行
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
        """
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

        Args:
            col: 全表列号，0-based，含左/右冻结列；不支持负数，越界点不到
            row: 全表行号，0-based，含表头行与冻结行；点列头排序也用它
            double_click: False=单击选中（默认）；True=双击，用于进入 editCellTrigger=doubleclick 的编辑器
            button: 按下的鼠标键：left（默认）/ middle / right，其它值报错
            verify: True（默认）回读选中区间、场景图绘制与局部截图作为落地证据，未过则降级为 unverified 并自动重试一次；False=点完即返回，verification_skipped
            observe_after: 是否在点击期间收集 Portal/提示/下拉（默认 False）；要看单元格触发的弹窗就打开
            settle_ms: 浮层观察窗口毫秒数（默认 300，限 0–2000，越界报错）；仅 observe_after 时生效
            max_results: 浮层条目上限（默认 20），调小会丢掉排序靠后的浮层
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
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
        """用目标 VTable 内部 API 将业务字段和记录索引解析为单元格地址。

        Args:
            field: 列定义里的 field/key 业务字段名（不是中文表头标题），取自 vtable_analysis 的 columns[].field
            record_index: 数据记录序号，0-based 且不含表头行，直接透传给 VTable API；树形/分组表可传多级索引数组
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
        """
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
        """按目标 VTable 的业务字段 + 记录索引稳定点击单元格。

        先用 VTable API 把 field/record_index 解成 col/row，再走 vtable_cell_click
        的完整滚动+可信点击+验证流程，因此无需自己换算行号。

        Args:
            field: 列定义里的 field/key 业务字段名（不是中文表头标题），取自 vtable_analysis 的 columns[].field
            record_index: 数据记录序号，0-based 且不含表头行；树形/分组表可传多级索引数组
            double_click: False=单击选中（默认）；True=双击，用于进入 editCellTrigger=doubleclick 的编辑器
            button: 按下的鼠标键：left（默认）/ middle / right，其它值报错
            verify: True（默认）回读选中区间、场景图绘制与局部截图作为落地证据，未过则降级为 unverified；False=点完即返回
            observe_after: 是否在点击期间收集 Portal/提示/下拉（默认 False）；要看单元格触发的弹窗就打开
            settle_ms: 浮层观察窗口毫秒数（默认 300，限 0–2000，越界报错）；仅 observe_after 时生效
            max_results: 浮层条目上限（默认 20），调小会丢掉排序靠后的浮层
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
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
            frame=frame,
            table_index=table_index,
        )

    @mcp.tool()
    @instrument_tool
    async def vtable_meta(
        frame: str | None = None,
        table_index: int | None = None,
    ) -> dict:
        """读取目标 VTable 规模/冻结行列/主题等元数据。

        Args:
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
        """
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
        """扫描目标 VTable 的列头、交互图标和有限值单元格交互证据。

        返回的 point/page_box 均为顶层页面视口 CSS 像素，配 analysis_id 交给
        ui_click / ui_interact 时会在执行前做布局签名校验。

        Args:
            max_columns: 扫描列数上限（默认 20，钳到 1–100）；调小会丢掉靠右的列，用 truncated.columns 判断是否被截
            sample_rows: 每列向下采样的正文行数（默认 2，钳到 0–8），从表头下一行连续取；0=完全不出单元格证据，只剩表头图标
            mode: interactive（默认）=只留有交互证据的紧凑结果；full=补上表头几何、evidence 链和编辑器全量
            fields: 按列 field 名精确白名单过滤（最多 30 项）；一旦非空就改扫前 100 列，max_columns 被忽略
            include_values: 默认 False；True 时每个采样单元格附 value（超 240 字符会被截断，明显涨 token）
            visible_only: 默认 True，丢弃中心点不在顶层视口内的几何；False 保留横向/纵向滚动窗外的元素
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会返回 needs_table_selection
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
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
        table_index: int | None = None,
    ) -> dict:
        """读取目标 VTable 矩形区域单元格值（最多 2,000 格，超限请分页）。

        Args:
            col0: 矩形一个角的列号，0-based 全表列号（含冻结列）；四个坐标必须一起给
            row0: 矩形同一个角的行号，0-based 全表行号（含表头行）
            col1: 对角列号，与 col0 顺序无关（内部取 min/max），闭区间含两端
            row1: 对角行号，与 row0 顺序无关，闭区间含两端
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
        """
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
        """把工作区文件拖放到目标 VTable 单元格。

        Args:
            col: 落点全表列号，0-based，含左/右冻结列
            row: 落点全表行号，0-based，含表头行；投放前会自动滚到该格
            files: 待投放文件路径列表，至少 1 个；相对路径按工作区根解析、不得越界且必须已存在；单个按字符串投递、多个按数组投递
            data: 预留的 dataTransfer 文本键值；当前实现未消费该参数，传了不影响投放结果
            frame: 目标 frame：省略=自动挑含 .vtable 的 frame（优先激活 iframe）；可传 main/top/active/vtable 或 frame_id
            table_index: 该 frame 内第几个 .vtable 根节点，0-based；省略时多张可见表会直接要求补传
        """
        return await automation.drop_files(
            col,
            row,
            files,
            data=data,
            frame=frame,
            table_index=table_index,
        )

    return mcp
