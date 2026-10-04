"""Browser process, CDP connection, page, and session lifecycle tools."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP

import qa_automation as automation

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Browser Lifecycle")

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": False},
    )
    @instrument_tool
    async def browser_open(url: str, headless: bool = True) -> dict:
        """打开 Playwright 浏览器并导航到目标页面(后续工具复用同一浏览器)。

        Args:
            url: 完整目标地址，直接在当前选中页上 goto(wait_until=load)
            headless: 预留参数；当前实现不消费它，无头与否由 browser_start/browser_connect 的启动方式决定
        """
        return await automation.open_url(url, headless=headless)

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": True},
    )
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

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": True},
    )
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

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": False},
    )
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

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def browser_pages() -> dict:
        """列出所有 BrowserContext 的标签页及稳定 page_id，并标记当前选中页。"""
        return await automation.list_pages()

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": True},
    )
    @instrument_tool
    async def browser_select_page(page_id: str) -> dict:
        """显式选中一个 page_id；后续页面、iframe、浮层和 VTable 工具固定使用该页。

        Args:
            page_id: browser_pages 返回的稳定 id；纯数字串按标签页枚举序号（0-based）取页，也可匹配 URL 或标题子串
        """
        return await automation.select_page(page_id)

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": True},
    )
    @instrument_tool
    async def browser_reset_viewport() -> dict:
        """重置浏览器视口为全屏自然视口并清除任何残留的 CDP 设备模拟。

        彻底清除 CDP 视口模拟导致的'右侧大片灰色/小视口冻结'现象，
        恢复窗口最大化并向顶层及所有 iframe 广播 resize 事件，触发 VTable 与 Ant Design
        等组件即刻重新自适应布局。
        """
        return await automation.reset_viewport()

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": True},
    )
    @instrument_tool
    async def browser_close() -> dict:
        """关闭 Playwright 浏览器,释放资源。

        CDP 连接的外部浏览器只断开连接,受管 Chrome 和 Playwright 浏览器则关闭。
        """
        return await automation.close_browser()

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": False},
    )
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

        支持在当前页面直接原地切换账号（自动清空旧会话并注入新 Cookie，无需手动退出登录）。
        凭据解析优先级：显式传参 > 账号档案（profiles.toml，按档案名/username/role 关键词匹配）
        > 环境变量。三者都缺或传入未知 profile 时返回可用档案列表，不回退到内置口令。

        典型用法：
          - 免参登录/恢复：browser_login()                     # 默认档案，命中缓存秒级恢复
          - 原地切换账号：  browser_login(profile="<档案名或角色名>")
          - 强制重新登录：  browser_login(profile=..., force=True)  # 忽略缓存取新 Token
          - 验证码两段式：  先 browser_login(profile=...) 拿验证码图片，
                            识别后 browser_login(profile=..., captcha="1234")

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

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": False},
    )
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

    @mcp.tool(
        name="run_js",
        annotations={
            "read_only_hint": False,
            "destructive_hint": True,
            "idempotent_hint": False,
            "open_world_hint": True,
        },
    )
    @instrument_tool
    async def run_js(
        script: str,
        arg: Any = None,
        frame: str | None = None,
        timeout_ms: int = 10_000,
    ) -> Any:
        """【受限逃生通道】在浏览器当前页面或指定 frame 中执行 JavaScript 脚本并返回结果。

        除非人类用户在提示词中显式、明确要求执行 JS，严禁主动调用本工具；常规点击/输入/选择/
        表格读取/弹层断言必须用专属高阶工具，严禁手写 querySelector/click 脚本替代。
        仅适用于用户明确要求的底层调试、读取特殊全局内存变量（如 window.__store__）、
        或极端自定义控件的逃生操作。

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

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": False, "idempotent_hint": False},
    )
    @instrument_tool
    async def browser_upload_file(
        files: list[str],
        css: str | None = None,
        xpath: str | None = None,
        text: str | None = None,
        role: str | None = None,
        name: str | None = None,
        frame: str | None = None,
        timeout_ms: int = 10_000,
    ) -> dict:
        """点击触发元素上传工作区文件，或直接为页面里的 <input type=file> 赋值。

        两种模式（二选一）：
          - 传入触发元素定位（css/xpath/text/role 任意一种）→ 点击该元素，用
            Playwright expect_file_chooser 截获"选择文件"系统对话框并注入文件；
            适配"点击按钮弹出文件选择框"的常见上传交互，iframe 内控件亦可。
          - 全部省略 → 在目标 frame（默认激活业务 iframe → 顶层文档）自动查找
            input[type=file] 直接 set_input_files，隐藏 input 也能赋值。

        Args:
            files: 待上传文件路径列表；相对路径按使用方工作区根解析，不得越出工作区
            css: 触发元素 CSS 选择器（优先级最高；也用于直接定位 input[type=file]）
            xpath: 触发元素 XPath 表达式（不带 xpath= 前缀）
            text: 触发元素可见文本（精确匹配，如 "选择文件"、"导 入"）
            role: 触发元素 ARIA 角色（与 name 配对使用，如 button）
            name: 与 role 配对的无障碍名
            frame: 目标 frame：省略=激活业务 iframe 优先；可传 main/top/active 或 frame_id
            timeout_ms: 点击/等待文件选择框/赋值的整体超时毫秒数（默认 10000）
        """
        return await automation.upload_files(
            files,
            css=css,
            xpath=xpath,
            text=text,
            role=role,
            name=name,
            frame=frame,
            timeout_ms=timeout_ms,
        )

    @mcp.tool(
        annotations={"read_only_hint": True, "destructive_hint": False, "idempotent_hint": False},
    )
    @instrument_tool
    async def browser_wait_download(
        timeout_ms: int = 30_000,
        filename_contains: str | None = None,
        include_recent_seconds: int = 60,
    ) -> dict:
        """等待下载文件写完并返回其在工作区产物目录中的路径。

        页面触发的下载会由监听器/CDP 自动保存到工作区 downloads 目录；本工具
        轮询该目录，判定"连续两次扫描文件大小与修改时间未再变化"即认为写完。
        典型用法：先点击导出/下载按钮，再调用本工具拿文件路径交给后续工具。
        超时不会抛错，返回 status=timeout 与当前目录快照便于排查。

        Args:
            timeout_ms: 最长等待毫秒数（默认 30000）
            filename_contains: 文件名过滤子串（不区分大小写），如 "xlsx"、"对账单"
            include_recent_seconds: 把最近 N 秒内已写完的文件也纳入候选（默认 60），
                覆盖"下载在调用本工具前已完成"的场景；0 表示只等调用之后新出现的文件
        """
        return await automation.wait_download(
            timeout_ms=timeout_ms,
            filename_contains=filename_contains,
            include_recent_seconds=include_recent_seconds,
        )

    return mcp
