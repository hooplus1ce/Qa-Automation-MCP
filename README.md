# Qa-Automation-MCP

面向 AI 与测试工程的通用 UI 自动化 MCP 服务。框架能力不绑定具体组件库：

- **通用浏览器自动化**：Chrome/CDP、BrowserContext、页面与 iframe、语义 DOM 交互、截图和浮层观测。
- **可选组件适配器**：VTable 位于 `qa_automation/components/vtable/`，只负责页面中 VTable 组件的确定性分析与交互。
- **FastMCP 组合服务**：浏览器、通用 UI、诊断、演示和 VTable 资源分别实现为聚焦子服务器，由统一入口组合。
- **内置演示数据**：位于 `qa_automation/mcp/apps/sample_data.py`，不在项目根目录散落运行时 Python 模块。

## VTable 实例 API 文档

对运行中的 VTable 实例做 **JS 实时枚举**(296 个方法 + 55 getter + 33 setter),
再与官方 `@visactor/vtable@1.26.2` 的 TypeScript 类型声明逐一对齐,生成
[`docs/vtable-instance-api.md`](docs/vtable-instance-api.md):

- 每个方法含 **签名 / 作用 / 参数(逐参数释义)/ 返回值**;
- 按功能分 18 类(读取判定 / 编辑写回 / 数据记录 / 列宽 / 滚动视口 / 选区 / 合并 / 树形 / 冻结 / 导出 / 主题 / 事件 / 坐标命中…);
- 标注 **@AI** 的方法是本 MCP 交互工具的核心依赖(如 `getCellRelativeRect` / `getSelectedCellRanges` / `scrollToCell`)。

## 快速开始

```bash
# 1. 安装依赖(自动创建 venv)
uv sync

# 2. 以 stdio 启动 MCP 服务(供 MCP 客户端连接,也是 fastmcp.json 的默认配置)
uv run fastmcp run fastmcp.json

# 等价的直接入口
uv run qa-automation-mcp

# 3. (可选)启动 FastMCP Apps 开发预览:浏览器打开 http://127.0.0.1:9090
#    该命令仅用于 UI 预览,会为预览临时启动一个 HTTP MCP 端点
#    选择工具 -> Launch 即可看到渲染的 UI
uv run fastmcp dev apps qa_automation/mcp/server.py --dev-port 9090 --mcp-port 9000

# 4. (可选)使用 MCP Inspector 检查 stdio 服务
npx @modelcontextprotocol/inspector uv run fastmcp run fastmcp.json

# (可选)启用 Playwright 浏览器交互工具
uv sync --extra browser
uv run playwright install chromium   # 首次需下载浏览器内核
```

### 使用 iPhone SSH 端口转发访问 Inspector v2

Inspector v2 的 Web UI 使用 `6274`;MCP Apps 沙箱固定使用 `6275`。在 iPhone 的 SSH 客户端中建立两个本地转发:

```text
本地 6274 -> Ubuntu 127.0.0.1:6274
本地 6275 -> Ubuntu 127.0.0.1:6275
```

然后在 iPhone 浏览器打开脚本输出的 URL:

```text
http://127.0.0.1:6274/?MCP_INSPECTOR_API_TOKEN=<终端输出的token>
```

SSH 端口转发本身不执行 TLS;这里使用 loopback 地址访问,浏览器会将其视为可信来源,因此 v2 所需的安全上下文 API 可以工作。不要把 Inspector 直接绑定到公网 IPv6 地址:它具备调用本地 MCP 进程的能力,应保留 token 认证并限制为 SSH 隧道访问。

### Windows 局域网客户端

Windows 10/11 通常自带 OpenSSH Client。在远端启动 MCP Inspector 后,可在 Windows PowerShell 中执行:

```powershell
ssh.exe -N `
  -L 6274:127.0.0.1:6274 `
  -L 6275:127.0.0.1:6275 `
  hooplus1ce@192.168.31.21
