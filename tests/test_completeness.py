"""Contracts for completeness metadata on bounded tool responses."""

from __future__ import annotations

import unittest

from qa_automation.completeness import completeness_report


class CompletenessReportTests(unittest.TestCase):
    def test_complete_scope_is_explicit(self) -> None:
        result = completeness_report(
            scope={"kind": "table_page", "offset": 0},
            returned_count=4,
            total_count=4,
            limit=10,
            truncated=False,
        )

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["complete_for_scope"])
        self.assertFalse(result["has_more"])

    def test_partial_scope_infers_more_results(self) -> None:
        result = completeness_report(
            scope="overlay_list",
            returned_count=2,
            total_count=5,
            limit=2,
        )

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete_for_scope"])
        self.assertTrue(result["has_more"])

    def test_unknown_total_is_not_claimed_complete(self) -> None:
        result = completeness_report(
            scope="filtered_packet_list",
            returned_count=3,
            total_count=None,
            truncated=None,
            has_more=None,
        )

        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["complete_for_scope"])
        self.assertIsNone(result["has_more"])

    def test_truncation_is_partial_even_without_known_total(self) -> None:
        result = completeness_report(
            scope="aria_snapshot",
            returned_count=24000,
            limit=24000,
            truncated=True,
            reasons=["character_limit", "character_limit"],
            unit="characters",
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["has_more"])
        self.assertEqual(result["reasons"], ["character_limit"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
