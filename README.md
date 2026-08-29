# vtable-mcp

VTable 虚拟表格 MCP 服务器(独立分发版)

- **交互式 UI 工具**(FastMCP Apps):测试用例表 / 引擎仪表盘 / 动态表单 / 用例执行台,工具返回的不是文本而是渲染在对话中的可交互界面
- **VTable JS 脚本资源**:19 个逆向脚本(React Fiber 绑定、单元格判定、坐标定位、scenegraph 表头图标、字段/记录地址解析、紧凑语义分析、编辑落值、批量读值、拖放落点等)**内化于本项目**,通过 MCP 资源 `vtable://js/{name}` 暴露,客户端按协议读取
- **内置 providers**:Approval(审批门控)/ Choice / FileUpload / GenerativeUI,一行注册即用
- **零外部依赖**:数据全部内建于 `sample_data.py`(11 条调拨订单测试用例 + 模具主数据表单),解压即可运行

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
uv run python server.py

# 3. (可选)启动 FastMCP Apps 开发预览:浏览器打开 http://127.0.0.1:9090
#    该命令仅用于 UI 预览,会为预览临时启动一个 HTTP MCP 端点
#    选择工具 -> Launch 即可看到渲染的 UI
uv run fastmcp dev apps server.py --dev-port 9090 --mcp-port 9000

# 4. (可选)使用最新 MCP Inspector v2 检查 stdio 服务
#    Inspector 绑定 Ubuntu 回环地址;远程 iPhone 请通过 SSH 转发 6274 和 6275
bash scripts/run_inspector_v2.sh

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

Windows 10/11 通常自带 OpenSSH Client。在 Ubuntu 上启动 `bash scripts/run_inspector_v2.sh` 后,在 Windows PowerShell 中执行:

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
| `ui_click` / `ui_interact` | 工具 | 统一页面交互：CSS → AX → XPath → 视口坐标，支持 AntD Portal 观察 |
| `ui_screenshot` | 工具 | 按元素定位器或顶层 viewport 矩形截取 PNG/JPEG，返回裁剪框与 base64 图像 |
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
仅由 UI 表单经 `CallTool` 触发(职责分离),提交结果落盘到 `data/` 目录。

客户端读取 vtable JS 脚本:

```python
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

transport = StdioTransport(
    command="uv",
    args=["run", "fastmcp", "run", "fastmcp.json", "--no-banner"],
    cwd="/path/to/vtable_mcp",
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
  "cwd": "/path/to/vtable_mcp"
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
  顶层 viewport 绝对坐标。`ui_click` 是常用点击入口，`ui_interact` 覆盖 fill、press、
  select 等其他动作。

配合 **Playwright 1.60/1.62 新特性**形成完整的 AI 测试闭环:

### 推荐的技术分工

- **FastMCP 运行时**:本项目使用 Python Playwright 驱动共享浏览器,负责 MCP 调用、iframe/frame 选择、trusted 输入以及点击后的 Portal 事件采集。
- **官方 `@playwright/mcp`**:借鉴其 accessibility snapshot、ARIA 语义定位和结构化工具返回方式;它适合作为通用网页探索能力,不替代本项目的 Ant Design 浮层专用检测器。
- **官方 `@playwright/test`**:在独立的 Node/TypeScript 测试工程中承担稳定回归,用 fixtures 隔离登录态,用 projects 覆盖 Chromium/Firefox/WebKit,并在 CI 运行 trace/HTML report。不要把 Playwright Test 的 worker 生命周期直接复用为 FastMCP 的全局 singleton。

### Profile、指标与真实页面回归

- `ui_profile` 暴露 `aps-antd` 当前配置：活动 Tab iframe 选择器、Portal/下拉选择器、定位顺序
  (CSS → AX → XPath → text/placeholder → coordinate) 以及 VTable 视觉验证顺序。可用
  `UI_AUTOMATION_PROFILE` 和 `UI_ACTIVE_IFRAME_SELECTOR` 配置；当前 profile 只在服务进程内解析一次。
- 所有浏览器/UI/VTable 工具的响应附带 `metrics`，`automation_metrics` 提供进程内最近调用和聚合统计，
  用于发现响应过大、跨 iframe 扫描过慢或工具重试异常。指标不落盘，服务重启后清空。
- APS 真实页面回归位于 `tests/e2e/aps_clean_changeover_spec.py`，默认不触碰浏览器；确认已在
  9222 端口打开“产品工艺 > 清洗改机设置”后执行：

  ```bash
  bash scripts/run_aps_e2e.sh
  ```

  脚本默认只运行已确认的清洗改机设置场景；物料替代明细页的双 VTable、编辑器和空白保存提示
  场景需要先切换到该模块，再显式执行 `APS_E2E_DETAIL=1 APS_DETAIL_RUN=1 bash scripts/run_aps_e2e.sh`，
  其中空白保存仍由页面状态和 `APS_E2E_VALIDATE_SAVE=1` 控制，避免误提交业务数据。

- `ui_snapshot`:`page.aria_snapshot(mode='ai', boxes=True)` 把 accessibility
  树(含 `[ref=xx]` 元素引用与 `[box=x,y,w,h]` 视口坐标)喂给 AI —— 官方 Playwright
  MCP 的"语义之眼"。VTable 本体是 canvas(单元格不进 a11y 树,仍走确定性几何定位),
  但工具栏/弹窗/编辑器输入框全在树里,AI 先读快照再决定交互目标。
- `ui_screenshot` 使用 Playwright 的页面裁剪能力截取指定元素的实际可见区域；定位器沿用
  `ui_interact` 的 CSS → AX → XPath → text/placeholder 顺序，并支持 `padding`。VTable 单元格
  或分析返回的坐标可传 `x/y/width/height` 做顶层 viewport 截图；默认 `max_bytes=2MB`，避免
  图像响应无界膨胀上下文。结果提供 `digest`，适合交互前后视觉比对。
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
  `VTABLE_ACTIVE_IFRAME_SELECTOR` 覆盖。
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
  Chrome;它会创建隔离的临时 profile、等待 `/json/version` 就绪并自动接管。已有
  Chrome 则调用 `browser_connect(port=9222)` 或传完整 `cdp_url`;`browser_close`
  会终止本服务启动的受管进程，但对外部 CDP 浏览器只断开连接。
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

## 打包分发

```bash
bash scripts/package.sh          # 生成 dist/vtable-mcp-0.1.0.zip
```

接收方解压后:

```bash
uv sync                          # 安装依赖(需要网络)
uv run fastmcp run fastmcp.json  # MCP 客户端使用 stdio 连接