```

如果私钥不是默认路径,追加 `-i $env:USERPROFILE\.ssh\id_ed25519`。保持该 PowerShell 窗口运行,再在 Windows 浏览器打开:

```text
http://127.0.0.1:6274/?MCP_INSPECTOR_API_TOKEN=<Ubuntu终端输出的token>
```

其中 `6274` 是 Inspector Web UI,`6275` 是 MCP Apps 沙箱。不要使用 v1 的 `6277` 端口,也不要在 Windows Inspector 表单中选择 STDIO; MCP 服务进程已经由 Ubuntu 上的 Inspector v2 启动。

## 工具与资源清单

| 名称 | 类型 | 说明 |
|---|---|---|
| `test_case_table` | UI 工具 | 测试用例 DataTable(搜索/排序/分页) |
| `vtable_engine_dashboard` | UI 工具 | VTable 引擎概览仪表盘 + 优先级分布图 |
| `mold_master_view` | UI 工具 | 模具主数据只读视图(20 字段) |
| `case_execution_panel` | FastMCPApp UI | 用例执行台:表单提交 → 后端执行 → 落盘 |
| `mold_master_entry` | FastMCPApp UI | 样例数据驱动的动态录入表单 |
| `request_approval` / `choose` / 文件三件套 / `generate_prefab_ui` | Providers | 审批 / 选择 / 上传 / 生成式 UI |
| `browser_open` | 工具 | Playwright 打开浏览器并导航到目标页面 |
| `browser_start` | 工具 | 在指定端口启动受管 Chrome 并自动 CDP 接管(默认 9222) |
| `browser_connect` | 工具 | CDP 连接已有浏览器(默认 9222),复用已打开的页面 |
| `browser_session` | 工具 | 管理隔离 BrowserContext 会话,支持账号 Cookie 环境切换与 storage state |
| `browser_pages` / `browser_select_page` | 工具 | 列出并显式固定稳定 `page_id`,避免多标签页误操作 |
| `vtable_cell_click` | 工具 | 点击 VTable 单元格:确定性坐标 + trusted 输入 + 回读验证 |
| `vtable_cell_info` | 工具 | 读单元格值/类型/中心点/可见性,交互前后确认 |
| `vtable_cell_resolve` / `vtable_cell_click_by_field` | 工具 | 用内部 API 将字段+记录索引解析为地址并 trusted 点击 |
| `vtable_analysis` | 工具 | 一次读取列头/scenegraph 图标/值单元格交互和编辑器证据，返回顶层 viewport 坐标 |
| `interaction_chain` | 工具 | 交互工具链：传 actions 一次性批量执行 N 个动作（1 次调用替代 N 次往返），链尾统一观察一次（浮层 + URL 变化）；不传时返回紧凑页面分析供 AI 规划 |
| `ui_click` / `ui_interact` | 工具 | 统一页面交互：CSS → AX → XPath → 视口坐标，支持 AntD Portal 观察 |
| `ui_screenshot` | 工具 | 按元素定位器或顶层 viewport 矩形截取 PNG/JPEG 并保存到 `.qa-automation/screenshots/`，返回文件路径（不再回传整图 base64） |
| `ui_page_context` / `ui_analyze_scope` | 工具 | 低 token 页面上下文与聚焦层控件分析 |
| `overlay_scan` / `overlay_observe` | 工具 | 当前活动范围的浮层快照与事件监听 |
| `ui_snapshot` | 工具 | aria 快照(mode='ai' + boxes):AI 的"语义之眼",含 [ref]/[box](支持 `frame`) |
| `ui_profile` | 工具 | 返回当前 UI profile、定位器优先级和 VTable 验证策略 |
| `automation_metrics` | 工具 | 返回工具调用耗时、响应体积和上下文 token 估算 |
| `vtable_meta` / `vtable_read_cells` | 工具 | 表格元数据与矩形单元格批量读取 |
| `vtable_drop_files` | 工具 | Locator.drop 精确拖放文件到目标单元格(Playwright 1.60+) |
| `browser_close` | 工具 | 关闭 Playwright/受管 Chrome;外部 CDP 浏览器只断开连接 |
| `vtable://js/index` | 资源 | JS 脚本目录(JSON) |
| `vtable://js/{name}` | 资源 | 19 个 VTable JS 脚本(fast_bind、vtable_analysis、resolve_cell、read_cells …) |

FastMCPApp 后端工具(`execute_test_case` / `save_mold_master`)不暴露给客户端,
仅由 UI 表单经 `CallTool` 触发(职责分离),默认提交结果落盘到使用方项目的 `.qa-automation/data/`；可用 `QA_AUTOMATION_DATA_DIR` 覆盖。

客户端读取 vtable JS 脚本:

```python
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

transport = StdioTransport(
    command="uv",
    args=["run", "fastmcp", "run", "fastmcp.json", "--no-banner"],
    cwd="/path/to/Qa-Automation-MCP",
)

async with Client(transport) as client:
    index = await client.read_resource("vtable://js/index")       # 目录
    script = await client.read_resource("vtable://js/fast_bind")  # 脚本
```

常见 MCP 客户端的进程配置等价于:

```json
{
  "command": "uv",
  "args": ["run", "fastmcp", "run", "fastmcp.json", "--no-banner"],
  "cwd": "/path/to/Qa-Automation-MCP"
}
```

## 关键实现要点

