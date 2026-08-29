"""
VTable 相关 JS 脚本集中管理
============================

把散落在项目各文件中的 VTable JS 脚本(vtable_tester.py / fast_vtable_helper.py /
vtable_helper_advance.py 里的字符串常量与内联脚本)统一收编于此,
供 FastMCP 服务器以 MCP 资源方式暴露(vtable://js/{name}),
客户端 / LLM / 宿主可按名称协议读取,无需翻阅源码。

约定:
  - 键名为脚本短名;value 为 JS 源码。
  - 含 `%d` / `%s` / `{col}` 等格式化占位符的脚本,调用方需自行替换后注入。
  - 脚本依赖 `window._vtable` 已通过 fast_bind 绑定。
"""

from __future__ import annotations

# ============================================================================
#  1. 实例绑定与探测
# ============================================================================

# 实例绑定(优先级:可见弹窗 VTable > 普通 VTable;容器直连 > React Fiber)
# VTable 容器元素自带 __vtable__ 指向表格实例 —— 这是最确定的一条路
# (实测官方 demo: el.__vtable__ 即 ListTable 实例,含全部交互 API)。
FAST_BIND = r"""
const visible = (node) => {
  if (!node) return false;
  const style = getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' &&
    rect.width > 0 && rect.height > 0;
};
const roots = Array.from(document.querySelectorAll('.vtable'));
const modal = roots.filter(node => {
  const owner = node.closest('.ant-modal[role="document"], .ant-modal-wrap[role="dialog"]');
  return owner && visible(owner) && visible(node);
});
const requestedIndex = Number(window.__vtable_target_index);
const requested = Number.isInteger(requestedIndex) ? roots[requestedIndex] : null;
const el = requested || (modal.length ? modal.at(-1) : roots.filter(visible)[0]);
if (!visible(el)) return false;
if (!el || !el.parentElement) return false;

// 一线:VTable 原生绑定(el.__vtable__,或 canvas 上的 __vtable__)
const canvas = el.querySelector('canvas');
const native = el.__vtable__ || (canvas && canvas.__vtable__);
if (native && typeof native.getCellRect === 'function') {
  window._vtable = native;
  return true;
}

const parent = el.parentElement;
const fk = Object.keys(parent).find(k =>
  k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
);
if (!fk) return false;

try {
  // 二线:React Fiber 绝对路径直达 vtableInstance
  const instance = parent[fk].return.return.return.return.stateNode.vtableInstance;
  if (instance && typeof instance.getCellRect === 'function') {
    window._vtable = instance;
    return true;
  }
} catch (e) { /* 路径不对时静默回退 */ }
return false;
"""

# BFS 全树扫描降级:快速直连失败时逐层遍历 Fiber 树找实例
BIND_BFS_FALLBACK = r"""
function __vtable_bfs_detect__() {
    const visible = (node) => {
        if (!node) return false;
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' &&
            rect.width > 0 && rect.height > 0;
    };
    const roots = Array.from(document.querySelectorAll('.vtable'));
    const modal = roots.filter(node => {
        const owner = node.closest('.ant-modal[role="document"], .ant-modal-wrap[role="dialog"]');
        return owner && visible(owner) && visible(node);
    });
    const requestedIndex = Number(window.__vtable_target_index);
    const requested = Number.isInteger(requestedIndex) ? roots[requestedIndex] : null;
    const root = requested || (modal.length ? modal.at(-1) : roots.filter(visible)[0]);
    if (!visible(root)) return 'FAILED: selected .vtable is not visible';
    if (!root) return 'FAILED: no .vtable element';
    // 先检查元素自带的 __vtable__(容器或 canvas 直连)
    const cv = root.querySelector('canvas');
    const native = root.__vtable__ || (cv && cv.__vtable__);
    if (native && typeof native.getCellRect === 'function') {
        window._vtable = native;
        return 'SUCCESS:__vtable__';
    }
    const fiberKey = Object.keys(root).find(k =>
        k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    if (!fiberKey) return 'FAILED: no fiber key';
    const queue = [{ node: root[fiberKey], path: '' }];
    const seen = new Set();
    while (queue.length) {
        const { node, path } = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        const st = node.stateNode;
        if (st && st.vtableInstance && typeof st.vtableInstance.getCellRect === 'function') {
            window._vtable = st.vtableInstance;
            return 'SUCCESS:' + path;
        }
        const inspect = (n, p) => {
            if (!n) return;
            const st = n.stateNode;
            if (st && st.vtableInstance && typeof st.vtableInstance.getCellRect === 'function') {
                window._vtable = st.vtableInstance;
                return 'SUCCESS:' + p;
            }
            const r = inspect(n.memoizedState, p + '.memoizedState');
            if (r) return 'SUCCESS:' + r;
        };
        const r = inspect(node.memoizedState, path + '.memoizedState');
        if (r) return r;
        if (node.child)   queue.push({ node: node.child,   path: path + '.child'   });
        if (node.sibling) queue.push({ node: node.sibling, path: path + '.sibling' });
        if (node.return)  queue.push({ node: node.return,  path: path + '.return'  });
    }
    return 'FAILED: 全树扫描完毕，未找到实例';
}
return __vtable_bfs_detect__();
"""

# 只列出当前 frame 中可见的 VTable 容器。目录用于多表页面的显式选表，
# 不读取单元格业务数据，也不依赖 canvas DOM 命中测试。
VTABLE_ROOTS = r"""
return Array.from(document.querySelectorAll('.vtable')).map((node, table_index) => {
  const style = getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  const visible = style.display !== 'none' && style.visibility !== 'hidden' &&
    rect.width > 0 && rect.height > 0;
  const modal = node.closest('.ant-modal[role="document"], .ant-modal-wrap[role="dialog"]');
  return {
    table_index,
    visible,
    in_modal: !!modal,
    box: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
  };
}).filter(item => item.visible);
"""

