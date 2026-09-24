# 禅道 BUG 提单执行规范（权威版）

> **本文件是本项目禅道提单的唯一权威规范。**
> 由 2026-09-11 实测固化，依据为 BUG #65532 / #65538 / #65548 三条完整提单链路（字段落位 → 图文内嵌 → 需求关联 → 提交后回查）。
> 已取代旧的 `zentao_mcp/zentao-bug-report-standard.md`（2026-09-09 版，含过时账号与矛盾口径）。
> **v1.1（2026-09-11 第二轮）**：新增 §4 正文 HTML 白名单与"全量覆盖"约束、§6 多图写入方式、§8 正文体检项、§13 MCP 缺陷修复记录。
> **v1.2（2026-09-11 第三轮）**：新增 §14 第二轮系统审计修复（20+ 项，含 4 个 P1 数据完整性问题）与 51 个回归用例的跑法。
> **v1.3（2026-09-20）**：新增 §4 `[步骤]` 分叉说明、§8 代码块完整性回读项；并**修复 `_steps_html()` 兜底排版拆散 `<pre>`/`<table>` 的缺陷**（§16，新增 5 条回归用例，全量 56 例通过），依据 BUG #65916 提单实测。

---

## 0. 执行原则（最高优先）

1. **永不确认，直接提交**（用户 2026-09-10 三次明确）：建单、传图、指派、关联需求、复制证据、清理临时文件，**一律直接做**，不要 `AskUserQuestion`、不要停下来等确认。目标是提 BUG 要快。
2. **唯一例外**：信息不足以判断字段归属时才问 —— 例如完全无法从上下文推断该派给谁、该关联哪个需求。
3. **提交后必须回读自检**（见 §8），不允许"发出去就不管"。

---

## 1. 字段默认值速查表（照抄即可）

| MCP 参数 | 默认值 | 说明 |
|---|---|---|
| `product` | `40` = **SCM** | 固定 |
| `execution` | `578` = **生和堂APS** | 固定，同时决定"所属项目" |
| `module` | 强制写死留空（不传或传 `""`）→ 根模块 `/`（id 0） | **【铁律·强制写死不要动】建单/改单时一律不传该参数，默认挂根模块 0（`/`）即可！严禁**调用 `list_modules` 去查找或自行挑选任何业务子模块（如**绝对严禁**选择“公共板块”等通用名称）；SCM 产品下无对应业务子模块映射，任何缺陷统一直接归属根模块 0。 |
| `build` | `trunk` = **主干** | **必填**，置空建单静默失败（详见 §11） |
| `type_name` | `代码错误` | **传中文**；英文 key 见 §5 |
| `severity` | `3` | 按 §3 口径自评 |
| `pri` | `2` ~ `3` | 按 §3 口径自评 |
| `assigned_to` | 中文姓名 | 直传即可，工具自动解析账号：`赵浩源`(`zhaohaoyuan` 前端)、`万棚`(`wangpeng` 后端)、`段广`、`胡嘉斌`、`李科勇` |
| `story` | 需求标题关键字或 ID | 如 `审批单列表` 或 `18058`，建单即关联 |
| `keywords` | `<用例编号>,<要点1>,<要点2>` | 例 `APS_SPLCG_SPDLB,重置,排序未复位`，便于从 BUG 回溯用例 |

---

## 2. 标准提单流程（5 步）

```
1. create_bug(title, steps, assigned_to, story, keywords, type_name, severity, pri, build, image_path)
        └─ 首图随建单直接内嵌正文
2. attach_image_to_bug(bug_id, image_path, caption)     ← 其余图逐张串行追加
3. get_bug(bug_id)                                       ← 回读，逐字段核对落位
4. find_story(bug_id=...)                                ← 反查需求关联是否成立
5. 回填用例 JSON：「测试结果」= 不通过 + 结构化备注
```

---

## 3. 定级口径（用户 2026-09-10 明确）

**`pri` = 紧急度 / 处理顺序，由 AI 自行评估决定，不要问用户**

