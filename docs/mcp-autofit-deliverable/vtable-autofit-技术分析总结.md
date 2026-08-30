# VTable 表头文本自适应列宽 — 工具链调用流程技术分析总结

> 场景：APS 管理平台（VTable + AntD + iframe 架构），通过 qa-automation MCP 驱动外部 Chrome（CDP 9222）。
> 本文沉淀 2026-08-30 产线管理页实测跑通的完整链路，并给出新工具（`vtable_autofit_columns`）的设计建议。

---

## 1. 目标定义

对指定 VTable 表格的每一列：

```
目标列宽 = max( 表头需要宽度, 最长单元格内容宽度 + 16 )
```

- **表头需要宽度** = 表头文本宽（`600 12px Arial,sans-serif` 加粗测量）+ 图标区 + 16px 水平内边距
- **内容宽度** = 该列所有记录渲染文本的最大宽度（`12px Arial,sans-serif` 常规测量）+ 16px 内边距

---

## 2. 现有工具链完整调用时序（跑通版）

### 阶段 0：环境接管与页面定位

| 步骤 | 工具 | 关键参数/要点 |
|---|---|---|
| 0.1 | `browser_connect` | `port: 9222`，接管外部浏览器，复用已打开页面 |
| 0.2 | `ui_page_context` | 枚举 frame；**注意 active_iframe 可能误报**，需交叉验证 |
| 0.3 | `ui_snapshot(frame="")` | 顶层文档快照，用 tablist 中 `[selected]` 确认真实活动页 |
| 0.4 | `vtable_meta(frame=...)` | 确认 VTable 已绑定：rowCount/colCount/frozenColCount/theme.padding |

### 阶段 1：坐标标定（整个链路的核心陷阱）

**陷阱：`vtable_analysis` 返回的 "top-page-viewport-css-pixels" 坐标实际是 iframe 内部坐标，未加 iframe 在顶层页面的偏移。** 直接拿去点击/拖拽会全部落空，甚至误触其他区域（实测曾误开新标签页）。

**修正方法**（通过 CDP `Runtime.evaluate` 在顶层执行，同源 iframe 可直接访问）：

```js
var f = document.querySelector('iframe#react_iframe_66240008');
var fr = f.getBoundingClientRect();              // iframe 在顶层视口的位置
var c = f.contentWindow.document.querySelector('.vtable canvas');
var r = c.getBoundingClientRect();               // canvas 在 iframe 内的位置
// 真实拖拽坐标 = vtable_analysis 报告坐标 + (fr.x, fr.y)
// canvas 绝对位置 = (fr.x + r.x, fr.y + r.y)
```

实测样例：`dpr=1.54, iframe=(169.998, 79.992), canvasAbs=(181.99, 291.55)`。
像素级验证：表头灰带设备像素 y=451..491 ÷1.54 = CSS 291.6 ✓。

补充：浏览器可能处于非标准 DPI 缩放（本例 1.54），截图像素 = CSS × dpr，做像素校验时必须除回。

### 阶段 2：数据采集与目标宽度计算（一次页面内求值完成）

VTable 实例挂在 iframe 的 `window._vtable`（可通过遍历 `contentWindow` 找含 `scenegraph` 的键定位）。以下 JS 一次返回全部决策数据：

```js
var w = document.querySelector('iframe#react_iframe_66240008').contentWindow;
var t = w._vtable;
var cols = t.options.columns;   // [{field, title, ...}] —— 权威列顺序
var recs = t.records;           // 数据行
var cv = document.createElement('canvas');
var ctx = cv.getContext('2d');

var plan = cols.map(function(cd, i) {
  var field = cd.field || '', title = String(cd.title || '');
  // 表头文本宽（表头是 600 加粗）
  ctx.font = '600 12px Arial,sans-serif';
  var hdrW = ctx.measureText(title).width;
  // 内容最长文本宽
  ctx.font = '12px Arial,sans-serif';
  var bodyW = 0, sample = '';
  for (var r = 0; r < recs.length; r++) {
    var v = recs[r][field];
    if (v === null || v === undefined) continue;
    var s = String(v);                    // 注意：时间字段是时间戳，渲染另有格式化
    var mw = ctx.measureText(s).width;
    if (mw > bodyW) { bodyW = mw; sample = s; }
  }
  var cur = t.getColWidth(i);
  return { col: i, field: field, title: title,
           cur: Math.round(cur),
           headerNeed: Math.round(hdrW) + iconAllowance(title, field),
           bodyNeed: Math.round(bodyW) + 16,
           target: 0, sample: sample };
});
JSON.stringify(plan);
```

