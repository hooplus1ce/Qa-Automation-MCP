"""General page, iframe, overlay, and screenshot interaction tools."""

from __future__ import annotations

from fastmcp import FastMCP

import qa_automation as automation

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Page Automation")

    @mcp.tool()
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
        compact: bool = False,
    ) -> dict:
        """统一点击页面控件，并即时返回 Portal、提示和聚焦浮层。

        可同时传多个来自分析结果的候选定位；执行顺序固定为 CSS → AX role/name
        (可带 description) → XPath → text/placeholder → 顶层视口绝对坐标。坐标仅作为
        最终回退，且传 analysis_id 时会拒绝陈旧的 VTable 分析坐标。
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

    @mcp.tool()
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
    ) -> dict:
        """模拟真实鼠标拖拽轨迹将目标对象从起始位置平移至目标位置。

        【重要前置约束】：
        调用本工具执行拖拽前，调用方必须先获取待拖拽对象的起始坐标（start_x, start_y）与目标位置的结束坐标（end_x, end_y）：
        - 若为 VTable 等 Canvas 绘制的列表/表格（例如表头列拖拽重排顺序、列宽拖拽调整）：请先调用
          vtable_analysis 获取待拖动列头与目标列头的 point 视口坐标；
        - 若为常规 DOM 元素、滑块或可拖拽条目：请先调用 ui_analyze_scope 或 ui_snapshot
          获取起始控件与目标落点的绝对视口坐标（page_box）。

        本工具使用底层真实事件流（移动至起点 → 按下 mousePressed → 连续 24+ 步平滑细密轨迹移动
        mouseMoved → 释放 mouseReleased），带全流程虚拟光标拖拽可视化，既能完美触发 Canvas 列表内部
        对连续 mousemove 轨迹有严格位移阈值要求的列拖拽重排与调宽，也能通用处理常规 DOM 元素的物理拖放。

        【VTable 列拖拽注意】起点必须落在列标题文字区，切勿选在表头功能图标（排序/筛选/冻结/下拉，
        坐标见 vtable_analysis 的 header_icons）上：这些图标会拦截 mousedown，导致按住拖动不进入列重排，
        释放后仅等效一次单击、列序不变。可把起点取在标题文字左端以避开图标。
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
        )

    @mcp.tool()
    @instrument_tool
    async def overlay_scan(max_results: int = 20, scope: str = "active") -> dict:
        """扫描当前页面范围内可见的 Ant Design Portal / ARIA 浮层。

        默认 scope=active 只扫描主文档和当前激活 iframe；scope=all 才扫描所有 iframe。
        返回每个浮层的 kind、文本、role、定位摘要、可见性、box 以及所属 frame。
        对短暂 message/toast，请改用 `ui_click` 或
        `vtable_cell_click(observe_after=True)`，以免在单次静态扫描前消失。
        """
        return await automation.scan_overlays(max_results=max_results, scope=scope)

    @mcp.tool()
    @instrument_tool
    async def ui_page_context(max_results: int = 10) -> dict:
        """返回当前页面、活动 iframe 和聚焦浮层的紧凑上下文。

        这是 AI 每次准备下一步交互时的低 token 入口；只有需要详细控件树时
        才继续调用 ui_snapshot。
        """
        return await automation.page_context(max_results=max_results)

    @mcp.tool()
    @instrument_tool
    async def ui_analyze_scope(max_controls: int = 40, max_overlays: int = 10) -> dict:
        """只分析当前活动页面范围或所聚焦浮层内的可操作控件。

        有 Modal/Drawer/Dropdown/Popover 时裁剪底层页面；否则只扫描顶层当前文档与
        激活的 AntD Tab iframe。结果是紧凑 role/name/CSS 定位清单，不展开整页 DOM。
        """
        return await automation.analyze_scope(
            max_controls=max_controls, max_overlays=max_overlays
        )

    @mcp.tool()
    @instrument_tool
    async def overlay_observe(
        settle_ms: int = 300, stop: bool = True, max_results: int = 20
    ) -> dict:
        """在限定窗口内收集 Ant Design Portal/ARIA 浮层事件，覆盖全部 iframe。

        适合已由其他工具或人工操作触发页面交互后的诊断；默认在取样后停止监听。
        """
        return await automation.observe_overlays(
            settle_ms=settle_ms, stop=stop, max_results=max_results
        )

    @mcp.tool()
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
        compact: bool = False,
    ) -> dict:
        """在当前页面/活动 iframe 中执行统一 DOM 交互并返回聚焦浮层结果。

        分析器返回 CSS 时优先 CSS；否则按 AX role/name/description、XPath、text/
        placeholder 依次尝试。x/y 是顶层 viewport 绝对 CSS 像素，只在前述候选都无法
        解析时作为可信点击回退。坐标应直接取自 `vtable_analysis`，带 analysis_id 会在
        执行前校验页面、iframe、滚动和布局。expect_input=True 时会验证
        本次交互后是否真的出现并聚焦 input/textarea/contenteditable。未显式指定
        frame 时优先当前激活的 AntD Tab iframe，再回退顶层文档。分析结果中的
        frame="active" / "top" 可固定上下文。默认点击后立即
        观察 Portal、消息、下拉和通知，限制 max_results 以控制 MCP token。
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

    @mcp.tool()
    @instrument_tool
    async def ui_snapshot(
        selector: str | None = None,
        frame: str | None = None,
        depth: int = 6,
        boxes: bool = True,
        ai_mode: bool = True,
    ) -> dict:
        """抓取页面 aria 快照(mode='ai' + boxes),给 AI 一张"语义之眼"。

        官方 Playwright MCP 范式:把 accessibility 树(含 [ref=xx] 引用和 [box=x,y,w,h]
        视口坐标)喂给 AI。VTable 本体是 canvas(单元格不进 a11y 树,仍走确定性几何定位),
        但工具栏/弹窗/编辑器输入框都在树里 —— 交互前先读快照,再决定点哪个。
        selector 非空时只快照该选择器命中的子树。

        frame=None → 主页面;frame="active" → 当前激活的 AntD Tab iframe;
        frame="vtable" → 自动定位含表格的 iframe;
        其它值按 iframe name 或 URL 子串匹配(如 "application" / "scm-spo")。
        """
        return await automation.dom_snapshot(
            selector=selector,
            frame=frame,
            depth=depth,
            boxes=boxes,
            ai_mode=ai_mode,
        )

    @mcp.tool()
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
        max_bytes: int = 2_000_000,
    ) -> dict:
        """截取指定 DOM 元素或顶层 viewport 区域并保存到工作区。

        元素定位顺序与 ui_interact 相同：CSS → AX role/name/description → XPath →
        text/placeholder。frame 未指定时优先活动 iframe。若没有可用定位器，可传
        x/y/width/height 使用顶层 viewport CSS 像素矩形；截图不会静默把 iframe 内坐标
        当成顶层坐标。结果包含工作区截图路径、base64 图像、裁剪框、frame、定位来源和
        摘要哈希；filename 只能指定截图目录内的文件名。
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
            max_bytes=max_bytes,
        )

    return mcp