| pri | 场景 |
|---|---|
| 1 | 阻断功能不可用（导出失败、无法提交、主流程走不通） |
| 2 ~ 3 | 一般功能问题 |
| 3 | 纯显示 / 文案类 |

**`severity` = 影响面**（与 pri 解耦，"要不要先修"看 pri）

| severity | 场景 |
|---|---|
| 1 | 致命：主流程不可用 / 数据丢失 |
| 2 | 严重：功能或数据结果错误 |
| 3 | 一般：功能缺失但可绕过 / 显示与数据问题 |
| 4 | 轻微：文案、命名不一致 |

---

## 4. 标题与正文规范

**标题**：`【模块】验证点：现象短语` 或 `菜单路径→页面：现象，影响`

> 示例：`审批流管理→审批单列表：点击「重置」未恢复排序状态，表头排序与数据顺序均保持重置前的排序`

**正文**：强制 **Markdown 结构化**，禁止【】+编号的"文本墙"（用户曾就 BUG#65411 明确返工过一次）。

标准骨架：
```
【前置条件】  …环境、页签、数据基线（条数）
【重现步骤】  1. …  2. …
【对照基准】  …正确态应该是什么（用于证明"这是缺陷"而非"设计如此"）
【补充说明】  …已排除的相邻项（如"查询值清空/运算符复位均正常，仅排序未复位"）
```
再由 `create_bug` 的 `expected` / `actual` 参数分别写预期与实际。

**硬性约束**
- 菜单路径分隔符**必须用 `→`**，**禁用 `>`**（禅道会把 `>` 当标签吃掉，曾导致路径被截断）
- **正文 HTML 只用白名单标签且必须闭合**：`p / b / i / ul / ol / li / pre / table / tr / th / td / h3 / img`。
  **不要把一个标签嵌进同类标签**（如 `<b><code>…</code></b>` 再套一层），实测会出现 `<b>` 与 `</b>` 错位、段落文字丢失。
  `code` 标签可用但**不要嵌套**；代码/报文一律用 `<pre>` 包。
- ⚠️ **`steps` 是「整字段全量覆盖」，不是追加**：补写或重写正文时，**必须把原正文里的 `<img src=...>` 一并带上**，
  否则旧截图会被**静默删除**（实测踩过：补写 #65538 正文时把原报障截图冲掉）。
  MCP 已内置**丢图告警**——返回串里出现 `[警告] 本次 steps 覆盖将【移除 N 张原有图片】` 时必须立即把图补回。
- **接口类问题必带 `[接口信息]` 段**（用户 2026-09-09 明确）：接口地址 + 请求报文示例，报文用 `<pre>` 包裹（17.1 不剥 `pre`）
- **正文里出现多行 `<pre>`（代码/报文）时，建议 `steps` 首部显式写 `<p>[步骤]</p>`**（2026-09-20，BUG #65916）：
  `_steps_html()` 用 `"[步骤]" in steps` 做分叉——
  - **命中**：整段 `steps` **原样写入**，排版完全由调用方掌控（**推荐**）；
  - **未命中**：工具补 `<p>[步骤]</p>` 后走兜底排版 —— v1.3 起，`<pre> / <table> / <ul> / <ol> / <blockquote>`
    块内以及"整行本身以标签开头"的行**原样保留**，仅纯文本行逐行包 `<p>`，因此多行代码块**不会再被拆散**。
  > 修复前的旧行为（**已修，仅在读老单时可能见到**）：无条件逐行包 `<p>`，多行 `<pre>` 被拆成一段段 `<p>`
  > （实测第一次提 #65916 三个代码块碎成 40+ 个 `<p>`），且行内 `=>`、`<` 会让该行部分内容丢失
  > （实测丢失 `this.gesture.on("doubletap", Q =>` 前缀）。详见 §16。
  `<pre>` 里的 `>` `<` 用 `&gt;` `&lt;` 实体更稳；该分叉 `create_bug` 与 `update_bug(steps=...)` 共用。
- **把缺陷范围钉死**：写明哪些相邻项已核实正常，避免开发误判为整个控件失效
- 通过项也要保留，不要只写缺陷

