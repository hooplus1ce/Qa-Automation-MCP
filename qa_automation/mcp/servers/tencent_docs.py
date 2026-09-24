"""Tencent Docs table/sheet automation tools.

基于官方 Tencent Docs MCP 协议与最佳实践，提供腾讯文档中表格（在线表格与智能表格）
的高性能通用化操作与数据管理：
1. 弹性连接与多格式 URL 解析（在线表格、多维智能表、纯 file_id）；
2. 动态表头与多别名弹性映射（主键、分类、状态、负责人、日期、备注等）；
3. 全字段无损提取与全文/自定义多字段检索；
4. 严格边界保护与安全分块（彻底规避 60871 错误）；
5. 单行/批量原子聚合回写（支持扩展列与防限流）。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ...config import TENCENT_DOCS_MCP_URL, resolve_tencent_docs_token
from ...tencent_sheet import (
    SheetBatchUpdateResult,
    SheetConnectResult,
    SheetQueryResult,
    SheetRowDetail,
    SheetUpdateResult,
    TencentDocError,
    TestCaseBatchUpdateResult,
    TestCaseConnectResult,
    TestCaseDetail,
    TestCaseQueryResult,
    TestCaseUpdateResult,
    parse_file_id,
    tencent_sheet_manager,
)

logger = logging.getLogger(__name__)


async def _call_mcp_tool(
    client: httpx.AsyncClient,
    tool_name: str,
    arguments: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    """通过 JSON-RPC 调用腾讯文档 MCP 接口并解析结果（兼容上游标准协议）。"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": token,
    }
    response = await client.post(
        TENCENT_DOCS_MCP_URL,
        json=payload,
        headers=headers,
        timeout=30.0,
    )
    response.raise_for_status()
    resp_data = response.json()
    if "error" in resp_data:
        raise RuntimeError(f"Tencent Docs MCP RPC error: {resp_data['error']}")

    result = resp_data.get("result", {})
    if "structuredContent" in result and isinstance(result["structuredContent"], dict):
        return result["structuredContent"]

    content = result.get("content", [])
    if not content or not isinstance(content, list):
        return {}

    first_text = content[0].get("text", "")
    if not first_text:
        return {}

    try:
        return json.loads(first_text, strict=False)
    except Exception:
        return {"raw_text": first_text, "csv_data": first_text}


