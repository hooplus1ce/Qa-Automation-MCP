"""Tencent Docs automation tools for fast, single-roundtrip workflow execution."""

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

from ...config import TENCENT_DOCS_MCP_URL, resolve_tencent_docs_token

logger = logging.getLogger(__name__)


async def _call_mcp_tool(
    client: httpx.AsyncClient,
    tool_name: str,
    arguments: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    """通过 JSON-RPC 调用腾讯文档 MCP 接口并解析结果。"""
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
    content = result.get("content", [])
    if not content or not isinstance(content, list):
        return {}

    first_text = content[0].get("text", "")
    if not first_text:
        return {}

    try:
        return json.loads(first_text)
    except Exception:
        return {"raw_text": first_text}


def create_server() -> FastMCP:
    mcp = FastMCP("Tencent Docs Automation")

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

        参数:
            file_id: 表格文档 ID（如 DUUhiWnFvZVdibWZq）
            sheet_id: 目标子表 ID（如 2sj7ou）
            case_id: 用例编号（如 APS_JCPZ_0758）
            test_result: 测试结果（如 通过 / 不通过 / 待定）
            executor: 执行人（如 Hoo）
            execution_date: 执行时间，默认为当前日期（格式 YYYY/M/D，精确到日）
        """
        token = resolve_tencent_docs_token()
        if not execution_date:
            now = datetime.now()
            execution_date = f"{now.year}/{now.month}/{now.day}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. 并发获取表头与首列用例编号数据，减少往返耗时
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
                if "用例编号" in name:
                    case_col = idx
                elif "测试结果" in name:
                    result_col = idx
                elif "执行人" in name:
                    executor_col = idx
                elif "执行时间" in name:
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