**根因级正文的加分项**（开发反馈"过于浅显"后固化，见 #65532 第二轮）
当现象背后能定位到代码/配置时，正文要给出：
1. **证据链**：接口实捕原文（`<pre>`）→ 前端 bundle 源码片段（`<pre>`）→ 组件配置 JSON（`<pre>`）
2. **对照矩阵**：多组输入 × 实际结果 × 判定，把"只有编码能命中、中文名一律 0 条"这类结论**用数据钉死**
3. **关键排除**：明确写出"不是后端能力问题"这类反向证据，避免开发往错方向查
4. **正确范式**：同模块里已经做对的写法（可直接复用）
5. **修复建议表**：方案 × 改动位置 × 工作量，并给出"最小且最正确的组合"
6. **回归断言点**：逐条可执行、可判定的断言，含多语言

---

## 5. BUG 类型 Key 映射表（v17.1 定制清单）

MCP 工具 `type_name` 传**中文名称**；直接打 REST 时传**英文 key**（用官方 `interface`/`standard` 等未定义 key 会显示英文原值）。

| key | 中文名称 | 说明 |
|---|---|---|
| `codeerror` | 代码错误 | 默认；功能逻辑异常、接口报错、500/SQL 错误 |
| `UserExperience` | 用户体验 | 交互卡顿、体验不合理 |
| `JMYHZX` | 界面优化 | 样式对齐、间距、换行等视觉问题 |
| `designdefect` | 设计缺陷 | 需求/蓝图本身缺失或矛盾 |
| `performance` | 性能问题 | 响应慢、大批量超时 |
| `onlinereq` | 操作问题 | 提示不清晰导致误操作 |
| `others` | 其他问题 | 兜底 |
| `others3` | 通道问题 | 消息、MQ、通知通道故障 |
| `others4` | 产品转需求 | 需转需求池处理 |

---

## 6. 图片证据规范

- **首图**：`create_bug(image_path=...)` 建单即内嵌
- **追加**：`attach_image_to_bug(bug_id, image_path, caption=...)`
- ⚠️ **多图必须串行调用** —— 并发会各自重写 `steps` 正文，导致互相覆盖丢图
- ⚠️ **每张图都要写 `caption`**，说明它证明什么（如"步骤2执行结果：点击重置后仍为 sort_upward"）
- ⚠️ **不要用 `update_bug(steps=...)` 覆盖式补写来"加图"**：
  - 需要"改正文 + 加图"时，把 `<img>` 直接写进 `steps` 提交（一次 PUT），或用 `update_bug(steps=..., image_path=...)`（v1.1 已修好，见 §13 缺陷 2）
  - 只加图不改正文时，一律用 `attach_image_to_bug`
- 底层机制：`POST /files` 表单字段名**必须是 `imgFile`**（`files[]` / `file` 均报 `{"error":"error"}`）→ 返回 `{id,url}` → `<img src="{url}">` 插进正文 → `PUT /bugs/{id}` 更新
- 推荐证据组合：**操作前 → 操作后 → 最小复现对照**（三张即可说清）
- ✅ **本项目流程约定（用户 2026-09-11 明确）：提单时一律"只向正文中写入"** —— 所有图文
  证据都内嵌进 `steps` 正文（`<img src="...">`），**不使用附件通道**（不用 `attach_file`
  的非图片分支 / `upload_attach`）。因此：
  - `steps` 的**唯一写入者就是提单流程本身**，且本规范已要求"多图必须串行调用"，
    所以 `steps` 的 read-modify-write 窗口**不构成实际风险**（§13 的遗留项据此关闭）；
  - 交付物形态统一为"一条可读的图文正文"，评审与回归都只看详情页正文，无需再翻附件列表。

---

## 7. 需求（story）关联

| 目的 | 调用 |
|---|---|
| 建单即关联 | `create_bug(..., story='审批单列表')` |
| 给存量单补关联 | `link_story_to_bug(bug_id=65532, story='审批单列表')` |
| 核对取值 | `find_story(ref='审批单列表')` / `list_stories(keyword='审批单列表')` |
| **反查某单关联了谁** | `find_story(bug_id=65548)` → 返回 `#18058 审批单列表（需求状态 active）` |

