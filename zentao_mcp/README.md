# 禅道 BUG 提交、多账户切换与回归测验分析 MCP Server（FastMCP）

把「禅道 BUG 提交、图文正文内嵌、多账户动态切换、人员精准直派与端到端（E2E）回归测验分析」封装为标准 MCP 工具服务，供 TRAE / Claude Desktop 等 MCP 客户端直接调用。

- **适配版本**：禅道开源版 **17.1**（REST API `/api.php/v1` + 定制字段 + KindEditor 图文内嵌 + 缺陷生命周期流转分析）
- **默认映射**：产品 `40` (SCM)，执行 `578` (生和堂APS)
- **提单执行规范（唯一权威）**：`skills/zentao-bug-ops/references/submission-standard.md`
  （与 `.agents/skills/zentao-bug-ops/references/submission-standard.md` 保持完全一致）
  —— 字段默认值速查、定级口径、标题与正文规范（含根因级正文标准骨架）、图片与需求关联、提交后自检清单、检索陷阱、表单兜底。**提单前必读**。
  ⚠️ 同目录的 `zentao-bug-report-standard.md` 已于 2026-09-11 **废止**。

### 提单核心执行纪律（最高原则）
1. **永不确认直接提交**（用户明确要求）：建单、传图、指派、关联需求一律直接执行，严禁停下来询问确认，提单务必敏捷高效；提交后**必须回读自检**。
2. **所属模块【强制铁律】**：`module` 参数**强制写死留空（挂根模块 0 /）**，严禁调用 `list_modules` 自选子模块（如绝对严禁选择“公共板块”等通用名称）；SCM 产品下无业务子模块映射。
3. **标题规范**：严格遵循 `【模块】验证点：现象短语` 或 `菜单路径→页面：现象，影响`，路径分隔符必须用 `→`，**严禁使用裸 `>`**。
4. **正文规范（强制 Markdown 结构化 / 根因级正文）**：**严禁【】+编号纯文本墙**。标准正文骨架必须包含：
   - `【环境说明】`：环境、页签、数据基线条数
   - `【一句话结论】`：核心现象简练概括
   - `【重现步骤】`：前置路径 + 编号步骤
   - `【实测表现与对照矩阵】`：HTML `<table>` 步骤意图与实际对照表（含 ❌/✅ 判定）
   - `【现场实捕 DOM 状态证据】`：`<pre>` 真实 DOM 切片、组件状态或接口请求报文
   - `【期望表现】`：明确系统正确逻辑与状态
   - `【关键排除】`：明确反向证据与相邻正常项，排除无关原因
   - `【根因链路推导】`：`<pre>` 交互触发 → 逻辑错误 → 状态脱节/死锁链路
   - `【业务影响】`：直接危害与潜在波及面
   - `【修复建议】`：HTML `<table>` 方案矩阵（方案 × 改动位置 × 工作量 × 说明）
   - `【回归断言点】`：`<ol>` 编号的可执行判定断言
   - `【证据截图】`：首图随建单内嵌，后续截图逐张串行追加
5. **定级口径（AI 自评，严禁询问用户）**：
   - `pri`（紧急度）：`1` 阻断不可用（主流程阻塞、死锁），`2~3` 一般功能问题，`3` 纯显示/文案类；
   - `severity`（影响面）：`1` 致命，`2` 严重，`3` 一般，`4` 轻微。
6. **关键词格式**：必须为 `<用例编号>,<要点1>,<要点2>`，以便测试用例与缺陷双向溯源。
7. **多图串行与全量覆盖护栏**：首图通过 `create_bug(image_path=...)` 上传，后续图片通过 `attach_image_to_bug` 逐张串行内嵌（严禁并发）并填写 `caption`；`steps` 为全量覆盖，更新时必须保留已有 `<img>` 标签。
---

## 工具清单

### 1. 账号会话与人员指派
| 工具 | 功能 | 说明 |
|------|------|------|
| `zentao_connect` | 连接禅道并登录换取 Token | 显式连接并缓存会话 |
| `switch_account` | 快速切换操作账户 | 随时切换不同人员身份（研发/测试/管理员） |
| `whoami` | 查询当前登录人员身份 | 显示连接地址、账号、真实姓名、角色、部门与 Token 状态 |
| `find_user` | 查询人员真实姓名与账号 | 支持输入「万棚」「赵浩源」「段广」「胡嘉斌」直接解析 account |
| `assign_bug` | 专门的缺陷指派工具 | 支持中文姓名直派（如 `assign_bug(65295, '胡嘉斌', '请回归')`） |