# (可选)仅用于浏览器预览 FastMCPApp UI
uv run fastmcp dev apps server.py
```

支持项目级 MCP 自动发现的 Agent 客户端可直接读取仓库根目录的 `.mcp.json`，并以
`vtable-ui-automation` 名称启动本服务。配置使用 stdio，启动时自动启用 `browser`
可选依赖；客户端进程的工作目录必须是本仓库根目录。若平台不会自动读取
`.mcp.json`，可将其中 `mcpServers.vtable-ui-automation` 原样导入其 MCP 设置。

## 项目结构

项目按 FastMCP 的组合服务器模式组织。根 `server.py` 只负责创建服务器和启动
stdio；`fastmcp.json` 是运行配置的单一来源。各领域模块创建独立的本地 FastMCP
子服务器，由 `mcp_server.factory.create_server()` 无 namespace 挂载，因此已有工具名
和资源 URI 保持兼容。

```text
vtable_mcp/
├── server.py                    # 稳定入口:server:mcp
├── fastmcp.json                 # FastMCP 声明式运行配置
├── .mcp.json                    # Agent Host 项目级自动发现
├── mcp_server/
│   ├── factory.py               # 组合根与官方 Providers
│   ├── app_ui.py                # FastMCPApp UI 与 app-only 后端工具
│   ├── demo_tools.py            # Prefab UI 演示工具
│   ├── browser_tools.py         # Chrome/CDP/Context 生命周期
│   ├── ui_tools.py              # DOM、iframe、Portal、截图工具
│   ├── vtable_tools.py          # VTable API 分析与交互工具
│   ├── system_tools.py          # Profile 与指标
│   └── resources.py             # vtable:// JS 资源
├── vtable_playwright.py         # Playwright/VTable 驱动实现
├── vtable_js.py                 # 浏览器侧 VTable 脚本
├── automation_profiles.py       # 应用定位策略
└── tool_metrics.py              # 工具可观测性
```

## 替换为真实数据 / 真实执行

- **数据**:编辑 `sample_data.py` 的 `TEST_CASES` / `MOLD_MASTER_FIELDS`,保持同构即可。
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

截图边界：`ui_screenshot` 只有在客户端明确调用时才执行页面截图，不会被普通点击隐式
触发。Playwright 的页面截图通常不会修改 DOM 或滚动页面，但浏览器在有硬件加速的有头
模式下仍可能发生短暂合成器同步，不能承诺所有环境绝对零闪烁；对 VTable 点击请使用
`verify=False`，对视觉诊断再显式调用 `ui_screenshot`。
