# 【已废止】禅道提 BUG 标准规范（v17.1 API，2026-09-09 版）

> ⚠️ **本文件已废止 —— 内容陈旧且与当前实现存在矛盾，请勿再参照执行。**
>
> **权威规范已迁移至**：`skills/zentao-bug-ops/references/submission-standard.md`
> （与 `.agents/skills/zentao-bug-ops/references/submission-standard.md` 完全一致）

## 为什么废止

本文件为 2026-09-09 版本（BUG #65478 时期），存在以下**会直接导致提单出错**的问题：

| 项 | 本文件（旧） | 当前实际 |
|---|---|---|
| 登录账号 | `lihaizhen` | `hujiabin`（由环境变量 `ZENTAO_ACCOUNT` 注入） |
| 服务地址 | `http://192.168.200.53/zentao` | 运行态为 `http://127.0.0.1:12306/zentao`（历史共享环境仍是前者） |
| 类型入参 | 英文 key `codeerror` | MCP 工具 `type_name` 传**中文**「代码错误」 |
| 需求（story）关联 | **完全未提及** | `create_bug(story=...)` 建单即关联；补关联用 `link_story_to_bug` |
| 模块解析失败 | 未说明 | 自动**降级挂根模块 0 并提示**，不终止建单 |
| 检索分页陷阱 | 未提示 | `GET /products/{id}/bugs` **忽略 `title=` 参数且只返首页**，必须走全量分页的 `search_bugs` |
| 章节编号 | 出现**两个 `2.`**，编号错乱 | — |
| curl / subprocess 踩坑 | 大段篇幅 | 已由 MCP 封装，无需再手工调 curl |

## 仍有价值、已迁入新规范的内容（不会丢失）

以下内容原出自本文件，已按原义保留在新规范的对应章节：

- **定级口径**（pri 定紧急度、由 AI 自评；severity 按影响面）→ 新规范 §3
- **「永不确认、直接提交」**的提速原则 → 新规范 §0
- **接口类问题必带 `[接口信息]` + 请求报文示例** → 新规范 §4
- **BUG 类型 Key 映射表**（codeerror / UserExperience / JMYHZX …）→ 新规范 §5
- **菜单路径一律用 `→`、禁用 `>`** → 新规范 §4
- **`POST /files` 表单字段名必须是 `imgFile`** → 新规范 §6

## 原始全文存档

本文件 2026-09-09 版全文可通过 git 取回：

```bash
git show c0e6b00:zentao_mcp/zentao-bug-report-standard.md
```
