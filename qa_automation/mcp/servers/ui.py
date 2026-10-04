"""General page, iframe, overlay, and screenshot interaction tools."""

from __future__ import annotations

from fastmcp import FastMCP

import qa_automation as automation

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Page Automation")

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": False},
    )
    @instrument_tool
    async def ui_click(
        role: str | None = None,
        name: str | None = None,
        description: str | None = None,
        css: str | None = None,
        xpath: str | None = None,
        text: str | None = None,
        placeholder: str | None = None,
        x: float | None = None,
        y: float | None = None,
        frame: str | None = None,
        timeout_ms: float = 3_000,
        observe_after: bool = True,
        settle_ms: int = 300,
        max_results: int = 20,
        analysis_id: str | None = None,
        expect_input: bool = False,
        compact: bool = True,
    ) -> dict:
        """统一主点击入口：点击页面控件并即时返回 Portal、提示和聚焦浮层。

        常规按钮、链接、图标、标签页、菜单项等所有点击动作，优先使用本工具。
        若需要执行输入填值、悬停、按键等非点击类动作，请使用 ui_interact；
        若需要操作 AntD 下拉框/日期框，请使用专用的 antd_select / antd_date_pick。

        可同时传多个来自分析结果的候选定位；执行顺序固定为 CSS → AX role/name
        (可带 description) → XPath → text/placeholder → 顶层视口绝对坐标。坐标仅作为
        最终回退，且传 analysis_id 时会拒绝陈旧的 VTable 分析坐标。

        Args:
            role: ARIA 角色名（button/textbox/combobox/checkbox…），与 name 走 get_by_role 匹配
            name: 与 role 配对的无障碍名，exact 全等匹配；不传 role 时不生效
            description: 与 role 配对的 aria-description，用于区分同名控件；不传 role 时忽略
            css: CSS 选择器，优先级最高的定位；命中多个可见元素直接报歧义错而非挑第一个
            xpath: XPath 表达式（不带 xpath= 前缀），排在 css 与 AX role 之后尝试
            text: 元素可见文本，exact 全等匹配，优先级低于 xpath
            placeholder: 输入框 placeholder，exact 全等匹配，定位器里优先级最低
            x: 回退点击点 X：顶层页面视口 CSS 像素，原点在视口左上角，必须与 y 成对给
            y: 回退点击点 Y：顶层页面视口 CSS 像素，必须与 x 成对给
            frame: 目标 frame：省略=先激活 AntD Tab iframe 再顶层；可传 main/top/active/vtable 或 frame_id
            timeout_ms: 动作执行超时毫秒数（默认 3000）；定位阶段为单次尝试不轮询等待，纯坐标点击也不消费该值
            observe_after: 点击后是否收集 Portal/提示/下拉（默认 True）；不需要浮层证据时关掉省 token
            settle_ms: 浮层观察窗口毫秒数（默认 300，限 0–2000，越界报错）；仅 observe_after 时生效。
                该值是硬上限：窗口内观察到 DOM 变更、且变更后静默约 25ms（无 loading 骨架）即自适应提前收口，
                否则等满整个窗口；实际耗时见返回体的 settle_mode（converged/settled/fixed）与 settle_elapsed_ms
            max_results: 浮层条目上限（默认 20），调小会丢掉排序靠后的浮层
            analysis_id: 传 vtable_analysis 返回的 id（约 120 秒内有效）；执行前比对布局签名，坐标陈旧直接判失败
            expect_input: True 时额外验证交互后是否真的聚焦了 input/textarea/contenteditable
            compact: True（默认）时只回 status/target/locator/changes 极简高信噪比结果，显著节省 80%+ Context Token；False 返回全量底层诊断数据
        """
        return await automation.dom_interact(
            "click",
            name=name,
            role=role,
            description=description,
            css=css,
            xpath=xpath,
            text=text,
            placeholder=placeholder,
            x=x,
            y=y,
            frame=frame,
            timeout_ms=timeout_ms,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            analysis_id=analysis_id,
            expect_input=expect_input,
            compact=compact,
        )

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": False},
    )
    @instrument_tool
    async def ui_mouse_drag(
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        steps: int = 24,
        button: str = "left",
        hold_ms: int = 80,
        settle_ms: int = 200,
        observe_after: bool = False,
        max_results: int = 20,
        visual_ghost: bool = True,
    ) -> dict:
        """模拟真实鼠标拖拽轨迹将目标对象从起始位置平移至目标位置。

        调用前必须先取得起止坐标：VTable 表头列拖拽/调宽推荐直接用专属工具
        vtable_reorder_column / vtable_resize_column(内置防图标拦截)；自行拖拽时坐标取自
        ui_snapshot 的 [box] 或 vtable_analysis 的 point。若自行拖拽 Canvas 列表，
        重排起点务必落在列标题文字区、调宽起点压在分界线上——落在功能图标上会被拦截。

        本工具走底层真实事件流(移动至起点 → 悬停建立状态 → mousePressed → 连续 24+ 步
        平滑 mouseMoved → mouseReleased)，带虚拟光标拖拽可视化，既能触发 Canvas 内部
        对连续 mousemove 轨迹有位移阈值要求的列拖拽，也适用常规 DOM 元素的物理拖放。

        Args:
            start_x: 拖拽起点 X：顶层页面视口 CSS 像素，原点为视口左上角，越出视口直接报错
            start_y: 拖拽起点 Y：顶层页面视口 CSS 像素，必须落在视口内
            end_x: 拖拽终点 X：顶层页面视口 CSS 像素，同样必须在视口内
            end_y: 拖拽终点 Y：顶层页面视口 CSS 像素
            steps: 中间 mouseMoved 插值步数（默认 24，实际钳到 4–100），每步约 16ms，越大越平滑也越慢
            button: 按住并释放的鼠标键：left（默认）/ middle / right，其它值报错
            hold_ms: 按下后、释放前各等待的毫秒数（默认 80，总耗时约 2×hold_ms）；Canvas 拖拽靠它坐实 mousedown
            settle_ms: 释放后再静默等待的毫秒数（默认 200）；0=立即返回
            observe_after: 是否在本次拖拽中收集浮层（默认 False）；链尾一次观察比逐帧更省 token
            max_results: observe_after 时的浮层条目上限（默认 20）
            visual_ghost: True（默认）画 Windows 风格虚拟光标 + 轻量拖拽卡片（不克隆元素本身，只带一段短标签）
                并走物理事件流；False 关闭拖拽卡片，并对 HTML5 draggable 元素改走系统级直达拖放，Canvas 拖拽会失效
        """
        return await automation.mouse_drag(
            start_x=start_x,
            start_y=start_y,
            end_x=end_x,
            end_y=end_y,
            steps=steps,
            button=button,
            hold_ms=hold_ms,
            settle_ms=settle_ms,
            observe_after=observe_after,
            max_results=max_results,
            visual_ghost=visual_ghost,
        )

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def overlay_scan(max_results: int = 20, scope: str = "active") -> dict:
        """扫描 APS 页面当前可见的 Ant Design Portal / ARIA 浮层。

        默认 scope=active 只扫主文档和当前激活 iframe，scope=all 才扫所有 iframe；
        没有活动 iframe 时只扫主文档。返回 kind、文本、role、稳定 CSS selector、
        overlay_id、可见/可交互状态、box/page_box 与所属 frame。静态扫描不安装
        MutationObserver；短暂 message/toast 请改用 ui_interact 或
        vtable_cell_click(observe_after=True)。

        Args:
            max_results: 返回浮层条目上限（默认 20，最小按 1 处理）；若 overlays.truncated/has_more 为 true，必须补扫剩余浮层
            scope: 扫描范围：active（默认）=主文档+激活 iframe，all=全部 iframe；focused 等同 active
        """
        return await automation.scan_overlays(max_results=max_results, scope=scope)

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def ui_page_context(max_results: int = 10) -> dict:
        """返回当前页面、活动 iframe 和聚焦浮层的紧凑上下文。

        这是 AI 每次准备下一步交互时的低 token 入口；只有需要详细控件树时
        才继续调用 ui_snapshot。

        Args:
            max_results: 同时限制列出的 frame 个数与可见浮层条数（默认 10）；检查 coverage.frames 和 coverage.visible_overlays，未完整时继续用 frame_id 定向检查
        """
        return await automation.page_context(max_results=max_results)

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def ui_analyze_scope(max_controls: int = 40, max_overlays: int = 10) -> dict:
        """只分析当前活动页面范围或所聚焦浮层内的可操作控件。

        有 Modal/Drawer/Dropdown/Popover 时裁剪底层页面；否则只扫描顶层当前文档与
        激活的 AntD Tab iframe。结果是紧凑 role/name/CSS 定位清单，不展开整页 DOM。

        Args:
            max_controls: 可操作控件条目上限（默认 40）；响应含 control_total_count 与 coverage.controls，若不完整需提高上限或按 focus layer/selector 定向扫描
            max_overlays: 参与裁剪判断的浮层条目上限（默认 10）
        """
        return await automation.analyze_scope(max_controls=max_controls, max_overlays=max_overlays)

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def overlay_observe(
        settle_ms: int = 300, stop: bool = True, max_results: int = 20
    ) -> dict:
        """在限定窗口内收集 APS Ant Design 浮层事件，覆盖全部 iframe。

        适合已由其他工具或人工操作触发页面后的诊断；默认取样后停止监听。
        事件缓冲区溢出返回 events_truncated/dropped_event_count；响应上限另由 events_limit_truncated 和 coverage.events 标记。

        Args:
            settle_ms: 观察窗口毫秒数（默认 300，限 0–2000，越界报错）；调小会漏掉慢动画浮层。
                该值是硬上限：观察到 DOM 变更并静默约 25ms 后自适应提前收口，无变更则等满窗口；
                实际耗时见返回体的 settle_mode 与 settle_elapsed_ms
            stop: True（默认）取样后卸载监听；False 保持常驻，供后续调用继续累积事件
            max_results: baseline/events/overlays 各自条数上限（默认 20）；调小会丢事件时间线
        """
        return await automation.observe_overlays(
            settle_ms=settle_ms, stop=stop, max_results=max_results
        )

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": False},
    )
    @instrument_tool
    async def ui_interact(
        action: str,
        role: str | None = None,
        name: str | None = None,
        description: str | None = None,
        text: str | None = None,
        placeholder: str | None = None,
        css: str | None = None,
        xpath: str | None = None,
        x: float | None = None,
        y: float | None = None,
        value: str | None = None,
        key: str | None = None,
        frame: str | None = None,
        in_iframe: bool = True,
        timeout_ms: float = 3_000,
        observe_after: bool = True,
        settle_ms: int = 300,
        max_results: int = 20,
        analysis_id: str | None = None,
        expect_input: bool = False,
        compact: bool = True,
    ) -> dict:
        """统一表单与复合交互入口：执行非点击类 DOM 操作（输入、按键、悬停、勾选）。

        主要用于 fill/type 文本填入（必须带 value）、press 按键（必须带 key）、hover、check；
        单纯点击请优先使用 ui_click，操作 AntD 下拉框/日期框请用专用工具。
        定位候选顺序与 ui_click 一致(CSS → AX → XPath → text → 坐标回退)；未显式指定
        frame 时优先激活 iframe 再回退顶层。expect_input=True 时验证交互后是否真的
        聚焦了可编辑控件。

        Args:
            action: 动作类型：click/dblclick/rightclick/hover/fill/type/press/check/uncheck/select；fill、type、select 必须带 value，press 必须带 key
            role: ARIA 角色名（button/textbox/combobox/checkbox…），与 name 走 get_by_role 匹配
            name: 与 role 配对的无障碍名，exact 全等匹配；不传 role 时不生效
            description: 与 role 配对的 aria-description，用于区分同名控件；不传 role 时忽略
            text: 元素可见文本，exact 全等匹配，优先级低于 xpath
            placeholder: 输入框 placeholder，exact 全等匹配，定位器里优先级最低
            css: CSS 选择器，优先级最高的定位；命中多个可见元素直接报歧义错而非挑第一个
            xpath: XPath 表达式（不带 xpath= 前缀），排在 css 与 AX role 之后尝试
            x: 坐标回退 X：顶层页面视口 CSS 像素，原点在视口左上角，必须与 y 成对给
            y: 坐标回退 Y：顶层页面视口 CSS 像素，必须与 x 成对给
            value: fill/type 写入的文本（逐字符打字机输入），或 select 要选的选项文本
            key: press 要按的 Playwright 键名，如 Enter、Tab、Control+a；仅 press 使用
            frame: 目标 frame：省略=先激活 AntD Tab iframe 再顶层；可传 main/top/active/vtable 或 frame_id
            in_iframe: True（默认）未显式给 frame 时先在激活 iframe 里找再回退顶层；False 只搜顶层文档
            timeout_ms: 动作执行超时毫秒数（默认 3000）；定位阶段为单次尝试不轮询等待，纯坐标动作也不消费该值
            observe_after: 动作后是否收集 Portal/提示/下拉（默认 True）；不需要浮层证据时关掉省 token
            settle_ms: 浮层观察窗口毫秒数（默认 300，限 0–2000，越界报错）；仅 observe_after 时生效。
                该值是硬上限：窗口内观察到 DOM 变更、且变更后静默约 25ms（无 loading 骨架）即自适应提前收口，
                否则等满整个窗口；实际耗时见返回体的 settle_mode（converged/settled/fixed）与 settle_elapsed_ms
            max_results: 浮层条目上限（默认 20），调小会丢掉排序靠后的浮层
            analysis_id: 传 vtable_analysis 返回的 id（约 120 秒内有效）；执行前比对布局签名，坐标陈旧直接判失败
            expect_input: True 时额外验证交互后是否真的聚焦了 input/textarea/contenteditable
            compact: True（默认）时只回 status/target/locator/changes 极简高信噪比结果，显著节省 80%+ Context Token；False 返回全量底层诊断数据
        """
        return await automation.dom_interact(
            action,
            role=role,
            name=name,
            description=description,
            text=text,
            placeholder=placeholder,
            css=css,
            xpath=xpath,
            x=x,
            y=y,
            value=value,
            key=key,
            frame=frame,
            in_iframe=in_iframe,
            timeout_ms=timeout_ms,
            observe_after=observe_after,
            settle_ms=settle_ms,
            max_results=max_results,
            analysis_id=analysis_id,
            expect_input=expect_input,
            compact=compact,
        )

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def ui_snapshot(
        selector: str | None = None,
        frame: str | None = None,
        depth: int = 6,
        boxes: bool = True,
        ai_mode: bool = True,
        nth: int | None = None,
        visible_only: bool = False,
        max_elements: int = 5,
        timeout: float = 3.0,
        prune_noise: bool = True,
    ) -> dict:
        """【页面元素识别与观察首选工具】抓取页面 aria 快照(mode='ai' + boxes)，给 AI 一张精准的“语义之眼”。

        所有常规页面元素识别、文本提取、定位决策必须首选本工具：把 accessibility 树
        (含 [ref=xx] 引用和 [box=x,y,w,h] 视口坐标)喂给 AI，毫秒级响应且极省 Token。
        交互前先读快照再决定点哪个；VTable 单元格在 canvas 里不进 aria 树，查表格请直接用 vtable_*。

        关键行为：
        - selector 命中多个元素时自动遍历前 max_elements 个，标注匹配序号、可见性与 `>> nth=X` 定位路径(消除 strict mode violation)。
        - 默认作用域是**激活的业务 iframe**(iframe 套壳应用的内容都在激活模块里)，拍顶层只能得到侧边栏骨架；响应的 scope/frame_id 说明拍了谁。
        - 默认剪除"整棵子树无可访问名"的结构包装节点(实测七成 token 花在重复骨架上)，prune_stats 给出剪除量。
        - frame 语义见 Args；深度上限 8，快照最多返回 24,000 字符；selector 未命中时在 timeout 内优雅返回 not_found。

        Args:
            selector: CSS/XPath 选择器；省略则快照整个作用域根节点，非空则只拍其命中子树
            frame: 作用域 frame：省略=激活业务 iframe，'main'=顶层文档，'active'/'vtable'=按语义解析，或传 frame_id
            depth: aria 树最大深度（默认 6，上限 8）；越小越省 token
            boxes: 是否输出 [box=x,y,w,h] 视口坐标（默认 True，交互定位需要）
            ai_mode: 使用 AI 友好紧凑格式（默认 True）；False 走 Playwright 原始 YAML 格式
            nth: 0-based 序号，从 selector 的多个命中里精确取第 nth 个
            visible_only: 仅快照当前可见元素（默认 False=含未渲染的隐藏节点）
            max_elements: selector 多命中时最多遍历几个，防止大结果集撑爆上下文
            timeout: selector 未命中时的等待秒数，默认 3.0
            prune_noise: 剪除无名的结构包装子树（默认 True；false 返回原始 aria 树）
        """
        return await automation.dom_snapshot(
            selector=selector,
            frame=frame,
            depth=depth,
            boxes=boxes,
            ai_mode=ai_mode,
            nth=nth,
            visible_only=visible_only,
            max_elements=max_elements,
            timeout=timeout,
            prune_noise=prune_noise,
        )

    @mcp.tool(
        annotations={"read_only_hint": True},
    )
    @instrument_tool
    async def ui_screenshot(
        role: str | None = None,
        name: str | None = None,
        description: str | None = None,
        text: str | None = None,
        placeholder: str | None = None,
        css: str | None = None,
        xpath: str | None = None,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        frame: str | None = None,
        in_iframe: bool = True,
        padding: float = 0,
        image_format: str = "png",
        filename: str | None = None,
        quality: int | None = None,
        timeout_ms: float = 3_000,
        screenshot_timeout_ms: float = 15_000,
        max_bytes: int = 2_000_000,
    ) -> dict:
        """【最低优先级 / 兜底工具】截取指定 DOM 元素或顶层 viewport 区域图像并保存到工作区。

        仅允许两种场景调用：人类用户显式要求截图/留档；或页面遇到纯无语义 Canvas、
        图片验证码、极端白屏等必须依靠视觉定位的死角。严禁常规化调用本工具识别页面，
        严禁根据截图像素反推点击坐标(高分屏缩放会偏移)——语义工具优先级见系统指令。

        元素定位顺序与 ui_interact 相同：CSS → AX role/name/description → XPath →
        text/placeholder。frame 未指定时优先活动 iframe。若没有可用定位器，可传
        x/y/width/height 使用顶层 viewport CSS 像素矩形；不传定位器与坐标时默认捕获
        当前完整视口。截图保存到 .qa-automation/screenshots/，响应只返回文件路径(path)、
        裁剪框、frame、定位来源与摘要哈希，不回传整图 base64；需要看像素时打开 path 即可。

        Args:
            role: ARIA 角色名，与 name 走 get_by_role 匹配被摄元素
            name: 与 role 配对的无障碍名，exact 全等匹配；不传 role 时不生效
            description: 与 role 配对的 aria-description，用于区分同名控件
            text: 元素可见文本，exact 全等匹配，优先级低于 xpath
            placeholder: 输入框 placeholder，exact 全等匹配，定位器里优先级最低
            css: CSS 选择器，优先级最高的定位；命中多个可见元素直接报歧义错
            xpath: XPath 表达式（不带 xpath= 前缀），排在 css 与 AX role 之后尝试
            x: 视口矩形左边界，顶层页面视口 CSS 像素；必须与 y/width/height 四个一起给
            y: 视口矩形上边界，顶层页面视口 CSS 像素，原点为视口左上角
            width: 视口矩形宽度，CSS 像素，必须为正数
            height: 视口矩形高度，CSS 像素，必须为正数
            frame: 目标 frame：省略=先激活 iframe 再顶层；可传 main/top/active/vtable 或 frame_id
            in_iframe: True（默认）未给 frame 时先在激活 iframe 里找定位器；False 只搜顶层文档
            padding: 元素截图向外扩的 CSS 像素边距（默认 0，限 0–200，越界报错）；不影响视口矩形模式
            image_format: 图片格式：png（默认）或 jpeg，其它值报错；jpeg 通常体积小很多
            filename: 只能写截图目录内的文件名（带路径会被拒）；后缀须与 image_format 一致，省略则自动补并保证唯一
            quality: 仅 jpeg 生效的压缩质量，1–100；png 传值会被忽略
            timeout_ms: 定位与等待元素可见的超时毫秒数（默认 3000）
            screenshot_timeout_ms: 截图本身的硬超时毫秒数（默认 15000）。
                Chromium 为 clip 截图会临时把视口撑到裁剪框尺寸，一旦这次截图被中断，
                override 会残留并把页面视口锁在该尺寸上（实测 900x383），导致后续
                vtable / 浮层 / 点击坐标全部错位。因此截图超时后会立即执行窗口与视口
                复位再抛错；响应里的 viewport_guard.restored=false 表示复位未成功。
            max_bytes: 允许回传的最大字节数（限 1024–20000000，默认 200 万）；超限仍存盘但判 failed 且只给 path
        """
        return await automation.screenshot_element(
            role=role,
            name=name,
            description=description,
            text=text,
            placeholder=placeholder,
            css=css,
            xpath=xpath,
            x=x,
            y=y,
            width=width,
            height=height,
            frame=frame,
            in_iframe=in_iframe,
            padding=padding,
            image_format=image_format,
            filename=filename,
            quality=quality,
            timeout_ms=timeout_ms,
            screenshot_timeout_ms=screenshot_timeout_ms,
            max_bytes=max_bytes,
        )

    return mcp
