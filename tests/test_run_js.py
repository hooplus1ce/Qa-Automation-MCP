"""Unit tests for the run_js emergency escape-hatch tool."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from qa_automation.browser import RUN_JS_OUTPUT_LIMIT, _run_js_impl, run_js
from qa_automation.mcp.servers.browser import create_server


class RunJsTests(unittest.TestCase):
    def test_run_js_tool_registered_with_strict_ai_constraint(self) -> None:
        server = create_server()
        tools = asyncio.run(server.list_tools())
        by_name = {t.name: t for t in tools}

        self.assertIn("run_js", by_name)
        tool = by_name["run_js"]

        # 验证说明文档包含显著的 AI 禁令与调用约束
        self.assertIn("【受限逃生通道】", tool.description)
        self.assertIn("严禁主动调用", tool.description)
        self.assertIn("除非人类用户在提示词中显式", tool.description)

        # 验证入参 schema
        props = tool.parameters["properties"]
        self.assertIn("script", props)
        self.assertIn("arg", props)
        self.assertIn("frame", props)
        self.assertIn("timeout_ms", props)

    def test_empty_script_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            asyncio.run(_run_js_impl(""))

        with self.assertRaises(ValueError):
            asyncio.run(_run_js_impl("   "))

    def test_script_smart_function_wrapping(self) -> None:
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value=42)

        with patch("qa_automation.browser.runjs._current_page_impl", return_value=mock_page):
            # 1. 纯表达式：不额外包裹
            res1 = asyncio.run(_run_js_impl("window.innerWidth"))
            mock_page.evaluate.assert_called_with("window.innerWidth", None)
            self.assertEqual(res1, 42)

            # 2. 包含 return 的语句块：自动包裹为 async (arg) => { ... }
            res2 = asyncio.run(_run_js_impl("const a = 10;\nreturn a * 4;"))
            called_code = mock_page.evaluate.call_args[0][0]
            self.assertTrue(called_code.startswith("async (arg) => {"))
            self.assertIn("return a * 4;", called_code)
            self.assertEqual(res2, 42)

            # 3. 自带函数的脚本：保持原样
            res3 = asyncio.run(_run_js_impl("() => document.title"))
            mock_page.evaluate.assert_called_with("() => document.title", None)
            self.assertEqual(res3, 42)

    def test_output_truncation_limits(self) -> None:
        mock_page = MagicMock()
        long_string = "z" * (RUN_JS_OUTPUT_LIMIT + 500)
        mock_page.evaluate = AsyncMock(return_value=long_string)

        with patch("qa_automation.browser.runjs._current_page_impl", return_value=mock_page):
            res = asyncio.run(_run_js_impl("getHugeString()"))
            self.assertTrue(isinstance(res, str))
            self.assertIn("...[输出截断:", res)
            self.assertTrue(res.startswith("z" * 100))

    def test_run_js_passes_argument(self) -> None:
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="matched")

        with patch("qa_automation.browser.runjs._current_page_impl", return_value=mock_page):
            res = asyncio.run(run_js("arg => arg.name", arg={"name": "Alice"}))
            mock_page.evaluate.assert_called_with("arg => arg.name", {"name": "Alice"})
            self.assertEqual(res, "matched")


if __name__ == "__main__":
    unittest.main()