1. **表单提交按钮**:必须 `Button("提交", buttonType="submit")`(不是 `type=`)。
2. **CallTool 参数**:不自动收集表单值,须显式插值:
   `arguments={"case_id": "{{ case_id }}", ...}`,key 与字段 `name=` 一致。
3. **动态字段进表单**:`with Form(...):` 内须显式组合
   `Column(children=[*fields, Button(...)])`。
4. **网络**:dev apps 从 cdn.jsdelivr.net 加载 prefab renderer,内网/离线环境
   可能加载失败。解决:下载到本地后用代理/路由拦截(见 e2e 脚本思路)。

## Playwright 交互工具的设计(替代旧 click_by_js)

`vtable_cell_click` 按官方 Playwright MCP 的范式重写了点击工具,解决旧
`click_by_js`(坐标 + dispatchEvent 合成事件)的准确率问题:

- **确定性实例绑定**:VTable 容器元素自带 `__vtable__` 指向表格实例(实测官方 demo
  直接命中 `el.__vtable__`,含全部交互 API),绑定顺序为
  `容器/ canvas 的 __vtable__ 直连` → `React Fiber 绝对路径` → `BFS 全树扫描`,
  不再依赖脆弱的固定 Fiber 路径。
- **语义目标输入,确定性坐标解析**:AI 传 col/row,坐标由 VTable 内部 API
  (`getCellRelativeRect`)计算并加 canvas 视口偏移,不是猜测像素。
- **业务字段寻址**:`vtable_cell_resolve(field, record_index)` 优先调用
  `getCellAddrByFieldRecord`,旧版本才组合 `getTableIndexByField` 与
  `getTableIndexByRecordIndex`;`vtable_cell_click_by_field` 再用
  `scrollToCell` / `getCellRelativeRect` 得到落点。整个链路不扫描 DOM 单元格,
  AI 也不提供像素坐标。虚拟滚动产生的非有限几何和高 DPI canvas 尺寸已做防御。
- **统一 VTable 分析与执行**:`vtable_analysis` 一次读取有限列头、已渲染的
  `scenegraph.getCell` / `globalAABBBounds` 图标和值单元格。它还通过
  `getEditor` 和 `editCellTrigger` 标示可点击单元格及单击是否会打开原生输入控件。
  Playwright 将 VTable canvas 局部几何和 iframe 偏移换成顶层 viewport 坐标；把返回的
  `geometry.point` 与 `analysis_id` 一起交给
  `ui_interact(action="click", x=..., y=..., analysis_id=...)`。执行前会重新
  核对页面、iframe、scroll 和 scenegraph 几何，陈旧坐标返回 `stale-coordinate`。
  默认 `mode="interactive"` 只展开有交互证据的可见样本且不返回业务值；需要诊断时
  才使用 `mode="full"`、`include_values=True` 或 `visible_only=False`。
  同一活动 iframe 有多张可见表格时，首次调用只返回表格目录（`table_index`、位置、
  有限列头）；下一次显式带入 `table_index`，从源头避免跨表坐标与无关数据进入上下文。
  `customLayout/customRender` 只标为 candidate；只有 VTable 控件/editor 或
  scenegraph 的明确功能、`cursor:pointer` 证据才标为 confirmed/clickable。
  复选框等单元格内控件同样沿用“VTable API/scenegraph 取坐标 + 通用坐标点击”，
  不另设控件专用点击工具。
- **trusted 真实输入**:`page.mouse.click` 走真实输入管道(`isTrusted=true`),
  React/AntD 的 hover/focus/click 行为与真人一致;不再派发合成事件。
- **可操作性等待**:等 `.vtable` 挂载、`window._vtable` 绑定、滚动到视口、
  等渲染帧后才点击。
- **验证回路**:点击后依次检查选区变化、目标仍处于已选状态、编辑器、目标单元格
  scenegraph 的填充/描边等视觉状态；仍无证据时比较单元格中心的局部截图哈希，再决定
  是否重试。`verify=False` 时只执行 trusted 输入，不读取 scenegraph 或截图，也不返回
  空的视觉证据字段；默认验证开启时，截图只作为前述证据不足时的最终兜底。返回的是
  紧凑证据而非完整场景图或截图。
- **统一定位器**:页面控件优先使用 `ui_analyze_scope` 返回的 CSS；CSS 当前不可用时
  回退 AX `role/name/description`，再回退 XPath，最后才使用经 `analysis_id` 校验的
  顶层 viewport 绝对坐标。坐标回退仅对 click/dblclick/rightclick 生效,fill/press 等
  动作必须提供定位器,不会被静默降级成点击。`ui_click` 是常用点击入口,`ui_interact`
  覆盖 fill、press、select 等其他动作。

