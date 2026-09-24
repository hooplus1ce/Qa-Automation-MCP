# -*- coding: utf-8 -*-
"""禅道 BUG 提交、多账户切换与回归测试分析 MCP Server（FastMCP 3.x，适配禅道开源版 17.1）

把「禅道 BUG 提交、图文正文内嵌、多账户切换、人员直派与缺陷修复进展/E2E回归报告生成」封装为标准 MCP 工具服务：
1. 账号与连接管理：
   - zentao_connect: 显式连接
   - switch_account: 运行时快速切换登录账户（支持切换不同人员身份）
   - whoami: 查看当前登录用户账号、真实姓名与权限角色
2. 归属与人员查询：
   - list_products / search_execution / list_modules / find_user
   - search_bugs: 按标题关键字 + 状态检索（分页拉全量后本地过滤）
3. 需求（story）关联：
   - list_stories / find_story: 按需求 ID 或标题关键字解析需求
   - link_story_to_bug: 为已存在 BUG 补关联 / 改绑需求
   - create_bug / update_bug 均支持 story 参数
4. BUG 创建与维护：
   - create_bug: 支持图文正文内嵌、人员中文直派、接口信息自动排版、需求关联
   - update_bug: 字段修正、需求关联与追加截图
   - assign_bug: 专门的缺陷指派工具，支持中文姓名与指派备注
   - attach_image_to_bug / upload_image / attach_file
5. 缺陷修复进展剖析与端到端（E2E）回归测验：
   - list_resolved_bugs: 检索待回归验证的已解决 BUG 列表
   - export_bug_regression_report: 深入解析开发团队修复进展、代码提交、历史激活原因，重构输出 E2E 回归测试指南 Markdown

适配实测要点（禅道 17.1 实测）：
- 默认映射：产品 40 (SCM)，执行 578 (生和堂APS)
- 定制类型：codeerror, UserExperience, JMYHZX, designdefect, performance, onlinereq, others, others3, others4
- 图文机制：REST API POST /files 采用专用表单字段 imgFile，富文本使用 <img onload="setImageSize(this,0)" src="...">
- 菜单路径：一律使用 '→'（严禁使用裸 '>'，避免被禅道解析截断）
- 分页陷阱：GET /products/{id}/bugs 会**忽略 title= 过滤参数**，且默认只返回首页；
  SCM 下 BUG 已达 462 条，只查首页会大面积漏检 → search_bugs / list_resolved_bugs 已改为全量分页后本地过滤
- 需求关联：BUG 的 story 字段走 PUT /bugs/{id} {"story": <id>} 最稳（建单接口对 story 支持不稳定），
  需求可用 GET /stories/{id} 校验、GET /products/{id}/stories 分页扫描
- 影响版本 openedBuild 为必填；为空时建单会被静默拒绝
"""
import base64
import functools
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from datetime import datetime
from html import escape, unescape
from pathlib import Path
from typing import Optional, List, Dict, Any

import openpyxl
import requests
from fastmcp import FastMCP

mcp = FastMCP(
    name="zentao-bug",
    instructions=(
        "禅道 BUG 提交、多账号切换与回归测验分析服务（适配开源版 17.1 定制规范，严格对齐 skill://zentao-bug-ops/ 权威规范）。\n"
        "核心执行规范与业务纪律：\n"
        "1. 执行原则【最高优先】：永不确认直接提交（建单、传图、指派、关联需求一律直接执行，严禁询问确认；目标是提单快）；提交后必须回读自检（核对各字段与正文落位）。\n"
        "2. 默认归属：产品 SCM (ID 40)，执行 生和堂APS (ID 578)。\n"
        "3. 所属模块【强制铁律】：建单/改单时一律不传 module 参数（或显式传空字符串 \"\"），强制默认挂根模块 0（/）。严禁调用 list_modules 自选业务子模块（如绝对严禁选择“公共板块”等通用名称）；SCM 产品下无业务子模块映射。\n"
        "4. 标题规范【强制铁律】：标题必须以【模块名称】开头，格式严格遵循【模块】验证点：现象短语（例如：【插单申请】编辑返回：...），严禁使用其他前缀。菜单路径分隔符必须用 '→'，严禁使用裸 '>'。\n"
        "5. 正文规范【强制覆盖式提交 / 严禁老旧占位符】：提交正文必须全量覆盖，严禁在正文中出现或遗留老旧的 '[步骤]'、'[结果]'、'[期望]' 等占位文本。标准结构必须为纯净的 Markdown/HTML 根因级骨架：\n"
        "   - 【环境说明】：环境、页签、数据基线条数\n"
        "   - 【一句话结论】：核心现象一句话概括\n"
        "   - 【重现步骤】：前置路径 + 编号步骤\n"
        "   - 【实测表现与对照矩阵】：HTML <table> 对比表（# / 交互步骤 / 用户意图 / 预期表现 / 实测表现 / 判定 ❌/✅）\n"
        "   - 【现场实捕 DOM 状态证据】：<pre> 格式的真实 DOM 切片、接口报文或代码片段\n"
        "   - 【期望表现】：明确系统正确行为与状态\n"
        "   - 【关键排除】：明确写出已排除的相邻正常项与反向证据，避免开发误判\n"
        "   - 【根因链路推导】：<pre> 格式的推导流程（交互触发 → 逻辑错误 → 状态脱节/死锁）\n"
        "   - 【业务影响】：直接危害与潜在波及面\n"
        "   - 【修复建议】：HTML <table> 修复方案矩阵（# / 方案 / 改动位置 / 工作量 / 说明）\n"
        "   - 【回归断言点】：<ol> 编号的可执行判定断言\n"
        "   - 【证据截图】：首图由 create_bug(image_path=...) 内嵌，多图由 attach_image_to_bug 串行追加并配 caption 说明\n"
        "6. 正文 HTML 标签规范：仅允许使用白名单标签（p/b/i/ul/ol/li/pre/table/tr/th/td/h3/img），严禁嵌套同类标签；正文含代码块时首部显式标注 <p>[步骤]</p> 走整段原样写入分支。\n"
        "7. 定级口径（AI 自评，不要问用户）：\n"
        "   - pri（紧急度）：1 阻断功能不可用（主流程阻塞、死锁），2~3 一般功能问题，3 纯显示/文案类。\n"
        "   - severity（影响面）：1 致命（主流程不可用/数据丢失），2 严重（功能或数据错误），3 一般（功能缺失但可绕过/展示异常），4 轻微（文案或命名）。\n"
        "8. 人员指派：assign_bug / create_bug / update_bug 均支持传中文真实姓名（如 '万棚', '赵浩源', '段广', '胡嘉斌', '李科勇'）或工号账号直派。\n"
        "9. 需求关联：create_bug(story='审批单列表' 或 '18058') 建单即关联需求；存量单据用 link_story_to_bug(bug_id, story)；可用 find_story / list_stories 核对或反查需求。\n"
        "10. 关键词规范：必须包含用例编号与核心特征词，格式为 <用例编号>,<要点1>,<要点2>。\n"
        "11. 图文全量覆盖护栏：steps 字段是整字段覆盖语义，更新正文时必须带上原正文已有 <img>，严禁丢图；多图必须逐张串行调用 attach_image_to_bug。\n"
        "12. 检索陷阱防护：search_bugs 与 list_resolved_bugs 必须基于分页拉全量后本地过滤，避免禅道忽略 title 过滤参数引发漏检。\n"
        "13. E2E 回归测验指南：调用 export_bug_regression_report(bug_id) 自动解析 BUG 底部修复进展、代码提交并输出 E2E 回归 Markdown 指南。"
    ),
)

# 定制类型清单（实测确认）
TYPE_MAP = {
    "代码错误": "codeerror",
    "用户体验": "UserExperience",
    "界面优化": "JMYHZX",
    "设计缺陷": "designdefect",
    "性能问题": "performance",
    "操作问题": "onlinereq",
    "其他问题": "others",
    "其他": "others",
    "通道问题": "others3",
    "产品转需求": "others4",
    # 英文 Key 大小写映射
    "codeerror": "codeerror",
    "userexperience": "UserExperience",
    "jmyhzx": "JMYHZX",
    "designdefect": "designdefect",
    "performance": "performance",
    "onlinereq": "onlinereq",
    "others": "others",
    "others3": "others3",
    "others4": "others4",
    # 历史与别名容错
    "测试脚本": "codeerror",
    "配置相关": "onlinereq",
    "安全相关": "codeerror",
    "接口问题": "codeerror",
}
TYPE_DEFAULT = "codeerror"
DEFAULT_PRODUCT = "40"       # SCM
DEFAULT_EXECUTION = "578"    # 生和堂APS

# ── 超时/重试预算（务必与 MCP 客户端上限对齐）─────────────────────────────
# 实测：DSH 的 MCP 客户端 toolCallTimeoutMs = 60s。原配置 timeout=25 + RETRY=2
# 意味着单个 HTTP 请求最坏 3×25 = 75s > 60s，复合工具（上传+读+写）最坏 225s，
# 必然偶发 "-32001 Request timed out"；而超时后调用方【无法得知服务端是否最终成功】。
# 实测禅道本身极快（login 0.10s / 读单 0.08s / 上传 236KB 0.26s、603KB 0.09s），
# 瓶颈从不在服务端，故收紧为：单请求 12s，且只对"连接类"失败重试一次。
# 最坏预算：连接失败 12+12 = 24s；读超时 12s；三连复合工具 ≈ 36s < 60s。
HTTP_TIMEOUT = 12
RETRY = 1

# ── 分页扫描墙钟闸门 ──────────────────────────────────────────────────────
# 单请求有 12s 上限，但【循环】没有：BUG_PAGE_MAX=60 页 × 12s = 720s，
# 加了连接重试最坏 1440s —— 远超 MCP 客户端 60s，客户端超时后服务端还在跑，
# 调用方拿到的是"结果未知"（最坏的失败模式）。故给每个分页循环加总闸门：
# 超时即停，返回已收集的部分并在返回串里显式标注 [部分结果]。
PAGE_DEADLINE_S = 45

# get_bug 回显的正文上限：steps 是整段富文本，实测单条可到 6KB+，
# 全量回吐会把对话上下文挤满（多数场景只需确认"落位对不对 + 有几张图"）。
STEP_DISPLAY_MAX = 4000

# export_bug_regression_report 回显上限：报告含全部流转历史，长寿命 BUG 可达数百 KB
REPORT_RETURN_MAX = 8000




class _Deadline:
    """分页扫描的墙钟闸门。"""

    def __init__(self, seconds: float = PAGE_DEADLINE_S):
        self.t0 = time.monotonic()
        self.limit = float(seconds)

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.t0) >= self.limit

    def remaining(self) -> float:
        """剩余预算（秒），至少留 1s，供单请求 timeout 收敛。"""
        return max(1.0, self.limit - (time.monotonic() - self.t0))


