"""Unit tests for AntV X6 canvas coordinate adapter and tool endpoints."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from qa_automation.components.x6 import X6Session, _match_node
from qa_automation.mcp.servers.x6 import create_server


class X6AdapterTests(unittest.TestCase):
    def test_session_coordinate_transformation(self) -> None:
        mock_page = MagicMock()
        mock_frame = MagicMock()
        session = X6Session(
            page=mock_page,
            frame=mock_frame,
            offset_x=150.0,
            offset_y=60.0,
        )

        # 验证内部局部坐标到顶层绝对视口坐标的换算
        vx, vy = session.to_viewport(50.0, 40.0)
        self.assertEqual(vx, 200.0)
        self.assertEqual(vy, 100.0)

    def test_match_node_criteria(self) -> None:
        nodes = [
            {"cellId": "node-1", "text": "开始节点"},
            {"cellId": "node-2", "text": "财务部门审批(李总)"},
            {"cellId": "node-3", "text": "结束"},
        ]

        # 1. cellId 精确匹配
        hit1 = _match_node(nodes, "node-2")
        self.assertIsNotNone(hit1)
        assert hit1 is not None
        self.assertEqual(hit1["cellId"], "node-2")

        # 2. 文本精确匹配
        hit2 = _match_node(nodes, "开始节点")
        self.assertIsNotNone(hit2)
        assert hit2 is not None
        self.assertEqual(hit2["cellId"], "node-1")

        # 3. 文本包含模糊匹配
        hit3 = _match_node(nodes, "财务部门")
        self.assertIsNotNone(hit3)
        assert hit3 is not None
        self.assertEqual(hit3["cellId"], "node-2")

        # 4. 未找到
        self.assertIsNone(_match_node(nodes, "不存在的节点"))

    def test_x6_mcp_server_tools_registered(self) -> None:
        server = create_server()
        tools = asyncio.run(server.list_tools())
        tool_names = {t.name for t in tools}

        expected_tools = {
            "x6_nodes",
            "x6_move_node",
            "x6_connect",
            "x6_click_node",
            "x6_delete_node",
        }
        self.assertTrue(expected_tools.issubset(tool_names))


if __name__ == "__main__":
    unittest.main()
