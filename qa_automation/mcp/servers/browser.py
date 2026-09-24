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
        """打开 Playwright 浏览器并导航到目标页面(后续工具复用同一浏览器)。

        Args:
            url: 完整目标地址，直接在当前选中页上 goto(wait_until=load)
            headless: 预留参数；当前实现不消费它，无头与否由 browser_start/browser_connect 的启动方式决定
        """
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
        """在指定端口启动受管 Chrome，并自动接管；Profile 与下载保存在工作区产物目录。

        Args:
            port: CDP 调试端口（默认 9222），只监听 127.0.0.1；端口已被其它 Chrome 占用会直接失败并提示改用 browser_connect
            headless: False（默认）带界面并 --start-maximized；True 用 --headless=new（无窗口，视口需自行处理）
            executable_path: Chrome/Edge 可执行文件绝对路径；省略时依次试 CHROME_EXECUTABLE 环境变量、Windows 常见安装位置和 PATH
            user_data_dir: 用户数据目录，相对路径按工作区根解析且不得越出工作区；默认落在产物目录 browser-profile/chrome-<port>，被其它 Chrome 锁定时换一个目录
            timeout_ms: 等待 CDP 端点就绪的最长毫秒数（默认 15000，下限 1000）；Chrome 秒退会立即报错而非等满超时
        """
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

        Args:
            cdp_url: 完整 CDP 端点地址；给出时忽略 port，末尾斜杠会被去掉
            port: 省略 cdp_url 时用于拼本地端点（默认 9222），给出 cdp_url 时完全忽略
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
        """管理隔离 BrowserContext 会话；storage_state_path 必须位于使用方项目工作区内。

        Args:
            action: list=列出全部会话（默认）；create=新建隔离 context；select=切换选中；save=导出登录态；close=关闭；reset_viewport=重置视口为全屏
            session_id: select/close 的目标会话 id；与 name 同时给时以 session_id 优先
            name: create 时给会话起名；select 时可按名称（或 context 序号）匹配
            storage_state_path: create 时读入的登录态 JSON（工作区内且须存在）；save 时写入的目标路径（工作区内，必填）
        """
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
        """显式选中一个 page_id；后续页面、iframe、浮层和 VTable 工具固定使用该页。

        Args:
            page_id: browser_pages 返回的稳定 id；纯数字串按标签页枚举序号（0-based）取页，也可匹配 URL 或标题子串
        """
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
        username: str | None = None,
        password: str | None = None,
        url: str | None = None,
        captcha: str | None = None,
        max_retries: int = 3,
    ) -> dict:
        """针对新建浏览器会话/登录过期的专属自动登录工具:
        自动处理登录失效弹窗、自动输入账号密码、截图并尝试AI识别图形验证码完成登录。
        若无需自动识别或已有验证码字符，可直接传入 captcha 参数跳过识别。

        账号密码默认取自环境变量，通常无需传：QA_AUTOMATION_LOGIN_USER /
        QA_AUTOMATION_LOGIN_PASSWORD / QA_AUTOMATION_APS_URL。未配置时返回
        status=config_missing 并提示缺哪一个，不会回退到代码内置口令。

        Args:
            username: 登录账号；省略时取 QA_AUTOMATION_LOGIN_USER
            password: 登录口令；省略时取 QA_AUTOMATION_LOGIN_PASSWORD
            url: 目标站点入口；省略时取 QA_AUTOMATION_APS_URL
            captcha: 已知的验证码字符，传入则跳过图形验证码识别
            max_retries: 登录失败后的最大重试次数（默认 3）
        """
        return await automation.browser_login(
            username=username,
            password=password,
            url=url,
            captcha=captcha,
            max_retries=max_retries,
        )

    @mcp.tool()
    @instrument_tool
    async def browser_inject_cookies(
        cookies: list[dict] | None = None,
        token: str | None = None,
        navigate_to: str | None = None,
        domain: str | None = None,
    ) -> dict:
        """向当前浏览器上下文快速注入 Cookies 或 Access-Token 凭据，并可按需跳转或刷新目标页面。

        适合已有登录令牌、会话 Cookies、或通过接口获取认证后的极速会话注入，
        免除重复的 UI 登录与表单交互耗时。

        Args:
            cookies: Cookie 列表，每项为字典，至少包含 name 与 value，可选 domain/path
            token: 快捷设置的 Access-Token；若提供则自动映射注入 HL-Access-Token、cookie_token、UCTOKEN
            navigate_to: 注入完成后当前标签页导航的目标 URL，省略时不跳转
            domain: Cookie 绑定的目标域名（省略时自动从当前页或 navigate_to 推导，如 .hoolinks.com）
        """
        return await automation.inject_cookies(
            cookies=cookies,
            token=token,
            navigate_to=navigate_to,
            domain=domain,
        )

    return mcp
