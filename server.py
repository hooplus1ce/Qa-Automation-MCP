"""
vtable-mcp:VTable 虚拟表格 MCP 服务器(独立分发版)
=====================================================

功能:
  1. 交互式 UI 工具(FastMCP Apps):测试用例表 / 引擎仪表盘 / 动态表单 / 用例执行台
  2. VTable JS 脚本资源(vtable://js/...):19 个逆向脚本内化,客户端可按协议读取
  3. 内置 providers:Approval(审批门控)/ Choice / FileUpload / GenerativeUI

数据全部内建于 sample_data.py,不依赖任何外部文件;表单提交落盘到 data/ 目录。

运行:
  # stdio MCP 服务(供 MCP 客户端连接)
  uv run fastmcp run fastmcp.json

  # 等价的直接入口
  uv run python server.py

  # 可选:FastMCP Apps 浏览器预览(内部临时使用 HTTP)
  uv run fastmcp dev apps server.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP, FastMCPApp
from fastmcp.apps.approval import Approval
from fastmcp.apps.choice import Choice
from fastmcp.apps.file_upload import FileUpload
from fastmcp.apps.generative import GenerativeUI
from prefab_ui import PrefabApp
from prefab_ui.actions.mcp import CallTool
from prefab_ui.components import (
    Button,
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
    Column,
    DataTable,
    DataTableColumn,
    Dashboard,
    Form,
    Input,
    Metric,
    Select,
    SelectOption,
    Text,
    Textarea,
)
from prefab_ui.components.charts import BarChart, ChartSeries

from sample_data import MOLD_FIELD_NAMES, MOLD_MASTER_FIELDS, TEST_CASES
from vtable_js import VTABLE_SCRIPTS, inventory
import vtable_playwright as vpw
from automation_profiles import profile_contract
from tool_metrics import instrument_tool, metrics_snapshot

# ============================================================================
#  运行数据(表单提交落盘)
# ============================================================================

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
EXEC_LOG_PATH = DATA_DIR / "exec_log.json"
MOLD_LOG_PATH = DATA_DIR / "mold_submissions.json"


def append_json(path: Path, payload: dict) -> None:
    """把一条记录追加到 data/ 下的 JSON 文件。"""
    records = []
    if path.exists():
        records = json.loads(path.read_text(encoding="utf-8"))
    records.append(payload)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cases() -> list[dict]:
    return list(TEST_CASES)


# ============================================================================
#  FastMCPApp:UI 与后端工具分离(表单提交 -> CallTool 回调)
# ============================================================================

app = FastMCPApp("vtable_app")


@app.tool()
def execute_test_case(case_id: str, executor: str) -> dict:
    """
    执行一条调拨订单测试用例(演示环境为模拟执行)。
    真实项目中,此处可注入 vtable://js/ 下的脚本驱动浏览器 VTable 实例。
    """
    cases = {c["用例编号"]: c for c in load_cases()}
    case = cases.get(case_id)
    if not case:
        return {"status": "failed", "message": f"用例 {case_id} 不存在"}

    # 模拟执行:按优先级给通过率
    passed = random.random() > 0.2
    result = {
        "status": "passed" if passed else "failed",
        "case_id": case_id,
        "title": case["用例标题"],
        "executor": executor,
        "executed_at": "2026-08-09 15:30:00",
    }
    append_json(EXEC_LOG_PATH, result)
    return result


@app.tool()
def save_mold_master(
    mold_code: str,
    mold_name: str,
    mold_type: str,
    mold_status: str,
    purchase_date: str,
    remark: str,
) -> dict:
    """保存模具主数据表单(演示:落盘到 data/mold_submissions.json)。"""
    record = {
        "mold_code": mold_code,
        "mold_name": mold_name,
        "mold_type": mold_type,
        "mold_status": mold_status,
        "purchase_date": purchase_date,
        "remark": remark,
    }
    append_json(MOLD_LOG_PATH, record)
    return {"status": "saved", "record": record}


@app.ui()
def case_execution_panel() -> PrefabApp:
    """用例执行台:选择用例 -> 提交 -> 后端工具执行 -> 展示结果。"""
    options = [
        SelectOption(label=f"{c['用例编号']} | {c['用例标题']}", value=c["用例编号"])
        for c in load_cases()
    ]
    with PrefabApp(title="调拨订单用例执行台") as view:
        with Card():
            with CardHeader():
                CardTitle("调拨订单用例执行台")
                CardDescription(
                    "选择测试用例并填写执行人,提交后经 CallTool 调用后端 execute_test_case"
                )
            with CardContent():
                with Form(
                    on_submit=CallTool(
                        "execute_test_case",
                        arguments={
                            "case_id": "{{ case_id }}",
                            "executor": "{{ executor }}",
                        },
                    )
                ):
                    Select(
                        name="case_id",
                        placeholder="选择要执行的用例…",
                        required=True,
                        children=options,
                    )
                    Input(
                        name="executor",
                        placeholder="执行人(如 Antigravity)",
                        required=True,
                    )
                    Button("开始执行", buttonType="submit")
    return view


@app.ui()
def mold_master_entry() -> PrefabApp:
    """模具主数据录入:由内置样例数据驱动的动态表单。"""
    fields = []
    for item in MOLD_MASTER_FIELDS:
        label, ftype, value = item["label"], item["type"], item["value"]
        fname = MOLD_FIELD_NAMES.get(label)
        if fname is None:
            continue
        if ftype == "select":
            fields.append(
                Select(
                    name=fname,
                    placeholder=f"请选择{label}",
                    required=True,
                    children=[
                        SelectOption(label=value, value=value),
                        SelectOption(label="其他", value="其他"),
                    ],
                )
            )
        elif ftype == "date":
            fields.append(Input(name=fname, input_type="date", value=value, required=True))
        else:
            if label == "备注":
                fields.append(Textarea(name=fname, placeholder=value, rows=3))
            else:
                fields.append(Input(name=fname, placeholder=value, required=True))

    with PrefabApp(title="模具主数据录入") as view:
        with Card():
            with CardHeader():
                CardTitle("模具主数据录入")
                CardDescription(
                    "由内置样例数据驱动,提交后调用后端 save_mold_master"
                )
            with CardContent():
                with Form(
                    on_submit=CallTool(
                        "save_mold_master",
                        arguments={
                            fname: "{{ " + fname + " }}"
                            for fname in MOLD_FIELD_NAMES.values()
                        },
                    )
                ):
                    Column(children=[*fields, Button("提交保存", buttonType="submit")])
    return view


# ============================================================================
#  主服务器:FastMCPApp + Providers + Interactive Tools + VTable 资源
# ============================================================================

mcp = FastMCP("vtable-mcp", providers=[app])

mcp.add_provider(Approval(title="确认执行该测试用例?"))
mcp.add_provider(Choice())
mcp.add_provider(FileUpload())
mcp.add_provider(GenerativeUI())


# ---------------------- VTable JS 脚本资源(内化) ----------------------


@mcp.resource("vtable://js/index")
def vtable_js_inventory() -> str:
    """VTable JS 脚本目录:所有脚本名与说明(JSON)。"""
    return json.dumps(inventory(), ensure_ascii=False, indent=2)


@mcp.resource("vtable://js/{name}")
def vtable_js_script(name: str) -> str:
    """按名称读取内化的 VTable JS 脚本(见 vtable://js/index 目录)。"""
    script = VTABLE_SCRIPTS.get(name)
    if script is None:
        raise ValueError(f"未知脚本: {name}。可用: {', '.join(VTABLE_SCRIPTS)}")
    return script


# ---------------------- Interactive Tools ----------------------


@mcp.tool(app=True)
def test_case_table() -> DataTable:
    """调拨订单测试用例总表:内置样例数据,支持搜索/排序/分页。"""
    columns = [
        DataTableColumn(key="用例编号", header="用例编号", sortable=True),
        DataTableColumn(key="优先级", header="优先级", sortable=True, width="80"),
        DataTableColumn(key="一级模块", header="一级模块"),
        DataTableColumn(key="二级模块", header="二级模块"),
        DataTableColumn(key="用例标题", header="用例标题", sortable=True),
        DataTableColumn(key="测试类型", header="测试类型"),
        DataTableColumn(key="测试结果", header="测试结果", width="90"),
        DataTableColumn(key="执行人", header="执行人", width="110"),
    ]
    return DataTable(
        columns=columns,
        rows=load_cases(),
        search=True,
        paginated=True,
        pageSize=10,
    )


@mcp.tool(app=True)
def vtable_engine_dashboard() -> PrefabApp:
    """VTable 引擎概览仪表盘:模拟表格实例状态 + 用例分布图。"""
    cases = load_cases()
    prio_counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for c in cases:
        prio_counts[c["优先级"]] = prio_counts.get(c["优先级"], 0) + 1

    with PrefabApp(title="VTable 引擎概览") as view:
        Dashboard(
            children=[
                Metric(
                    label="recordsCount",
                    value="1,024",
                    description="当前页 VTable 数据量(模拟)",
                ),
                Metric(
                    label="rowCount × colCount",
                    value="64 × 16",
                    description="虚拟表格行列规模(模拟)",
                ),
                Metric(
                    label="测试用例总数",
                    value=str(len(cases)),
                    description="内置样例数据",
                ),
                Metric(
                    label="P0 用例",
                    value=str(prio_counts.get("P0", 0)),
                    description="最高优先级",
                    delta="+1",
                ),
            ]
        )
        Column(
            children=[
                Text(content="各优先级测试用例分布", bold=True),
                BarChart(
                    data=[
                        {"prio": k, "count": v}
                        for k, v in sorted(prio_counts.items())
                    ],
                    series=[ChartSeries(data_key="count", name="用例数")],
                ),
            ]
        )
    return view


@mcp.tool(app=True)
def mold_master_view() -> PrefabApp:
    """模具主数据只读视图:渲染全部字段(内置样例)。"""
    items = []
    for item in MOLD_MASTER_FIELDS:
        label, ftype, value = item["label"], item["type"], item["value"]
        icon = {"textbox": "📝", "select": "🔽", "date": "📅"}.get(ftype, "•")
        items.append(f"{icon} {label}: {value}")

    with PrefabApp(title="模具主数据(只读)") as view:
        with Card():
            with CardHeader():
                CardTitle("模具主数据")
                CardDescription(f"共 {len(items)} 个字段(内置样例数据)")
            with CardContent():
                for line in items:
                    Text(content=line)
    return view


# ---------------------- Playwright 浏览器交互工具 ----------------------
#
# 设计遵循官方 Playwright MCP 范式 + Playwright 1.60/1.62 新特性:
#   AI 语义之眼(aria_snapshot mode='ai' + boxes)→ 先看全局(table_meta /
#   cells_read)→ 语义目标输入 + 确定性坐标解析 + trusted 真实输入 → 回读验证;
#   文件拖放走 Locator.drop(position= 由 VTable API 换算);DOM 点击支持
#   get_by_role(description=) 消歧。
# 依赖 playwright(可选,uv sync --extra browser);未安装时工具返回可操作报错。


@mcp.tool()
@instrument_tool
async def browser_open(url: str, headless: bool = True) -> dict:
    """打开 Playwright 浏览器并导航到目标页面(后续工具复用同一浏览器)。"""
    return await vpw.open_url(url, headless=headless)


@mcp.tool()
@instrument_tool
async def browser_start(
    port: int = 9222,
    headless: bool = False,
    executable_path: str | None = None,
    user_data_dir: str | None = None,
    timeout_ms: int = 15_000,
) -> dict:
    """在指定端口启动受管 Chrome，并自动通过 CDP 接管。"""
    return await vpw.launch_chrome(
        port=port,
        headless=headless,
        executable_path=executable_path,
        user_data_dir=user_data_dir,
        timeout_ms=timeout_ms,
    )


@mcp.tool()
@instrument_tool
async def browser_connect(cdp_url: str | None = None, port: int = 9222) -> dict:
    """经 CDP 连接一个已运行的浏览器，默认连接 9222 端口。

    复用外部浏览器与其已打开的 VTable 页面(含页面内的实例),无需重新导航;
    vtable_cell_click / ui_snapshot 等工具直接驱动该页面。
    关闭时仅断开连接,不关闭外部浏览器进程。
    """
    return await vpw.connect_browser(cdp_url, port=port)


@mcp.tool()
@instrument_tool
async def browser_session(
    action: Literal["list", "create", "select", "save", "close"] = "list",
    session_id: str | None = None,
    name: str | None = None,
    storage_state_path: str | None = None,
) -> dict:
    """管理隔离 BrowserContext 会话，支持账号 Cookie 环境切换和 storage state 持久化。"""
    return await vpw.browser_session(
        action=action,
        session_id=session_id,
        name=name,
        storage_state_path=storage_state_path,
    )


@mcp.tool()
@instrument_tool
async def browser_pages() -> dict:
    """列出所有 BrowserContext 的标签页及稳定 page_id，并标记当前选中页。"""
    return await vpw.list_pages()


@mcp.tool()
@instrument_tool
async def browser_select_page(page_id: str) -> dict:
    """显式选中一个 page_id；后续页面、iframe、浮层和 VTable 工具固定使用该页。"""
    return await vpw.select_page(page_id)


@mcp.tool()
@instrument_tool
async def browser_close() -> dict:
    """关闭 Playwright 浏览器,释放资源。

    CDP 连接的外部浏览器只断开连接,受管 Chrome 和 Playwright 浏览器则关闭。
    """
    return await vpw.close_browser()


@mcp.tool()
@instrument_tool
async def vtable_cell_info(col: int, row: int) -> dict:
    """读取 VTable 单元格信息:值/行为分类/编辑能力/中心点/是否在视口。

    交互前后各调一次,让 AI 确认目标与结果(Playwright MCP 的验证回路思想)。
    """
    return await vpw.cell_info(col, row)


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
    return await vpw.click_cell(
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
async def vtable_cell_resolve(
    field: str, record_index: int | list[int]
) -> dict:
    """用 VTable 内部 API 将业务字段和记录索引解析为单元格地址。

    优先调用 getCellAddrByFieldRecord，旧版本实例才回退到
    getTableIndexByField + getTableIndexByRecordIndex。不会扫描 DOM 或猜测坐标。
    """
    return await vpw.resolve_vtable_cell(field, record_index)


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
    return await vpw.click_vtable_cell_by_field(
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
async def ui_click(
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    x: float | None = None,
    y: float | None = None,
    frame: str | None = None,
    timeout_ms: float = 3_000,
    observe_after: bool = True,
    settle_ms: int = 300,
    max_results: int = 20,
    analysis_id: str | None = None,
    expect_input: bool = False,
) -> dict:
    """统一点击页面控件，并即时返回 Portal、提示和聚焦浮层。

    可同时传多个来自分析结果的候选定位；执行顺序固定为 CSS → AX role/name
    (可带 description) → XPath → text/placeholder → 顶层视口绝对坐标。坐标仅作为
    最终回退，且传 analysis_id 时会拒绝陈旧的 VTable 分析坐标。
    """
    return await vpw.dom_interact(
        "click",
        name=name,
        role=role,
        description=description,
        css=css,
        xpath=xpath,
        text=text,
        placeholder=placeholder,
        x=x,
        y=y,
        frame=frame,
        timeout_ms=timeout_ms,
        observe_after=observe_after,
        settle_ms=settle_ms,
        max_results=max_results,
        analysis_id=analysis_id,
        expect_input=expect_input,
    )


@mcp.tool()
@instrument_tool
async def overlay_scan(
    max_results: int = 20, scope: str = "active"
) -> dict:
    """扫描当前页面范围内可见的 Ant Design Portal / ARIA 浮层。

    默认 scope=active 只扫描主文档和当前激活 iframe；scope=all 才扫描所有 iframe。
    返回每个浮层的 kind、文本、role、定位摘要、可见性、box 以及所属 frame。
    对短暂 message/toast，请改用 `ui_click` 或
    `vtable_cell_click(observe_after=True)`，以免在单次静态扫描前消失。
    """
    return await vpw.scan_overlays(max_results=max_results, scope=scope)


@mcp.tool()
@instrument_tool
async def ui_page_context(max_results: int = 10) -> dict:
    """返回当前页面、活动 iframe 和聚焦浮层的紧凑上下文。

    这是 AI 每次准备下一步交互时的低 token 入口；只有需要详细控件树时
    才继续调用 ui_snapshot。
    """
    return await vpw.page_context(max_results=max_results)


@mcp.tool()
@instrument_tool
async def ui_analyze_scope(
    max_controls: int = 40, max_overlays: int = 10
) -> dict:
    """只分析当前活动页面范围或所聚焦浮层内的可操作控件。

    有 Modal/Drawer/Dropdown/Popover 时裁剪底层页面；否则只扫描顶层当前文档与
    激活的 AntD Tab iframe。结果是紧凑 role/name/CSS 定位清单，不展开整页 DOM。
    """
    return await vpw.analyze_scope(
        max_controls=max_controls, max_overlays=max_overlays
    )


@mcp.tool()
@instrument_tool
async def overlay_observe(
    settle_ms: int = 300, stop: bool = True, max_results: int = 20
) -> dict:
    """在限定窗口内收集 Ant Design Portal/ARIA 浮层事件，覆盖全部 iframe。

    适合已由其他工具或人工操作触发页面交互后的诊断；默认在取样后停止监听。
    """
    return await vpw.observe_overlays(
        settle_ms=settle_ms, stop=stop, max_results=max_results
    )


@mcp.tool()
@instrument_tool
async def ui_interact(
    action: str,
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    value: str | None = None,
    key: str | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    timeout_ms: float = 3_000,
    observe_after: bool = True,
    settle_ms: int = 300,
    max_results: int = 20,
    analysis_id: str | None = None,
    expect_input: bool = False,
) -> dict:
    """在当前页面/活动 iframe 中执行统一 DOM 交互并返回聚焦浮层结果。

    分析器返回 CSS 时优先 CSS；否则按 AX role/name/description、XPath、text/
    placeholder 依次尝试。x/y 是顶层 viewport 绝对 CSS 像素，只在前述候选都无法
    解析时作为可信点击回退。坐标应直接取自 `vtable_analysis`，带 analysis_id 会在
    执行前校验页面、iframe、滚动和布局。expect_input=True 时会验证
    本次交互后是否真的出现并聚焦 input/textarea/contenteditable。未显式指定
    frame 时优先当前激活的 AntD Tab iframe，再回退顶层文档。分析结果中的
    frame="active" / "top" 可固定上下文。默认点击后立即
    观察 Portal、消息、下拉和通知，限制 max_results 以控制 MCP token。
    """
    return await vpw.dom_interact(
        action,
        role=role,
        name=name,
        description=description,
        text=text,
        placeholder=placeholder,
        css=css,
        xpath=xpath,
        x=x,
        y=y,
        value=value,
        key=key,
        frame=frame,
        in_iframe=in_iframe,
        timeout_ms=timeout_ms,
        observe_after=observe_after,
        settle_ms=settle_ms,
        max_results=max_results,
        analysis_id=analysis_id,
        expect_input=expect_input,
    )


@mcp.tool()
@instrument_tool
async def ui_snapshot(
    selector: str | None = None,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
) -> dict:
    """抓取页面 aria 快照(mode='ai' + boxes),给 AI 一张"语义之眼"。

    官方 Playwright MCP 范式:把 accessibility 树(含 [ref=xx] 引用和 [box=x,y,w,h]
    视口坐标)喂给 AI。VTable 本体是 canvas(单元格不进 a11y 树,仍走确定性几何定位),
    但工具栏/弹窗/编辑器输入框都在树里 —— 交互前先读快照,再决定点哪个。
    selector 非空时只快照该选择器命中的子树。

    frame=None → 主页面;frame="active" → 当前激活的 AntD Tab iframe;
    frame="vtable" → 自动定位含表格的 iframe;
    其它值按 iframe name 或 URL 子串匹配(如 "application" / "scm-spo")。
    """
    return await vpw.dom_snapshot(selector, frame=frame, depth=depth, boxes=boxes, ai_mode=ai_mode)


@mcp.tool()
@instrument_tool
async def ui_screenshot(
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    padding: float = 0,
    image_format: str = "png",
    quality: int | None = None,
    timeout_ms: float = 3_000,
    max_bytes: int = 2_000_000,
) -> dict:
    """截取指定 DOM 元素或顶层 viewport 区域并返回受限大小的图像。

    元素定位顺序与 ui_interact 相同：CSS → AX role/name/description → XPath →
    text/placeholder。frame 未指定时优先活动 iframe。若没有可用定位器，可传
    x/y/width/height 使用顶层 viewport CSS 像素矩形；截图不会静默把 iframe 内坐标
    当成顶层坐标。结果包含 base64 图像、裁剪框、frame、定位来源和摘要哈希。
    """
    return await vpw.screenshot_element(
        role=role,
        name=name,
        description=description,
        text=text,
        placeholder=placeholder,
        css=css,
        xpath=xpath,
        x=x,
        y=y,
        width=width,
        height=height,
        frame=frame,
        in_iframe=in_iframe,
        padding=padding,
        image_format=image_format,
        quality=quality,
        timeout_ms=timeout_ms,
        max_bytes=max_bytes,
    )


@mcp.tool()
@instrument_tool
async def vtable_meta() -> dict:
    """读取 VTable 规模/冻结行列/主题等元数据(防御性取值)。

    AI 先拿到 rowCount/colCount/frozenRowCount,再规划批量读取范围与滚动策略。
    """
    return await vpw.table_meta()


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
    """
    return await vpw.vtable_analysis(
        max_columns=max_columns,
        sample_rows=sample_rows,
        mode=mode,
        fields=fields,
        include_values=include_values,
        visible_only=visible_only,
        table_index=table_index,
    )


@mcp.tool()
@instrument_tool
async def vtable_read_cells(col0: int, row0: int, col1: int, row1: int) -> dict:
    """批量读取矩形区域(col0,row0)-(col1,row1)单元格值(行优先)。"""
    return await vpw.cells_read(col0, row0, col1, row1)


@mcp.tool()
@instrument_tool
async def vtable_drop_files(
    col: int,
    row: int,
    files: list[str],
    data: dict[str, str] | None = None,
) -> dict:
    """把服务端本地文件拖放到指定 VTable 单元格(Playwright 1.60 Locator.drop)。

    落点由 VTable getCellRelativeRect 换算成 .vtable 容器相对坐标,精确命中目标格。
    files 为服务端文件路径列表(如 data/demo.xlsx);data 可附带剪贴板式键值。
    """
    return await vpw.drop_files(col, row, files, data=data)


@mcp.tool()
async def ui_profile() -> dict:
    """返回当前页面 Profile、定位顺序和 VTable 点击验证顺序。"""
    return {"status": "ok", **profile_contract()}


@mcp.tool()
async def automation_metrics(limit: int = 50) -> dict:
    """返回浏览器侧工具的近期耗时、响应体积和上下文 token 估算。"""
    return metrics_snapshot(limit)


def main() -> None:
    """CLI 入口;默认通过 stdio 提供 MCP 协议。"""
    # Keep the direct executable entry point deterministic and protocol-clean.
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
