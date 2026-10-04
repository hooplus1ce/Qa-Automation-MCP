"""Unit tests for Ant Design geometric helpers and declarative scenario engine."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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


    def test_guarantee_dropdown_closed_stages(self) -> None:
        from qa_automation.antd import _guarantee_dropdown_closed

        mock_page = AsyncMock()
        mock_target = AsyncMock()
        mock_trigger = MagicMock()
        mock_dd = AsyncMock()

        # 场景 1：浮层原本就不可见
        mock_dd.is_visible.return_value = False
        res1 = asyncio.run(_guarantee_dropdown_closed(mock_page, mock_target, mock_trigger, mock_dd))
        self.assertTrue(res1)
        mock_page.keyboard.press.assert_not_called()

        # 场景 2：浮层原本可见，按 ESC 后关闭
        mock_dd.is_visible.side_effect = [True, False]
        res2 = asyncio.run(_guarantee_dropdown_closed(mock_page, mock_target, mock_trigger, mock_dd))
        self.assertTrue(res2)
        mock_page.keyboard.press.assert_awaited_with("Escape")

        # 场景 3：ESC 未能关闭，点击触发框后关闭
        mock_page.keyboard.press.reset_mock()
        mock_arrow = AsyncMock()
        mock_arrow.is_visible.return_value = True
        mock_trigger.locator.return_value.first = mock_arrow
        mock_dd.is_visible.side_effect = [True, True, False]
        res3 = asyncio.run(_guarantee_dropdown_closed(mock_page, mock_target, mock_trigger, mock_dd))
        self.assertTrue(res3)
        mock_arrow.click.assert_awaited()

    def test_stable_locator_click_dismisses_obscuring_dropdown(self) -> None:
        from qa_automation.interaction import _stable_locator_click

        mock_page = AsyncMock()
        mock_target = AsyncMock()

        # 模拟目标保存按钮位于 (100, 500)，宽 80，高 30
        mock_target.bounding_box.side_effect = [
            {"x": 100.0, "y": 500.0, "width": 80.0, "height": 30.0},
            {"x": 100.0, "y": 500.0, "width": 80.0, "height": 30.0},
            {"x": 100.0, "y": 500.0, "width": 80.0, "height": 30.0},
        ]
        mock_target.is_enabled.return_value = True

        # 模拟页面上有展开的多选下拉浮层位于 (50, 400)，宽 300，高 200（正巧遮挡了 (140, 515) 的保存按钮）
        mock_floating = MagicMock()
        mock_floating.count = AsyncMock(return_value=1)
        mock_floating_dd = MagicMock()
        mock_floating_dd.bounding_box = AsyncMock(return_value={"x": 50.0, "y": 400.0, "width": 300.0, "height": 200.0})
        mock_floating.nth.return_value = mock_floating_dd
        mock_page.locator = MagicMock(return_value=mock_floating)

        with patch("qa_automation.interaction._stable_viewport_click", AsyncMock()) as mock_vp_click:
            asyncio.run(_stable_locator_click(mock_page, mock_target))
            # 必须检测到遮挡并自动派发 ESC
            mock_page.keyboard.press.assert_awaited_with("Escape")
            mock_vp_click.assert_awaited_once()

if __name__ == "__main__":
    unittest.main()
