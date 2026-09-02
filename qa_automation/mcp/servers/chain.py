"""Interaction chain tool: batch-execute actions, or return compact analysis."""

from __future__ import annotations

from fastmcp import FastMCP

from qa_automation.interaction.chain import analyze_page_compact, execute_chain

from ..metrics import instrument_tool


def create_server() -> FastMCP:
    mcp = FastMCP("Interaction Chain")

    @mcp.tool()
    @instrument_tool
    async def interaction_chain(
        goal: str | None = None,
        actions: list[dict] | None = None,
        mode: str = "auto",
        max_actions: int = 10,
        stop_on_error: bool = True,
        include_analysis: bool = True,
    ) -> dict:
        """一次性批量执行 N 个 UI 交互动作(1 次调用替代 N 次往返),或返回紧凑页面分析。

        两种用法:
        - 传入 actions(非空):按序批量执行,忽略 mode。动作类型:click/dblclick/
          rightclick/hover/fill/type/press/check/uncheck/select/drag/cell_click/
          cell_click_field/wait,字段名遵循对应原语(ui_interact/ui_mouse_drag/
          vtable_cell_click/vtable_cell_click_by_field)。每个动作结果只保留
          {action, ok, status, target/locator/point, evidence_count, error} 摘要;
          链尾统一观察一次:结果含 observation {url_changed, url, overlays[极简
          kind/text/visible]},不逐动作返回浮层 dump。planning 由客户端 AI 完成,
          本工具不做任何服务端 LLM 调用。
        - 不传 actions 且 mode="auto":返回 status="analysis-only" 与紧凑
          analysis/page_context,由 AI 据此在下次调用显式传 actions。

        失败语义:单步硬超时(默认 5000ms,超时记该步失败);stop_on_error=True(默认)
        时首个失败动作终止链条(整体 status="failed",executed 停在失败前成功数);
        False 时收集失败继续(整体 status="partial" 或 "executed")。
        """
        if actions is not None:
            if not actions:
                return {
                    "status": "failed",
                    "executed": 0,
                    "results": [],
                    "truncated": False,
                    "reason": "actions 不能为空;不传 actions 时返回页面分析",
                }
            outcome = await execute_chain(
                actions, stop_on_error=stop_on_error, max_actions=max_actions
            )
            failed = sum(1 for r in outcome["results"] if not r.get("ok"))
            if failed:
                status = "failed" if stop_on_error else "partial"
            else:
                status = "executed"
            return {
                "status": status,
                "executed": outcome["executed"],
                "results": outcome["results"],
                "truncated": outcome["truncated"],
                "observation": outcome["observation"],
            }
        mode_norm = str(mode).strip().lower()
        if mode_norm not in {"auto", "manual"}:
            return {
                "status": "failed",
                "executed": 0,
                "results": [],
                "truncated": False,
                "reason": f"unsupported mode {mode!r}; expected 'auto' or 'manual'",
            }
        if mode_norm == "manual":
            return {
                "status": "failed",
                "executed": 0,
                "results": [],
                "truncated": False,
                "reason": "manual mode requires actions; pass actions or use mode='auto'",
            }
        analysis_full = await analyze_page_compact()
        response = {
            "status": "analysis-only",
            "executed": 0,
            "results": [],
            "truncated": False,
            "reason": "no-actions-provided",
        }
        if include_analysis:
            response["analysis"] = {
                k: v for k, v in analysis_full.items() if k != "page_context"
            }
            response["page_context"] = analysis_full.get("page_context")
        return response

    return mcp