配合 **Playwright 1.60/1.62 新特性**形成完整的 AI 测试闭环:

### 推荐的技术分工

- **FastMCP 运行时**:本项目使用 Python Playwright 驱动共享浏览器,负责 MCP 调用、iframe/frame 选择、trusted 输入以及点击后的 Portal 事件采集。
- **官方 `@playwright/mcp`**:借鉴其 accessibility snapshot、ARIA 语义定位和结构化工具返回方式;它适合作为通用网页探索能力,不替代本项目的 Ant Design 浮层专用检测器。
- **官方 `@playwright/test`**:在独立的 Node/TypeScript 测试工程中承担稳定回归,用 fixtures 隔离登录态,用 projects 覆盖 Chromium/Firefox/WebKit,并在 CI 运行 trace/HTML report。不要把 Playwright Test 的 worker 生命周期直接复用为 FastMCP 的全局 singleton。

### Profile、指标与真实页面回归

- `ui_profile` 暴露 `aps-antd` 当前配置：活动 Tab iframe 选择器、Portal/下拉选择器、定位顺序
  (CSS → AX → XPath → text/placeholder → coordinate) 以及 VTable 视觉验证顺序。可用
  `QA_AUTOMATION_PROFILE` 和 `QA_AUTOMATION_ACTIVE_IFRAME_SELECTOR` 配置；当前 profile 只在服务进程内解析一次。
- 所有浏览器/UI/VTable 工具的响应附带 `metrics`，`automation_metrics` 提供进程内最近调用和聚合统计，
  用于发现响应过大、跨 iframe 扫描过慢或工具重试异常。指标不落盘，服务重启后清空。
- APS 真实页面回归位于 `tests/e2e/aps_clean_changeover_spec.py`，默认不触碰浏览器；确认已在
  9222 端口打开“产品工艺 > 清洗改机设置”后执行：

  ```bash
  APS_E2E=1 uv run python -m unittest -v tests.e2e.aps_clean_changeover_spec
  ```

  物料替代明细页的双 VTable、编辑器和空白保存提示场景，需要先切换到对应模块，再增加
  `APS_E2E_DETAIL=1 APS_DETAIL_RUN=1`。空白保存仍由页面状态和
  `APS_E2E_VALIDATE_SAVE=1` 控制，避免误提交业务数据。

  CI 中该回归不随 push/pull request 运行：`.github/workflows/ci.yml` 提供
  `e2e` 作业，仅在 **手动触发(workflow_dispatch)** 时于能访问 APS 系统的自托管
  runner 上执行，运行前会先做 CDP 连通性预检。触发时可配置 `aps_cdp_url` 与
  `e2e_runner`，勾选 `run_detail` 一并跑物料明细页场景。前置条件由人工保证：
  runner 本机 Chrome 已用 `--remote-debugging-port=9222` 启动并停留在目标模块
  页面；未停留在目标模块时测试自动 skip 而非失败。`APS_E2E_VALIDATE_SAVE`
  会提交业务数据，保持仅限本机手动执行，永不进入 CI。

- `ui_snapshot`:`page.aria_snapshot(mode='ai', boxes=True)` 把 accessibility
  树(含 `[ref=xx]` 元素引用与 `[box=x,y,w,h]` 视口坐标)喂给 AI —— 官方 Playwright
  MCP 的"语义之眼"。VTable 本体是 canvas(单元格不进 a11y 树,仍走确定性几何定位),
  但工具栏/弹窗/编辑器输入框全在树里,AI 先读快照再决定交互目标。
- `ui_screenshot` 使用 Playwright 的页面裁剪能力截取指定元素的实际可见区域；定位器沿用
  `ui_interact` 的 CSS → AX → XPath → text/placeholder 顺序，并支持 `padding`。VTable 单元格
  或分析返回的坐标可传 `x/y/width/height` 做顶层 viewport 截图；默认 `max_bytes=2MB`，
  超限时返回 failed 且只保留文件。截图一律落盘到 `.qa-automation/screenshots/`，响应只
  返回文件路径 `path`（不回传 base64），需要看像素时直接打开该文件，避免大图撑爆上下文。
  结果提供 `digest`，适合交互前后视觉比对。
- `vtable_meta` / `vtable_read_cells`:先用 VTable 内部 API 读规模
  (rowCount/colCount/冻结行列)与区域值,做到"先看全局再动手"。
- `vtable_drop_files`:`Locator.drop(payload, position=)`(1.60 新增)模拟 native
  拖放,position 由 `getCellRelativeRect` 换算成 `.vtable` 容器相对坐标,精确把
  文件落到目标单元格(图片/文件列上传)。

