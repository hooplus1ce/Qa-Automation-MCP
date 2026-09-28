"""Unit tests for network listening and packet inspection."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from qa_automation.net import (
    BODY_LIMIT,
    PacketRecord,
    PageNetworkListener,
    _truncate_body,
    _truncate_text,
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
            listener._matches_filter(
                "https://example.com/api/v1/users", "POST", "fetch"
            )
        )
        self.assertTrue(
            listener._matches_filter(
                "https://example.com/orders/create", "PUT", "fetch"
            )
        )

        # 方法不匹配
        self.assertFalse(
            listener._matches_filter(
                "https://example.com/api/v1/users", "GET", "fetch"
            )
        )

        # 资源类型不匹配
        self.assertFalse(
            listener._matches_filter(
                "https://example.com/api/v1/users", "POST", "document"
            )
        )

        # URL 不匹配
        self.assertFalse(
            listener._matches_filter(
                "https://example.com/other/path", "POST", "fetch"
            )
        )


if __name__ == "__main__":
    unittest.main()
