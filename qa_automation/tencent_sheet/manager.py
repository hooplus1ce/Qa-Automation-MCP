"""腾讯文档表格业务门面:连接、动态表头、主键索引、检索与防限流回写引擎。"""

from __future__ import annotations

import csv
import functools
import io
import logging
import os
import threading
from datetime import datetime
from typing import Any

from .parsing import normalize_header, parse_file_id
from .transport import TencentDocError, TencentSheetClient

logger = logging.getLogger("qa_automation.tencent_sheet")

def _state_guard(method):
    """Serialize a manager call on the instance's ``_state_lock``.

    FastMCP 在线程池里运行同步工具:两个并发调用若各自连不同文档,会在共享的
    active_file_id/_sheets_by_name/表头缓存上互相串改,把数据写进错误的表格。
    用 RLock(可重入)保证嵌套公开调用不死锁。
    """

    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)

    return wrapped


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
        # 并发防护:见 _state_guard。RLock 允许公开方法嵌套调用。
        self._state_lock = threading.RLock()
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

    @_state_guard
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

    @_state_guard
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

    @_state_guard
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

    @_state_guard
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

    def _read_id_column(
        self, sheet_id: str, id_col_idx: int, row_start: int, row_end: int
    ) -> dict[int, str]:
        """读取主键列 [row_start, row_end]（0-based 闭区间），返回 {0based_row: 值}。"""
        csv_text = self.client.get_cell_data(
            file_id=self.effective_file_id,
            sheet_id=sheet_id,
            start_row=row_start,
            end_row=row_end,
            start_col=id_col_idx,
            end_col=id_col_idx,
            return_csv=True,
        )
        values: dict[int, str] = {}
        for offset, r in enumerate(csv.reader(io.StringIO(csv_text))):
            val = r[0].strip() if r else ""
            if val:
                values[row_start + offset] = val
        return values

    def _verify_row_ids(
        self, sheet_id: str, id_col_idx: int, pairs: dict[str, int]
    ) -> tuple[dict[str, int], list[str]]:
        """写前按主键回读校验，防「缓存行号漂移 → 静默写错行」。

        行号索引缓存只在 id 查不到时才重建；表格被外部（或并发调用）插删行后，
        id 仍能命中但物理行号已漂移，直接按旧坐标写入会把结果写到错误的行。
        这里对待写行统一回读主键列比对：一致的放行，不一致的列入 stale 交由
        调用方强制重建索引后重解析；整段回读越界（表格已缩小）时全部视为 stale。
        返回 (校验通过的 {id: row}, 未通过的 id 列表)。
        """
        if not pairs:
            return {}, []
        verified: dict[str, int] = {}
        try:
            col_vals = self._read_id_column(
                sheet_id, id_col_idx, min(pairs.values()), max(pairs.values())
            )
        except TencentDocError:
            return {}, list(pairs.keys())
        stale = [i for i, row in pairs.items() if col_vals.get(row) != i]
        verified = {i: row for i, row in pairs.items() if col_vals.get(row) == i}
        return verified, stale

    @_state_guard
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

    @_state_guard
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

    @_state_guard
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

    @_state_guard
    def resolve_target_columns(self, sheet_name: str | None = None) -> dict[str, int]:
        """解析子表中标准业务字段(结果/执行人/执行时间/备注)的列索引。

        供工具层做 fail-closed 检查:批量写引擎对"别名列不存在"是静默跳过,
        单字段回写类工具必须先确认列可解析,否则会静默丢写。
        """
        headers = self.get_headers(sheet_name)
        return self._resolve_target_cols(headers)

    @_state_guard
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

        # ---- 第一遍：解析每条 update 的目标行号（显式行号优先，其次主键索引） ----
        entries: list[dict[str, Any]] = []
        id_pairs: dict[str, int] = {}  # 走主键索引解析的 {id: row}，供写前校验
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
            via_id = False
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
                via_id = True
                id_pairs[id_str] = row_idx

            entries.append({"item": item, "id": id_str, "row": row_idx, "via_id": via_id})

        # ---- 写前主键回读校验：行号缓存只在 id 查不到时重建，表格被外部插删行后
        # id 仍命中但行号已漂移，直接写会把结果写到错误的行（静默数据损坏）。
        # 校验失败的 id 强制重建索引重解析；重建后仍找不到则拒绝写入该条。 ----
        if id_pairs:
            id_col_idx = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["id"]) or 0
            _, stale_ids = self._verify_row_ids(sheet_id, id_col_idx, id_pairs)
            if stale_ids:
                logger.warning(
                    "子表 [%s] 检测到 %d 个主键的缓存行号已漂移，强制重建索引后重解析",
                    resolved_sname,
                    len(stale_ids),
                )
                index_map = self.build_id_index(sheet_name, force=True)
                for entry in entries:
                    if not entry["via_id"] or entry["id"] not in stale_ids:
                        continue
                    fresh_row = index_map.get(entry["id"])
                    if fresh_row is None:
                        failed_items.append({
                            "item": str(entry["item"]),
                            "error": (
                                f"主键 [{entry['id']}] 的缓存行号 {entry['row'] + 1} 已漂移，"
                                "重建索引后仍未找到该行，已拒绝写入（防止写错行）"
                            ),
                        })
                        entry["row"] = None
                    else:
                        entry["row"] = fresh_row

        for entry in entries:
            item = entry["item"]
            id_str = entry["id"]
            row_idx = entry["row"]
            if row_idx is None:
                continue

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

    # ------------------------------------------------------------------
    # 行列维度物理增删（官方 sheet.delete_dimension / sheet.insert_dimension）
    # ------------------------------------------------------------------

    MAX_DIMENSION_OPS = 200

    def _collect_row_indices(
        self,
        sheet_name: str | None,
        row_ids: list[str] | None,
        row_indices: list[int] | None,
    ) -> tuple[str, dict[str, Any], list[int], list[str], list[str], list[int]]:
        """把主键/用例编号与显式行号统一解析为升序去重的 0-based 全表行号。

        Returns:
            (sheet_id, sheet_info, sorted_indices, hit_ids, missing_ids, missing_indices)
        """
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        row_count = int(sheet_info.get("row_count") or 0)

        resolved: set[int] = set()
        hit_ids: list[str] = []
        missing_ids: list[str] = []
        missing_indices: list[int] = []

        if row_ids:
            id_map = self.build_id_index(sheet_name)
            pairs: dict[str, int] = {}
            for raw in row_ids:
                key = str(raw or "").strip()
                if not key:
                    continue
                if key in id_map:
                    pairs[key] = id_map[key]
                else:
                    missing_ids.append(key)

            # 删除是破坏性操作：缓存行号在表格被外部插删行后会漂移，按旧行号删会误删
            # 无辜行。写前（删前）按主键回读校验，漂移的 id 重建索引重解析，仍找不到
            # 则归入 missing 拒绝删除。
            if pairs:
                headers = self.get_headers(sheet_name)
                id_col_idx = self._find_column_index(headers, self.FILTER_FIELD_ALIASES["id"]) or 0
                _, stale = self._verify_row_ids(sheet_id, id_col_idx, pairs)
                if stale:
                    logger.warning(
                        "子表 [%s] 检测到 %d 个主键的缓存行号已漂移，删除前强制重建索引",
                        sheet_info.get("sheet_name", sheet_id),
                        len(stale),
                    )
                    id_map = self.build_id_index(sheet_name, force=True)
                    for key in stale:
                        fresh = id_map.get(key)
                        if fresh is None:
                            missing_ids.append(key)
                        else:
                            pairs[key] = fresh

            for key, row in pairs.items():
                resolved.add(row)
                hit_ids.append(key)

        if row_indices:
            for raw in row_indices:
                try:
                    idx = int(raw)
                except (TypeError, ValueError):
                    missing_indices.append(-1)
                    continue
                if idx < 0 or (row_count and idx >= row_count):
                    missing_indices.append(idx)
                else:
                    resolved.add(idx)

        return sheet_id, sheet_info, sorted(resolved), hit_ids, missing_ids, missing_indices

    def _invalidate_after_dimension_change(self, sheet_id: str, delta: int) -> int | None:
        """维度变更后失效行主键缓存、刷新子表行数，返回刷新后的总行数。"""
        self._id_index_cache.pop(sheet_id, None)

        info = self._sheets_by_id.get(sheet_id)
        if info is not None and delta:
            info["row_count"] = max(0, int(info.get("row_count") or 0) + delta)

        try:
            fresh = self.client.get_sheet_info(self.effective_file_id)
            sheets = fresh.get("sheets") if isinstance(fresh, dict) else fresh
            if isinstance(sheets, list):
                for s in sheets:
                    sid = s.get("sheet_id")
                    cached = self._sheets_by_id.get(sid) if sid else None
                    if cached is None:
                        continue
                    for key in ("row_count", "col_count", "hidden", "sheet_name", "sheet_type"):
                        if s.get(key) is not None:
                            cached[key] = s[key]
                    self._sheets_by_name[cached["sheet_name"]] = cached
        except Exception as e:
            logger.warning("维度变更后刷新子表元数据失败，沿用本地推算行数: %s", e)

        info = self._sheets_by_id.get(sheet_id)
        return int(info.get("row_count") or 0) if info else None

    @_state_guard
    def delete_rows(
        self,
        sheet_name: str | None = None,
        row_ids: list[str] | None = None,
        row_indices: list[int] | None = None,
        dry_run: bool = False,
        allow_header: bool = False,
    ) -> dict[str, Any]:
        """物理删除子表中的整行（删除后下方行整体上移，子表总行数减少）。

        与 ``batch_update_rows``（回写单元格内容）的本质区别：本方法真正移除行结构。
        多行删除按行号**降序**执行，避免删行后行号漂移导致误删。

        Args:
            sheet_name: 子表名称或 sheet_id，缺省为当前活跃子表
            row_ids: 主键 / 用例编号列表（经主键列索引解析为行号）
            row_indices: 全表 0-based 行号列表（含表头行，0 即表头）
            dry_run: 仅解析目标、返回影响范围，不实际执行删除
            allow_header: 是否允许删除第 0 行（表头）；默认 False 予以保护
        """
        sheet_id, sheet_info, indices, hit_ids, missing_ids, missing_indices = (
            self._collect_row_indices(sheet_name, row_ids, row_indices)
        )
        resolved_sname = sheet_info.get("sheet_name", sheet_name or sheet_id)
        row_count_before = int(sheet_info.get("row_count") or 0)
        requested = len(row_ids or []) + len(row_indices or [])

        skipped_header = False
        if not allow_header and 0 in indices:
            indices = [i for i in indices if i != 0]
            skipped_header = True

        if len(indices) > self.MAX_DIMENSION_OPS:
            raise TencentDocError(
                f"单次最多删除 {self.MAX_DIMENSION_OPS} 行，本次命中 {len(indices)} 行；请分批调用"
            )

        deleted_indices: list[int] = []
        failed_items: list[dict[str, str]] = []

        if dry_run or not indices:
            deleted_indices = list(indices)
        else:
            for idx in sorted(indices, reverse=True):
                try:
                    self.client.delete_dimension(
                        file_id=self.effective_file_id,
                        sheet_id=sheet_id,
                        dimension_type="row",
                        index=idx,
                        count=1,
                    )
                    deleted_indices.append(idx)
                except Exception as e:
                    logger.error("删除子表 [%s] 第 %d 行失败: %s", resolved_sname, idx, e)
                    failed_items.append({"row_index": str(idx), "error": str(e)})

        row_count_after = row_count_before
        if not dry_run and deleted_indices:
            row_count_after = self._invalidate_after_dimension_change(
                sheet_id, -len(deleted_indices)
            )

        if dry_run:
            message = f"预览：命中 {len(deleted_indices)} 行待删除（未执行）"
        elif failed_items:
            message = (
                f"已删除 {len(deleted_indices)} 行，{len(failed_items)} 行失败；"
                f"子表行数 {row_count_before} → {row_count_after}"
            )
        else:
            message = (
                f"成功删除 {len(deleted_indices)} 行；子表行数 {row_count_before} → {row_count_after}"
            )

        return {
            "ok": not failed_items,
            "sheet_name": resolved_sname,
            "sheet_id": sheet_id,
            "dimension_type": "row",
            "requested": requested,
            "affected_indices": sorted(deleted_indices, reverse=True),
            "affected_rows": hit_ids,
            "not_found_rows": missing_ids,
            "not_found_indices": missing_indices,
            "skipped_header": skipped_header,
            "failed_items": failed_items,
            "row_count_before": row_count_before,
            "row_count_after": row_count_after,
            "dry_run": dry_run,
            "message": message,
        }

    @_state_guard
    def insert_rows(
        self,
        sheet_name: str | None = None,
        row_indices: list[int] | None = None,
        count: int = 1,
        direction: str = "before",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """在指定位置插入空白行（行结构新增，子表总行数增加）。

        多位置插入按行号**升序**执行，并对前序插入造成的行号漂移自动补偿。

        Args:
            row_indices: 全表 0-based 参照行号列表（含表头行）
            count: 每个位置插入的行数，默认 1
            direction: "before"（默认）或 "after"
        """
        sheet_id, sheet_info = self.resolve_sheet(sheet_name)
        resolved_sname = sheet_info.get("sheet_name", sheet_name or sheet_id)
        row_count_before = int(sheet_info.get("row_count") or 0)

        valid: list[int] = []
        missing_indices: list[int] = []
        for raw in row_indices or []:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                missing_indices.append(-1)
                continue
            if idx < 0 or (row_count_before and idx >= row_count_before):
                missing_indices.append(idx)
            else:
                valid.append(idx)
        valid = sorted(set(valid))

        ins_count = max(1, int(count))
        if len(valid) * ins_count > self.MAX_DIMENSION_OPS:
            raise TencentDocError(
                f"单次最多插入 {self.MAX_DIMENSION_OPS} 行，本次 {len(valid) * ins_count} 行；请分批调用"
            )

        inserted: list[int] = []
        failed_items: list[dict[str, str]] = []

        if dry_run or not valid:
            inserted = list(valid)
        else:
            shift = 0
            for idx in valid:
                try:
                    self.client.insert_dimension(
                        file_id=self.effective_file_id,
                        sheet_id=sheet_id,
                        dimension_type="row",
                        index=idx + shift,
                        count=ins_count,
                        direction=direction,
                    )
                    inserted.append(idx)
                    shift += ins_count
                except Exception as e:
                    logger.error("向子表 [%s] 第 %d 行插入失败: %s", resolved_sname, idx, e)
                    failed_items.append({"row_index": str(idx), "error": str(e)})

        row_count_after = row_count_before
        if not dry_run and inserted:
            row_count_after = self._invalidate_after_dimension_change(
                sheet_id, len(inserted) * ins_count
            )

        if dry_run:
            message = f"预览：将在 {len(inserted)} 个位置插入 {len(inserted) * ins_count} 行（未执行）"
        else:
            message = (
                f"成功插入 {len(inserted) * ins_count} 行；子表行数 {row_count_before} → {row_count_after}"
            )

        return {
            "ok": not failed_items,
            "sheet_name": resolved_sname,
            "sheet_id": sheet_id,
            "dimension_type": "row",
            "requested": len(row_indices or []),
            "affected_indices": sorted(inserted),
            "affected_rows": [],
            "not_found_rows": [],
            "not_found_indices": missing_indices,
            "skipped_header": False,
            "failed_items": failed_items,
            "row_count_before": row_count_before,
            "row_count_after": row_count_after,
            "dry_run": dry_run,
            "message": message,
        }

    # 兼容历史方法名
    batch_update_results = batch_update_rows


# 全局共享管理实例
tencent_sheet_manager = TencentSheetManager()

# 兼容历史实例名
testcase_manager = tencent_sheet_manager
TestCaseManager = TencentSheetManager
