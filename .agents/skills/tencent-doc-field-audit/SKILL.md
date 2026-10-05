---
name: tencent-doc-field-audit
description: 腾讯文档接口字段富文本样式极速审计（解决 OpenAPI 丢失删除线与标红样式问题，基于 V8 内存数据模型 0.6ms 毫秒级提取废弃/修订字段）
metadata:
  version: 1.0.0
  framework: fastmcp-3.0+
---

# Tencent Doc Field Audit — 腾讯文档接口字段富文本样式极速审计

## Overview

在进行接口自动化测试、协议对接与字段对齐时，业务人员或架构师常在腾讯文档（Tencent Docs）在线表格中对字段进行富文本标记：
- **中划删除线（`font.strike: true`）**：明确作废、删除、不再传递的字段（例如接口改由唯一Key自动判断新增/修改后，将 `operationType` 整行划掉）；
- **红色高亮字体（`color: FFFF0000`）**：重点修订、格式调整或待确认字段。

### 核心痛点与解决对比

| 维度 | 常规 OpenAPI 读取 | 纯视觉截图辨认（旧方法） | **本技能：V8 内存模型提取（推荐）** |
| :--- | :--- | :--- | :--- |
| **样式捕获能力** | ❌ 丢失所有样式（纯字符串） | ⚠️ 易因高分屏缩放/黑细线误判漏判 | ✅ **100% 精确捕获 `strike` 与 `color`** |
| **Token 消耗** | 极低 | ❌ **巨量 Token（全屏图+局部图多次往返）** | ✅ **零图片 Token 开销** |
| **单表扫描耗时** | ~800ms | 15~30 秒（加载+截图+传输+OCR识别） | ✅ **0.6 ~ 1.5 毫秒** |
| **自动化批量能力** | 无法识别样式 | 人工/AI 视力极其疲劳，无法大批量运行 | ✅ **支持全文档 43+ 个子表批量秒级巡检** |

---

## FastMCP 内置技能架构

FastMCP (v3.0+) 引入了原生的 `SkillsDirectoryProvider`，本项目 `qa_automation.mcp.providers` 已将其直接接入服务根目录：

```python
from fastmcp.server.providers.skills import SkillsDirectoryProvider
# 自动扫描 skills/ 下包含 SKILL.md 的目录
providers.append(SkillsDirectoryProvider(roots=skills_root, reload=True))
```

### 暴露的标准 MCP 资源
1. **指令文档**：`skill://tencent-doc-field-audit/SKILL.md`
2. **资源清单**：`skill://tencent-doc-field-audit/_manifest`
3. **配套脚本**：`skill://tencent-doc-field-audit/scripts/scanner.py`

接入本 MCP 的任何 AI Client（Claude Code、Cursor、VS Code Copilot 等）均可自动感知并读取本 Skill 开展专业审计。

---

## 核心实现原理：0.6ms V8 内存抓取

腾讯文档前端是一个基于 HTML5 Canvas 绘制的大型单页应用，在浏览器加载完成后，底层数据模型完全暴露在 JavaScript 运行时中：
- 顶层句柄：`window.SpreadsheetApp.workbook`
- 当前活动表格数据网格：`window.SpreadsheetApp.workbook.activeSheet.cellDataGrid`
- 单元格读取方法：`cellDataGrid.getCellData(row_index, col_index)`

每个单元格对象的 `style.font` 属性完整记录了样式元数据：
```javascript
const cell = grid.getCellData(r, c);
const font = cell?.style?.font;
const isStruck = !!font?.strike; // 存在删除横线
const rgb = font?.color?.rgb?.toUpperCase();
const isRed = rgb === "FFFF0000" || rgb === "FFD93025"; // 红色字体
```
借助 Playwright 的 `page.evaluate()`，脚本直接在 Chromium 内存中循环遍历 100~200 行单元格，耗时仅需 **0.6 毫秒**。

---