def create_server() -> FastMCP:
    mcp = FastMCP("Tencent Docs Automation")

    # =======================================================================
    # 核心标准工具集：腾讯文档表格操作管理（tencent_sheet_*）
    # =======================================================================

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "连接腾讯文档表格", "readOnlyHint": False},
    )
    def tencent_sheet_connect(
        url_or_file_id: str | None = None,
        token: str | None = None,
    ) -> SheetConnectResult:
        """连接并绑定腾讯文档在线表格或多维智能表格。

        解析文档链接或 file_id，拉取所有子表元数据（名称、行数、列数、活跃状态）并缓存映射，
        设为当前活跃工作表格，后续表格查询与回写操作直接生效于该文档。

        Args:
            url_or_file_id: 腾讯文档完整链接（如 https://docs.qq.com/sheet/DUUhiWnFvZVdibWZq?tab=rukw1z）、
                           智能表格链接（https://docs.qq.com/smartsheet/...）或纯 file_id；
                           缺省时自动使用环境变量 TENCENT_DOCS_DEFAULT_URL
            token: 腾讯文档 OpenAPI 访问令牌（可选，缺省自动从 .mcp.json、配置或环境变量读取）
        """
        try:
            res = tencent_sheet_manager.connect(url_or_file_id=url_or_file_id, token=token)
            return SheetConnectResult(**res)
        except TencentDocError as e:
            raise ToolError(f"连接腾讯文档失败: {e}") from e
        except Exception as e:
            raise ToolError(f"连接腾讯文档发生未知异常: {e}") from e

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "列出子表清单", "readOnlyHint": True},
    )
    def tencent_sheet_list_sheets(
        url_or_file_id: str | None = None,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出表格中的所有子表清单及各子表规模（名称、sheet_id、行数、列数、活跃状态）。

        若传入 url_or_file_id 则直接列出目标文档的所有子表；未传入时自动使用当前已连接文档。
        """
        try:
            if url_or_file_id or not tencent_sheet_manager.active_file_id:
                tencent_sheet_manager.connect(url_or_file_id=url_or_file_id, token=token)

            active_id = tencent_sheet_manager.active_tab_id
            active_name = tencent_sheet_manager.active_tab_sheet

            sheets = [
                {
                    "sheet_name": s["sheet_name"],
                    "sheet_id": s["sheet_id"],
                    "row_count": s["row_count"],
                    "col_count": s["col_count"],
                    "hidden": s.get("hidden", False),
                    "sheet_type": s.get("sheet_type", "worksheet"),
                    "is_active": (s["sheet_id"] == active_id or s["sheet_name"] == active_name),
                }
                for s in tencent_sheet_manager._sheets_by_name.values()
                if not s.get("hidden")
            ]
            return sheets
        except TencentDocError as e:
            raise ToolError(f"获取子表清单失败: {e}") from e
        except Exception as e:
            raise ToolError(f"获取子表清单发生未知异常: {e}") from e

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "获取单行数据详情", "readOnlyHint": True},
    )
    def tencent_sheet_get_row(
        sheet_name: str | None = None,
        row_id: str | None = None,
        row_index: int | None = None,
        search_column: str | None = None,
        search_value: str | None = None,
        case_id: str | None = None,
        function: str | None = None,
        check_point: str | None = None,
    ) -> SheetRowDetail:
        """根据行主键、物理行号或字段检索条件精确定位并提取整行全部列字段数据。

        支持：
        1. 行主键索引秒级直达（自动适配「主键/编码/用例编号/ID」等列）；
        2. 物理行号 (row_index, 1-based) 坐标直达；
        3. 一次性返回整行全部列字段键值对字典（data 字段）。

        Args:
            sheet_name: 子表名称或 sheet_id（如 '预警管理'、'puwuj9'，缺省时自动使用当前活跃子表）
            row_id: 行主键或唯一标识（如 'APS_YJGL_0001'、'ORD_20260901'，亦可用 case_id 传入）
            row_index: 表格 1-based 物理行号（如第 2 行输入 2；已知行号时可直接提取）
            search_column: 辅助搜索列名称
            search_value: 辅助搜索列匹配值
            case_id: 兼容历史用例编号参数
            function: 兼容历史功能搜索参数
            check_point: 兼容历史验证点搜索参数
        """
        try:
            res = tencent_sheet_manager.get_row(
                sheet_name=sheet_name,
                row_id=row_id,
                row_index=row_index,
                case_id=case_id,
                search_column=search_column,
                search_value=search_value,
                function=function,
                check_point=check_point,
            )
            return SheetRowDetail(**res)
        except TencentDocError as e:
            raise ToolError(f"获取行数据失败: {e}") from e
        except Exception as e:
            raise ToolError(f"获取行数据异常: {e}") from e

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "多维度检索表格行数据", "readOnlyHint": True},
    )
    def tencent_sheet_query_rows(
        sheet_name: str | None = None,
        keyword: str | None = None,
        filters: dict[str, str] | None = None,
        id_pattern: str | None = None,
        limit: int = 20,
        offset: int = 0,
        case_id_pattern: str | None = None,
        level: str | None = None,
        module: str | None = None,
        sub_module: str | None = None,
        function: str | None = None,
        check_point: str | None = None,
        result_filter: str | None = None,
    ) -> SheetQueryResult:
        """多维度组合条件检索指定子表中的行数据清单。

        特性：
        - 支持 keyword 全行全文模糊检索；
        - 支持 filters 任意表头字段键值包含过滤（如 {"负责人": "张三", "状态": "已审核"}）；
        - 每条命中结果完整保留全量字段字典 (data 字段) 并透传，单次调用即可获取完整业务数据。

        Args:
            sheet_name: 子表名称或 sheet_id（缺省时自动使用当前活跃子表）
            keyword: 全局搜索关键字（在整行所有列中模糊匹配）
            filters: 自定义字段键值对过滤条件（如 {"分类": "工艺规则", "状态": "启用"}）
            id_pattern: 主键编号包含的关键词（亦可用 case_id_pattern 传入）
            limit: 返回条数上限（默认 20，最大 100）
            offset: 分页偏移量（默认 0）
            case_id_pattern: 兼容历史用例编号搜索参数
            level: 兼容级别过滤
            module: 兼容一级模块过滤
            sub_module: 兼容二级模块过滤
            function: 兼容功能过滤
            check_point: 兼容验证点过滤
            result_filter: 兼容执行结果过滤
        """
        try:
            limit = min(max(limit, 1), 100)
            items = tencent_sheet_manager.query_rows(
                sheet_name=sheet_name,
                keyword=keyword,
                filters=filters,
                id_pattern=id_pattern or case_id_pattern,
                limit=limit,
                offset=offset,
                level=level,
                module=module,
                sub_module=sub_module,
                function=function,
                check_point=check_point,
                result_filter=result_filter,
            )
            sheet_id, sheet_info = tencent_sheet_manager.resolve_sheet(sheet_name)
            return SheetQueryResult(
                sheet_name=sheet_info.get("sheet_name", sheet_name or ""),
                sheet_id=sheet_id,
                count=len(items),
                items=items,
            )
        except TencentDocError as e:
            raise ToolError(f"检索表格行失败: {e}") from e
        except Exception as e:
            raise ToolError(f"检索表格行异常: {e}") from e

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "更新单行表格数据", "readOnlyHint": False},
    )
    def tencent_sheet_update_row(
        sheet_name: str | None = None,
        row_id: str | None = None,
        row_index: int | None = None,
        fields: dict[str, Any] | None = None,
        case_id: str | None = None,
        result: str | None = None,
        executor: str | None = None,
        execute_time: str | None = None,
        remark: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> SheetUpdateResult:
        """更新指定行的字段单元格（支持按 row_id 或 row_index 定位，更新任意表头列）。

        Args:
            sheet_name: 子表名称或 sheet_id（缺省时自动使用当前活跃子表）
            row_id: 行主键或唯一标识（如 'APS_YJGL_0001'）
            row_index: 物理行号（1-based，如直接指定更新第 3 行）
            fields: 待更新的字段键值对字典（如 {"状态": "已通过", "负责人": "张三", "备注": "复核正常"}）
            case_id: 兼容历史用例编号参数
            result: 兼容测试结果参数
            executor: 兼容执行人参数
            execute_time: 兼容执行时间参数
            remark: 兼容备注参数
            extra_fields: 兼容扩展字段参数
        """
        try:
            res = tencent_sheet_manager.update_row(
                sheet_name=sheet_name,
                row_id=row_id,
                row_index=row_index,
                fields=fields,
                case_id=case_id,
                result=result,
                executor=executor,
                execute_time=execute_time,
                remark=remark,
                extra_fields=extra_fields,
            )
            return SheetUpdateResult(**res)
        except TencentDocError as e:
            raise ToolError(f"更新表格行失败: {e}") from e
        except Exception as e:
            raise ToolError(f"更新表格行异常: {e}") from e

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "批量更新多行表格数据", "readOnlyHint": False},
    )
    def tencent_sheet_batch_update(
        sheet_name: str | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> SheetBatchUpdateResult:
        """批量更新多行表格数据（防限流核心引擎）。

        通过内存聚合与单次 `sheet.set_range_value` 请求提交全部单元格变更，速度极快且完全规避 HTTP 429。
        每项更新支持定位方式：row_id / case_id（按主键列匹配）或 row_index / _row_index（按物理行号直达）；
        每项更新支持传入 fields 字典或扁平键值对。

        Args:
            sheet_name: 子表名称或 sheet_id（缺省时自动使用当前活跃子表）
            updates: 待更新的行列表，每个字典项支持：
                     - row_id / case_id: 行主键标识
                     - row_index / _row_index: 物理行号 (1-based)
                     - fields: 待更新的列名字典（如 {"状态": "完成", "备注": "已核验"}）
                     - 或直接将列名作为字典键（如 {"状态": "通过", "执行人": "李四"}）
        """
        try:
            res = tencent_sheet_manager.batch_update_rows(
                sheet_name=sheet_name,
                updates=updates or [],
            )
            return SheetBatchUpdateResult(**res)
        except TencentDocError as e:
            raise ToolError(f"批量更新表格数据失败: {e}") from e
        except Exception as e:
            raise ToolError(f"批量更新表格数据异常: {e}") from e

    @mcp.tool(
        tags={"tencent_sheet", "tencent_docs"},
        annotations={"title": "读取表格单元格切片", "readOnlyHint": True},
    )
    def tencent_sheet_read_cells(
        sheet_name: str | None = None,
        start_row: int = 0,
        end_row: int = 20,
        start_col: int = 0,
        end_col: int = 20,
    ) -> dict[str, Any]:
        """读取指定子表指定区域的单元格文本数据（带自动网格边界保护，防止触发 60871 错误）。

        Args:
            sheet_name: 子表名称或 sheet_id（缺省时自动使用当前活跃子表）
            start_row: 起始行号（0-based，默认 0）
            end_row: 结束行号（0-based，默认 20，自动钳制不超过子表总行数）
            start_col: 起始列号（0-based，默认 0）
            end_col: 结束列号（0-based，默认 20，自动钳制不超过子表总列数）
        """
        try:
            sheet_id, sheet_info = tencent_sheet_manager.resolve_sheet(sheet_name)
            max_r = max(0, (sheet_info.get("row_count") or 1) - 1)
            max_c = max(0, (sheet_info.get("col_count") or 1) - 1)

            safe_start_row = max(0, min(start_row, max_r))
            safe_end_row = max(safe_start_row, min(end_row, max_r))
            safe_start_col = max(0, min(start_col, max_c))
            safe_end_col = max(safe_start_col, min(end_col, max_c))

            csv_text = tencent_sheet_manager.client.get_cell_data(
                file_id=tencent_sheet_manager.effective_file_id,
                sheet_id=sheet_id,
                start_row=safe_start_row,
                end_row=safe_end_row,
                start_col=safe_start_col,
                end_col=safe_end_col,
                return_csv=True,
            )
            rows = list(csv.reader(io.StringIO(csv_text)))
            return {
                "ok": True,
                "sheet_name": sheet_info.get("sheet_name", sheet_name or ""),
                "sheet_id": sheet_id,
                "range": {
                    "start_row": safe_start_row,
                    "end_row": safe_end_row,
                    "start_col": safe_start_col,
                    "end_col": safe_end_col,
                },
                "row_count": len(rows),
                "csv_data": csv_text,
                "rows": rows,
            }
        except TencentDocError as e:
            raise ToolError(f"读取单元格失败: {e}") from e
        except Exception as e:
            raise ToolError(f"读取单元格发生异常: {e}") from e

    # =======================================================================
    # 历史保留工具集：testcase_* 兼容别名（确保旧脚本与既有测试 100% 正常运行）
    # =======================================================================

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "连接腾讯文档用例表格(兼容别名)", "readOnlyHint": False},
    )
    def testcase_connect(
        url_or_file_id: str | None = None,
        token: str | None = None,
    ) -> TestCaseConnectResult:
        """连接并绑定腾讯文档在线测试用例表格（等价于 tencent_sheet_connect）。"""
        return tencent_sheet_connect(url_or_file_id=url_or_file_id, token=token)

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "列出用例子表清单(兼容别名)", "readOnlyHint": True},
    )
    def testcase_list_sheets(
        url_or_file_id: str | None = None,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出用例子表清单（等价于 tencent_sheet_list_sheets）。"""
        return tencent_sheet_list_sheets(url_or_file_id=url_or_file_id, token=token)

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "获取单条测试用例详情(兼容别名)", "readOnlyHint": True},
    )
    def testcase_get(
        sheet_name: str | None = None,
        case_id: str | None = None,
        row_index: int | None = None,
        function: str | None = None,
        check_point: str | None = None,
    ) -> TestCaseDetail:
        """获取单条测试用例详情（等价于 tencent_sheet_get_row）。"""
        return tencent_sheet_get_row(
            sheet_name=sheet_name,
            case_id=case_id,
            row_index=row_index,
            function=function,
            check_point=check_point,
        )

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "多维度检索测试用例(兼容别名)", "readOnlyHint": True},
    )
    def testcase_query(
        sheet_name: str | None = None,
        case_id_pattern: str | None = None,
        level: str | None = None,
        module: str | None = None,
        sub_module: str | None = None,
        function: str | None = None,
        check_point: str | None = None,
        result_filter: str | None = None,
        keyword: str | None = None,
        filters: dict[str, str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TestCaseQueryResult:
        """多维度检索测试用例（等价于 tencent_sheet_query_rows）。"""
        return tencent_sheet_query_rows(
            sheet_name=sheet_name,
            keyword=keyword,
            filters=filters,
            id_pattern=case_id_pattern,
            limit=limit,
            offset=offset,
            level=level,
            module=module,
            sub_module=sub_module,
            function=function,
            check_point=check_point,
            result_filter=result_filter,
        )

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "单条用例测试结果回写(兼容别名)", "readOnlyHint": False},
    )
    def testcase_update_result(
        sheet_name: str | None = None,
        case_id: str = "",
        result: str = "",
        executor: str | None = None,
        execute_time: str | None = None,
        remark: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> TestCaseUpdateResult:
        """单条用例测试结果回写（等价于 tencent_sheet_update_row）。"""
        return tencent_sheet_update_row(
            sheet_name=sheet_name,
            case_id=case_id,
            result=result,
            executor=executor,
            execute_time=execute_time,
            remark=remark,
            extra_fields=extra_fields,
        )

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "批量回写测试结果(兼容别名)", "readOnlyHint": False},
    )
    def testcase_batch_update_results(
        sheet_name: str | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> TestCaseBatchUpdateResult:
        """批量回写多条测试用例结果（等价于 tencent_sheet_batch_update）。"""
        return tencent_sheet_batch_update(
            sheet_name=sheet_name,
            updates=updates,
        )

    @mcp.tool(
        tags={"testcase", "tencent_docs", "compatibility"},
        annotations={"title": "读取表格原始单元格数据(兼容别名)", "readOnlyHint": True},
    )
    def testcase_read_cells(
        sheet_name: str | None = None,
        start_row: int = 0,
        end_row: int = 20,
        start_col: int = 0,
        end_col: int = 20,
    ) -> dict[str, Any]:
        """读取指定子表单元格文本数据（等价于 tencent_sheet_read_cells）。"""
        return tencent_sheet_read_cells(
            sheet_name=sheet_name,
            start_row=start_row,
            end_row=end_row,
            start_col=start_col,
            end_col=end_col,
        )

    # -----------------------------------------------------------------------
    # 历史保留工具：update_test_case_result（保持既有测试与旧调用方 100% 契约兼容）
    # -----------------------------------------------------------------------

    @mcp.tool()
    async def update_test_case_result(
        file_id: str,
        sheet_id: str,
        case_id: str,
        test_result: str,
        executor: str,
        execution_date: str | None = None,
    ) -> dict[str, Any]:
        """在腾讯文档在线表格中，根据用例编号定位行，单次调用更新测试结果、执行人与执行时间。

        Args:
            file_id: 腾讯文档表格文件 ID，从该文档分享链接里取得
            sheet_id: 目标子表（工作表）ID；只在该子表内查找与写入
            case_id: 用例编号原文，在该子表用例编号列内精确匹配定位行，找不到直接返回 error
            test_result: 写入「测试结果」列的文本，常用 通过/不通过/待定，不限于这几个值
            executor: 写入「执行人」列的文本
            execution_date: 写入「执行时间」列的文本；省略时取当天，格式 YYYY/M/D，精确到日
        """
        clean_fid, _ = parse_file_id(file_id)
        file_id = clean_fid or file_id
        token = resolve_tencent_docs_token()
        if not execution_date:
            now = datetime.now()
            execution_date = f"{now.year}/{now.month}/{now.day}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. 获取表头以动态解析字段列
            async def get_header() -> list[str]:
                data = await _call_mcp_tool(
                    client,
                    "sheet.get_cell_data",
                    {
                        "file_id": file_id,
                        "sheet_id": sheet_id,
                        "start_row": 0,
                        "end_row": 0,
                        "start_col": 0,
                        "end_col": 30,
                        "return_csv": True,
                    },
                    token,
                )
                csv_data = data.get("csv_data", "")
                reader = csv.reader(io.StringIO(csv_data))
                return next(reader, [])

            async def get_case_column() -> list[str]:
                data = await _call_mcp_tool(
                    client,
                    "sheet.get_cell_data",
                    {
                        "file_id": file_id,
                        "sheet_id": sheet_id,
                        "start_row": 0,
                        "end_row": 5000,
                        "start_col": 0,
                        "end_col": 0,
                        "return_csv": True,
                    },
                    token,
                )
                csv_data = data.get("csv_data", "")
                reader = csv.reader(io.StringIO(csv_data))
                return [row[0].strip() if row else "" for row in reader]

            try:
                headers, case_rows = await asyncio.gather(get_header(), get_case_column())
            except Exception as e:
                err_str = str(e)
                if "60871" in err_str or "grid range" in err_str:
                    try:
                        meta = await _call_mcp_tool(
                            client,
                            "sheet.get_sheet_info",
                            {"file_id": file_id},
                            token,
                        )
                        s_list = meta if isinstance(meta, list) else meta.get("sheets", [])
                        row_cnt = 500
                        for s in s_list:
                            if s.get("sheet_id") == sheet_id:
                                row_cnt = s.get("row_count") or 500
                                break
                        safe_end = max(0, row_cnt - 1)
                        data = await _call_mcp_tool(
                            client,
                            "sheet.get_cell_data",
                            {
                                "file_id": file_id,
                                "sheet_id": sheet_id,
                                "start_row": 0,
                                "end_row": safe_end,
                                "start_col": 0,
                                "end_col": 0,
                                "return_csv": True,
                            },
                            token,
                        )
                        csv_data = data.get("csv_data", "")
                        reader = csv.reader(io.StringIO(csv_data))
                        case_rows = [row[0].strip() if row else "" for row in reader]
                        headers = await get_header()
                    except Exception as fallback_err:
                        logger.exception("Failed fallback reading sheet")
                        return {
                            "status": "error",
                            "file_id": file_id,
                            "sheet_id": sheet_id,
                            "case_id": case_id,
                            "message": f"读取表格数据失败: {fallback_err}",
                        }
                else:
                    logger.exception("Failed to read sheet metadata from Tencent Docs")
                    return {
                        "status": "error",
                        "file_id": file_id,
                        "sheet_id": sheet_id,
                        "case_id": case_id,
                        "message": f"读取表格数据失败: {e}",
                    }

            # 2. 动态解析字段所在列索引（带基线回退机制）
            case_col = 0
            result_col = 10
            executor_col = 11
            time_col = 12

            for idx, col_name in enumerate(headers):
                name = col_name.strip()
                if "用例编号" in name or "用例ID" in name:
                    case_col = idx
                elif "测试结果" in name or "执行结果" in name or "结果" in name or "状态" in name:
                    result_col = idx
                elif "执行人" in name or "测试人" in name or "负责人" in name:
                    executor_col = idx
                elif "执行时间" in name or "测试时间" in name or "日期" in name:
                    time_col = idx

            # 3. 定位目标用例所在行
            clean_case_id = case_id.strip()
            if clean_case_id not in case_rows:
                return {
                    "status": "error",
                    "file_id": file_id,
                    "sheet_id": sheet_id,
                    "case_id": case_id,
                    "message": f"未在子表「{sheet_id}」的首列中找到用例编号「{case_id}」",
                }

            target_row = case_rows.index(clean_case_id)

            # 4. 批量更新单元格数值
            values = [
                {
                    "row": target_row,
                    "col": result_col,
                    "value_type": "STRING",
                    "string_value": test_result,
                },
                {
                    "row": target_row,
                    "col": executor_col,
                    "value_type": "STRING",
                    "string_value": executor,
                },
                {
                    "row": target_row,
                    "col": time_col,
                    "value_type": "STRING",
                    "string_value": execution_date,
                },
            ]

            try:
                update_res = await _call_mcp_tool(
                    client,
                    "sheet.set_range_value",
                    {
                        "file_id": file_id,
                        "sheet_id": sheet_id,
                        "values": values,
                    },
                    token,
                )
                if update_res.get("error"):
                    return {
                        "status": "error",
                        "file_id": file_id,
                        "sheet_id": sheet_id,
                        "case_id": case_id,
                        "message": f"写入单元格失败: {update_res['error']}",
                    }
            except Exception as e:
                logger.exception("Failed to update cells in Tencent Docs")
                return {
                    "status": "error",
                    "file_id": file_id,
                    "sheet_id": sheet_id,
                    "case_id": case_id,
                    "message": f"提交更新失败: {e}",
                }

            return {
                "status": "ok",
                "file_id": file_id,
                "sheet_id": sheet_id,
                "case_id": case_id,
                "row_index": target_row,
                "row_number": target_row + 1,
                "updated_fields": {
                    "测试结果": test_result,
                    "执行人": executor,
                    "执行时间": execution_date,
                },
                "columns_resolved": {
                    "用例编号": case_col,
                    "测试结果": result_col,
                    "执行人": executor_col,
                    "执行时间": time_col,
                },
                "message": (
                    f"用例「{case_id}」(第 {target_row + 1} 行) 更新成功："
                    f"测试结果={test_result}，执行人={executor}，执行时间={execution_date}"
                ),
            }

    return mcp
