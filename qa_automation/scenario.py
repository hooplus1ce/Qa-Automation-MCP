"""Declarative scenario runner for QA regression testing.

Supports YAML / JSON test suites, step-by-step MCP tool orchestration, variable
interpolation (${tab_id}, ${user}), and result assertions.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import yaml

VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_0-9]+)\}")
SCENARIOS_DIR = Path("scenarios")


def parse_scenario(source: str | dict[str, Any]) -> dict[str, Any]:
    """解析场景数据源（支持字典、文件路径、内联 YAML/JSON 字符串）。"""
    if isinstance(source, dict):
        return source

    text = str(source).strip()
    if not text:
        raise ValueError("场景定义不能为空")

    # 1. 尝试作为文件路径解析
    candidates = [
        Path(text),
        SCENARIOS_DIR / text,
        SCENARIOS_DIR / f"{text}.yaml",
        SCENARIOS_DIR / f"{text}.yml",
        SCENARIOS_DIR / f"{text}.json",
    ]
    for p in candidates:
        if p.is_file():
            raw = p.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                raise ValueError(f"场景文件必须是字典结构: {p}")
            return data

    # 2. 尝试作为内联 YAML/JSON 字符串解析
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        raise ValueError(f"场景内容解析失败: {exc}") from exc

    raise ValueError(f"未找到场景文件或合法的场景定义: {source!r}")


def substitute_vars(obj: Any, variables: dict[str, Any]) -> Any:
    """递归替换对象中的 ${var} 变量占位符。"""
    if isinstance(obj, str):

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))

        return VAR_PATTERN.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: substitute_vars(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_vars(x, variables) for x in obj]
    return obj


def _field(data: Any, name: str) -> Any:
    """从工具返回值里安全取一个字段（兼容 dict 与模型对象）。"""
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get(name)
    val = getattr(data, name, None)
    if val is not None:
        return val
    dump = getattr(data, "model_dump", None)
    if callable(dump):
        try:
            return dump().get(name)
        except Exception:
            return None
    return None


def _dig(data: Any, path: str) -> Any:
    """按点号/下标路径取值，供场景 save: 从结构化结果里提取字段（例如 a.b.0.id）。"""
    cur: Any = data
    for part in str(path).split("."):
        if isinstance(cur, (list, tuple)):
            if not part.isdigit() or int(part) >= len(cur):
                return None
            cur = cur[int(part)]
        else:
            cur = _field(cur, part)
            if cur is None:
                return None
    return cur


def _summarize_output(data: Any, content: list[Any] | None = None) -> str:
    if data is not None:
        if hasattr(data, "model_dump"):
            dumped = data.model_dump()
            return json.dumps(dumped, ensure_ascii=False)[:300]
        return str(data)[:300]
    if content:
        texts = [c.text for c in content if hasattr(c, "text")]
        return " ".join(texts)[:300]
    return ""


async def run_scenario(
    source: str | dict[str, Any],
    client: Any,
    stop_on_error: bool = True,
) -> dict[str, Any]:
    """运行声明式场景，返回完整的执行与断言结果。

    Args:
        source: 场景文件路径、字典结构或 YAML 字符串
        client: FastMCP Client 实例（用于派发 call_tool）
        stop_on_error: 遇到单步失败时是否立即终止流程（默认 True）
    """
    scenario_data = parse_scenario(source)
    name = str(scenario_data.get("name") or "unnamed_scenario")
    raw_steps = scenario_data.get("steps") or []
    if not raw_steps or not isinstance(raw_steps, list):
        raise ValueError("场景中未包含有效的 steps 步骤列表")

    variables: dict[str, Any] = dict(scenario_data.get("variables") or {})
    step_results: list[dict[str, Any]] = []
    overall_ok = True
    failed_step_info: str | None = None
    start_time = time.time()

    for idx, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            continue

        step_name = str(raw_step.get("step") or f"步骤 {idx}")
        tool_name = str(raw_step.get("tool") or "").strip()
        if not tool_name:
            continue

        raw_args = raw_step.get("args") or {}
        # 变量插值
        args = substitute_vars(raw_args, variables)

        step_t0 = time.time()
        step_ok = True
        step_err: str | None = None
        output_summary = ""

        try:
            res = await client.call_tool(tool_name, args)
            data = getattr(res, "data", None)
            content = getattr(res, "content", None)
            output_summary = _summarize_output(data, content)

            # 自动跟踪核心上下文变量（如 page_id, session_id）
            if data is not None:
                for track_key in ("page_id", "tab_id", "session_id", "context_id"):
                    val = _field(data, track_key)
                    if val:
                        variables[track_key] = str(val)

            # 自定义变量提取
            save_vars = raw_step.get("save") or {}
            if isinstance(save_vars, dict) and data is not None:
                for target_var, src_field in save_vars.items():
                    val = _dig(data, src_field)
                    if val is not None:
                        variables[target_var] = str(val)

            # 执行断言检查
            expect = raw_step.get("expect") or {}
            if isinstance(expect, dict):
                # 检查 status_ok
                if expect.get("status_ok") and data is not None:
                    ok_val = _field(data, "ok")
                    status_val = _field(data, "status")
                    if ok_val is False or status_val in ("error", "failed", "config_missing"):
                        step_ok = False
                        step_err = f"断言失败: 返回结果指示操作未成功 (ok={ok_val}, status={status_val})"

                # 检查 message_contains
                msg_pat = expect.get("message_contains")
                if msg_pat and step_ok:
                    msg_txt = str(_field(data, "message") or _field(data, "matched_text") or output_summary)
                    if str(msg_pat) not in msg_txt:
                        step_ok = False
                        step_err = f"断言失败: 消息未包含期望的 [{msg_pat}]，实际输出: {msg_txt[:100]}"

        except Exception as exc:
            step_ok = False
            step_err = f"{type(exc).__name__}: {exc}"

        duration_ms = round((time.time() - step_t0) * 1000, 2)
        step_res = {
            "step": step_name,
            "tool": tool_name,
            "ok": step_ok,
            "duration_ms": duration_ms,
            "error": step_err,
            "output_summary": output_summary,
        }
        step_results.append(step_res)

        # 步后休眠（若显式指定）
        post_sleep = raw_step.get("sleep")
        if post_sleep and isinstance(post_sleep, (int, float)) and post_sleep > 0:
            await asyncio.sleep(post_sleep)

        if not step_ok:
            overall_ok = False
            failed_step_info = f"{step_name} [{tool_name}] 失败: {step_err}"
            if stop_on_error:
                break

    elapsed_seconds = round(time.time() - start_time, 3)
    passed_count = sum(1 for s in step_results if s["ok"])

    return {
        "ok": overall_ok,
        "scenario_name": name,
        "total_steps": len(step_results),
        "passed_steps": passed_count,
        "failed_step": failed_step_info,
        "elapsed_seconds": elapsed_seconds,
        "steps": step_results,
        "variables": variables,
    }