# 已绑定单个表格后输出目录摘要，不返回行值，供 AI 从多个 VTable 中选择目标。
VTABLE_SUMMARY = r"""
return (function(){
  const t = window._vtable;
  if (!t) return null;
  const number = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const text = value => String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const colCount = number(t.colCount) ?? 0;
  const headerRow = Math.max(0, (number(t.headerRowCount) ?? 1) - 1);
  const headers = [];
  for (let col = 0; col < Math.min(colCount, 12); col++) {
    let field = '', title = '';
    try { field = text(t.getHeaderField?.(col, headerRow) ?? t.getBodyField?.(col, headerRow)); } catch (_) {}
    try { title = text(t.getCellValue?.(col, headerRow)); } catch (_) {}
    headers.push({col, field, title});
  }
  return {
    rowCount: number(t.rowCount), colCount,
    headerRowCount: number(t.headerRowCount),
    headers,
  };
})();
"""

# requestAnimationFrame 渲染等待(虚拟滚动后等待下一帧)
WAIT_RENDER = r"""
function __wait_render__() {
    return new Promise(resolve => requestAnimationFrame(resolve));
}
return __wait_render__();
"""

# ============================================================================
#  2. 单元格判定
# ============================================================================

# 读"真实渲染色"——VTable 是 Canvas 渲染,文字图元的 fill 才是肉眼所见
GET_RENDERED_FILL = r"""
(function(col, row){
  const t = window._vtable;
  const cell = t.scenegraph.getCell(col, row);
  if (!cell) return null;
  const want = String(t.getCellValue(col, row) ?? '').trim();

  let any = null, matched = null;
  const walk = (n) => {
    if (!n) return;
    const a = n.attribute || {};
    const isText = n.type === 'text' || a.text !== undefined;
    if (isText && a.fill) {
      any = any || a.fill;
      const txt = Array.isArray(a.text) ? a.text.join('') : String(a.text ?? '');
      if (txt.trim() === want) matched = matched || a.fill;
    }
    const kids = n.children || (n.getChildren && n.getChildren());
    if (Array.isArray(kids)) kids.forEach(walk);
    else if (n.forEachChildren) n.forEachChildren(walk);
  };
  walk(cell);
  return matched || any;
})(%d, %d);
"""

# "偏蓝"判定:#108ee9 / 'blue' / rgb(...) 归一为 rgb 后做蓝分量优势判定
IS_BLUE = r"""
(function(c){
  if (!c) return false;
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.fillStyle = c;                          // 浏览器自动归一
  const s = ctx.fillStyle.match(/\d+/g);
  if (!s) return false;
  const [r, g, b] = s.map(Number);
  return b > 150 && b - r > 40 && b - g > 25; // #108ee9 命中
})(%s);
"""

# 单元格分类:返回 {behavior, editable}
#   getCellType      -> 解析后类型;link/button/checkbox/radio/switch 有专属行为
#   getCustomLayout  -> 业务是否用 customLayout 接管渲染(蓝色弹窗格的"指纹")
#   getCellStyle     -> 声明色;scenegraph fill 才是真实渲染色
#   getEditor        -> 配了编辑器才有返回值;序号/聚合/分组行一律 falsy
CLASSIFY_CELL = r"""
return (function(col, row){
  const t = window._vtable;
  if (!t) return null;

  const editable = !!(
    t.getEditor && t.getEditor(col, row) &&
    !(t.isSeriesNumber && t.isSeriesNumber(col, row)) &&
    !(t.internalProps && t.internalProps.layoutMap && t.internalProps.layoutMap.isAggregation &&
      t.internalProps.layoutMap.isAggregation(col, row)) &&
    !((t.getCellRawRecord && t.getCellRawRecord(col, row) || {}).vtableMerge)
  );

  const type = t.getCellType(col, row);
  if (type === 'link') {
    const def = t.getBodyColumnDefine && t.getBodyColumnDefine(col, row);
    if (!def || def.linkJump !== false) return { behavior: 'jump', editable };
  }
  if (type === 'button' || type === 'checkbox' || type === 'radio' || type === 'switch') {
    return { behavior: 'control:' + type, editable };
  }

  const hasCustom = !!(t.getCustomLayout && t.getCustomLayout(col, row)) ||
                    !!(t.getCustomRender && t.getCustomRender(col, row));
  if (hasCustom) {
    let fill = null, cfg = null, visible = false;
    try {
      const cell = t.scenegraph.getCell(col, row);
      if (cell) {
        visible = true;
        const want = String(t.getCellValue(col, row) ?? '').trim();
        const walk = (n) => {
          if (!n || fill) return;
          const a = n.attribute || {};
          if ((n.type === 'text' || a.text !== undefined) && a.fill) {
            const txt = Array.isArray(a.text) ? a.text.join('') : String(a.text ?? '');
            if (txt.trim() === want) { fill = a.fill; return; }
            if (!fill) fill = a.fill;
          }
          const kids = n.children || (n.getChildren && n.getChildren());
          if (Array.isArray(kids)) kids.forEach(walk);
          else if (n.forEachChildren) n.forEachChildren(walk);
        };
        walk(cell);
        cfg = ((t.getCellStyle && t.getCellStyle(col, row)) || {}).color;
      }
    } catch (e) {}
    return { behavior: { hint: 'popup-candidate', fill, cfg, visible }, editable };
  }
  return { behavior: 'none', editable };
})(arguments[0], arguments[1]);
"""

# 单元格场景图的紧凑视觉签名。VTable 选区通常以 cell/group 的 fill、background
# 或 stroke 更新；这个签名只保留会影响渲染的属性，避免把完整 scenegraph 送回 MCP。
CELL_VISUAL_STATE = r"""
return (function(col, row){
  const t = window._vtable;
  if (!t || !t.scenegraph || typeof t.scenegraph.getCell !== 'function') return null;
  let cell = null;
  try { cell = t.scenegraph.getCell(col, row); } catch (_) { return null; }
  if (!cell) return null;
  const nodes = [];
  const paints = [];
  const walk = (node, depth) => {
    if (!node || depth > 8 || nodes.length >= 80) return;
    const a = node.attribute || {};
    const visual = {};
    for (const key of ['fill', 'background', 'backgroundColor', 'stroke', 'lineWidth', 'opacity', 'visible', 'cornerRadius']) {
      if (a[key] !== undefined && a[key] !== null) visual[key] = a[key];
    }
    if (Object.keys(visual).length) {
      nodes.push({type: String(node.type || node.name || 'node'), visual});
      for (const key of ['fill', 'background', 'backgroundColor', 'stroke']) {
        if (visual[key] !== undefined) paints.push(String(visual[key]));
      }
    }
    const children = node.children || (node.getChildren && node.getChildren());
    if (Array.isArray(children)) children.forEach(child => walk(child, depth + 1));
    else if (node.forEachChildren) node.forEachChildren(child => walk(child, depth + 1));
  };
  walk(cell, 0);
  const signature = JSON.stringify(nodes);
  return {signature, paints: [...new Set(paints)].slice(0, 12), node_count: nodes.length};
})(arguments[0], arguments[1]);
"""

