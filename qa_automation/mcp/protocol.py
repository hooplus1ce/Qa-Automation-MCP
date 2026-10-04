"""Shared backwards-compatible response and error contracts for MCP tools."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = "1.0"


class ErrorCode(StrEnum):
    """Stable error codes exposed to MCP clients."""

    CONFIG_MISSING = "CONFIG_MISSING"
    BROWSER_NOT_READY = "BROWSER_NOT_READY"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    TARGET_NOT_VISIBLE = "TARGET_NOT_VISIBLE"
    IFRAME_NOT_FOUND = "IFRAME_NOT_FOUND"
    VTABLE_NOT_FOUND = "VTABLE_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NETWORK_ERROR = "NETWORK_ERROR"
    UPSTREAM_AUTH_FAILED = "UPSTREAM_AUTH_FAILED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_FAILURE_STATUSES = frozenset(
    {"failed", "error", "config_missing", "timeout", "unauthorized", "forbidden"}
)
_STATUS_ERROR_CODES: dict[str, ErrorCode] = {
    "config_missing": ErrorCode.CONFIG_MISSING,
    "timeout": ErrorCode.TIMEOUT,
    "unauthorized": ErrorCode.UPSTREAM_AUTH_FAILED,
    "forbidden": ErrorCode.PERMISSION_DENIED,
}
_METADATA_FIELDS = frozenset(
    {
        "protocol_version",
        "trace_id",
        "status",
        "ok",
        "code",
        "error",
        "message",
        "next_action",
        "metrics",
    }
)


def new_trace_id() -> str:
    """Return a short, opaque identifier safe to show to an MCP client."""
    return f"qa_{uuid.uuid4().hex[:16]}"


def is_failure_status(status: Any) -> bool:
    """Whether a domain status represents an unsuccessful operation."""
    return isinstance(status, str) and status.lower() in _FAILURE_STATUSES


def _first_text(result: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = result.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()[:1000]
    return None


def _infer_error_code(result: dict[str, Any]) -> str:
    status = str(result.get("status", "")).lower()
    if status in _STATUS_ERROR_CODES:
        return _STATUS_ERROR_CODES[status].value

    text = (_first_text(result, "error", "reason", "message") or "").lower()
    keyword_codes = (
        (("timeout", "timed out", "超时"), ErrorCode.TIMEOUT),
        (
            ("browser not ready", "browser is not connected", "browser not connected"),
            ErrorCode.BROWSER_NOT_READY,
        ),
        (("session", "会话"), ErrorCode.SESSION_NOT_FOUND),
        (("vtable",), ErrorCode.VTABLE_NOT_FOUND),
        (("iframe", "frame"), ErrorCode.IFRAME_NOT_FOUND),
        (("profile", "档案"), ErrorCode.PROFILE_NOT_FOUND),
        (("not found", "不存在", "未找到", "not visible", "不可见"), ErrorCode.TARGET_NOT_VISIBLE),
        (("permission", "权限"), ErrorCode.PERMISSION_DENIED),
        (("network", "网络", "connection"), ErrorCode.NETWORK_ERROR),
        (("auth", "登录", "token", "credential"), ErrorCode.UPSTREAM_AUTH_FAILED),
        (("argument", "参数", "must be", "required"), ErrorCode.INVALID_ARGUMENT),
    )
    for keywords, code in keyword_codes:
        if any(keyword in text for keyword in keywords):
            return code.value
    return ErrorCode.INTERNAL_ERROR.value


def _default_next_action(code: str) -> str | None:
    actions = {
        ErrorCode.BROWSER_NOT_READY.value: "Call browser_start or browser_connect first.",
        ErrorCode.SESSION_NOT_FOUND.value: "Call browser_session(action='list') and select a valid session.",
        ErrorCode.CONFIG_MISSING.value: "Configure the required environment variable or profile, then retry.",
        ErrorCode.VTABLE_NOT_FOUND.value: "Call vtable_discover or vtable_analysis after selecting the correct page.",
        ErrorCode.IFRAME_NOT_FOUND.value: "Refresh the page context and verify the target iframe is loaded.",
        ErrorCode.TIMEOUT.value: "Retry after the page settles or increase the operation timeout.",
        ErrorCode.PERMISSION_DENIED.value: "Verify the account permissions and the requested resource scope.",
        ErrorCode.PROFILE_NOT_FOUND.value: "Call the profile listing/diagnostic tool and use an available profile name.",
        ErrorCode.TARGET_NOT_VISIBLE.value: "Refresh the UI snapshot and verify the target locator and visibility.",
        ErrorCode.NETWORK_ERROR.value: "Check network reachability and retry after the upstream service recovers.",
    }
    return actions.get(code)


def _normalize_dict(
    result: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    result.setdefault("protocol_version", PROTOCOL_VERSION)
    if trace_id is not None:
        result["trace_id"] = trace_id
    else:
        result.setdefault("trace_id", new_trace_id())
    failed = is_failure_status(result.get("status")) or result.get("ok") is False
    if failed:
        if not is_failure_status(result.get("status")):
            result["status"] = "failed"
        result["ok"] = False
        current_code = result.get("code")
        code = (
            str(current_code)
            if current_code and current_code != "OK"
            else _infer_error_code(result)
        )
        result["code"] = code
        message = _first_text(result, "message", "error", "reason") or "Tool execution failed"
        if not result.get("message"):
            result["message"] = message
        if not result.get("error"):
            result["error"] = message
        if not result.get("next_action"):
            result["next_action"] = _default_next_action(code)
    else:
        result.setdefault("status", "ok")
        result["ok"] = True
        result.setdefault("code", "OK")
        result.setdefault("error", None)
    return result


def ensure_response_metadata(
    result: Any,
    *,
    trace_id: str | None = None,
) -> Any:
    """Add protocol metadata to dicts and Pydantic models without wrapping domain data."""
    if isinstance(result, dict):
        return _normalize_dict(result, trace_id=trace_id)

    model_dump = getattr(result, "model_dump", None)
    model_copy = getattr(result, "model_copy", None)
    model_fields = getattr(type(result), "model_fields", None)
    if callable(model_dump) and callable(model_copy) and isinstance(model_fields, dict):
        payload = model_dump(mode="python")
        if isinstance(payload, dict):
            normalized = _normalize_dict(payload, trace_id=trace_id)
            updates = {key: normalized[key] for key in model_fields if key in normalized}
            return model_copy(update=updates)
    return result


def success_response(
    data: Any = None, *, trace_id: str | None = None, **fields: Any
) -> dict[str, Any]:
    """Build a success envelope for new tools."""
    result: dict[str, Any] = {"status": "ok", "code": "OK", "error": None, **fields}
    if data is not None:
        result["data"] = data
    return ensure_response_metadata(result, trace_id=trace_id)


def failure_response(
    code: ErrorCode | str,
    message: str,
    *,
    next_action: str | None = None,
    trace_id: str | None = None,
    data: Any = None,
    **fields: Any,
) -> dict[str, Any]:
    """Build a structured failure envelope for new tools and adapters."""
    error_code = code.value if isinstance(code, ErrorCode) else str(code)
    result: dict[str, Any] = {
        "status": "failed",
        "code": error_code,
        "message": message,
        "error": message,
        "next_action": next_action if next_action is not None else _default_next_action(error_code),
        **fields,
    }
    if data is not None:
        result["data"] = data
    return ensure_response_metadata(result, trace_id=trace_id)


__all__ = [
    "ErrorCode",
    "PROTOCOL_VERSION",
    "ensure_response_metadata",
    "failure_response",
    "is_failure_status",
    "new_trace_id",
    "success_response",
]