- **iframe 感知(表格在 iframe 里也照点不误)**:实测 WMS 应用把 VTable 渲染在
  `/static/old/scm-spo` 这个 iframe 里,而 `page.mouse` 用的是主页面视口坐标。
  `vtable_frame` 自动在页面所有 frame 中定位含 `.vtable` 的 frame(主 frame 优先),
  `vtable_cell_click` / `vtable_cell_info` / `vtable_meta` / `vtable_read_cells` /
  `vtable_drop_files` 内部自动走该 frame;`ui_click` / `ui_snapshot`
  支持 `frame` 参数:`frame=None` 主页面、`frame="vtable"` 自动定位含表格的 iframe、
  其它值按 iframe name 或 URL 子串匹配(如 `"application"` / `"scm-spo"`)。未找到显式
  frame 会返回失败,不会静默改点主页面。单元格坐标 = VTable `getCellRelativeRect`
  (canvas 相对)+ canvas 在 frame 内偏移 + iframe 元素在主页面视口偏移 —— 三条坐标
  空间拼接成 `page.mouse` 坐标,实测跨 iframe 点击命中、回读选中区间验证通过。
- **活动模块优先**:真实系统的二级菜单位于
  `.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe`。`frame="active"`
  会优先解析这个 iframe;`vtable_frame` 也会优先从活动模块查找 VTable,再回退到
  所有 frame。没有该 Tab 结构的页面仍可使用 `frame="vtable"` 或 name/URL 子串。
  这是业务页面 profile,不是通用 iframe 规则,可通过环境变量
  `QA_AUTOMATION_ACTIVE_IFRAME_SELECTOR` 覆盖。
- **Portal / 消息即时观测**:React Ant Design 的 Modal、Drawer、Dropdown、Select、
  Picker、Popover、Tooltip、Message、Notification 等通常追加到所属 iframe 的
  `document.body`,也可能由 `getPopupContainer` 追加到顶层文档。`ui_click`
  会在真实点击前为主文档和当前全部 iframe 安装 `MutationObserver`,点击后在
  `settle_ms`(默认 300ms,上限 2000ms)内返回结果。VTable 单元格则使用
  `vtable_cell_click(..., observe_after=True)` 走同一侦测器。
  交互期间新挂载的 iframe 会通过 `frameattached/framenavigated` 监听器和
  `add_init_script` 尽早注入观察器,排空阶段再做一次兜底扫描;因此短生命周期
  toast 即使在动态二级模块中创建也能保留事件证据。跨域 iframe 无法执行同源
  DOM 观察脚本,会在 `observer_errors` 中明确返回。
  `ui_events` 保留已经消失的短生命周期浮层,`overlays` 是相对点击前新增或文本变化的
  结果,`visible_overlays` 是结束时仍可见的结果。每项带 `frame_id` / `frame_url` /
  `frame_name`、`scope`、`kind`、文本、ARIA role、稳定 selector、所属 frame 的
  `box` 以及主页面 viewport 的 `page_box`。单独的 `overlay_scan` 只做当前
  状态快照,不适合捕获瞬时 toast。
  VTable 内嵌搜索编辑器的多个 `.virtual-option` 会合并为一个 dropdown，携带
  `option_count` 与最多三个 `option_preview`，避免候选项逐条占用上下文。
  `context.focus_layer` 会优先给出当前可交互的 Modal/Drawer/Dropdown/Popover;
  下一次控件分析应优先限制在该浮层内。
- **低 token 页面上下文**:`ui_page_context` 只返回 `page_id`、URL、标题、frame 摘要、
  活动 iframe、聚焦浮层和有限数量的可见 overlay。AI 每次交互前优先调用它,
  下一步使用 `ui_analyze_scope`:Modal/Drawer/Dropdown/Popover 可见时只返回该
  浮层内控件,否则只返回顶层当前文档与活动 iframe 的有限控件。只有诊断语义结构时
  才调用 `ui_snapshot`,避免反复传输无关 iframe 和整页 DOM。
  `overlay_scan` 默认 `scope="active"` 只看主文档和活动 iframe;
  没有活动 Tab iframe 结构时自动回退全量 frame,需要排查隐藏模块时可显式传
  `scope="all"`。所有 overlay 工具支持 `max_results`,
  默认最多返回 20 条。
  AntD 表单控件优先携带其 `Form.Item` 标签；同类控件的回退 CSS 会附加 `>> nth=N`，
  保证可直接用于严格定位。
  `overlay_observe(stop=False)` 会在下一次 observe 调用时推进 baseline 并清空已读事件;
  它适合连续诊断,而需要严格绑定“点击前/点击后”的场景应使用组合点击工具。
