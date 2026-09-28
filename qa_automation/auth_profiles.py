"""账号档案（多账号预配置）与登录态缓存。

登录流程的两个支撑层，配合 auth.py / browser.py 的既有登录工具使用：

1. **账号集中预配置**在 TOML 中，路径优先级：
   ``QA_AUTOMATION_PROFILES_FILE`` > ``<MCP 项目根>/profiles.toml`` > ``<cwd>/profiles.toml``；
   支持 ``[profiles.xxx]`` 外层表，也支持扁平写法（顶层即字段）。
2. **凭据只驻留服务端**：登录工具按档案名取用，任何返回值都不含密码。
3. **登录态缓存**：登录成功后将 ``token + cookies`` 落盘，登录工具优先复用，
   命中即“注入 cookies 秒级恢复”，避免重复登录与验证码。

TOML 示例::

    [profiles.<档案名>]
    host_prefix = "<环境前缀>"      # 推导站点地址；也可直接给 base_url
    username    = "<账号>"
    password    = "<口令>"
    role        = "<角色说明>"

环境变量（统一写在 .env）::

    QA_AUTOMATION_PROFILES_FILE   档案文件路径（缺省 <MCP 项目根>/profiles.toml）
    QA_AUTOMATION_ACCOUNT         默认档案名（缺省取第一个档案）
    QA_AUTOMATION_SESSION_DIR     缓存目录（缺省 <工作区产物>/.qa-automation/sessions）
    QA_AUTOMATION_SESSION_TTL     缓存有效期秒（缺省 43200 = 12 小时）
    QA_AUTOMATION_SESSION_PERSIST 是否落盘（缺省 true；false 则仅内存缓存）
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .workspace import artifact_dir

HOST_TEMPLATE = "{prefix}-scm.hoolinks.com"
ADMIN_URL_TEMPLATE = "https://{host}/static/admin/"
LOGIN_PAGE_TEMPLATE = "https://{host}/static/admin/login"
DEFAULT_PROFILES_FILE = "profiles.toml"
DEFAULT_ACCOUNT = "aps"
DEFAULT_SESSION_TTL = 43200.0
DEFAULT_SESSION_DIRNAME = "sessions"

# 与真实浏览器一致的默认 UA（可被档案的 user_agent 覆盖）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

# 档案字段（TOML 键名）
_FIELDS = (
    "host_prefix",
    "base_url",
    "admin_url",
    "login_page",
    "cookie_domain",
    "access_domain",
    "api_prefix",
    "username",
    "password",
    "role",
    "captcha_path",
    "captcha_key",
    "login_path",
    "username_field",
    "password_field",
    "captcha_field",
    "success_field",
    "message_field",
    "token_field",
    "token_store",
    "user_agent",
    "timeout",
    "verify_ssl",
)

# 内存缓存（QA_AUTOMATION_SESSION_PERSIST=false 时使用）
_MEMORY_SESSIONS: dict[str, dict[str, Any]] = {}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _project_root() -> Path:
    """MCP 项目根目录（本文件位于 <root>/qa_automation/ 下）。"""
    return Path(__file__).resolve().parents[1]


def profiles_file() -> Path:
    """档案文件路径：环境变量 > MCP 项目根 > 当前工作目录。"""
    explicit = _env("QA_AUTOMATION_PROFILES_FILE")
    if explicit:
        return Path(explicit).expanduser()
    project_default = _project_root() / DEFAULT_PROFILES_FILE
    if project_default.exists():
        return project_default
    return Path.cwd() / DEFAULT_PROFILES_FILE


@dataclass(frozen=True)
class AccountProfile:
    """一个可登录的账号档案（含口令，禁止序列化给客户端）。"""

    name: str
    username: str
    password: str
    origin: str
    admin_url: str
    login_page: str
    cookie_domain: str
    api_prefix: str = ""
    role: str | None = None
    captcha_path: str = "/scmpsm/login/validateCode"
    captcha_key: str = "regValidateCode"
    login_path: str = "/scmpsm/login/signin"
    username_field: str = "userName"
    password_field: str = "userPwd"
    captcha_field: str = "vcode"
    success_field: str = "ok"
    message_field: str = "msg"
    token_field: str = "data"
    token_store: str = "HL-Access-Token"
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 20.0
    verify_ssl: bool = True
    source: str = "toml"

    def public(self) -> dict[str, Any]:
        """可安全返回给客户端的字段（不含口令）。"""
        return {
            "profile": self.name,
            "username": self.username,
            "role": self.role,
            "origin": self.origin,
            "admin_url": self.admin_url,
            "cookie_domain": self.cookie_domain,
            "source": self.source,
        }


def _derive_urls(raw: dict[str, Any]) -> tuple[str, str, str, str]:
    """由 host_prefix / base_url 推导 (origin, admin_url, login_page, cookie_domain)。"""
    base_url = str(raw.get("base_url") or "").strip()
    prefix = str(raw.get("host_prefix") or "").strip()

    if base_url:
        parts = urlsplit(base_url if "://" in base_url else f"https://{base_url}")
        origin = f"{parts.scheme}://{parts.netloc}"
        host = parts.netloc
    elif prefix:
        host = prefix if "." in prefix else HOST_TEMPLATE.format(prefix=prefix)
        origin = f"https://{host}"
    else:
        host = ""
        origin = ""

    admin_url = str(raw.get("admin_url") or "").strip() or (
        ADMIN_URL_TEMPLATE.format(host=host) if host else ""
    )
    login_page = str(raw.get("login_page") or "").strip() or (
        LOGIN_PAGE_TEMPLATE.format(host=host) if host else ""
    )
    cookie_domain = str(raw.get("cookie_domain") or "").strip() or (
        "." + ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    )
    return origin, admin_url, login_page, cookie_domain


def _build(name: str, raw: dict[str, Any], source: str) -> AccountProfile:
    data = {k: raw.get(k) for k in _FIELDS if raw.get(k) is not None}
    origin, admin_url, login_page, cookie_domain = _derive_urls(data)
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not origin:
        raise ValueError(f"档案 {name} 缺少 host_prefix 或 base_url，无法推导站点地址")
    if not username or not password:
        raise ValueError(f"档案 {name} 缺少 username / password")
    try:
        timeout = float(data.get("timeout") or 20.0)
    except (TypeError, ValueError):
        timeout = 20.0
    return AccountProfile(
        name=name,
        username=username,
        password=password,
        origin=origin,
        admin_url=admin_url,
        login_page=login_page,
        cookie_domain=cookie_domain,
        api_prefix=str(data.get("api_prefix") or "").strip(),
        role=(str(data["role"]).strip() if data.get("role") else None),
        captcha_path=str(data.get("captcha_path") or AccountProfile.captcha_path),
        captcha_key=str(data.get("captcha_key") or AccountProfile.captcha_key),
        login_path=str(data.get("login_path") or AccountProfile.login_path),
        username_field=str(data.get("username_field") or AccountProfile.username_field),
        password_field=str(data.get("password_field") or AccountProfile.password_field),
        captcha_field=str(data.get("captcha_field") or AccountProfile.captcha_field),
        success_field=str(data.get("success_field") or AccountProfile.success_field),
        message_field=str(data.get("message_field") or AccountProfile.message_field),
        token_field=str(data.get("token_field") or AccountProfile.token_field),
        token_store=str(data.get("token_store") or AccountProfile.token_store),
        user_agent=str(data.get("user_agent") or DEFAULT_USER_AGENT),
        timeout=timeout,
        verify_ssl=_as_bool(data.get("verify_ssl"), True),
        source=source,
    )


def _legacy_env_profile() -> AccountProfile | None:
    """兼容既有单账号环境变量：QA_AUTOMATION_LOGIN_USER/_PASSWORD/_APS_URL。"""
    username = _env("QA_AUTOMATION_LOGIN_USER")
    password = _env("QA_AUTOMATION_LOGIN_PASSWORD")
    url = _env("QA_AUTOMATION_APS_URL")
    if not (username and password and url):
        return None
    return _build(
        "default",
        {"base_url": url, "admin_url": url, "username": username, "password": password},
        source="env",
    )


def load_profiles() -> dict[str, AccountProfile]:
    """实时读取账号档案（改完即生效，无需重启 MCP）。

    优先级：TOML 档案 > 单账号环境变量（兼容）。任一项解析失败会抛 ValueError，
    由工具层转成可操作的报错，绝不静默降级。
    """
    path = profiles_file()
    profiles: dict[str, AccountProfile] = {}

    if path.exists():
        with path.open("rb") as fh:
            raw_doc = tomllib.load(fh)
        table = raw_doc.get("profiles") if isinstance(raw_doc.get("profiles"), dict) else None
        if table:
            entries = table
        else:
            # 扁平写法：顶层即字段（排除 profiles 之外的杂项键）
            entries = {"default": {k: v for k, v in raw_doc.items() if k in _FIELDS}}
        for name, raw in entries.items():
            if not isinstance(raw, dict):
                continue
            profiles[str(name)] = _build(str(name), raw, source="toml")

    if not profiles:
        legacy = _legacy_env_profile()
        if legacy:
            profiles[legacy.name] = legacy

    return profiles


def resolve_profile(name: str | None = None) -> AccountProfile:
    """按档案名、账号名或角色关键词解析账号；省略时取 QA_AUTOMATION_ACCOUNT，其次 aps / default，最后第一个档案。"""
    profiles = load_profiles()
    if not profiles:
        raise ValueError(
            f"未找到任何账号档案：请配置 {profiles_file()}（格式见 auth_profiles 模块文档），"
            "或设置 QA_AUTOMATION_PROFILES_FILE / QA_AUTOMATION_LOGIN_USER 等环境变量。"
        )

    wanted = (name or _env("QA_AUTOMATION_ACCOUNT") or "").strip()
    if wanted:
        wanted_lower = wanted.lower()
        for key, profile in profiles.items():
            if key.lower() == wanted_lower or profile.username.lower() == wanted_lower:
                return profile
        for profile in profiles.values():
            if profile.role and profile.role.lower() == wanted_lower:
                return profile
        role_matches = [
            profile
            for profile in profiles.values()
            if profile.role and wanted_lower in profile.role.lower()
        ]
        if len(role_matches) == 1:
            return role_matches[0]
        summary = ", ".join(
            f"{k}({p.role})" if p.role else k for k, p in profiles.items()
        )
        raise ValueError(f"未找到档案 {wanted}；可用档案：{summary}")

    for candidate in (DEFAULT_ACCOUNT, "default"):
        if candidate in profiles:
            return profiles[candidate]
    return next(iter(profiles.values()))


# --------------------------------------------------------------------------- #
# 登录态缓存：token + cookies 落盘，登录工具优先复用以实现“秒级恢复”
# --------------------------------------------------------------------------- #


def session_ttl() -> float:
    raw = _env("QA_AUTOMATION_SESSION_TTL")
    try:
        return float(raw) if raw else DEFAULT_SESSION_TTL
    except ValueError:
        return DEFAULT_SESSION_TTL


def session_persist() -> bool:
    return _as_bool(os.getenv("QA_AUTOMATION_SESSION_PERSIST"), True)


def session_dir() -> Path:
    explicit = _env("QA_AUTOMATION_SESSION_DIR")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = artifact_dir().parent / path
        return path
    return artifact_dir(DEFAULT_SESSION_DIRNAME)


def session_file(profile: AccountProfile) -> Path:
    return session_dir() / f"{profile.name}.json"


def save_session(
    profile: AccountProfile,
    *,
    token: str,
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """写入登录态缓存（含明文 token，目录已在 .gitignore 中忽略）。"""
    now = time.time()
    payload = {
        "profile": profile.name,
        "username": profile.username,
        "origin": profile.origin,
        "admin_url": profile.admin_url,
        "token": token,
        "cookies": cookies or [],
        "saved_at": now,
        "expires_at": now + session_ttl(),
        "cookie_names": sorted({c.get("name", "") for c in (cookies or []) if c.get("name")}),
    }
    _MEMORY_SESSIONS[profile.name] = payload
    if session_persist():
        path = session_file(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_session(profile: AccountProfile) -> dict[str, Any] | None:
    """读取登录态缓存（内存优先；TTL 过期即视为无效并清理）。"""
    payload = _MEMORY_SESSIONS.get(profile.name)
    if payload is None and session_persist():
        path = session_file(profile)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if payload is not None:
                _MEMORY_SESSIONS[profile.name] = payload
    if not payload:
        return None
    if float(payload.get("expires_at") or 0) <= time.time():
        clear_session(profile)
        return None
    return payload


def clear_session(profile: AccountProfile) -> bool:
    """清除登录态缓存（内存 + 落盘）。"""
    existed = _MEMORY_SESSIONS.pop(profile.name, None) is not None
    path = session_file(profile)
    if path.exists():
        try:
            path.unlink()
            existed = True
        except OSError:
            pass
    return existed


def session_info(profile: AccountProfile) -> dict[str, Any]:
    """缓存状态摘要（绝不含 token 明文，仅给尾 4 位用于人工比对）。"""
    payload = load_session(profile)
    if not payload:
        return {"session_cached": False}
    token = str(payload.get("token") or "")
    return {
        "session_cached": True,
        "session_token_tail": token[-4:] if token else None,
        "session_saved_at": payload.get("saved_at"),
        "session_expires_at": payload.get("expires_at"),
        "session_cookie_names": payload.get("cookie_names") or [],
    }


def list_profiles() -> list[dict[str, Any]]:
    """档案清单（含缓存状态，不含口令）。"""
    items: list[dict[str, Any]] = []
    for profile in load_profiles().values():
        merged = profile.public()
        merged.update(session_info(profile))
        items.append(merged)
    return items
