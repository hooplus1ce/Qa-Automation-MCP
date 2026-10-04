"""Low-overhead metrics and response metadata for MCP tool calls."""

from __future__ import annotations

import functools
import inspect
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextvars import ContextVar
from threading import RLock
from typing import Any

from .protocol import ensure_response_metadata, is_failure_status, new_trace_id

_recent: deque[dict[str, Any]] = deque(maxlen=200)
_summary: dict[str, dict[str, float]] = defaultdict(
    lambda: {"calls": 0, "failures": 0, "elapsed_ms": 0, "response_bytes": 0}
)
_current_trace_id: ContextVar[str | None] = ContextVar("qa_automation_trace_id", default=None)
_metrics_lock = RLock()


def _as_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="python")
        except Exception:
            return value
    return value


def _size(value: Any) -> int:
    """Estimate JSON-like byte size without copying large strings such as screenshots."""
    total = 0
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, (bool, int, float)):
            total += 4
        elif isinstance(item, str):
            total += (len(item) if item.isascii() else len(item.encode("utf-8", "replace"))) + 2
        elif isinstance(item, dict):
            total += 2
            for key, val in item.items():
                stack.append(key)
                stack.append(val)
        elif isinstance(item, (list, tuple)):
            total += 2 + max(0, len(item) - 1)
            stack.extend(item)
        else:
            payload = _as_payload(item)
            if payload is item:
                stack.append(str(item))
            else:
                stack.append(payload)
    return total


def current_trace_id() -> str | None:
    """Return the trace id of the currently executing instrumented tool call."""
    return _current_trace_id.get()


def reset_metrics() -> None:
    """Clear in-memory metrics; intended for tests and local diagnostics."""
    with _metrics_lock:
        _recent.clear()
        _summary.clear()


def _result_count(result: Any) -> int | None:
    payload = _as_payload(result)
    if not isinstance(payload, dict):
        return None
    for key in ("controls", "overlays", "events", "pages", "tables", "items", "rows"):
        if isinstance(payload.get(key), list):
            return len(payload[key])
    return None


def _record_call(
    *,
    tool_name: str,
    trace_id: str,
    started: float,
    request_bytes: int,
    result: Any,
    error: BaseException | None,
) -> Any:
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    payload = _as_payload(result)
    response_bytes = _size(payload) if error is None else 0
    result_count = _result_count(payload)
    metric = {
        "tool": tool_name,
        "trace_id": trace_id,
        "elapsed_ms": elapsed_ms,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "estimated_context_tokens": math.ceil(response_bytes / 4),
        "frame_count": payload.get("frame_count") if isinstance(payload, dict) else None,
        "result_count": result_count,
        "error": type(error).__name__ if error else None,
    }
    if isinstance(result, dict):
        result["metrics"] = {
            "tool": tool_name,
            "trace_id": trace_id,
            "elapsed_ms": elapsed_ms,
            "response_bytes": response_bytes,
            "estimated_context_tokens": math.ceil(response_bytes / 4),
            "result_count": result_count,
        }
    else:
        model_copy = getattr(result, "model_copy", None)
        model_fields = getattr(type(result), "model_fields", None)
        if callable(model_copy) and isinstance(model_fields, dict) and "metrics" in model_fields:
            result = model_copy(
                update={
                    "metrics": {
                        "tool": tool_name,
                        "trace_id": trace_id,
                        "elapsed_ms": elapsed_ms,
                        "response_bytes": response_bytes,
                        "estimated_context_tokens": math.ceil(response_bytes / 4),
                        "result_count": result_count,
                    }
                }
            )
    with _metrics_lock:
        _recent.append(metric)
        item = _summary[tool_name]
        item["calls"] += 1
        item["failures"] += int(
            error is not None
            or (isinstance(payload, dict) and is_failure_status(payload.get("status")))
        )
        item["elapsed_ms"] += elapsed_ms
        item["response_bytes"] += response_bytes
    return result


def instrument_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    """Instrument sync and async tools without changing signatures or execution model.

    Nested calls through another decorated Python tool share the outer trace and are
    not counted twice; metrics therefore describe client-visible tool invocations.
    """
    signature = inspect.signature(function)
    tool_name = function.__name__

    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def wrapped_async(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            request_bytes = _size(signature.bind_partial(*args, **kwargs).arguments)
            parent_trace_id = current_trace_id()
            trace_id = parent_trace_id or new_trace_id()
            should_record = parent_trace_id is None
            token = _current_trace_id.set(trace_id)
            result: Any = None
            error: BaseException | None = None
            try:
                result = await function(*args, **kwargs)
                result = ensure_response_metadata(result, trace_id=trace_id)
            except BaseException as exc:
                error = exc
                raise
            finally:
                _current_trace_id.reset(token)
                if should_record:
                    result = _record_call(
                        tool_name=tool_name,
                        trace_id=trace_id,
                        started=started,
                        request_bytes=request_bytes,
                        result=result,
                        error=error,
                    )
            return result

        wrapped_async.__signature__ = signature
        return wrapped_async

    @functools.wraps(function)
    def wrapped_sync(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        request_bytes = _size(signature.bind_partial(*args, **kwargs).arguments)
        parent_trace_id = current_trace_id()
        trace_id = parent_trace_id or new_trace_id()
        should_record = parent_trace_id is None
        token = _current_trace_id.set(trace_id)
        result: Any = None
        error: BaseException | None = None
        try:
            result = function(*args, **kwargs)
            result = ensure_response_metadata(result, trace_id=trace_id)
        except BaseException as exc:
            error = exc
            raise
        finally:
            _current_trace_id.reset(token)
            if should_record:
                result = _record_call(
                    tool_name=tool_name,
                    trace_id=trace_id,
                    started=started,
                    request_bytes=request_bytes,
                    result=result,
                    error=error,
                )
        return result

    wrapped_sync.__signature__ = signature
    return wrapped_sync


def metrics_snapshot(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(200, int(limit)))
    with _metrics_lock:
        summary = {}
        for name, item in _summary.items():
            calls = max(1, int(item["calls"]))
            summary[name] = {
                "calls": int(item["calls"]),
                "failures": int(item["failures"]),
                "avg_elapsed_ms": round(item["elapsed_ms"] / calls, 2),
                "avg_response_bytes": round(item["response_bytes"] / calls),
            }
        recent = list(_recent)[-limit:]
    return ensure_response_metadata(
        {"status": "ok", "summary": summary, "recent": recent},
        trace_id=current_trace_id(),
    )


__all__ = ["current_trace_id", "instrument_tool", "metrics_snapshot", "reset_metrics"]