- **浏览器生命周期**:优先调用 `browser_start(port=9222, headless=false)` 启动受管
  Chrome;它会创建隔离的临时 profile、等待 `/json/version` 就绪并自动接管。启动前会
  预检端口:端口已有可用 CDP 端点(提示改用 `browser_connect` 复用)、被非 Chrome
  服务占用、或 Chrome 启动即退出(profile 被其他实例锁定)时,都会立刻返回明确原因
  与建议,不再空等超时。已有 Chrome 则调用 `browser_connect(port=9222)` 或传完整
  `cdp_url`;`browser_close` 会终止本服务启动的受管进程，但对外部 CDP 浏览器只断开
  连接。
- **多账号会话**:`browser_session(action="list")` 查看会话；`create` 创建新的
  BrowserContext(可传 `name` 和已有 `storage_state_path`)，`select` 切换当前
  Cookie 环境，`save` 将登录态保存为 Playwright storage state，`close` 关闭非默认
  会话。后续页面、iframe、浮层和 VTable 工具只作用于选中的会话和页面。若被接管的
  CDP Chrome 不允许创建上下文，工具会返回明确提示，此时为每个账号使用独立的
  `user_data_dir` 与端口调用 `browser_start`。
  多标签页先调用 `browser_pages`,再用 `browser_select_page(page_id)`
  显式固定当前页;之后页面、iframe、浮层与 VTable 工具不会再逐次猜页。

依赖 `playwright>=1.62`(可选,`uv sync --extra browser`);未安装时工具返回
可操作报错,不影响服务器其余功能。

## 部署与分发

`fastmcp.json` 是运行配置的单一来源。依赖环境只由外层 `uv run` 创建；
`fastmcp.json` 不声明第二个 UVEnvironment，避免重复派生 `uv run --skip-env`。

仓库根目录的 `.env.qa-automation.example` 是共享运行变量模板；本机复制为
`.env.qa-automation` 后由 `uv run --env-file` 显式加载。该文件属于 MCP 服务项目，
其中的产物路径保持相对；最终解析基准由 Agent 传入的使用方项目 `cwd` 决定。

```dotenv
# 禁用 Python 标准输出缓冲，保证 stdio MCP 消息立即发送给 Agent。
PYTHONUNBUFFERED=1

# 选择页面适配 Profile；aps-antd 是内置页面策略，不限制使用方项目类型。
QA_AUTOMATION_PROFILE=aps-antd

# 是否在浏览器页面中显示 MCP 模拟鼠标指针，支持 true/false。
QA_AUTOMATION_SHOW_CURSOR=true

# 单次浮层扫描最多返回的结果数量，至少为 1。
QA_AUTOMATION_OVERLAY_RESULT_LIMIT=20

# 所运行的使用方项目根目录；支持绝对路径（脱离 cwd 限制）或点号 . （默认基于当前工作目录）。
QA_AUTOMATION_PROJECT_ROOT=.

# 下载、截图、会话等产物根目录；相对路径基于使用方项目根目录。
QA_AUTOMATION_ARTIFACT_ROOT=.qa-automation

# FastMCPApp 执行记录目录；相对路径同样基于使用方项目根目录。
QA_AUTOMATION_DATA_DIR=.qa-automation/data
```

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `PYTHONUNBUFFERED` | `1` | 禁用 stdout 缓冲，避免 stdio MCP 消息延迟。 |
| `QA_AUTOMATION_PROFILE` | `aps-antd` | 选择浏览器页面定位与浮层适配策略；名称不限定使用方项目类型。配置了未知名称时会告警并回退到默认 `aps-antd`,不会导致服务启动失败。 |
| `QA_AUTOMATION_SHOW_CURSOR` | `true` | 控制浏览器页面中的模拟鼠标指针。 |
| `QA_AUTOMATION_PROJECT_ROOT` | `.` | 所运行的使用方项目根目录。支持绝对路径（优先级高于 `"cwd": "${workspaceFolder}"`，脱离进程当前工作目录限制）；若配置为 `.` 则基于进程当前 `cwd`。兼容历史别名 `QA_AUTOMATION_WORKSPACE_ROOT`。 |
| `QA_AUTOMATION_ARTIFACT_ROOT` | `.qa-automation` | 下载、截图、会话和浏览器 Profile 的统一产物根目录。 |
| `QA_AUTOMATION_DATA_DIR` | `.qa-automation/data` | FastMCPApp 执行记录和表单提交记录目录。 |

下文将 Agent 当前打开并使用该 MCP 服务的任意项目统一称为“使用方项目”；它不要求
特定业务类型，也不要求包含本 MCP 的源码或配置文件。