**实现说明**：建单接口对 `story` 支持不稳定，工具内部改为「**先建单，再 `PUT /bugs/{id}` `{"story": id}` 补关联**」，并在返回串中回显关联结果。
**注意**：SCM 产品下有 3377 条需求，按标题关键字解析时务必让 `find_story` 先确认唯一命中。

---

## 8. 提交后自检清单（必做，缺一不可）

- [ ] `get_bug(bug_id)` 回读：**产品 / 执行 / 模块 / 严重 / 优先级 / 类型 / 指派 / 影响版本** 全部落位
- [ ] 影响版本非空（`trunk`），正文含 `[步骤]` 与 `[结果]` / `[期望]`
- [ ] `<img src=...>` 张数与预期一致，URL 可访问
- [ ] **正文体检（v1.1 新增）**：图片张数 == 预期；`p/b/i/ul/ol/li/pre/table/tr/td/th/h3` 开闭标签数**一一相等**；无 `→ <` 破坏签名
      一键执行：`python scripts/zentao_bug_body_check.py <bug_id> [...]`（凭证走环境变量，见 §10）
- [ ] **代码块完整性（v1.3 新增）**：`get_bug(full_steps=True)` 回读，确认每个 `<pre>` 仍是**一整块**、`<pre>` 内没有夹 `<p>`
      —— 被逐行拆散 = 提交时 `steps` 漏了 `[步骤]` 标记（见 §4），必须重写正文并带上 `[步骤]`
- [ ] **界面事实必须实测确认（v1.1 新增）**：页签名、列表条数、字段值等"看得见的事实"务必现场读一次再写进正文
      —— 实测教训：#65532/#65548 连续两张单子把 4 条数据所在的页签写成「已办」，实际是「**待办**」（已办是 20+ 条完全不同的记录）
- [ ] `find_story(bug_id=...)` 返回预期需求（关联链路成立）
- [ ] `search_bugs(keyword=<标题特征片段>)` 能命中（新单可被检索到）
- [ ] 用例 JSON 的「测试结果」已回填 `不通过` + 结构化备注（含执行人 / 执行时间）
- [ ] 若一条用例同时暴露**两个独立缺陷** → **分开建单**，并在各自正文标注"与 #xxxxx 非同一问题，请勿合并"

---

## 9. 检索陷阱（曾大面积漏检）

- `GET /products/{id}/bugs` 会**忽略 `title=` 过滤参数**，且**默认只返回首页**
  （SCM 下共 400+ 条 BUG，旧版 `search_bugs` / `list_resolved_bugs` 严重漏检：实测"已解决"只能看到 2 条，实际 254 条）
- 必须用 MCP 的 `search_bugs` / `list_resolved_bugs`（**已改为全量分页后本地过滤**，输出回显「已扫描 全量 N/N 条」）
- ⚠️ **关键词可能被标点断开而搜不到**：标题里是「点击「重置**」**未恢复排序」，搜 `重置未恢复排序` 会 0 命中。
  **对策：用最短的特征子串**（搜 `未恢复排序`）。

---

## 10. 环境与运维

- 凭证**走环境变量**：`ZENTAO_URL` / `ZENTAO_ACCOUNT` / `ZENTAO_PASSWORD`（勿在仓库内硬编码明文）
- 当前运行态（DSH `cordis.patch.yml` 的 `mcp-zentao`）：`http://127.0.0.1:12306/zentao`，账号 `hujiabin`
- 历史共享环境：`http://192.168.200.53/zentao`
- **改了 `zentao_mcp_server.py` 必须重启 MCP 服务才生效**：
  - 该服务由 `dsh-mcp-client` 以 stdio 子进程托管，**reconnect 默认开启**（500ms 起退避，最多 10 次）
  - 做法：kill 掉 zentao 的进程树 → 监管器自动重启并重新同步工具集
  - 定位命令（`Get-CimInstance` 在沙箱内不可用，**用 `wmic`**）：
    ```powershell
    wmic process where "name='python.exe'" get processid,parentprocessid,commandline /format:list | Select-String zentao
    ```
  - **验证是否真的生效**：调一个新增工具（如 `find_story`）。返回 `unknown tool` = 仍是旧进程。
