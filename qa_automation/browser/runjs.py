"""Restricted run_js escape hatch with lock-starvation guards.

拆分自原 browser.py 单文件;公开名经包 __init__ 再导出,外部导入路径不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .core import _current_page_impl
from .state import _action_lock, _frame_id

logger = logging.getLogger("qa_automation.browser")


RUN_JS_OUTPUT_LIMIT = 8000


async def _run_js_impl(
    script: str,
    arg: Any = None,
    *,
    frame: str | None = None,
    timeout_ms: int = 10_000,
) -> Any:
    """在当前页面或指定 frame 中执行 JavaScript 并返回结果。"""
    # 参数校验必须先于 _current_page_impl()：否则一个空脚本也会先自动连接/拉起
    # 浏览器再报错，异常路径上不清理就会把僵尸 driver 留在全局 _state 里，
    # 毒化后续所有真实浏览器操作（测试套件实测复现过）。
    clean_script = (script or "").strip()
    if not clean_script:
        raise ValueError("JavaScript 脚本内容不能为空")

    from ..profiles import active_profile

    page = await _current_page_impl()

    # 解析目标 Frame
    target: Any = page
    if frame and frame not in ("main", "top"):
        if frame == "active":
            profile = active_profile()
            try:
                iframe_ele = await page.query_selector(profile.active_iframe_selector)
                if iframe_ele:
                    content_frame = await iframe_ele.content_frame()
                    if content_frame:
                        target = content_frame
            except Exception:
                pass
        else:
            for f in page.frames:
                if _frame_id(page, f) == frame or f.name == frame:
                    target = f
                    break

    # 智能函数包裹：如果包含 return 且未被函数闭包包裹，自动包裹为闭包
    if clean_script.startswith(("function", "async function", "()", "(", "async (")):
        eval_code = clean_script
    elif "return " in clean_script:
        eval_code = f"async (arg) => {{\n{clean_script}\n}}"
    else:
        eval_code = clean_script

    # Playwright 的 evaluate 不受 set_default_timeout 约束：页面卡死/脚本死循环时
    # 这个 await 永不返回，而本工具又持有全局 _action_lock，一步就会饿死所有浏览器
    # 工具。wait_for 是唯一可靠的上界；超时后脚本仍在页面里跑（JS 无法强杀），
    # 但锁会被释放，服务不至于瘫痪。
    try:
        raw_result = await asyncio.wait_for(
            target.evaluate(eval_code, arg), timeout=max(timeout_ms, 0) / 1000
        )
    except TimeoutError:
        raise TimeoutError(
            f"run_js 执行超过 {timeout_ms}ms 未返回（脚本可能含死循环或在等待一个永不 "
            "resolve 的 Promise；锁已释放可继续其他操作，如需终止脚本只能刷新页面）"
        ) from None

    # 基础类型直接返回
    if raw_result is None or isinstance(raw_result, (int, float, bool)):
        return raw_result

    # 字符串截断
    if isinstance(raw_result, str):
        if len(raw_result) > RUN_JS_OUTPUT_LIMIT:
            return (
                raw_result[:RUN_JS_OUTPUT_LIMIT]
                + f"\n...[输出截断: 共 {len(raw_result)} 字符，如需完整数据请在 JS 内自行裁剪返回]"
            )
        return raw_result

    # 字典/数组结构截断检测
    try:
        dumped = json.dumps(raw_result, ensure_ascii=False)
        if len(dumped) > RUN_JS_OUTPUT_LIMIT:
            return (
                dumped[:RUN_JS_OUTPUT_LIMIT]
                + f"\n...[输出截断: JSON 序列化共 {len(dumped)} 字符，如需完整数据请在 JS 内自行裁剪返回]"
            )
    except Exception:
        pass

    return raw_result


async def run_js(
    script: str,
    arg: Any = None,
    *,
    frame: str | None = None,
    timeout_ms: int = 10_000,
) -> Any:
    """在当前页面或指定 frame 中执行 JavaScript 并返回结果（逃生通道）。"""
    async with _action_lock:
        return await _run_js_impl(
            script=script,
            arg=arg,
            frame=frame,
            timeout_ms=timeout_ms,
        )