### 2. 缺陷流转与端到端回归测验（核心特色）
| 工具 | 功能 | 说明 |
|------|------|------|
| `list_resolved_bugs` | 检索已解决待验证 BUG 列表 | 快速筛选产品下所有待回归验证的缺陷清单 |
| `export_bug_regression_report` | 生成缺陷修复进展与 E2E 回归测试指南 Markdown | **深入解析开发团队在 BUG 底部填写的修复进展、根因分析、代码提交及历史激活重开原因，重构成结构化 E2E 回归测验 Markdown 文档** |

### 3. 需求（story）关联
| 工具 | 功能 | 说明 |
|------|------|------|
| `find_story` | 解析需求 | 按需求 ID（`18058` / `#18058`）或标题关键字（`审批单列表`）解析为具体需求；只传 `bug_id` 则返回该单当前关联的需求 |
| `list_stories` | 检索产品需求 | 分页扫描产品需求列表并按标题过滤（SCM 下共 3377 条） |
| `link_story_to_bug` | 补关联 / 改绑需求 | 为已存在 BUG 关联需求，走 `PUT /bugs/{id}` 的 `story` 字段 |

> `create_bug` 与 `update_bug` 均已支持 `story` 参数，建单/改单时可直接带入需求 ID 或标题。

### 4. BUG 创建、更新与图文上传
| 工具 | 功能 | 说明 |
|------|------|------|
| `create_bug` | 创建单条 BUG | 支持**图文正文内嵌**、**人员中文直派**、**接口信息自动排版**、**需求关联**（`story`）；`module` 不存在时降级挂根模块而非终止建单 |
| `get_bug` | 查询单条 BUG 核心详情 | 查看完整 steps HTML、严重度、优先级、指派人等 |
| `update_bug` | 更新已存在 BUG 字段 | 标题/严重度/优先级/类型/正文/指派人/追加截图 |
| `upload_image` | 上传截图到禅道获取图片地址 | 走 REST API `POST /files`（专用 `imgFile` 字段），返回 `{id, url}` |
| `attach_image_to_bug` | 为已有 BUG 内嵌截图 | 上传图片并将 `<img src="...">` 自动追加进 `steps` HTML 正文 |
| `attach_file` | 上传截图或附件 | 图片文件自动走 REST 内嵌正文，其它格式走 web 通道 |
| `submit_bugs_from_xlsx` | 批量提交 BUG 清单 xlsx | 本地记录 + 远端标题双重幂等去重 |

### 5. 基础信息查询
| 工具 | 功能 | 说明 |
|------|------|------|
| `list_products` | 列出产品列表 | BUG 必须挂产品，默认 `40` (SCM) |
| `search_execution` | 搜索项目/执行 | 生和堂APS 默认对应 `578` |
| `list_modules` | 列出产品下的 BUG 模块 | 17.1 走 `/modules?id={产品}&type=bug` |
| `search_bugs` | 搜索产品下已有 BUG | 提交前去重；**分页拉全量后本地过滤**（见「分页陷阱」），支持 `keyword` + `status` |

---

## ⚠️ 17.1 实测踩坑与修复记录（2026-09-11）

> **第二轮（同日，系统审计后）** 共修复 20+ 项，完整清单见下方「### 5. 第二轮系统审计修复清单」。
> 回归测试：`python zentao_mcp/tests/test_zentao_mcp.py`（51 个用例，全部对应一个实测缺陷）。

### 1. 分页陷阱：`search_bugs` 曾大面积漏检（已修复）

`GET /products/{id}/bugs` 会**忽略 `title=` 等过滤参数**（传了 total 也不变），且默认只返回首页。
老实现只取 `limit=min(limit,100)` 的单页数据，而 **SCM 下 BUG 总量已 462 条**，导致：

- 明明存在的缺陷搜不到（实测「审批单列表」「通用模块」「审批」相关 BUG 全部漏检）→ 提交前去重形同失效；
- `list_resolved_bugs` 只能看到 **2 条**已解决单，实际有 **254 条**。