## 极速审计工作流（SOP）

### 步骤 1 — 获取文档子表清单与 Tab ID
首先调用 MCP 工具 `tencent_sheet_list_sheets` 或连接文档，获取各模块接口对应的 `sheet_id`（Tab ID）：
```json
// 示例：生和堂APS系统外部接口对接文档
{ "sheet_id": "000004", "sheet_name": "01_物料主数据同步" },
{ "sheet_id": "000005", "sheet_name": "02_BOM同步" },
{ "sheet_id": "000007", "sheet_name": "04_销售订单同步" }
```

### 步骤 2 — 执行内存样式极速扫描
直接调用 FastMCP 提供的专用工具 `tencent_sheet_scan_deleted_fields`：

```json
{
  "sheet_name": "02_BOM同步",
  "tab_id": "000005"
}
```

响应示例（毫秒级返回）：
```json
{
  "ok": true,
  "scan_elapsed_ms": 1.2,
  "struck_rows": [
    {
      "row_index": 25,
      "index": 3,
      "code": "operationType",
      "name": "操作类型",
      "type": "Integer",
      "required": "是",
      "status": "已确认",
      "description": "本次业务数据操作语义"
    }
  ],
  "red_cells_count": 8,
  "red_cells": [
    { "row_index": 29, "col_index": 2, "value": "bomVersion", "color": "FFFF0000" },
    { "row_index": 30, "col_index": 2, "value": "bomVersionNo", "color": "FFFF0000" }
  ]
}
```

### 步骤 3 — 跨接口多子表批量巡检（秒级巡查全部模块）
当需要排查整套系统对接文档中所有接口的作废与变动字段时，运行批量扫描脚本（详见配套 `scripts/scanner.py`）：

```bash
uv run python skills/tencent-doc-field-audit/scripts/scanner.py --all
```

巡检引擎将按 Tab ID 逐个加载，并发或顺序提取并聚合生成全局《接口废弃与重点修订字段矩阵》。

---

## 样式判定规则与业务映射标准

| 样式表征 | 数据模型特征 | 业务含义 | 自动化测试 / 对接处理准则 |
| :--- | :--- | :--- | :--- |
| **中划删除线** | `font.strike === true` | **明确作废删除** | 1. 请求体 Payload 中严禁组装该字段；<br>2. 接口反向用例中校验传参是否会被后端拦截或忽略；<br>3. 数据库表结构标记为下线。 |
| **纯红色字体** | `font.color.rgb == "FFFF0000"` | **重点修订 / 待定 / 强校验** | 1. 重点比对 ERP 与 APS 两端字段命名与数据字典；<br>2. 检查联调环境是否已落地该修订。 |
| **仅类型标红** | 仅 `col_index=4` 标红 | **字段类型发生变更** | 注意历史数据兼容性（如 String 改为 Integer 枚举）。 |

---

## 标准审计交付单模板

完成扫描后，应输出如下格式的交付报告：

```markdown
### 接口字段样式审查报告：[子表名称] (Tab: [tab_id])
- **扫描耗时**：1.2 ms
- **文档链接**：https://docs.qq.com/sheet/...

#### 1. 确定作废删除字段（带删除横线）
| 序号 | 字段编码 | 字段名称 | 字段类型 | 确认状态 | 业务说明 | 变更说明 / 依据 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| 3 | ~~operationType~~ | ~~操作类型~~ | ~~Integer~~ | 已确认 | 本次业务数据操作语义 | 2026/8/24 评审：统一由唯一Key判断，不再传操作类型 |

#### 2. 重点修订 / 待确认字段（标红高亮）
| 序号 | 字段编码 | 字段名称 | 当前类型 | 标记特征 | 关注要点 |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 7 | bomVersion | BOM版本(组合唯一) | String | 全格标红 | 需核对是否与版本号合并 |
| 8 | bomVersionNo | 版本号 | String | 全格标红 | 需核对编码规则 |
```
