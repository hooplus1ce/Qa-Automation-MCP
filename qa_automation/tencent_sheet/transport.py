"""腾讯文档 MCP 的 JSON-RPC 传输层:令牌解析、pacing、429 退避与错误语义化。

所有对 docs.qq.com/openapi/mcp 的调用都必须走 :class:`TencentSheetClient`,
禁止在其他模块另起 HTTP 路径(pacing/重试/错误口径会分叉)。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from ..config import TENCENT_DOCS_MCP_URL, resolve_tencent_docs_token

logger = logging.getLogger("qa_automation.tencent_sheet")

DEFAULT_MCP_URL = "https://docs.qq.com/openapi/mcp"


class TencentDocError(Exception):
    """腾讯文档接口调用异常。"""

    def __init__(self, message: str, code: int | None = None, trace_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.trace_id = trace_id


# ---------------------------------------------------------------------------
# 客户端通讯
# ---------------------------------------------------------------------------


def _resolve_token(token: str | None = None) -> str:
    """解析腾讯文档访问令牌:显式传参优先,其次统一走 config.resolve_tencent_docs_token。

    env 变量与本项目 .mcp.json 的回退链只在 config.py 一处维护,不在这里重复。
    未配置时返回空串而非抛错——client 在包导入期就会构造(全局单例),
    令牌缺失的可操作报错延迟到 call_tool 时给出。
    """
    if token and token.strip():
        return token.strip()
    try:
        return resolve_tencent_docs_token().strip()
    except Exception:
        return ""


class TencentSheetClient:
    """腾讯文档 MCP / OpenAPI 原生客户端（JSON-RPC 2.0 over HTTP POST）。

    内置：
    1. 频率控制与请求间隔防抖（Pacing）；
    2. HTTP 429 限流自适应退避重试（Exponential Backoff）；
    3. JSON 与非 JSON（CSV/纯文本）响应解析容错；
    4. 腾讯文档专用业务错误代码智能语义化识别。
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        min_interval: float = 0.5,
        max_retries: int = 4,
    ):
        self.token = _resolve_token(token)
        self.base_url = base_url or TENCENT_DOCS_MCP_URL or DEFAULT_MCP_URL
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last_call_time = 0.0
        self._req_id = 0

    def set_token(self, token: str) -> None:
        self.token = token.strip()

    def _wait_pacing(self) -> None:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """通过 JSON-RPC 2.0 调用腾讯文档 MCP 工具。"""
        if not self.token:
            self.token = _resolve_token()
        if not self.token:
            raise TencentDocError(
                "未配置腾讯文档 MCP 访问令牌，请配置环境变量 TENCENT_DOCS_MCP_TOKEN 或在 connect 时传入 token"
            )

        self._req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            self._wait_pacing()
            req = urllib.request.Request(
                self.base_url,
                data=data_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self.token,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_bytes = resp.read()
                    self._last_call_time = time.monotonic()
                    raw_text = resp_bytes.decode("utf-8", errors="replace")

                res_json = json.loads(raw_text, strict=False)
                if "error" in res_json and res_json["error"]:
                    err_info = res_json["error"]
                    msg = (
                        err_info.get("message", str(err_info))
                        if isinstance(err_info, dict)
                        else str(err_info)
                    )
                    code = err_info.get("code") if isinstance(err_info, dict) else None
                    if "60871" in msg or code == 60871:
                        raise TencentDocError(
                            f"腾讯文档表格区域超出实际行列范围 (code: 60871, invalid input grid range): {msg}",
                            code=60871,
                        )
                    raise TencentDocError(f"RPC错误: {msg}", code=code)

                result = res_json.get("result", {})
                # 1. 优先解析 structuredContent
                if "structuredContent" in result and isinstance(result["structuredContent"], dict):
                    struct = result["structuredContent"]
                    if struct.get("error"):
                        err_msg = struct["error"]
                        raise TencentDocError(
                            f"工具返回错误: {err_msg}",
                            trace_id=struct.get("trace_id"),
                        )
                    return struct

                # 2. 其次解析 content 列表
                content_list = result.get("content", [])
                if content_list and isinstance(content_list, list):
                    first_text = content_list[0].get("text", "")
                    if first_text:
                        try:
                            parsed = json.loads(first_text, strict=False)
                            if isinstance(parsed, dict):
                                if parsed.get("error"):
                                    raise TencentDocError(
                                        f"工具返回错误: {parsed['error']}",
                                        trace_id=parsed.get("trace_id"),
                                    )
                                return parsed
                            if isinstance(parsed, list):
                                return {"items": parsed}
                        except json.JSONDecodeError:
                            return {"raw_text": first_text, "csv_data": first_text}

                return result

            except urllib.error.HTTPError as e:
                self._last_call_time = time.monotonic()
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After")
                    try:
                        backoff = float(retry_after) if retry_after else 1.5 * (attempt + 1)
                    except ValueError:
                        backoff = 1.5 * (attempt + 1)
                    # 服务端可能给出任意大的 Retry-After;无上限的 sleep 会把工作
                    # 线程挂住任意久(客户端早已超时,服务端仍在跑)。钳到 10s,
                    # 剩余预算交给后续重试轮次。
                    backoff = min(backoff, 10.0)
                    logger.warning(
                        "触发腾讯文档 429 限流，等待 %.1f 秒后重试 (attempt %d/%d)...",
                        backoff,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(backoff)
                    last_err = TencentDocError(
                        "腾讯文档接口限流 (HTTP 429)，已耗尽重试次数", code=429
                    )
                    continue
                err_body = e.read().decode("utf-8", errors="replace")[:400]
                raise TencentDocError(f"HTTP错误 {e.code}: {err_body}", code=e.code) from e
            except urllib.error.URLError as e:
                self._last_call_time = time.monotonic()
                logger.warning(
                    "网络通讯异常: %s，等待重试 (attempt %d/%d)...",
                    e,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(1.0 * (attempt + 1))
                last_err = e
            except json.JSONDecodeError as e:
                raise TencentDocError(f"解析腾讯文档响应 JSON 失败: {e}") from e

        if last_err:
            raise last_err
        raise TencentDocError("调用腾讯文档接口异常未知")

    def get_sheet_info(self, file_id: str) -> dict[str, Any]:
        """获取表格全部子表结构信息（调用官方 sheet.get_sheet_info）。"""
        res = self.call_tool("sheet.get_sheet_info", {"file_id": file_id})
        if isinstance(res, list):
            return {"sheets": res}
        return res

    def get_cell_data(
        self,
        file_id: str,
        sheet_id: str,
        start_row: int,
        end_row: int,
        start_col: int,
        end_col: int,
        return_csv: bool = True,
    ) -> str:
        """获取指定区域单元格数据（调用官方 sheet.get_cell_data），返回 CSV 字符串。"""
        res = self.call_tool(
            "sheet.get_cell_data",
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "start_row": start_row,
                "end_row": end_row,
                "start_col": start_col,
                "end_col": end_col,
                "return_csv": return_csv,
            },
        )
        return res.get("csv_data", "")

    def set_range_value(
        self, file_id: str, sheet_id: str, values: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """批量更新单元格数值（原子操作，调用官方 sheet.set_range_value）。"""
        return self.call_tool(
            "sheet.set_range_value",
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "values": values,
            },
        )

    def set_cell_value(
        self,
        file_id: str,
        sheet_id: str,
        row: int,
        col: int,
        value_type: str = "STRING",
        string_value: str = "",
        number_value: float | None = None,
        bool_value: bool | None = None,
    ) -> dict[str, Any]:
        """更新单个单元格数值（调用官方 sheet.set_cell_value）。"""
        payload: dict[str, Any] = {
            "file_id": file_id,
            "sheet_id": sheet_id,
            "row": row,
            "col": col,
            "value_type": value_type,
        }
        if string_value is not None:
            payload["string_value"] = string_value
        if number_value is not None:
            payload["number_value"] = number_value
        if bool_value is not None:
            payload["bool_value"] = bool_value
        return self.call_tool("sheet.set_cell_value", payload)

    def clear_range_cells(
        self,
        file_id: str,
        sheet_id: str,
        start_row: int,
        end_row: int,
        start_col: int,
        end_col: int,
    ) -> dict[str, Any]:
        """清空单元格内容（调用官方 sheet.clear_range_cells）。"""
        return self.call_tool(
            "sheet.clear_range_cells",
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "start_row": start_row,
                "end_row": end_row,
                "start_col": start_col,
                "end_col": end_col,
            },
        )

    def delete_dimension(
        self,
        file_id: str,
        sheet_id: str,
        dimension_type: str = "row",
        index: int = 0,
        count: int = 1,
    ) -> dict[str, Any]:
        """物理删除指定位置的行或列（调用官方 sheet.delete_dimension）。

        与 ``clear_range_cells`` 的本质区别：本方法**真正移除整行/整列**，
        后续行会整体上移，子表总行数随之减少；后者只清空内容、行结构不变。

        Args:
            file_id: 在线表格唯一标识
            sheet_id: 子表 ID
            dimension_type: "row" 或 "col"
            index: 起始索引（0-based，含表头行，即 0 为表头）
            count: 删除数量，默认 1
        """
        return self.call_tool(
            "sheet.delete_dimension",
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "dimension_type": dimension_type,
                "index": int(index),
                "count": int(count),
            },
        )

    def insert_dimension(
        self,
        file_id: str,
        sheet_id: str,
        dimension_type: str = "row",
        index: int = 0,
        count: int = 1,
        direction: str = "before",
    ) -> dict[str, Any]:
        """在指定位置插入空行或空列（调用官方 sheet.insert_dimension）。

        Args:
            index: 参照索引（0-based，含表头行）
            count: 插入数量，默认 1
            direction: "before"（默认，插在 index 之前）或 "after"
        """
        return self.call_tool(
            "sheet.insert_dimension",
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "dimension_type": dimension_type,
                "index": int(index),
                "count": int(count),
                "direction": direction,
            },
        )

    def query_file_info(self, file_id: str) -> dict[str, Any]:
        """获取文档基础信息（调用官方 manage.query_file_info）。"""
        return self.call_tool("manage.query_file_info", {"file_id": file_id})


# 兼容别名
TencentDocSheetClient = TencentSheetClient