# ============================================================================
#  3. 定位 / 滚动 / 交互
# ============================================================================

# 单元格相对可视坐标(兼容 rect 多字段命名风格)
CELL_RELATIVE_LOC = r"""
    return (function(col, row){
    const t = window._vtable;
    if (!t) return null;
    const rect = t.getCellRelativeRect(col, row);
    if (!rect) return null;
    const pick = (a, b) => (a !== undefined && a !== null ? a : b);
    const left   = pick(rect.left,   pick(rect.x1, rect.bounds && rect.bounds.x1));
    const top    = pick(rect.top,    pick(rect.y1, rect.bounds && rect.bounds.y1));
    const right  = pick(rect.right,  pick(rect.x2, rect.bounds && rect.bounds.x2));
    const bottom = pick(rect.bottom, pick(rect.y2, rect.bounds && rect.bounds.y2));
    if (left === undefined || top === undefined) return null;
    const width  = (right  !== undefined) ? (right  - left) : (rect.width  || 0);
    const height = (bottom !== undefined) ? (bottom - top)  : (rect.height || 0);
    return { x: left + width / 2, y: top + height / 2 };
    })(arguments[0], arguments[1]);
    """

# 单元格是否可点(中心点是否落在可见区域内,含冻结行列补偿)
# 点击落点取单元格中心点,故以中心点判定:超宽列(整列宽 > canvas 宽)的单元格
# 右缘必然超出 canvas(right > cw),若按"整格完整可见"会被误判为不可点。
IS_CELL_VISIBLE = r"""
(function(col, row){
    const t = window._vtable;
    if (!t) return null;
    const rect = t.getCellRelativeRect(col, row);
    if (!rect) return null;
    const pick = (a, b) => (a !== undefined && a !== null ? a : b);
    const left   = pick(rect.left,   pick(rect.x1, rect.bounds && rect.bounds.x1));
    const top    = pick(rect.top,    pick(rect.y1, rect.bounds && rect.bounds.y1));
    const right  = pick(rect.right,  pick(rect.x2, rect.bounds && rect.bounds.x2));
    const bottom = pick(rect.bottom, pick(rect.y2, rect.bounds && rect.bounds.y2));
    if (left === undefined || top === undefined) return null;
    const frozenW = t.frozenColCount ? t.getFrozenColsWidth() : 0;
    const frozenH = t.frozenRowCount ? t.getFrozenRowsHeight() : 0;
    // getCellRelativeRect 使用 CSS 像素；canvas.width/height 可能已乘 DPR，
    // 因此可视边界必须使用实际 CSS 盒尺寸。
    const canvasRect = t.canvas.getBoundingClientRect();
    const cw = canvasRect.width, ch = canvasRect.height;
    const tol = 1;
    const cx = (left + right) / 2;
    const cy = (top + bottom) / 2;
    if (cx === undefined || cy === undefined) return null;
    return (cx >= frozenW - tol) && (cy >= frozenH - tol) &&
           (cx <= cw + tol) && (cy <= ch + tol);
})({col}, {row});
"""

# JS 派发原生 pointer/mouse 事件点击,绕过 Ant Design 的 hover 状态
CLICK_BY_JS = r"""
(function(){
  const el = document.elementFromPoint({x}, {y});
  if (!el) return false;
  const opts = { bubbles: true, cancelable: true, view: window,
                 clientX: {x}, clientY: {y}, button: 0 };
  el.dispatchEvent(new PointerEvent('pointerdown', opts));
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  el.dispatchEvent(new PointerEvent('pointerup', opts));
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('click', opts));
  if ({double}) {
    el.dispatchEvent(new MouseEvent('dblclick', opts));
  }
  return true;
})();
"""

# 走 VTable editorManager 写入并落值(兼容 select/date/textarea 等内置编辑器)
EDIT_CELL = r"""
return(function(){
  const t = window._vtable;
  const editor = t.getEditor && t.getEditor({col}, {row});
  if (!editor) return { ok: false, reason: 'no-editor' };

  // 触发 startEditCell(与 VTable 内部 EditManager 一致)
  t.editorManager.startEditCell({col}, {row});
  const e = t.editorManager.editingEditor;
  if (!e) return { ok: false, reason: 'start-failed' };

  // 写入值(setValue 兼容 VTable-editors 的所有内置编辑器)
  e.setValue && e.setValue({value});

  // 落值(commit=true 走 onEnd 写回,false 留编辑态便于连续操作)
  if ({commit}) {
    t.editorManager.completeEdit();
  }
  return { ok: true };
})();
"""

# ============================================================================
#  4. 选区 / 复制
# ============================================================================

SELECT_RANGES = "window._vtable.selectCells({ranges});"

GET_SELECTED_RANGES = "return window._vtable.getSelectedCellRanges();"

GET_COPY_VALUE = "return window._vtable.getCopyValue();"

# ============================================================================
#  4.1 批量读值 / 表格元数据(供 AI 先"看"清表格规模与内容,再规划交互)
# ============================================================================

# 批量读取矩形区域单元格值(行优先),单格失败不中断,返回 err 字段
READ_CELLS = r"""
return (function(col0, row0, col1, row1){
  const t = window._vtable;
  if (!t) return null;
  const minCol = Math.min(col0, col1), maxCol = Math.max(col0, col1);
  const minRow = Math.min(row0, row1), maxRow = Math.max(row0, row1);
  const values = [];
  for (let r = minRow; r <= maxRow; r++) {
    const line = [];
    for (let c = minCol; c <= maxCol; c++) {
      let v = null, err = null;
      try { v = t.getCellValue ? t.getCellValue(c, r) : null; }
      catch (e) { err = String(e); }
      line.push({ c, r, v, err });
    }
    values.push(line);
  }
  return { minCol, maxCol, minRow, maxRow, values };
})(arguments[0], arguments[1], arguments[2], arguments[3]);
"""

