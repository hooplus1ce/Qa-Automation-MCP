"""Unit tests for network listening and packet inspection."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from qa_automation.net import (
    BODY_LIMIT,
    PacketRecord,
    PageNetworkListener,
    _truncate_body,
    _truncate_text,
    net_listen_snapshot,
)


class NetToolsTests(unittest.TestCase):
    def test_truncate_text_and_body(self) -> None:
        short_str = "hello world"
        self.assertEqual(_truncate_text(short_str, limit=50), short_str)

        long_str = "a" * (BODY_LIMIT + 100)
        truncated = _truncate_text(long_str, limit=BODY_LIMIT)
        self.assertTrue(truncated.endswith("…(已截断)"))
        self.assertEqual(len(truncated), BODY_LIMIT + len("…(已截断)"))

        # 字典载荷截断
        dict_data = {"key": "x" * 5000}
        truncated_dict = _truncate_body(dict_data, limit=100)
        self.assertTrue(str(truncated_dict).endswith("…(已截断)"))

    def test_packet_record_serialization(self) -> None:
        pkt = PacketRecord(
            url="https://api.example.com/v1/orders?type=1",
            method="POST",
            resource_type="fetch",
            status=200,
            status_text="OK",
            request_headers={"Authorization": "Bearer token"},
            request_body={"order_id": 123},
            response_headers={"Content-Type": "application/json"},
            response_body={"success": True},
            duration_ms=45.2,
        )
        d = pkt.to_dict()
        self.assertEqual(d["url"], "https://api.example.com/v1/orders?type=1")
        self.assertEqual(d["method"], "POST")
        self.assertEqual(d["status"], 200)
        self.assertEqual(d["duration_ms"], 45.2)

    def test_listener_filter_matching(self) -> None:
        mock_page = MagicMock()
        listener = PageNetworkListener(mock_page)
        listener.configure(
            urls=["/api/v1/users", "orders"],
            methods=["POST", "PUT"],
            res_types=["fetch"],
        )

        # 匹配
        self.assertTrue(
            listener._matches_filter("https://example.com/api/v1/users", "POST", "fetch")
        )
        self.assertTrue(
            listener._matches_filter("https://example.com/orders/create", "PUT", "fetch")
        )

        # 方法不匹配
        self.assertFalse(
            listener._matches_filter("https://example.com/api/v1/users", "GET", "fetch")
        )

        # 资源类型不匹配
        self.assertFalse(
            listener._matches_filter("https://example.com/api/v1/users", "POST", "document")
        )

        # URL 不匹配
        self.assertFalse(
            listener._matches_filter("https://example.com/other/path", "POST", "fetch")
        )


class NetworkSnapshotCompletenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_uses_lookahead_and_reports_more_packets(self) -> None:
        packets = [
            SimpleNamespace(url=f"https://example.test/{i}", to_dict=lambda i=i: {"id": i})
            for i in range(3)
        ]
        listener = SimpleNamespace(packets=packets)
        with (
            patch("qa_automation.net.current_page", new=AsyncMock(return_value=object())),
            patch("qa_automation.net.get_or_create_listener", new=AsyncMock(return_value=listener)),
        ):
            first = await net_listen_snapshot(limit=1)
            second = await net_listen_snapshot(limit=1, offset=1)

        self.assertEqual(first["matched_count"], 1)
        self.assertEqual(first["packets"], [{"id": 2}])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 1)
        self.assertFalse(first["coverage"]["complete_for_scope"])
        self.assertEqual(second["packets"], [{"id": 1}])
        self.assertTrue(second["has_more"])
        self.assertEqual(second["next_offset"], 2)

    async def test_snapshot_reports_complete_when_queue_is_exhausted(self) -> None:
        listener = SimpleNamespace(
            packets=[SimpleNamespace(url="https://example.test/1", to_dict=lambda: {"id": 1})]
        )
        with (
            patch("qa_automation.net.current_page", new=AsyncMock(return_value=object())),
            patch("qa_automation.net.get_or_create_listener", new=AsyncMock(return_value=listener)),
        ):
            result = await net_listen_snapshot(limit=20)

        self.assertFalse(result["has_more"])
        self.assertEqual(result["total_count"], 1)
        self.assertIsNone(result["next_offset"])
        self.assertTrue(result["coverage"]["complete_for_scope"])


if __name__ == "__main__":
    unittest.main()
