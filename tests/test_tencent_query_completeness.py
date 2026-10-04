"""MCP paging contract for complete Tencent Docs row queries."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client

import qa_automation.mcp.servers.tencent_docs as tencent_docs_module


class TencentQueryCompletenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_lookahead_exposes_next_offset_and_final_page(self) -> None:
        rows = [{"row_id": "R1"}, {"row_id": "R2"}, {"row_id": "R3"}]
        manager = tencent_docs_module.tencent_sheet_manager
        query_mock = MagicMock(side_effect=[rows, rows[2:]])
        with (
            patch.object(manager, "query_rows", query_mock),
            patch.object(
                manager,
                "resolve_sheet",
                return_value=("sheet-1", {"sheet_name": "Demo"}),
            ),
        ):
            async with Client(tencent_docs_module.create_server()) as client:
                first = await client.call_tool(
                    "tencent_sheet_query_rows",
                    {"sheet_name": "Demo", "limit": 2, "offset": 0},
                )
                second = await client.call_tool(
                    "tencent_sheet_query_rows",
                    {"sheet_name": "Demo", "limit": 2, "offset": 2},
                )

        first_page = first.structured_content
        second_page = second.structured_content
        self.assertEqual(first_page["count"], 2)
        self.assertTrue(first_page["has_more"])
        self.assertEqual(first_page["next_offset"], 2)
        self.assertFalse(first_page["coverage"]["complete_for_scope"])
        self.assertEqual([row["row_id"] for row in first_page["items"]], ["R1", "R2"])
        self.assertFalse(second_page["has_more"])
        self.assertIsNone(second_page["next_offset"])
        self.assertTrue(second_page["coverage"]["complete_for_scope"])
        self.assertEqual([row["row_id"] for row in second_page["items"]], ["R3"])
        self.assertEqual(query_mock.call_args_list[0].kwargs["limit"], 3)
        self.assertEqual(query_mock.call_args_list[1].kwargs["offset"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
