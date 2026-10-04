"""腾讯文档工具的 Pydantic 结果模型与 TestCase 兼容别名。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..mcp.protocol import PROTOCOL_VERSION, new_trace_id


class McpToolResult(BaseModel):
    """Common additive metadata for structured MCP tool results."""

    status: str = "ok"
    ok: bool = True
    protocol_version: str = PROTOCOL_VERSION
    trace_id: str = Field(default_factory=new_trace_id)
    code: str = "OK"
    error: str | None = None
    next_action: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


# Pydantic 结果数据模型
# ---------------------------------------------------------------------------


class SheetConnectResult(McpToolResult):
    """腾讯文档表格连接结果。"""

    ok: bool
    file_id: str
    title: str | None = None
    url: str | None = None
    active_tab_sheet: str | None = None
    active_tab_id: str | None = None
    total_sheets: int = 0
    visible_sheets_count: int = 0
    sheets: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class SheetRowDetail(McpToolResult):
    """单行记录详情。"""

    sheet_name: str
    sheet_id: str | None = None
    row_index: int = Field(description="1-based 物理行号（对应 Excel 界面行号）")
    row_id: str = Field(description="主键或唯一标识，如用例编号、订单编号、员工号等")
    case_id: str = Field(default="", description="兼容历史用例编号字段")
    data: dict[str, Any] = Field(default_factory=dict, description="整行所有字段键值对")


class SheetQueryResult(McpToolResult):
    """分页表格查询结果；has_more 可用于安全地继续读取下一页。"""

    sheet_name: str
    sheet_id: str | None = None
    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 20
    offset: int = 0
    has_more: bool = False
    next_offset: int | None = None
    coverage: dict[str, Any] = Field(default_factory=dict)


class SheetUpdateResult(McpToolResult):
    """单行回写结果。"""

    ok: bool
    sheet_name: str
    sheet_id: str | None = None
    row_id: str = Field(default="", description="行标识符")
    case_id: str = Field(default="", description="兼容历史用例编号字段")
    result: str = Field(default="", description="兼容历史执行结果字段")
    executor: str | None = None
    execute_time: str | None = None
    remark: str | None = None
    updated_fields: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class SheetBatchUpdateResult(McpToolResult):
    """批量表格更新结果。"""

    ok: bool
    sheet_name: str
    sheet_id: str | None = None
    total_submitted: int
    updated_count: int
    updated_cells_count: int = 0
    updated_rows: list[str] = Field(default_factory=list)
    updated_cases: list[str] = Field(default_factory=list)
    not_found_rows: list[str] = Field(default_factory=list)
    not_found_cases: list[str] = Field(default_factory=list)
    failed_items: list[dict[str, str]] = Field(default_factory=list)
    failed_cases: list[dict[str, str]] = Field(default_factory=list)
    message: str = ""


class SheetDimensionResult(McpToolResult):
    """表格行/列维度增删结果（物理插删行，非清空内容）。"""

    ok: bool
    sheet_name: str
    sheet_id: str | None = None
    dimension_type: str = Field(default="row", description="row | col")
    requested: int = Field(default=0, description="请求处理的目标数量")
    affected_indices: list[int] = Field(
        default_factory=list,
        description="实际处理的全表 0-based 行号（删除按降序执行）",
    )
    affected_rows: list[str] = Field(default_factory=list, description="命中的主键/用例编号")
    not_found_rows: list[str] = Field(default_factory=list, description="未匹配到的主键/用例编号")
    not_found_indices: list[int] = Field(default_factory=list, description="越界或非法的行号")
    skipped_header: bool = Field(default=False, description="是否因保护表头而跳过了第 1 行")
    failed_items: list[dict[str, str]] = Field(default_factory=list)
    row_count_before: int | None = Field(default=None, description="操作前子表总行数")
    row_count_after: int | None = Field(default=None, description="操作后子表总行数")
    dry_run: bool = False
    message: str = ""


# 别名兼容
TestCaseConnectResult = SheetConnectResult
TestCaseDetail = SheetRowDetail
TestCaseQueryResult = SheetQueryResult
TestCaseUpdateResult = SheetUpdateResult
TestCaseBatchUpdateResult = SheetBatchUpdateResult
