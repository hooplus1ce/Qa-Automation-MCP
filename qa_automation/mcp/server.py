"""FastMCP composition root."""

from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.apps.approval import Approval
from fastmcp.apps.choice import Choice
from fastmcp.apps.file_upload import FileUpload
from fastmcp.apps.generative import GenerativeUI
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from qa_automation.mcp.apps.provider import create_app
from qa_automation.mcp.resources import vtable as vtable_resources
from qa_automation.mcp.servers import (
    antd,
    browser,
    chain,
    demos,
    diagnostics,
    net,
    scenario,
    tencent_docs,
    ui,
    vtable,
    x6,
)


def _sanitize_generative_doc() -> None:
    """Strip double-brace template examples from prefab_ui.generative docs.

    GenerativeUI builds the `generate_prefab_ui` tool description from
    `prefab_ui.generative.execute.__doc__`, which contains literal examples
    like `{{ threshold }}` and `{{ balance | currency }}`. dsh (DeepSeek
    Harness) renders every visible tool description into its `tools:sdk`
    prompt section and treats any `{{ ... }}` there as a prompt variable
    whose name must match /^[a-z][a-z0-9_]*$/, so the spaces/pipes make it
    throw `malformed prompt variable reference "{{ threshold }}" in section
    "tools:sdk"` on every turn of a Code Mode session. The double braces are
    only illustrative (the Python API emits them via `.rx`/`Rx()`), so a
    single-brace rendering keeps the description intact without breaking the
    harness interpolation.
    """
    try:
        import prefab_ui.generative as _gen_ui

        doc = _gen_ui.execute.__doc__
        if doc and "{{" in doc:
            _gen_ui.execute.__doc__ = doc.replace("{{", "{").replace("}}", "}")
    except Exception:
        # Description stays as-is if prefab_ui cannot be imported; the
        # failure mode above only matters while the generative provider loads.
        pass
INSTRUCTIONS = """
你是一个专业严谨的自动化测试工程师助手。请严格遵守以下工具优先级与交互行为准则：

1. 【页面元素识别与观察优先级（极重要）】：
   - 【第一优先级 / 首选工具】：`ui_snapshot`（生成紧凑准确的 ARIA 语义树，附带确定性的 [ref] 与 [box=x,y,w,h] 真实视口坐标，无视觉畸变、毫秒级响应且极度节省 Token）。在执行任何定位与点击前，必须首选快照！
   - 【局部聚焦分析】：使用 `ui_analyze_scope` 聚焦当前活动弹窗或浮层；
   - 【业务表格结构与数据】：必须使用 `vtable_analysis`（提取列定义与交互证据）与 `vtable_read_cells`（批量读取单元格），严禁使用视觉截图比对表格！
   - 【全局提示与操作反馈】：必须使用 `wait_message`（基于浏览器内联 MutationObserver，零轮询无损捕获 toast 提示）或 `overlay_scan`。
   - ⚠️【严禁滥用视觉截图】：`ui_screenshot` 是优先级最低的兜底工具！除非人类用户明确指令要求提供截图，或者遇到页面纯无语义 Canvas 渲染/图片验证码等视觉死角，严禁在常规自动化交互中重复调用 `ui_screenshot` 识别页面！

2. 【坐标点击与定位防陷阱准则】：
   - 严禁通过截图像素推算点击坐标！在 Windows 高分屏缩放（DPI/DSF）下，全屏截图可能产生灰边或缩放比例偏移（如 0.8x），依赖截图像素计算坐标必然偏离目标。
   - 必须直接使用 DOM / VTable / AX 报告的真实 CSS 坐标，或优先使用语义定位器（CSS 选择器、Role/Name、Text、Placeholder）。
   - 坐标具有时效性：任何视口重置或窗口变化后，必须重新获取最新坐标，严禁复用旧坐标。
   - VTable 单元格推荐优先使用业务字段定位 `vtable_cell_click(field=..., record_index=...)`。

3. 【逃生通道调用约束】：
   - `run_js` 是受限逃生工具。除非用户明确显式要求执行 JS 脚本，否则 AI 严禁擅自主动调用本工具。

4. 【APS 登录与账号切换准则】：
   - 在当前页面登录、恢复会话或原地切换账号/角色时，必须优先直接调用 `browser_login(profile="<档案名或角色关键词>")`（基于 `profiles.toml` 与 Cookie 缓存秒级覆盖切换），严禁通过 `ui_snapshot` / `ui_click` 在页面右上角手动点击退出登录再填表！
   - 若需多账号同时在线、互不覆盖（如双角色协同对照），先调用 `browser_session(action="create", name="...")` 新建隔离上下文，再调用 `browser_login(profile="...")`。
""".strip()


def create_server(
    include_demos: bool | None = None,
    include_prefab_ui: bool | None = None,
) -> FastMCP:
    """Compose focused local servers while preserving the public tool names."""
    if include_demos is None:
        include_demos = os.getenv("QA_AUTOMATION_ENABLE_DEMOS", "false").lower() in ("true", "1")
    if include_prefab_ui is None:
        include_prefab_ui = os.getenv("QA_AUTOMATION_ENABLE_PREFAB_UI", "false").lower() in ("true", "1")

    providers = []
    if include_demos:
        providers.append(create_app())

    server = FastMCP("qa-automation", instructions=INSTRUCTIONS, providers=providers)
    server.add_provider(Approval(title="确认执行该测试用例?"))
    server.add_provider(Choice())
    server.add_provider(FileUpload())

    if include_prefab_ui:
        _sanitize_generative_doc()
        server.add_provider(GenerativeUI())

    skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
    server.add_provider(SkillsDirectoryProvider(roots=skills_dir, reload=True))

    child_modules = [
        vtable_resources,
        browser,
        ui,
        antd,
        net,
        scenario,
        x6,
        vtable,
        chain,
        diagnostics,
        tencent_docs,
    ]
    if include_demos:
        child_modules.insert(1, demos)

    for module in child_modules:
        server.mount(module.create_server())
    return server


mcp = create_server()


def main() -> None:
    """Run the composed MCP server over protocol-clean stdio."""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
