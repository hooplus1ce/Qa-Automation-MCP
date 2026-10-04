"""Unit tests for VTable column interaction operations:

- Header text extraction & icon avoidance in vtable_analysis
- Separator extraction & divider coordinate accuracy
- reorder_column (column dragging with safe text targeting)
- resize_column (divider dragging with hover settle)
- autofit_columns (batch auto-resize with order protection)
- interaction_chain integration for reorder_column and resize_column
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import qa_automation as automation
from qa_automation.components.vtable.column_ops import (
    _reorder_column_impl,
    _resize_column_impl,
)


class VTableColumnOpsTests(unittest.IsolatedAsyncioTestCase):
    async def test_reorder_column_validates_inputs(self) -> None:
        with self.assertRaises(ValueError):
            await automation.reorder_column(from_col=None, to_col=1)
        with self.assertRaises(ValueError):
            await automation.reorder_column(from_col=0, to_col=None)

    async def test_resize_column_validates_inputs(self) -> None:
        with self.assertRaises(ValueError):
            await automation.resize_column(col=None, target_width=100)
        with self.assertRaises(ValueError):
            await automation.resize_column(col=0, target_width=None, delta_width=None)

    async def test_reorder_column_already_in_place(self) -> None:
        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        mock_frame.evaluate.return_value = {
            "ok": True,
            "status": "already-in-place",
            "srcCol": 1,
            "dstCol": 1,
            "fields": ["id", "name", "status"],
        }

        with (
            patch("qa_automation.components.vtable.column_ops._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.column_ops.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.column_ops._page_id", return_value="p-1"),
        ):
            result = await _reorder_column_impl(from_col=1, to_col=1)
            self.assertEqual(result["status"], "already-in-place")
            self.assertEqual(result["col"], 1)

    async def test_reorder_column_success_with_text_targeting(self) -> None:
        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        mock_probe = {
            "ok": True,
            "canvas": {"left": 20.0, "top": 50.0, "width": 800.0, "height": 400.0},
            "srcCol": 1,
            "dstCol": 3,
            "srcField": "name",
            "dstField": "status",
            "srcTitle": "姓名",
            "dstTitle": "状态",
            "beforeFields": ["id", "name", "code", "status"],
            "start": {"x": 120.0, "y": 15.0},  # Safe text center, avoiding sort/filter icons
            "end": {"x": 380.0, "y": 15.0},
        }

        mock_frame.evaluate.side_effect = [
            mock_probe,  # initial probe
            mock_probe,  # probe after ensure_visible
            ["id", "code", "status", "name"],  # after_fields
        ]

        with (
            patch("qa_automation.components.vtable.column_ops._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.column_ops.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.column_ops.ensure_cell_visible", AsyncMock(return_value=True)),
            patch("qa_automation.components.vtable.column_ops._frame_page_offset", AsyncMock(return_value={"x": 10.0, "y": 15.0})),
            patch("qa_automation.components.vtable.column_ops._page_viewport_size", AsyncMock(return_value={"width": 1280.0, "height": 800.0})),
            patch("qa_automation.components.vtable.column_ops._mouse_drag_impl", AsyncMock(return_value={"status": "dragged"})) as mock_drag,
            patch("qa_automation.components.vtable.column_ops._page_id", return_value="p-1"),
            patch("qa_automation.components.vtable.column_ops._frame_context_details", AsyncMock(return_value={"frame_id": "main"})),
        ):
            result = await _reorder_column_impl(from_field="name", to_field="status")

            self.assertEqual(result["status"], "reordered")
            self.assertEqual(result["from_col"], 1)
            self.assertEqual(result["to_col"], 3)
            self.assertEqual(result["from_field"], "name")
            self.assertEqual(result["to_field"], "status")
            # Verify coordinates incorporate frame offset (10, 15) + canvas (20, 50) + text relative (120, 15)
            # start: 10 + 20 + 120 = 150; 15 + 50 + 15 = 80
            self.assertEqual(result["start_point"], {"x": 150.0, "y": 80.0})
            # end: 10 + 20 + 380 = 410; 15 + 50 + 15 = 80
            self.assertEqual(result["end_point"], {"x": 410.0, "y": 80.0})

            mock_drag.assert_awaited_once()
            drag_kwargs = mock_drag.await_args.kwargs if mock_drag.await_args.kwargs else {}
            drag_args = mock_drag.await_args.args
            # Verify steps and hold_ms
            self.assertEqual(drag_kwargs.get("steps", drag_args[5] if len(drag_args) > 5 else 28), 28)

    async def test_resize_column_already_fit(self) -> None:
        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        mock_frame.evaluate.return_value = {
            "ok": True,
            "col": 2,
            "field": "age",
            "curWidth": 100,
            "border": {"x": 200.0, "y": 15.0, "draggable": True},
        }

        with (
            patch("qa_automation.components.vtable.column_ops._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.column_ops.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.column_ops._page_id", return_value="p-1"),
        ):
            result = await _resize_column_impl(col=2, target_width=100)
            self.assertEqual(result["status"], "already-fit")
            self.assertEqual(result["width"], 100)

    async def test_resize_column_skipped_frozen(self) -> None:
        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        mock_frame.evaluate.return_value = {
            "ok": True,
            "col": 5,
            "field": "action",
            "curWidth": 80,
            "border": {"x": 750.0, "y": 15.0, "draggable": False},  # In right-frozen deadzone
        }

        with (
            patch("qa_automation.components.vtable.column_ops._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.column_ops.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.column_ops._page_id", return_value="p-1"),
        ):
            result = await _resize_column_impl(col=5, delta_width=40)
            self.assertEqual(result["status"], "skipped-frozen")
            self.assertEqual(result["reason"], "border-in-frozen-deadzone")

    async def test_resize_column_success_with_divider_targeting(self) -> None:
        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        mock_probe = {
            "ok": True,
            "canvas": {"left": 10.0, "top": 20.0, "width": 800.0, "height": 400.0},
            "col": 1,
            "field": "desc",
            "curWidth": 120,
            "border": {"x": 220.0, "y": 14.0, "draggable": True},
        }

        mock_frame.evaluate.side_effect = [
            mock_probe,  # initial probe
            mock_probe,  # probe after ensure_visible
            160,         # after width
        ]

        with (
            patch("qa_automation.components.vtable.column_ops._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.column_ops.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.column_ops.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.column_ops.ensure_cell_visible", AsyncMock(return_value=True)),
            patch("qa_automation.components.vtable.column_ops._frame_page_offset", AsyncMock(return_value={"x": 5.0, "y": 10.0})),
            patch("qa_automation.components.vtable.column_ops._page_viewport_size", AsyncMock(return_value={"width": 1280.0, "height": 800.0})),
            patch("qa_automation.components.vtable.column_ops._mouse_drag_impl", AsyncMock(return_value={"status": "dragged"})) as mock_drag,
            patch("qa_automation.components.vtable.column_ops._page_id", return_value="p-1"),
            patch("qa_automation.components.vtable.column_ops._frame_context_details", AsyncMock(return_value={"frame_id": "main"})),
        ):
            result = await _resize_column_impl(col=1, delta_width=40)

            self.assertEqual(result["status"], "resized")
            self.assertEqual(result["col"], 1)
            self.assertEqual(result["before_width"], 120)
            self.assertEqual(result["after_width"], 160)
            self.assertEqual(result["delta_applied"], 40)
            # border at: frame_offset (5, 10) + canvas (10, 20) + border (220, 14)
            # start: 5 + 10 + 220 = 235; 10 + 20 + 14 = 44
            self.assertEqual(result["separator_point"], {"x": 235.0, "y": 44.0})
            self.assertEqual(result["end_point"], {"x": 275.0, "y": 44.0})

            mock_drag.assert_awaited_once()

    async def test_autofit_columns_dry_run(self) -> None:
        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        mock_probe_result = {
            "ok": True,
            "canvas": {"left": 0.0, "top": 0.0, "right": 800.0, "width": 800.0},
            "rightFrozenW": 80,
            "scrollLeft": 0,
            "fields": ["id", "title"],
            "items": [
                {
                    "col": 0, "field": "id", "title": "ID", "width": 50,
                    "headerNeed": 60, "bodyNeed": 40, "allowance": 40,
                    "sample": "1", "border": {"x": 50.0, "y": 14.0, "draggable": True},
                    "center": {"x": 25.0, "y": 14.0},
                },
                {
                    "col": 1, "field": "title", "title": "标题", "width": 100,
                    "headerNeed": 120, "bodyNeed": 150, "allowance": 96,
                    "sample": "长文本标题测试", "border": {"x": 200.0, "y": 14.0, "draggable": True},
                    "center": {"x": 125.0, "y": 14.0},
                },
            ],
        }

        mock_frame.evaluate.return_value = mock_probe_result

        with (
            patch("qa_automation.components.vtable.autofit._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.autofit.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.autofit.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.autofit.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.autofit._page_id", return_value="p-1"),
            patch("qa_automation.components.vtable.autofit._frame_context_details", AsyncMock(return_value={"frame_id": "main"})),
            patch("qa_automation.components.vtable.autofit._frame_page_offset", AsyncMock(return_value={"x": 0.0, "y": 0.0})),
        ):
            plan = await automation.autofit_columns(dry_run=True)
            self.assertEqual(plan["status"], "dry-run")
            self.assertEqual(len(plan["plan"]), 2)
            self.assertEqual(plan["plan"][0]["target"], 60)
            self.assertEqual(plan["plan"][1]["target"], 150)

    async def test_interaction_chain_reorder_and_resize(self) -> None:
        from qa_automation.interaction.chain import execute_chain

        with (
            patch("qa_automation._reorder_column_impl", AsyncMock(return_value={"status": "reordered"})) as mock_reorder,
            patch("qa_automation._resize_column_impl", AsyncMock(return_value={"status": "resized"})) as mock_resize,
            patch("qa_automation.interaction.chain.analyze_page_compact", AsyncMock(return_value={})),
            patch("qa_automation.scan_overlays", AsyncMock(return_value={"overlays": []})),
            patch("qa_automation.current_page", AsyncMock(return_value=AsyncMock(url="http://test"))),
        ):
            chain_result = await execute_chain([
                {"action": "reorder_column", "from_col": 0, "to_col": 2},
                {"action": "resize_column", "col": 1, "target_width": 180},
            ])

            self.assertEqual(chain_result["executed"], 2)
            self.assertFalse(chain_result.get("stopped_early", False))
            mock_reorder.assert_awaited_once_with(
                from_col=0, to_col=2, from_field=None, to_field=None, frame=None, table_index=None
            )
            mock_resize.assert_awaited_once_with(
                col=1, field=None, target_width=180, delta_width=None, frame=None, table_index=None
            )

    async def test_interaction_chain_holds_action_lock_across_steps(self) -> None:
        """批内动作必须整体持 _action_lock:步骤执行期间并发的其他工具调用无法插队。

        锁不是可重入锁——若链条改回逐步走公共包装(各自加锁),本测试里的
        acquire 会立刻成功并触发 fail;持有全局锁时则只能等到超时。
        """
        import asyncio

        from qa_automation.interaction.chain import execute_chain

        lock_observed: list[bool] = []

        async def _spy_reorder(**kwargs: object) -> dict:
            lock_observed.append(automation._action_lock.locked())
            try:
                await asyncio.wait_for(automation._action_lock.acquire(), timeout=0.05)
            except TimeoutError:
                pass
            else:
                automation._action_lock.release()
                self.fail("interleaved task acquired _action_lock mid-chain")
            return {"status": "reordered"}

        with (
            patch("qa_automation._reorder_column_impl", side_effect=_spy_reorder),
            patch("qa_automation.scan_overlays", AsyncMock(return_value={"overlays": []})),
            patch("qa_automation.current_page", AsyncMock(return_value=AsyncMock(url="http://test"))),
        ):
            result = await execute_chain([{"action": "reorder_column", "from_col": 0, "to_col": 1}])

        self.assertEqual(result["executed"], 1)
        self.assertEqual(lock_observed, [True])
        self.assertFalse(automation._action_lock.locked())

    async def test_vtable_analysis_extracts_header_text_and_separator(self) -> None:
        from qa_automation.components.vtable.analysis import _vtable_analysis_impl

        mock_page = AsyncMock()
        mock_frame = AsyncMock()

        raw_analysis = {
            "meta": {
                "rowCount": 10,
                "colCount": 2,
                "headerRowCount": 1,
                "frozenRowCount": 0,
                "frozenColCount": 0,
                "scrollLeft": 0,
                "scrollTop": 0,
                "canvas_box": {"x": 15.0, "y": 30.0, "width": 800.0, "height": 400.0},
            },
            "columns": [
                {
                    "col": 0,
                    "field": "product_name",
                    "title": "产品名称",
                    "header": [
                        {
                            "row": 0,
                            "geometry": {"box": {"x": 0.0, "y": 0.0, "width": 150.0, "height": 30.0}, "center": {"x": 75.0, "y": 15.0}},
                            "text": {"box": {"x": 10.0, "y": 5.0, "width": 80.0, "height": 20.0}, "center": {"x": 50.0, "y": 15.0}},
                            "separator": {"box": {"x": 146.0, "y": 0.0, "width": 8.0, "height": 30.0}, "center": {"x": 150.0, "y": 15.0}, "draggable": True},
                            "icons": [
                                {"name": "sort-icon", "box": {"x": 120.0, "y": 7.0, "width": 16.0, "height": 16.0}, "center": {"x": 128.0, "y": 15.0}}
                            ],
                        }
                    ],
                    "sample_cells": [],
                }
            ],
        }

        mock_frame.evaluate.return_value = raw_analysis

        with (
            patch("qa_automation.components.vtable.analysis._current_page_impl", return_value=mock_page),
            patch("qa_automation.components.vtable.analysis.resolve_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.analysis.vtable_frame", return_value=mock_frame),
            patch("qa_automation.components.vtable.analysis._vtable_directory", AsyncMock(return_value=[{"table_index": 0, "context": "page"}])),
            patch("qa_automation.components.vtable.analysis._frame_context_details", AsyncMock(return_value={"frame_id": "main"})),
            patch("qa_automation.components.vtable.analysis.ensure_vtable", AsyncMock()),
            patch("qa_automation.components.vtable.analysis._frame_page_offset", AsyncMock(return_value={"x": 5.0, "y": 10.0})),
            patch("qa_automation.components.vtable.analysis._page_viewport_size", AsyncMock(return_value={"width": 1280.0, "height": 800.0})),
            patch("qa_automation.components.vtable.analysis._page_id", return_value="p-1"),
        ):
            res = await _vtable_analysis_impl(mode="interactive")
            self.assertEqual(res["status"], "ok")
            col0 = res["analysis"]["columns"][0]
            self.assertIn("header_text", col0)
            self.assertIn("separator", col0)
            self.assertIn("header_icons", col0)

            # Verify top viewport calculation for header_text:
            # frame_offset (5, 10) + canvas (15, 30) + text.center (50, 15) = (70.0, 55.0)
            self.assertEqual(col0["header_text"]["point"], {"x": 70.0, "y": 55.0})

            # Verify top viewport calculation for separator:
            # frame_offset (5, 10) + canvas (15, 30) + separator.center (150, 15) = (170.0, 55.0)
            self.assertEqual(col0["separator"]["point"], {"x": 170.0, "y": 55.0})
            self.assertTrue(col0["separator"]["draggable"])


if __name__ == "__main__":
    unittest.main()
