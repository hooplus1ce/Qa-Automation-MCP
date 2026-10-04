"""Ant Design specialized interaction tools tailored for Playwright.

Covers:
- `wait_message`: Zero-polling MutationObserver for transient toast/notification bubbles
- `antd_select`: Geometric dropdown attribution score, option click, value verification, auto ESC
- `antd_date_pick`: AntD v3 & v4/v5 date cell selection
- `nav_menu`: Deterministic APS micro-frontend module navigation via header tabs & search
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from playwright.async_api import Frame, Locator, Page

from .browser import _frame_id, current_page
from .profiles import active_profile

# 消息浮层监听器 JavaScript（基于浏览器内联 MutationObserver，消除死等与跨进程轮询）
WAIT_MESSAGE_OBSERVER_JS = r"""async function({patternStr, timeoutMs}) {
    var regex;
    try {
        regex = new RegExp(patternStr, 'i');
    } catch(e) {
        // fail-closed：非法正则不匹配任何内容。旧版降级为 '.+' 会把第一条 toast
        // 当作命中返回 found=true，产出假阳性（Python 侧已前置校验，此处是兜底）。
        regex = null;
    }

    function getRoots() {
        var roots = [document];
        try {
            if (window.top && window.top !== window && window.top.document) {
                roots.push(window.top.document);
            }
        } catch(e) {}
        // 探测所有 iframe 内部文档
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            try {
                var idoc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                if (idoc && roots.indexOf(idoc) === -1) roots.push(idoc);
            } catch(e) {}
        }
        return roots;
    }

    function scan() {
        var roots = getRoots();
        var allSeen = [];
        var matched = null;
        for (var r = 0; r < roots.length; r++) {
            var doc = roots[r];
            if (!doc || !doc.querySelectorAll) continue;
            var nodes = doc.querySelectorAll('.ant-message-notice, .ant-notification-notice, .layui-layer-msg, .ant-modal-confirm-body');
            for (var i = 0; i < nodes.length; i++) {
                var n = nodes[i];
                var txt = String(n.textContent || n.innerText || '').replace(/\s+/g, ' ').trim();
                if (!txt) continue;
                allSeen.push(txt.slice(0, 200));
                if (!matched && regex && regex.test(txt)) {
                    var cls = String(n.className || '').toLowerCase();
                    var source = cls.indexOf('ant-notification-notice') >= 0 ? 'notification'
                               : cls.indexOf('layui-layer') >= 0 ? 'layer'
                               : 'message';
                    var level = null;
                    if (source !== 'layer') {
                        var host = n.closest ? n.closest('.ant-message-notice, .ant-notification-notice') : null;
                        var hcls = String((host || n).className || '').toLowerCase();
                        level = hcls.indexOf('success') >= 0 ? 'success'
                              : hcls.indexOf('error') >= 0 ? 'error'
                              : hcls.indexOf('warn') >= 0 ? 'warning' : 'info';
                    }
                    matched = {
                        found: true,
                        text: txt.slice(0, 200),
                        source: source,
                        level: level
                    };
                }
            }
        }
        return { matched: matched, all: allSeen };
    }

    var immediate = scan();
    if (immediate.matched || timeoutMs <= 0) {
        return {
            found: Boolean(immediate.matched),
            text: immediate.matched ? immediate.matched.text : null,
            source: immediate.matched ? immediate.matched.source : null,
            level: immediate.matched ? immediate.matched.level : null,
            all: immediate.all
        };
    }

    return new Promise(function(resolve) {
        var observers = [];
        var cleaned = false;

        function cleanup() {
            if (cleaned) return;
            cleaned = true;
            for (var i = 0; i < observers.length; i++) {
                try { observers[i].disconnect(); } catch(e) {}
            }
        }

        var timer = setTimeout(function() {
            cleanup();
            var last = scan();
            resolve({
                found: false,
                text: null,
                source: null,
                level: null,
                all: last.all,
                pattern_ok: regex !== null
            });
        }, Math.max(timeoutMs, 50));

        function onMutation() {
            var res = scan();
            if (res.matched) {
                clearTimeout(timer);
                cleanup();
                resolve({
                    found: true,
                    text: res.matched.text,
                    source: res.matched.source,
                    level: res.matched.level,
                    all: res.all
                });
            }
        }

        var roots = getRoots();
        for (var j = 0; j < roots.length; j++) {
            var doc = roots[j];
            try {
                var target = doc.body || doc.documentElement;
                if (target) {
                    var obs = new MutationObserver(onMutation);
                    obs.observe(target, {
                        childList: true,
                        subtree: true,
                        characterData: true
                    });
                    observers.push(obs);
                }
            } catch(e) {}
        }

        if (observers.length === 0) {
            clearTimeout(timer);
            resolve({
                found: false,
                text: null,
                source: null,
                level: null,
                all: immediate.all,
                pattern_ok: regex !== null
            });
        }
    });
}"""

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _resolve_target_frame(page: Page, frame: str | None = None) -> Page | Frame:
    if not frame or frame in ("main", "top"):
        return page
    if frame == "active":
        profile = active_profile()
        selector = profile.active_iframe_selector
        try:
            iframe_ele = await page.query_selector(selector)
            if iframe_ele:
                content_frame = await iframe_ele.content_frame()
                if content_frame:
                    return content_frame
        except Exception:
            pass
        return page
    # 按 frame_id 或 name 查找
    for f in page.frames:
        if _frame_id(page, f) == frame or f.name == frame:
            return f
    return page


async def wait_message(
    pattern: str | None = None,
    timeout: float = 5.0,
    raise_if_not_found: bool = True,
) -> dict[str, Any]:
    """等待或即时快照全局操作结果气泡（message / notification / Modal 提示 / layui-layer）。

    Args:
        pattern: 正则或关键字（如 "保存成功"、"成功|完成"）；空表示匹配任意提示。非法正则直接报错
        timeout: 0=即时快照不等待；>0=最长等待秒数（默认 5.0）
        raise_if_not_found: 超时未匹配时是否抛出异常（默认 True）
    """
    page = await current_page()
    clean_pat = (pattern or "").strip() or r".+"
    if pattern:
        # 显式传入的正则必须先在 Python 侧校验：旧版 JS 在 RegExp 编译失败时静默
        # 降级为 '.+'，把第一条 toast 当作匹配成功返回 found=true（fail-open 假阳性）。
        try:
            re.compile(clean_pat, re.I)
        except re.error as e:
            raise ValueError(f"wait_message 的 pattern 不是合法正则: {e}") from None
    timeout_ms = int(max(timeout, 0.0) * 1000)
    start_t = time.time()

    try:
        data = await page.evaluate(
            WAIT_MESSAGE_OBSERVER_JS,
            {"patternStr": clean_pat, "timeoutMs": timeout_ms},
        )
    except Exception as exc:
        data = {"found": False, "text": None, "source": None, "level": None, "all": [], "error": str(exc)}

    if pattern and data.get("pattern_ok") is False:
        raise ValueError(
            f"wait_message 的 pattern 无法在浏览器端编译为 RegExp: {pattern!r}"
        )

    elapsed = round(time.time() - start_t, 3)
    found = bool(data.get("found"))
    all_seen = data.get("all") or []

    if not found and raise_if_not_found and timeout > 0:
        captured_str = "、".join(repr(m) for m in all_seen) or "（未捕捉到任何消息气泡）"
        raise RuntimeError(
            f"在 {elapsed}s 内未等到匹配 [{pattern}] 的操作结果气泡。捕获到的全部气泡: {captured_str}"
        )

    return {
        "ok": found or timeout <= 0,
        "found": found,
        "pattern": pattern,
        "matched_text": data.get("text"),
        "source": data.get("source"),
        "level": data.get("level"),
        "elapsed_seconds": elapsed,
        "all_messages": all_seen,
    }


def _dropdown_score(
    t_box: dict[str, float],
    d_box: dict[str, float],
) -> tuple[float, float] | None:
    """计算下拉浮层与触发框的几何归属评分: (水平重叠率, 垂直间距)。

    AntD 稳定几何特征：浮层顶边贴触发框底边（或底边贴顶边），且水平大幅重叠。
    用于坚决避免误取顶部导航菜单或其它无关控件的残留浮层。
    """
    t_l, t_t, t_w, t_h = t_box["x"], t_box["y"], t_box["width"], t_box["height"]
    d_l, d_t, d_w, d_h = d_box["x"], d_box["y"], d_box["width"], d_box["height"]
    t_r, t_b = t_l + t_w, t_t + t_h
    d_r, d_b = d_l + d_w, d_t + d_h

    overlap = max(0.0, min(d_r, t_r) - max(d_l, t_l))
    x_ratio = overlap / max(min(d_w, t_w), 1.0)
    gap = min(abs(d_t - t_b), abs(d_b - t_t))
    return (x_ratio, gap)

async def _guarantee_dropdown_closed(
    page: Page,
    target: Page | Frame,
    trigger_loc: Locator | None,
    dropdown_loc: Locator | None,
    max_wait_ms: int = 1500,
) -> bool:
    """确保下拉浮层彻底收起，防止遮挡页面下方的确定/保存按钮。

    采用四段式稳妥收起与隐藏确认机制：
    1. 键盘 ESC：向当前页面及目标 frame 派发 Escape 键；
    2. 触发框复击：若浮层依然可见（部分多选框拦截了 ESC），点击 Select 触发框再次触发 toggle 关闭；
    3. 安全中立空白区点击：若依然可见，点击触发框上方或空白处使其失焦（Blur）；
    4. 轮询隐藏确认：等待浮层包含 .ant-select-dropdown-hidden 或被从 DOM 移除。
    """
    if dropdown_loc is None:
        return True

    try:
        if not await dropdown_loc.is_visible():
            return True
    except Exception:
        return True

    # 阶段一：键盘 Escape 键收起（兼容顶层与 iframe 内部焦点）
    try:
        await page.keyboard.press("Escape")
        if target != page and hasattr(target, "keyboard"):
            try:
                await target.keyboard.press("Escape")
            except Exception:
                pass
        await page.wait_for_timeout(100)
        if not await dropdown_loc.is_visible():
            return True
    except Exception:
        pass

    # 阶段二：点击触发框进行 toggle 关闭（多选下拉框的常见交互）
    if trigger_loc is not None:
        try:
            arrow = trigger_loc.locator(".ant-select-arrow, .ant-select-suffix").first
            if await arrow.is_visible():
                await arrow.click()
            else:
                await trigger_loc.click()
            await page.wait_for_timeout(120)
            if not await dropdown_loc.is_visible():
                return True
        except Exception:
            pass

    # 阶段三：安全中立空白区微点击以触发全局 blur
    if trigger_loc is not None:
        try:
            t_box = await trigger_loc.bounding_box()
            if t_box:
                safe_x = max(10.0, t_box["x"] + t_box["width"] / 2)
                safe_y = max(10.0, t_box["y"] - 15.0)
                await page.mouse.click(safe_x, safe_y)
                await page.wait_for_timeout(100)
                if not await dropdown_loc.is_visible():
                    return True
        except Exception:
            pass

    # 阶段四：轮询等待隐藏状态确认
    deadline = time.monotonic() + (max_wait_ms / 1000)
    while time.monotonic() < deadline:
        try:
            if not await dropdown_loc.is_visible():
                return True
        except Exception:
            return True
        await asyncio.sleep(0.05)

    return False


async def antd_select(
    option_text: str | list[str],
    css: str | None = None,
    xpath: str | None = None,
    text: str | None = None,
    search_text: str | None = None,
    frame: str | None = None,
    close_multi: bool = True,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """操作 AntD 下拉选择框（Select）：通过几何特征匹配准确的浮层，点击选项并复核已选值。

    Args:
        option_text: 要选择的目标选项文本（如 "按部门审批"）
        css: Select 触发框的 CSS 选择器（如 ".ant-select"、"#approver-select"）
        xpath: 触发框 XPath
        text: 触发框上的可见文本（用于精确匹配）
        search_text: 若 Select 支持搜索过滤，在展开后输入的搜索关键字
        frame: 所在 frame（默认自动判定 active iframe 或顶层）
        close_multi: 选择完成后若浮层未收起（多选框），自动按 ESC 键收回（默认 True）
        timeout_ms: 整体超时毫秒数（默认 5000ms）
    """
    page = await current_page()
    target = await _resolve_target_frame(page, frame)

    # 1. 定位并点击展开下拉框
    trigger_loc: Locator | None = None
    if css:
        trigger_loc = target.locator(css).first
    elif xpath:
        trigger_loc = target.locator(f"xpath={xpath}").first
    elif text:
        trigger_loc = target.get_by_text(text, exact=True).first
    else:
        # 回退寻找通用的可操作 select
        trigger_loc = target.locator(".ant-select").first

    if not await trigger_loc.is_visible():
        raise RuntimeError("未找到可见的 AntD Select 触发框控件")

    await trigger_loc.scroll_into_view_if_needed()
    t_box = await trigger_loc.bounding_box()
    if not t_box:
        raise RuntimeError("无法测量 Select 触发框的视口几何尺寸")

    # 点击展开
    await trigger_loc.click()
    await page.wait_for_timeout(250)

    # 若需要搜索，在 input 中输入
    if search_text:
        search_input = trigger_loc.locator(".ant-select-search__field, input").first
        if await search_input.is_visible():
            await search_input.fill(search_text)
            await page.wait_for_timeout(200)

    # 2. 扫描当前文档与顶层文档中所有可见的 AntD 下拉浮层
    candidate_roots = [target]
    if target != page:
        candidate_roots.append(page)

    best_dropdown: Locator | None = None
    best_score: tuple[float, float] | None = None

    for root in candidate_roots:
        dds = root.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)")
        count = await dds.count()
        for i in range(count):
            dd = dds.nth(i)
            if not await dd.is_visible():
                continue
            d_box = await dd.bounding_box()
            if not d_box:
                continue
            score = _dropdown_score(t_box, d_box)
            if not score:
                continue
            x_ratio, gap = score
            # 过滤明显无关的浮层（横向重叠度过低或距离过远）
            if x_ratio < 0.25 or gap > 90.0:
                continue
            key = (gap, -x_ratio)
            if best_score is None or key < best_score:
                best_dropdown = dd
                best_score = key

    if not best_dropdown:
        # 兜底：若几何比对偏严，直接获取任一可见 dropdown
        fallback = target.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").first
        if await fallback.is_visible():
            best_dropdown = fallback
        else:
            raise RuntimeError("未找到与目标 Select 关联的下拉菜单浮层")

    # 3. 规范化待选目标列表（支持单个选项、选项数组或逗号分隔多选）
    if isinstance(option_text, list):
        target_options = [str(o).strip() for o in option_text if str(o).strip()]
    elif "," in option_text or "，" in option_text:
        target_options = [s.strip() for s in re.split(r"[,，]", option_text) if s.strip()]
    else:
        target_options = [str(option_text).strip()]

    selected_options = []
    for opt_title in target_options:
        # 在匹配到的下拉浮层中寻找目标选项
        # 支持 v4/v5 (.ant-select-item-option) 与 v3 (.ant-select-dropdown-menu-item)
        opt_locators = [
            best_dropdown.locator(".ant-select-item-option").filter(has_text=opt_title).first,
            best_dropdown.locator(".ant-select-dropdown-menu-item").filter(has_text=opt_title).first,
            best_dropdown.get_by_text(opt_title, exact=True).first,
        ]

        clicked_option = False
        for opt in opt_locators:
            try:
                if await opt.is_visible():
                    await opt.scroll_into_view_if_needed()
                    await opt.click()
                    clicked_option = True
                    selected_options.append(opt_title)
                    break
            except Exception:
                continue

        if not clicked_option:
            # 收集当前可见选项供错误报告
            visible_options = []
            try:
                items = best_dropdown.locator(".ant-select-item-option, .ant-select-dropdown-menu-item")
                for j in range(min(await items.count(), 10)):
                    txt = (await items.nth(j).text_content() or "").strip()
                    if txt:
                        visible_options.append(txt)
            except Exception:
                pass
            opts_str = "、".join(repr(o) for o in visible_options) or "（空）"
            raise RuntimeError(f"在下拉浮层中未找到选项 [{opt_title}]。当前可见候选: {opts_str}")

        await page.wait_for_timeout(100)

    await page.wait_for_timeout(150)

    # 4. 多选或浮层残留时彻底收回（四段式保障，严防遮挡底部确定/保存按钮）
    dropdown_closed = True
    if close_multi:
        dropdown_closed = await _guarantee_dropdown_closed(
            page, target, trigger_loc, best_dropdown, max_wait_ms=1200
        )

    # 5. 校验已选值回读
    displayed_values: list[str] = []
    for sel in (".ant-select-selection-item", ".ant-select-selection-selected-value", ".ant-select-selection-overflow-item"):
        items = trigger_loc.locator(sel)
        try:
            cnt = await items.count()
            for i in range(cnt):
                txt = (await items.nth(i).text_content() or "").strip()
                if txt and txt not in displayed_values:
                    displayed_values.append(txt)
        except Exception:
            continue

    displayed_str = " / ".join(displayed_values)
    first_option = target_options[0] if target_options else ""
    opts_display = ", ".join(selected_options)

    return {
        "ok": True,
        "selected_option": first_option if len(target_options) == 1 else target_options,
        "selected_options": selected_options,
        "displayed_value": displayed_str or None,
        "dropdown_closed": dropdown_closed,
        "verified": all(any(opt in dv for dv in displayed_values) for opt in selected_options) if displayed_values else None,
        "message": f"已选择下拉选项 [{opts_display}]" + (f"，当前展示 [{displayed_str}]" if displayed_str else "") + ("（浮层已收起）" if dropdown_closed else ""),
    }


async def antd_date_pick(
    date: str,
    css: str | None = None,
    frame: str | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """操作 AntD 日期选择器（DatePicker）：自动兼容 v3 及 v4+ 日历弹层并点击指定日期。

    Args:
        date: 目标日期，格式规范为 YYYY-MM-DD（如 "2026-09-25"）
        css: DatePicker 容器或输入框 CSS 选择器（如 ".ant-picker"）
        frame: 所在 frame
        timeout_ms: 超时毫秒数
    """
    if not DATE_RE.match(date):
        raise ValueError(f"日期格式应为 YYYY-MM-DD，收到: {date!r}")

    page = await current_page()
    target = await _resolve_target_frame(page, frame)

    picker_trigger = target.locator(css or ".ant-picker, .ant-calendar-picker").first
    if not await picker_trigger.is_visible():
        raise RuntimeError("未找到可见的 AntD DatePicker 控件")

    await picker_trigger.scroll_into_view_if_needed()
    await picker_trigger.click()
    await page.wait_for_timeout(250)

    # 探测日历单元格
    cell_selectors = [
        f'.ant-picker-cell[title="{date}"]',
        f'.ant-calendar-cell[title="{date}"]',
    ]

    clicked_cell = False
    for cell_sel in cell_selectors:
        cell = target.locator(cell_sel).first
        if not await cell.is_visible() and target != page:
            cell = page.locator(cell_sel).first
        try:
            if await cell.is_visible():
                await cell.click()
                clicked_cell = True
                break
        except Exception:
            continue

    if not clicked_cell:
        # 如果日历未能通过面板点击，尝试直接对 input 进行键盘输入
        inp = picker_trigger.locator("input").first
        if await inp.is_visible():
            await inp.click()
            await inp.fill(date)
            await page.keyboard.press("Enter")
            return {
                "ok": True,
                "date": date,
                "method": "keyboard_input",
                "message": f"通过输入框录入日期: {date}",
            }
        raise RuntimeError(f"在当前展开的日历面板中未找到日期 [{date}]（可能需要先翻月或直接键盘输入）")

    # 点击确定按钮（如果存在）
    for ok_sel in (".ant-picker-ok button", ".ant-calendar-ok-btn"):
        ok_btn = target.locator(ok_sel).first
        if not await ok_btn.is_visible() and target != page:
            ok_btn = page.locator(ok_sel).first
        try:
            if await ok_btn.is_visible():
                await ok_btn.click()
                break
        except Exception:
            pass

    return {
        "ok": True,
        "date": date,
        "method": "calendar_click",
        "message": f"已在日历面板中选择日期: {date}",
    }


async def nav_menu(
    menu_name: str,
    force_reload: bool = False,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """在 APS 管理后台中按菜单名一键导航直达功能模块。

    自动复用/切换已有顶部标签；若未打开则展开顶部「到达菜单」下拉检索并点击进入，
    自动等待目标模块 iframe 激活并返回权威面包屑。

    Args:
        menu_name: 菜单/功能模块名称，如 "产线管理"、"审批流模板管理"、"采购订单"
        force_reload: 是否先关闭已开的同名标签页再重新进入（默认 False）
        timeout_ms: 等待模块 iframe 激活的最长毫秒数（默认 15000ms）
    """
    clean_name = (menu_name or "").strip()
    if not clean_name:
        raise ValueError("菜单名称不能为空")

    page = await current_page()
    reused = False

    # 1. 检查顶部标签栏中是否已有该模块标签
    existing_tabs = page.locator(".ant-tabs-nav .ant-tabs-tab")
    tab_count = await existing_tabs.count()
    target_tab: Locator | None = None

    for i in range(tab_count):
        t = existing_tabs.nth(i)
        txt = (await t.text_content() or "").strip()
        if clean_name in txt:
            target_tab = t
            break

    if target_tab:
        if force_reload:
            close_btn = target_tab.locator(".anticon-close").first
            try:
                if await close_btn.is_visible():
                    await close_btn.click()
                    await page.wait_for_timeout(300)
            except Exception:
                pass
        else:
            cls = await target_tab.get_attribute("class") or ""
            if "active" not in cls:
                await target_tab.click()
                await page.wait_for_timeout(300)
            reused = True

    # 2. 未复用或已关闭时，通过顶部「到达菜单」搜索打开
    if not reused:
        select_box: Locator | None = None
        for sel in (
            ".right-header .ant-select",
            ".ant-layout-header .ant-select",
            ".ant-select:has(.ant-select-selection__placeholder)",
        ):
            box = page.locator(sel).first
            try:
                if await box.is_visible():
                    select_box = box
                    break
            except Exception:
                continue

        if not select_box:
            raise RuntimeError(
                f"在当前页面未找到顶部菜单导航框（.ant-select），请确认当前页面为 APS 管理后台主框架（{page.url}）"
            )

        await select_box.click()
        await page.wait_for_timeout(200)

        # 输入菜单名
        search_inp = select_box.locator(".ant-select-search__field, input").first
        if not await search_inp.is_visible():
            search_inp = page.locator(".ant-select-search__field, input.ant-select-search__field").first

        if await search_inp.is_visible():
            await search_inp.fill(clean_name)
            await page.wait_for_timeout(300)

        # 在下拉选项中找到并点击匹配项
        item = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").locator(
            ".ant-select-dropdown-menu-item, .ant-select-item-option"
        ).filter(has_text=clean_name).first

        if not await item.is_visible():
            # 尝试回车选择第一项
            await page.keyboard.press("Enter")
        else:
            await item.click()

    # 3. 等待目标模块 iframe 激活就绪
    profile = active_profile()
    active_iframe_sel = profile.active_iframe_selector
    deadline = time.time() + (timeout_ms / 1000.0)

    active_frame_obj: Frame | None = None
    while time.time() < deadline:
        try:
            iframe_ele = await page.query_selector(active_iframe_sel)
            if iframe_ele:
                frame_obj = await iframe_ele.content_frame()
                if frame_obj and frame_obj.url and frame_obj.url != "about:blank":
                    active_frame_obj = frame_obj
                    break
        except Exception:
            pass
        await asyncio.sleep(0.3)

    # 4. 获取权威面包屑导航
    breadcrumbs = []
    try:
        b_items = page.locator(".ant-breadcrumb-link, .ant-breadcrumb > span")
        for k in range(await b_items.count()):
            b_txt = (await b_items.nth(k).text_content() or "").strip()
            if b_txt and b_txt != "/":
                breadcrumbs.append(b_txt)
    except Exception:
        pass

    breadcrumb_str = " > ".join(breadcrumbs) if breadcrumbs else clean_name

    # iframe 一直未激活说明导航实际失败：旧版无条件 ok=True 会把失败报告为成功
    # （fail-open）。这里如实返回 ok=False 并给出排查线索。
    if active_frame_obj is None:
        return {
            "ok": False,
            "menu_name": clean_name,
            "reused_tab": reused,
            "breadcrumb": breadcrumb_str,
            "frame_id": None,
            "frame_url": None,
            "page_url": page.url,
            "reason": (
                f"等待 {timeout_ms}ms 后目标模块 iframe 未激活（{active_iframe_sel}）。"
                "请核对菜单名是否正确、页面是否已跳转，或用 ui_snapshot 检查当前页面状态。"
            ),
        }

    return {
        "ok": True,
        "menu_name": clean_name,
        "reused_tab": reused,
        "breadcrumb": breadcrumb_str,
        "frame_id": _frame_id(page, active_frame_obj) if active_frame_obj else None,
        "frame_url": active_frame_obj.url if active_frame_obj else None,
        "page_url": page.url,
    }