- ⚠️ **超时预算必须短于 MCP 客户端上限**（v1.1 固化）：
  - DSH 的 MCP 客户端 `toolCallTimeoutMs = 60s`；服务端 `HTTP_TIMEOUT = 12s`、`RETRY = 1`
  - **只对"连接类"失败重试**（`ConnectionError`）；读超时**不重试**——重试只会让墙钟时间翻倍并撞穿 60s，
    造成"客户端已超时、服务端还在写"的黑洞（调用方无法得知最终是否成功）
  - 历史配置 `timeout=25 × RETRY=2` = 单请求最坏 75s、复合工具最坏 225s，是 `-32001 Request timed out` 的结构性根因
  - **实测禅道本身极快**：login 0.10s / 读单 0.08s / 上传 236KB 0.26s、603KB 0.09s —— 瓶颈从不在服务端，
    遇到超时先怀疑 MCP 侧而非禅道

---

## 11. 浏览器表单兜底（仅当 MCP 不可用）

MCP 正常时**不要**走浏览器表单。必须走时，以下四条是实测血泪：

1. **填单顺序强制**：`所属项目 → 影响版本 → 相关需求 → 其余`
   原因：`#execution` 的 `onchange=loadExecutionRelated(...)` 会重载并**清空「相关需求」**（实测被清空两次）
2. **Chosen / labelSelector 控件不能 JS 改值**：底层 `select` 是 `display:none`，直改 `select.value` 会被控件状态**静默回滚**。
   必须**点控件本身**（点开 `chosen-single` / `chosen-choices`）再点候选 `li`
3. **「相关需求」picker 浮层渲染在主文档**（`div#pickerDropMenu-pk_story`，选项 `a#pk_story-item-{id}-option`），**不在 iframe 内** —— 在 iframe 里搜必然失败
4. **「影响版本」是必填项，漏填会以 `net::ERR_ABORTED` 静默失败**（无任何页面报错）
   排查配方：`xpath://*[contains(@class,"required")]` 找必填容器，`xpath://*[contains(@class,"has-error")]` 找当前失败控件

**其他已知限制**：CDP **读不到** `multipart/form-data` 表单的请求体（`post_data: null`）；禅道建单表单是 `form#dataform`，`enctype=multipart/form-data`、无 `action`（POST 到当前 URL），提交走 XHR。

---

## 12. 相关文件索引

| 文件 | 作用 |
|---|---|
| `.agents/skills/zentao-bug-ops/SKILL.md` | 技能入口：工具清单 + 调用示例 |
| `zentao_mcp/README.md` | MCP 服务部署、工具签名、接口与字段分析、踩坑记录 |
| `zentao_mcp/zentao_mcp_server.py` | 服务实现（权威代码） |
| `bug_reports/` | 提单产物：`templates/` 模板、`zentao_xlsx/` 导入清单、`submitted_records/` 提交记录 |
| `scripts/zentao_bug_body_check.py` | **正文体检器**（§8 自检项的一键执行）；直连 REST，绕过 MCP 超时边界 |
| `tencent_mcp/tencent_docs_rpc.py` | 腾讯文档 MCP 的**直连 JSON-RPC 客户端**（见 §15） |
| `scripts/tencent_backfill_results.py` | **E2E 结果回填腾讯表格**（幂等、带回读校验，见 §15） |
| `tencent_mcp/README.md` | **在线表格精简代理**（只暴露 `sheet.*` 25 工具，省 96.4% token） |
| `scripts/extract_tencent_tools.py` | 生成/刷新代理用的工具 schema 快照 |
| `scripts/check_tencent_proxy.py` | 校验代理 schema 与上游逐字节一致 + 转发可用 |
| `scripts/audit_mcp_schema_tokens.py` | 量化各 MCP / 子集的 schema token 成本 |
| `scripts/validate_cordis_patch.py` | 改完 `cordis.patch.yml` 后必跑的校验器 |
| `zentao_mcp/tests/test_zentao_mcp.py` | **回归测试套件**（51 用例，标准库 unittest，零依赖）；改完 MCP 必跑 |
| `zentao_mcp/README.md` | 踩坑与两轮系统审计的完整缺陷清单（含"旧行为为什么错"） |
| `docs/reports/BUG*_深度补充分析_*.md` | 深度根因报告（正文的素材来源，先写报告再落单） |