# 表格规模/冻结行列/主题等元数据(防御性读取,属性缺失自动跳过)
TABLE_META = r"""
return (function(){
  const t = window._vtable;
  if (!t) return null;
  const meta = {};
  const keys = ['rowCount','colCount','frozenRowCount','frozenColCount',
                'headerRowCount','bottomFrozenRowCount','rightFrozenColCount','theme'];
  for (const k of keys) {
    try { if (t[k] !== undefined) meta[k] = t[k]; } catch (e) {}
  }
  if (Array.isArray(t.records)) meta.records = t.records.length;
  if (typeof t.getRecords === 'function') {
    try { meta.records = t.getRecords().length; } catch (e) {}
  }
  if (typeof t.getCellHeaderPaths === 'function') {
    try { const h = t.getCellHeaderPaths(0, 0); if (h) meta.sampleHeaders = h; } catch (e) {}
  }
  return meta;
})();
"""

# 紧凑语义分析:只通过 VTable 实例 API 读取字段、表头能力和少量样本。
# 不扫描 DOM 猜测 canvas 单元格,也不把整表记录传给 AI。
ANALYZE_TABLE = r"""
return (function(maxColumns, sampleRows){
  const t = window._vtable;
  if (!t) return null;
  const limit = (value, fallback, max) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(0, Math.min(max, Math.trunc(n))) : fallback;
  };
  const text = value => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      return String(value).replace(/\s+/g, ' ').trim().slice(0, 200);
    }
    if (Array.isArray(value)) return value.map(text).filter(Boolean).join(' / ').slice(0, 200);
    if (typeof value === 'object') {
      for (const key of ['title', 'text', 'label', 'name', 'field']) {
        if (value[key] !== undefined) {
          const resolved = text(value[key]);
          if (resolved) return resolved;
        }
      }
    }
    return '';
  };
  const compactValue = value => {
    if (value === null || value === undefined || ['string', 'number', 'boolean'].includes(typeof value)) {
      return typeof value === 'string' ? value.slice(0, 300) : value;
    }
    try {
      const json = JSON.stringify(value);
      return json && json.length <= 300 ? JSON.parse(json) : String(json || '').slice(0, 300);
    } catch (_) {
      return text(value);
    }
  };
  const rowCount = limit(t.rowCount, 0, 10000000);
  const colCount = limit(t.colCount, 0, 100000);
  const headerRowCount = limit(t.headerRowCount, 0, rowCount);
  const firstBodyRow = Math.min(Math.max(0, headerRowCount), Math.max(0, rowCount - 1));
  const columnLimit = Math.min(colCount, limit(maxColumns, 40, 200));
  const rowLimit = Math.min(Math.max(0, rowCount - firstBodyRow), limit(sampleRows, 3, 20));
  const columns = [];

  for (let col = 0; col < columnLimit; col++) {
    let field = '', title = '', type = '', definition = null, icons = [];
    try { field = text(t.getBodyField && t.getBodyField(col, firstBodyRow)); } catch (_) {}
    try { definition = t.getBodyColumnDefine && t.getBodyColumnDefine(col, firstBodyRow); } catch (_) {}
    try {
      title = text(definition && (definition.title ?? definition.header ?? definition.caption));
      if (!title && headerRowCount > 0 && t.getCellValue) title = text(t.getCellValue(col, headerRowCount - 1));
    } catch (_) {}
    try { type = text(t.getCellType && t.getCellType(col, firstBodyRow)); } catch (_) {}
    try {
      icons = (t.getCellIcons && t.getCellIcons(col, Math.max(0, headerRowCount - 1)) || [])
        .slice(0, 8).map(icon => text(icon && (icon.funcType ?? icon.name ?? icon.type ?? icon.tooltip)))
        .filter(Boolean);
    } catch (_) {}
    const iconText = icons.join(' ').toLowerCase();
    let editable = false, custom = false;
    try { editable = !!(t.getEditor && t.getEditor(col, firstBodyRow)); } catch (_) {}
    try {
      custom = !!((t.getCustomLayout && t.getCustomLayout(col, firstBodyRow)) ||
                  (t.getCustomRender && t.getCustomRender(col, firstBodyRow)));
    } catch (_) {}
    columns.push({
      col,
      field,
      title,
      type,
      capabilities: {
        editable,
        custom,
        sortable: !!(definition && (definition.sort || definition.sortable)) || iconText.includes('sort') || iconText.includes('排序'),
        filterable: !!(definition && (definition.filter || definition.filterable)) || iconText.includes('filter') || iconText.includes('筛选'),
        control: ['button', 'checkbox', 'radio', 'switch', 'link'].includes(type),
      },
      icons,
    });
  }

  const samples = [];
  for (let row = firstBodyRow; row < firstBodyRow + rowLimit; row++) {
    let recordIndex = null;
    try { recordIndex = t.getRecordIndexByCell ? t.getRecordIndexByCell(0, row) : row - firstBodyRow; } catch (_) {}
    const values = [];
    for (let col = 0; col < columnLimit; col++) {
      let value = null;
      try { value = t.getCellValue ? compactValue(t.getCellValue(col, row)) : null; } catch (_) {}
      values.push(value);
    }
    samples.push({ row, record_index: recordIndex, values });
  }

  return {
    meta: { rowCount, colCount, headerRowCount, frozenRowCount: t.frozenRowCount || 0, frozenColCount: t.frozenColCount || 0 },
    columns,
    sample_rows: samples,
    truncated: { columns: colCount > columnLimit, rows: rowCount - firstBodyRow > rowLimit },
  };
})(arguments[0], arguments[1]);
"""

