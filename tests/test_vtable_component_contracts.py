"""不依赖浏览器的 VTable 组件层回归测试。

直接执行 _cell_info_impl / _drop_files_impl 函数体,捕获
"函数引用了未导入名称"(NameError)这类 mock 实现层的测试无法发现的问题。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from qa_automation.components import vtable as vt


def _make_frame() -> SimpleNamespace:
    """覆盖 _cell_info_impl / _drop_files_impl 用到的 frame 行为。"""

    class _Locator:
        def __init__(self) -> None:
            self.drop_payload: object = None
            self.drop_position: dict | None = None

        @property
        def first(self) -> _Locator:
            return self

        async def count(self) -> int:
            return 1

        async def drop(self, payload: object, position: dict | None = None) -> None:
            self.drop_payload = payload
            self.drop_position = position

    frame = SimpleNamespace()
    frame.evaluate = AsyncMock(
        side_effect=lambda *a, **k: {"left": 2, "right": 12, "top": 4, "bottom": 14}
    )
    locator = _Locator()
    frame.locator = lambda selector: locator
    frame._test_locator = locator
    return frame


class VTableComponentContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_cell_info_impl_executes_fully(self) -> None:
        frame = _make_frame()
        with (
            patch.object(vt, "_current_page_impl", AsyncMock(return_value=SimpleNamespace()),
                         create=True),
            patch.object(vt, "vtable_frame", AsyncMock(return_value=frame), create=True),
            patch.object(vt, "ensure_vtable", AsyncMock(return_value=None), create=True),
            patch.object(vt, "cell_center", AsyncMock(return_value={"x": 7.0, "y": 9.0}),
                         create=True),
            patch.object(vt, "cell_visible", AsyncMock(return_value=True), create=True),
            patch.object(vt, "_frame_context_details",
                         AsyncMock(return_value={"frame_id": "frame-0"}), create=True),
        ):
            result = await vt._cell_info_impl(1, 2)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["in_viewport"])
        self.assertEqual(result["center"], {"x": 7.0, "y": 9.0})

    async def test_drop_files_impl_executes_fully(self) -> None:
        frame = _make_frame()
        with (
            patch.object(vt, "_current_page_impl", AsyncMock(return_value=SimpleNamespace()),
                         create=True),
            patch.object(vt, "vtable_frame", AsyncMock(return_value=frame), create=True),
            patch.object(vt, "ensure_vtable", AsyncMock(return_value=None), create=True),
            patch.object(vt, "ensure_cell_visible", AsyncMock(return_value=True), create=True),
            patch.object(vt, "_frame_context_details",
                         AsyncMock(return_value={"frame_id": "frame-0"}), create=True),
            patch.object(
                vt,
                "resolve_workspace_path",
                lambda value, **kwargs: Path("C:/workspace/file.png"),
                create=True,
            ),
        ):
            result = await vt._drop_files_impl(0, 1, ["file.png"])

        self.assertEqual(result["status"], "dropped")
        self.assertEqual(
            Path(str(frame._test_locator.drop_payload)), Path("C:/workspace/file.png")
        )
        self.assertEqual(frame._test_locator.drop_position, {"x": 7.0, "y": 9.0})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
