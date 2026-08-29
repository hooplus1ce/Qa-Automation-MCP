"""Browser process, CDP connection, page, and session lifecycle tools."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

import vtable_playwright as vpw
from tool_metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Browser Lifecycle")

    @mcp.tool()
    @instrument_tool
    async def browser_open(url: str, headless: bool = True) -> dict:
        """打开 Playwright 浏览器并导航到目标页面(后续工具复用同一浏览器)。"""
        return await vpw.open_url(url, headless=headless)

    @mcp.tool()
    @instrument_tool
    async def browser_start(
        port: int = 9222,
        headless: bool = False,
        executable_path: str | None = None,
        user_data_dir: str | None = None,
        timeout_ms: int = 15_000,
    ) -> dict:
        """在指定端口启动受管 Chrome，并自动通过 CDP 接管。"""
        return await vpw.launch_chrome(
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
        """
        return await vpw.connect_browser(cdp_url, port=port)

    @mcp.tool()
    @instrument_tool
    async def browser_session(
        action: Literal["list", "create", "select", "save", "close"] = "list",
        session_id: str | None = None,
        name: str | None = None,
        storage_state_path: str | None = None,
    ) -> dict:
        """管理隔离 BrowserContext 会话，支持账号 Cookie 环境切换和 storage state 持久化。"""
        return await vpw.browser_session(
            action=action,
            session_id=session_id,
            name=name,
            storage_state_path=storage_state_path,
        )

    @mcp.tool()
    @instrument_tool
    async def browser_pages() -> dict:
        """列出所有 BrowserContext 的标签页及稳定 page_id，并标记当前选中页。"""
        return await vpw.list_pages()

    @mcp.tool()
    @instrument_tool
    async def browser_select_page(page_id: str) -> dict:
        """显式选中一个 page_id；后续页面、iframe、浮层和 VTable 工具固定使用该页。"""
        return await vpw.select_page(page_id)

    @mcp.tool()
    @instrument_tool
    async def browser_close() -> dict:
        """关闭 Playwright 浏览器,释放资源。

        CDP 连接的外部浏览器只断开连接,受管 Chrome 和 Playwright 浏览器则关闭。
        """
        return await vpw.close_browser()

    return mcp
