---
name: tencent-sheet-style-augmentation
description: 腾讯文档表格富文本样式增强与全维度视觉元数据提取（强化现有腾讯文档 MCP 工具仅能获取纯文本的缺陷，毫秒级提取删除线、字体颜色、背景高亮、加粗、合并单元格及功能区块）
metadata:
  version: 2.0.0
  framework: fastmcp-3.0+
---

# Tencent Sheet Style Augmentation — 腾讯文档表格全维度样式增强能力

## 1. 核心定位与设计初衷

本 Skill 旨在为当前 **腾讯文档 MCP 工具集（`tencent_sheet_*`）** 提供**通用的富文本样式感知与视觉元数据增强层**。

### 痛点背景：官方 OpenAPI 的“纯文本盲区”
腾讯文档官方 OpenAPI（如 `sheet.get_cell_data`）是面向纯字符数据的接口：
1. **样式完全丢弃**：接口下发的所有单元格只保留纯字符串数值（`cells[].string_value`），中划删除线、文字颜色、加粗、单元格背景色全部被剥离；
2. **合并单元格“幽灵空值”**：对于跨行跨列的合并单元格，OpenAPI 仅在左上角单元格保留数值，其余合并区域全部返回空字符串 `""`，严重误导自动化下游对数据完整性的判断；
3. **视觉评审信息不可见**：业务人员、架构师在评审中通过“红字修改”、“黄色背景高亮”、“中划线删除”所做的关键标注，原生 MCP 工具完全“看不见”。

### 解决机制：V8 内存数据模型抽取（0.6ms ~ 2ms）
利用无头浏览器（Playwright）加载文档，直接穿透至 Chromium V8 引擎中腾讯文档运行时维护的完整数据对象网格：
`window.SpreadsheetApp.workbook.activeSheet.cellDataGrid`
在纯内存中完成全表样式特征提取，**无需任何视觉截图，零图片 Token 消耗，单表毫秒级响应，100% 确定性输出**。

---

## 2. 全维度样式特征矩阵 (Full Style Matrix)

本能力将腾讯文档中所有视觉样式抽象为 4 大核心维度：

```
                      ┌─────────────────────────────────────────┐
                      │    Tencent Sheet Style Augmentation     │
                      └────────────────────┬────────────────────┘
                                           │
         ┌───────────────────┬─────────────┴─────┬───────────────────┐
         ▼                   ▼                   ▼                   ▼
  【文字排版层】       【背景填充层】       【网格结构层】       【语义区块层】
  - 中划删除线 (strike) - 背景高亮 (fillRgb) - 合并单元格 (mergeRef) - 功能区块标题识别
  - 字体颜色 (fontColor)- 状态警示 (红/黄/绿) - 跨列/跨行跨度 (span) - 表头与数据体分离
  - 加粗/字号 (b, sz)   - 分区底色 (蓝底表头) - 消除合并空值歧义     - 废弃/修改字段归类
```

### 维度 1：文字排版（Typography）
* **中划删除线（`font.strike: true`）**：
  - **业务语义**：明确作废、物理/逻辑删除、下线字段或废弃条款。
  - **典型案例**：如在系统重构后，全模块废弃 `operationType`（改由唯一Key自动判断），文档作者添加删除线。
* **字体颜色（`font.color.rgb`）**：
  - **红色系（`#FFFF0000` / `#FFD93025`）**：关键修订、接口参数变更、联调强校验、待确认风险点。
  - **绿色系（`#FF00B050`）**：新增字段、已确认可用、通过验收。
  - **灰色系（`#FF7F7F7F`）**：仅供参考、只读展示、历史留档。
* **文字加粗与字号（`font.b: true`, `font.sz`）**：
  - 关键字段、主键、二级分类表头的显式强调。

### 维度 2：背景填充（Cell Fills & Highlighting）
* **表头与分组底色**：
  - 区分主区块（深蓝 `#FF1F4E78`）、子功能标题（天蓝 `#FF5B9BD5`）、数据表头（浅蓝 `#FFD9EAF7`）。
* **业务状态高亮**：
  - **黄色/橙色底色**：待评审争议项、联调临时配置。
  - **红色底色**：重大阻塞缺陷、禁止调用警告。
  - **绿色底色**：联调测试通过。

