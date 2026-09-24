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

# 自适应差分收敛（迁移自 DrissionPage-MCP overlays.py 的设计）：

# 交互后不再"死等满 settle_ms"：只有【确实观察到 DOM 变更】+【变更后安静 quiet_ms】
# +【无 loading 骨架】三个条件同时成立才提前收口；任一不成立就照旧等满 settle_ms。
# 这样既拿到毫秒级快路径，又完整保留既有"观察窗口"语义（无变更时窗口一分钟都不少）。
OVERLAY_ADAPTIVE_SETTLE = _env_bool("QA_AUTOMATION_OVERLAY_ADAPTIVE_SETTLE", True)
OVERLAY_QUIET_MS = _env_int("QA_AUTOMATION_OVERLAY_QUIET_MS", 25)
OVERLAY_PROBE_MS = _env_int("QA_AUTOMATION_OVERLAY_PROBE_MS", 30)

SHOW_CURSOR = _env_bool("QA_AUTOMATION_SHOW_CURSOR", True)
SHOW_DRAG_GHOST = _env_bool("QA_AUTOMATION_SHOW_DRAG_GHOST", True)
QA_AUTOMATION_PROJECT_ROOT = os.getenv("QA_AUTOMATION_PROJECT_ROOT", "").strip()


TENCENT_DOCS_MCP_URL = os.getenv("TENCENT_DOCS_MCP_URL", "https://docs.qq.com/openapi/mcp")


# ---------------- 登录凭据：只允许来自环境变量，绝不写进代码 ----------------
#
# 为什么是硬规则而不是"方便起见留个默认值"：MCP 工具的签名默认值会进入
# inputSchema，而 schema 的每一轮对话都会整份下发给模型。把口令写在默认参数里，
# 等于把它常驻塞进 LLM 上下文与所有会话日志——这既是泄密面，也是白烧的 token。
# 实测本项目 `browser_login` 的 schema 因此长期携带明文口令。

APS_BASE_URL = os.getenv("QA_AUTOMATION_APS_URL", "").strip()


def _login_env(name: str) -> str:
    """调用期读取，而非 import 期绑定。

    import 期取值会让单测无法用 monkeypatch 环境变量验证缺失分支，
    也扛不住宿主在进程启动后才注入 env 的情形。APS_BASE_URL 保留为
    模块常量仅供只读展示类逻辑使用。
    """
    return os.getenv(name, "").strip()


def resolve_login_credentials(
    username: str | None = None,
    password: str | None = None,
    url: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """解析登录三要素：显式入参优先，其次环境变量；两者都缺则返回 None。

    Returns:
        (username, password, url) —— 任一项为 None 表示未配置，
        由调用方给出指名环境变量缺哪一项的可操作报错。
    """
    resolved_user = (username or "").strip() or _login_env("QA_AUTOMATION_LOGIN_USER") or None
    resolved_password = (password or "").strip() or _login_env("QA_AUTOMATION_LOGIN_PASSWORD") or None
    resolved_url = (url or "").strip() or _login_env("QA_AUTOMATION_APS_URL") or None
    return resolved_user, resolved_password, resolved_url


def credential_missing_message(*, user: bool, password: bool) -> str:
    """拼出"缺哪一项、去哪儿补"的报错文案（不含任何凭据值）。"""
    missing = []
    if not user:
        missing.append("QA_AUTOMATION_LOGIN_USER")
    if not password:
        missing.append("QA_AUTOMATION_LOGIN_PASSWORD")
    hint = "、".join(missing)
    return (
        f"未配置登录凭据：缺少 {hint} 环境变量。"
        "请在 .env.qa-automation（参见 .env.qa-automation.example）或 MCP 客户端的 env 中设置，"
        "也可以在调用 browser_login 时显式传 username/password。"
        "凭据不支持写死在代码或工具默认参数里——那会让它随 schema 每轮进入模型上下文。"
    )


def resolve_tencent_docs_token() -> str:
    """Resolve the Tencent Docs MCP token from explicit local configuration or environment."""
    for env_var in ("TENCENT_DOCS_MCP_TOKEN", "TENCENT_DOCS_TOKEN", "TENCENT_API_KEY"):
        token = os.getenv(env_var)
        if token and token.strip():
            return token.strip()

    # 扫描当前工作目录、项目根目录以及父级目录下的 .mcp.json 与 .mcp.json.example
    candidate_dirs = [Path.cwd(), Path(__file__).resolve().parent.parent]
    if QA_AUTOMATION_PROJECT_ROOT:
        candidate_dirs.append(Path(QA_AUTOMATION_PROJECT_ROOT))
    for curr in list(candidate_dirs):
        p = curr.resolve()
        for _ in range(5):
            if p not in candidate_dirs:
                candidate_dirs.append(p)
            if p.parent == p:
                break
            p = p.parent

    candidate_files: list[Path] = []
    for d in candidate_dirs:
        for fname in (".mcp.json", ".mcp.json.example", "mcp.json"):
            fpath = d / fname
            if fpath not in candidate_files:
                candidate_files.append(fpath)

    # 常见 MCP 客户端全局配置文件
    candidate_files.extend([
        Path.home() / ".mcporter" / "mcporter.json",
        Path(os.getenv("APPDATA", "")) / "TRAE SOLO CN" / "User" / "mcp.json",
        Path(os.getenv("APPDATA", "")) / "Cursor" / "User" / "globalStorage" / "mcp.json",
        Path(os.getenv("APPDATA", "")) / "Claude" / "claude_desktop_config.json",
    ])

    for cfg_path in candidate_files:
        if cfg_path.exists() and cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                servers = data.get("mcpServers", {})
                for key in ("tencent-docs", "tencent_docs", "qa-automation-mcp", "qa-automation"):
                    target = servers.get(key)
                    if isinstance(target, dict):
                        t = (
                            target.get("headers", {}).get("Authorization")
                            or target.get("env", {}).get("TENCENT_DOCS_MCP_TOKEN")
                            or target.get("env", {}).get("TENCENT_DOCS_TOKEN")
                        )
                        if t and str(t).strip() and not str(t).startswith("<"):
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