# 表头图标扫描:图标节点和局部几何只来自 VTable scenegraph。脚本不访问
# window.frameElement;frame 到顶层 viewport 的坐标拼接统一由 Playwright 完成。
HEADER_ICONS = r"""
return (function(maxColumns, maxResults){
  const t = window._vtable;
  if (!t || !t.scenegraph || typeof t.scenegraph.getCell !== 'function') return null;

  const clamp = (value, fallback, maximum) => {
    const number = Number(value);
    return Number.isFinite(number)
      ? Math.max(0, Math.min(maximum, Math.trunc(number)))
      : fallback;
  };
  const compactText = value => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      return String(value).replace(/\s+/g, ' ').trim().slice(0, 160);
    }
    if (typeof value === 'object') {
      for (const key of ['title', 'text', 'label', 'name', 'field']) {
        if (value[key] !== undefined) {
          const resolved = compactText(value[key]);
          if (resolved) return resolved;
        }
      }
    }
    return '';
  };
  const iconFunction = value => {
    const name = String(value || '').toLowerCase();
    if (name.includes('sort')) return 'sort';
    if (name.includes('filter')) return 'filter';
    if (name.includes('dropdown') || name.includes('downward')) return 'dropdown';
    if (name.includes('freeze') || name.includes('frozen')) return 'freeze';
    if (name.includes('checkbox')) return 'checkbox';
    if (name.includes('radio')) return 'radio';
    if (name.includes('switch')) return 'switch';
    if (name.includes('expand')) return 'expand';
    if (name.includes('collapse')) return 'collapse';
    return 'custom';
  };
  const childrenOf = node => {
    if (!node) return [];
    if (Array.isArray(node.children)) return node.children;
    try {
      const children = node.getChildren && node.getChildren();
      if (Array.isArray(children)) return children;
    } catch (_) {}
    const children = [];
    try {
      if (typeof node.forEachChildren === 'function') node.forEachChildren(child => children.push(child));
    } catch (_) {}
    return children;
  };
  const nodeName = node => compactText(
    node && (node.name || (node.attribute &&
      (node.attribute.name || node.attribute.iconName || node.attribute.funcType)))
  );
  const structuralNames = new Set([
    '', 'group', 'cell', 'cell-group', 'content', 'text', 'background',
    'border', 'line', 'rect', 'shadow', 'stroke'
  ]);
  const collectIcons = cell => {
    const icons = [];
    const seenNodes = new Set();
    const seenIcons = new Set();
    const queue = [{node: cell, depth: 0}];
    let visited = 0;
    while (queue.length && visited < 256) {
      const current = queue.shift();
      const node = current.node;
      if (!node || seenNodes.has(node) || current.depth > 6) continue;
      seenNodes.add(node);
      visited += 1;
      if (current.depth > 0) {
        const name = nodeName(node);
        const normalized = name.toLowerCase();
        const attribute = node.attribute || {};
        const type = String(node.type || '').toLowerCase();
        const textNode = type === 'text' || attribute.text !== undefined;
        const bounds = node.globalAABBBounds;
        if (name && !textNode && !structuralNames.has(normalized) && bounds) {
          const x1 = Number(bounds.x1), y1 = Number(bounds.y1);
          const x2 = Number(bounds.x2), y2 = Number(bounds.y2);
          const width = x2 - x1, height = y2 - y1;
          const finite = [x1, y1, x2, y2, width, height].every(Number.isFinite);
          if (finite && Math.abs(x1) < 1e7 && Math.abs(y1) < 1e7 &&
              width > 0 && width < 500 && height > 0 && height < 500) {
            const centerX = (x1 + x2) / 2, centerY = (y1 + y2) / 2;
            const fingerprint = [name, x1, y1, x2, y2].join('|');
            if (!seenIcons.has(fingerprint)) {
              seenIcons.add(fingerprint);
              icons.push({
                name,
                function: iconFunction(name),
                box: {x: x1, y: y1, width, height},
                center: {x: centerX, y: centerY},
              });
            }
          }
        }
      }
      for (const child of childrenOf(node)) queue.push({node: child, depth: current.depth + 1});
    }
    return icons;
  };

  const root = document.querySelector('.vtable');
  const canvas = t.canvas || (root && root.querySelector('canvas')) || root;
  if (!canvas) return null;
  const canvasRect = canvas.getBoundingClientRect();
  const canvasBox = {
    x: Number(canvasRect.left), y: Number(canvasRect.top),
    width: Number(canvasRect.width), height: Number(canvasRect.height),
  };
  const configured = t.columns || (t.options && t.options.columns) || [];
  const configuredColumns = Array.isArray(configured) ? configured : [];
  const columnCount = Math.max(clamp(t.colCount, 0, 100000), configuredColumns.length);
  const columnLimit = Math.min(columnCount, clamp(maxColumns, 40, 200));
  const headerRows = Math.max(1, clamp(
    t.columnHeaderLevelCount ?? t.headerRowCount ?? 1, 1, Math.max(1, Number(t.rowCount) || 1)
  ));
  const resultLimit = Math.max(1, clamp(maxResults, 80, 400));
  const icons = [];
  let discovered = 0;

  for (let col = 0; col < columnLimit; col++) {
    for (let row = 0; row < headerRows; row++) {
      let isHeader = row < headerRows;
      try { if (typeof t.isHeader === 'function') isHeader = !!t.isHeader(col, row); } catch (_) {}
      if (!isHeader) continue;
      let cell = null;
      try { cell = t.scenegraph.getCell(col, row); } catch (_) {}
      if (!cell) continue;
      let field = '', title = '', definition = null;
      try { field = compactText(t.getHeaderField && t.getHeaderField(col, row)); } catch (_) {}
      try { definition = t.getHeaderDefine && t.getHeaderDefine(col, row); } catch (_) {}
      if (!field) {
        try { field = compactText(t.getBodyField && t.getBodyField(col, headerRows)); } catch (_) {}
      }
      if (!field) field = compactText(
        (definition && (definition.field ?? definition.key)) ||
        (configuredColumns[col] && (configuredColumns[col].field ?? configuredColumns[col].key))
      );
      try { title = compactText(t.getCellValue && t.getCellValue(col, row)); } catch (_) {}
      if (!title) title = compactText(
        (definition && (definition.title ?? definition.caption)) ||
        (configuredColumns[col] &&
          (configuredColumns[col].title ?? configuredColumns[col].caption ?? configuredColumns[col].field))
      );
      for (const icon of collectIcons(cell)) {
        const center = icon.center;
        const inCanvas = canvasBox.width <= 0 || canvasBox.height <= 0 ||
          (center.x >= 0 && center.y >= 0 && center.x < canvasBox.width && center.y < canvasBox.height);
        if (!inCanvas) continue;
        discovered += 1;
        if (icons.length < resultLimit) icons.push({col, row, field, title, ...icon});
      }
    }
  }
  return {
    meta: {columnCount, headerRowCount: headerRows, scannedColumns: columnLimit},
    canvas_box: canvasBox,
    icons,
    discovered,
    truncated: discovered > icons.length || columnCount > columnLimit,
  };
})(arguments[0], arguments[1]);
"""

