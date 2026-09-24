"""测试腾讯文档在线测试用例管理与结果回写工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastmcp import Client

from qa_automation.mcp.servers.tencent_docs import create_server
from qa_automation.testcase import (
    TencentDocError,
    TestCaseManager,
    parse_file_id,
    testcase_manager,
)


def test_parse_file_id():
    # 1. 完整 URL 包含 tab
    url1 = "https://docs.qq.com/sheet/DUUhiWnFvZVdibWZq?tab=rukw1z"
    file_id1, tab1 = parse_file_id(url1)
    assert file_id1 == "DUUhiWnFvZVdibWZq"
    assert tab1 == "rukw1z"

    # 2. 纯 file_id
    id2 = "DUUhiWnFvZVdibWZq"
    file_id2, tab2 = parse_file_id(id2)
    assert file_id2 == "DUUhiWnFvZVdibWZq"
    assert tab2 is None

    # 3. URL 不包含 tab
    url3 = "https://docs.qq.com/sheet/sheet01"
    file_id3, tab3 = parse_file_id(url3)
    assert file_id3 == "sheet01"
    assert tab3 is None


class FakeTencentDocSheetClient:
    """模拟腾讯文档客户端，提供固定的测试数据。"""

    def __init__(self):
        self.token = "test_token"
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.written_values: list[dict[str, Any]] = []

    def set_token(self, token: str) -> None:
        self.token = token

    def query_file_info(self, file_id: str) -> dict[str, Any]:
        self.calls.append(("manage.query_file_info", {"file_id": file_id}))
        return {
            "file_id": "canonical_" + file_id,
            "title": "测试项目用例表",
            "url": f"https://docs.qq.com/sheet/{file_id}",
        }

    def get_sheet_info(self, file_id: str) -> dict[str, Any]:
        self.calls.append(("sheet.get_sheet_info", {"file_id": file_id}))
        return {
            "sheets": [
                {
                    "sheet_id": "sheet_yj",
                    "sheet_name": "预警管理",
                    "row_count": 10,
                    "col_count": 16,
                    "hidden": False,
                },
                {
                    "sheet_id": "sheet_qx",
                    "sheet_name": "权限管理",
                    "row_count": 20,
                    "col_count": 16,
                    "hidden": False,
                },
                {
                    "sheet_id": "sheet_hidden",
                    "sheet_name": "隐藏表",
                    "row_count": 5,
                    "col_count": 10,
                    "hidden": True,
                },
            ]
        }

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
        self.calls.append((
            "sheet.get_cell_data",
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "start_row": start_row,
                "end_row": end_row,
                "start_col": start_col,
                "end_col": end_col,
            },
        ))

        # 表头 (row 0)
        if start_row == 0 and end_row == 0:
            return "用例编号,级别,一级模块,二级模块,功能,验证点,前置条件,测试步骤,测试数据,预期结果,测试结果,执行人,执行时间,编写人,编写时间,备注\n"
        if start_row == 0 and end_row == 1:
            return "用例编号,级别,一级模块\nAPS_YJGL_0001,中级,预警通知\n"

        # 扫描用例编号列 (col 0, rows 1..N)
        if start_col == 0 and end_col == 0:
            if sheet_id == "sheet_yj":
                return "APS_YJGL_0001\nAPS_YJGL_0002\nAPS_YJGL_0003\n\n\n"
            return "APS_QXGL_0001\nAPS_QXGL_0002\n\n"

        # 读取单行数据 (row 1)
        if start_row == 1 and end_row == 1:
            return "APS_YJGL_0001,中级,预警通知,预警管理,列表,验证列表加载,已登录,步骤1:打开页面,无,页面正常展示,通过,测试小王,2026/09/20,黄小珍,2026/8/12,测试备注\n"

        # 读取多行数据 (用于 query)
        if start_row == 1:
            return (
                "APS_YJGL_0001,中级,预警通知,预警管理,列表,验证列表加载,已登录,打开页面,无,正常展示,通过,小王,2026/09/20,黄小珍,2026/8/12,正常\n"
                "APS_YJGL_0002,高,预警通知,预警管理,导出,验证导出EXCEL,已登录,点击导出,无,成功下载,不通过,小李,2026/09/21,黄小珍,2026/8/12,文件损坏\n"
                "APS_YJGL_0003,中级,系统设置,参数配置,保存,验证参数校验,已登录,点击保存,无,提示错误,,,,,待测\n"
            )

        return ""

    def set_range_value(
        self, file_id: str, sheet_id: str, values: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.calls.append((
            "sheet.set_range_value",
            {"file_id": file_id, "sheet_id": sheet_id, "values": values},
        ))
        self.written_values.extend(values)
        return {"error": ""}


def test_manager_lifecycle():
    fake_client = FakeTencentDocSheetClient()
    mgr = TestCaseManager(client=fake_client)

    # 1. 连接
    res = mgr.connect("https://docs.qq.com/sheet/test_file_001?tab=sheet_yj")
    assert res["ok"] is True
    assert res["title"] == "测试项目用例表"
    assert res["active_tab_sheet"] == "预警管理"
    assert res["visible_sheets_count"] == 2

    # 2. 子表解析与模糊匹配
    sid, sinfo = mgr.resolve_sheet("预警管理")
    assert sid == "sheet_yj"
    sid_fuzzy, _ = mgr.resolve_sheet("预警")
    assert sid_fuzzy == "sheet_yj"

    with pytest.raises(TencentDocError, match="未找到名为"):
        mgr.resolve_sheet("不存在的子表")

    # 3. 表头提取与缓存
    headers = mgr.get_headers("预警管理")
    assert headers[0] == "用例编号"
    assert "测试结果" in headers
    assert "执行人" in headers
    assert "执行时间" in headers
    assert "备注" in headers

    # 4. 用例编号索引
    idx_map = mgr.build_id_index("预警管理")
    assert "APS_YJGL_0001" in idx_map
    assert idx_map["APS_YJGL_0001"] == 1  # 0-based row 1

    # 5. 精确提取单条用例
    case = mgr.get_case("预警管理", "APS_YJGL_0001")
    assert case["case_id"] == "APS_YJGL_0001"
    assert case["row_index"] == 2  # 1-based row 2
    assert case["data"]["功能"] == "列表"
    assert case["data"]["验证点"] == "验证列表加载"
    assert case["data"]["测试结果"] == "通过"

    # 6. 多维度查询
    q1 = mgr.query_cases("预警管理", function="导出")
    assert len(q1) == 1
    assert q1[0]["用例编号"] == "APS_YJGL_0002"

    q2 = mgr.query_cases("预警管理", result_filter="未执行")
    assert len(q2) == 1
    assert q2[0]["用例编号"] == "APS_YJGL_0003"

    # 7. 单条测试结果回写
    up_res = mgr.update_case_result(
        sheet_name="预警管理",
        case_id="APS_YJGL_0001",
        result="不通过",
        executor="自动化机器人",
        execute_time="2026/09/22",
        remark="断言失败：状态码非200",
    )
    assert up_res["ok"] is True

    # 8. 批量回写验证（单次聚合提交）
    fake_client.written_values.clear()
    batch_res = mgr.batch_update_results(
        sheet_name="预警管理",
        updates=[
            {
                "case_id": "APS_YJGL_0001",
                "result": "通过",
                "executor": "张三",
                "execute_time": "2026/09/22",
                "remark": "复测通过",
            },
            {
                "case_id": "APS_YJGL_0002",
                "result": "通过",
                "executor": "李四",
                "execute_time": "2026/09/22",
                "remark": "已修复",
            },
            {
                "case_id": "NON_EXISTENT_CASE",
                "result": "通过",
            },
        ],
    )
    assert batch_res["ok"] is True
    assert batch_res["total_submitted"] == 3
    assert batch_res["updated_count"] == 2
    assert "NON_EXISTENT_CASE" in batch_res["not_found_cases"]
    assert len(batch_res["updated_cases"]) == 2

    # 验证是否一次性提交了所有单元格（2 条用例 * 4 字段 = 8 单元格）
    assert len(fake_client.written_values) == 8
    written_map = {(v["row"], v["col"]): v["string_value"] for v in fake_client.written_values}
    # row 1 (APS_YJGL_0001): col 10 为 测试结果, col 11 为 执行人
    assert written_map[(1, 10)] == "通过"
    assert written_map[(1, 11)] == "张三"
    assert written_map[(1, 12)] == "2026/09/22"
    assert written_map[(1, 15)] == "复测通过"
    # row 2 (APS_YJGL_0002)
    assert written_map[(2, 10)] == "通过"
    assert written_map[(2, 11)] == "李四"


@pytest.mark.asyncio
async def test_fastmcp_tools_registration(monkeypatch):
    """验证 MCP 工具通过 fastmcp 客户端能正常发现与调用。"""
    fake_client = FakeTencentDocSheetClient()
    monkeypatch.setattr(testcase_manager, "client", fake_client)
    testcase_manager._sheets_by_name.clear()
    testcase_manager._headers_cache.clear()
    testcase_manager._id_index_cache.clear()

    mcp = create_server()
    client = Client(mcp)
    async with client:
        # 1. 验证工具列表中暴露了相关工具
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "testcase_connect" in tool_names
        assert "testcase_get" in tool_names
        assert "testcase_update_result" in tool_names
        assert "testcase_batch_update_results" in tool_names

        # 2. 调用 testcase_connect
        conn_res = await client.call_tool(
            "testcase_connect",
            {"url_or_file_id": "https://docs.qq.com/sheet/test_001?tab=sheet_yj"},
        )
        assert conn_res.data.ok is True
        assert conn_res.data.title == "测试项目用例表"

        # 3. 调用 testcase_get
        get_res = await client.call_tool(
            "testcase_get",
            {"sheet_name": "预警管理", "case_id": "APS_YJGL_0001"},
        )
        assert get_res.data.case_id == "APS_YJGL_0001"
        assert get_res.data.row_index == 2

        # 4. 调用 testcase_batch_update_results
        batch_res = await client.call_tool(
            "testcase_batch_update_results",
            {
                "sheet_name": "预警管理",
                "updates": [
                    {
                        "case_id": "APS_YJGL_0001",
                        "result": "通过",
                        "executor": "自动化测试",
                    }
                ],
            },
        )
        assert batch_res.data.ok is True
        assert batch_res.data.updated_count == 1


# ---------------------------------------------------------------------------
# 回归：执行时间自动补全
# ---------------------------------------------------------------------------


def _manager_with_fake_client():
    fake = FakeTencentDocSheetClient()
    mgr = TestCaseManager(client=fake)
    mgr.connect("https://docs.qq.com/sheet/test_file_001?tab=sheet_yj")
    fake.written_values.clear()
    return mgr, fake


def test_single_update_autofills_execute_time():
    """单条回写未传 execute_time 时，必须自动补当天日期。"""
    mgr, fake = _manager_with_fake_client()
    today = datetime.now().strftime("%Y/%m/%d")

    res = mgr.update_case_result(
        sheet_name="预警管理",
        case_id="APS_YJGL_0001",
        result="通过",
        executor="张三",
    )

    written = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    # row 1 (APS_YJGL_0001): col 12 为执行时间
    assert (1, 12) in written, f"执行时间未写入单元格，实际写入: {written}"
    assert written[(1, 12)] == today

    assert res["execute_time"] == today


def test_explicit_execute_time_is_respected():
    """显式传入 execute_time 时必须原样落库，不被当天日期覆盖。"""
    mgr, fake = _manager_with_fake_client()

    mgr.update_case_result(
        sheet_name="预警管理",
        case_id="APS_YJGL_0001",
        result="通过",
        execute_time="2020/01/01",
    )

    written = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    assert written.get((1, 12)) == "2020/01/01"


def test_no_result_does_not_stamp_date():
    """没有测试结果（例如仅补备注）时不应凭空写入执行时间。"""
    mgr, fake = _manager_with_fake_client()

    mgr.update_case_result(
        sheet_name="预警管理",
        case_id="APS_YJGL_0001",
        result="",
        remark="仅补备注",
    )

    written = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    assert (1, 12) not in written
    assert written.get((1, 15)) == "仅补备注"


def test_batch_autofill_still_works_when_key_omitted():
    """批量路径省略 execute_time 键时，仍然自动补当天日期。"""
    mgr, fake = _manager_with_fake_client()
    today = datetime.now().strftime("%Y/%m/%d")

    mgr.batch_update_results(
        sheet_name="预警管理",
        updates=[
            {"case_id": "APS_YJGL_0002", "result": "通过"},
        ],
    )

    written = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    assert written.get((2, 12)) == today


def test_none_valued_fields_are_treated_as_absent():
    """键存在但值为 None 的字段一律视为未提供，不得写入空单元格。"""
    mgr, fake = _manager_with_fake_client()

    mgr.batch_update_results(
        sheet_name="预警管理",
        updates=[
            {
                "case_id": "APS_YJGL_0001",
                "result": "通过",
                "executor": None,
                "execute_time": "2025/12/31",
                "remark": None,
            },
        ],
    )

    written = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    # col 11 为执行人、col 15 为备注，传了 None 则完全不应入写队列
    assert (1, 11) not in written
    assert (1, 15) not in written
    assert written.get((1, 10)) == "通过"


# ---------------------------------------------------------------------------
# 通用化与弹性功能测试
# ---------------------------------------------------------------------------


def test_parse_file_id_advanced():
    """测试高级与复杂链接解析（智能表格、子表别名参数、hash片段等）。"""
    # 1. 智能表格 smartsheet + sub_id
    f1, t1 = parse_file_id("https://docs.qq.com/smartsheet/DV3pUSmNkTG1hQk13?sub_id=puwuj9")
    assert f1 == "DV3pUSmNkTG1hQk13"
    assert t1 == "puwuj9"

    # 2. fragment hash (#tab=xxx)
    f2, t2 = parse_file_id("https://docs.qq.com/sheet/DV3pUSmNkTG1hQk13#tab=puwuj9")
    assert f2 == "DV3pUSmNkTG1hQk13"
    assert t2 == "puwuj9"

    # 3. 裸 ID 带 query 参数
    f3, t3 = parse_file_id("test_fid_123?tab=sub01")
    assert f3 == "test_fid_123"
    assert t3 == "sub01"

    # 4. 路径带动作后缀 (/sheet/xxx/edit)
    f4, t4 = parse_file_id("https://docs.qq.com/sheet/ABC123XYZ/edit?tab=t1")
    assert f4 == "ABC123XYZ"
    assert t4 == "t1"


def test_resolve_sheet_by_id_and_fallback():
    """测试按 sheet_id 解析、大小写不敏感匹配及默认活跃子表回退。"""
    mgr, _ = _manager_with_fake_client()

    # 1. 按 sheet_id 直接解析
    sid1, sinfo1 = mgr.resolve_sheet("sheet_yj")
    assert sid1 == "sheet_yj"
    assert sinfo1["sheet_name"] == "预警管理"

    # 2. 缺省 None / 空字符串回退到当前活跃子表 (sheet_yj)
    sid2, sinfo2 = mgr.resolve_sheet(None)
    assert sid2 == "sheet_yj"

    sid3, sinfo3 = mgr.resolve_sheet("")
    assert sid3 == "sheet_yj"

    # 3. 大小写不敏感
    sid4, _ = mgr.resolve_sheet("SHEET_QX")
    assert sid4 == "sheet_qx"


def test_universal_query_features():
    """测试全局关键字检索、任意字段筛选及完整整行数据保留。"""
    mgr, _ = _manager_with_fake_client()

    # 1. keyword 全文检索（整行任意单元格命中）
    items_kw = mgr.query_cases("预警管理", keyword="打开页面")
    assert len(items_kw) >= 1
    assert items_kw[0]["用例编号"] == "APS_YJGL_0001"

    # 2. filters 自定义表头字段筛选（如按编写人筛选）
    items_filter = mgr.query_cases("预警管理", filters={"编写人": "黄小珍"})
    assert len(items_filter) == 2

    # 3. 结果完整保留全量字段字典 data
    item = items_kw[0]
    assert "data" in item
    assert item["data"]["测试步骤"] == "打开页面"
    assert item["data"]["前置条件"] == "已登录"
    assert item["测试步骤"] == "打开页面"  # 透传到顶层


def test_universal_update_arbitrary_columns():
    """测试回写任意表头匹配的扩展字段及 _row_index 直接定位。"""
    mgr, fake = _manager_with_fake_client()
    fake.written_values.clear()

    # 1. 批量回写携带自定义列（如 备注、测试数据）以及物理行号 _row_index
    res = mgr.batch_update_results(
        sheet_name="预警管理",
        updates=[
            {
                "_row_index": 2,  # row 1 (0-based)
                "result": "通过",
                "executor": "赵六",
                "测试数据": "env=test",
                "备注": "自定义备注更新",
            }
        ],
    )
    assert res["ok"] is True
    assert res["updated_count"] == 1

    written = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    assert written.get((1, 10)) == "通过"
    assert written.get((1, 11)) == "赵六"
    # col 8 为测试数据, col 15 为备注
    assert written.get((1, 8)) == "env=test"
    assert written.get((1, 15)) == "自定义备注更新"

    # 2. 单条回写使用 extra_fields
    fake.written_values.clear()
    single_res = mgr.update_case_result(
        sheet_name="预警管理",
        case_id="APS_YJGL_0001",
        result="不通过",
        extra_fields={"测试数据": "input=invalid"},
    )
    assert single_res["ok"] is True
    written_single = {(v["row"], v["col"]): v["string_value"] for v in fake.written_values}
    assert written_single.get((1, 10)) == "不通过"
    assert written_single.get((1, 8)) == "input=invalid"


@pytest.mark.asyncio
async def test_fastmcp_read_cells_tool(monkeypatch):
    """验证新增的 testcase_read_cells 工具具备安全边界保护。"""
    fake_client = FakeTencentDocSheetClient()
    monkeypatch.setattr(testcase_manager, "client", fake_client)
    testcase_manager._sheets_by_name.clear()
    testcase_manager._headers_cache.clear()
    testcase_manager._id_index_cache.clear()

    mcp = create_server()
    client = Client(mcp)
    async with client:
        # 1. 连接
        await client.call_tool(
            "testcase_connect",
            {"url_or_file_id": "https://docs.qq.com/sheet/test_001?tab=sheet_yj"},
        )
        # 2. 读取单元格，故意传入超大行号 9999 和列号 9999
        read_res = await client.call_tool(
            "testcase_read_cells",
            {
                "sheet_name": "预警管理",
                "start_row": 0,
                "end_row": 9999,
                "start_col": 0,
                "end_col": 9999,
            },
        )
        data = read_res.data
        assert data["ok"] is True
        # row_count 在 fake 中为 10，col_count 为 16，安全钳制为 9 和 15
        assert data["range"]["end_row"] == 9
        assert data["range"]["end_col"] == 15


@pytest.mark.asyncio
async def test_tencent_sheet_standard_tools_flow(monkeypatch):
    """验证标准 tencent_sheet_* 工具集完整生命周期调用。"""
    fake_client = FakeTencentDocSheetClient()
    from qa_automation.tencent_sheet import tencent_sheet_manager
    monkeypatch.setattr(tencent_sheet_manager, "client", fake_client)
    tencent_sheet_manager._sheets_by_name.clear()
    tencent_sheet_manager._headers_cache.clear()
    tencent_sheet_manager._id_index_cache.clear()

    mcp = create_server()
    client = Client(mcp)
    async with client:
        # 1. 连接表格
        conn_res = await client.call_tool(
            "tencent_sheet_connect",
            {"url_or_file_id": "https://docs.qq.com/sheet/test_001?tab=sheet_yj"},
        )
        assert conn_res.data.ok is True
        assert conn_res.data.active_tab_sheet == "预警管理"

        # 2. 列出子表
        sheets = await client.call_tool("tencent_sheet_list_sheets", {})
        assert len(sheets.data) == 2

        # 3. 按 row_id 读取单行详情
        row_res = await client.call_tool(
            "tencent_sheet_get_row",
            {"sheet_name": "预警管理", "row_id": "APS_YJGL_0001"},
        )
        assert row_res.data.row_id == "APS_YJGL_0001"
        assert row_res.data.row_index == 2
        assert row_res.data.data["功能"] == "列表"

        # 4. 多维度检索行
        q_res = await client.call_tool(
            "tencent_sheet_query_rows",
            {"sheet_name": "预警管理", "keyword": "导出"},
        )
        assert q_res.data.count == 1
        assert q_res.data.items[0]["row_id"] == "APS_YJGL_0002"

        # 5. 更新单行字段
        up_res = await client.call_tool(
            "tencent_sheet_update_row",
            {
                "sheet_name": "预警管理",
                "row_id": "APS_YJGL_0001",
                "fields": {"测试结果": "通过", "执行人": "王五"},
            },
        )
        assert up_res.data.ok is True
        assert up_res.data.row_id == "APS_YJGL_0001"

        # 6. 批量更新多行字段
        fake_client.written_values.clear()
        batch_res = await client.call_tool(
            "tencent_sheet_batch_update",
            {
                "sheet_name": "预警管理",
                "updates": [
                    {"row_id": "APS_YJGL_0001", "fields": {"测试结果": "通过"}},
                    {"row_id": "APS_YJGL_0002", "fields": {"测试结果": "不通过"}},
                ],
            },
        )
        assert batch_res.data.ok is True
        assert batch_res.data.updated_count == 2

        # 7. 读取单元格切片
        read_res = await client.call_tool(
            "tencent_sheet_read_cells",
            {"sheet_name": "预警管理", "start_row": 0, "end_row": 1, "start_col": 0, "end_col": 2},
        )
        assert read_res.data["ok"] is True
        assert read_res.data["row_count"] == 2
