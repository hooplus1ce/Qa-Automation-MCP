"""腾讯文档在线表格通用操作与管理模块。

提供与腾讯文档 OpenAPI（JSON-RPC 2.0 / MCP 契约）的原生通讯、
子表元数据缓存、多维度行记录定位与检索、网格切片读取、
以及防限流的单行/批量单元格回写引擎。

支持普通在线表格（sheet）与多维智能表格（smartsheet），
适用于测试用例管理、业务数据录入、多表同步及通用表格自动化操作。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .config import TENCENT_DOCS_MCP_URL, resolve_tencent_docs_token

logger = logging.getLogger("qa_automation.tencent_sheet")

DEFAULT_MCP_URL = "https://docs.qq.com/openapi/mcp"


class TencentDocError(Exception):
    """腾讯文档接口调用异常。"""

    def __init__(self, message: str, code: int | None = None, trace_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.trace_id = trace_id


# ---------------------------------------------------------------------------
# 字段规范化辅助函数
# ---------------------------------------------------------------------------


def normalize_header(text: str) -> str:
    """去除表头空格、常见中英文标点、下划线并转小写，便于弹性模糊匹配。"""
    if not text:
        return ""
    return re.sub(r"[\s\(\)（）\[\]【】_—\-:：#]+", "", str(text).strip().lower())


# ---------------------------------------------------------------------------
# Pydantic 结果数据模型
# ---------------------------------------------------------------------------


class SheetConnectResult(BaseModel):
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


class SheetRowDetail(BaseModel):
    """单行记录详情。"""

    sheet_name: str
    sheet_id: str | None = None
    row_index: int = Field(description="1-based 物理行号（对应 Excel 界面行号）")
    row_id: str = Field(description="主键或唯一标识，如用例编号、订单编号、员工号等")
    case_id: str = Field(default="", description="兼容历史用例编号字段")
    data: dict[str, Any] = Field(default_factory=dict, description="整行所有字段键值对")


class SheetQueryResult(BaseModel):
    """表格多维度查询结果。"""

    sheet_name: str
    sheet_id: str | None = None
    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)


class SheetUpdateResult(BaseModel):
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


class SheetBatchUpdateResult(BaseModel):
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


# 别名兼容
TestCaseConnectResult = SheetConnectResult
TestCaseDetail = SheetRowDetail
TestCaseQueryResult = SheetQueryResult
TestCaseUpdateResult = SheetUpdateResult
TestCaseBatchUpdateResult = SheetBatchUpdateResult


# ---------------------------------------------------------------------------
# 客户端通讯
# ---------------------------------------------------------------------------


def _resolve_token(token: str | None = None) -> str:
    """解析腾讯文档访问令牌。"""
    if token and token.strip():
        return token.strip()
    try:
        return resolve_tencent_docs_token()
    except Exception:
        return (
            os.getenv("TENCENT_DOCS_MCP_TOKEN")
            or os.getenv("TENCENT_DOCS_TOKEN")
            or os.getenv("TENCENT_API_KEY")
            or ""
        )


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
                    backoff = float(retry_after) if retry_after else (1.5 * (attempt + 1))
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

    def query_file_info(self, file_id: str) -> dict[str, Any]:
        """获取文档基础信息（调用官方 manage.query_file_info）。"""
        return self.call_tool("manage.query_file_info", {"file_id": file_id})


# 兼容别名
TencentDocSheetClient = TencentSheetClient


def parse_file_id(url_or_id: str) -> tuple[str, str | None]:
    """从文档 URL 或 file_id 中提取规范的 (file_id, tab_sheet_id)。"""
    raw = str(url_or_id or "").strip().strip("'\"")
    if not raw:
        return "", None

    if not raw.startswith("http://") and not raw.startswith("https://"):
        if "?" in raw or "#" in raw:
            raw = f"https://docs.qq.com/sheet/{raw}"
        else:
            return raw, None

    parsed = urllib.parse.urlparse(raw)
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        return "", None

    action_words = {"edit", "view", "preview", "sheet", "smartsheet", "doc", "form", "table"}
    if path_parts[-1].lower() in action_words and len(path_parts) > 1:
        candidate_id = path_parts[-2]
    else:
        candidate_id = path_parts[-1]

    file_id = candidate_id.split(".")[0]

    query_params = urllib.parse.parse_qs(parsed.query)
    tab = None
    for key in ("tab", "sub_id", "subid", "subId", "sheet_id", "sheetId", "padid"):
        if key in query_params and query_params[key]:
            tab = query_params[key][0]
            break

    if not tab and parsed.fragment:
        frag_params = urllib.parse.parse_qs(parsed.fragment)
        for key in ("tab", "sub_id", "subid", "subId", "sheet_id", "sheetId"):
            if key in frag_params and frag_params[key]:
                tab = frag_params[key][0]
                break

    return file_id, tab


class TencentSheetManager:
    """腾讯文档表格管理与数据读写门面。

    通用化特性：
    1. 动态自适应表头：支持业务主数据表、16字段功能用例、17列接口用例及任意定制列；
    2. 多别名弹性列识别：主键编号、分类、状态、负责人、日期、备注等均支持广泛别名；
    3. 子表按名称、按 sheet_id、或缺省当前活跃 tab 自动解析；
    4. 严格网格边界保护：防止超出实际行列范围导致官方 60871 错误；
    5. 全字段查询与检索：保留每行全量原始字段，支持全文检索与任意字段组合过滤；
    6. 防限流批量结果回写：一次性聚合提交所有待更新单元格，支持回写任意扩展列。
    """

    __test__ = False

    def __init__(self, client: TencentSheetClient | None = None):
        self.client = client or TencentSheetClient()
        self.active_file_id: str | None = None
        self.active_canonical_id: str | None = None
        self.active_url: str | None = None
        self.active_title: str | None = None
        self.active_tab_id: str | None = None
        self.active_tab_sheet: str | None = None

        self._sheets_by_name: dict[str, dict[str, Any]] = {}
        self._sheets_by_id: dict[str, dict[str, Any]] = {}
        self._headers_cache: dict[str, list[str]] = {}
        self._id_index_cache: dict[str, dict[str, int]] = {}

        # 常用业务字段别名映射
        self.TARGET_FIELD_ALIASES = {
            "result": [
                "测试结果", "执行结果", "用例结果", "测试结论", "结论", "结果",
                "Test Result", "TestResult", "Result", "Status", "状态", "审批状态",
            ],
            "executor": [
                "执行人", "测试人", "执行人员", "测试人员", "Tester", "Executor",
                "Owner", "责任人", "执行者", "测试者", "操作人", "经办人",
            ],
            "execute_time": [
                "执行时间", "测试时间", "执行日期", "测试日期", "完成时间",
                "Test Time", "TestTime", "Date", "日期", "更新时间", "修改时间",
            ],
            "remark": [
                "备注", "说明", "缺陷说明", "Bug单号", "Bug ID", "缺陷ID",
                "Remark", "Notes", "Comment", "Note", "问题描述", "详情",
            ],
        }

        # 检索过滤别名映射
        self.FILTER_FIELD_ALIASES = {
            "id": [
                "用例编号", "用例ID", "用例序号", "用例编码", "用例代码",
                "测试用例编号", "编号", "序号", "编码", "代码", "主键",
                "Case ID", "CaseID", "Case_ID", "Case_No", "ID", "Code", "No",
            ],
            "level": ["级别", "用例级别", "优先级", "重要程度", "Level", "Priority", "Pri"],
            "module": ["一级模块", "所属模块", "系统模块", "模块", "功能模块", "Module", "分类"],
            "sub_module": ["二级模块", "子模块", "二级功能", "子功能", "SubModule", "Sub-Module"],
            "function": ["功能", "功能点", "功能名称", "业务功能", "Function", "Feature"],
            "check_point": ["验证点", "测试点", "检查点", "验证内容", "CheckPoint", "Check Point"],
            "result": [
                "测试结果", "执行结果", "用例结果", "测试结论", "结论", "结果",
                "Test Result", "TestResult", "Result", "Status", "状态",
            ],
        }

    def connect(
        self, url_or_file_id: str | None = None, token: str | None = None
    ) -> dict[str, Any]:
        """连接并绑定腾讯文档在线表格。"""
        if token:
            self.client.set_token(token)

        target = (
            url_or_file_id
            or os.getenv("TENCENT_DOCS_DEFAULT_URL")
            or os.getenv("TENCENT_DOCS_FILE_ID")
        )
        if not target:
            raise TencentDocError(
                "未提供表格链接或 file_id，且未配置环境变量 TENCENT_DOCS_DEFAULT_URL"
            )

        file_id, tab_id = parse_file_id(target)
        self.active_file_id = file_id
        self.active_tab_id = tab_id
        if target.startswith("http"):
            self.active_url = target

        try:
            info = self.client.query_file_info(file_id)
            self.active_canonical_id = info.get("file_id") or file_id
            self.active_title = info.get("title") or "未命名表格"
            if not self.active_url and info.get("url"):
                self.active_url = info.get("url")
        except Exception as e:
            logger.warning("获取文档基础信息略过: %s", e)
            self.active_canonical_id = file_id
            self.active_title = "在线表格"

        sheet_meta = self.client.get_sheet_info(self.effective_file_id)
        raw_sheets = sheet_meta.get("sheets", []) if isinstance(sheet_meta, dict) else sheet_meta
        if not isinstance(raw_sheets, list):
            raw_sheets = []

        self._sheets_by_name.clear()
        self._sheets_by_id.clear()
        self._headers_cache.clear()
        self._id_index_cache.clear()

        sheets_summary = []
        active_sheet_name = None

        for s in raw_sheets:
            s_name = s.get("sheet_name", "").strip()
            s_id = s.get("sheet_id", "")
            data = {
                "sheet_id": s_id,
                "sheet_name": s_name,
                "row_count": s.get("row_count", 0),
                "col_count": s.get("col_count", 0),
                "hidden": s.get("hidden", False),
                "sheet_type": s.get("sheet_type", "worksheet"),
            }
            self._sheets_by_name[s_name] = data
            self._sheets_by_id[s_id] = data

            if self.active_tab_id and s_id == self.active_tab_id:
                active_sheet_name = s_name

            if not data["hidden"]:
                sheets_summary.append({
                    "sheet_id": s_id,
                    "sheet_name": s_name,
                    "row_count": data["row_count"],
                    "col_count": data["col_count"],
                })

        self.active_tab_sheet = active_sheet_name or (
            sheets_summary[0]["sheet_name"] if sheets_summary else None
        )
        if not self.active_tab_id and sheets_summary:
            self.active_tab_id = sheets_summary[0]["sheet_id"]

        return {
            "ok": True,
            "file_id": self.effective_file_id,
            "title": self.active_title,
            "url": self.active_url,
            "active_tab_sheet": self.active_tab_sheet,
            "active_tab_id": self.active_tab_id,
            "total_sheets": len(raw_sheets),
            "visible_sheets_count": len(sheets_summary),
            "sheets": sheets_summary,
            "message": f"成功连接表格 [{self.active_title}]，包含 {len(sheets_summary)} 个可用子表",
        }

    @property
    def effective_file_id(self) -> str:
        fid = self.active_canonical_id or self.active_file_id
        if not fid:
            raise TencentDocError("尚未连接文档，请先调用 tencent_sheet_connect 连接表格")
        return fid

    def resolve_sheet(self, sheet_name: str | None = None) -> tuple[str, dict[str, Any]]:
        """根据名称、简称或 sheet_id 解析目标子表 (sheet_id, sheet_info)。"""
        if not self._sheets_by_name:
            self.connect()

        # 1. 缺省子表：使用当前活跃子表
        if not sheet_name or not str(sheet_name).strip():
            if self.active_tab_id and self.active_tab_id in self._sheets_by_id:
                return self.active_tab_id, self._sheets_by_id[self.active_tab_id]
            if self.active_tab_sheet and self.active_tab_sheet in self._sheets_by_name:
                return (
                    self._sheets_by_name[self.active_tab_sheet]["sheet_id"],
                    self._sheets_by_name[self.active_tab_sheet],
                )
            visible = [s for s in self._sheets_by_name.values() if not s.get("hidden")]
            if visible:
                return visible[0]["sheet_id"], visible[0]
            raise TencentDocError("文档中无可用可见子表")

        clean_name = str(sheet_name).strip()

        # 2. 精确匹配 sheet_id
        if clean_name in self._sheets_by_id:
            info = self._sheets_by_id[clean_name]
            return info["sheet_id"], info

        # 3. 精确匹配 sheet_name
        if clean_name in self._sheets_by_name:
            info = self._sheets_by_name[clean_name]
            return info["sheet_id"], info

        # 4. 大小写不敏感匹配
        clean_lower = clean_name.lower()
        for s_id, info in self._sheets_by_id.items():
            if s_id.lower() == clean_lower:
                return s_id, info
        for s_name, info in self._sheets_by_name.items():
            if s_name.lower() == clean_lower:
                return info["sheet_id"], info

        # 5. 模糊子串匹配
        candidates = [
            info
            for name, info in self._sheets_by_name.items()
            if (clean_name in name or name in clean_name or clean_name in info.get("sheet_id", ""))
            and not info.get("hidden")
        ]
        if len(candidates) == 1:
            return candidates[0]["sheet_id"], candidates[0]
        if len(candidates) > 1:
            names = [f"{c['sheet_name']} (id:{c['sheet_id']})" for c in candidates]
            raise TencentDocError(
                f"子表名称 '{sheet_name}' 匹配到多个子表: {names}，请提供精确全名或 sheet_id"
            )

        available = [
            f"{n} (id:{s['sheet_id']})"
            for n, s in self._sheets_by_name.items()
            if not s.get("hidden")
        ]
        raise TencentDocError(
            f"未找到名为 '{sheet_name}' 的子表。可用子表包括: {available[:12]}"
        )

    def get_headers(self, sheet_name: str | None = None) -> list[str]:
        """获取子表表头列名列表。"""
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        if sheet_id in self._headers_cache:
            return self._headers_cache[sheet_id]

        total_cols = sheet_info.get("col_count") or 30
        max_col = max(0, min(total_cols - 1, 60))

        csv_text = self.client.get_cell_data(
            file_id=self.effective_file_id,
            sheet_id=sheet_id,
            start_row=0,
            end_row=0,
            start_col=0,
            end_col=max(max_col, 15),
            return_csv=True,
        )
        rows = list(csv.reader(io.StringIO(csv_text)))
        if not rows or not rows[0]:
            raise TencentDocError(f"无法读取子表 [{sheet_info.get('sheet_name', sheet_id)}] 的表头")

        headers = [h.strip() for h in rows[0]]

        non_empty = [h for h in headers if h]
        row_count = sheet_info.get("row_count") or 0
        if len(non_empty) <= 1 and row_count > 1:
            csv_text_row1 = self.client.get_cell_data(
                file_id=self.effective_file_id,
                sheet_id=sheet_id,
                start_row=1,
                end_row=1,
                start_col=0,
                end_col=max(max_col, 15),
                return_csv=True,
            )
            rows1 = list(csv.reader(io.StringIO(csv_text_row1)))
            if rows1 and rows1[0] and len([c for c in rows1[0] if c.strip()]) > len(non_empty):
                headers = [h.strip() for h in rows1[0]]

        while headers and not headers[-1]:
            headers.pop()

        self._headers_cache[sheet_id] = headers
        return headers

    def _find_column_index(
        self, headers: list[str], aliases: list[str], fallback: int | None = None
    ) -> int | None:
        """弹性匹配表头中的列索引。"""
        for idx, h in enumerate(headers):
            if h in aliases:
                return idx

        norm_aliases = {normalize_header(a) for a in aliases if normalize_header(a)}
        for idx, h in enumerate(headers):
            if normalize_header(h) in norm_aliases:
                return idx

        for idx, h in enumerate(headers):
            norm_h = normalize_header(h)
            for a in aliases:
                norm_a = normalize_header(a)
                if norm_a and (norm_a in norm_h or norm_h in norm_a):
                    return idx

        return fallback

    def build_id_index(
        self,
        sheet_name: str | None = None,
        force: bool = False,
        id_column: str | None = None,
    ) -> dict[str, int]:
        """扫描并缓存唯一标识/主键列的行号索引字典 {row_id: 0_based_row_idx}。"""
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        if not force and sheet_id in self._id_index_cache:
            return self._id_index_cache[sheet_id]

        headers = self.get_headers(sheet_name)
        id_col_idx = 0
        if id_column:
            matched_col = self._find_column_index(headers, [id_column])
            if matched_col is not None:
                id_col_idx = matched_col
        else:
            matched_col = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["id"])
            if matched_col is not None:
                id_col_idx = matched_col

        total_rows = sheet_info.get("row_count", 0)
        if total_rows <= 1:
            self._id_index_cache[sheet_id] = {}
            return {}

        chunk_size = 200
        index_map: dict[str, int] = {}
        header_name = headers[id_col_idx] if id_col_idx < len(headers) else ""

        for r_start in range(1, total_rows, chunk_size):
            r_end = min(r_start + chunk_size - 1, total_rows - 1)
            if r_start > r_end:
                break
            try:
                csv_text = self.client.get_cell_data(
                    file_id=self.effective_file_id,
                    sheet_id=sheet_id,
                    start_row=r_start,
                    end_row=r_end,
                    start_col=id_col_idx,
                    end_col=id_col_idx,
                    return_csv=True,
                )
            except TencentDocError as e:
                if e.code == 60871:
                    logger.warning(
                        "子表 [%s] 扫描主键第 %d-%d 行超出网格，停止后续拉取",
                        sheet_name,
                        r_start,
                        r_end,
                    )
                    break
                raise

            rows = list(csv.reader(io.StringIO(csv_text)))
            if not rows:
                break

            consecutive_empty = 0
            for offset, r in enumerate(rows):
                val = r[0].strip() if r else ""
                if val:
                    consecutive_empty = 0
                    if val != header_name and val not in self.FILTER_FIELD_ALIASES["id"]:
                        index_map[val] = r_start + offset
                else:
                    consecutive_empty += 1

            if consecutive_empty >= 50 and (
                r_start + len(rows) >= total_rows or len(rows) < chunk_size
            ):
                break

        self._id_index_cache[sheet_id] = index_map
        logger.info(
            "已构建子表 [%s] 行主键索引，共收录 %d 行数据",
            sheet_info.get("sheet_name", sheet_id),
            len(index_map),
        )
        return index_map

    def get_row(
        self,
        sheet_name: str | None = None,
        row_id: str | None = None,
        row_index: int | None = None,
        case_id: str | None = None,
        search_column: str | None = None,
        search_value: str | None = None,
        function: str | None = None,
        check_point: str | None = None,
    ) -> dict[str, Any]:
        """根据行主键、物理行号或字段检索定位并获取单行完整数据。"""
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        headers = self.get_headers(sheet_name)
        ncols = len(headers)
        total_rows = sheet_info.get("row_count", 0)

        target_row_idx: int | None = None
        target_id = row_id or case_id

        if row_index is not None:
            if row_index < 1 or (total_rows > 0 and row_index > total_rows):
                raise TencentDocError(
                    f"传入的行号 {row_index} 超出子表有效范围 (1 - {total_rows})"
                )
            target_row_idx = row_index - 1
        elif target_id:
            target_id_clean = str(target_id).strip()
            index_map = self.build_id_index(sheet_name)
            if target_id_clean in index_map:
                target_row_idx = index_map[target_id_clean]
            else:
                index_map = self.build_id_index(sheet_name, force=True)
                target_row_idx = index_map.get(target_id_clean)

            if target_row_idx is None:
                raise TencentDocError(
                    f"在子表 [{sheet_info.get('sheet_name', sheet_id)}] 中未找到行标识 [{target_id}]"
                )
        else:
            # 组合条件检索定位
            q_filters: dict[str, str] = {}
            if search_column and search_value:
                q_filters[search_column] = search_value
            query_results = self.query_rows(
                sheet_name=sheet_name,
                filters=q_filters if q_filters else None,
                function=function,
                check_point=check_point,
                limit=5,
            )
            if not query_results:
                raise TencentDocError(
                    f"在子表 [{sheet_info.get('sheet_name', sheet_id)}] 中未找到匹配的行数据"
                )
            if len(query_results) > 1:
                matched_ids = [q.get("row_id") or q.get("用例编号") for q in query_results]
                raise TencentDocError(
                    f"在子表 [{sheet_info.get('sheet_name', sheet_id)}] 匹配到多行数据 {matched_ids}，"
                    "请指定 row_id 或 row_index 唯一定位"
                )
            target_row_idx = query_results[0]["_row_index_0_based"]

        csv_text = self.client.get_cell_data(
            file_id=self.effective_file_id,
            sheet_id=sheet_id,
            start_row=target_row_idx,
            end_row=target_row_idx,
            start_col=0,
            end_col=ncols - 1,
            return_csv=True,
        )
        rows = list(csv.reader(io.StringIO(csv_text)))
        if not rows or not rows[0]:
            raise TencentDocError(
                f"读取子表 [{sheet_info.get('sheet_name', sheet_id)}] 第 {target_row_idx + 1} 行数据为空"
            )

        row_vals = rows[0]
        data_dict: dict[str, str] = {}
        for idx, h in enumerate(headers):
            data_dict[h] = row_vals[idx] if idx < len(row_vals) else ""

        id_col_idx = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["id"], fallback=0)
        resolved_id = (
            data_dict.get(headers[id_col_idx])
            if (id_col_idx is not None and id_col_idx < len(headers))
            else ""
        )
        final_id = resolved_id or target_id or f"ROW_{target_row_idx + 1}"

        return {
            "sheet_name": sheet_info["sheet_name"],
            "sheet_id": sheet_id,
            "row_index": target_row_idx + 1,
            "row_id": final_id,
            "case_id": final_id,
            "data": data_dict,
        }

    # 兼容历史方法名
    get_case = get_row

    def query_rows(
        self,
        sheet_name: str | None = None,
        keyword: str | None = None,
        filters: dict[str, str] | None = None,
        id_pattern: str | None = None,
        limit: int = 20,
        offset: int = 0,
        # 兼容历史参数
        case_id_pattern: str | None = None,
        level: str | None = None,
        module: str | None = None,
        sub_module: str | None = None,
        function: str | None = None,
        check_point: str | None = None,
        result_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """按多维度条件检索表格行数据列表。

        支持全文搜索 (keyword)、任意列包含匹配 (filters)，
        保留每行完整列字段数据字典 (data 字段及顶层透传)。
        """
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        headers = self.get_headers(sheet_name)
        ncols = len(headers)
        total_rows = sheet_info.get("row_count", 0)
        if total_rows <= 1:
            return []

        c_id = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["id"])
        c_lvl = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["level"])
        c_mod = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["module"])
        c_submod = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["sub_module"])
        c_func = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["function"])
        c_chk = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["check_point"])
        c_res = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["result"])

        custom_filter_cols: list[tuple[int, str]] = []
        if filters:
            for f_key, f_val in filters.items():
                if f_val is not None:
                    matched_col = self._find_column_index(headers, [f_key])
                    if matched_col is not None:
                        custom_filter_cols.append((matched_col, str(f_val).strip()))

        chunk_size = 200
        matched: list[dict[str, Any]] = []
        skipped = 0
        clean_kw = keyword.strip().lower() if keyword else None
        target_pattern = id_pattern or case_id_pattern

        for r_start in range(1, total_rows, chunk_size):
            r_end = min(r_start + chunk_size - 1, total_rows - 1)
            if r_start > r_end:
                break
            try:
                csv_text = self.client.get_cell_data(
                    file_id=self.effective_file_id,
                    sheet_id=sheet_id,
                    start_row=r_start,
                    end_row=r_end,
                    start_col=0,
                    end_col=ncols - 1,
                    return_csv=True,
                )
            except TencentDocError as e:
                if e.code == 60871:
                    logger.warning(
                        "子表 [%s] 检索第 %d-%d 行超出有效网格范围，停止后续翻页",
                        sheet_name,
                        r_start,
                        r_end,
                    )
                    break
                raise

            rows = list(csv.reader(io.StringIO(csv_text)))
            if not rows:
                break

            consecutive_empty = 0
            for offset_i, row in enumerate(rows):
                if not row or not any(row):
                    consecutive_empty += 1
                    continue
                consecutive_empty = 0

                val_id = row[c_id].strip() if c_id is not None and c_id < len(row) else ""
                val_lvl = row[c_lvl].strip() if c_lvl is not None and c_lvl < len(row) else ""
                val_mod = row[c_mod].strip() if c_mod is not None and c_mod < len(row) else ""
                val_submod = (
                    row[c_submod].strip() if c_submod is not None and c_submod < len(row) else ""
                )
                val_func = row[c_func].strip() if c_func is not None and c_func < len(row) else ""
                val_chk = row[c_chk].strip() if c_chk is not None and c_chk < len(row) else ""
                val_res = row[c_res].strip() if c_res is not None and c_res < len(row) else ""

                if not val_id and not any(row):
                    continue

                if target_pattern and target_pattern not in val_id:
                    continue
                if level and level != val_lvl:
                    continue
                if module and module not in val_mod:
                    continue
                if sub_module and sub_module not in val_submod:
                    continue
                if function and function not in val_func:
                    continue
                if check_point and check_point not in val_chk:
                    continue
                if result_filter:
                    if result_filter in ("未执行", "空", "待执行"):
                        if val_res != "":
                            continue
                    elif result_filter not in val_res:
                        continue

                if clean_kw and not any(clean_kw in c.lower() for c in row):
                    continue

                matched_filters = True
                for col_idx, expected_val in custom_filter_cols:
                    actual_val = row[col_idx].strip() if col_idx < len(row) else ""
                    if expected_val not in actual_val:
                        matched_filters = False
                        break
                if not matched_filters:
                    continue

                if skipped < offset:
                    skipped += 1
                    continue

                abs_row = r_start + offset_i
                row_data: dict[str, str] = {}
                for idx, h in enumerate(headers):
                    row_data[h] = row[idx].strip() if idx < len(row) else ""

                item = {
                    "_row_index": abs_row + 1,
                    "_row_index_0_based": abs_row,
                    "row_id": val_id,
                    "case_id": val_id,
                    "用例编号": val_id,
                    "级别": val_lvl,
                    "一级模块": val_mod,
                    "二级模块": val_submod,
                    "功能": val_func,
                    "验证点": val_chk,
                    "测试结果": val_res,
                    "data": row_data,
                }
                for k, v in row_data.items():
                    if k not in item and v:
                        item[k] = v

                matched.append(item)
                if len(matched) >= limit:
                    return matched

            if consecutive_empty >= 50:
                break

        return matched

    # 兼容历史方法名
    query_cases = query_rows

    def _resolve_target_cols(self, headers: list[str]) -> dict[str, int]:
        """识别常见标准列在表头中的列索引。"""
        res_cols: dict[str, int] = {}
        for canonical, aliases in self.TARGET_FIELD_ALIASES.items():
            matched_idx = self._find_column_index(headers, aliases)
            if matched_idx is not None:
                res_cols[canonical] = matched_idx
        return res_cols

    def update_row(
        self,
        sheet_name: str | None = None,
        row_id: str | None = None,
        row_index: int | None = None,
        fields: dict[str, Any] | None = None,
        # 兼容历史测试用例回写参数
        case_id: str | None = None,
        result: str | None = None,
        executor: str | None = None,
        execute_time: str | None = None,
        remark: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """单行表格字段原子更新。"""
        update_item: dict[str, Any] = {}
        if row_id or case_id:
            update_item["row_id"] = row_id or case_id
            update_item["case_id"] = row_id or case_id
        if row_index is not None:
            update_item["row_index"] = row_index

        if fields:
            update_item.update(fields)

        # 兼容历史测试用例字段
        if result is not None:
            update_item["result"] = result
        if executor is not None:
            update_item["executor"] = executor
        if execute_time is not None:
            update_item["execute_time"] = execute_time
        if remark is not None:
            update_item["remark"] = remark
        if extra_fields:
            update_item.update(extra_fields)

        batch_res = self.batch_update_rows(
            sheet_name=sheet_name,
            updates=[update_item],
        )
        if batch_res.get("not_found_rows"):
            raise TencentDocError(f"未找到行: {batch_res['not_found_rows']}")
        if batch_res.get("failed_items"):
            err = batch_res["failed_items"][0]
            raise TencentDocError(f"更新失败: {err}")

        effective_id = row_id or case_id or (f"ROW_{row_index}" if row_index else "")
        effective_time = execute_time or (
            datetime.now().strftime("%Y/%m/%d") if result else None
        )
        return {
            "ok": True,
            "sheet_name": batch_res.get("sheet_name", sheet_name or ""),
            "sheet_id": batch_res.get("sheet_id"),
            "row_id": effective_id,
            "case_id": effective_id,
            "result": result or "",
            "executor": executor,
            "execute_time": effective_time,
            "remark": remark,
            "updated_fields": {
                k: v for k, v in update_item.items()
                if v is not None and k not in ("row_id", "case_id", "row_index", "_row_index")
            },
            "message": f"表格行 [{effective_id}] 已成功更新",
        }

    # 兼容历史方法名
    update_case_result = update_row

    def batch_update_rows(
        self,
        sheet_name: str | None = None,
        updates: list[dict[str, Any]] | None = None,
        chunk_cell_size: int = 150,
    ) -> dict[str, Any]:
        """批量回写多行表格数据（防限流核心引擎）。"""
        updates = updates or []
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        resolved_sname = sheet_info.get("sheet_name", sheet_name or "")

        if not updates:
            return {
                "ok": True,
                "sheet_name": resolved_sname,
                "sheet_id": sheet_id,
                "total_submitted": 0,
                "updated_count": 0,
                "updated_rows": [],
                "updated_cases": [],
                "not_found_rows": [],
                "not_found_cases": [],
                "failed_items": [],
                "failed_cases": [],
                "message": "无需更新的条目",
            }

        headers = self.get_headers(sheet_name)
        col_indices = self._resolve_target_cols(headers)
        index_map = self.build_id_index(sheet_name)

        today_str = datetime.now().strftime("%Y/%m/%d")
        cells_to_write: list[dict[str, Any]] = []
        updated_rows: list[str] = []
        not_found_rows: list[str] = []
        failed_items: list[dict[str, str]] = []

        header_exact_map = {h: idx for idx, h in enumerate(headers)}
        header_norm_map = {normalize_header(h): idx for idx, h in enumerate(headers)}

        for item in updates:
            raw_id = (
                item.get("row_id")
                or item.get("case_id")
                or item.get("用例编号")
                or item.get("用例ID")
                or item.get("ID")
                or item.get("编码")
                or item.get("编号")
            )
            raw_row = item.get("_row_index") or item.get("row_index")

            row_idx: int | None = None
            id_str = str(raw_id).strip() if raw_id is not None else ""

            if raw_row is not None:
                try:
                    row_idx = int(raw_row) - 1
                except Exception:
                    pass

            if row_idx is None:
                if not id_str:
                    failed_items.append({"item": str(item), "error": "缺少 row_id / case_id 或 row_index 字段"})
                    continue

                if id_str not in index_map:
                    index_map = self.build_id_index(sheet_name, force=True)

                if id_str not in index_map:
                    not_found_rows.append(id_str)
                    continue

                row_idx = index_map[id_str]

            def _get_val(d: dict[str, Any], *keys: str) -> tuple[bool, Any]:
                for k in keys:
                    if d.get(k) is not None:
                        return True, d[k]
                return False, None

            # 1. 结果 / 状态
            has_res, res_val = _get_val(item, "result", "测试结果", "执行结果", "结果", "status", "状态")
            if has_res and res_val is not None and "result" in col_indices:
                cells_to_write.append({
                    "row": row_idx,
                    "col": col_indices["result"],
                    "value_type": "STRING",
                    "string_value": str(res_val).strip(),
                })

            # 2. 执行人 / 负责人
            has_exec, exec_val = _get_val(item, "executor", "执行人", "测试人", "owner", "负责人")
            if has_exec and exec_val is not None and "executor" in col_indices:
                cells_to_write.append({
                    "row": row_idx,
                    "col": col_indices["executor"],
                    "value_type": "STRING",
                    "string_value": str(exec_val).strip(),
                })

            # 3. 执行时间 / 日期
            has_time, time_val = _get_val(item, "execute_time", "执行时间", "测试时间", "执行日期", "date", "日期")
            if not has_time and res_val:
                has_time, time_val = True, today_str
            if has_time and time_val is not None and "execute_time" in col_indices:
                cells_to_write.append({
                    "row": row_idx,
                    "col": col_indices["execute_time"],
                    "value_type": "STRING",
                    "string_value": str(time_val).strip(),
                })

            # 4. 备注 / 说明
            has_remark, remark_val = _get_val(item, "remark", "备注", "说明", "缺陷说明", "notes")
            if has_remark and remark_val is not None and "remark" in col_indices:
                cells_to_write.append({
                    "row": row_idx,
                    "col": col_indices["remark"],
                    "value_type": "STRING",
                    "string_value": str(remark_val).strip(),
                })

            # 5. 支持任意扩展表头字段写入
            internal_keys = {
                "row_id", "case_id", "用例编号", "用例ID", "id", "_row_index", "row_index",
                "_row_index_0_based", "result", "测试结果", "执行结果", "结果", "status", "状态",
                "executor", "执行人", "测试人", "owner", "负责人", "execute_time", "执行时间",
                "测试时间", "remark", "备注", "说明", "缺陷说明", "extra_fields", "fields",
            }
            extra_dict: dict[str, Any] = {}
            if isinstance(item.get("fields"), dict):
                extra_dict.update(item["fields"])
            if isinstance(item.get("extra_fields"), dict):
                extra_dict.update(item["extra_fields"])
            extra_dict.update({k: v for k, v in item.items() if k not in internal_keys})

            for ext_k, ext_v in extra_dict.items():
                if ext_v is None:
                    continue
                target_col: int | None = None
                if ext_k in header_exact_map:
                    target_col = header_exact_map[ext_k]
                elif normalize_header(ext_k) in header_norm_map:
                    target_col = header_norm_map[normalize_header(ext_k)]

                if target_col is not None:
                    cells_to_write.append({
                        "row": row_idx,
                        "col": target_col,
                        "value_type": "STRING",
                        "string_value": str(ext_v).strip(),
                    })

            updated_rows.append(id_str or f"ROW_{row_idx + 1}")

        if not cells_to_write:
            return {
                "ok": False,
                "sheet_name": resolved_sname,
                "sheet_id": sheet_id,
                "total_submitted": len(updates),
                "updated_count": 0,
                "updated_rows": [],
                "updated_cases": [],
                "not_found_rows": not_found_rows,
                "not_found_cases": not_found_rows,
                "failed_items": failed_items,
                "failed_cases": failed_items,
                "message": "未匹配到任何有效单元格修改",
            }

        for i in range(0, len(cells_to_write), chunk_cell_size):
            chunk = cells_to_write[i:i + chunk_cell_size]
            try:
                self.client.set_range_value(
                    file_id=self.effective_file_id,
                    sheet_id=sheet_id,
                    values=chunk,
                )
            except Exception as e:
                logger.error("批量提交单元格分片失败: %s", e)
                raise TencentDocError(f"批量写入表格时出错: {e}") from e

        return {
            "ok": True,
            "sheet_name": resolved_sname,
            "sheet_id": sheet_id,
            "total_submitted": len(updates),
            "updated_count": len(updated_rows),
            "updated_cells_count": len(cells_to_write),
            "updated_rows": updated_rows,
            "updated_cases": updated_rows,
            "not_found_rows": not_found_rows,
            "not_found_cases": not_found_rows,
            "failed_items": failed_items,
            "failed_cases": failed_items,
            "message": f"成功批量回写 {len(updated_rows)} 行数据，共更新 {len(cells_to_write)} 个单元格",
        }

    # 兼容历史方法名
    batch_update_results = batch_update_rows


# 全局共享管理实例
tencent_sheet_manager = TencentSheetManager()

# 兼容历史实例名
testcase_manager = tencent_sheet_manager
TestCaseManager = TencentSheetManager
