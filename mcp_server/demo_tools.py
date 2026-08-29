"""Interactive demo tools exposed as FastMCP Apps."""

from __future__ import annotations

from fastmcp import FastMCP
from prefab_ui import PrefabApp
from prefab_ui.components import (
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
    Column,
    Dashboard,
    DataTable,
    DataTableColumn,
    Metric,
    Text,
)
from prefab_ui.components.charts import BarChart, ChartSeries

from sample_data import MOLD_MASTER_FIELDS
from .app_ui import load_cases


def create_server() -> FastMCP:
    mcp = FastMCP("Demo Apps")

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
            columns=columns, rows=load_cases(), search=True, paginated=True, pageSize=10
        )

    @mcp.tool(app=True)
    def vtable_engine_dashboard() -> PrefabApp:
        """VTable 引擎概览仪表盘:模拟表格实例状态 + 用例分布图。"""
        cases = load_cases()
        counts = {
            priority: sum(1 for case in cases if case["优先级"] == priority)
            for priority in ("P0", "P1", "P2", "P3")
        }
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
                        value=str(counts["P0"]),
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
                            {"prio": key, "count": value}
                            for key, value in sorted(counts.items())
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
            icon = {"textbox": "📝", "select": "🔽", "date": "📅"}.get(
                item["type"], "•"
            )
            items.append(f"{icon} {item['label']}: {item['value']}")
        with PrefabApp(title="模具主数据(只读)") as view:
            with Card():
                with CardHeader():
                    CardTitle("模具主数据")
                    CardDescription(f"共 {len(items)} 个字段(内置样例数据)")
                with CardContent():
                    for line in items:
                        Text(content=line)
        return view

    return mcp
