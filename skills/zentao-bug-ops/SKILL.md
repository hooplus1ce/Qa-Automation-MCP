---
name: zentao-bug-ops
description: 禅道缺陷管理全链路技能：内置经过实测固化的提单执行规范（字段默认值、定级口径、标题正文规范、图文内嵌、需求关联、提交后自检），支持动态切换登录账户、人员精准直派，以及基于开发修复进展自动导出端到端（E2E）回归测验指南。
version: 1.2.0
---

# 禅道缺陷全流程自动化技能

## 加载顺序（按需加载，勿一次性全读）

| 顺序 | 文件 | 何时读 | 体量 |
|---|---|---|---|
| 1 | 本文件 `SKILL.md` | 每次必读 | 小 |
| 2 | **`references/submission-standard.md`** | **提单 / 回归 / 流转前必读** —— 字段默认值、定级口径、标题与正文规范（含 HTML 白名单与根因级正文要求）、图片与需求关联、提交后自检清单、检索陷阱、表单兜底、**§13 MCP 缺陷修复记录** | 中 |
| 3 | `zentao_mcp/README.md` | 需要工具签名、接口字段分析或部署配置时 | 中 |

> ⚠️ **提单前必须先读 `references/submission-standard.md`**，按其「§1 字段默认值速查表」+「§2 标准提单流程」执行。
> 该文件是 2026-09-11 由 BUG #65532 / #65538 / #65548 三条完整链路实测固化的**唯一权威规范**，不要再凭记忆填字段。

## 功能说明
配合捆绑的 `zentao-bug` MCP 服务，实现以下核心自动化能力：
1. **多账户切换与登录身份查询**：`switch_account` / `whoami`
2. **人员精准直派**：`assign_bug`（支持中文真实姓名如「万棚」「赵浩源」「段广」「胡嘉斌」自动解析）
3. **缺陷建单与图文正文内嵌**：`create_bug` / `attach_image_to_bug` / `upload_image`（走 REST API `POST /files` 的 `imgFile` 字段）
4. **需求（story）关联**：`find_story` / `list_stories` / `link_story_to_bug`；`create_bug` 与 `update_bug` 均已支持 `story` 参数（传需求 ID 或标题关键字）
5. **缺陷检索**：`search_bugs`（已修复「只查首页」的漏检问题，改为全量分页后本地过滤，支持 `keyword` + `status`）
6. **E2E 回归测验指南导出**：`export_bug_regression_report`（深入解析开发团队修复说明、代码提交及历史重开原因）
7. **web 通道动作备注**：`activate_bug` / `close_bug` / `comment_bug`（激活/关闭/追加备注，支持 image_path 自动内嵌截图；回归不通过时用 activate_bug 重新激活并指派回开发）

## 一键速查（完整口径见 `references/submission-standard.md`）

| 参数 | 默认值 |
|---|---|
| `product` | `40`（SCM） |
| `execution` | `578`（生和堂APS） |
| `module` | 写死留空（不传或传 `""`） | **【铁律·写死不要动】** 强制默认挂根模块 `0`（`/`）。**严禁**调用 `list_modules` 自行挑选任何子模块（如**绝对严禁**选择“公共板块”等）！ |
| `build` | `trunk`（**必填**，置空静默失败） |
| `type_name` | `代码错误`（传**中文**名） |
| `severity` / `pri` | `3` / `2`~`3`（按影响面 / 紧急度自评，**不要问用户**） |
| `assigned_to` | 中文姓名直传，如 `赵浩源` |
| `story` | 需求标题关键字或 ID，如 `审批单列表` / `18058` |
| `keywords` | `<用例编号>,<要点1>,<要点2>` |
| `title` | `【模块】验证点：现象短语` | **【铁律】** 必须以【模块名称】开头（如 `【插单申请】...`），严禁使用其他前缀 |
| `keywords` | `<用例编号>,<要点1>,<要点2>` | 便于双向追踪溯源 |

**执行原则**：
1. **永不确认直接提交**（用户 2026-09-10 三次明确）；提交后**必须回读自检**。
2. **覆盖式提交正文**：正文必须全量覆盖落库，**严禁在正文中出现或遗留老旧的 `[步骤]`、`[结果]`、`[期望]` 等占位标记**。
## 常用调用示例
- 查询待验证缺陷：`list_resolved_bugs(product='40')`
- 导出 E2E 回归指南：`export_bug_regression_report(bug_id=65295, output_file='docs/regression_BUG65295.md')`
- 直派缺陷给开发：`assign_bug(bug_id=65295, assigned_to='段广', comment='请排查行键问题')`
- 回归不通过重新激活：`activate_bug(bug_id=65411, assigned_to='李科勇', comment=<结构化备注见下>, image_path='evidence/复测截图.jpeg')`
- **建单并关联需求**：`create_bug(title=..., steps=..., assigned_to='赵浩源', story='审批单列表', keywords='APS_SPLCG_SPDLB_0052')`
- **给存量单补关联需求**：`link_story_to_bug(bug_id=65532, story='审批单列表')`（也可直接传 `story='18058'`）
- **核对需求取值**：`find_story(ref='审批单列表')`；批量找：`list_stories(keyword='审批单列表')`
- **反查某单关联了谁**：`find_story(bug_id=65548)` → `#18058 审批单列表（需求状态 active）`