**修复**：新增 `ZentaoClient.all_bugs()` 分页拉全量（`BUG_PAGE_SIZE=100`，`BUG_PAGE_MAX=60` 页保护），
`search_bugs` / `list_resolved_bugs` 改为全量本地过滤，并在输出中回显「已扫描 全量 462/462 条」便于自查。

### 2. 需求关联：建单接口对 `story` 支持不稳定（已用 PUT 兜底）

- BUG 详情返回 `story` / `storyTitle` / `storyStatus` 字段，`PUT /bugs/{id}` 传 `{"story": 18058}` **实测可靠**；
- 但创建接口对 `story` 的支持不稳定，因此 `create_bug(story=...)` 的实现是：**先建单，再用 PUT 补关联**，并在返回信息中回显关联结果；
- 需求解析：`GET /stories/{id}` 校验 ID；按标题检索需 `GET /products/{id}/stories` 分页扫描（SCM 下 3377 条，`STORY_SCAN_MAX_PAGES=60` 页保护）。

### 3. `module` 不存在时不再终止建单

产品 SCM 下并无「审批流管理」等模块（模块下拉仅 `/` + 芯片仓、辅料仓、税费管理、公共板块、出货情况、预归类、
消息中心、进出货管理、贸汇通、脚轮项目、智造供应链、开票管理、非标、一般贸易 等 14 个）。
老实现在 module 名解析失败时直接 `[跳过]` 终止，导致整条缺陷无法提交；
现改为**降级挂根模块(0) 并在返回信息中提示**，保证缺陷先落单。

### 4. 影响版本 `openedBuild` 为必填

`title` / `pri` / `severity` / `type` / `openedBuild` 均不可为空。`openedBuild` 默认 `trunk`（主干），
置空会导致建单被拒绝（浏览器表单表现为请求 `ERR_ABORTED` 且无任何提示）。

### 5. 第二轮系统审计修复清单（2026-09-11）

由「禅道 MCP 是否已经完美」触发，对全文件做了分段系统审计，修复如下。
**每一条都有对应回归用例**，用例注释写明"旧行为为什么错"，防止回退。

#### P0 / P1（崩溃、数据丢失、静默错误）

| # | 缺陷 | 旧行为的后果 | 修法 |
|---|---|---|---|
| 1 | `_req(401)` ↔ `login()` **无限递归** | 凭证错误时 `RecursionError`，且每层都重发 `POST /tokens`（实测 12 次请求被瞬间打满） | 加 `_logging_in` 重入保护 + 401 分支要求 `attempt < RETRY` 且非登录请求自身 |
| 2 | 刷新 Token 后重试**仍带旧 Token** | headers 在循环外拍好，`continue` 复用 → 再撞 401，"重新登录"形同虚设（实测 Token 序列 `[None, None, None, 'NEW-TOKEN-2']`） | headers 移入重试循环内重建 |
| 3 | `list_bug_titles` **只取首页** | 终止条件依赖 `total`，缺失时 `page*100>=0` 立即为真 → 标题集 ≤100 条 → `submit_bugs_from_xlsx` **重复建单** | 与 `all_bugs` 同构的分页终止条件（总量/不满页/零新增）；实服已从 ≤100 修到 **447** |
| 4 | 空用户列表被**永久缓存** + `find_user` 原样返回中文 | `_users_cache` 空列表不是 `None` → 永不重试；`find_user('张三')` 返回 `'张三'` 并被当账号 PUT，工具还打印「已成功指派」 | 只缓存非空；中文姓名解析不到时**抛错**，纯 ASCII 才允许透传 |
| 5 | 分页循环**无墙钟闸门** | 单请求 12s × 60 页 = 720s ≫ 客户端 60s 上限，超时后服务端仍在跑、调用方结果未知 | `_Deadline`（45s）+ 每请求 `timeout=min(12, 剩余)`，返回 `truncated` 标注 |
| 6 | `create_bug` **任何非 2xx 都兜底重发** | 首次已落库却返回 5xx 时会**重复建单**；兜底报错还盖掉真实校验错误 | 只在 404/405 兜底，两次错误原文一起抛出 |
| 7 | `create_bug` 指派重试 `except: pass` | 返回串照样打印「指派 X」→ 静默错误 | 失败写入 notes 提示 |
| 8 | `find_story` 吞掉所有异常 + 接受不存在的 ID | 404/400 也能 `_json` 解析成功 → 空 title 仍返回 `(sid, "")`，**不存在的需求被当有效** | 区分 404/400（不存在）与 5xx（故障抛错）；空 title 视为未命中 |
| 9 | web 动作**只按 HTTP 200 判成功** | 禅道表单失败返回 `200 + {"result":"fail"}`、会话失效返回登录页 HTML，都被判成功并打印 `[ok]` | `_web_ok()` 解析 `result` / 识别登录页，失败即置位重登 |
| 10 | `_web_login` 成功后**终身复用** | 会话过期无法自愈，且报出误导性的"页面结构异常" | kuid 缺失 / 判定失败时置 `_web_logged=False`；`_web_form_action` 自动重登重试一次 |
| 11 | `paste_image_to_editor` 最坏 ~130s | 3 次重试且每次都整图下载回来量大小 | 闸门 20s、重试降为 2 次、用 `stream=True` 只取前 1KB 判空 |
| 12 | `_build_regression_report` 在 `actions: null` 时崩 | `bug.get('actions', [])` 返回 `None` → `TypeError`；且会断言"开发未记录修复文本"这种**未经验证的事实** | `or []` + 区分"无历史"与"有历史无备注"两种措辞 |
| 13 | `submit_bugs_from_xlsx` 单行异常毁整批 / 结果不落盘 | 记录只在整批跑完后写一次 → 客户端超时后连"哪些建了"都丢失；坏行（严重程度=5）中断全批 | 整行构造进 try、**逐条落盘**、批次闸门 + 续跑提示 |
| 14 | 去重键与实际落库标题**不一致** | 建单时标题过 `_sanitize_path_arrows`（`" > "`→`"→"`），旧代码拿原始标题比对 → 永远不等 → 每次运行重复建单 | 两处统一用 `_sanitize_path_arrows(title)` 做键 |
| 15 | `force=True` **清空**提交记录 | 覆盖写入只保留本次 → 历史 bugId 丢失 | 始终 load+merge；记录文件按 xlsx 命名并兼容旧文件 |