---

## 13. MCP 缺陷修复记录（v1.1，2026-09-11）

由"禅道 MCP 是否已经完美"的自检触发，实测定位并修复 4 处缺陷。**修复后必须重启 MCP 才生效**（见 §10）。

| # | 缺陷 | 症状（实测） | 修复 |
|---|---|---|---|
| 1 | `_sanitize_path_arrows` 破坏 HTML | 无条件 `replace("> ", " → ")`，把**标签之间的空格**也当箭头：`</b> <code>` → `</b> → <code>`；`x => y` → `x =→ y` | 按标签切分，只在标签**之外**、且**不在 `pre`/`code` 内**的文本上替换；`=>` / `->` 先占位保护。15 条回归用例全绿 |
| 2 | `update_bug(steps=…, image_path=…)` 丢图 | 先 `attach_image_to_bug`（写入含图正文）→ 又用 `payload["steps"]` **整覆盖一次**，图片被静默丢弃 | 把 `<img>` 并入最终 `steps`，**全程只 PUT 一次** |
| 3 | `steps` 全量覆盖无护栏 | 补写正文时漏抄旧 `<img>`，旧截图被**静默删除** | 比对覆盖前后图片 `src` 集合，返回串中告警 `[警告] …将【移除 N 张原有图片】` |
| 4 | 超时预算 > 客户端上限 | `timeout=25 × RETRY=2` → 单请求最坏 75s、复合工具 225s > 客户端 60s，必然偶发 `-32001`，且超时后**结果未知** | `HTTP_TIMEOUT=12`、`RETRY=1`，且只对连接类失败重试 |

**顺带优化**：`update_bug` 原先在 module / story / image 三处各调一次 `get_bug`（最多 3 次读取），改为**单次读取复用**。

**仍未解决 / 待观察**：
- 服务端 `requests.Session` 为**全局单例共享**，FastMCP 同步工具在线程池执行 —— 并发工具调用共用连接池与 cookie，**非线程安全**。当前靠"串行调用"规避（见 §6）
- `get_bug` 返回纯文本全量正文（#65532 已达 6.4KB），token 开销偏大，暂无结构化输出

---

## 14. MCP 第二轮系统审计修复（v1.2，2026-09-11）

第一轮修的是"已经踩到的坑"；这一轮把整个 2400 行服务**分段系统审计**了一遍，
又找出 20+ 处缺陷（含 4 个 P1 数据完整性问题）。**完整清单与每个缺陷的"旧行为为什么错"
见 `zentao_mcp/README.md` 的「### 5. 第二轮系统审计修复清单」。**

**结论层要点（提单时真正会碰到的）**：

| 影响 | 说明 |
|---|---|
| **不再重复建单** | `list_bug_titles` 旧实现只取首页（≤100 标题），`submit_bugs_from_xlsx` 的远端去重因此失效；实服已修为 **447** 个标题。同时 `create_bug` 不再对任何非 2xx 兜底重发 |
| **不再"静默错误指派"** | `find_user` 解析不到中文姓名时**抛错**，不再把 `张三` 当账号发出去 |
| **不再"假成功"** | web 动作（关闭/激活/备注）改按 `result`/登录页判定，失败即 `[error]`/`[warn]`，不再只看 HTTP 200 就报 `[ok]` |
| **不会撞穿 60s** | 所有分页循环加 45s 墙钟闸门；批量提单逐条落盘、可续跑 |
| **异常必有可读输出** | 全部工具经 `_tool` 装饰器兜底，任何异常 → `[error] 工具名: 类型: 消息` |
| **正文更省 token** | `get_bug` 默认截断正文到 4000 字符（`full_steps=True` 取全文），并回显「正文概况：字符数/图片数/附件数」 |

