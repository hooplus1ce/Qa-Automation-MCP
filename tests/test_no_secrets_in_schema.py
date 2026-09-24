"""凭据不得进入代码与 MCP 工具 schema 的回归测试。

动因（实测）：`browser_login` 曾把账号口令与站点域名写成参数默认值，
而工具默认值会进 inputSchema、随**每一轮**对话整份下发给模型——口令因此
常驻 LLM 上下文与会话日志。`qa_automation/browser.py` 里也有同样的硬编码。
本模块把这条约束钉死，防止"加个默认值更方便"再次发生。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent / "qa_automation"

# 生产代码里不允许出现的凭据字面量（含历史泄露过的值）
FORBIDDEN_LITERALS = ("pingxiang", "Ac123456", "demo18-scm.hoolinks.com")

# 参数名 -> 该参数的字符串默认值视为秘密
CREDENTIAL_PARAMS = {"password", "token", "secret", "api_key", "authorization"}


def _py_files():
    for path in PKG_ROOT.rglob("*.py"):
        if "autofit" in path.name:
            continue  # docs/ 里的交付物不参与
        yield path


class NoHardcodedCredentialsTest(unittest.TestCase):
    def test_package_sources_have_no_credential_literals(self):
        offenders = []
        for path in _py_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
            for literal in FORBIDDEN_LITERALS:
                if literal in text:
                    offenders.append(f"{path.relative_to(PKG_ROOT.parent)}: {literal}")
        self.assertEqual(offenders, [], f"生产代码中仍存在硬编码凭据/环境专属值：{offenders}")

    def test_login_credentials_resolve_from_env(self):
        from qa_automation.config import resolve_login_credentials

        os.environ["QA_AUTOMATION_LOGIN_USER"] = "env-user"
        os.environ["QA_AUTOMATION_LOGIN_PASSWORD"] = "env-pw"
        os.environ["QA_AUTOMATION_APS_URL"] = "https://example.test/admin/"
        try:
            self.assertEqual(
                resolve_login_credentials(),
                ("env-user", "env-pw", "https://example.test/admin/"),
            )
            # 显式入参优先于 env
            self.assertEqual(resolve_login_credentials("arg", None)[0], "arg")
        finally:
            for k in (
                "QA_AUTOMATION_LOGIN_USER",
                "QA_AUTOMATION_LOGIN_PASSWORD",
                "QA_AUTOMATION_APS_URL",
            ):
                os.environ.pop(k, None)

    def test_missing_credentials_report_var_names_not_values(self):
        from qa_automation.config import credential_missing_message

        msg = credential_missing_message(user=False, password=True)
        self.assertIn("QA_AUTOMATION_LOGIN_USER", msg)
        self.assertNotIn("QA_AUTOMATION_LOGIN_PASSWORD", msg)  # 只报真正缺的那项
        self.assertTrue(all(lit not in msg for lit in FORBIDDEN_LITERALS))

    def test_calling_login_without_credentials_is_config_missing(self):
        """未配置时必须干净失败，而不是回退到内置口令。"""
        from qa_automation.browser import _browser_login_impl

        for k in ("QA_AUTOMATION_LOGIN_USER", "QA_AUTOMATION_LOGIN_PASSWORD"):
            os.environ.pop(k, None)
        result = asyncio.run(_browser_login_impl())
        self.assertEqual(result.get("status"), "config_missing")
        self.assertIn("QA_AUTOMATION_LOGIN", result.get("reason", ""))


class ToolSchemaCarriesNoSecretsTest(unittest.TestCase):
    def test_tool_panel_has_no_credential_values_or_defaults(self):
        from fastmcp import Client

        from qa_automation.mcp.server import mcp

        async def collect():
            async with Client(mcp) as c:
                return await c.list_tools()

        tools = asyncio.run(collect())
        blob = json.dumps(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "schema": getattr(t, "input_schema", None)
                    or getattr(t, "inputSchema", None),
                }
                for t in tools
            ],
            ensure_ascii=False,
            default=str,
        )
        for literal in FORBIDDEN_LITERALS:
            self.assertNotIn(literal, blob, f"工具面板泄露了 {literal!r}")

        offenders = []
        for t in tools:
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
            for pname, spec in (schema.get("properties") or {}).items():
                default = (spec or {}).get("default")
                if pname.lower() in CREDENTIAL_PARAMS and isinstance(default, str) and default:
                    offenders.append(f"{t.name}.{pname} 带字符串默认值")
                # 形如口令的高熵短串也不应出现在默认值里
                if isinstance(default, str) and re.fullmatch(r"[A-Za-z0-9]{8,20}", default) \
                        and pname.lower() not in {"profile", "name", "mode", "frame"}:
                    offenders.append(f"{t.name}.{pname} 默认值疑似秘密")
        self.assertEqual(offenders, [], f"凭据类参数不得有默认值：{offenders}")

    def test_every_tool_has_at_least_one_param_description(self):
        """参数缺 description 时模型只能靠名字猜；这里给出可观测的回归下限。"""
        from fastmcp import Client

        from qa_automation.mcp.server import mcp

        async def collect():
            async with Client(mcp) as c:
                return await c.list_tools()

        tools = asyncio.run(collect())
        missing = []
        total = 0
        for t in tools:
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
            for pname, spec in (schema.get("properties") or {}).items():
                total += 1
                if not (spec or {}).get("description"):
                    missing.append(f"{t.name}.{pname}")
        # 本项目 mcp/servers/*.py 的工具参数已全部补齐 description；剩下的只可能是
        # 第三方 provider 自带的参数（read_file.name、generate_prefab_ui.code/data、
        # search_prefab_components.components），它们的 docstring 在 .venv 里，改不了。
        self.assertLessEqual(
            len(missing),
            4,
            f"无描述参数数量上升：{len(missing)} 个，例如 {missing[:10]}",
        )
        self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()