**表头图标区宽度（iconAllowance）实测经验值**：

| 列类型 | 图标构成 | 追加宽度 |
|---|---|---|
| 常规排序列 | 排序16 + 冻结22 + 筛选12 + 间距 | **+96**（含16内边距） |
| 含下拉筛选列（如"创建组织"） | 上述 + 下拉12 | +116 |
| 仅筛选列（如"操作"） | 筛选12 | +40 |

校准依据：取一列原生宽度恰好完整显示表头的列反推 —— 数据状态（4字=48px）原生宽 144 = 48 + 96，公式成立。

### 阶段 3：串行拖拽执行（动作链）

- **顺序：从右往左**。右侧列先定型，左侧列边框位置不受影响。
- **拖拽调用**：`ui_mouse_drag`
  - `start = (该列右边框x, 表头垂直中线y)`，`end = (边框x + Δ, y)`
  - `steps=28, hold_ms=150, settle_ms=350`（28+ 步细密轨迹才能触发 VTable 的列宽拖拽阈值）
- **禁区**：右冻结列（`rightFrozenColCount=1`，约 116px）覆盖画布右缘，落入其中的边框不可拖。
- **每次拖拽后必须校验并重算**（Δ 不命中会使后续列边框累计偏移）：

```js
// 校验：宽度是否生效 + 列顺序是否被误动
var t = document.querySelector('iframe#react_iframe_66240008').contentWindow._vtable;
var out = t.options.columns.map(function(c,i){ return i+':'+c.field+'='+Math.round(t.getColWidth(i)); });
out.join(',');
```

### 阶段 4：结果验证

- 期望宽度逐列比对（`getColWidth`）
- 列顺序完整性比对（`options.columns` 的 field 序列）
- 可选：`ui_screenshot` 留档 + 像素扫描表头灰带确认无省略号

---

## 3. 实测踩坑清单（新工具必须内置防护）

| # | 坑 | 现象 | 防护措施 |
|---|---|---|---|
| 1 | iframe 坐标偏移 | 所有点击/拖拽落空 | 页面内求值实时取 `iframe.getBoundingClientRect()`，**绝不缓存** |
| 2 | 压点落表头文字区 | 触发**列移动/换序**而非调宽 | 起点必须精确压在列边框 ±4px 内，y 取表头中线 |
| 3 | 单列失败后连锁偏移 | 后续拖拽全部错位 → 连环误触列移动 | 每步拖拽后重读宽度/边框坐标，自适应重算下一步 |
| 4 | 画布未渲染的僵尸态 | JS 实例有数据但画布不画，元素 boundingBox 全 null | 前置健康检查：canvas rect 非空 + 像素灰带存在；异常则 `Page.reload` 自愈后重试 |
| 5 | 时间戳字段 | records 里是 `1787570459000`，渲染是格式化日期 | 对日期字段按渲染格式（如 `2026-08-23 10:23`）测量，或提供字段类型标注 |
| 6 | MCP 服务崩溃 | `MCP tool is not found` | 工具内部异常要捕获收敛为结构化错误，绝不让进程崩溃 |
| 7 | active_iframe 误报 | 分析了非活动页 | 绑定实例前用顶层 tab `[selected]` + frame URL 双重确认 |

---

## 4. 新工具设计建议：`vtable_autofit_columns`

### 4.1 工具签名

```json
{
  "name": "vtable_autofit_columns",
  "description": "对目标 VTable 一键完成列宽自适应（表头文本/单元格内容完全显现）。内部采集列元数据与文本测量 → 生成调宽计划 → 从右往左串行真实拖拽 → 每步校验自适应 → 输出最终报告。",
  "args": {
    "frame": "iframe 名称/URL 子串/'active'（默认自动定位）",
    "mode": "header | content | both（默认 both）",
    "columns": ["field名数组，缺省=全部可调列"],
    "extra_padding": 0,
    "min_width": 60,
    "dry_run": false,
    "max_retries_per_col": 2
  }
}
```

### 4.2 内部动作链（一次调用，流水串行）

