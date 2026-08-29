"""Low-overhead metrics for browser-facing MCP tool calls."""

from __future__ import annotations

import functools
import inspect
import json
import math
import time
from collections import defaultdict, deque
from typing import Any, Callable


_recent: deque[dict[str, Any]] = deque(maxlen=200)
_summary: dict[str, dict[str, float]] = defaultdict(
    lambda: {"calls": 0, "failures": 0, "elapsed_ms": 0, "response_bytes": 0}
)


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode())


def instrument_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    signature = inspect.signature(function)

    @functools.wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        bound = signature.bind_partial(*args, **kwargs)
        request_bytes = _size(bound.arguments)
        result: Any = None
        error: BaseException | None = None
        try:
            result = await function(*args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response_bytes = _size(result) if error is None else 0
            metric = {
                "tool": function.__name__,
                "elapsed_ms": elapsed_ms,
                "request_bytes": request_bytes,
                "response_bytes": response_bytes,
                "estimated_context_tokens": math.ceil(response_bytes / 4),
                "frame_count": result.get("frame_count") if isinstance(result, dict) else None,
                "result_count": _result_count(result),
                "error": type(error).__name__ if error else None,
            }
            if isinstance(result, dict):
                result["metrics"] = metric
            _recent.append(metric)
            item = _summary[function.__name__]
            item["calls"] += 1
            item["failures"] += int(
                error is not None
                or (isinstance(result, dict) and result.get("status") == "failed")
            )
            item["elapsed_ms"] += elapsed_ms
            item["response_bytes"] += response_bytes

    wrapped.__signature__ = signature
    return wrapped


def _result_count(result: Any) -> int | None:
    if not isinstance(result, dict):
        return None
    for key in ("controls", "overlays", "events", "pages", "tables"):
        if isinstance(result.get(key), list):
            return len(result[key])
    return None


def metrics_snapshot(limit: int = 50) -> dict:
    limit = max(1, min(200, int(limit)))
    summary = {}
    for name, item in _summary.items():
        calls = max(1, int(item["calls"]))
        summary[name] = {
            "calls": int(item["calls"]),
            "failures": int(item["failures"]),
            "avg_elapsed_ms": round(item["elapsed_ms"] / calls, 2),
            "avg_response_bytes": round(item["response_bytes"] / calls),
        }
    return {"status": "ok", "summary": summary, "recent": list(_recent)[-limit:]}