# 面向 MCP 的一次性 VTable 交互模型。只读取 VTable API/scenegraph，不执行点击，
# 因而不会打开编辑器、改变选区或触发业务副作用。几何保持在 canvas 局部坐标，
# Playwright 层统一换算为顶层 viewport，避免 iframe 内访问 parent 的跨域限制。
VTABLE_ANALYSIS = r"""
return (function(options, unused){
  const t = window._vtable;
  if (!t || !t.scenegraph || typeof t.scenegraph.getCell !== 'function') return null;
  const settings = options && typeof options === 'object' ? options : {};
  const clamp = (value, fallback, maximum) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.min(maximum, Math.trunc(number))) : fallback;
  };
  const text = value => {
    if (value === null || value === undefined) return '';
    if (['string', 'number', 'boolean'].includes(typeof value)) return String(value).replace(/\s+/g, ' ').trim().slice(0, 160);
    if (Array.isArray(value)) return value.map(text).filter(Boolean).join(' / ').slice(0, 160);
    if (typeof value === 'object') {
      for (const key of ['title', 'text', 'label', 'name', 'field', 'key']) {
        if (value[key] !== undefined) { const resolved = text(value[key]); if (resolved) return resolved; }
      }
    }
    return '';
  };
  const valuePreview = value => {
    if (value === null || value === undefined || ['string', 'number', 'boolean'].includes(typeof value)) {
      return typeof value === 'string' ? value.slice(0, 240) : value;
    }
    try { return String(JSON.stringify(value) || '').slice(0, 240); } catch (_) { return text(value); }
  };
  const childrenOf = node => {
    if (!node) return [];
    if (Array.isArray(node.children)) return node.children;
    try { const children = node.getChildren && node.getChildren(); if (Array.isArray(children)) return children; } catch (_) {}
    const children = [];
    try { if (typeof node.forEachChildren === 'function') node.forEachChildren(child => children.push(child)); } catch (_) {}
    return children;
  };
  const iconFunction = value => {
    const name = String(value || '').toLowerCase();
    if (name.includes('sort')) return 'sort';
    if (name.includes('filter')) return 'filter';
    if (name.includes('dropdown') || name.includes('downward')) return 'dropdown';
    if (name.includes('freeze') || name.includes('frozen')) return 'freeze';
    if (name.includes('checkbox')) return 'checkbox';
    if (name.includes('radio')) return 'radio';
    if (name.includes('switch')) return 'switch';
    if (name.includes('expand')) return 'expand';
    if (name.includes('collapse')) return 'collapse';
    return 'custom';
  };
  const collectTargets = cell => {
    const structural = new Set(['', 'group', 'cell', 'cell-group', 'content', 'text', 'background', 'border', 'line', 'rect', 'shadow', 'stroke']);
    const queue = [{node: cell, depth: 0}], seenNodes = new Set(), seenTargets = new Set(), targets = [];
    let visited = 0;
    while (queue.length && visited < 256) {
      const current = queue.shift(), node = current.node;
      if (!node || seenNodes.has(node) || current.depth > 6) continue;
      seenNodes.add(node); visited += 1;
      if (current.depth > 0) {
        const attribute = node.attribute || {};
        const nodeText = text(attribute.text);
        const name = text(node.name || attribute.name || attribute.iconName || attribute.funcType || nodeText);
        const type = String(node.type || '').toLowerCase();
        const cursor = String(attribute.cursor || node.cursor || '').toLowerCase();
        const pickable = attribute.pickable ?? node.pickable;
        const bounds = node.globalAABBBounds;
        const fn = iconFunction(name);
        const knownFunction = fn !== 'custom';
        const pointer = cursor === 'pointer';
        const controlType = ['button', 'checkbox', 'radio', 'switch'].includes(type);
        const actionable = pickable !== false && (knownFunction || pointer || controlType);
        if (name && actionable && (!structural.has(name.toLowerCase()) || pointer || controlType) && bounds) {
          const x1 = Number(bounds.x1), y1 = Number(bounds.y1), x2 = Number(bounds.x2), y2 = Number(bounds.y2);
          const width = x2 - x1, height = y2 - y1;
          if ([x1, y1, x2, y2, width, height].every(Number.isFinite) && Math.abs(x1) < 1e7 && Math.abs(y1) < 1e7 && width > 0 && width < 500 && height > 0 && height < 500) {
            const fingerprint = [name, x1, y1, x2, y2].join('|');
            if (!seenTargets.has(fingerprint)) {
              seenTargets.add(fingerprint);
              targets.push({name, function: fn, node_type: type || null, cursor: cursor || null, confidence: 'confirmed', evidence: knownFunction ? ['scenegraph-function'] : (pointer ? ['scenegraph-cursor:pointer'] : ['scenegraph-control']), box: {x: x1, y: y1, width, height}, center: {x: (x1 + x2) / 2, y: (y1 + y2) / 2}});
            }
          }
        }
      }
      for (const child of childrenOf(node)) queue.push({node: child, depth: current.depth + 1});
    }
    return targets.slice(0, 8);
  };
  const relativeBox = (col, row) => {
    try {
      const rect = t.getCellRelativeRect && t.getCellRelativeRect(col, row);
      if (!rect) return null;
      const pick = (...values) => values.find(value => value !== undefined && value !== null);
      const left = Number(pick(rect.left, rect.x1, rect.bounds && rect.bounds.x1));
      const top = Number(pick(rect.top, rect.y1, rect.bounds && rect.bounds.y1));
      const right = Number(pick(rect.right, rect.x2, rect.bounds && rect.bounds.x2));
      const bottom = Number(pick(rect.bottom, rect.y2, rect.bounds && rect.bounds.y2));
      if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return null;
      return {box: {x: left, y: top, width: right - left, height: bottom - top}, center: {x: (left + right) / 2, y: (top + bottom) / 2}};
    } catch (_) { return null; }
  };
  const editorTags = editor => {
    const name = text(editor && ((editor.constructor && editor.constructor.name) || editor.name || editor.type)).toLowerCase();
    if (name.includes('textarea')) return ['textarea'];
    if (name.includes('list') || name.includes('select')) return ['input', 'button'];
    if (name.includes('date')) return ['input'];
    return ['input'];
  };
  const root = document.querySelector('.vtable');
  const canvas = t.canvas || (root && root.querySelector('canvas')) || root;
  if (!canvas) return null;
  const canvasRect = canvas.getBoundingClientRect();
  const configured = t.columns || (t.options && t.options.columns) || [];
  const configuredColumns = Array.isArray(configured) ? configured : [];
  const rowCount = clamp(t.rowCount, 0, 10000000);
  const colCount = Math.max(clamp(t.colCount, 0, 100000), configuredColumns.length);
  const headerRows = Math.max(1, clamp(t.columnHeaderLevelCount ?? t.headerRowCount ?? 1, 1, Math.max(1, rowCount)));
  const columnLimit = Math.min(colCount, clamp(settings.max_columns, 20, 100));
  const bodyRows = Math.max(0, rowCount - headerRows);
  const rowLimit = Math.min(bodyRows, clamp(settings.sample_rows, 2, 8));
  const requestedFields = new Set((Array.isArray(settings.fields) ? settings.fields : []).map(value => String(value)));
  const includeValues = settings.include_values === true;
  const scanColumnCount = requestedFields.size ? Math.min(colCount, 100) : columnLimit;
  const triggerValue = (t.options && t.options.editCellTrigger) ?? 'doubleclick';
  const editTriggers = (Array.isArray(triggerValue) ? triggerValue : [triggerValue]).map(value => String(value).toLowerCase());
  const columns = [];

  for (let col = 0; col < scanColumnCount; col++) {
    let definition = null, field = '', title = '';
    try { definition = t.getBodyColumnDefine && t.getBodyColumnDefine(col, headerRows); } catch (_) {}
    try { field = text(t.getBodyField && t.getBodyField(col, headerRows)); } catch (_) {}
    if (!field) field = text((definition && (definition.field ?? definition.key)) || (configuredColumns[col] && (configuredColumns[col].field ?? configuredColumns[col].key)));
    if (requestedFields.size && !requestedFields.has(field)) continue;
    try { title = text(t.getCellValue && t.getCellValue(col, Math.max(0, headerRows - 1))); } catch (_) {}
    if (!title) title = text((definition && (definition.title ?? definition.header ?? definition.caption)) || (configuredColumns[col] && (configuredColumns[col].title ?? configuredColumns[col].caption ?? configuredColumns[col].field)));
    const header = [];
    for (let row = 0; row < headerRows; row++) {
      let isHeader = true;
      try { if (typeof t.isHeader === 'function') isHeader = !!t.isHeader(col, row); } catch (_) {}
      if (!isHeader) continue;
      let headerCell = null;
      try { headerCell = t.scenegraph.getCell(col, row); } catch (_) {}
      if (headerCell) header.push({row, geometry: relativeBox(col, row), icons: collectTargets(headerCell)});
    }
    const samples = [];
    for (let index = 0; index < rowLimit; index++) {
      const row = headerRows + index;
      let type = '', editor = null, custom = false, recordIndex = index, cell = null, value = null;
      try { type = text(t.getCellType && t.getCellType(col, row)); } catch (_) {}
      try { editor = t.getEditor && t.getEditor(col, row); } catch (_) {}
      try { custom = !!((t.getCustomLayout && t.getCustomLayout(col, row)) || (t.getCustomRender && t.getCustomRender(col, row))); } catch (_) {}
      try { recordIndex = t.getRecordIndexByCell ? t.getRecordIndexByCell(col, row) : index; } catch (_) {}
      try { cell = t.scenegraph.getCell(col, row); } catch (_) {}
      try { value = t.getCellValue ? t.getCellValue(col, row) : null; } catch (_) {}
      const control = ['button', 'checkbox', 'radio', 'switch'].includes(type);
      const link = type === 'link';
      const editorAvailable = !!editor;
      const targets = cell ? collectTargets(cell) : [];
      const confirmed = control || link || editorAvailable || targets.length > 0;
      const confidence = confirmed ? 'confirmed' : (custom ? 'candidate' : 'none');
      const kind = control ? 'control:' + type : (link ? 'link' : (editorAvailable ? 'editable-text' : (targets.length ? 'scenegraph-target' : (custom ? 'custom-render' : 'none'))));
      const activation = editorAvailable ? (editTriggers.includes('click') ? 'click' : (editTriggers.includes('doubleclick') ? 'doubleclick' : (editTriggers[0] || 'api'))) : (confirmed ? 'click' : 'unknown');
      const sample = {
        row, record_index: recordIndex, type,
        interaction: {kind, confidence, clickable: confirmed, activation, custom, evidence: control ? ['VTable.getCellType'] : (link ? ['VTable.getCellType'] : (editorAvailable ? ['VTable.getEditor'] : targets.flatMap(target => target.evidence).slice(0, 4)))},
        editor: {available: editorAvailable, edit_triggers: editTriggers, click_opens_dom_input: editorAvailable ? editTriggers.includes('click') : false, expected_dom_tags: editorAvailable ? editorTags(editor) : [], evidence: editorAvailable ? ['VTable.getEditor', 'options.editCellTrigger'] : []},
        geometry: relativeBox(col, row), targets,
      };
      if (includeValues) sample.value = valuePreview(value);
      samples.push(sample);
    }
    columns.push({col, field, title, header, sample_cells: samples});
  }
  return {
    meta: {rowCount, colCount, headerRowCount: headerRows, frozenRowCount: Number(t.frozenRowCount || 0), frozenColCount: Number(t.frozenColCount || 0), editCellTrigger: editTriggers, scrollLeft: Number(t.scrollLeft || 0), scrollTop: Number(t.scrollTop || 0), canvas_box: {x: Number(canvasRect.left), y: Number(canvasRect.top), width: Number(canvasRect.width), height: Number(canvasRect.height)}},
    columns,
    truncated: {columns: requestedFields.size ? colCount > 100 : colCount > columnLimit, sample_rows: bodyRows > rowLimit},
  };
})(arguments[0], arguments[1]);
"""