#### P2（健壮性与质量）

- `_sanitize_path_arrows` 把正文里的**裸 `<`** 当标签（`数量<5 时点 保存 > 提交` 里的 `>` 被静默漏改）→ 只认 `</?[a-zA-Z]…>` 形态
- `_steps_html` 的 `interface_url` **未转义**就拼进单引号属性（可注入）→ `escape(url, quote=True)`
- `_resolve_type` 未收录类型**静默降级**为 codeerror → 改为抛错并列出可选项
- `_clean_html_for_md` 从不做**实体反转义**（`&lt;`/`&nbsp;` 字面漏出）→ 加 `unescape`；解析不出 src 的 `<img>` 不再静默删除，改为可见占位符
- Markdown 表格单元格**不转义 `|`**（标题带竖线会毁掉整张表）→ 新增 `_md_cell()` 全面应用
- 回归报告**无上限**（长寿命 BUG 的 actions 可达数百条）→ §四 只列最近 50 条、历史修复只列最近 20 轮
- `int(activated_count or 0)` 遇 `"2 次"` 抛 `ValueError` → try/except 兜 0
- `list_products` 的 `or "无匹配产品"` 因 `+` 优先级更高而是**死代码** → 改为显式分支
- 关键字过滤**大小写敏感**（`list_products("scm")` 查不到 `SCM`）→ 统一 `.lower()`
- `search_bugs` 的 `status` 大小写敏感、`…/0` 计数显示 → 统一 `.lower()` + `total or len(bugs)`
- `find_user` 工具 `users[:30]` 截断却报"匹配用户 30 人" → 报真实命中数 + 「仅显示前 30 人」
- `export_bug_regression_report` 写入**未兜底**（路径是目录即抛 `OSError`）+ 返回无上限 → try/except + `REPORT_RETURN_MAX=8000`
- `get_bug` **无 try 且正文无上限** → `[error]` 兜底 + 正文截断 `STEP_DISPLAY_MAX=4000`（新增 `full_steps=True` 取全文）+「正文概况」一行（字符数/图片数/附件数）
- `upload_attach` 破坏自身三元组契约（`_get_edit_uid` 抛异常外泄、响应非对象时 `AttributeError`）→ 全程 try + `isinstance` 守卫
- `close_bug` 不提交 `resolution` → 新增参数（默认 `fixed`），回读校验并提示空解决方案
- `close_bug`/`activate_bug`/`comment_bug` 包装层**丢弃返回值**，失败也报 `[ok]` → 改为按返回布尔 + 状态复核给 `[ok]`/`[warn]`/`[error]`
- **线程安全**：FastMCP 在线程池里跑同步工具，`requests.Session` 与全局单例 `_STATE` 都不安全 → 新增 `_LockedSession`（串行化每次 HTTP）与 `_STATE_LOCK`
- **异常兜底**：所有工具统一经 `_tool` 装饰器注册，任何未捕获异常都转为 `[error] 工具名: 类型: 消息`，不再以 traceback 收场（`functools.wraps` 保留签名，schema 不变）