**回归测试（改完 MCP 必跑）**：

```powershell
.venv\Scripts\python.exe zentao_mcp\tests\test_zentao_mcp.py   # 51 个用例
```

用例全部对应真实缺陷，注释写明旧行为，**禁止为了让测试过而改测试**。

> ⚠️ 两次踩到的自伤教训，写在这里防止再犯：
> 1. 收紧 `_users_from_web` 时加了 `if not bug_id: return []`，而 `bug-activate-0.html`
>    **确实是**本机唯一可用的用户列表通道（REST `/users` 无权限）→ 用户解析整体被打断。
> 2. 把 `find_story` 的非 200 一律当故障，而禅道对**不存在的需求**返回的是 400 →
>    把"ID 写错"误报成系统异常。
> **教训：改"防御性收紧"之前，先把收紧前后的真实请求各打一遍。**

---

## 15. E2E 结果回填腾讯文档（v1.2）

**为什么上游 MCP 被 disabled、现在怎么用**：官方 `mcp-tencent-docs` 暴露
**224 个工具、schema 约 39.8 万字符 ≈ 15.1 万 token**，占工具定义 token 的 ~90%，
因此保持 `disabled: true`。现在改用**本地精简代理 `mcp-tencent-sheets`**
（`tencent_mcp/tencent_sheets_mcp_server.py`，已启用）：

| 目标 | 工具数 | 估算 token |
|---|---|---|
| 上游全部 | 224 | ~150,925 |
| **精简代理（`sheet.`）** | **25** | **~5,488（省 96.4%）** |

代理的工具名 / 描述 / **入参 schema 与上游逐字节一致**，调用原样转发，用法无差别。
**脚本侧**（不开会话也能跑）仍可直连上游：`tencent_mcp/tencent_docs_rpc.py` 发 JSON-RPC。

> ⚠️ **harness 会改写工具名**：点号被规范化成下划线并追加哈希后缀，例如
> `sheet.set_cell_value` → `mcp__tencent-sheets__sheet_set_cell_value_6182fefea08d`。
> **照上游点号名调用会直接 `unknown tool`**。25 个工具的完整映射见
> `tencent_mcp/README.md` 的「harness 里的实际工具名」一节。
> 已知入口：查页签 `..._sheet_get_sheet_info_2df3eeeed13a`、
> 读区域 `..._sheet_get_cell_data_df2fd71cf388`、
> 写单元格 `..._sheet_set_cell_value_6182fefea08d`。


### 15.1 表格定位（实测）

| 项 | 值 |
|---|---|
| 工作簿 | `file_id = DV3pUSmNkTG1hQk13`（在线表格，非智能表格 → 用 `sheet.*` 而非 `smartsheet.*`） |
| 页签 | `sheet_id = puwuj9` = **审批流管理**（另有 `BB08J2` 产品工艺、`jwqfrl` 主生产计划、`sz9wx4` 接口与日志） |
| 行号 | **行号 = 用例号**：表头在 row 0，故 `APS_SPLCG_SPDLB_0052` 在 **row 52**（仍建议按列 A 实扫校验） |

### 15.2 列位（0 基）

```
0 用例编号  1 级别  2 一级模块  3 二级模块  4 功能  5 验证点
6 前置条件  7 测试步骤  8 测试数据  9 预期结果
10 测试结果  11 执行人  12 执行时间  13 编写人  14 编写时间  15 备注
```

- 结果取值口径（全表一致）：**通过 / 不通过 / 待定**（待定 = 前置条件不满足、用例无法执行）
- 写入工具：`sheet.set_cell_value`（`file_id/sheet_id/row/col/string_value/value_type`）

### 15.3 必须知道的三个坑（都实测踩过）

1. **大范围读会被服务端直接断连**（`RemoteDisconnected`）：一次拉 136 行 × 16 列必挂。
   → **分块读，≤25 行/次**（`scripts/tencent_docs_rpc.py` 已内置连接类失败重试）。
