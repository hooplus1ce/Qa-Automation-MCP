"""Browser process, CDP connection, page, and session lifecycle tools."""

from __future__ import annotations

from typing import Any, Literal

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
        profile: str | None = None,
        force: bool = False,
        max_retries: int = 3,
    ) -> dict:
        """登录 / 恢复 / 切换 APS 账号会话的统一入口（账号档案 + 登录态缓存 + 验证码两段式）。

        支持在当前页面直接原地切换账号（自动清空旧会话并注入新账号 Cookie 刷新后台，无需在页面内点击退出登录）。
        凭据解析优先级：显式传参 > 账号档案（profiles.toml，支持按 profile 档案名、username 或 role 角色关键词匹配）> 环境变量
        QA_AUTOMATION_LOGIN_USER / QA_AUTOMATION_LOGIN_PASSWORD / QA_AUTOMATION_APS_URL。
        三者都缺或传入未知 profile 时返回可用档案列表（含角色说明），不回退到内置口令。

        典型用法：
          - 免参登录/恢复：browser_login()                     # 用默认档案，命中缓存则秒级恢复
          - 原地切换账号：  browser_login(profile="<档案名或角色名>") # 直接切换当前页登录账号（如超管/系统管理员/权限测试）
          - 强制重新登录：  browser_login(profile="<档案名>", force=True) # 忽略缓存重新调接口获取新 Token
          - 验证码两段式：  先 browser_login(profile=...) 拿到验证码图片，
                            识别后 browser_login(profile=..., captcha="1234") 完成登录

        Args:
            username: 登录账号；显式传入时优先于账号档案与环境变量
            password: 登录口令；显式传入时优先于账号档案与环境变量
            url: 目标站点入口；省略时取账号档案的 admin_url 或 QA_AUTOMATION_APS_URL
            captcha: 已知的图形验证码字符；省略时返回待识别的验证码图片
            profile: 账号档案名、账号名或角色关键词（见 profiles.toml）；省略时取 QA_AUTOMATION_ACCOUNT，其次第一个档案
            force: True 时忽略登录态缓存、强制重新登录（缓存失效或显式传 profile 切号会自动处理，通常无需传）
            max_retries: 接口登录失败后的最大重试次数（默认 3）
        """
        return await automation.browser_login(
            username=username,
            password=password,
            url=url,
            captcha=captcha,
            profile=profile,
            force=force,
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

    @mcp.tool(name="run_js")
    @instrument_tool
    async def run_js(
        script: str,
        arg: Any = None,
        frame: str | None = None,
        timeout_ms: int = 10_000,
    ) -> Any:
        """【受限逃生通道】在浏览器当前页面或指定 frame 中执行 JavaScript 脚本并返回结果。

        ⚠️【AI 强制行为准则与调用约束】⚠️
        1. 严禁主动调用：除非人类用户在提示词中显式、明确指令要求执行 JS（例如：“请用 run_js 执行...”、“执行一段 JS 脚本...”），否则 AI 严禁擅自调用本工具！
        2. 常规自动化严禁替代：常规点击、输入、下拉选择、表格数据读取、弹层断言等，必须使用专属高阶工具（ui_click / ui_interact / antd_select / vtable_cell_click / wait_message 等），严禁擅自手写 querySelector/click 脚本替代。
        3. 适用场景：仅用于用户明确要求的底层调试、读取特殊的全局内存变量（如 window.__store__）、或极端自定义控件的逃生操作。

        Args:
            script: 要执行的 JavaScript 代码。支持表达式（如 'window.innerWidth'）、异步函数或包含 return 的代码块
            arg: 传递给 JS 脚本的入参对象（在脚本中可通过参数或 arguments[0] 访问）
            frame: 目标 frame：省略=顶层文档；'active'=当前激活的微前端 iframe；也可传 frame_id 或 name
            timeout_ms: 执行超时毫秒数（默认 10000ms）
        """
        return await automation.run_js(
            script=script,
            arg=arg,
            frame=frame,
            timeout_ms=timeout_ms,
        )

    return mcp
