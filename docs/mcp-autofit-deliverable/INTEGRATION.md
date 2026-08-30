# vtable_autofit_columns 集成补丁(共 3 个注册点)

将 `autofit.py` 复制到 `Qa-Automation-MCP/qa_automation/components/vtable/autofit.py` 后,
按以下补丁完成注册。

---

## 补丁 1:组件导出

文件: `qa_automation/components/vtable/__init__.py`

在文件头部既有 import 区(建议放在 `from .verification import ...` 之后)追加:

```python
from .autofit import (
    _autofit_columns_impl,
    autofit_columns,
)
```

> 该文件无 `__all__`,直接导入即随包导出。

---

## 补丁 2:门面导出(facade)

文件: `qa_automation/__init__.py`

### 2a. 在 `from .components.vtable import (...)` 块中追加两行

```python
from .components.vtable import (
    _autofit_columns_impl,      # ← 追加
    autofit_columns,            # ← 追加
    _cells_read_impl,
    _click_cell_impl,
    _do_click,
    _drop_files_impl,
    _table_meta_impl,
    _trusted_viewport_click,
    cell_info,
    cells_read,
    click_cell,
    click_vtable_cell_by_field,
    drop_files,
    table_meta,
)
```

### 2b. 在 `__all__` 的 `# VTable` 分组中追加

```python
    # VTable
    "vtable_frame",
    "active_application_frame",
    "resolve_frame",
    "ensure_vtable",
    "cell_center",
    "cell_info",
    "click_cell",
    "resolve_vtable_cell",
    "click_vtable_cell_by_field",
    "table_meta",
    "cells_read",
    "drop_files",
    "vtable_analysis",
    "autofit_columns",          # ← 追加
```

---

## 补丁 3:MCP 工具注册

文件: `qa_automation/mcp/servers/vtable.py`

在 `vtable_drop_files` 工具定义之后、`return mcp` 之前追加:

```python
    @mcp.tool()
    @instrument_tool
    async def vtable_autofit_columns(
        frame: str | None = None,
        mode: str = "both",
        columns: list[str] | None = None,
        dropdown_fields: list[str] | None = None,
        extra_padding: int = 0,
        min_width: int = 60,
        dry_run: bool = False,
        max_retries: int = 2,
    ) -> dict:
        """一键将 VTable 各列列宽调整至表头文本/单元格内容完全显现。

        内部闭环:单次页面内求值完成 文本测量+目标宽度+边框绝对坐标 计算 →
        从右往左串行 trusted 拖拽(28 步细密轨迹)→ 每步拖拽后重读
        宽度/列序/边框坐标自适应重算下一步(容差 2px,误差止步于当前列)→
        列序被误动时先熔断修复再继续 → 僵尸态(canvas 不渲染)自动 reload
        自愈一次。全程复用 ui_mouse_drag 的 CDP 真实输入管道。

        mode: header=仅表头完全显现 / content=仅内容 / both=取两者最大(默认)。
        columns: 业务 field 白名单,缺省=全部可调列。
        dropdown_fields: 表头含下拉筛选图标的 field 列表(该类列图标区按
        116px 计,常规列 96px,操作列 40px)。
        dry_run=True 只返回执行计划(各列 当前宽/目标宽/依据/样本/边框可拖性)
        不做任何拖拽。
        """
        return await automation.autofit_columns(
            frame=frame,
            mode=mode,
            columns=columns,
            dropdown_fields=dropdown_fields,
            extra_padding=extra_padding,
            min_width=min_width,
            dry_run=dry_run,
            max_retries=max_retries,
        )
```

---

## 验证步骤

```text
1. 重启 MCP 服务(TRAE 面板禁用再启用 qa-automation)
2. browser_connect(port=9222) 接管浏览器,打开任一 VTable 列表页
3. vtable_autofit_columns(dry_run=True)  → 检查 plan 中 target 是否合理
4. vtable_autofit_columns()              → 检查 status=ok / order_intact=true
5. 验收清单逐项过《vtable-autofit-技术分析总结.md》第 5 节
```

## 实现与代码库的对接关系

| autofit.py 依赖 | 来源 | 用途 |
|---|---|---|
| `_current_page_impl` / `_page_id` / `_frame_context_details` | `browser.py` | 取页面/上下文 |
| `_frame_page_offset` | `browser.py` | iframe 偏移换算(修复坐标陷阱的关键) |
| `_page_viewport_size` | `browser.py` | 拖拽终点视口钳制 |
| `_action_lock` | `browser.py` | 与既有工具一致的串行化锁 |
| `ensure_vtable` / `resolve_frame` / `vtable_frame` | `binding.py` | 实例绑定与 frame 定位 |
| `_mouse_drag_impl` | `mouse.py` | trusted CDP 拖拽管道(直接调 impl,避免二次抢锁死锁) |
| `BIND_TIMEOUT_MS` | `config.py` | 自愈后等待 .vtable 可见 |