```
[1] 绑定与健康检查
    └─ 定位活动 iframe（tab[selected]+URL 双确认）
    └─ 取 window._vtable；canvas rect 非空；僵尸态 → Page.reload 自愈重试(≤1次)

[2] 单次页面内求值 —— 生成计划（替代 N 次外部工具往返）
    └─ options.columns + getColWidth + records
    └─ canvas measureText：表头(600 12px) / 内容(12px)
    └─ 目标宽 = max(headerNeed, bodyNeed, min_width)
    └─ 同一求值内直接算出每列右边框的【绝对视口坐标】
       （getCellRelativeRect + iframe offset，页面内完成，天然规避坐标陷阱）
    └─ 输出 plan[]: {col, field, cur, target, delta, borderXY}

[3] dry_run=true → 直接返回 plan 终止

[4] 串行执行（从右往左；每列一个原子动作）→ 细节见 4.5

[5] 终检 + 报告
    └─ 全列宽度比对 + options.columns 顺序完整性
    └─ 返回 {plan, steps[], final_widths, order_intact, skipped[]}
```

### 4.3 关键实现要点

1. **测量与坐标全部下沉到页面内**：一次 `Runtime.evaluate` 完成采集+测量+坐标换算，外部只负责"拖拽"这一必须走真实输入管道的动作。这把本会话约 30 次工具往返压缩为 **1 次调用内的 N 个原子拖拽**。
2. **复用现有 trusted 拖拽管道**：`ui_mouse_drag` 的 24+ 步细密轨迹已被验证可触发 VTable 调宽阈值，新工具内部直接调用同一 CDP 输入通道。
3. **每步自适应重算**：详见 4.5。
4. **列移动熔断**：每步比对 `options.columns` field 序列，一旦检测到换序立即修复再继续，绝不带伤推进。
5. **僵尸态自愈**：绑定时校验 canvas 可见性，异常自动 `Page.reload` 一次后重走流程。

### 4.4 返回值结构（供 AI 复核）

```json
{
  "status": "ok",
  "adjusted": 12, "skipped": 2,
  "order_intact": true,
  "columns": [
    { "field": "createOrgName", "title": "创建组织", "before": 101, "after": 197,
      "basis": "content", "sample": "广东生和坛健康食品股份有限公司", "status": "ok" },
    { "field": "dataStatusName", "before": 144, "after": 144, "basis": "header", "status": "already-fit" }
  ],
  "steps": [ { "col": 14, "from": [1579.99,305.55], "to": [1632.99,305.55],
               "width_before": 95, "width_after": 149, "retries": 0 } ]
}
```

### 4.5 「每步自适应重算」具体代码逻辑

#### 4.5.1 设计原则

"每步自适应重算"的本质是一个 **闭环控制回路（闭环反馈控制）**：

```
计算目标 → 拖拽执行 → 量测实际 → 误差反馈 → 修正下一步
```

三条硬规则：

1. **坐标永不缓存跨步复用**：任何一次拖拽后，表格布局（列宽/列位置/边框坐标）都可能变化，下一步的起点必须重新量测。
2. **一次求值拿全部状态**：宽度和边框坐标在同一次页面内求值中返回，避免"读了宽度、坐标又变了"的竞态。
3. **误差不等累积**：单列误差 ≤2px 视为命中（VTable 拖拽步进粒度所致），超过则当步重试，绝不让误差传导到下一列。

#### 4.5.2 页面内求值：`state_probe`（每步拖拽前后各调一次）

注入 iframe 所属顶层页面执行（同源 iframe 可直取）。单次求值返回 **宽度 + 列序 + 真实边框绝对坐标 + 表头中线** 四类决策数据：