### 维度 3：网格结构与合并单元格（Grid Structure & Merges）
* **跨行跨列合并（`mergeReference`）**：
  - 记录精确坐标：`{ start_row, end_row, start_col, end_col, row_span, col_span }`。
  - **价值**：将合并范围与文本内容绑定，彻底消除 OpenAPI 将合并单元格副格读为空白的歧义。

### 维度 4：语义区块提取（Semantic Outlines）
* 自动结合合并跨度（`col_span >= 3`）+ 背景颜色 + 加粗文本，自动识别出表格中的章节大纲（例如“接口基本信息”、“请求公共字段”、“业务请求字段”、“响应字段”、“业务规则”、“请求示例”）。

---

## 3. FastMCP 项目原生集成

本能力已作为第一公民内置到当前 FastMCP 服务架构中：

### 1. 暴露的 MCP 技能资源
FastMCP 通过 `SkillsDirectoryProvider` 自动暴露：
- `skill://tencent-sheet-style-augmentation/SKILL.md`
- `skill://tencent-sheet-style-augmentation/_manifest`

### 2. 暴露的 MCP 标准工具
任何 MCP 客户端可直接调用：
* **`tencent_sheet_scan_styles`**（主力工具）：
  - 输入：`sheet_name`、`tab_id`、`url_or_file_id`
  - 输出：全维度样式特征、区块大纲、作废字段与红字变更汇总。
* **`tencent_sheet_scan_deleted_fields`**（兼容别名）：
  - 重点输出被删除线划掉的字段清单。

---

## 4. 常见业务场景标准执行流程 (SOP)

### 场景 A：接口变更与作废字段审计（识别删除线与红字）
1. 连接表格或调用 `tencent_sheet_list_sheets` 锁定目标子表 Tab ID；
2. 调用 `tencent_sheet_scan_styles(tab_id=...)`；
3. 读取返回结果中的 `semantic_summary.deleted_fields`：
   - 将带删除线字段标记为**严禁组装 Payload / 已下线**；
4. 读取 `semantic_summary.modified_fields`：
   - 提取出所有红色字体标注的字段，重点比对联调环境报文。

### 场景 B：表格视觉高亮与待办/评审项提取（识别黄色背景与彩色文本）
1. 调用 `tencent_sheet_scan_styles(tab_id=...)`；
2. 检查 `fills.fill_color_groups`：
   - 筛选出非蓝色表头的突出背景色（如黄色 `#FFFF00`、橙色等）；
   - 输出“当前表格中人工标注的高亮重点单元格清单”。

### 场景 C：复合层级与合并单元格数据还原（消除空值陷阱）
1. 在常规读取 `tencent_sheet_read_cells` 遇到大面积空值时；
2. 调用 `tencent_sheet_scan_styles(tab_id=...)` 提取 `structure.merged_blocks`；
3. 将合并块的主文本映射回该矩形范围内的所有坐标格，实现数据完整性平铺。

---

## 5. 标准化增强输出报文格式

调用 `tencent_sheet_scan_styles` 时返回的标准化 JSON 格式：

```json
{
  "ok": true,
  "scan_elapsed_ms": 1.6,
  "dimensions": { "scanned_rows": 93, "scanned_cols": 26 },
  "typography": {
    "strikethrough_count": 9,
    "bold_count": 42,
    "font_color_groups": [
      { "color": "FFFF0000", "count": 39, "sample_cells": [...] }
    ]
  },
  "fills": {
    "fill_color_groups": [
      { "color": "FF5B9BD5", "count": 7, "sample_cells": [...] },
      { "color": "FFD9EAF7", "count": 38, "sample_cells": [...] }
    ]
  },
  "structure": {
    "merged_blocks_count": 24,
    "sections": [
      { "row": 3, "title": "接口基本信息", "col_span": 9 },
      { "row": 15, "title": "请求公共字段", "col_span": 9 },
      { "row": 21, "title": "业务请求字段", "col_span": 9 },
      { "row": 71, "title": "响应字段", "col_span": 9 }
    ]
  },
  "semantic_summary": {
    "deleted_fields": [
      { "row_index": 25, "code": "operationType", "name": "操作类型", "type": "Integer" }
    ],
    "modified_fields": [
      { "row_index": 29, "code": "bomVersion", "name": "BOM版本(组合唯一)" }
    ]
  }
}
```
