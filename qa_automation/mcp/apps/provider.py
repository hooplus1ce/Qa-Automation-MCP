"""FastMCP Apps provider and its app-visible backend tools."""

from __future__ import annotations

import json
import os
import random
import threading
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCPApp
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
    Form,
    Input,
    Select,
    SelectOption,
    Textarea,
)

from ...workspace import data_dir
from .sample_data import MOLD_FIELD_NAMES, MOLD_MASTER_FIELDS, TEST_CASES

DATA_DIR = data_dir()
EXEC_LOG_PATH = DATA_DIR / "exec_log.json"
MOLD_LOG_PATH = DATA_DIR / "mold_submissions.json"


_APPEND_LOCK = threading.Lock()


def _append_json(path: Path, payload: dict) -> None:
    """加锁读-改-写,并通过临时文件原子替换,避免并发丢数据或半写损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK:
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        records.append(payload)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)


def load_cases() -> list[dict]:
    return list(TEST_CASES)


def create_app() -> FastMCPApp:
    """Create the FastMCP Apps provider so it can be injected into the root server."""
    app = FastMCPApp("qa_automation_app")

    @app.tool()
    def execute_test_case(case_id: str, executor: str) -> dict:
        """执行一条调拨订单测试用例(演示环境为模拟执行)。

        真实项目中,此处可注入 vtable://js/ 下的脚本驱动浏览器 VTable 实例。
        """
        cases = {case["用例编号"]: case for case in load_cases()}
        case = cases.get(case_id)
        if not case:
            return {"status": "failed", "message": f"用例 {case_id} 不存在"}
        result = {
            "status": "passed" if random.random() > 0.2 else "failed",
            "case_id": case_id,
            "title": case["用例标题"],
            "executor": executor,
            "executed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _append_json(EXEC_LOG_PATH, result)
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
        """保存模具主数据表单(演示:落盘到配置的 UI 自动化数据目录)。"""
        record = {
            "mold_code": mold_code,
            "mold_name": mold_name,
            "mold_type": mold_type,
            "mold_status": mold_status,
            "purchase_date": purchase_date,
            "remark": remark,
        }
        _append_json(MOLD_LOG_PATH, record)
        return {"status": "saved", "record": record}

    @app.ui()
    def case_execution_panel() -> PrefabApp:
        """用例执行台:选择用例 -> 提交 -> 后端工具执行 -> 展示结果。"""
        options = [
            SelectOption(
                label=f"{case['用例编号']} | {case['用例标题']}", value=case["用例编号"]
            )
            for case in load_cases()
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
            label, field_type, value = item["label"], item["type"], item["value"]
            field_name = MOLD_FIELD_NAMES.get(label)
            if field_name is None:
                continue
            if field_type == "select":
                fields.append(
                    Select(
                        name=field_name,
                        placeholder=f"请选择{label}",
                        required=True,
                        children=[
                            SelectOption(label=value, value=value),
                            SelectOption(label="其他", value="其他"),
                        ],
                    )
                )
            elif field_type == "date":
                fields.append(
                    Input(
                        name=field_name, input_type="date", value=value, required=True
                    )
                )
            elif label == "备注":
                fields.append(Textarea(name=field_name, placeholder=value, rows=3))
            else:
                fields.append(Input(name=field_name, placeholder=value, required=True))
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
                                name: "{{ " + name + " }}"
                                for name in MOLD_FIELD_NAMES.values()
                            },
                        )
                    ):
                        Column(
                            children=[*fields, Button("提交保存", buttonType="submit")]
                        )
        return view

    return app