# 由业务字段 + 原始记录索引解析表格地址。首选单一官方 API,旧版本再使用两个
# index 映射 API 组合;两条路径都不读取 DOM 或猜测坐标。
RESOLVE_CELL = r"""
return (function(field, recordIndex){
  const t = window._vtable;
  if (!t) return null;
  const compact = value => {
    if (value === null || value === undefined || ['string', 'number', 'boolean'].includes(typeof value)) {
      return typeof value === 'string' ? value.slice(0, 300) : value;
    }
    try {
      const json = JSON.stringify(value);
      return json && json.length <= 300 ? JSON.parse(json) : String(json || '').slice(0, 300);
    } catch (_) { return String(value).slice(0, 300); }
  };
  let address = null, method = '';
  try {
    if (typeof t.getCellAddrByFieldRecord === 'function') {
      address = t.getCellAddrByFieldRecord(field, recordIndex);
      method = 'getCellAddrByFieldRecord';
    }
  } catch (_) { address = null; }
  if (!address || !Number.isFinite(Number(address.col)) || !Number.isFinite(Number(address.row))) {
    try {
      if (typeof t.getTableIndexByField === 'function' && typeof t.getTableIndexByRecordIndex === 'function') {
        address = {
          col: t.getTableIndexByField(field),
          row: t.getTableIndexByRecordIndex(recordIndex),
        };
        method = 'getTableIndexByField+getTableIndexByRecordIndex';
      }
    } catch (_) { address = null; }
  }
  if (!address) return { ok: false, reason: 'address-unavailable', field, recordIndex };
  const col = Number(address.col), row = Number(address.row);
  if (!Number.isInteger(col) || !Number.isInteger(row) || col < 0 || row < 0 ||
      col >= Number(t.colCount || 0) || row >= Number(t.rowCount || 0)) {
    return { ok: false, reason: 'address-out-of-range', field, recordIndex, address: { col, row }, method };
  }
  let value = null, type = null, headerPaths = null;
  try { value = t.getCellValue ? compact(t.getCellValue(col, row)) : null; } catch (_) {}
  try { type = t.getCellType ? t.getCellType(col, row) : null; } catch (_) {}
  try { headerPaths = t.getCellHeaderPaths ? t.getCellHeaderPaths(col, row) : null; } catch (_) {}
  return { ok: true, col, row, field, recordIndex, value, type, headerPaths, method };
})(arguments[0], arguments[1]);
"""

