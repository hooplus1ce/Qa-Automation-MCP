"""Ant Design component and APS micro-frontend navigation MCP tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ... import antd as antd_ops
from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Ant Design & Navigation")

    @mcp.tool(name="wait_message")
    @instrument_tool
    async def wait_message(
        pattern: str | None = None,
        timeout: float = 5.0,
        raise_if_not_found: bool = True,
    ) -> dict[str, Any]:
        """等待或即时快照全局操作结果气泡（message / notification / Modal 提示 / layui-layer）。

        基于浏览器内联 MutationObserver，零轮询死等，毫秒级捕获瞬时（如 1.5s 消失）的操作提示。

        Args:
            pattern: 正则或关键字（如 "保存成功"、"操作成功|已完成"）；留空表示捕获任意提示
            timeout: 最长等待秒数（默认 5.0）。设置为 0 时为即时快照模式（立刻返回已有气泡，不抛错）
            raise_if_not_found: 超时未匹配时是否抛错（默认 True；False 时返回 found=False 的结果字典）
        """
        return await antd_ops.wait_message(
            pattern=pattern,
            timeout=timeout,
            raise_if_not_found=raise_if_not_found,
        )

    @mcp.tool(name="antd_select")
    @instrument_tool
    async def antd_select(
        option_text: str,
        css: str | None = None,
        xpath: str | None = None,
        text: str | None = None,
        search_text: str | None = None,
        frame: str | None = None,
        close_multi: bool = True,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        """操作 AntD 下拉选择框（Select）：通过几何特征匹配准确的浮层，点击选项并复核已选值。

        解决 AntD 弹窗内下拉误抓顶部导航菜单遗留浮层、以及点击后未真实落值的顽固问题。

        Args:
            option_text: 目标选项文本（如 "按部门审批"）
            css: Select 触发框 CSS 选择器（如 ".ant-select" 或具有业务语义的容器选择器）
            xpath: 触发框 XPath
            text: 触发框上原有的可见文本
            search_text: 若下拉框支持搜索过滤，展开后输入的搜索关键字
            frame: 目标 frame（默认自动判定 active iframe 或顶层）
            close_multi: 选择完成后自动派发 ESC 键收起浮层，防止遮挡后续表单控件（默认 True）
            timeout_ms: 整体执行超时毫秒数（默认 5000ms）
        """
        return await antd_ops.antd_select(
            option_text=option_text,
            css=css,
            xpath=xpath,
            text=text,
            search_text=search_text,
            frame=frame,
            close_multi=close_multi,
            timeout_ms=timeout_ms,
        )

    @mcp.tool(name="antd_date_pick")
    @instrument_tool
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
            frame: 目标 frame
            timeout_ms: 超时毫秒数
        """
        return await antd_ops.antd_date_pick(
            date=date,
            css=css,
            frame=frame,
            timeout_ms=timeout_ms,
        )

    @mcp.tool(name="nav_menu")
    @instrument_tool
    async def nav_menu(
        menu_name: str,
        force_reload: bool = False,
        timeout_ms: int = 15000,
    ) -> dict[str, Any]:
        """在 APS 管理后台中按菜单名一键导航直达功能模块。

        智能识别复用/切换顶部已有标签；若未打开则展开顶部「到达菜单」下拉模糊检索并进入，
        自动等待目标微前端 iframe 激活并返回权威面包屑，无需手动进行多次试探点击。

        Args:
            menu_name: 菜单/功能模块名称，如 "产线管理"、"审批流模板管理"、"采购订单"
            force_reload: 是否先关闭已开的同名标签页再重新进入（默认 False）
            timeout_ms: 等待模块 iframe 激活的最长毫秒数（默认 15000ms）
        """
        return await antd_ops.nav_menu(
            menu_name=menu_name,
            force_reload=force_reload,
            timeout_ms=timeout_ms,
        )

    return mcp