### 6. 用户列表的特殊通道（易踩）

本机 REST `/users` **无权限**（返回空），用户列表实际来自 web 兜底
`bug-activate-{id}.html?onlybody=yes` 页面的 `assignedTo` 下拉。

⚠️ **`bug_id=0` 也必须照常请求**：实测 `bug-activate-0.html` 一样返回带下拉的完整表单
（471 个选项）。曾因加了 `if not bug_id: return []` 而把用户解析整体打断。
另外 `GET /bugs?limit=1` 在 17.1 上**不存在**，`_auto_bug_id()` 必须再用
`/products/{id}/bugs?limit=1` 兜一层（实服已能取到真实 ID）。

### 7. 需求解析：不存在的 ID 会 400 或直接断连

实测 `GET /stories/99999999` 有时返回 **400 + `{"error":"error"}`**（不是 404），
有时**直接断开连接**（`RemoteDisconnected`）。因此：

- 404/400 一律视为"需求不存在"；
- 5xx 与连接异常算接口故障并**抛错**（不假装"未找到"）；
- `create_bug` 已把"需求解析失败"降级为提示 —— 因为此时**单子已经建好了**，
  抛出去会变成"建了单却没返回 ID"，调用方极易重复提单。

---

---

## 缺陷修复进展剖析与端到端回归测验范例（以 BUG #65295 为例）

当开发团队完成缺陷修复后，单据状态变为 `resolved`。此时调用：
```python
export_bug_regression_report(bug_id=65295, output_file="docs/BUG_65295_regression_report.md")
```

工具将自动从禅道底层提取以下信息并结构化重构成回归测验指南：
1. **缺陷基本档案看板**：BUG ID、标题、模块、严重度、优先级、提单人、解决人、解决时间、激活重开次数。
2. **开发团队最新修复进展与根因剖析**：
   - 提取最新备注中的【修复责任人】、【原因分析】、【解决方案】、【验证情况】与【提交 Commit / 分支】。
   - 例（BUG#65295）：开发（段广）指出行键 `_rowKey` 重复导致 toggle 反转，修复为全局唯一序号保证独立勾选，提交于 `feature_gantt_20260803` 分支。
3. **历史多轮修复与重开争议溯源**：
   - 梳理历次激活重开的原因（如测试人员胡嘉斌两次重开时指出的复现细节与附带 GIF 动图），让回归测试人员一眼看出历史未覆盖到的边界条件。
4. **端到端（E2E）回归测验用例矩阵**：
   - 根据开发暴露的底层技术根因，设计靶向测试用例（基础正向、同商品多行重复边界、数据刷新与旧状态残留清除、保存持久化数据回显、并发快速连续勾选）。
5. **判定闭环标准**：提供 Close（关闭）与 Re-activate（重开）的明确执行准则。

---

## 接口与字段分析（v17.1 实测）

### 1. BUG 创建核心接口

- **推荐 REST 接口**：`POST /zentao/api.php/v1/products/{product_id}/bugs`
- **请求头**：
  - `Token: <token>`
  - `Content-Type: application/json`
- **必填字段与校验**：
  | 字段 | 类型 | 说明 | 示例 |
  |------|------|------|------|
  | `title` | string | Bug 标题（不能为空，建议格式：【模块】验证点：现象短语） | `销售意向单导出失败...` |
  | `pri` | int (1~4) | 优先级（不能为空，1最高/阻断，2~3普通，4低） | `2` |
  | `severity` | int (1~4) | 严重程度（不能为空，1致命，2严重，3一般，4轻微） | `3` |
  | `type` | string | Bug 类型（不能为空，定制清单必须使用合法 key，见下表） | `codeerror` |
  | `openedBuild` | string / list | 影响版本（不能为空，默认主干） | `"trunk"` 或 `["trunk"]` |