```js
// __AUTOFIT_PROBE__ —— 返回当前表格全量状态（自适应重算的数据源）
(function () {
  var f = document.querySelector('iframe#react_iframe_66240008');
  if (!f) return JSON.stringify({ ok: false, reason: 'iframe-gone' });
  var w = f.contentWindow, t = w._vtable, d = f.contentWindow.document;
  if (!t || !t.scenegraph) return JSON.stringify({ ok: false, reason: 'vtable-gone' });

  // ① 僵尸态检测：画布必须真实渲染（有非零可见矩形）
  var canvas = d.querySelector('.vtable canvas');
  var cr = canvas ? canvas.getBoundingClientRect() : null;
  if (!cr || cr.width === 0 || cr.height === 0)
    return JSON.stringify({ ok: false, reason: 'canvas-dead' });

  // ② 坐标基准：每次求值实时获取，绝不缓存（DPR 变化/窗口滚动/布局抖动都由它吸收）
  var fr = f.getBoundingClientRect();

  // ③ 表头垂直中线（表头行高来自 theme，实测 28px；用 getCellRelativeRect 兜底）
  var headerRect = t.getCellRelativeRect(2, 0);          // 任取一个非冻结列的表头
  var headerMidY = fr.y + headerRect.y + headerRect.height / 2;

  // ④ 全列状态：宽度 + 列序 + 右边框绝对坐标（关键：由当前宽度实时累加推出）
  var cols = t.options.columns;
  var frozenLeft = 0;
  for (var fc = 0; fc < (t.frozenColCount || 0); fc++) frozenLeft += t.getColWidth(fc);

  var items = [], xCursor = null, rightFrozenW = 0;
  for (var rc = cols.length - 1; rc >= cols.length - (t.rightFrozenColCount || 0); rc--)
    rightFrozenW += t.getColWidth(rc);                    // 右冻结区总宽（边框禁区）

  for (var i = 0; i < cols.length; i++) {
    var wd = t.getColWidth(i);
    // 用 VTable 自身 API 取列左缘，而非外部累加 —— 消除浮点/隐藏列误差
    var rect = t.getCellRelativeRect(i, 0);
    var borderX = fr.x + rect.x + rect.width;             // 该列右边框的绝对视口 x
    var inDeadZone = (fr.x + rect.x + rect.width) > (fr.x + cr.width - rightFrozenW);
    items.push({
      col: i, field: cols[i].field || '', title: String(cols[i].title || ''),
      width: Math.round(wd),
      border: { x: +borderX.toFixed(2), y: +headerMidY.toFixed(2), draggable: !inDeadZone }
    });
  }
  return JSON.stringify({ ok: true, scrollLeft: t.scrollLeft || 0, items: items });
})()
```

要点：

- **边框坐标来自 `getCellRelativeRect(i,0)` 而非外部累加**——VTable 内部已处理冻结区、隐藏列、滚动偏移，直接取右缘即天然正确。
- **`inDeadZone`** 标记落入右冻结列覆盖区的边框（画布右缘减右冻结总宽以左），这类列直接进 `skipped`，杜绝盲拖。
- 返回 `canvas-dead` 时由外层触发 `Page.reload` 自愈流程。

#### 4.5.3 外层驱动循环：闭环控制（Python 伪代码，MCP 服务端）

```python
TOLERANCE = 2          # px：VTable 拖拽步进粒度内的误差视为命中
DRAG_STEPS = 28        # ≥28 步细密轨迹才能触发 VTable 调宽阈值

def autofit_loop(page, plan, max_retries=2):
    """plan: [{col, field, target, delta}] 按 col 从大到小（从右往左）排列"""
    steps_log = []

    for item in plan:                                   # 从右往左串行
        col, target = item["col"], item["target"]
        state = probe(page)                             # ← 每列开始前：全量重读状态
        if not state["ok"]:
            return heal_and_restart(page, reason=state["reason"])   # 僵尸态自愈

        cur = find(state, col)
        if abs(cur["width"] - target) <= TOLERANCE:     # 已达标，跳过
            steps_log.append(step(item, cur, status="already-fit", retries=0))
            continue
        if not cur["border"]["draggable"]:              # 右冻结区禁区
            steps_log.append(step(item, cur, status="skipped-frozen", retries=0))
            continue

        for attempt in range(1, max_retries + 1):
            # ── 核心：目标像素 = 当前真实边框坐标 + 仍差多少宽度 ──
            # 不是回放计划期算好的旧坐标！
            delta_now = target - cur["width"]
            start = (cur["border"]["x"], cur["border"]["y"])
            end   = (cur["border"]["x"] + delta_now, cur["border"]["y"])

            trusted_drag(page, start, end, steps=DRAG_STEPS,
                         hold_ms=150, settle_ms=350)    # 复用真实输入管道

            # ── 校验：一次求值同时拿宽度 + 列序 ──
            after = probe(page)
            if not after["ok"]:
                return heal_and_restart(page, reason=after["reason"])

            # 熔断：列序被误动（压点落入表头文字区触发列移动）→ 先修序再重试
            if order_changed(state, after):
                fixed = restore_column_order(page, expected=baseline_fields)
                steps_log.append(step(item, cur, status="order-restored",
                                      retries=attempt, detail=fixed))
                # 修序后布局全变，回到本列开头重走（不消耗业务列）
                cur = find(probe(page), col)
                continue

            new = find(after, col)
            steps_log.append(step(item, new, status="dragged",
                                  retries=attempt,
                                  width_before=cur["width"]))

            if abs(new["width"] - target) <= TOLERANCE: # 命中 → 进入下一列
                break

            # 未命中 → 下一轮 attempt 用 probe 的最新 border 重算 delta_now
            # （闭环：误差反馈进下一次起点，而非累计进后续列）
            cur = new
        else:
            # 重试耗尽：记录 degraded，继续后续列（带伤不推进 = 误差止步于此列）
            steps_log.append(step(item, cur, status="degraded-unreachable",
                                  retries=max_retries))

    return finalize(page, plan, steps_log)
```