## 建单踩坑速记（完整版见 `references/submission-standard.md`）
- **必填项**：`title` / `pri` / `severity` / `type` / `openedBuild`。`openedBuild` 默认 `trunk`（主干），**置空会导致建单失败**。
- **相关需求**：禅道建单接口对 `story` 支持不稳定，工具内部改为「先建单，再 `PUT /bugs/{id}` 补关联」，并在返回信息中回显关联结果。
- **所属模块（强制死规矩·写死不要动）**：**建单/改单时一律不传 `module` 参数**（或显式传 `""`），保持默认根模块 `0`。产品 SCM 下并无业务子模块映射，所有缺陷统一归入根模块 `/`。**严禁调用 `list_modules` 去寻找或自作主张挑选任何看似相关的子模块（如绝对严禁填写“公共板块”等）！**
- **检索漏检已修复**：`GET /products/{id}/bugs` 会**忽略 `title=` 过滤参数**且默认只返回首页（SCM 下 400+ 条 BUG、3377 条需求），旧版 `search_bugs` / `list_resolved_bugs` 会大面积漏检；现已全量分页后本地过滤，输出会回显「已扫描 全量 N/N 条」。
  ⚠️ 关键词可能因**标点断开**而搜不到（标题「点击「重置**」**未恢复排序」，搜 `重置未恢复排序` 为 0 命中）——用**最短特征子串**。
- **多图串行**：`attach_image_to_bug` 并发调用会各自重写 steps 正文而互相覆盖，必须逐张串行。
- **`steps` 是全量覆盖，不是追加**：补写/重写正文时**必须把原正文的 `<img>` 一并带上**，否则旧截图被静默删除。
  MCP 已内置丢图告警——返回串出现 `[警告] 本次 steps 覆盖将【移除 N 张原有图片】` 时必须立即把图补回。
- **正文 HTML 只用白名单标签且必须闭合**（`p/b/i/ul/ol/li/pre/table/tr/th/td/h3/img`），**不要嵌套同类标签**（`<b><code>…</code></b>` 会错位丢字）；代码/报文一律用 `<pre>`。
- **界面事实必须实测确认**：页签名、条数、字段值先读一次再写进正文 —— 实测把 4 条数据所在的页签写成「已办」（实际是「待办」，已办是 20+ 条不同记录）。
- **超时预算**：MCP 侧 `HTTP_TIMEOUT=12s` / `RETRY=1`（只对连接类失败重试），必须短于客户端 60s 上限；**禅道本身极快**（读单 0.08s、上传 600KB 0.09s），遇超时先怀疑 MCP 侧。
- **改了 `zentao_mcp_server.py` 必须重启 MCP 服务才生效**（kill zentao 进程树 → `dsh-mcp-client` 自动重连；用 `find_story` 返回 `unknown tool` 判定是否仍是旧进程）。
- **必须走浏览器表单时**：填表顺序须为 **所属项目 → 影响版本 → 相关需求 → 其余**（`#execution` 的 `onchange=loadExecutionRelated` 会清空「相关需求」）；Chosen / labelSelector 等自定义控件的底层 `select` 是 `display:none`，**不能直改 select.value**（会被控件状态覆盖），必须走控件点击。

## 备注格式规范（强制）
所有写入禅道的文本（`activate_bug` / `close_bug` / `comment_bug` 的备注，`create_bug` 的步骤正文）**一律使用 Markdown 结构化格式**，禁止纯文本堆砌（如【】+编号列表的"文本墙"）。用户已就此明确返工过一次（BUG#65411），效果基准即该单 2026-09-10 返工版激活备注。

**标准骨架**（回归/复测类备注）：
- `## 标题`：结论一句话 + 时间 + 环境，如「回归结果（2026-09-10 17:45-17:55，demo18）」
- 首段：核心现象一句话 + 数据基线（总数/各状态数）
- **对比表格**：`| 条件 | 实测返回 | 判定 |`，判定列用 ❌ / ✅，清单式数据优先入表
- `## 小节标题`：根因与证据分点
- `-` 列表 + **加粗**关键结论词（如"**前端**：..."、"**关键排除**：..."）
- 末尾附证据截图（走 image_path 参数内嵌）

**实现要点**：禅道按 HTML 渲染备注（无 markdown 服务端渲染），提交 Markdown 语义的等价 HTML（`<h2>` / `<table border="1" cellspacing="0" cellpadding="6">` / `<ul><li>` / `<b>` / `<p><img .../></p>`）即得同款渲染效果。已发备注可原地返工：`action-editComment-{actionId}.html` 表单 `{lastComment, uid}`（actionId 取详情页"修改备注"表单或 REST actions[].id；注意编辑会把该动作时间刷新为编辑时刻）。
