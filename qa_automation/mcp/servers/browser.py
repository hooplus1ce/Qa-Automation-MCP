"""Browser process, CDP connection, page, and session lifecycle tools."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

import qa_automation as automation

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Browser Lifecycle")

    @mcp.tool()
    @instrument_tool
    async def browser_open(url: str, headless: bool = True) -> dict:
        """打开 Playwright 浏览器并导航到目标页面(后续工具复用同一浏览器)。"""
        return await automation.open_url(url, headless=headless)

    @mcp.tool()
    @instrument_tool
    async def browser_start(
        port: int = 9222,
        headless: bool = False,
        executable_path: str | None = None,
        user_data_dir: str | None = None,
        timeout_ms: int = 15_000,
    ) -> dict:
        """在指定端口启动受管 Chrome，并自动接管；Profile 与下载保存在工作区产物目录。"""
        return await automation.launch_chrome(
            port=port,
            headless=headless,
            executable_path=executable_path,
            user_data_dir=user_data_dir,
            timeout_ms=timeout_ms,
        )

    @mcp.tool()
    @instrument_tool
    async def browser_connect(cdp_url: str | None = None, port: int = 9222) -> dict:
        """经 CDP 连接一个已运行的浏览器，默认连接 9222 端口。

        复用外部浏览器与其已打开的 VTable 页面(含页面内的实例),无需重新导航;
        vtable_cell_click / ui_snapshot 等工具直接驱动该页面。
        关闭时仅断开连接,不关闭外部浏览器进程。

        下载行为会配置到工作区产物目录；若 CDP 不支持全局下载配置，则退化为
        Playwright download 事件持久化。
        """
        return await automation.connect_browser(cdp_url, port=port)

    @mcp.tool()
    @instrument_tool
    async def browser_session(
        action: Literal["list", "create", "select", "save", "close", "reset_viewport"] = "list",
        session_id: str | None = None,
        name: str | None = None,
        storage_state_path: str | None = None,
    ) -> dict:
        """管理隔离 BrowserContext 会话；storage_state_path 必须位于使用方项目工作区内。"""
        return await automation.browser_session(
            action=action,
            session_id=session_id,
            name=name,
            storage_state_path=storage_state_path,
        )

    @mcp.tool()
    @instrument_tool
    async def browser_pages() -> dict:
        """列出所有 BrowserContext 的标签页及稳定 page_id，并标记当前选中页。"""
        return await automation.list_pages()

    @mcp.tool()
    @instrument_tool
    async def browser_select_page(page_id: str) -> dict:
        """显式选中一个 page_id；后续页面、iframe、浮层和 VTable 工具固定使用该页。"""
        return await automation.select_page(page_id)

    @mcp.tool()
    @instrument_tool
    async def browser_reset_viewport() -> dict:
        """重置浏览器视口为全屏自然视口并清除任何残留的 CDP 设备模拟。

        彻底清除 CDP 视口模拟导致的'右侧大片灰色/小视口冻结'现象，
        恢复窗口最大化并向顶层及所有 iframe 广播 resize 事件，触发 VTable 与 Ant Design
        等组件即刻重新自适应布局。
        """
        return await automation.reset_viewport()

    @mcp.tool()
    @instrument_tool
    async def browser_close() -> dict:
        """关闭 Playwright 浏览器,释放资源。

        CDP 连接的外部浏览器只断开连接,受管 Chrome 和 Playwright 浏览器则关闭。
        """
        return await automation.close_browser()

    @mcp.tool()
    @instrument_tool
    async def browser_login(
        username: str = "pingxiang",
        password: str = "Ac123456",
        url: str = "https://demo18-scm.hoolinks.com/static/admin/",
        captcha: str | None = None,
        max_retries: int = 3,
    ) -> dict:
        """针对新建浏览器会话/登录过期的专属自动登录工具:
        自动处理登录失效弹窗、自动输入账号密码、截图并尝试AI识别图形验证码完成登录。
        若无需自动识别或已有验证码字符，可直接传入 captcha 参数跳过识别。
        """
        return await automation.browser_login(
            username=username,
            password=password,
            url=url,
            captcha=captcha,
            max_retries=max_retries,
        )

    return mcp