### 场景 A：直接打开并开发/测试本 MCP 项目自身 (OMP / VS Code / Trae)

在当前项目根目录下运行时，客户端默认就会以当前目录作为工作目录。此时仓库根目录
自带的 `.mcp.json` **不需要且严禁配置 `"cwd": "${workspaceFolder}"`**：
- OMP 的变量展开规则仅支持系统环境变量（`${VAR}` / `${VAR:-default}`），不支持
  IDE 专有的宏 `${workspaceFolder}`；若配置了未解析的 `${workspaceFolder}`，OMP
  会将其作为字面量传递给 Windows 底层 `CreateProcess`，导致报 `[WinError 123] 文件名、目录名或卷标语法不正确` 并连接失败；
- 省略 `cwd` 时，OMP、VS Code、Trae 均会天然使用当前项目根目录启动服务。

```json
{
  "mcpServers": {
    "qa-automation": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--project",
        "D:/Developer/Hoolinks/Qa-Automation-MCP",
        "--extra",
        "browser",
        "--env-file",
        "D:/Developer/Hoolinks/Qa-Automation-MCP/.env.qa-automation",
        "fastmcp",
        "run",
        "D:/Developer/Hoolinks/Qa-Automation-MCP/fastmcp.json",
        "--no-banner"
      ]
    }
  }
}
```

### 场景 B：在其他外部项目（如 APS 项目）中使用本 MCP 服务

当 Agent 打开外部项目时，可通过 Agent 平台的**全局用户配置**（例如 Trae 的
`AppData/Roaming/TRAE SOLO CN/User/mcp.json`）加载本服务。此时由 IDE 全局管理器
负责将 `${workspaceFolder}` 展开为用户当前正在使用的外部项目目录：

```json
{
  "mcpServers": {
    "qa-automation": {
      "command": "uv",
      "cwd": "${workspaceFolder}",
      "args": [
        "run",
        "--project",
        "D:/Developer/Hoolinks/Qa-Automation-MCP",
        "--extra",
        "browser",
        "--env-file",
        "D:/Developer/Hoolinks/Qa-Automation-MCP/.env.qa-automation",
        "fastmcp",
        "run",
        "D:/Developer/Hoolinks/Qa-Automation-MCP/fastmcp.json",
        "--no-banner"
      ]
    }
  }
}
```

配置核心关注点：
- `cwd: "${workspaceFolder}"`：仅在跨项目全局配置中生效，指向 Agent 当前打开的使用方项目；
- `QA_AUTOMATION_PROJECT_ROOT` 环境变量（推荐跨平台通用方案）：对于不支持 `"cwd"` 参数或不支持 `${workspaceFolder}` 变量展开的 Agent 客户端（如部分 CLI 工具或特定 IDE），可在 MCP 配置的 `"env"` 中直接添加 `"QA_AUTOMATION_PROJECT_ROOT": "D:/path/to/project"`，其优先级高于 `cwd`，所有产物均会自动落盘至该指定项目目录下；
- `uv run --project D:/Developer/Hoolinks/Qa-Automation-MCP`：指定 MCP 依赖和源码；
- `fastmcp run D:/Developer/Hoolinks/Qa-Automation-MCP/fastmcp.json`：官方声明式入口。
当前 FastMCP 3.4.6 的 filesystem source 实际相对进程 `cwd` 解析，而不是按配置
文件目录解析；因此本机 `fastmcp.json` 使用 MCP 服务文件的绝对路径，确保使用方
项目工作区作为 `cwd` 时仍可加载服务。启动命令为：

```bash
uv run \
  --project D:/Developer/Hoolinks/Qa-Automation-MCP \
  --extra browser \
  --env-file D:/Developer/Hoolinks/Qa-Automation-MCP/.env.qa-automation \
  fastmcp run D:/Developer/Hoolinks/Qa-Automation-MCP/fastmcp.json \
  --no-banner
```

所有持久化业务产物限制在使用方项目的 `${workspaceFolder}/.qa-automation/`：

```text
.qa-automation/
├── data/             # FastMCPApp 执行与提交记录
├── downloads/        # Playwright/CDP 浏览器下载
├── screenshots/      # ui_screenshot 显式截图
├── sessions/         # browser_session storage state
└── browser-profile/  # browser_start 受管 Chrome Profile
```

截图结果落盘到使用方项目工作区 `.qa-automation/screenshots/` 并返回文件路径 `path`，
不再回传 base64（如集成视觉模型需要像素，可自行读取返回的文件）；下载目录优先通过
Chromium CDP 配置，并保留 Playwright download 事件持久化作为退路。`browser_session`、
`vtable_drop_files` 接受工作区内相对或绝对路径，越出使用方项目工作区的路径会被拒绝。

