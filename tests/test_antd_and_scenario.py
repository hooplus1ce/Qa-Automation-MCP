"""Unit tests for Ant Design geometric helpers and declarative scenario engine."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from qa_automation.antd import _dropdown_score
from qa_automation.scenario import (
    _dig,
    _field,
    parse_scenario,
    run_scenario,
    substitute_vars,
)


class AntDAndScenarioTests(unittest.TestCase):
    def test_dropdown_score_matching(self) -> None:
        # 触发框：位于 (100, 200)，宽 150，高 30
        trigger = {"x": 100.0, "y": 200.0, "width": 150.0, "height": 30.0}

        # 正常下拉浮层：紧贴触发框下方，位于 (100, 232)，宽 150，高 120
        good_dd = {"x": 100.0, "y": 232.0, "width": 150.0, "height": 120.0}
        score = _dropdown_score(trigger, good_dd)
        self.assertIsNotNone(score)
        assert score is not None
        x_ratio, gap = score
        self.assertAlmostEqual(x_ratio, 1.0, places=2)
        self.assertAlmostEqual(gap, 2.0, places=2)

        # 远端浮层（例如顶部遗留的到达菜单浮层）：位于 (800, 50)，宽 200，高 200
        remote_dd = {"x": 800.0, "y": 50.0, "width": 200.0, "height": 200.0}
        score_remote = _dropdown_score(trigger, remote_dd)
        self.assertIsNotNone(score_remote)
        rx_ratio, rgap = score_remote
        self.assertEqual(rx_ratio, 0.0)  # 无水平重叠，低于 0.25 门槛
        self.assertLess(rx_ratio, 0.25)

    def test_scenario_parse_and_substitute(self) -> None:
        yaml_content = """
name: test_login_flow
variables:
  username: "admin"
  timeout: 5
steps:
  - step: "1. 登录系统"
    tool: "browser_login"
    args:
      user: "${username}"
      wait: "${timeout}"
    expect:
      status_ok: true
"""
        data = parse_scenario(yaml_content)
        self.assertEqual(data["name"], "test_login_flow")
        self.assertEqual(len(data["steps"]), 1)

        # 变量替换
        substituted = substitute_vars(
            {"user": "${username}", "path": "/home/${username}/docs"},
            {"username": "aps_operator"},
        )
        self.assertEqual(substituted["user"], "aps_operator")
        self.assertEqual(substituted["path"], "/home/aps_operator/docs")

    def test_scenario_dig_and_field(self) -> None:
        payload = {
            "status": "ok",
            "data": {
                "order_list": [
                    {"order_id": "OD-1001", "total": 99.5},
                    {"order_id": "OD-1002", "total": 199.0},
                ]
            },
        }
        self.assertEqual(_field(payload, "status"), "ok")
        self.assertEqual(_dig(payload, "data.order_list.0.order_id"), "OD-1001")
        self.assertEqual(_dig(payload, "data.order_list.1.total"), 199.0)
        self.assertIsNone(_dig(payload, "data.order_list.5.order_id"))
        self.assertIsNone(_dig(payload, "non.existent.path"))

    def test_scenario_execution_flow(self) -> None:
        mock_client = MagicMock()
        mock_res1 = MagicMock()
        mock_res1.data = {"ok": True, "token": "mock-token-abc", "message": "登录成功"}
        mock_res1.content = []

        mock_res2 = MagicMock()
        mock_res2.data = {"ok": True, "record_id": 8888, "message": "保存成功"}
        mock_res2.content = []

        mock_client.call_tool = AsyncMock(side_effect=[mock_res1, mock_res2])

        scenario_def = {
            "name": "mock_crud_flow",
            "variables": {"user": "tester"},
            "steps": [
                {
                    "step": "登录",
                    "tool": "browser_login",
                    "args": {"username": "${user}"},
                    "expect": {"status_ok": True, "message_contains": "成功"},
                    "save": {"auth_token": "token"},
                },
                {
                    "step": "保存记录",
                    "tool": "ui_click",
                    "args": {"token": "${auth_token}"},
                    "expect": {"status_ok": True},
                },
            ],
        }

        result = asyncio.run(run_scenario(scenario_def, mock_client))
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_steps"], 2)
        self.assertEqual(result["passed_steps"], 2)
        self.assertIsNone(result["failed_step"])
        self.assertEqual(result["variables"]["auth_token"], "mock-token-abc")


if __name__ == "__main__":
    unittest.main()