- **关联与业务字段**：
  | 字段 | 类型 | 说明 | 示例 |
  |------|------|------|------|
  | `product` | int | 产品 ID（生和堂APS 对应 SCM） | `40` |
  | `execution` | int | 关联执行 ID（生和堂APS） | `578` |
  | `module` | int | 模块 ID（空则挂根模块） | `0` |
  | `assignedTo` | string | 指派给用户账号（支持传中文名如「万棚」，工具自动匹配） | `"wangpeng"` |
  | `story` | int | **相关需求 ID**（建单接口支持不稳定，工具内部改用 `PUT /bugs/{id}` 补关联） | `18058` |
  | `steps` | string | 重现步骤 HTML 正文 | `<p>[步骤]</p>...` |
  | `keywords` | string | 关键词（建议携带测试用例编号） | `"TC_ORDER_001"` |

### 2. BUG 类型定制 Key 映射表

| key | 中文名称 | 说明 |
|-----|---------|------|
| `codeerror` | 代码错误 | 默认类型，功能逻辑异常、接口报错、500/SQL 错误等 |
| `UserExperience` | 用户体验 | 交互卡顿、体验不合理 |
| `JMYHZX` | 界面优化 | 样式对齐、间距、换行等视觉问题 |
| `designdefect` | 设计缺陷 | 需求/蓝图设计本身缺失或矛盾 |
| `performance` | 性能问题 | 响应慢、大批量超时 |
| `onlinereq` | 操作问题 | 提示不清晰导致误操作 |
| `others` | 其他问题 | 兜底类型 |
| `others3` | 通道问题 | 消息、MQ、通知通道故障 |
| `others4` | 产品转需求 | 需转为需求池处理的问题 |

---

## 图文内容上传与正文内嵌机制

在禅道 17.1 中，**用户偏好将截图直接内嵌到重现步骤正文中**，而不是作为独立的下载型附件挂在页面底部：

1. **上传接口**：`POST /zentao/api.php/v1/files`
   - **Header**：`Token: <token>`
   - **Form-data 字段名**：必须为 **`imgFile`**（KindEditor 专用，传 `file` 或 `files[]` 会报 `{"error":"error"}`）。
   - **响应**：`{"id": "143809", "url": "http://192.168.200.53/zentao/file-read-143809.png"}`
2. **正文标签拼装**：
   ```html
   <p><img onload="setImageSize(this,0)" src="{url}" alt="问题截图" /></p>
   ```
3. **避坑提示**：
   - 菜单路径一律用 `→`（如 `销售预测管理 → 销售意向单`），严禁使用裸 `>`，否则禅道富文本解析时会截断丢弃后续内容。

4. **✅ 本项目流程约定（用户 2026-09-11 明确）：提单时"只向正文中写入"**
   - 所有图文证据一律内嵌 `steps` 正文，**不使用附件通道**（`attach_file` 的非图片分支 / `upload_attach` 保留但非默认路径）；
   - 由此 `steps` 的**唯一写入者就是提单流程本身**；配合"多图串行调用"，
     `steps` 的 read-modify-write 窗口不构成实际风险（§5 遗留项据此关闭）；
   - 交付物统一为"一条可读的图文正文"，评审/回归只看详情页正文。

---

## 典型对话使用指令

1. **身份切换与指派**：
   - 「切换到账号 `lihaizhen`，密码 `Aa123456`」 $\rightarrow$ `switch_account`
   - 「查看当前是哪个账号登录」 $\rightarrow$ `whoami`
   - 「把 BUG#65295 指派给胡嘉斌，备注请尽快回归」 $\rightarrow$ `assign_bug(65295, '胡嘉斌', '请尽快回归')`
2. **回归分析与报告导出**：
   - 「查一下当前产品下有哪些已解决待验证的 BUG」 $\rightarrow$ `list_resolved_bugs`
   - 「分析一下 BUG#65295 开发修复情况，输出端到端回归测验文档」 $\rightarrow$ `export_bug_regression_report(65295)`
3. **建单与图文**：
   - 「提一条 BUG，标题...，指派给万棚，附带截图 D:/xx/shot.png」 $\rightarrow$ `create_bug(..., assigned_to='万棚', image_path='D:/xx/shot.png')`