def _tool(fn):
    """注册 MCP 工具，并统一兜住异常 → 一律以 `[error] ...` 文本返回。

    为什么必须做：FastMCP 会把未捕获异常抛成 MCP 协议层错误，客户端只看到
    "tool call failed"，看不到「禅道 401」「页面结构变了」这类可操作信息。而本项目里
    大量工具是「先 `_get_client()` 再进 try」，登录失败/网络异常很容易在 try 之外抛出
    （实测 `upload_image` / `attach_image_to_bug` / `attach_file` 都是这种写法）。
    统一兜底后，任何工具都不会再以 traceback 收场，调用方拿到的是可读、可判断的文本。

    用 `functools.wraps` 保留原签名与注解，FastMCP 才能继续正确生成入参 schema。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:      # noqa: BLE001 —— 兜底就是它的职责
            return f"[error] {fn.__name__} 执行失败: {type(e).__name__}: {e}"
    return mcp.tool(wrapper)



# 分页保护：REST 的 title/keyword 过滤参数会被服务端忽略（实测 17.1），
# 因此 search_bugs / list_resolved_bugs 必须本地分页拉全量后过滤。
BUG_PAGE_SIZE = 100          # /products/{id}/bugs 单页上限
BUG_PAGE_MAX = 60            # 全量拉取最大页数（60*100 = 6000 条）
STORY_PAGE_SIZE = 100        # /products/{id}/stories 单页上限
STORY_SCAN_MAX_PAGES = 60    # 按标题扫描需求的最大页数

_STATE = {"client": None}
_STATE_LOCK = threading.Lock()


class _LockedSession:
    """给 `requests.Session` 的每次调用加锁。

    为什么必须做：FastMCP 在线程池里执行**同步**工具，两个工具调用会并发；
    而 `requests.Session` 的连接池与 cookie jar 并非为跨线程并发设计，
    全局单例 `_STATE["client"]` 还可能被 `switch_account` 中途替换。
    把每次 HTTP 调用串行化，是成本最低、且不改变语义的修法
    （单个工具本就是"几次短请求"，串行代价远小于竞态风险）。
    """

    def __init__(self, session, lock):
        object.__setattr__(self, "_s", session)
        object.__setattr__(self, "_lock", lock)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_s"), name)
        if not callable(attr):
            return attr
        lock = object.__getattribute__(self, "_lock")

        def wrapper(*a, **kw):
            with lock:
                return attr(*a, **kw)
        return wrapper

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_s"), name, value)



class ZentaoClient:
    """禅道客户端：REST API(token) 为主，web 会话为辅。"""

    def __init__(self, base_url, account, password, timeout=HTTP_TIMEOUT):
        self.base_web = base_url.rstrip("/")
        self.base = self.base_web
        if not self.base.endswith("/api.php/v1"):
            self.base += "/api.php/v1"
        self.account, self.password = account, password
        self.timeout = timeout
        self.token = None
        self._web_logged = False
        self._users_cache = None
        self._last_bug_id = 0
        self._current_user_profile = None
        self._logging_in = False        # login() 重入保护（见 login 与 _req 的 401 分支）
        self._lock = threading.RLock()  # 串行化 HTTP：见 _LockedSession 的说明
        self.s = _LockedSession(requests.Session(), self._lock)

    def _req(self, method, path, _timeout=None, **kw):
        url = f"{self.base}{path}"
        extra_headers = kw.pop("headers", None) or {}
        timeout = _timeout or self.timeout
        last_err = None
        for attempt in range(RETRY + 1):
            # ⚠ 每轮都**重建** Token 头：刷新 Token 后必须用【新】token 重试。
            # 旧实现在循环外拍好 headers，重试仍带旧 token → 再撞 401，
            # "重新登录"形同虚设（实测 Token 序列 [None, None, None, 'NEW-TOKEN-2']）。
            headers = dict(extra_headers)
            if self.token:
                headers["Token"] = self.token
            try:
                r = self.s.request(method, url, headers=headers, timeout=timeout, **kw)
            except requests.exceptions.ConnectionError as e:
                # 连接类失败（服务未起 / 端口不通），重试一次是合理的
                last_err = e
                if attempt < RETRY:
                    time.sleep(1.0)
                    continue
                break
            except requests.exceptions.RequestException as e:
                # 读超时等：重试只会让墙钟时间翻倍并撞穿 MCP 客户端 60s 上限，
                # 直接失败把错误交回调用方，避免"客户端已超时、服务端还在写"的黑洞。
                last_err = e
                break
            # Token 过期 → 重新登录后重试。
            # 三个前置条件缺一不可：还有重试机会 / 不是登录请求自身触发的 / 账号已配置。
            # 若不加 `not self._logging_in`，POST /tokens 自身返回 401（凭证错误）时
            # 会 _req → login → _req → login 无限递归，实测能把请求数瞬间打满。
            if (r.status_code == 401 and self.account
                    and attempt < RETRY and not self._logging_in):
                try:
                    if self.login():
                        continue
                except Exception as e:      # 登录失败：把原始 401 交回调用方，不吞不炸
                    last_err = e
                    break
            if r.status_code >= 500 and attempt < RETRY:
                time.sleep(1.5)
                continue
            return r
        raise RuntimeError(f"请求失败 {url}: {last_err}")

    @staticmethod
    def _json(r):
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"响应非JSON(HTTP {r.status_code}): {r.text[:200]}")

    def login(self):
        # 整个登录过程持锁：避免并发工具同时发起登录、互相覆盖 self.token。
        # RLock 可重入，因为 login 内部还要走 _req → self.s.request（同一把锁）。
        with self._lock:
            return self._login_locked()

    def _login_locked(self):
        # 重入保护：POST /tokens 自身 401 时，_req 会想再调 login()，
        # 不拦就是无限递归（实测）。这里直接给出可读错误，而不是 RecursionError。
        if self._logging_in:
            raise RuntimeError(
                "登录重入被拦截：POST /tokens 返回 401，凭证可能不正确。"
                "请检查 ZENTAO_ACCOUNT / ZENTAO_PASSWORD。"
            )
        self._logging_in = True
        try:
            r = self._req("POST", "/tokens",
                          json={"account": self.account, "password": self.password})
            if r.status_code not in (200, 201):
                hint = "（请确认：①账号密码正确 ②禅道≥12.0开源版 ③后台-二次开发-接口已开启）" \
                    if r.status_code in (401, 403, 404) else ""
                raise RuntimeError(f"获取Token失败 HTTP {r.status_code} {r.text[:200]}{hint}")
            data = self._json(r)
            self.token = data.get("token")
            if not self.token:
                raise RuntimeError(f"响应缺少token字段: {data}")
            # 重置缓存
            self._users_cache = None
            self._current_user_profile = None
            return self.token
        finally:
            self._logging_in = False

    def whoami(self) -> Dict[str, Any]:
        """获取当前登录用户的详细信息。"""
        if self._current_user_profile:
            return self._current_user_profile
        # 从 users 列表中匹配当前 account
        users = self.list_users()
        for u in users:
            if u.get("account") == self.account:
                self._current_user_profile = u
                return u
        # 兜底
        return {"account": self.account, "realname": self.account}

    def find_product(self, ref):
        if not ref:
            return DEFAULT_PRODUCT
        r = self._req("GET", "/products", params={"limit": 500})
        items = self._json(r)
        if isinstance(items, dict):
            items = items.get("products", [])
        if str(ref).isdigit():
            for p in items:
                if str(p["id"]) == str(ref):
                    return str(p["id"])
        for p in items:
            if p.get("name") == ref or ref in str(p.get("name", "")):
                return str(p["id"])
        return str(ref)

    def find_module(self, product_id, name):
        if not name:
            return 0
        r = self._req("GET", "/modules", params={"id": product_id, "type": "bug"})
        if r.status_code != 200:
            return 0
        items = self._json(r)
        if isinstance(items, dict):
            items = items.get("modules", [])
        if str(name).isdigit():
            return int(name)
        for m in items:
            if m.get("name") == name or name in str(m.get("name", "")):
                return m["id"]
        return 0

    def find_execution(self, ref):
        if not ref:
            return DEFAULT_EXECUTION
        r = self._req("GET", "/executions", params={"limit": 500})
        items = self._json(r)
        if isinstance(items, dict):
            items = items.get("executions", [])
        if str(ref).isdigit():
            for e in items:
                if str(e["id"]) == str(ref):
                    return e["id"]
        for e in items:
            if e.get("name") == ref or ref in str(e.get("name", "")):
                return e["id"]
        return None

    def _users_from_rest(self):
        """REST /users 拉取用户列表（无 company-browse 权限时返回空列表）。"""
        try:
            r = self._req("GET", "/users", params={"limit": 500})
            items = self._json(r)
            if isinstance(items, dict):
                items = items.get("users", [])
            return items if isinstance(items, list) else []
        except Exception:
            return []

    def _users_from_web(self, bug_id):
        """从 bug-activate 页的 assignedTo 下拉提取全员列表（REST 不可用时的兜底）。

        ⚠ **bug_id=0 也必须照常请求**：实测 `bug-activate-0.html?onlybody=yes` 一样返回
        带 `assignedTo` 下拉的完整表单（471 个选项）。本机 REST `/users` 无权限、
        `_auto_bug_id()` 又可能拿不到 ID，这条 0 号通道往往是**唯一**的用户列表来源 ——
        曾因加了 `if not bug_id: return []` 而把用户解析整体打断（find_user 全部失败）。
        """
        self._web_login()
        r = self.s.get(
            f"{self.base_web}/bug-activate-{bug_id}.html?onlybody=yes", timeout=self.timeout
        )
        if r.status_code != 200:
            return []
        html = r.text
        sel = re.search(r"<select[^>]*name=['\"]assignedTo['\"][^>]*>(.*?)</select>", html, re.S)
        if not sel:
            return []
        users = []
        for acc, name in re.findall(
            r"<option[^>]*value=['\"]([^'\"]*)['\"][^>]*>([^<]{0,40})</option>", sel.group(1)
        ):
            acc = acc.strip()
            if not acc:
                continue
            users.append({
                "account": acc,
                "realname": re.sub(r"^[A-Za-z]:\s*", "", name.strip()),
                "role": None,
                "dept": None,
            })
        return users

    def _auto_bug_id(self):
        """取一个可用 BUG ID 供兜底页面使用：优先最近访问的，其次产品最新一条。

        ⚠ `GET /bugs?limit=1` 在禅道 17.1 上**不存在**（本机实测返回 0），
        所以必须再用 `/products/{id}/bugs?limit=1` 兜一层；否则 `_auto_bug_id()`
        恒为 0，用户列表就只能靠 `bug-activate-0.html` 那条通道（虽然实测可用，
        但多一层可靠来源总是好的）。
        """
        if self._last_bug_id:
            return self._last_bug_id
        for path, params in ((f"/products/{DEFAULT_PRODUCT}/bugs", {"limit": 1}),
                             ("/bugs", {"limit": 1})):
            try:
                r = self._req("GET", path, params=params)
                if r.status_code != 200:
                    continue
                items = self._json(r)
                if isinstance(items, dict):
                    items = items.get("bugs", [])
                if isinstance(items, list) and items:
                    bid = items[0].get("id")
                    if bid:
                        return bid
            except Exception:
                continue
        return 0

    def list_users(self, keyword="", bug_id=0):
        # ⚠ 只缓存【非空】结果。旧实现是 `if self._users_cache is None:`，而**空列表不是 None**：
        # 一旦首次拉取失败（REST /users 无权限 + web 兜底拿到 []），空列表就被永久缓存，
        # 之后即使传入真实 bug_id 也不再重试 → find_user 永远解析不出账号。
        if not self._users_cache:
            users = self._users_from_rest()
            if not users:
                try:
                    users = self._users_from_web(bug_id or self._auto_bug_id())
                except Exception:
                    users = []
            if users:
                self._users_cache = users
        users = self._users_cache or []
        if not keyword:
            return users
        kw = keyword.strip().lower()
        return [
            u for u in users
            if kw in (u.get("account") or "").lower()
            or kw in (u.get("realname") or "").lower()
        ]

    def find_user(self, ref: str, bug_id: int = 0) -> str:
        """根据真实姓名或账号查找用户 account 标识。

        解析不到时的处理（关键）：
          - 入参形如账号（纯 ASCII）→ 原样返回，允许直接透传（禅道本就接受账号）
          - 入参是中文姓名等 → **抛错**，绝不把中文当账号发出去。
            旧实现无条件 `return ref_strip`，于是 `assign_bug(65295, "张三")` 会 PUT
            `{"assignedTo": "张三"}` 并打印「已成功指派给: 张三（账号: 张三）」——
            表面上成功、实际没指派，属最危险的一类静默错误。
        """
        if not ref:
            return ""
        ref_strip = ref.strip()
        users = self.list_users(bug_id=bug_id)
        # 1. 精确匹配 account
        for u in users:
            if u.get("account") == ref_strip:
                return u["account"]
        # 2. 精确匹配 realname
        for u in users:
            if u.get("realname") == ref_strip:
                return u["account"]
        # 3. 包含 realname
        for u in users:
            if ref_strip in (u.get("realname") or ""):
                return u["account"]
        if ref_strip.isascii():
            return ref_strip
        raise ValueError(
            f"无法把『{ref_strip}』解析为禅道账号：用户列表为空（REST /users 无权限且 web 兜底失败）"
            f"或姓名不匹配。请先用 find_user(keyword='{ref_strip}') / list_users 核对，"
            f"或直接传账号（如 zhaohaoyuan / wangpeng）。"
        )

    def list_bug_titles(self, product_id, deadline: "_Deadline" = None):
        """拉取产品下【全部】BUG 标题（`submit_bugs_from_xlsx` 的远端去重依据）。

        ⚠ 旧实现把 `total` 当作唯一终止条件：
            if not isinstance(data, dict) or page * 100 >= int(data.get("total", 0) or 0): break
        `total` 缺失或为 0 时 `page*100 >= 0` 立即为真 → **循环只跑一页**，
        标题集最多 100 条；更早的标题一律被当成"新单"→ **重复建单**。
        现改为与 `all_bugs` 同构的终止条件：总量够 / 本页不满 / 本页零新增（服务端忽略 page）。
        """
        dl = deadline or _Deadline()
        titles = set()
        fetched = 0
        page = 1
        page_size = BUG_PAGE_SIZE
        while page <= BUG_PAGE_MAX and not dl.expired:
            r = self._req("GET", f"/products/{product_id}/bugs",
                          params={"limit": page_size, "page": page},
                          _timeout=min(self.timeout, dl.remaining()))
            if r.status_code != 200:
                break
            data = self._json(r)
            bugs = data.get("bugs", []) if isinstance(data, dict) else data
            if not isinstance(bugs, list) or not bugs:
                break
            before = len(titles)
            titles.update(b.get("title", "") for b in bugs)
            fetched += len(bugs)
            total = int(data.get("total", 0) or 0) if isinstance(data, dict) else 0
            if total and fetched >= total:
                break
            if len(bugs) < page_size:
                break
            if len(titles) == before:      # page 被忽略：再翻也没新内容
                break
            page += 1
        return titles

    def all_bugs(self, product_id, page_size: int = BUG_PAGE_SIZE, max_pages: int = BUG_PAGE_MAX,
                 deadline: "_Deadline" = None):
        """分页拉取产品下【全部】BUG。

        背景（17.1 实测）：`GET /products/{id}/bugs` 的 `title=` 等过滤参数会被服务端忽略，
        且默认只返回首页；产品 SCM 下总量已达数百条，只查首页会大面积漏检。
        因此这里拉全量，交由调用方本地过滤。

        Returns:
            (bugs, total, truncated)：bug 列表、产品 BUG 总数、是否因闸门/页上限而提前停止
        """
        dl = deadline or _Deadline()
        bugs: List[Dict[str, Any]] = []
        seen_ids = set()                 # 去重：服务端若忽略 page，同一页会被重复追加
        page = 1
        total = 0
        truncated = False
        page_size = max(1, min(int(page_size or BUG_PAGE_SIZE), BUG_PAGE_SIZE))
        max_pages = max(1, int(max_pages or BUG_PAGE_MAX))
        while page <= max_pages:
            if dl.expired:
                truncated = True
                break
            r = self._req("GET", f"/products/{product_id}/bugs",
                          params={"limit": page_size, "page": page},
                          _timeout=min(self.timeout, dl.remaining()))
            if r.status_code != 200:
                truncated = True
                break
            data = self._json(r)
            chunk = data.get("bugs", []) if isinstance(data, dict) else data
            if not isinstance(chunk, list) or not chunk:
                break
            new = 0
            for b in chunk:
                bid = b.get("id")
                if bid in seen_ids:
                    continue
                seen_ids.add(bid)
                bugs.append(b)
                new += 1
            if isinstance(data, dict):
                total = int(data.get("total", 0) or 0) or total
            if total and len(bugs) >= total:
                break
            if len(chunk) < page_size:
                break
            if new == 0:                 # page 被忽略：再翻也不会有新内容，立即停
                break
            page += 1
        else:
            truncated = True             # 跑满页上限仍未收敛
        return bugs, total, truncated

    def find_story(self, product_id, ref: str):
        """把「需求 ID」或「需求标题关键字」解析为 (story_id, story_title)。

        支持三种写法：
          - 纯数字（如 '18058'）：直接 GET /stories/{id} 校验后返回
          - '#18058' / '18058:xxx'：提取数字后同上
          - 标题关键字（如 '审批单列表'）：分页扫描产品需求列表做包含匹配

        Returns:
            (story_id, story_title)；未命中返回 (None, "")。
            按关键字扫描超时会**抛错**（而不是伪装成"未找到"）。
        """
        raw = str(ref or "").strip()
        if not raw:
            return None, ""
        digit = re.search(r"(\d{2,})", raw)
        # 纯数字 / #123 / 123:标题 这类以 ID 为主的写法，优先按 ID 精确解析
        if digit and (raw.lstrip("#").strip().isdigit() or raw.lstrip("#").split(":")[0].strip().isdigit()):
            sid = int(digit.group(1))
            # ⚠ 旧实现 `except Exception: return None, ""` 有两个问题：
            #   1) 吞掉 500/超时，把"接口故障"伪装成"需求不存在"，建单时静默漏关联；
            #   2) 禅道对不存在的需求返回 404 + JSON，`_json` 能解析成功，
            #      于是拿到的是空 title，却仍然 `return sid, ""` —— **不存在的 ID 被当成有效需求**。
            r = self._req("GET", f"/stories/{sid}")
            if r.status_code == 404:
                return None, ""
            if r.status_code != 200:
                # 实测：禅道对**不存在的需求**返回 400 + {"error":"error"}（不是 404）。
                # 只有 5xx / 网关类错误才算"接口故障"并抛错，否则会把"ID 写错"
                # 误报成系统异常，反而更误导。
                if r.status_code >= 500:
                    raise RuntimeError(
                        f"校验需求 #{sid} 失败：HTTP {r.status_code} {r.text[:200]}"
                        f"（接口故障，无法判断需求是否存在，请重试）"
                    )
                return None, ""
            s = self._json(r)
            title = (s or {}).get("title") if isinstance(s, dict) else None
            if not title:
                return None, ""
            return sid, title
        keyword = raw.lstrip("#")
        dl = _Deadline()
        page = 1
        page_size = STORY_PAGE_SIZE
        while page <= STORY_SCAN_MAX_PAGES:
            if dl.expired:
                # 伪装成"未找到"会让 create_bug 静默漏掉需求关联，必须显式报错
                raise RuntimeError(
                    f"按关键字『{keyword}』扫描需求达到 {PAGE_DEADLINE_S}s 闸门"
                    f"（已扫 {page - 1} 页），无法确认是否命中。"
                    f"请改用需求 ID（如 '18058'），或先单独调 find_story 核对。"
                )
            r = self._req("GET", f"/products/{product_id}/stories",
                          params={"limit": page_size, "page": page},
                          _timeout=min(self.timeout, dl.remaining()))
            if r.status_code != 200:
                break
            data = self._json(r)
            items = data.get("stories", []) if isinstance(data, dict) else data
            if not items:
                break
            for s in items:
                if keyword in str(s.get("title", "")):
                    return s.get("id"), s.get("title", "")
            total = int((data.get("total") if isinstance(data, dict) else 0) or 0)
            if total and page * page_size >= total:
                break
            page += 1
        return None, ""

    def link_story(self, bug_id, story_id):
        """把 BUG 关联到指定需求（REST PUT /bugs/{id} 的 `story` 字段，17.1 实测可用）。"""
        return self.update_bug(bug_id, {"story": int(story_id)})

    def upload_image(self, image_path: str) -> Dict[str, Any]:
        """通过 REST API /files 上传图片（imgFile 字段，适配 KindEditor）。"""
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        ext = path.suffix.lower() or ".png"
        safe_fname = f"img_{uuid.uuid4().hex[:8]}{ext}"
        mime = mimetypes.guess_type(path.name)[0] or "image/png"

        with open(path, "rb") as f:
            content = f.read()

        files = {"imgFile": (safe_fname, content, mime)}
        r = self._req("POST", "/files", files=files)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"图片上传失败 HTTP {r.status_code}: {r.text[:300]}")
        data = self._json(r)
        if "url" not in data:
            raise RuntimeError(f"图片上传未返回 url: {data}")
        return data

    def create_bug(self, product_id: str, payload: dict):
        """建单。

        ⚠ 兜底仅在「路由不存在」时启用（404/405）。旧实现任何非 2xx 都改走 `POST /bugs`：
        若首次 POST 已落库但返回 5xx / 网关超时，兜底会**再建一条重复 BUG**；
        若 `/bugs` 路由本身不存在，抛出的还是兜底请求的错，真实校验错误被吞掉。
        现在只对 404/405 兜底，并把两次错误原文一起抛出。
        """
        pid = str(product_id)
        r = self._req("POST", f"/products/{pid}/bugs", json=payload)
        if r.status_code in (200, 201):
            return self._json(r)
        first = f"POST /products/{pid}/bugs -> HTTP {r.status_code} {r.text[:200]}"
        if r.status_code in (404, 405):
            r2 = self._req("POST", "/bugs", json=dict(payload, product=pid))
            if r2.status_code in (200, 201):
                return self._json(r2)
            raise RuntimeError(f"创建 BUG 失败：{first}；兜底 POST /bugs -> "
                               f"HTTP {r2.status_code} {r2.text[:200]}")
        raise RuntimeError(
            f"创建 BUG 失败：{first}。（未走兜底：非 404/405 的失败可能是参数校验失败、"
            f"也可能服务端已落库，请先用 search_bugs 核对是否已建单，避免重复建单）"
        )

    def get_bug(self, bug_id):
        self._last_bug_id = bug_id
        r = self._req("GET", f"/bugs/{bug_id}")
        if r.status_code != 200:
            raise RuntimeError(f"获取 BUG#{bug_id} 失败 HTTP {r.status_code}: {r.text[:200]}")
        return self._json(r)

    def update_bug(self, bug_id, payload):
        r = self._req("PUT", f"/bugs/{bug_id}", json=payload)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"更新 BUG#{bug_id} 失败 HTTP {r.status_code}: {r.text[:300]}")
        return self._json(r)

    def assign_bug(self, bug_id: int, assigned_to: str, comment: str = ""):
        account = self.find_user(assigned_to, bug_id=bug_id)
        if not account:
            raise ValueError(f"未找到匹配的人员: {assigned_to}")
        payload = {"assignedTo": account}
        if comment:
            payload["comment"] = comment
        return self.update_bug(bug_id, payload)

    def _web_ok(self, r) -> bool:
        """判定 web 通道动作是否**真的**成功（不能只看 HTTP 200）。

        ⚠ 实测两种假成功：
          1. 表单校验/权限失败时禅道返回 200 + `{"result":"fail"}`
          2. web 会话失效时 200 返回**登录页 HTML**
        只看 `status_code == 200` 会把这些都判成成功，于是工具打印「[ok] 已关闭/备注已添加」，
        而引用的是**上一次的** action。判定失败时顺便把 `_web_logged` 置位为 False，
        让下一次调用重新登录（`_web_login` 原本一旦成功就终身复用，会话过期后无法自愈）。
        """
        if r.status_code != 200:
            return False
        body = (r.text or "").strip()
        if not body:
            return True
        try:
            data = json.loads(body)
        except ValueError:
            low = body[:3000].lower()
            if ("user-login" in low or 'id="loginform"' in low
                    or ('name="account"' in low and "password" in low)):
                self._web_logged = False        # 会话失效 → 下次重登
                return False
            return True
        if isinstance(data, dict):
            if "result" in data:
                return str(data["result"]).lower() in ("success", "ok", "true")
            if data.get("error"):
                return False
        return True

    def _web_form_action(self, action_path: str, form_page: str, build_data) -> bool:
        """web 表单动作统一入口：取 kuid → 提交 → 校验 → 会话失效自动重登重试一次。

        Args:
            action_path: 表单 POST 的 URL
            form_page:   取 kuid 的页面 URL（会话失效时它返回登录页，故重登后必须重取）
            build_data:  callable(kuid) -> dict，构造表单字段
        """
        last = ""
        for attempt in range(2):
            self._web_login()
            page = self.s.get(form_page, timeout=self.timeout)
            m = re.search(r"var kuid\s*=\s*'([0-9a-f]+)'", page.text)
            if not m:
                self._web_logged = False
                last = f"页面未解析到 kuid（会话可能已失效或页面结构变更）: {form_page}"
                continue
            r = self.s.post(action_path, data=build_data(m.group(1)), timeout=self.timeout)
            if self._web_ok(r):
                return True
            self._web_logged = False
            last = f"HTTP {r.status_code} {(r.text or '')[:200]}"
        raise RuntimeError(f"web 动作提交失败（已重登重试一次）: {last}")

    def close_bug(self, bug_id: int, comment: str = "", image_path: str = "",
                  resolution: str = "fixed") -> bool:
        """以 web 通道执行「关闭」动作（等价禅道界面 bug-close-{id} 表单提交）。

        resolution 为禅道关闭表单的必填项，默认 fixed；旧实现不提交该字段，
        会出现「状态已关闭但解决方案为空」的脏数据。
        image_path 非空时先经 file-ajaxPasteImg 通道把截图粘入备注（与界面粘贴行为一致）。
        """

        def build(kuid):
            c = comment
            if image_path:
                c = c + f"<p>{self.paste_image_to_editor(kuid, image_path)}</p>"
            return {"uid": kuid, "status": "closed", "resolution": resolution, "comment": c}

        url = f"{self.base_web}/bug-close-{bug_id}.html"
        return self._web_form_action(url, url, build)

    def activate_bug(self, bug_id: int, assigned_to_account: str, comment: str = "",
                     opened_build: str = "trunk", image_path: str = "") -> bool:
        """以 web 通道执行「激活」动作（等价禅道界面 bug-activate-{id} 表单提交）。

        字段对齐真实抓包：{uid, status='active', assignedTo, openedBuild[]='trunk', comment}。
        image_path 非空时先经 file-ajaxPasteImg 通道把截图粘入备注（与界面粘贴行为一致）。
        """

        def build(kuid):
            c = comment
            if image_path:
                c = c + f"<p>{self.paste_image_to_editor(kuid, image_path)}</p>"
            return {
                "uid": kuid,
                "status": "active",
                "assignedTo": assigned_to_account,
                "openedBuild[]": opened_build,
                "comment": c,
            }

        url = f"{self.base_web}/bug-activate-{bug_id}.html?onlybody=yes"
        return self._web_form_action(url, url, build)

    def paste_image_to_editor(self, kuid: str, image_path: str) -> str:
        """把本地图片经 file-ajaxPasteImg 通道粘入编辑器，返回可直接嵌入备注/正文的 <img> HTML。

        与禅道界面「编辑器粘贴图片」行为一致（base64 内嵌 → 服务端落盘 → 返回 file-read 地址）。
        实测服务端偶发落盘为空（file-read 0 字节），故落盘后核验、最多重试 2 次，
        仍失败则回退 REST 上传通道（upload_image），保证返回的标签一定可用。

        ⚠ 加墙钟闸门：旧实现是 _web_login(3 请求) + 3×(POST+GET 整图) + 外层页面与提交，
        最坏可到 ~130s，远超 MCP 客户端 60s 上限；且核验时把整张图下载回来只为量大小。
        """
        dl = _Deadline(20)
        self._web_login()
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        html = f'<img src="data:{mime};base64,{b64}" alt="" />'
        for _ in range(2):
            if dl.expired:
                break
            r = self.s.post(
                f"{self.base_web}/file-ajaxPasteImg-{kuid}.html?onlybody=yes",
                data={"editor": html},
                timeout=min(self.timeout, dl.remaining()),
            )
            m = re.search(r'<img[^>]+src="([^"]+)"[^>]*>', r.text)
            if not m:
                continue
            src = m.group(1)
            chk_url = src if src.startswith("http") else f"{self.base_web}{src}"
            try:
                # 只取前 1KB 判断"落盘非空"，不再把整张图下载回来
                chk = self.s.get(chk_url, timeout=min(self.timeout, dl.remaining()), stream=True)
                first = next(chk.iter_content(1024), b"") if chk.status_code == 200 else b""
                chk.close()
            except Exception:
                continue
            if first:
                return m.group(0)
        img = self.upload_image(image_path)
        url = str(img.get("url") or "")
        if not url:
            raise RuntimeError("粘贴通道连续落盘为空，且回退 REST 上传失败")
        return f'<img src="{url}" alt="" />'

    def comment_bug(self, bug_id: int, comment: str, image_path: str = "") -> bool:
        """以 web 通道给 BUG 追加备注（等价禅道界面「添加备注」，不改动状态/指派）。

        端点对齐详情页真实表单 action：action-comment-bug-{id}.html。
        image_path 非空时先经 file-ajaxPasteImg 通道把截图粘入备注（与界面粘贴行为一致）。
        """

        def build(kuid):
            c = comment
            if image_path:
                c = c + f"<p>{self.paste_image_to_editor(kuid, image_path)}</p>"
            return {"comment": c, "uid": kuid}

        return self._web_form_action(
            f"{self.base_web}/action-comment-bug-{bug_id}.html",
            f"{self.base_web}/bug-activate-{bug_id}.html?onlybody=yes",
            build,
        )

    def attach_image_to_bug(self, bug_id: int, image_path: str, caption: str = "问题截图") -> Dict[str, Any]:
        """上传图片并内嵌至 BUG 正文 steps。

        ⚠ steps 是「整字段覆盖」，天然是 read-modify-write（禅道 17.1 的 PUT 无版本校验）。
        这里把顺序刻意排成 [上传 → 重读 → 覆盖]，让"重读"与"覆盖"之间的窗口最小化；
        上传是最慢的一步，所以放在读之前。多图请【串行】调用（并发会互相覆盖）。
        若需要在写之前再确认，可先 get_bug 比对 steps 是否仍与预期一致。
        """
        img_info = self.upload_image(image_path)
        img_url = img_info.get("url")
        img_id = img_info.get("id")

        bug = self.get_bug(bug_id)
        current_steps = bug.get("steps") or ""

        img_tag = f'<p><img onload="setImageSize(this,0)" src="{img_url}" alt="{escape(caption)}" /></p>'

        if "[期望]" in current_steps:
            parts = current_steps.split("[期望]", 1)
            new_steps = f"{parts[0]}{img_tag}<p>[期望]{parts[1]}"
        else:
            new_steps = f"{current_steps}\n{img_tag}"

        self.update_bug(bug_id, {"steps": new_steps})
        return {"imageId": img_id, "imageUrl": img_url, "bugId": bug_id}

    def upload_attach(self, bug_id, file_path, caption="", object_bind=True):
        """上传普通附件到 BUG：web 通道 file-ajaxUpload。

        ⚠ 契约：**任何失败都必须返回三元组** (False, msg, None)，绝不抛异常。
        调用方是 `ok, msg, _detail = ...`（无 try 包裹），抛出去就变成 traceback，
        而不是可读的 `[error] ...`。响应体也可能不是对象（JSON 数组）→ 需 isinstance 守卫。
        """
        if not os.path.isfile(file_path):
            return False, f"文件不存在: {file_path}", None
        fname = os.path.basename(file_path)
        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        try:
            self._web_login()
            uid = self._get_edit_uid(bug_id)
            with open(file_path, "rb") as f:
                data = {"uid": uid}
                if object_bind:
                    data.update({"objectType": "bug", "objectID": str(bug_id)})
                r = self.s.post(
                    f"{self.base_web}/file-ajaxUpload.html?uid={uid}",
                    data=data,
                    files={"files[]": (fname, f, mime)},
                    timeout=self.timeout,
                )
        except Exception as e:
            return False, f"附件上传通道异常: {type(e).__name__}: {e}", None
        try:
            resp = r.json()
        except ValueError:
            return False, f"上传响应非JSON（HTTP {r.status_code}）", None
        if not isinstance(resp, dict):
            return False, f"上传响应结构异常（期望对象，实为 {type(resp).__name__}）", None
        if resp.get("result") != "success":
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "?"
            msg = resp.get("message") or "未知错误"
            hint = (
                f"服务器文件白名单可能未包含扩展名 .{ext}，"
                "或服务端拦截了脚本上传（config/file.php 的 $config->file->allowed）"
            )
            return False, f"{msg}。{hint}", None
        detail = resp.get("extra") or resp.get("extras") or resp
        file_id = None
        if isinstance(detail, dict):
            file_id = detail.get("id") or detail.get("fileID")
        return True, f"附件已上传（fileID={file_id or '未知'}）", detail

    def _web_login(self):
        if self._web_logged:
            return
        s = self.s
        s.headers.update({
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_web}/user-login.html",
            "Origin": self.base_web,
        })
        s.get(f"{self.base_web}/user-login.html", timeout=self.timeout)
        rand = s.get(f"{self.base_web}/user-refreshRandom.html",
                     timeout=self.timeout).text.strip()
        if not rand:
            raise RuntimeError("web 登录失败：user-refreshRandom 返回空 rand")
        pwd_enc = hashlib.md5(
            (hashlib.md5(self.password.encode()).hexdigest() + rand).encode()
        ).hexdigest()
        r = s.post(
            f"{self.base_web}/user-login.html",
            data={
                "account": self.account,
                "password": pwd_enc,
                "passwordStrength": "1",
                "referer": "/zentao/",
                "verifyRand": rand,
                "keepLogin": "1",
                "captcha": "",
            },
            allow_redirects=False,
            timeout=self.timeout,
        )
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"web 登录失败（HTTP {r.status_code}，响应非JSON）")
        if data.get("result") != "success":
            raise RuntimeError(f"web 登录失败: {data.get('message')}")
        self._web_logged = True

    def _get_edit_uid(self, bug_id):
        r = self.s.get(f"{self.base_web}/bug-edit-{bug_id}.html", timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"获取 BUG#{bug_id} 编辑页失败 HTTP {r.status_code}")
        m = re.search(r"var kuid\s*=\s*'([0-9a-f]+)'", r.text)
        if not m:
            # 最常见的原因是 web 会话已失效（页面被 200 重定向到登录页），而不是"页面结构变了"。
            # 这里置位 `_web_logged = False`，让下一次调用重新登录（原实现一旦登录成功就终身复用，
            # 会话过期后既不能自愈、又给出误导性的"页面结构异常"）。
            self._web_logged = False
            raise RuntimeError(
                f"BUG#{bug_id} 编辑页未解析到 kuid：多半是 web 会话已失效"
                f"（已置位待重登，可直接重试一次）；若重试仍如此才是页面结构变更"
            )
        return m.group(1)


def _get_client() -> ZentaoClient:
    with _STATE_LOCK:
        if _STATE["client"]:
            return _STATE["client"]
        url = os.environ.get("ZENTAO_URL", "")
        account = os.environ.get("ZENTAO_ACCOUNT", "")
        password = os.environ.get("ZENTAO_PASSWORD", "")
        if not (url and account and password):
            raise RuntimeError(
                "禅道未连接。请先调用 zentao_connect(url, account, password) 或在环境配置 "
                "ZENTAO_URL / ZENTAO_ACCOUNT / ZENTAO_PASSWORD"
            )
        c = ZentaoClient(url, account, password)
        c.login()
        _STATE["client"] = c
        return c


def _resolve_type(type_name: str) -> str:
    """把中文/英文类型名解析为禅道自定义的英文 key。

    空值 → 默认 codeerror；**非空但未收录 → 抛错**，不再静默降级为 codeerror。
    旧实现一律回落 `TYPE_DEFAULT`，于是传了错别字（如「性能优化」）时工具照样返回 `[ok]`，
    用户以为已按指定类型建单，实际被记成了「代码错误」。
    """
    if not type_name:
        return TYPE_DEFAULT
    t = type_name.strip()
    hit = TYPE_MAP.get(t) or TYPE_MAP.get(t.lower())
    if not hit:
        raise ValueError(
            f"未知的 BUG 类型『{type_name}』。可选：{'/'.join(k for k in TYPE_MAP if not k.isascii())}"
            f"（或英文 key，如 codeerror / performance）"
        )
    return hit


def _norm_severity(v) -> int:
    name_map = {"致命": 1, "严重": 2, "一般": 3, "轻微": 4}
    if isinstance(v, str):
        v = v.strip()
        if v in name_map:
            return name_map[v]
        if v.isdigit():
            v = int(v)
    if isinstance(v, (int, float)) and int(v) in (1, 2, 3, 4):
        return int(v)
    raise ValueError(f"严重程度/优先级需为 1-4 或 {list(name_map)}，收到: {v!r}")


# 只把「看起来真的是标签」的片段当标签：`<` 后必须跟字母（或 `</` 字母）。
# 旧写法 `<[^>]*>` 会把正文里的裸 `<` 也当标签，例如「数量<5 时点 保存 > 提交」整段
# 会被判为标签并原样保留，其中的裸 `>` 就永远规范不成 `→`（静默漏改）。
_HTML_TAG_SPLIT_RE = re.compile(r"(</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>)")
_HTML_TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z0-9]+)")
# <pre>/<code> 内是代码原文，一律不许改写（否则 => 会变成 =→）
_VERBATIM_TAGS = {"pre", "code"}

# 兜底排版时必须整体保留的块级结构：块内逐行包 <p> 会把代码/表格拆散（见 _steps_plain_lines_to_p）
_HTML_BLOCK_TAG_RE = re.compile(r"<(/?)(pre|table|ul|ol|blockquote)\b", re.I)
# 整行本身已是 HTML 标记（如 `<h3>x</h3>`、`<p>y</p>`）：再包一层 <p> 只会造出 <p><p>… 嵌套
_HTML_LINE_START_RE = re.compile(r"\s*</?[a-zA-Z][a-zA-Z0-9]*")


def _sanitize_path_arrows(text: str) -> str:
    """把菜单/操作路径里的裸 '>' 规范为 '→'（规范要求：严禁裸 '>'）。

    ⚠ 三条硬约束（均由实测缺陷反推）：
    1. **绝不能动 HTML 标签边界**。旧实现无条件 `text.replace("> ", " → ")`，会把
       标签之间的空格也当箭头：`</b> <code>` → `</b> → <code>`，正文结构直接被改写
       （已实测复现，属定时炸弹）。
    2. **<pre>/<code> 内是代码原文**，一律逐字保留（`x => String(t)` 不能被改成 `x =→ String(t)`）。
    3. **=> / -> 是运算符**，不是路径分隔符，先占位保护再还原。
    4. **只认真正的标签**：`<` 后必须跟字母（或 `</` 字母）。裸 `<`（如「数量<5 时点 保存 > 提交」）
       不会被当成标签，否则其中的裸 `>` 会被静默漏改。
    做法：按标签切分，只在标签【之外】、且不在 pre/code 内的文本片段上替换。
    """
    if not text:
        return ""

    def _fix(seg: str) -> str:
        seg = re.sub(r"(?<=[=\-])>", "\x00", seg)          # 约束 3：保护 => / ->
        seg = (seg.replace(" > ", " → ")
                  .replace(" >", " → ")
                  .replace("> ", " → "))
        return seg.replace("\x00", ">")

    if "<" not in text:
        return _fix(text)

    parts = _HTML_TAG_SPLIT_RE.split(text)
    verbatim_depth = 0
    for i, seg in enumerate(parts):
        if i % 2 == 1:                                      # 奇数下标 = 标签本身
            m = _HTML_TAG_NAME_RE.match(seg)
            if m and m.group(1).lower() in _VERBATIM_TAGS:
                verbatim_depth += -1 if seg.startswith("</") else 1
                verbatim_depth = max(verbatim_depth, 0)
            continue
        if verbatim_depth == 0:                             # 约束 1+2
            parts[i] = _fix(seg)
    return "".join(parts)


def _steps_plain_lines_to_p(text: str) -> str:
    """兜底排版：逐行包 `<p>`，但**块级结构（pre/table/ul/ol/blockquote）内原样保留**。

    ⚠ 旧实现无条件逐行包 `<p>`（`for line in text.splitlines(): parts.append(f"<p>{line}</p>")`），
    多行 `<pre>` 代码/报文会被拆成一段段 `<p>`、`<table>` 同理被打散；拆散后禅道的 HTML
    过滤还会连带吃掉半行（实测 #65916 第一次提单：三个代码块碎成 40+ 个 `<p>`，并丢了
    `this.gesture.on("doubletap", Q =>` 前缀）。规范 §4 有对应约束与自检项。

    规则：行内出现块级开/闭标签、或该行位于已打开的块内、或整行本身就以标签开头 → 整行原样输出
    （保留缩进与空行、行间以 `\n` 连接）；其余纯文本行仍按老行为包 `<p>`。
    深度按标签净增减维护，避免少一个闭合标签就"永久吞行"。
    """
    chunks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            chunks.append("\n".join(buf))
            buf.clear()

    depth = 0
    for line in text.splitlines():
        if not line.strip():
            if depth > 0:            # 代码块内的空行有语义，保留
                buf.append("")
            continue
        hit = _HTML_BLOCK_TAG_RE.findall(line)
        if depth > 0 or hit or _HTML_LINE_START_RE.match(line):
            buf.append(line.rstrip())
        else:
            flush()
            chunks.append(f"<p>{line.strip()}</p>")
        for is_close, _tag in hit:
            depth += -1 if is_close else 1
        depth = max(depth, 0)
    flush()
    return "".join(chunks)


def _steps_html(
    steps: str,
    expected: str = "",
    actual: str = "",
    interface_url: str = "",
    request_sample: str = "",
    image_url: str = "",
    image_caption: str = "问题截图",
) -> str:
    parts = []
    if interface_url or request_sample:
        parts.append("<p>[接口信息]</p>")
        if interface_url:
            # URL 未转义直接拼进单引号属性：含 `'` 即断开属性、可注入标记。必须先 escape。
            clean_url = interface_url.strip()
            safe_url = escape(clean_url, quote=True)
            parts.append(f"<p>接口地址：POST <a href='{safe_url}' target='_blank'>{escape(clean_url)}</a></p>")
        if request_sample:
            parts.append("<p>请求参数示例：</p>")
            parts.append(f"<pre>{escape(request_sample.strip())}</pre>")

    clean_steps = re.sub(r'<p>\s*\[(?:步骤|结果|期望)\]\s*</p>', '', _sanitize_path_arrows(steps))
    clean_steps = re.sub(r'^\s*\[(?:步骤|结果|期望)\]\s*$\n?', '', clean_steps, flags=re.MULTILINE).strip()
    if clean_steps:
        is_structured = bool("<" in clean_steps or "【" in clean_steps)
        if is_structured:
            parts.append(clean_steps)
        else:
            parts.append("<p>[步骤]</p>")
            parts.append(_steps_plain_lines_to_p(clean_steps))
    clean_actual = _sanitize_path_arrows(actual)
    if clean_actual:
        parts.append("<p>[结果]</p>")
        parts.append(_steps_plain_lines_to_p(clean_actual))

    if image_url:
        parts.append(f'<p><img onload="setImageSize(this,0)" src="{image_url}" alt="{escape(image_caption)}" /></p>')

    clean_expected = _sanitize_path_arrows(expected)
    if clean_expected:
        parts.append("<p>[期望]</p>")
        parts.append(_steps_plain_lines_to_p(clean_expected))

    return "".join(parts)


def _md_cell(v) -> str:
    """Markdown 表格单元格转义。

    ⚠ 字面 `|` 会**截断整行**并让后续所有列错位（标题里带 `|` 就会毁掉整张表）。
    旧实现只对 `comment_brief` 做了 `.replace('|','/')`，title / changes / 各人员字段都漏了。
    """
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _clean_html_for_md(html_text: str) -> str:
    """将禅道 HTML 转换为易读的 Markdown 文本（保留图片与分段）。

    ⚠ 必须做实体反转义：`_steps_html` 会用 `escape()` 把报文写成 `&lt; &gt; &amp; &quot;`，
    只剥标签不反转义的话，回归报告/正文回显里就会看到字面的 `&lt;`、`&nbsp;`。
    ⚠ 解析不出 src 的 `<img>` 不能静默删除（信息会凭空消失），改为留下可见占位符。
    """
    if not html_text:
        return ""
    text = html_text
    dropped = []

    # 替换图片
    def replace_img(match):
        raw = match.group(0)
        src_match = re.search(r'src=["\']([^"\']+)["\']', raw)
        if src_match:
            return f"\n![截图]({src_match.group(1)})\n"
        dropped.append(raw)
        return f"\n[图片:无法解析 src，原文 {escape(raw[:120], quote=False)}]\n"

    text = re.sub(r'<img[^>]*>', replace_img, text)
    # 替换常用标签
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<p[^>]*>', '', text)
    text = re.sub(r'<pre[^>]*>', '\n```json\n', text)
    text = re.sub(r'</pre>', '\n```\n', text)
    text = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text)
    # 去除其它 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 实体反转义（&nbsp; &lt; &gt; &amp; &quot; …）
    text = unescape(text)
    # 合并多余换行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---- 第五节：按缺陷类型生成靶向回归矩阵（避免所有 BUG 套用同一套场景） ----

# 分类关键词表：按序匹配、先命中者生效（专指类别在前，泛化类别在后）
_MATRIX_KEYWORD_RULES = [
    ("export", ("导出", "下载")),
    ("perf", ("卡死", "卡顿", "很卡", "好卡", "会卡", "加载慢", "性能", "响应慢", "超时")),
    ("selection", ("勾选", "复选框", "多选", "全选", "选中态", "选中状态", "行键")),
    ("calc", ("数量", "计算", "汇总", "统计", "金额", "不一致", "对不上", "差异")),
    ("display", ("显示", "展示", "字眼", "文案", "字段", "编码", "布局", "没出来", "留白", "命名", "改成", "改为", "改一下")),
    ("validation", ("只能", "需要控制", "校验", "限制", "不允许", "不可选", "必填", "过滤")),
    ("search", ("搜索", "查询", "筛选", "检索", "操作符", "不等于")),
    ("interaction", ("按钮", "点击", "跳转", "弹框", "弹窗", "关联单号", "无法", "操作", "提交")),
]

_MATRIX_CATEGORY_LABEL = {
    "export": "导出/文件生成",
    "perf": "性能/卡顿",
    "selection": "勾选/选择态一致性",
    "calc": "数据计算与多视图一致性",
    "display": "显示、字段配置与文案一致性",
    "validation": "校验/范围控制",
    "search": "查询/筛选/操作符语义",
    "interaction": "按钮交互与跳转链路",
    "generic": "通用基线",
}

# 决议类结局（未实际修复代码，回归重点转为口径确认）
_MATRIX_DECISION_RESOLUTIONS = ("willnotfix", "bydesign", "postponed", "external")

# 各分类的靶向场景矩阵：(场景, 重点验证的技术边界与断言, 预期结果)
_MATRIX_SCENARIOS = {
    "export": [
        ("基线导出复测", "按原始场景执行导出（含自定义列、排序等触发条件）", "导出文件成功生成，无异常提示，内容与页面数据一致"),
        ("排序一致性", "对列表做自定义排序后再次导出", "导出记录顺序与列表显示顺序完全一致"),
        ("记录数一致性", "对比导出条数与列表总数/当前筛选结果数", "二者完全一致，无缺行、无多行"),
        ("筛选条件透传", "携带查询条件执行导出", "导出内容仅包含筛选结果集，条件未被忽略"),
        ("特殊字符与大数据量", "含特殊字符字段、大数据量分页后导出", "无乱码/截断/超时，文件可正常打开"),
    ],
    "perf": [
        ("基线性能复测", "按原始卡顿路径操作（如字段输入搜索、列表加载）", "响应流畅无卡死，交互反馈延迟在可接受范围"),
        ("数据量梯度", "分别在数据量大、小两种条件下重复上述操作", "耗时随数据量合理变化，无异常放大"),
        ("连续高频操作", "快速连续输入、清空、切换条件重复触发", "页面无假死、无未响应弹窗、无请求堆积"),
        ("交叉回归", "同页其它字段与操作（分页、排序等）抽测", "未引入新的卡顿点"),
    ],
    "selection": [
        ("基线勾选复测", "首次进入页面，按原始步骤执行勾选/选择操作", "控件视觉打勾/高亮与内部选中集完全一致，无逻辑错位"),
        ("同键多行边界", "针对行键可能重复的数据（如多批次/多行同料品）连续勾选多行", "各行勾选独立响应，无 toggle 反转，选中集包含全部勾选行"),
        ("数据变动与清空残留", "切换查询条件、翻页或重载列表", "旧数据选中态被彻底清空，无残留"),
        ("提交数据闭环", "完成选择并提交保存，重新进入详情/编辑核对", "后端数据与选中记录一致，业务链路跑通"),
        ("快速连续操作", "快速交替勾选/取消勾选不同记录", "渲染与内部状态无延迟脱节，无未捕获异常"),
    ],
    "calc": [
        ("基线数值复测", "按原始步骤复现相关单据/列表，核对涉及字段", "各数量/金额/状态数值与业务规则一致（以提单期望为基线）"),
        ("变更后重算", "上游数据变更（新增/修改/取消/作废）后刷新核对", "相关字段实时正确重算，取消/作废数据不计入"),
        ("边界值", "数量为 0、全部取消、跨页汇总等边界场景", "无异常值（0/空/负数）与逻辑错误"),
        ("跨视图一致性", "列表、详情、关联单据间核对同一字段", "各处数值一致，无口径差异"),
        ("持久化验证", "保存后重新打开/重新进入核对", "计算结果持久化正确，刷新不漂移"),
    ],
    "display": [
        ("基线显示复测", "进入原页面核对涉及字段/文案/布局", "字段正常显示、文案与需求口径一致，无空白或错位"),
        ("空值处理", "数据为空或未维护时核对显示", "展示为统一空态（如 '-'），无异常字符"),
        ("一致性核对", "同类字段在列表/详情/查询区的命名与格式比对", "无两套命名或格式差异"),
        ("关联页面抽查", "对涉及改动的文案/字段在所有出现处逐一核对", "无遗漏的旧文案、旧字段"),
        ("刷新稳定性", "刷新/重新进入页面", "显示稳定，无闪烁或加载后数据不完整"),
    ],
    "validation": [
        ("基线校验复测", "按原始场景触发目标字段/控件（含下拉数据源过滤）", "仅允许规则范围内的取值，越界或禁用项不可选/给出提示"),
        ("边界值", "允许范围的边界值、临界日期逐一尝试", "边界可正常选取，后续流转不受影响"),
        ("非法输入", "越界/非法值直接输入或粘贴", "被拦截并给出明确提示，不产生脏数据"),
        ("联动校验", "关联字段/上下文变化时复核校验规则", "规则随上下文正确生效"),
        ("反向回归", "原有合法操作路径重复走查", "未因新增校验被误拦截"),
    ],
    "search": [
        ("基线查询复测", "按原始步骤设置条件并执行查询", "结果集合完全符合条件语义，无漏返回、无多返回"),
        ("请求报文核验", "抓取实际请求参数（Network/接口日志）", "条件与操作符被正确序列化传参，后端按语义匹配"),
        ("反向条件对照", "用相邻/相反条件（如『等于』对照『不等于』）验证", "两类条件结果互补，未返回同一结果集"),
        ("组合与边界条件", "多字段组合、空条件、特殊字符查询", "条件正确叠加，清空条件恢复全量"),
        ("分页排序下过滤", "带条件翻页、排序", "每页记录均满足条件语义，总数一致"),
    ],
    "interaction": [
        ("基线交互复测", "按原始步骤执行目标操作（点击/跳转/弹框等）", "行为与预期一致，目标正确（跳转对象/弹框内容正确）"),
        ("按钮/入口矩阵", "在不同数据状态、选中与否、不同权限下核对入口可用性", "按状态与权限正确启用/禁用/隐藏"),
        ("关联链路核对", "从关联单号/链接双向进入核对", "双向跳转一致，返回后页面状态保留"),
        ("异常分支", "无权限、数据已变更等异常条件下操作", "有明确提示，不产生误操作"),
        ("快速重复操作", "连续快速点击同一操作", "无重复提交、无重复弹框"),
    ],
    "generic": [
        ("基线复现路径", "按第三节原始步骤完整重走", "原现象不再出现"),
        ("反向与边界抽查", "相邻的边界与反向场景", "行为一致，无连带异常"),
        ("关联场景", "该功能相关的上下游操作", "无新增问题"),
        ("数据持久化", "操作后重新进入/重新登录核对", "结果持久化正确"),
        ("快速重复操作", "快速重复触发目标操作", "无状态脱节或未捕获异常"),
    ],
}


def _classify_defect(text: str) -> str:
    """按关键词先命中原则对缺陷文本分类；无命中返回 generic。"""
    for category, keywords in _MATRIX_KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            return category
    return "generic"


def _build_regression_matrix(bug_id, title: str, resolved_by: str, activated_count: int, dev_note_text: str = "", resolution: str = "") -> list:
    """第五节：按缺陷类型生成靶向回归用例矩阵；标题未命中时结合开发修复说明再判。"""
    category = _classify_defect(title or "")
    if category == "generic" and dev_note_text:
        category = _classify_defect(dev_note_text)
    label = _MATRIX_CATEGORY_LABEL.get(category, _MATRIX_CATEGORY_LABEL["generic"])
    rows = list(_MATRIX_SCENARIOS.get(category, _MATRIX_SCENARIOS["generic"]))
    decision = (resolution or "").strip().lower()
    if decision in _MATRIX_DECISION_RESOLUTIONS:
        rows.insert(0, (
            "决议口径确认",
            f"与开发/产品确认「{decision}」决议与需求口径是否一致（本单未实际修复代码）",
            "形成书面结论：接受决议→关闭；不接受→重开并附需求依据",
        ))
    if activated_count and activated_count > 0:
        rows.append((
            "历史重开点专项复测",
            f"本单曾激活重开 {activated_count} 次，逐一重走第二节列出的争议场景",
            "所有历史重开场景均不再复现",
        ))

    md = []
    md.append("## 五、端到端（E2E）回归测验实施方案")
    md.append("")
    md.append(f"针对本单缺陷类型（{label}），结合开发团队披露的修复进展，设计如下靶向回归用例矩阵（以第三节原始复现步骤为基线）：")
    md.append("")
    md.append("### 1. 靶向回归测试用例矩阵")
    md.append("| 序号 | 回归测试场景 | 重点验证的技术边界与断言 | 预期结果 | 测试结果 |")
    md.append("|---|---|---|---|---|")
    for idx, (scene, check, expect) in enumerate(rows, 1):
        md.append(f"| {idx} | **{scene}** | {check} | {expect} | `[ ]` |")
    md.append("")
    md.append("### 2. 回归结论判定与闭环指引")
    if decision in _MATRIX_DECISION_RESOLUTIONS:
        md.append(f"- [ ] **接受决议（Close Bug）**：确认「{decision}」决议与需求口径一致后，可在禅道中执行 **关闭（Close）** 单据。")
        md.append(f"- [ ] **有异议（Re-activate Bug）**：如认为应修复/应协商排期，使用 `assign_bug({bug_id}, '{resolved_by}')` 并附需求依据与截图重新激活。")
    else:
        md.append(f"- [ ] **通过（Close Bug）**：上述 {len(rows)} 项回归用例全部通过，验证环境无回归性缺陷，可在禅道中执行 **关闭（Close）** 单据。")
        md.append(f"- [ ] **不通过（Re-activate Bug）**：任何一项验证不通过（原现象复现或出现连带异常），使用 `assign_bug({bug_id}, '{resolved_by}')` 并附带最新复现日志与截图重新激活。")
    return md


def _build_regression_report(bug: dict) -> str:
    """根据 BUG 核心数据与流转历史构建端到端回归测验指南 Markdown 文档。"""
    bug_id = bug.get('id')
    title = bug.get('title')
    status = bug.get('status')
    resolution = bug.get('resolution') or '待解决'
    severity = bug.get('severity')
    pri = bug.get('pri')
    product = bug.get('product')
    execution = bug.get('execution')
    module = bug.get('module')

    opened_by = bug.get('openedBy', {}).get('realname') if isinstance(bug.get('openedBy'), dict) else bug.get('openedBy')
    opened_date = bug.get('openedDate', '')
    assigned_to = bug.get('assignedTo', {}).get('realname') if isinstance(bug.get('assignedTo'), dict) else bug.get('assignedTo')
    resolved_by = bug.get('resolvedBy', {}).get('realname') if isinstance(bug.get('resolvedBy'), dict) else bug.get('resolvedBy')
    resolved_date = bug.get('resolvedDate', '')
    resolved_build = bug.get('resolvedBuild') or '主干'
    activated_count = bug.get('activatedCount', 0)
    steps_raw = bug.get('steps') or ''

    # ⚠ `bug.get('actions', [])` 在 "actions": null 时返回 None，下面的 for 直接 TypeError。
    actions = bug.get('actions') or []
    if not isinstance(actions, list):
        actions = []

    dev_notes = []
    activation_notes = []
    timeline = []

    for act in actions:
        actor = act.get('actor')
        act_type = act.get('action')
        act_date = act.get('date')
        comment = (act.get('comment') or '').strip()
        histories = act.get('history') or act.get('histories') or []

        hist_desc = []
        for h in histories:
            hist_desc.append(f"{h.get('fieldName', h.get('field'))}: {h.get('old')} → {h.get('new')}")

        timeline.append({
            "date": act_date,
            "actor": actor,
            "action": act_type,
            "comment": comment,
            "changes": ", ".join(hist_desc)
        })

        if act_type in ('commented', 'resolved') and comment:
            dev_notes.append({
                "date": act_date,
                "actor": actor,
                "comment": comment
            })
        elif act_type == 'activated':
            activation_notes.append({
                "date": act_date,
                "actor": actor,
                "comment": comment
            })

    latest_dev_note = dev_notes[-1] if dev_notes else None

    # 开始组织 Markdown
    status_cn = "【已解决·待回归验证】" if status == 'resolved' else f"【当前状态: {status}】"
    md = []
    md.append(f"# BUG #{bug_id} 缺陷修复进展汇报与端到端（E2E）回归测验指南")
    md.append("")
    md.append(f"> **状态看板**：`{status_cn}` | **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    md.append(f"> **禅道详情**：http://192.168.200.53/zentao/bug-view-{bug_id}.html")
    md.append("")

    md.append("## 一、缺陷基本档案与归属")
    md.append("")
    md.append("| 字段 | 详情 | 字段 | 详情 |")
    md.append("|---|---|---|---|")
    md.append(f"| **BUG ID** | #{bug_id} | **当前状态** | `{status}` ({'已解决' if status == 'resolved' else status}) |")
    md.append(f"| **缺陷标题** | {_md_cell(title)} | **解决方案** | `{_md_cell(resolution)}` |")
    md.append(f"| **产品/执行** | 产品ID {product} / 执行ID {execution} | **所属模块** | 模块ID {module} |")
    md.append(f"| **严重程度** | P{severity} | **优先级** | Pri {pri} |")
    md.append(f"| **提单人员** | {_md_cell(opened_by)} ({_md_cell(opened_date)}) | **当前指派** | **{_md_cell(assigned_to)}** |")
    md.append(f"| **解决人员** | **{_md_cell(resolved_by) or '尚未解决'}** | **解决时间** | {_md_cell(resolved_date) or '待定'} |")
    md.append(f"| **解决版本** | {_md_cell(resolved_build)} | **激活/重开次数** | **{_md_cell(activated_count)} 次** |")
    md.append("")

    md.append("## 二、开发团队最新修复进展与根因剖析")
    md.append("")
    if latest_dev_note:
        md.append(f"**最新修复责任人**：**{_md_cell(latest_dev_note['actor'])}**（提交时间：`{latest_dev_note['date']}`）")
        md.append("")
        md.append("```text")
        # 开发备注是富文本 HTML，必须与 §三 一样先清洗，否则会看到裸 <p>/<img> 源码
        md.append(_clean_html_for_md(latest_dev_note['comment']))
        md.append("```")
    elif not actions:
        md.append("*未取到流转历史（接口返回的 `actions` 为空）—— 因此**无法判断**开发是否记录了修复文本。*")
        md.append("*本报告不对「未记录修复文本」下结论，请先用 get_bug 核对接口返回。*")
    else:
        md.append("*已取到流转历史，但其中没有 `commented` / `resolved` 的备注文本，单据直接标记为已解决。*")
    md.append("")

    if len(dev_notes) > 1:
        md.append("### 历史多轮修复记录溯源")
        md.append("")
        older = dev_notes[:-1]
        for idx, dn in enumerate(older[-20:], 1):
            clean_cmt = _clean_html_for_md(dn['comment']).replace('\n', ' ')
            md.append(f"- **第 {idx} 次修复处理**（{_md_cell(dn['actor'])} @ {dn['date']}）：")
            md.append(f"  > {clean_cmt[:240]}")
        if len(older) > 20:
            md.append(f"- *（更早的 {len(older) - 20} 轮修复记录已省略）*")
        md.append("")

    if activation_notes:
        md.append("### 历史激活/重开争议焦点（测试人员重点避坑）")
        md.append("")
        md.append("> ⚠️ **警示**：此 BUG 曾经历过激活重开，说明初始修复方案存在边界遗漏或未彻底覆盖全部交互路径，回归测试务必重测以下引发重开的场景：")
        md.append("")
        for an in activation_notes:
            clean_an = _clean_html_for_md(an['comment'])
            md.append(f"- **{an['date']} 由 {an['actor']} 激活重开**：\n  {clean_an}")
        md.append("")

    md.append("## 三、原始缺陷复现现象（基线参考）")
    md.append("")
    md.append("```text")
    md.append(_clean_html_for_md(steps_raw))
    md.append("```")
    md.append("")

    md.append("## 四、全生命周期流转事件时间线")
    md.append("")
    md.append("| 时间 | 操作人 | 动作 | 核心字段变更 | 备注要点 |")
    md.append("|---|---|---|---|---|")
    # 长寿命 BUG 的 actions 可达数百条，全量渲染会把单次响应撑到数百 KB —— 只列最近 N 条
    MAX_TIMELINE_ROWS = 50
    shown_tl = timeline[-MAX_TIMELINE_ROWS:]
    if len(timeline) > MAX_TIMELINE_ROWS:
        md.append(f"| — | — | — | *（共 {len(timeline)} 条事件，仅列最近 {MAX_TIMELINE_ROWS} 条）* | — |")
    for tl in shown_tl:
        comment_brief = _clean_html_for_md(tl['comment'] or '').replace('\n', ' ')[:60]
        md.append(f"| {_md_cell(tl['date'])} | {_md_cell(tl['actor'])} | `{_md_cell(tl['action'])}` "
                  f"| {_md_cell(tl['changes']) or '-'} | {_md_cell(comment_brief) or '-'} |")
    md.append("")

    try:
        activated_num = int(activated_count or 0)
    except (TypeError, ValueError):
        activated_num = 0        # 接口可能给 "2 次" 这类非数字，不能让 int() 在建完整份报告后炸掉
    md.extend(
        _build_regression_matrix(
            bug_id=bug_id,
            title=title or "",
            resolved_by=resolved_by or "开发",
            activated_count=activated_num,
            dev_note_text=(latest_dev_note["comment"] if latest_dev_note else ""),
            resolution=resolution or "",
        )
    )

    return "\n".join(md)


# ============================ MCP 工具定义 ============================

@_tool
def zentao_connect(url: str, account: str, password: str) -> str:
    """连接禅道系统并登录换取 Token。

    Args:
        url: 禅道地址，如 http://192.168.200.53/zentao
        account: 登录账号
        password: 登录密码
    """
    with _STATE_LOCK:       # 与 _get_client 同一把锁：避免"替换单例"与"读取单例"并发
        c = ZentaoClient(url, account, password)
        c.login()
        _STATE["client"] = c
    prof = c.whoami()
    return f"连接成功: {url}（当前身份: {prof.get('realname')} / {account}，REST Token 已就绪）"


@_tool
def switch_account(account: str, password: str, url: str = "") -> str:
    """切换当前登录的禅道操作账户（可随时切换不同研发或测试人员身份）。

    Args:
        account: 新账号用户名（如 'lihaizhen', 'wangpeng', 'hujiabin'）
        password: 新账号密码
        url: 禅道地址（若留空则沿用当前连接的服务器地址）
    """
    current = _STATE.get("client")
    target_url = url or (current.base_web if current else "http://192.168.200.53/zentao")
    with _STATE_LOCK:
        c = ZentaoClient(target_url, account, password)
        c.login()
        _STATE["client"] = c
    prof = c.whoami()
    return f"账号切换成功！当前操作者: {prof.get('realname')}（账号: {account}，部门ID: {prof.get('dept', 0)}）"


@_tool
def whoami() -> str:
    """查询当前 MCP 客户端连接的服务器与登录人员身份。"""
    c = _get_client()
    prof = c.whoami()
    return (
        f"当前连接禅道: {c.base_web}\n"
        f"登录账号: {c.account}\n"
        f"真实姓名: {prof.get('realname')}\n"
        f"人员角色: {prof.get('role')}\n"
        f"所属部门: {prof.get('dept')}\n"
        f"Token 状态: {'已缓存有效' if c.token else '未获取'}"
    )


@_tool
def assign_bug(bug_id: int, assigned_to: str, comment: str = "") -> str:
    """将 BUG 指派给指定研发或测试人员（支持输入真实姓名或账号，支持流转备注）。

    Args:
        bug_id: BUG ID（如 65295）
        assigned_to: 指派目标（中文名如 '万棚', '赵浩源', '段广', '胡嘉斌' 或账号 'wangpeng'）
        comment: 指派流转备注（可选，写入历史记录）
    """
    c = _get_client()
    account = c.find_user(assigned_to, bug_id=bug_id)
    if not account:
        return f"[error] 未找到匹配人员『{assigned_to}』，请先 find_user 确认姓名"
    c.assign_bug(bug_id, account, comment)
    return f"[ok] BUG#{bug_id} 已成功指派给: {assigned_to}（账号: {account}）" + (f"，附带备注: {comment}" if comment else "")

@_tool
def close_bug(bug_id: int, comment: str = "", image_path: str = "",
              resolution: str = "fixed") -> str:
    """关闭指定 BUG（回归验证通过后执行，等价禅道界面「关闭 → 保存」）。

    Args:
        bug_id: BUG ID（如 65295）
        comment: 关闭备注（建议写回归结论与证据位置）
        image_path: 可选，本地截图路径（粘贴进备注，与界面编辑器粘贴一致）
        resolution: 解决方案（默认 fixed；禅道关闭表单必填项，留空会产生
            "状态已关闭但解决方案为空"的脏数据）
    """
    c = _get_client()
    ok = c.close_bug(bug_id, comment, image_path, resolution)
    bug = c.get_bug(bug_id)
    if bug.get("status") != "closed":
        # 旧实现丢弃 close_bug 的返回值、只看状态，且状态没变也照样报 [ok]
        return (f"[warn] BUG#{bug_id} 关闭动作未生效"
                f"（web 通道返回 {'成功' if ok else '失败'}，当前状态仍是 {bug.get('status')}）；"
                f"请在界面复核，或用 comment_bug 记录结论")
    closed_by = bug.get("closedBy")
    if isinstance(closed_by, dict):
        closed_by = closed_by.get("realname")
    res = bug.get("resolution")
    warn = "" if res else "；⚠ 解决方案为空，建议在界面补填"
    return (f"[ok] BUG#{bug_id} 已关闭（closedBy={closed_by or c.account}，"
            f"closedDate={bug.get('closedDate')}，resolution={res or '空'}{warn}）")


@_tool
def activate_bug(bug_id: int, assigned_to: str, comment: str = "", image_path: str = "") -> str:
    """重新激活指定 BUG（回归不通过时使用；等价禅道界面「激活 → 保存」）。

    Args:
        bug_id: BUG ID（如 65352）
        assigned_to: 指派回谁（中文名如 '李科勇' 或账号）
        comment: 激活备注（建议写复现结论与证据位置）
        image_path: 可选，本地截图路径（粘贴进备注，与界面编辑器粘贴一致）
    """
    c = _get_client()
    account = c.find_user(assigned_to, bug_id=bug_id)
    if not account:
        return f"[error] 未找到匹配人员『{assigned_to}』，请先 find_user 确认姓名"
    ok = c.activate_bug(bug_id, account, comment, image_path=image_path)
    bug = c.get_bug(bug_id)
    at = bug.get("assignedTo")
    at = at.get("realname") if isinstance(at, dict) else at
    if bug.get("status") != "active":
        return (f"[warn] BUG#{bug_id} 激活动作未生效"
                f"（web 通道返回 {'成功' if ok else '失败'}，当前状态仍是 {bug.get('status')}）；请在界面复核")
    return f"[ok] BUG#{bug_id} 已重新激活（status=active，激活次数={bug.get('activatedCount')}，指派给={at}）"


@_tool
def comment_bug(bug_id: int, comment: str, image_path: str = "") -> str:
    """给指定 BUG 追加备注（等价禅道界面「添加备注」，不改动状态/指派）。

    Args:
        bug_id: BUG ID（如 65352）
        comment: 备注内容
        image_path: 可选，本地截图路径（粘贴进备注，与界面编辑器粘贴一致）
    """
    c = _get_client()
    # 记录提交前的最后动作：否则失败时返回串会引用"上一次"动作，看起来像成功
    before = (c.get_bug(bug_id).get("actions") or [])
    before_last = before[-1].get("date") if before else None
    ok = c.comment_bug(bug_id, comment, image_path)
    bug = c.get_bug(bug_id)
    acts = bug.get("actions") or []
    last = acts[-1] if acts else {}
    if not ok:
        return (f"[error] BUG#{bug_id} 备注提交未成功（web 通道判定失败，可能会话失效或表单被拒）；"
                f"当前最后动作仍是 {last.get('action')} by {last.get('actor')}")
    if before_last and last.get("date") == before_last:
        return (f"[warn] BUG#{bug_id} 备注提交返回成功，但未观察到新的流转动作"
                f"（最后动作仍为 {last.get('action')} @ {last.get('date')}）；请到界面确认备注是否落库")
    return (f"[ok] BUG#{bug_id} 备注已添加（最后动作: {last.get('action')} by "
            f"{last.get('actor')}，时间 {last.get('date')}）")


@_tool
def list_products(keyword: str = "") -> str:
    """列出禅道产品列表（BUG 必须挂载到产品下，生和堂APS对应产品ID 40 / SCM）。

    Args:
        keyword: 产品名称关键字（空则列全部）
    """
    c = _get_client()
    r = c._req("GET", "/products", params={"limit": 500})
    items = c._json(r)
    if isinstance(items, dict):
        items = items.get("products", [])
    kw = keyword.strip().lower()
    lines = [f"{p.get('id')}\t{p.get('name')}\t{p.get('status')}" for p in items
             if not kw or kw in str(p.get("name", "")).lower()]
    # 注意：原写法 `f"..." + "\n".join(lines) or "无匹配产品"` 中 `+` 优先级高于 `or`，
    # 左侧恒为真 → 兜底分支是死代码，无匹配时返回"产品 0 个…"。
    if not lines:
        return f"无匹配产品（关键字『{keyword}』，共取到 {len(items)} 个产品）"
    return f"产品 {len(lines)} 个（id/名称/状态）:\n" + "\n".join(lines)


@_tool
def search_execution(keyword: str = "生和堂") -> str:
    """按关键字搜索项目/执行（生和堂APS默认对应执行ID 578）。

    Args:
        keyword: 执行名称关键字，默认 生和堂
    """
    c = _get_client()
    r = c._req("GET", "/executions", params={"limit": 500})
    items = c._json(r)
    if isinstance(items, dict):
        items = items.get("executions", [])
    kw = keyword.strip().lower()
    hits = [e for e in items if kw in str(e.get("name", "")).lower()]
    if not hits:
        return f"未找到匹配『{keyword}』的执行"
    return "\n".join(f"{e['id']}\t{e.get('name')}\t状态:{e.get('status')}" for e in hits)


@_tool
def list_modules(product: str = DEFAULT_PRODUCT) -> str:
    """列出产品下的 BUG 模块（默认产品 40 / SCM）。

    ⚠️ 提单规范铁律：产品 40 (SCM) 下并无业务子模块映射，建单/改单强制留空 module 参数（挂根模块 0 /）。
    严禁调用本工具去寻找或挑选任何业务子模块（如绝对严禁选择“公共板块”等通用名称）！本工具仅供元数据巡检。

    Args:
        product: 产品 ID 或名称，默认 40
    """
    c = _get_client()
    pid = c.find_product(product)
    r = c._req("GET", "/modules", params={"id": pid, "type": "bug"})
    if r.status_code != 200:
        return f"产品 {pid} 无模块数据（HTTP {r.status_code}）"
    items = c._json(r)
    if isinstance(items, dict):
        items = items.get("modules", [])
    if not items:
        return f"产品 {product}(ID {pid}) 无 BUG 模块，create_bug 可不传 module（挂根模块 0）"
    return f"产品 {product}(ID {pid}) 的 BUG 模块 {len(items)} 个:\n" + \
        "\n".join(f"{m['id']}\t{m.get('name')}" for m in items)


@_tool
def list_stories(product: str = DEFAULT_PRODUCT, keyword: str = "", limit: int = 20) -> str:
    """列出/检索产品下的需求（story），用于核对 create_bug(story=...) 的取值。

    Args:
        product: 产品 ID 或名称，默认 40
        keyword: 需求标题关键字（空则列出最新需求）
        limit: 最多返回条数（默认 20）
    """
    c = _get_client()
    pid = c.find_product(product)
    limit = max(1, int(limit or 20))          # limit<=0 会让循环在第一页就 break，静默漏结果
    kw = keyword.strip().lower()
    dl = _Deadline()
    hits: List[Dict[str, Any]] = []
    scanned, total, page = 0, 0, 1
    truncated = False
    while page <= STORY_SCAN_MAX_PAGES:
        if dl.expired:
            truncated = True
            break
        r = c._req("GET", f"/products/{pid}/stories",
                   params={"limit": STORY_PAGE_SIZE, "page": page},
                   _timeout=min(c.timeout, dl.remaining()))
        if r.status_code != 200:
            truncated = True
            break
        data = c._json(r)
        items = data.get("stories", []) if isinstance(data, dict) else data
        if not isinstance(items, list) or not items:
            break
        scanned += len(items)
        if isinstance(data, dict):
            total = int(data.get("total", 0) or 0) or total
        hits.extend(s for s in items
                    if not kw or kw in str(s.get("title", "")).lower())
        if len(hits) >= limit:                # 够数即停：计数语义见下方"≥"
            break
        if total and page * STORY_PAGE_SIZE >= total:
            break
        page += 1
    scope = f"已扫描 {scanned}/{total or scanned}" + ("，⚠ 已达扫描闸门" if truncated else "")
    if not hits:
        return f"产品 {product} 下无匹配『{keyword}』的需求（{scope}）"
    return (f"命中 ≥{min(len(hits), limit)} 条（{scope}）:\n"
            + "\n".join(f"#{s.get('id')}\t{s.get('title')}\t状态{s.get('status')}" for s in hits[:limit]))


@_tool
def find_story(product: str = DEFAULT_PRODUCT, ref: str = "", bug_id: int = 0) -> str:
    """把「需求 ID」或「需求标题关键字」解析为具体需求（提交前核对 story 取值）。

    Args:
        product: 产品 ID 或名称，默认 40
        ref: 需求 ID（'18058'、'#18058'）或标题关键字（'审批单列表'）
        bug_id: 可选；ref 为空且提供 bug_id 时，返回该 BUG 当前已关联的需求
    """
    c = _get_client()
    if not ref and bug_id:
        b = c.get_bug(bug_id)
        if b.get("story"):
            return (f"BUG#{bug_id} 当前关联需求: #{b.get('story')} {b.get('storyTitle', '')}"
                    f"（需求状态 {b.get('storyStatus', '')}）")
        return f"BUG#{bug_id} 当前未关联任何需求"
    pid = c.find_product(product)
    sid, stitle = c.find_story(pid, ref)
    if not sid:
        return f"未在产品 {product} 下找到匹配『{ref}』的需求，可用 list_stories 核对"
    return f"需求匹配成功: #{sid} {stitle}（可直接 create_bug(story='{sid}') 或 update_bug(story='{sid}')）"


@_tool
def link_story_to_bug(bug_id: int, story: str, product: str = DEFAULT_PRODUCT) -> str:
    """把已存在的 BUG 关联到指定需求（补关联 / 改绑）。

    Args:
        bug_id: BUG ID
        story: 需求 ID（'18058'）或标题关键字（'审批单列表'）
        product: 产品 ID 或名称（按标题解析需求时使用），默认 40
    """
    c = _get_client()
    b = c.get_bug(bug_id)
    pid = str(b.get("product") or c.find_product(product))
    sid, stitle = c.find_story(pid, story)
    if not sid:
        return f"[跳过] 未找到匹配需求『{story}』，请用 find_story 核对"
    r = c.link_story(bug_id, sid)
    return (f"[ok] BUG#{bug_id} 已关联需求 #{sid} {stitle}"
            f"（当前 story={r.get('story')} {r.get('storyTitle', '')}）")


@_tool
def find_user(keyword: str = "", bug_id: int = 0) -> str:
    """按姓名或账号搜索禅道用户（如 万棚/赵浩源/段广/胡嘉斌），返回账号与真实姓名。

    当 REST /users 无权限时自动改用 bug-activate 页兜底获取全员列表。

    Args:
        keyword: 真实姓名或账号关键字
        bug_id: 可选，兜底页面所用的 BUG ID（默认自动取最近访问/最新一条）
    """
    c = _get_client()
    users = c.list_users(keyword, bug_id=bug_id)
    if not users:
        hint = ""
        if not c._users_cache:
            hint = ("（注意：用户列表当前为空 —— REST /users 无权限且 web 兜底也未取到；"
                    "此时按中文姓名指派会失败，请改用账号）")
        return f"未找到匹配『{keyword}』的用户{hint}"
    shown = users[:30]
    lines = [f"账号: {u.get('account')}\t姓名: {u.get('realname')}\t角色: {u.get('role')}\t部门ID: {u.get('dept')}"
             for u in shown]
    # 旧实现把标题写成 len(lines)：截断后"匹配用户 30 人"被当成总数，真实命中数被隐藏
    more = f"（仅显示前 {len(shown)} 人）" if len(users) > len(shown) else ""
    return f"匹配用户 {len(users)} 人{more}:\n" + "\n".join(lines)


@_tool
def search_bugs(product: str = DEFAULT_PRODUCT, keyword: str = "",
                status: str = "", limit: int = 20) -> str:
    """搜索产品下已有 BUG（按标题关键字/状态过滤，**覆盖全量而非仅首页**）。

    重要：禅道 17.1 的 `GET /products/{id}/bugs` 会**忽略 `title=` 过滤参数**，
    且默认只返回首页。老实现只取首页（≤100 条），导致明明存在的 BUG 搜不到
    （实测产品 SCM 下 BUG 总数 462，历史「审批单列表」相关缺陷全部漏检，
    去重判断因此失效）。现改为分页拉全量后本地过滤。

    Args:
        product: 产品 ID 或名称，默认 40
        keyword: 标题关键字（空则不过滤）
        status: 状态过滤，如 active / resolved / closed（空则不过滤）
        limit: 最多返回条数（默认 20）
    """
    c = _get_client()
    pid = c.find_product(product)
    bugs, total, truncated = c.all_bugs(pid)
    if not bugs:
        return f"产品 {product}({pid}) 下未取到 BUG 数据，请检查连接与权限"
    kw = keyword.strip().lower()
    st = status.strip().lower()
    hits = [b for b in bugs
            if (not kw or kw in str(b.get("title", "")).lower())
            and (not st or str(b.get("status") or "").lower() == st)]
    scope = f"全量 {len(bugs)}/{total or len(bugs)} 条"
    if truncated:
        scope += "，⚠ 已达扫描闸门（结果可能不完整）"
    if not hits:
        cond = f"『{keyword}』" if keyword else ""
        cond += f" 状态={status}" if status else ""
        tip = "；状态取值需为英文 key（active/resolved/closed），中文状态名不参与匹配" if status else ""
        return f"产品 {product} 下无匹配 {cond} 的 BUG（已扫描 {scope}）{tip}"
    return (f"命中 {len(hits)} 条（已扫描 {scope}）:\n" + "\n".join(
        f"#{b['id']}\t{b.get('title')}\t严重{b.get('severity')}\t优先级{b.get('pri')}"
        f"\t指派{(b.get('assignedTo') or {}).get('realname', '') if isinstance(b.get('assignedTo'), dict) else (b.get('assignedTo') or '')}"
        f"\t状态{b.get('status')}"
        for b in hits[:limit]))


@_tool
def list_resolved_bugs(product: str = DEFAULT_PRODUCT, limit: int = 20) -> str:
    """列出当前产品下所有【已解决】（待测试团队回归验证）的 BUG 清单。

    注意：与 search_bugs 同理，老实现只取首页会漏掉大量已解决单据；
    现改为分页拉全量后过滤，并按解决时间倒序（最新修复的排在最前）。

    Args:
        product: 产品 ID 或名称，默认 40
        limit: 最大返回条数
    """
    c = _get_client()
    pid = c.find_product(product)
    bugs, total, truncated = c.all_bugs(pid)
    resolved = [b for b in bugs if b.get("status") == "resolved"]
    tail = "，⚠ 已达扫描闸门（结果可能不完整）" if truncated else ""
    if not resolved:
        return f"产品 {product} 下暂无已解决待回归的 BUG（已扫描全量 {len(bugs)}/{total or len(bugs)} 条{tail}）"
    resolved.sort(key=lambda b: str(b.get("resolvedDate") or ""), reverse=True)
    lines = []
    for b in resolved[:limit]:
        res_by = b.get('resolvedBy', {}).get('realname') if isinstance(b.get('resolvedBy'), dict) else b.get('resolvedBy')
        ass_to = b.get('assignedTo', {}).get('realname') if isinstance(b.get('assignedTo'), dict) else b.get('assignedTo')
        lines.append(f"#{b['id']}\t{b.get('title')}\t解决人:{res_by}\t指派给:{ass_to}\t解决时间:{b.get('resolvedDate')}")
    return (f"已解决待回归 BUG {len(lines)} 条（全量共 {len(resolved)} 条，已扫描 {len(bugs)}/{total or len(bugs)}{tail}）:\n"
            + "\n".join(lines))


@_tool
def export_bug_regression_report(bug_id: int, output_file: str = "") -> str:
    """深入分析指定 BUG 详情中开发团队描述的修复进展、根因与代码提交，重构成端到端（E2E）回归测验指南 Markdown。

    Args:
        bug_id: BUG ID（如 65295）
        output_file: 本地保存路径（可选，如 'docs/regression_BUG65295.md'；若不提供则直接返回 Markdown 正文）
    """
    c = _get_client()
    bug = c.get_bug(bug_id)
    report_md = _build_regression_report(bug)

    if output_file:
        out_path = Path(output_file)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report_md, encoding="utf-8")
        except OSError as e:
            # 旧实现未兜底：路径是目录 / 无权限时直接把 OSError 抛给客户端
            return f"[error] 报告已生成但写入失败（{output_file}）: {type(e).__name__}: {e}"

    # 无论哪种模式都限幅：报告本身可能是数百 KB（含全部流转历史），不能整段塞进上下文
    head = report_md[:REPORT_RETURN_MAX]
    tail_note = ("" if len(report_md) <= REPORT_RETURN_MAX
                 else f"\n\n...(正文共 {len(report_md)} 字符，此处仅回显前 {REPORT_RETURN_MAX} 字符；"
                      f"完整报告请传 output_file 落盘)")
    if output_file:
        return f"[ok] 回归测试指南已生成并保存至: {output_file}{tail_note}\n\n" + head
    return head + tail_note


@_tool
def upload_image(image_path: str) -> str:
    """上传本地截图到禅道（REST API POST /files，专用 imgFile 字段），返回可内嵌到富文本正文中的图片地址。

    Args:
        image_path: 本地截图绝对路径（如 D:/screenshots/bug1.png）
    """
    c = _get_client()
    try:
        res = c.upload_image(image_path)
        return f"[ok] 图片上传成功: id={res.get('id')}, url={res.get('url')}"
    except Exception as e:
        return f"[error] 图片上传失败: {e}"


@_tool
def attach_image_to_bug(bug_id: int, image_path: str, caption: str = "问题截图") -> str:
    """为已有 BUG 内嵌截图（标准图文机制：上传图片并把 <img src='...'> 写入重现步骤 HTML 正文）。

    ⚠️ 提单规范约束：
    1. 多张图片必须【逐张串行调用】，严禁并发（并发会导致正文互相覆盖丢图）；
    2. 每张图都必须传入明确的 caption 说明（说明该图片证明的具体交互状态或缺陷证据）；
    3. 本项目统一约定所有图文证据均内嵌写入 steps 正文，不使用附件通道。

    Args:
        bug_id: BUG ID（如 65485）
        image_path: 本地截图绝对路径
        caption: 截图描述文案（必填明确说明，如「步骤3执行结果：确认返回后编辑页签依然残留」）
    """
    c = _get_client()
    try:
        res = c.attach_image_to_bug(bug_id, image_path, caption)
        return f"[ok] BUG#{bug_id} 已内嵌截图: id={res['imageId']}, url={res['imageUrl']}"
    except Exception as e:
        return f"[error] BUG#{bug_id} 内嵌截图失败: {e}"


@_tool
def create_bug(
    title: str,
    steps: str = "",
    product: str = DEFAULT_PRODUCT,
    execution: str = DEFAULT_EXECUTION,
    severity: int = 3,
    pri: int = 3,
    type_name: str = "代码错误",
    expected: str = "",
    actual: str = "",
    module: str = "",
    build: str = "trunk",
    keywords: str = "",
    assigned_to: str = "",
    story: str = "",
    image_path: str = "",
    interface_url: str = "",
    request_sample: str = "",
) -> str:
    """在禅道创建一条 BUG（遵循 skill://zentao-bug-ops/ 权威规范，支持图文正文内嵌、根因级排版、人员中文直派、需求关联）。

    执行原则【最高优先】：永不确认直接提交（建单、传图、指派、关联需求一律直接执行，严禁询问确认；目标是提单快）；提交后必须回读自检。

    Args:
        title: BUG 标题（必须严格遵循格式：【模块】验证点：现象短语 或 菜单路径→页面：现象，影响；路径分隔符必须用 '→'，严禁使用裸 '>'）
        steps: 重现步骤（正文强制 Markdown 结构化 / 根因级格式，严禁【】+编号纯文本墙；应包含【环境说明】、【一句话结论】、【重现步骤】、【实测表现与对照矩阵】table、【现场实捕 DOM 状态证据】pre、【期望表现】、【关键排除】、【根因链路推导】pre、【业务影响】、【修复建议】table、【回归断言点】ol 等；含代码块时建议首部显式标注 <p>[步骤]</p>）
        product: 产品 ID 或名称（默认 40 / SCM）
        execution: 关联执行 ID 或名称（默认 578 / 生和堂APS）
        severity: 严重程度 1致命/2严重/3一般/4轻微，默认 3（按影响面自评，不要问用户）
        pri: 优先级 1-4，默认 3（1阻断/2~3一般功能/3文案显示，按紧急度自评，不要问用户）
        type_name: BUG类型中文名：代码错误/用户体验/界面优化/设计缺陷/性能问题/操作问题/其他问题，默认代码错误
        expected: 预期结果（追加到步骤末尾，与正文协同表达期望行为）
        actual: 实际结果（追加到步骤末尾，与正文协同表达缺陷现象）
        module: 模块名称或 ID（【强制铁律】强制写死留空，默认挂根模块 0；严禁调用 list_modules 自选子模块如“公共板块”等）
        build: 影响版本（默认 trunk 主干；**该字段为禅道必填**，置空会导致建单失败）
        keywords: 关键词（必须遵循规范：<用例编号>,<要点1>,<要点2>，便于双向追溯）
        assigned_to: 指派人姓名或账号（支持中文姓名如 '万棚', '赵浩源', '段广', '胡嘉斌', '李科勇'）
        story: 关联需求，支持需求 ID（'18058'）或标题关键字（'审批单列表'），建单即关联
        image_path: 本地截图文件路径（可选，首图将自动上传并内嵌至 steps 正文中）
        interface_url: 接口地址（接口类问题必带，自动格式化 [接口信息]）
        request_sample: 接口请求示例报文 JSON/文本（自动以 <pre> 包裹排版）
    """
    c = _get_client()
    pid = c.find_product(product)
    notes: List[str] = []
    module_id = 0
    if module:
        module_id = c.find_module(pid, module) or 0
        if not module_id:
            # 原实现直接终止建单；实测产品 SCM 下并无「审批流管理」等模块，
            # 直接终止会让整条缺陷无法提交，故降级为根模块并明确提示。
            notes.append(f"模块『{module}』在产品 {product} 下不存在，已降级挂根模块(0)")

    image_url = ""
    if image_path:
        try:
            up_res = c.upload_image(image_path)
            image_url = up_res.get("url", "")
        except Exception as e:
            return f"[error] 上传截图失败导致终止建单: {e}"

    steps_html = _steps_html(
        steps=steps,
        expected=expected,
        actual=actual,
        interface_url=interface_url,
        request_sample=request_sample,
        image_url=image_url,
    )

    payload = {
        "title": _sanitize_path_arrows(title),
        "product": int(pid) if str(pid).isdigit() else pid,
        "module": module_id,
        "openedBuild": [build] if isinstance(build, str) else build,
        "severity": _norm_severity(severity),
        "pri": _norm_severity(pri),
        "type": _resolve_type(type_name),
        "steps": steps_html,
        "keywords": keywords,
    }

    if execution:
        eid = c.find_execution(execution)
        if eid:
            payload["execution"] = eid

    if assigned_to:
        account = c.find_user(assigned_to)
        if account:
            payload["assignedTo"] = account

    try:
        resp = c.create_bug(pid, payload)
    except Exception as e:
        # 建单是整条链路里最"重"的一步：必须明确告知"结果未知"，否则调用方可能重复提单
        return (f"[error] 建单失败: {type(e).__name__}: {e}\n"
                f"（⚠ 建单结果未知：若错误为超时/5xx，记录可能已落库。请先用 "
                f"search_bugs(keyword='{(title or '')[:20]}') 核对，确认未建单再重提，避免重复建单）")
    bug_id = resp.get("id")

    if assigned_to and bug_id and not resp.get("assignedTo"):
        try:
            c.update_bug(bug_id, {"assignedTo": c.find_user(assigned_to)})
        except Exception as e:
            # 旧实现是 `except Exception: pass`，但返回串照样打印「指派 X」——静默错误。
            notes.append(f"⚠ 指派『{assigned_to}』失败，该单可能仍是默认指派人: {e}")

    # 关联需求：REST 建单接口对 story 支持不稳定，故建单后统一用 PUT 补关联（17.1 实测可靠）
    if story and bug_id:
        # ⚠ 需求解析必须包住：`find_story` 现在会对"接口故障/超时"抛错，
        # 而此处**单子已经建好了** —— 抛出去就会变成"建了单却没返回 ID"，
        # 调用方无法知道该单已存在，极易重复提单。失败降级为提示，不影响建单结果。
        sid, stitle, story_err = None, "", ""
        try:
            sid, stitle = c.find_story(pid, story)
        except Exception as e:
            story_err = f"{type(e).__name__}: {e}"
            notes.append(f"⚠ 需求解析失败（{story}）: {story_err}；该单未关联需求，"
                         f"可稍后用 link_story_to_bug 补关联")
        if sid:
            try:
                c.link_story(bug_id, sid)
                notes.append(f"已关联需求 #{sid} {stitle}")
            except Exception as e:
                notes.append(f"需求关联失败({story}): {e}")
        elif not story_err:
            notes.append(f"未找到匹配需求『{story}』，可通过 find_story 或 list_stories 核对")

    tail = f"；{'；'.join(notes)}" if notes else ""
    return (f"[ok] BUG#{bug_id} 已创建: {title}"
            f"（产品{pid} 执行{payload.get('execution')} "
            f"版本{','.join(payload['openedBuild']) if isinstance(payload['openedBuild'], list) else payload['openedBuild']} "
            f"指派{payload.get('assignedTo', '未指派')}）{tail}")


@_tool
def get_bug(bug_id: int, full_steps: bool = False) -> str:
    """查询单条 BUG 的核心信息（含步骤正文、严重程度、优先级、类型、指派人、状态、附件等）。

    ⚠ 步骤正文默认**截断到 4000 字符**：steps 是整段富文本，实测单条可到 6KB+，
    全量回吐会把上下文挤满（而多数场景只需要"落位是否正确 + 有多少张图"）。
    需要完整正文时传 `full_steps=True`，或用 scripts/zentao_bug_body_check.py 导出体检。

    Args:
        bug_id: BUG ID（如 65485）
        full_steps: 是否返回完整步骤正文（默认 False，截断到 4000 字符）
    """
    c = _get_client()
    b = c.get_bug(bug_id)
    assigned = b.get("assignedTo")
    assigned_name = assigned.get("realname", "") if isinstance(assigned, dict) else str(assigned or "")
    steps = b.get("steps") or ""
    n_img = len(re.findall(r"<img", steps))
    if full_steps or len(steps) <= STEP_DISPLAY_MAX:
        steps_out = steps
    else:
        steps_out = (steps[:STEP_DISPLAY_MAX]
                     + f"\n...(已截断，共 {len(steps)} 字符；"
                       f"完整正文请传 full_steps=True 或跑 scripts/zentao_bug_body_check.py)")
    lines = [
        f"BUG#{b.get('id')}: {b.get('title')}",
        f"产品: {b.get('product')}\t执行: {b.get('execution')}\t模块: {b.get('module')}",
        f"严重程度: {b.get('severity')}\t优先级: {b.get('pri')}\t类型: {b.get('type')}",
        f"状态: {b.get('status')}\t指派给: {assigned_name}\t影响版本: {b.get('openedBuild')}",
        f"关键词: {b.get('keywords')}",
        f"正文概况: {len(steps)} 字符 / 内嵌图片 {n_img} 张 / 附件 {len(b.get('files') or [])} 条",
        f"步骤内容:\n{steps_out}",
    ]
    files = b.get("files") or []
    if files:
        lines.append("附件列表: " + ", ".join(f"{f.get('title')}(id:{f.get('id')})" for f in files))
    return "\n".join(lines)


@_tool
def update_bug(
    bug_id: int,
    title: str = "",
    severity: int = 0,
    pri: int = 0,
    type_name: str = "",
    steps: str = "",
    expected: str = "",
    actual: str = "",
    module: str = "",
    keywords: str = "",
    assigned_to: str = "",
    story: str = "",
    image_path: str = "",
) -> str:
    """更新已存在 BUG 的字段（仅修改传入的字段；severity/pri 传 0 表示不修改）。

    ⚠️ 提单规范约束：
    1. steps 字段是「整字段全量覆盖」语义，重写或补写正文时必须完整保留原有正文中的 <img src=...> 标签，否则原有截图会被移除；
    2. module 字段严禁随意指定业务子模块，仍须保持根模块 0；
    3. 标题与正文同样需符合 skill://zentao-bug-ops/ 的根因级结构与菜单路径规范。

    Args:
        bug_id: BUG ID
        title: 新标题（遵循【模块】验证点：现象短语 或 菜单路径→页面：现象，影响，路径分隔符必须用 '→'）
        severity: 新严重程度 1-4（0=不修改，按影响面自评）
        pri: 新优先级 1-4（0=不修改，按紧急度自评）
        type_name: 新 BUG 类型中文名（代码错误/用户体验等，空则不修改）
        steps: 新重现步骤（全空则不修改；全量覆盖语义，需带上已有 <img> 标签与根因级排版）
        expected: 新预期结果
        actual: 新实际结果
        module: 新模块名称或 ID（留空保持根模块 0，严禁自选子模块）
        keywords: 新关键词（必须遵循规范：<用例编号>,<要点1>,<要点2>）
        assigned_to: 新指派人员（支持中文真实姓名如 '万棚', '赵浩源', '李科勇'）
        story: 关联/改绑需求，支持需求 ID（'18058'）或标题关键字（'审批单列表'）
        image_path: 追加新截图（若传入将上传并插进正文 steps 中）
    """
    c = _get_client()
    payload = {}

    # 单次读取当前单据，供 module / story / image_path 三处复用（旧实现最多重复读 3 次）
    cur: Optional[Dict[str, Any]] = None

    def _current() -> Dict[str, Any]:
        nonlocal cur
        if cur is None:
            cur = c.get_bug(bug_id)
        return cur

    if title:
        payload["title"] = _sanitize_path_arrows(title)
    if severity:
        payload["severity"] = _norm_severity(severity)
    if pri:
        payload["pri"] = _norm_severity(pri)
    if type_name:
        payload["type"] = _resolve_type(type_name)
    if steps or expected or actual:
        payload["steps"] = _steps_html(steps, expected, actual)
    if module:
        pid = c.find_product(str(_current().get("product")))
        mid = c.find_module(pid, module)
        if not mid:
            return f"[跳过] 模块『{module}』不存在，请先 list_modules 核对"
        payload["module"] = mid
    if keywords:
        payload["keywords"] = keywords
    if assigned_to:
        payload["assignedTo"] = c.find_user(assigned_to, bug_id=bug_id)

    if story:
        pid = str(_current().get("product") or DEFAULT_PRODUCT)
        sid, stitle = c.find_story(pid, story)
        if not sid:
            return f"[跳过] 未找到匹配需求『{story}』，请用 find_story 核对后重试"
        payload["story"] = sid

    image_url = ""
    if image_path:
        # 旧实现分两步写：先 attach_image_to_bug()（内含一次整覆盖 steps，图片已写进去），
        # 随后又用 payload["steps"] 整覆盖一次 → 图片被**静默丢弃**。
        # 现在改为：先把图片标签并入最终的 steps，全程只 PUT 一次。
        try:
            image_url = c.upload_image(image_path).get("url", "")
        except Exception as e:
            return f"[error] BUG#{bug_id} 截图上传失败（未改动单据）: {e}"
        base_steps = payload.get("steps")
        if base_steps is None:
            base_steps = _current().get("steps") or ""
        img_tag = (f'<p><img onload="setImageSize(this,0)" src="{image_url}" '
                   f'alt="问题截图" /></p>')
        if "【证据截图】" in base_steps:
            head, tail = base_steps.split("【证据截图】", 1)
            base_steps = f"{head}【证据截图】{img_tag}{tail}"
        elif "[期望]" in base_steps:
            head, tail = base_steps.split("[期望]", 1)
            base_steps = f"{head}{img_tag}<p>[期望]{tail}"
        else:
            base_steps = f"{base_steps}{img_tag}"
        payload["steps"] = base_steps

    if not payload:
        return "[跳过] 未传入任何需修改的字段"

    # ⚠ 全量覆盖护栏：steps 是"整字段覆盖"语义，新版正文若漏抄了旧正文里的图片，
    # 旧图会被【静默删除】（实测踩到过：原报障截图被补写正文冲掉）。这里显式比对
    # 图片 src 集合并告警，把"静默丢图"变成"看得见的提醒"。
    warn = ""
    if "steps" in payload:
        src_re = re.compile(r'src=["\']([^"\']+)["\']')
        old_srcs = set(src_re.findall((_current().get("steps") or "")))
        new_srcs = set(src_re.findall(payload["steps"]))
        dropped = sorted(old_srcs - new_srcs)
        if dropped:
            warn = (f"\n[警告] 本次 steps 覆盖将【移除 {len(dropped)} 张原有图片】："
                    f"{', '.join(dropped)}。若为误删请把对应 <img> 标签补回 steps 后重发；"
                    f"文件仍在服务器上，可用 attach_image_to_bug 重新内嵌。")

    b = c.update_bug(bug_id, payload)
    changed = ", ".join(k for k in payload if k != "steps")
    if "steps" in payload:
        changed = (changed + ", steps(含截图)" if changed else "steps(含截图)")
    return f"[ok] BUG#{bug_id} 已更新: {b.get('title')}（变更字段: {changed}）{warn}"


@_tool
def attach_file(bug_id: int, image_path: str) -> str:
    """为 BUG 上传截图/附件（图片文件自动走 REST API 内嵌至 steps 正文，其它类型走 web 通道）。

    Args:
        bug_id: BUG ID（如 65485）
        image_path: 本地图片/文件绝对路径
    """
    ext = os.path.splitext(image_path)[-1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return attach_image_to_bug(bug_id, image_path)
    ok, msg, _detail = _get_client().upload_attach(bug_id, image_path)
    return f"[{'ok' if ok else 'error'}] BUG#{bug_id} 附件上传: {msg}"


def _flush_record(record_path: Path, record: dict) -> None:
    """把提交记录**逐条**落盘。

    旧实现在整批跑完后才写一次：客户端 60s 超时时整批结果（哪些建了、哪些跳过）
    连同记录文件一起丢失，重跑还可能重复建单。
    """
    try:
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _load_bugs_from_xlsx(xlsx_path: Path):
    """读取「BUG清单」sheet；缺列 / 空表 / 非法取值都抛**可读的 ValueError**，不裸抛 KeyError。

    旧实现直接 `idx["BUG标题"]` / `rows[0]` / `int(r[idx["严重程度"]])`：
    表头被改名 → KeyError；空表 → IndexError；严重程度写「一般」→ ValueError。
    而这些异常发生在工具体之外（调用点未包 try），于是整批以 traceback 收场。
    """
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    if "BUG清单" not in wb.sheetnames:
        raise ValueError(f"xlsx 缺少『BUG清单』sheet（现有: {', '.join(wb.sheetnames)}）")
    rows = list(wb["BUG清单"].iter_rows(values_only=True))
    if not rows:
        raise ValueError("『BUG清单』sheet 为空（连表头都没有）")
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    idx = {h: i for i, h in enumerate(headers)}
    required = ["BUG标题", "所属模块", "严重程度", "优先级", "BUG类型", "重现步骤"]
    missing = [h for h in required if h not in idx]
    if missing:
        raise ValueError(
            f"『BUG清单』缺少必需列: {', '.join(missing)}；"
            f"实际表头: {', '.join(h for h in headers if h)}"
        )

    def cell(r, name):
        i = idx.get(name, -1)
        if i < 0 or i >= len(r):
            return ""
        v = r[i]
        return "" if v is None else str(v).strip()

    bugs = []
    for r in rows[1:]:
        if not cell(r, "BUG标题"):
            continue
        bugs.append({
            "title": cell(r, "BUG标题"),
            "module_name": cell(r, "所属模块"),
            # 严重程度 / 优先级原样交给 _norm_severity：它同时支持 1-4 与 致命/严重/一般/轻微
            "severity": cell(r, "严重程度"),
            "pri": cell(r, "优先级") or 3,
            "type_name": cell(r, "BUG类型"),
            "steps": cell(r, "重现步骤"),
            "expected": cell(r, "预期结果"),
            "actual": cell(r, "实际结果"),
            "keywords": cell(r, "关键词"),
            "assigned_to": cell(r, "指派给"),
        })
    return bugs


@_tool
def submit_bugs_from_xlsx(
    xlsx_path: str,
    product: str = DEFAULT_PRODUCT,
    execution: str = DEFAULT_EXECUTION,
    build: str = "trunk",
    force: bool = False,
) -> str:
    """批量提交「禅道BUG导入清单」xlsx（pytest-api-auto 技能导出清单，sheet 名 BUG清单）。

    幂等：本地提交记录 + 产品下已有 BUG 标题双重去重，重复运行不重复建单。
    整批受墙钟闸门（45s）约束：到点即停并在返回里说明剩余条数；**记录逐条落盘**，
    因此超时/中断后再次调用可直接续跑。

    Args:
        xlsx_path: xlsx 文件绝对路径
        product: 产品 ID 或名称（默认 40 / SCM）
        execution: 关联执行 ID 或名称（默认 578 / 生和堂APS）
        build: 影响版本（默认 trunk 主干）
        force: True 则忽略本地提交记录全部重提（仍保留远端标题去重；**不会删除已有记录**）
    """
    path = Path(xlsx_path)
    if not path.exists():
        return f"[error] 文件不存在: {xlsx_path}"
    try:
        bugs = _load_bugs_from_xlsx(path)
    except Exception as e:
        return f"[error] 解析 xlsx 失败: {type(e).__name__}: {e}"
    if not bugs:
        return "[error] xlsx『BUG清单』无数据（除表头外没有 BUG标题 非空的行）"

    c = _get_client()
    pid = c.find_product(product)
    eid = c.find_execution(execution) if execution else None

    # 提交记录【按 xlsx 命名】并兼容旧文件名：旧实现固定在目录下的 zentao_submitted.json，
    # 同目录放两份清单会互相覆盖（A 单的记录被 B 单冲掉 → A 重跑就重复建单）。
    record_path = path.with_suffix(".submitted.json")
    legacy_path = path.parent / "zentao_submitted.json"
    record: Dict[str, Any] = {}
    for rp in (legacy_path, record_path):
        if rp.exists():
            try:
                loaded = json.loads(rp.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    record.update(loaded)
            except Exception:
                pass

    remote_titles = c.list_bug_titles(pid)

    created, skipped, failed = [], [], []
    module_cache: Dict[str, Any] = {}
    dl = _Deadline()
    hit_gate = False
    for n, b in enumerate(bugs, 1):
        if dl.expired:
            hit_gate = True
            failed.append(f"（本批剩余 {len(bugs) - n + 1} 条未处理：已达 {PAGE_DEADLINE_S}s 闸门；"
                          f"记录已逐条落盘，再次调用本工具即可续跑）")
            break
        title = b["title"]
        # ⚠ 去重键必须与**实际写入禅道的标题**一致：建单时标题会过 _sanitize_path_arrows
        # （" > " → "→"），远端存的是转换后的标题。旧实现拿原始标题去比对，永远不相等 →
        # 每次运行都重复建单。
        key = _sanitize_path_arrows(title)
        if not force and (key in record or title in record):
            prev = record.get(key) or record.get(title) or {}
            skipped.append(f"{title}（本地记录 #{prev.get('bugId')}）")
            continue
        if key in remote_titles:
            record[key] = {"bugId": None, "submittedAt": datetime.now().isoformat(), "remote": True}
            _flush_record(record_path, record)
            skipped.append(f"{title}（禅道已存在同名）")
            continue
        try:
            mn = b["module_name"]
            if mn not in module_cache:
                module_cache[mn] = c.find_module(pid, mn)
            payload = {
                "title": key,
                "product": int(pid) if str(pid).isdigit() else pid,
                "module": module_cache[mn] or 0,
                "openedBuild": [build],
                "severity": _norm_severity(b["severity"]),
                "pri": _norm_severity(b["pri"]),
                "type": _resolve_type(b["type_name"]),
                "steps": _steps_html(b["steps"], b["expected"], b["actual"]),
                "keywords": b["keywords"],
            }
            if eid:
                payload["execution"] = eid
            if b.get("assigned_to"):
                payload["assignedTo"] = c.find_user(b["assigned_to"])
            resp = c.create_bug(pid, payload)
            record[key] = {"bugId": resp.get("id"), "submittedAt": datetime.now().isoformat()}
            _flush_record(record_path, record)
            created.append(f"#{resp.get('id')} {title}")
        except Exception as e:
            # 整行构造都在 try 内：单行坏数据（严重程度=5、类型未收录、网络抖动）
            # 只让该行进 failed，不再中断整批
            failed.append(f"{title}: {type(e).__name__}: {e}")
        time.sleep(0.2)

    _flush_record(record_path, record)
    out = [f"产品: {product}(ID {pid})，版本: {build}" + (f"，关联执行: {execution}(ID {eid})" if eid else ""),
           f"新建 {len(created)} / 跳过 {len(skipped)} / 失败 {len(failed)}，提交记录: {record_path}",
           "⚠ 本批因闸门提前结束，记录已落盘，可再次调用续跑" if hit_gate else "✓ 本批已全部处理"]
    if created:
        out.append("新建: " + "\n  ".join(created))
    if skipped:
        out.append("跳过: " + "\n  ".join(skipped))
    if failed:
        out.append("失败: " + "\n  ".join(failed))
    return "\n".join(out)


if __name__ == "__main__":
    try:
        mcp.run()  # stdio 传输（MCP 客户端标准接入方式）
    except (KeyboardInterrupt, SystemExit):
        pass