# 拖放目标盒:canvas 与 .vtable 容器的视口边界,换算 locator.drop 的相对坐标
DROP_TARGET_BOXES = r"""
return (function(){
  const t = window._vtable;
  if (!t) return null;
  const vtableEl = document.querySelector('.vtable');
  if (!vtableEl) return null;
  const box = (el) => { const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height }; };
  const canvas = t.canvas || vtableEl.querySelector('canvas');
  return { vtable: box(vtableEl), canvas: canvas ? box(canvas) : box(vtableEl) };
})();
"""

# ============================================================================
#  目录
# ============================================================================

VTABLE_SCRIPTS: dict[str, str] = {
    "fast_bind": FAST_BIND,
    "bind_bfs_fallback": BIND_BFS_FALLBACK,
    "wait_render": WAIT_RENDER,
    "get_rendered_fill": GET_RENDERED_FILL,
    "cell_visual_state": CELL_VISUAL_STATE,
    "is_blue": IS_BLUE,
    "classify_cell": CLASSIFY_CELL,
    "cell_relative_loc": CELL_RELATIVE_LOC,
    "is_cell_visible": IS_CELL_VISIBLE,
    "click_by_js": CLICK_BY_JS,
    "edit_cell": EDIT_CELL,
    "select_ranges": SELECT_RANGES,
    "get_selected_ranges": GET_SELECTED_RANGES,
    "get_copy_value": GET_COPY_VALUE,
    "read_cells": READ_CELLS,
    "table_meta": TABLE_META,
    "vtable_analysis": VTABLE_ANALYSIS,
    "resolve_cell": RESOLVE_CELL,
    "drop_target_boxes": DROP_TARGET_BOXES,
}

SCRIPT_DESCRIPTIONS: dict[str, str] = {
    "fast_bind": "实例绑定:容器 __vtable__ 直连 > React Fiber 绝对路径(主入口)",
    "bind_bfs_fallback": "快速直连失败时的 BFS 全树扫描降级探测",
    "wait_render": "requestAnimationFrame 渲染等待(虚拟滚动后)",
    "get_rendered_fill": "读单元格真实渲染色(Canvas 文字图元 fill),占位符 %d %d",
    "cell_visual_state": "读取单元格 scenegraph 紧凑视觉签名(背景/填充/描边等),参数走 arguments ×2",
    "is_blue": "偏蓝颜色判定,占位符 %s(颜色字符串)",
    "classify_cell": "单元格分类:返回 {behavior, editable}",
    "cell_relative_loc": "单元格相对可视坐标中心点,参数走 arguments",
    "is_cell_visible": "单元格是否在视口内(含冻结行列补偿),占位符 {col} {row}",
    "click_by_js": "JS 派发 pointer/mouse 事件点击,占位符 {x} {y} {double}",
    "edit_cell": "editorManager 编辑单元格并落值,占位符 {col} {row} {value} {commit}",
    "select_ranges": "按 ranges 数组框选,占位符 {ranges}(JSON)",
    "get_selected_ranges": "读取当前选区",
    "get_copy_value": "读取表格复制内容",
    "read_cells": "批量读取矩形区域单元格值(行优先),参数走 arguments ×4",
    "table_meta": "表格规模/冻结行列/主题等元数据(防御性读取)",
    "vtable_analysis": "读取列头/图标/值单元格交互和编辑器证据,参数走 arguments ×2",
    "resolve_cell": "通过 VTable API 将字段+记录索引解析为单元格地址,参数走 arguments ×2",
    "drop_target_boxes": "canvas 与 .vtable 容器的视口边界,供拖放换算相对坐标",
}


def inventory() -> dict[str, dict[str, str]]:
    """脚本目录:name -> {description, length}。"""
    return {
        name: {
            "description": SCRIPT_DESCRIPTIONS.get(name, ""),
            "length": len(script),
        }
        for name, script in VTABLE_SCRIPTS.items()
    }
