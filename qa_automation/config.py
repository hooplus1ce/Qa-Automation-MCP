"""Central configuration, profile resolution, and embedded cursor assets."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .profiles import (
    LOCATOR_STRATEGY as LOCATOR_STRATEGY,  # 有意再导出
)
from .profiles import (
    VTABLE_VERIFICATION_STRATEGY as VTABLE_VERIFICATION_STRATEGY,  # 有意再导出
)
from .profiles import (
    active_profile,
)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


NAV_TIMEOUT_MS = 30_000
BIND_TIMEOUT_MS = 8_000
SETTLE_MS = 60
SCROLL_WAIT_RAF = 2
ANALYSIS_CACHE_LIMIT = 32
ANALYSIS_MAX_AGE_SECONDS = 120
OVERLAY_EVENT_LIMIT = 100
OVERLAY_SETTLE_LIMIT_MS = 2_000

OVERLAY_RESULT_LIMIT = _env_int("QA_AUTOMATION_OVERLAY_RESULT_LIMIT", 20)
SHOW_CURSOR = _env_bool("QA_AUTOMATION_SHOW_CURSOR", True)
QA_AUTOMATION_PROJECT_ROOT = os.getenv("QA_AUTOMATION_PROJECT_ROOT", "").strip()


TENCENT_DOCS_MCP_URL = os.getenv("TENCENT_DOCS_MCP_URL", "https://docs.qq.com/openapi/mcp")


def resolve_tencent_docs_token() -> str:
    """Resolve the Tencent Docs MCP token from explicit local configuration."""
    token = os.getenv("TENCENT_DOCS_MCP_TOKEN")
    if token and token.strip():
        return token.strip()

    candidate_files = [
        Path.home() / ".mcporter" / "mcporter.json",
        Path(os.getenv("APPDATA", "")) / "TRAE SOLO CN" / "User" / "mcp.json",
    ]
    for cfg_path in candidate_files:
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                servers = data.get("mcpServers", {})
                target = servers.get("tencent-docs") or servers.get("qa-automation-mcp")
                if isinstance(target, dict):
                    t = (
                        target.get("headers", {}).get("Authorization")
                        or target.get("env", {}).get("TENCENT_DOCS_MCP_TOKEN")
                    )
                    if t and str(t).strip():
                        return str(t).strip()
            except Exception:
                pass

    raise RuntimeError(
        "Tencent Docs MCP token is not configured. Set TENCENT_DOCS_MCP_TOKEN "
        "or configure it in the local MCP client settings."
    )
ACTIVE_PROFILE = active_profile()
ACTIVE_IFRAME_SELECTOR = ACTIVE_PROFILE.active_iframe_selector
ANTD_OVERLAY_SELECTOR = ",".join(ACTIVE_PROFILE.overlay_selectors)
OVERLAY_OBSERVER_KEY = "__qa_automation_overlay_observer__"

PLAYWRIGHT_INSTALL_HINT = (
    "Playwright 未安装或不可用。请在安装额外依赖后重试:\n"
    "  uv sync --extra browser\n"
    "  uv run playwright install chromium\n"
    "或者检查当前 Python 环境是否支持 playwright。"
)

# Solidified Windows 11 Dark HD high-definition pointer cursor (32x32, hotspot at (5, 10))
_EMBEDDED_CURSOR_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAB8klEQVR42u2WTUsCURSGy68srSZF6Z"
    "OIIiho1zJCw7XQOgjFH+BP0HLVbjYRtHFb0CzCHyC4aycJg7kRgtnoQnDET2Q6dzgTw6Bpee/QYg"
    "684L0jvM85Z+bcOzdnhRVW/OOYN8g8Y1EU/YVCIQC/XSgnyG4KyHA45BWMVqt1D1srIC9ogTnEYD"
    "C4JMY8z6si0Wg0nuFRELQKcjOFgOxviCn8VJVOp1WIer3+AutNEMcUwgigh6jVagJziFEApkLAO3"
    "A7CmAChIMaxE8AYyDWQIvUICYB6CHa7baYSCSOqEJMA0AUi8W+IeLx+DHs+ahATAugh2g2m2+w3j"
    "K8mOwB9O3I5XJXsF7Hien8cxV+C5BMJlUAQRCuYb2N05KMbBsTgFAopKRSKSWfzyvValU173a7Ej"
    "w7xDawASDGxFQLWZY/JEl6LRaLd9Fo9BT+s4vnhRdPT3otIBmT6Pf7cqlUeoxEImewTz6/AzTeAP"
    "lByzOfmNoo5jhONc9ms6p5pVJ5CofD57BHPrl97HcQZwAxXsLMZxvNnU7nghiScmslL5fLD/DoBP"
    "u8ozuaPZixdlmx0ZiGtl6vl4FWvIM+IfMMZryHpfahsUtnSv0e6MCSksESwIz9eDOie/iMqwKW1Y"
    "3ZenDMuky7FyKEHbN10OyxFcb4AvzesBnJB6WlAAAAAElFTkSuQmCC"
)
_EMBEDDED_CURSOR_DATA_URL = f"data:image/png;base64,{_EMBEDDED_CURSOR_PNG_BASE64}"
_CURSOR_HOT_X = 5
_CURSOR_HOT_Y = 10
_CURSOR_WIDTH = 32
_CURSOR_HEIGHT = 32