## 项目结构

FastMCP 官方不强制唯一目录结构；官方文档推荐用 `fastmcp.json` 作为配置真源，并通过
聚焦服务器组合或按功能组织组件。本项目采用组合服务器模式，保留现有公共工具名，避免
namespace 造成破坏性重命名：

- [Project Configuration](https://gofastmcp.com/deployment/server-configuration)
- [Composing Servers](https://gofastmcp.com/servers/composition)
- [Filesystem Provider directory conventions](https://gofastmcp.com/servers/providers/filesystem)

```text
Qa-Automation-MCP/
├── fastmcp.json                     # FastMCP 声明式运行配置
├── .mcp.json                        # Agent Host 项目级自动发现
├── .env.qa-automation.example       # MCP 共享运行变量模板
├── pyproject.toml                   # Python/uv 项目元数据
├── qa_automation/                   # 通用 UI 自动化测试框架
│   ├── workspace.py                 # 使用方项目工作区与产物路径边界
│   ├── browser.py                   # Chrome/CDP/Context/Page 生命周期
│   ├── interaction/                 # DOM 定位、交互、快照与证据契约
│   ├── overlay/                     # Portal/ARIA 浮层观测
│   ├── profiles.py                  # 页面 Profile 与定位策略
│   ├── components/
│   │   └── vtable/                  # 可选 VTable 组件适配器及 JS 资源
│   ├── mcp/
│   │   ├── server.py                # FastMCP 组合根和 stdio 入口
│   │   ├── servers/                 # 浏览器/UI/VTable/诊断/演示子服务器
│   │   ├── resources/               # MCP 资源
│   │   ├── apps/                    # FastMCP Apps 与演示数据
│   │   └── metrics.py               # 工具可观测性
│   └── assets/                      # 框架运行资产
├── tests/
└── docs/
```

## 替换为真实数据 / 真实执行

- **数据**:编辑 `qa_automation/mcp/apps/sample_data.py` 的 `TEST_CASES` / `MOLD_MASTER_FIELDS`,保持同构即可。
- **真实执行**:`execute_test_case` 目前是模拟执行。接入真实浏览器时,可先调
  `browser_open(url)` 打开页面,再按"AI 闭环"驱动:
  1. CDP 多页签先 `browser_pages` → `browser_select_page`;随后调用
     `ui_page_context` → `ui_analyze_scope`,只读取当前焦点控件;
  2. 表格调用 `vtable_analysis` 读取列头图标、值单元格交互和有限样本;已知字段时优先
     `vtable_cell_resolve` / `vtable_cell_click_by_field`,必要时再用 `vtable_read_cells`;
  3. `vtable_cell_click` 仍支持明确 col/row 的场景并 trusted 点击,需要验证 Portal 时传
     `observe_after=True`;DOM 按钮使用 `ui_click`,按需传 `frame`
     点 iframe 内工具栏/弹窗按钮并立即读取浮层事件;
  4. `vtable_cell_info` 前后确认,`vtable_drop_files` 覆盖拖放上传;只有诊断时再取
     `ui_snapshot` 的完整 ARIA 子树;
  页面内的 VTable 脚本仍可通过 `client.read_resource("vtable://js/...")` 读取。
  整个流程由 AI 按 "语义目标 → 确定性解析 → trusted 操作 → 回读验证" 闭环完成。
  5. 真实物理拖拽使用 `ui_mouse_drag(start_x, start_y, end_x, end_y)`，使用底层事件流
     （起点按下 mousePressed → 24+ 步连续细密轨迹移动 mouseMoved → 终点释放 mouseReleased）
     驱动，并带全流程虚拟光标反馈。调用前必须先获取待拖拽对象的起始坐标与目标位置的结束坐标：
     - Canvas 列表/表格（如 VTable 列重排、列宽调整）：先通过 `vtable_analysis` 获取
       待移动列头与目标列头的 `point` 视口坐标；
     - 常规 DOM 元素或滑块：先通过 `ui_analyze_scope` 或 `ui_snapshot` 获取目标元素的
       `page_box` 视口中心坐标。

截图边界：`ui_screenshot` 只有在客户端明确调用时才执行页面截图，并保存到使用方项目
工作区的 `.qa-automation/screenshots/`，响应只返回文件路径 `path`（不携带 base64）。
普通点击不会隐式落盘；VTable 点击校验使用的微型图像只保留在内存。Playwright 页面
截图通常不会修改 DOM 或滚动页面，但有头硬件加速模式仍可能发生短暂合成器同步；对
VTable 点击请使用 `verify=False`，需要视觉诊断时再显式调用 `ui_screenshot`。