2. **请求范围超出实际行数报 `code 60871 invalid input grid range`**：
   → 先用 `sheet.get_sheet_info` 取 `row_count` 再收敛 `end_row`。
3. **备注不要覆盖**：表格里已有测试设计备注（如「P-01 未勾选拦截」）。
   → 追加而不覆盖，并以 `执行(YYYY/MM/DD)：` 为幂等标记，重复运行不重复追加。

### 15.4 执行方式

```powershell
.venv\Scripts\python.exe scripts\tencent_backfill_results.py --dry-run   # 先看差异
.venv\Scripts\python.exe scripts\tencent_backfill_results.py            # 写入并自动回读校验
```

脚本只回填 `RESULTS` 列表里的用例；每轮 E2E 结束后把新结论加进去即可。
**单一权威源仍是 `testcase_json/functional/*.json`**（§8 自检要求先回填 JSON），
表格是给人看的汇总，两者必须一致。

---

## 16. MCP 缺陷修复记录（v1.3，2026-09-20）

### 16.1 `_steps_html()` 兜底排版拆散 `<pre>`/`<table>`（BUG #65916 提单时实测）

| 项 | 内容 |
|---|---|
| 症状 | `steps` 未带 `[步骤]` 标记时，兜底分支 `for line in steps.splitlines(): parts.append(f"<p>{line}</p>")` **无条件逐行包 `<p>`** → 多行 `<pre>` 代码/报文被拆成一段段 `<p>`（#65916 首次提单：三个代码块碎成 40+ 个 `<p>`）；`<table>` 同理被打散；拆散后禅道 HTML 过滤还会连带吃掉半行（实测丢失 `this.gesture.on("doubletap", Q =>` 前缀） |
| 影响面 | `create_bug(steps=...)` 与 `update_bug(steps=...)` 共用该函数；凡正文含多行代码块/表格报文且未写 `[步骤]` 即命中 |
| 定位 | `zentao_mcp/zentao_mcp_server.py` → `_steps_html()` 兜底分支（`steps` / `actual` / `expected` 三处同源） |
| 修复 | 新增 `_steps_plain_lines_to_p()` 统一兜底排版：① `<pre> / <table> / <ul> / <ol> / <blockquote>` 块内（按标签深度判定，含块内空行与缩进）整行保留；② 整行本身以标签开头（`<h3>x</h3>`、`<p>y</p>`）的行保留，不再套 `<p>` 造成 `<p><p>` 嵌套；③ 其余纯文本行保持"逐行包 `<p>`"的老行为，纯文本步骤排版不受影响 |
| 新增常量 | `_HTML_BLOCK_TAG_RE`（块级标签识别）、`_HTML_LINE_START_RE`（整行 HTML 判定） |
| 回归用例 | `zentao_mcp/tests/test_zentao_mcp.py::TestStepsHtml` 新增 5 例（多行 `<pre>` 完整、`<table>` 完整、纯文本行仍包 `<p>`、`actual` 段代码块完整、整行 HTML 不双重包裹）→ 全量 **56 例通过** |
| 线上验证 | 用修复后的函数对 #65916 重建正文（**故意不带** `[步骤]`）→ `PUT` 200 → 回读：`<pre>` 3 个且内部**无** `<p>`、代码特征串齐全、图片 3 张；`scripts/zentao_bug_body_check.py 65916` 标签闭合全部平衡 ✔ |
| 生效条件 | **需重启 zentao MCP 服务**（stdio 子进程由 dsh-mcp-client 托管，kill 进程树即自动重启，见 §10） |

### 16.2 操作建议（保留）

- 正文含代码块/报文时**首部仍建议写 `<p>[步骤]</p>`**：整段原样写入，排版完全可控，不受兜底逻辑后续变更影响。
- `<pre>` 内的 `>` `<` 用 `&gt;` `&lt;` 实体表达更稳（`_sanitize_path_arrows` 对 `pre`/`code` 内是逐字保护，但实体化可免去歧义）。
- 提交后仍按 §8 回读一次：`get_bug(full_steps=True)` 看 `<pre>` 是否成块 + 跑正文体检器。