#### 4.5.4 辅助函数要点

```python
def order_changed(before, after):
    """列序熔断判定：比对 field 序列（顺序敏感）。"""
    return [c["field"] for c in before["items"]] != [c["field"] for c in after["items"]]

def trusted_drag(page, start, end, **kw):
    """与现有 ui_mouse_drag 同一 CDP 输入通道：
    mousemove→press→24+步细密 move→release。坐标为顶层视口 CSS 像素。
    任何底层异常捕获为 StepError，绝不向 MCP 进程外抛。"""

def restore_column_order(page, expected):
    """把误移的列拖回原位：
    1. diff(after.fields, expected) 定位错位列 A（现位置 i，应在位置 j）
    2. probe 取 A 表头【单元格中心】坐标（注意：中心，不是边框！）
    3. 取目标位置 j 的列中心坐标，trusted_drag 拖过去（跨列拖拽触发换序）
    4. probe 复核 fields == expected，失败则再 diff 迭代（实测一次即成）"""

def heal_and_restart(page, reason):
    """僵尸态自愈：Page.reload → 等 .vtable canvas 可见 → 重建 baseline_fields → 重跑 autofit_loop（仅一次，防循环）"""
```

#### 4.5.5 闭环数据流图解

```
   ┌────────── 每列开始 ──────────┐
   │                              ▼
   │  probe() ──→ {width, border, fields, draggable}
   │      │                              │
   │      ├─ 已达标(≤2px) ──→ 下一列      │
   │      ├─ 禁区(draggable=false) → skip│
   │      ▼                              ▼
   │  delta_now = target − width    start = border 实测坐标
   │      └──────────┬───────────────────┘
   │                 ▼
   │          trusted_drag(start → start+delta_now)
   │                 ▼
   │           probe() 校验
   │           ┌──────┼─────────────┐
   │      命中 │   列序变了           │ 未命中(>2px)
   │           ▼      ▼              ▼
   │       下一列  restore_order   attempt+1（用最新 border 重算 delta）
   │                  │                └─ 耗尽 → degraded，仍进下一列
   └──────────────────┴──────────────────────────
```

三个关键不变量（实现评审时的检查项）：

| 不变量 | 含义 | 违反后果（实测翻车记录） |
|---|---|---|
| **I1 坐标即时性** | 每次拖拽起点都来自上一次 probe 的 border | 坐标累计偏移 → 连环误触列移动 |
| **I2 误差止步** | 误差在本列重试消化，绝不带进下一列 | 3 列顺序错乱（本会话实际发生） |
| **I3 顺序先行** | 检测到换序先修序、后调宽 | 在错误列上继续调宽，全盘皆错 |

---

## 5. 验收清单（新工具上线前）

- [ ] 非标准 DPI（1.25/1.5/1.54/2.0）下坐标全部命中
- [ ] 表头加省略号的列 → 调整后文本完整无 `…`
- [ ] 长内容列（15字公司名）→ 内容完整显示
- [ ] 连续 12+ 列调整，全程零列换序
- [ ] 单列拖拽失败时自动重试且不影响后续列
- [ ] 右冻结列边框正确标记 skipped 而非盲拖
- [ ] dry_run 返回的计划与实际执行结果一致
- [ ] 工具内部异常返回结构化错误，MCP 服务不崩溃
- [ ] probe 单次求值 < 50ms（20 列规模），整表 12 列调整全程 < 15s
