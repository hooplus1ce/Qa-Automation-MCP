# VTable 实例 API 全量清单(版本 1.26.2)

> **来源**:浏览器 JS 实时枚举(对运行中的 VTable 实例探测 `Object.getOwnPropertyNames` 等方法)+ 官方 TypeScript 类型声明(`npm @visactor/vtable@1.26.2` 的 `base-table.d.ts` / `ListTable.d.ts`)。
> 实例构造类:`ListTableAll`(继承链 `ListTable` → `BaseTable`)。
> 运行环境实测:WMS 采购订单页 iframe(`scm-spo`)内 VTable 实例,经 `el.__vtable__` 绑定,rowCount=12 / colCount=18。

## 统计

| 项 | 数量 |
|---|---|
| 实例方法 | **296** |
| └ 官方 API(有 TS 声明) | **236** |
| └ 运行时/注入方法(官方 .d.ts 未声明) | **60** |
| getter(属性只读) | **55** |
| setter(属性可写) | **33** |

> `exportToCsv` / `exportToExcel` / `getTheme` / `dispose` / `on` / `addEventListener` 等方法由构建产物注入,未在官方类型面内,但真实存在于实例上,可直接调用。
> 标注 **@AI** 的方法是为本 MCP 交互工具(`vtable_cell_click` / `vtable_cell_info` / `vtable_cells_read` 等)核心依赖。

## 目录

- A. 单元格读取与判定(60)
- B. 编辑与数据写回(17)
- C. 数据与记录(21)
- D. 列与宽度(19)
- E. 行与高度(6)
- F. 滚动与视口(23)
- G. 选择与选区(13)
- H. 合并单元格(8)
- I. 树形与层级(6)
- J. 冻结行列(11)
- K. 渲染 / 刷新 / 更新选项(17)
- L. 导出与图片(10)
- M. 主题与像素比(2)
- N. 事件与监听(8)
- O. 图表与自定义布局(6)
- P. 坐标 / 几何 / 命中检测(20)
- R. 菜单 / 工具提示 / 交互 UI(7)
- Z. 运行时内部方法(_ 前缀,无 TS 声明)(42)
- getter / setter 属性清单

---

## A. 单元格读取与判定(60)

### `getCellValue` @AI

- **签名** `getCellValue(col: number, row: number, skipCustomMerge?: boolean): FieldData`
- **作用** 读取单元格的当前显示值(经过数据格式化的文本/数字,UI 所见即所得)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `skipCustomMerge?: boolean` — (可选) 是否跳过自定义合并单元格
- **返回** `FieldData` — 单元格数据(文本或数值)

### `getCellOriginValue`

- **签名** `getCellOriginValue(col: number, row: number): FieldData`
- **作用** 读取单元格的原始值(未格式化,数据源原样)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `FieldData` — 单元格数据(文本或数值)

### `getCellRawValue`

- **签名** `getCellRawValue(col: number, row: number): FieldData`
- **作用** 读取单元格的底层数据(FieldData,可能含 data 映射)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `FieldData` — 单元格数据(文本或数值)

### `getCellOriginRecord`

- **签名** `getCellOriginRecord(col: number, row: number): MaybePromiseOrUndefined`
- **作用** 读取单元格所在行的原始记录对象(异步数据可能返回 Promise)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `MaybePromiseOrUndefined` — Promise(异步结果,见签名)

### `getCellRawRecord`

- **签名** `getCellRawRecord(col: number, row: number): MaybePromiseOrUndefined`
- **作用** 读取单元格所在行的底层记录对象(同 getCellOriginRecord)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `MaybePromiseOrUndefined` — Promise(异步结果,见签名)

### `getRecordByCell` @AI

- **签名** `getRecordByCell: (col: number, row: number) => MaybePromiseOrUndefined`
- **作用** 读取单元格对应的整条数据记录(record)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `MaybePromiseOrUndefined` — Promise(异步结果,见签名)

### `getRecordByRowCol` @AI

- **签名** `getRecordByRowCol(col: number, row: number): any`
- **作用** 按行列号获取对应数据记录(record)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `any` — 任意值(记录/单元格值)

### `getRecordShowIndexByCell`

- **签名** `getRecordShowIndexByCell(col: number, row: number): number`
- **作用** 获取单元格所在记录在过滤/排序后的显示索引。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `number` — 数值(像素/索引/数量)

### `getRecordIndexByCell`

- **签名** `getRecordIndexByCell(col: number, row: number): number | number[]`
- **作用** 获取单元格所在记录在原始数据中的索引(树形为路径数组)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `number | number[]` — 数值(像素/索引/数量)

### `getTableIndexByRecordIndex` @AI

- **签名** `getTableIndexByRecordIndex(recordIndex: number | number[]): number`
- **作用** 由记录索引换算成表格行号(含表头/冻结偏移)。
- **参数**
  - `recordIndex: number | number[]` — 记录索引(数字;树形为层级路径数组)
- **返回** `number` — 数值(像素/索引/数量)

### `getTableIndexByField` @AI

- **签名** `getTableIndexByField(field: FieldDef): number`
- **作用** 由字段名(field)换算成表格列号。
- **参数**
  - `field: FieldDef` — 字段名(与列定义的 field 键对应)
- **返回** `number` — 数值(像素/索引/数量)

### `getCellAddrByFieldRecord`

- **签名** `getCellAddrByFieldRecord(field: FieldDef, recordIndex: number | number[]): CellAddress`
- **作用** 由字段名 + 记录索引定位单元格地址(CellAddress)。
- **参数**
  - `field: FieldDef` — 字段名(与列定义的 field 键对应)
  - `recordIndex: number | number[]` — 记录索引(数字;树形为层级路径数组)
- **返回** `CellAddress` — CellAddress

### `getRecordStartRowByRecordIndex`

- **签名** `getRecordStartRowByRecordIndex: (index: number) => number`
- **作用** 获取记录索引对应的起始行号(树形展开时占多行)。
- **参数**
  - `index: number` — 
- **返回** `number` — 数值(像素/索引/数量)

### `getBodyIndexByTableIndex`

- **签名** `getBodyIndexByTableIndex: (col: number, row: number) => CellAddress`
- **作用** 表格行列 → body 区域行列(去掉表头/冻结)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `CellAddress` — CellAddress

### `getTableIndexByBodyIndex`

- **签名** `getTableIndexByBodyIndex: (col: number, row: number) => CellAddress`
- **作用** body 区域行列 → 表格行列(与上互为逆运算)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `CellAddress` — CellAddress

### `getCellInfo` @AI

- **签名** `getCellInfo: (col: number, row: number) => Omit<MousePointerCellEvent, 'target'>`
- **作用** 读取单元格完整信息(值/坐标/类型/上下文,等价鼠标事件载荷)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `Omit<MousePointerCellEvent, 'target'>` — Omit<MousePointerCellEvent, 'target'>

### `getCellType`

- **签名** `getCellType: (col: number, row: number) => ColumnTypeOption`
- **作用** 获取单元格的列类型(ColumnTypeOption)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ColumnTypeOption` — ColumnTypeOption

### `getBodyColumnType`

- **签名** `getBodyColumnType: (col: number, row: number) => ColumnTypeOption`
- **作用** 获取 body 单元格列类型。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ColumnTypeOption` — ColumnTypeOption

### `getCellAddress`

- **签名** `getCellAddress(findTargetRecord: any | ((record: any) => boolean), field: FieldDef): CellAddress`
- **作用** 按记录(或谓词函数)+ 字段名查询单元格地址。
- **参数**
  - `findTargetRecord: any | ((record: any) => boolean), field: FieldDef` — 记录对象或判断函数 (record)=>bool
- **返回** `CellAddress` — CellAddress

### `getFieldData`

- **签名** `getFieldData(field: FieldDef | FieldFormat | undefined, col: number, row: number): FieldData`
- **作用** 按字段名读取单元格数据(支持 FieldFormat 映射)。
- **参数**
  - `field: FieldDef | FieldFormat | undefined` — 字段名(与列定义的 field 键对应)
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `FieldData` — 单元格数据(文本或数值)

### `getRawFieldData`

- **签名** `getRawFieldData(field: FieldDef | FieldFormat | undefined, col: number, row: number): FieldData`
- **作用** 按字段名读取单元格原始数据(未格式化)。
- **参数**
  - `field: FieldDef | FieldFormat | undefined` — 字段名(与列定义的 field 键对应)
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `FieldData` — 单元格数据(文本或数值)

### `getHeaderField`

- **签名** `getHeaderField: (col: number, row: number) => any | undefined`
- **作用** 读取表头单元格的字段名。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `any | undefined` — 任意值(记录/单元格值)

### `getBodyField`

- **签名** `getBodyField: (col: number, row: number) => FieldDef | undefined`
- **作用** 读取 body 单元格的字段名。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `FieldDef | undefined` — FieldDef | undefined

### `getHeaderDefine` @AI

- **签名** `getHeaderDefine: (col: number, row: number) => ColumnDefine | IRowSeriesNumber | ColumnSeriesNumber`
- **作用** 读取表头单元格的列定义(ColumnDefine)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ColumnDefine | IRowSeriesNumber | ColumnSeriesNumber` — ColumnDefine | IRowSeriesNumber | ColumnSeriesNumber

### `getHeadersDefine` @AI

- **签名** `getHeadersDefine(): ColumnsDefine`
- **作用** 获取全部表头列定义。
- **返回** `ColumnsDefine` — 列定义数组

### `getBodyColumnDefine`

- **签名** `getBodyColumnDefine: (col: number, row: number) => ColumnDefine | IRowSeriesNumber | ColumnSeriesNumber`
- **作用** 读取 body 单元格的列定义(含序号列判断)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ColumnDefine | IRowSeriesNumber | ColumnSeriesNumber` — ColumnDefine | IRowSeriesNumber | ColumnSeriesNumber

### `getHeaderDescription`

- **签名** `getHeaderDescription: (col: number, row: number) => string | undefined`
- **作用** 读取表头单元格的 description(提示文本)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `string | undefined` — 字符串(dataURL/文本等)

### `getCellHeaderPaths`

- **签名** `getCellHeaderPaths: (col: number, row: number) => ICellHeaderPaths`
- **作用** 获取单元格的表头路径(ICellHeaderPaths,定位多维表头)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ICellHeaderPaths` — ICellHeaderPaths

### `getCellLocation`

- **签名** `getCellLocation: (col: number, row: number) => CellLocation`
- **作用** 获取单元格位置类别(CellLocation: 表头/表体等)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `CellLocation` — CellLocation

### `getCellStyle`

- **签名** `getCellStyle: (col: number, row: number) => CellStyle`
- **作用** 获取单元格计算后的样式(CellStyle)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `CellStyle` — CellStyle

### `getCellIcons`

- **签名** `getCellIcons: (col: number, row: number) => ColumnIconOption[]`
- **作用** 获取单元格图标列表。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ColumnIconOption[]` — ColumnIconOption[]

### `getCellOverflowText`

- **签名** `getCellOverflowText: (col: number, row: number) => string | null`
- **作用** 读取溢出单元格的完整文本(列宽截断前的全文)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `string | null` — 字符串(dataURL/文本等)

### `getCopyValue` @AI

- **签名** `getCopyValue: (getCellValueFunction?: (col: number, row: number) => string | number) => string`
- **作用** 读取选中区域用于复制的文本(可自定义取值函数)。
- **参数**
  - `getCellValueFunction?: (col: number, row: number) => string | number` — (可选) 自定义单元格取值函数
- **返回** `string` — 字符串(dataURL/文本等)

### `isHeader`

- **签名** `isHeader: (col: number, row: number) => boolean`
- **作用** 判定单元格是否为表头。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isColumnHeader`

- **签名** `isColumnHeader: (col: number, row: number) => boolean`
- **作用** 判定单元格是否为列表头。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isRowHeader`

- **签名** `isRowHeader: (col: number, row: number) => boolean`
- **作用** 判定单元格是否为行表头(行维表头)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isCornerHeader`

- **签名** `isCornerHeader: (col: number, row: number) => boolean`
- **作用** 判定单元格是否为角头(左上角)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isSeriesNumber`

- **签名** `isSeriesNumber: (col: number, row?: number) => boolean`
- **作用** 判定单元格是否在序号列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isSeriesNumberInBody`

- **签名** `isSeriesNumberInBody: (col: number, row: number) => boolean`
- **作用** 判定 body 单元格是否在序号列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isSeriesNumberInHeader`

- **签名** `isSeriesNumberInHeader(col: number, row: number): boolean`
- **作用** 判定表头单元格是否在序号列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isHasSeriesNumber`

- **签名** `isHasSeriesNumber: () => boolean`
- **作用** 是否配置了序号列。
- **返回** `boolean` — 布尔值(是否成立)

### `isFrozenCell` @AI

- **签名** `isFrozenCell: (col: number, row: number) => { row: boolean`
- **作用** 判定单元格是否位于冻结区(返回 {row, col} 布尔)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `{ row: boolean` — { row: boolean

### `isFrozenColumn`

- **签名** `isFrozenColumn: (col: number, row?: number) => boolean`
- **作用** 判定列是否为冻结列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isLeftFrozenColumn`

- **签名** `isLeftFrozenColumn: (col: number, row?: number) => boolean`
- **作用** 判定列是否为左侧冻结列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isRightFrozenColumn`

- **签名** `isRightFrozenColumn: (col: number, row?: number) => boolean`
- **作用** 判定列是否为右侧冻结列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isFrozenRow`

- **签名** `isFrozenRow: (col: number, row?: number) => boolean`
- **作用** 判定行是否为冻结行。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isTopFrozenRow`

- **签名** `isTopFrozenRow: (col: number, row?: number) => boolean`
- **作用** 判定行是否为顶部冻结行。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isBottomFrozenRow`

- **签名** `isBottomFrozenRow: (col: number, row?: number) => boolean`
- **作用** 判定行是否为底部冻结行。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isColumnSelected` @AI

- **签名** `isColumnSelected: (col: number) => boolean`
- **作用** 判定整列是否被选中。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isRowSelected` @AI

- **签名** `isRowSelected: (row: number) => boolean`
- **作用** 判定整行是否被选中。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `cellIsInVisualView` @AI

- **签名** `cellIsInVisualView(col: number, row: number): boolean`
- **作用** 判定单元格当前是否在可视区域内。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `isAutoRowHeight`

- **签名** `isAutoRowHeight: (row?: number) => boolean`
- **作用** 判定行是否启用自适应行高。
- **参数**
  - `row?: number` — (可选) 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `hasCustomCellStyle`

- **签名** `hasCustomCellStyle(): boolean`
- **作用** 是否使用了自定义单元格样式。
- **返回** `boolean` — 布尔值(是否成立)

### `getAllBodyCells`

- **签名** `getAllBodyCells(): CellAddress[]`
- **作用** 获取全部表体单元格地址列表。
- **返回** `CellAddress[]` — 单元格地址数组

### `getAllCells`

- **签名** `getAllCells(): CellAddress[]`
- **作用** 获取全部单元格地址列表(含表头)。
- **返回** `CellAddress[]` — 单元格地址数组

### `getAllColumnHeaderCells`

- **签名** `getAllColumnHeaderCells(): CellAddress[]`
- **作用** 获取全部列表头单元格地址。
- **返回** `CellAddress[]` — 单元格地址数组

### `getAllRowHeaderCells`

- **签名** `getAllRowHeaderCells(): CellAddress[]`
- **作用** 获取全部行表头单元格地址。
- **返回** `CellAddress[]` — 单元格地址数组

### `getGroupTitleLevel`

- **签名** `getGroupTitleLevel(col: number, row: number): number | undefined`
- **作用** 获取分组标题层级。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `number | undefined` — 数值(像素/索引/数量)

### `isAggregation`

- **签名** `isAggregation(col: number, row: number): boolean`
- **作用** 判定单元格是否为聚合结果(分组/透视)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `getAggregateValuesByField`

- **签名** `getAggregateValuesByField(field: string | number): { col: number`
- **作用** 按字段获取聚合值列表(透视/分组表)。
- **参数**
  - `field: string | number` — 字段名(字符串/数字)
- **返回** `{ col: number` — { col: number

---

## B. 编辑与数据写回(17)

### `startEditCell` @AI

- **签名** `startEditCell(col?: number, row?: number, value?: string | number): void`
- **作用** 进入单元格编辑模式(可选指定行列与初始值)。
- **参数**
  - `col?: number` — (可选) 目标列号
  - `row?: number` — (可选) 目标行号
  - `value?: string | number` — (可选) 预填编辑值
- **返回** `void` — 无返回值

### `completeEditCell` @AI

- **签名** `completeEditCell(): void`
- **作用** 提交当前编辑内容,结束编辑模式。
- **返回** `void` — 无返回值

### `cancelEditCell` @AI

- **签名** `cancelEditCell(): void`
- **作用** 取消当前编辑,恢复原值。
- **返回** `void` — 无返回值

### `getEditor`

- **签名** `getEditor(col: number, row: number): IEditor<any, any>`
- **作用** 获取单元格的编辑器实例(IEditor)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `IEditor<any, any>` — IEditor<any, any>

### `isHasEditorDefine`

- **签名** `isHasEditorDefine(col: number, row: number): boolean`
- **作用** 判定单元格是否配置了编辑器。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean` — 布尔值(是否成立)

### `changeCellValue` @AI

- **签名** `changeCellValue(col: number, row: number, value: string | number | null, workOnEditableCell?: boolean, triggerEvent?: boolean, noTriggerChangeCellValuesEvent?: boolean): void`
- **作用** 修改单个单元格的值(触发数据回写与事件)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `value: string | number | null` — 要写入/设置的值
  - `workOnEditableCell?: boolean` — (可选) 是否仅对可编辑单元格生效
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
  - `noTriggerChangeCellValuesEvent?: boolean` — (可选) 是否不触发 changeCellValues 事件
- **返回** `void` — 无返回值

### `changeCellValues` @AI

- **签名** `changeCellValues(startCol: number, startRow: number, values: (string | number)[][], workOnEditableCell?: boolean, triggerEvent?: boolean, noTriggerChangeCellValuesEvent?: boolean): Promise<boolean[][]>`
- **作用** 批量修改一个矩形区域的单元格值(返回各格是否成功)。
- **参数**
  - `startCol: number` — 起始列号(含)
  - `startRow: number` — 起始行号(含)
  - `values: (string | number)[][]` — 二维值数组(外层行、内层列)
  - `workOnEditableCell?: boolean` — (可选) 是否仅对可编辑单元格生效
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
  - `noTriggerChangeCellValuesEvent?: boolean` — (可选) 是否不触发 changeCellValues 事件
- **返回** `Promise<boolean[][]>` — Promise(异步结果,见签名)

### `changeCellValuesByRanges`

- **签名** `changeCellValuesByRanges(ranges: CellRange[], value: string | number | null, workOnEditableCell?: boolean, triggerEvent?: boolean, noTriggerChangeCellValuesEvent?: boolean): Promise<void>`
- **作用** 按多个单元格范围批量改值。
- **参数**
  - `ranges: CellRange[]` — 
  - `value: string | number | null` — 要写入/设置的值
  - `workOnEditableCell?: boolean` — (可选) 是否仅对可编辑单元格生效
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
  - `noTriggerChangeCellValuesEvent?: boolean` — (可选) 是否不触发 changeCellValues 事件
- **返回** `Promise<void>` — Promise(异步结果,见签名)

### `changeSourceCellValue`

- **签名** `changeSourceCellValue(recordIndex: number | number[], field: FieldDef, value: string | number | null): void`
- **作用** 直接改数据源中某记录的某字段值(不经过单元格)。
- **参数**
  - `recordIndex: number | number[]` — 记录索引
  - `field: FieldDef` — 字段名
  - `value: string | number | null` — 要写入/设置的值
- **返回** `void` — 无返回值

### `changeCellValueByRecord`

- **签名** `changeCellValueByRecord(recordIndex: number | number[], field: FieldDef, value: string | number | null, options?: { triggerEvent?: boolean; noTriggerChangeCellValuesEvent?: boolean; autoRefresh?: boolean; }): void`
- **作用** 按记录索引 + 字段修改值(带选项)。
- **参数**
  - `recordIndex: number | number[]` — 记录索引(数字;树形为层级路径数组)
  - `field: FieldDef` — 字段名(与列定义的 field 键对应)
  - `value: string | number | null` — 要写入/设置的值
  - `options?: { triggerEvent?: boolean; noTriggerChangeCellValuesEvent?: boolean; autoRefresh?: boolean; }` — (可选) 可选配置对象(见签名类型)
- **返回** `void` — 无返回值

### `changeCellValueBySource`

- **签名** `changeCellValueBySource(recordIndex: number | number[], field: FieldDef, value: string | number | null, triggerEvent?: boolean, noTriggerChangeCellValuesEvent?: boolean): void`
- **作用** 按记录索引 + 字段修改数据源值。
- **参数**
  - `recordIndex: number | number[]` — 记录索引(数字;树形为层级路径数组)
  - `field: FieldDef` — 字段名(与列定义的 field 键对应)
  - `value: string | number | null` — 要写入/设置的值
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
  - `noTriggerChangeCellValuesEvent?: boolean` — (可选) 是否不触发 changeCellValues 事件
- **返回** `void` — 无返回值

### `changeCellValuesByRecords`

- **签名** `changeCellValuesByRecords(changeValues: { recordIndex: number | number[]; field: FieldDef; value: string | number | null; }[], options?: { triggerEvent?: boolean; noTriggerChangeCellValuesEvent?: boolean; autoRefresh?: boolean; }): void`
- **作用** 按多条记录批量修改值。
- **参数**
  - `changeValues: { recordIndex: number | number[]; field: FieldDef; value: string | number | null; }[]` — 改值数组[{recordIndex, field, value}]
  - `options?: { triggerEvent?: boolean; noTriggerChangeCellValuesEvent?: boolean; autoRefresh?: boolean; }` — (可选) 可选配置对象(见签名类型)
- **返回** `void` — 无返回值

### `changeCellValuesBySource`

- **签名** `changeCellValuesBySource(changeValues: { recordIndex: number | number[]; field: FieldDef; value: string | number | null; }[], triggerEvent?: boolean, noTriggerChangeCellValuesEvent?: boolean): void`
- **作用** 按多条记录批量改数据源值。
- **参数**
  - `changeValues: { recordIndex: number | number[]; field: FieldDef; value: string | number | null; }[]` — 改值数组[{recordIndex, field, value}]
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
  - `noTriggerChangeCellValuesEvent?: boolean` — (可选) 是否不触发 changeCellValues 事件
- **返回** `void` — 无返回值

### `refreshAfterSourceChange`

- **签名** `refreshAfterSourceChange(options?: { reapplyFilter?: boolean; reapplySort?: boolean; clearRowHeightCache?: boolean; }): void`
- **作用** 外部改完数据源后刷新表格(可重新过滤/排序)。
- **参数**
  - `options?: { reapplyFilter?: boolean; reapplySort?: boolean; clearRowHeightCache?: boolean; }` — (可选) 可选配置对象(见签名类型)
- **返回** `void` — 无返回值

### `updateCellContent`

- **签名** `updateCellContent: (col: number, row: number) => void`
- **作用** 刷新单元格内容(局部重绘)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `updateCellContentRange`

- **签名** `updateCellContentRange: (startCol: number, startRow: number, endCol: number, endRow: number) => void`
- **作用** 刷新矩形区域内单元格内容。
- **参数**
  - `startCol: number` — 起始列号(含)
  - `startRow: number` — 起始行号(含)
  - `endCol: number` — 结束列号(含)
  - `endRow: number` — 结束行号(含)
- **返回** `void` — 无返回值

### `updateCellContentRanges`

- **签名** `updateCellContentRanges: (ranges: CellRange[]) => void`
- **作用** 刷新多个范围内的单元格内容。
- **参数**
  - `ranges: CellRange[]` — 单元格范围数组 CellRange[]
- **返回** `void` — 无返回值

---

## C. 数据与记录(21)

### `setRecords` @AI

- **签名** `setRecords(records: Array<any>, option?: { sortState?: SortState | SortState[] | null; }): void`
- **作用** 整体替换数据记录(可同时设置排序状态)。
- **参数**
  - `records: Array<any>` — 数据记录数组(对象数组,key 对应列 field)
  - `option?: { sortState?: SortState | SortState[] | null; }` — (可选) 配置对象(见签名类型)
- **返回** `void` — 无返回值

### `setRecordChildren`

- **签名** `setRecordChildren(records: any[], col: number, row: number, recalculateColWidths?: boolean): void`
- **作用** 设置某树形记录的子树数据。
- **参数**
  - `records: any[]` — 子树记录数组
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `recalculateColWidths?: boolean` — (可选) 是否重新计算列宽
- **返回** `void` — 无返回值

### `addRecord`

- **签名** `addRecord(record: any, recordIndex?: number | number[], triggerEvent?: boolean): void`
- **作用** 追加/插入一条记录。
- **参数**
  - `record: any` — 记录对象
  - `recordIndex?: number | number[]` — (可选) 记录索引(数字;树形为层级路径数组)
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
- **返回** `void` — 无返回值

### `addRecords`

- **签名** `addRecords(records: any[], recordIndex?: number | number[], triggerEvent?: boolean): void`
- **作用** 批量追加/插入记录。
- **参数**
  - `records: any[]` — 数据记录数组(对象数组,key 对应列 field)
  - `recordIndex?: number | number[]` — (可选) 记录索引(数字;树形为层级路径数组)
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
- **返回** `void` — 无返回值

### `deleteRecords`

- **签名** `deleteRecords(recordIndexs: number[] | number[][], triggerEvent?: boolean): void`
- **作用** 删除指定记录(按索引)。
- **参数**
  - `recordIndexs: number[] | number[][]` — 记录索引或索引数组
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
- **返回** `void` — 无返回值

### `updateRecords`

- **签名** `updateRecords(records: any[], recordIndexs: (number | number[])[], triggerEvent?: boolean): void`
- **作用** 按索引更新记录数据。
- **参数**
  - `records: any[]` — 数据记录数组(对象数组,key 对应列 field)
  - `recordIndexs: (number | number[])[]` — 记录索引数组
  - `triggerEvent?: boolean` — (可选) 是否触发对应事件
- **返回** `void` — 无返回值

### `changeRecordOrder`

- **签名** `changeRecordOrder(sourceIndex: number, targetIndex: number): void`
- **作用** 调整记录顺序(拖拽排序后的落位)。
- **参数**
  - `sourceIndex: number` — 源记录索引
  - `targetIndex: number` — 目标记录索引
- **返回** `void` — 无返回值

### `getFilteredRecords`

- **签名** `getFilteredRecords(): any[]`
- **作用** 获取过滤后的记录列表。
- **返回** `any[]` — 任意值(记录/单元格值)

### `updateFilterRules`

- **签名** `updateFilterRules(filterRules: FilterRules, options?: { clearRowHeightCache?: boolean; clearForceVisibleRecords?: boolean; onFilterRecordsEnd?: (records: any[]) => any[]; }): void`
- **作用** 更新过滤规则。
- **参数**
  - `filterRules: FilterRules` — 过滤规则 FilterRules
  - `options?: { clearRowHeightCache?: boolean; clearForceVisibleRecords?: boolean; onFilterRecordsEnd?: (records: any[]) => any[]; }` — (可选) 可选配置对象(见签名类型)
- **返回** `void` — 无返回值

### `getBodyRowIndexByRecordIndex`

- **签名** `getBodyRowIndexByRecordIndex(index: number | number[]): number`
- **作用** 由记录索引换算 body 行号(去表头偏移)。
- **参数**
  - `index: number | number[]` — 记录索引
- **返回** `number` — 数值(像素/索引/数量)

### `getCheckboxState` @AI

- **签名** `getCheckboxState(field?: string | number): any[]`
- **作用** 读取某字段复选框状态数组。
- **参数**
  - `field?: string | number` — (可选) 字段名(可选)
- **返回** `any[]` — 任意值(记录/单元格值)

### `getCellCheckboxState` @AI

- **签名** `getCellCheckboxState(col: number, row: number): boolean | "indeterminate"`
- **作用** 读取单元格复选框状态(含 indeterminate)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean | "indeterminate"` — 布尔值(是否成立)

### `setCellCheckboxState` @AI

- **签名** `setCellCheckboxState(col: number, row: number, checked: boolean | 'indeterminate'): void`
- **作用** 设置单元格复选框状态。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `checked: boolean | 'indeterminate'` — true/false/'indeterminate'
- **返回** `void` — 无返回值

### `getRadioState`

- **签名** `getRadioState(field?: string | number): number | boolean | Record<string | number, number | boolean | Record<number, number>>`
- **作用** 读取单选框状态。
- **参数**
  - `field?: string | number` — (可选) 字段名(与列定义的 field 键对应)
- **返回** `number | boolean | Record<string | number, number | boolean | Record<number, number>>` — 数值(像素/索引/数量)

### `getCellRadioState`

- **签名** `getCellRadioState(col: number, row: number): boolean | number`
- **作用** 读取单元格单选框状态。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean | number` — 布尔值(是否成立)

### `setCellRadioState`

- **签名** `setCellRadioState(col: number, row: number, index?: number): void`
- **作用** 设置单元格单选框状态。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `index?: number` — (可选) 单选索引(可选)
- **返回** `void` — 无返回值

### `getSwitchState`

- **签名** `getSwitchState(field?: string | number): any[]`
- **作用** 读取开关状态。
- **参数**
  - `field?: string | number` — (可选) 字段名(与列定义的 field 键对应)
- **返回** `any[]` — 任意值(记录/单元格值)

### `getCellSwitchState`

- **签名** `getCellSwitchState(col: number, row: number): boolean | "indeterminate"`
- **作用** 读取单元格开关状态。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `boolean | "indeterminate"` — 布尔值(是否成立)

### `setCellSwitchState`

- **签名** `setCellSwitchState(col: number, row: number, checked: boolean | 'indeterminate'): void`
- **作用** 设置单元格开关状态。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `checked: boolean | 'indeterminate'` — true/false/'indeterminate'
- **返回** `void` — 无返回值

### `updateSortState`

- **签名** `updateSortState(sortState: SortState[] | SortState | null, executeSort?: boolean): void`
- **作用** 更新排序状态(可立即执行)。
- **参数**
  - `sortState: SortState[] | SortState | null` — 排序状态(对象/数组/null)
  - `executeSort?: boolean` — (可选) 是否立即执行排序
- **返回** `void` — 无返回值

### `setSortedIndexMap`

- **签名** `setSortedIndexMap: (field: FieldDef, filedMap: ISortedMapItem) => void`
- **作用** 设置排序索引映射(自定义排序)。
- **参数**
  - `field: FieldDef` — 字段名
  - `filedMap: ISortedMapItem` — 排序映射 ISortedMapItem
- **返回** `void` — 无返回值

---

## D. 列与宽度(19)

### `updateColumns` @AI

- **签名** `updateColumns(columns: ColumnsDefine, options?: { clearColWidthCache?: boolean; clearRowHeightCache?: boolean; }): void`
- **作用** 整体更新列定义。
- **参数**
  - `columns: ColumnsDefine` — 新列定义 ColumnsDefine
  - `options?: { clearColWidthCache?: boolean; clearRowHeightCache?: boolean; }` — (可选) 可选配置对象(见签名类型)
- **返回** `void` — 无返回值

### `addColumns` @AI

- **签名** `addColumns(toAddColumns: ColumnDefine[], colIndex?: number, isMaintainArrayData?: boolean): void`
- **作用** 在指定位置追加列定义。
- **参数**
  - `toAddColumns: ColumnDefine[]` — 新增列定义数组
  - `colIndex?: number` — (可选) 插入位置列索引(可选)
  - `isMaintainArrayData?: boolean` — (可选) 是否保持数据数组结构(可选)
- **返回** `void` — 无返回值

### `deleteColumns` @AI

- **签名** `deleteColumns(deleteColIndexs: number[], isMaintainArrayData?: boolean): void`
- **作用** 按索引删除列定义。
- **参数**
  - `deleteColIndexs: number[]` — 要删除的列索引数组
  - `isMaintainArrayData?: boolean` — (可选) 是否保持数据数组结构(可选)
- **返回** `void` — 无返回值

### `getColWidth` @AI

- **签名** `getColWidth: (col: number) => number`
- **作用** 读取指定列当前宽度(像素)。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `number` — 数值(像素/索引/数量)

### `getColWidthDefined`

- **签名** `getColWidthDefined: (col: number) => string | number`
- **作用** 读取列宽定义(可能为 'auto'/百分比等)。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `string | number` — 字符串(dataURL/文本等)

### `getColWidthDefinedNumber`

- **签名** `getColWidthDefinedNumber: (col: number) => number`
- **作用** 读取列宽定义的数值部分。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `number` — 数值(像素/索引/数量)

### `setColWidth`

- **签名** `setColWidth: (col: number, width: number) => void`
- **作用** 设置列宽(像素)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `width: number` — 宽度(像素)
- **返回** `void` — 无返回值

### `getColsWidth` @AI

- **签名** `getColsWidth: (startCol: number, endCol: number) => number`
- **作用** 计算 [startCol, endCol] 区间总宽度。
- **参数**
  - `startCol: number` — 起始列号(含)
  - `endCol: number` — 结束列号(含)
- **返回** `number` — 数值(像素/索引/数量)

### `getColsWidths`

- **签名** `getColsWidths: () => number[]`
- **作用** 读取全部列宽数组。
- **返回** `number[]` — 数值(像素/索引/数量)

### `getAllColsWidth`

- **签名** `getAllColsWidth: () => number`
- **作用** 读取所有列总宽度。
- **返回** `number` — 数值(像素/索引/数量)

### `getMaxColWidth`

- **签名** `getMaxColWidth: (col: number) => number`
- **作用** 读取列最大宽度限制。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `number` — 数值(像素/索引/数量)

### `setMaxColWidth`

- **签名** `setMaxColWidth: (col: number, maxwidth: string | number) => void`
- **作用** 设置列最大宽度。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `maxwidth: string | number` — 最大宽度(数字或字符串)
- **返回** `void` — 无返回值

### `getMinColWidth`

- **签名** `getMinColWidth: (col: number) => number`
- **作用** 读取列最小宽度限制。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `number` — 数值(像素/索引/数量)

### `setMinColWidth`

- **签名** `setMinColWidth: (col: number, minwidth: string | number) => void`
- **作用** 设置列最小宽度。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `minwidth: string | number` — 最小宽度(数字或字符串)
- **返回** `void` — 无返回值

### `setMinMaxLimitWidth`

- **签名** `setMinMaxLimitWidth(col: number, minWidth: number, maxWidth: number): void`
- **作用** 一次性设置列最小与最大宽度限制。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `minWidth: number` — 最小宽度
  - `maxWidth: number` — 最大宽度
- **返回** `void` — 无返回值

### `checkHasColumnAutoWidth`

- **签名** `checkHasColumnAutoWidth: () => boolean`
- **作用** 是否有列配置了自适应宽度。
- **返回** `boolean` — 布尔值(是否成立)

### `getDefaultColumnWidth`

- **签名** `getDefaultColumnWidth: (col: number) => number | 'auto'`
- **作用** 读取默认列宽。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `number | 'auto'` — 数值(像素/索引/数量)

### `registerCustomCellStyle`

- **签名** `registerCustomCellStyle: (customStyleId: string, customStyle: ColumnStyleOption | undefined | null) => void`
- **作用** 注册自定义单元格样式(按 id)。
- **参数**
  - `customStyleId: string` — 样式 ID
  - `customStyle: ColumnStyleOption | undefined | null` — 自定义列样式(可 null)
- **返回** `void` — 无返回值

### `arrangeCustomCellStyle`

- **签名** `arrangeCustomCellStyle: (cellPos: { col?: number; row?: number; range?: CellRange; }, customStyleId: string) => void`
- **作用** 把已注册的自定义样式应用到单元格/区域。
- **参数**
  - `cellPos: { col?: number; row?: number; range?: CellRange; }` — 定位 {col?, row?, range?}
  - `customStyleId: string` — 已注册的样式 ID
- **返回** `void` — 无返回值

---

## E. 行与高度(6)

### `getRowHeight` @AI

- **签名** `getRowHeight: (row: number) => number`
- **作用** 读取指定行高(像素)。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `number` — 数值(像素/索引/数量)

### `setRowHeight`

- **签名** `setRowHeight: (row: number, height: number) => void`
- **作用** 设置行高。
- **参数**
  - `row: number` — 行号(从 0 开始)
  - `height: number` — 高度(像素)
- **返回** `void` — 无返回值

### `getRowsHeight` @AI

- **签名** `getRowsHeight: (startRow: number, endRow: number) => number`
- **作用** 计算 [startRow, endRow] 区间总高度。
- **参数**
  - `startRow: number` — 起始行号(含)
  - `endRow: number` — 结束行号(含)
- **返回** `number` — 数值(像素/索引/数量)

### `getAllRowsHeight`

- **签名** `getAllRowsHeight: () => number`
- **作用** 读取所有行总高度。
- **返回** `number` — 数值(像素/索引/数量)

### `getDefaultRowHeight`

- **签名** `getDefaultRowHeight: (row: number) => number | 'auto'`
- **作用** 读取默认行高(可能为 'auto')。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `number | 'auto'` — 数值(像素/索引/数量)

### `updateAutoWrapText`

- **签名** `updateAutoWrapText(row: number): void`
- **作用** 更新整行自动换行后的行高。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

---

## F. 滚动与视口(23)

### `scrollToCell` @AI

- **签名** `scrollToCell: (cellAddr: { col?: number; row?: number; }, animationOption?: ITableAnimationOption | boolean) => void`
- **作用** 滚动到指定单元格(可带动画)。
- **参数**
  - `cellAddr: { col?: number; row?: number; }` — 目标地址 {col?, row?}
  - `animationOption?: ITableAnimationOption | boolean` — (可选) 动画:布尔或配置
- **返回** `void` — 无返回值

### `scrollToRow` @AI

- **签名** `scrollToRow: (row: number, animationOption?: ITableAnimationOption | boolean) => void`
- **作用** 滚动到指定行。
- **参数**
  - `row: number` — 行号(从 0 开始)
  - `animationOption?: ITableAnimationOption | boolean` — (可选) 滚动/操作动画:布尔值或动画配置
- **返回** `void` — 无返回值

### `scrollToCol` @AI

- **签名** `scrollToCol: (col: number, animationOption?: ITableAnimationOption | boolean) => void`
- **作用** 滚动到指定列。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `animationOption?: ITableAnimationOption | boolean` — (可选) 滚动/操作动画:布尔值或动画配置
- **返回** `void` — 无返回值

### `getScrollLeft` @AI

- **签名** `getScrollLeft(): number`
- **作用** 读取水平滚动偏移。
- **返回** `number` — 数值(像素/索引/数量)

### `getScrollTop` @AI

- **签名** `getScrollTop(): number`
- **作用** 读取垂直滚动偏移。
- **返回** `number` — 数值(像素/索引/数量)

### `setScrollLeft` @AI

- **签名** `setScrollLeft(left: number): void`
- **作用** 设置水平滚动偏移。
- **参数**
  - `left: number` — 滚动偏移(像素)
- **返回** `void` — 无返回值

### `setScrollTop` @AI

- **签名** `setScrollTop(top: number): void`
- **作用** 设置垂直滚动偏移。
- **参数**
  - `top: number` — 滚动偏移(像素)
- **返回** `void` — 无返回值

### `getTargetScrollTop`

- **签名** `getTargetScrollTop(targetTop: number): number`
- **作用** 由目标内容顶部位置换算 scrollTop。
- **参数**
  - `targetTop: number` — 目标内容顶部坐标
- **返回** `number` — 数值(像素/索引/数量)

### `getVisibleRect`

- **签名** `getVisibleRect(): Rect`
- **作用** 读取当前可视区域矩形(相对画布)。
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getBodyVisibleCellRange` @AI

- **签名** `getBodyVisibleCellRange: () => { rowStart: number`
- **作用** 读取 body 可视单元格范围 {rowStart,colStart,rowEnd,colEnd}。
- **返回** `{ rowStart: number` — { rowStart: number

### `getBodyVisibleRowRange` @AI

- **签名** `getBodyVisibleRowRange: (start_deltaY?: number, end_deltaY?: number) => { rowStart: number`
- **作用** 读取 body 可视行范围。
- **参数**
  - `start_deltaY?: number` — (可选) 起始 Y 偏移增量
  - `end_deltaY?: number` — (可选) 结束 Y 偏移增量
- **返回** `{ rowStart: number` — { rowStart: number

### `getBodyVisibleColRange` @AI

- **签名** `getBodyVisibleColRange: (start_deltaX?: number, end_deltaX?: number) => { colStart: number`
- **作用** 读取 body 可视列范围。
- **参数**
  - `start_deltaX?: number` — (可选) 起始 X 偏移增量
  - `end_deltaX?: number` — (可选) 结束 X 偏移增量
- **返回** `{ colStart: number` — { colStart: number

### `getVisibleCellRangeRelativeRect`

- **签名** `getVisibleCellRangeRelativeRect: (cellRange: CellRange | CellAddress) => Rect`
- **作用** 读取单元格范围在可视区的相对矩形。
- **参数**
  - `cellRange: CellRange | CellAddress` — 单元格范围或单个单元格地址
- **返回** `Rect` — 矩形 {x,y,width,height}

### `disableScroll`

- **签名** `disableScroll: () => void`
- **作用** 禁用表格滚动。
- **返回** `void` — 无返回值

### `enableScroll`

- **签名** `enableScroll: () => void`
- **作用** 启用表格滚动。
- **返回** `void` — 无返回值

### `shouldVScrollBarWidthShow`

- **签名** `shouldVScrollBarWidthShow(): boolean`
- **作用** 判定是否显示垂直滚动条。
- **返回** `boolean` — 布尔值(是否成立)

### `shouldHScrollBarWidthShow`

- **签名** `shouldHScrollBarWidthShow(): boolean`
- **作用** 判定是否显示水平滚动条。
- **返回** `boolean` — 布尔值(是否成立)

### `updateViewBox`

- **签名** `updateViewBox(): void`
- **作用** 更新视口(布局变化后)。
- **返回** `void` — 无返回值

### `setViewBoxTransform`

- **签名** `setViewBoxTransform(): void`
- **作用** 设置视口变换。
- **返回** `void` — 无返回值

### `setContentInsetXY`

- **签名** `setContentInsetXY(x: number, y: number): void`
- **作用** 设置内容内边距(X/Y 偏移)。
- **参数**
  - `x: number` — X 偏移(像素)
  - `y: number` — Y 偏移(像素)
- **返回** `void` — 无返回值

### `getDrawRange`

- **签名** `getDrawRange: () => Rect`
- **作用** 读取当前绘制范围矩形。
- **返回** `Rect` — 矩形 {x,y,width,height}

### `resize`

- **签名** `resize: () => void`
- **作用** 重算尺寸并重渲染(容器尺寸变化时调用)。
- **返回** `void` — 无返回值

### `setCanvasSize`

- **签名** `setCanvasSize: (width: number, height: number) => void`
- **作用** 手动设置画布尺寸。
- **参数**
  - `width: number` — 画布宽度
  - `height: number` — 画布高度
- **返回** `void` — 无返回值

---

## G. 选择与选区(13)

### `selectCell`

- **签名** `selectCell: (col: number, row: number, isShift?: boolean, isCtrl?: boolean, makeSelectCellVisible?: boolean, skipBodyMerge?: boolean) => void`
- **作用** 选中单个单元格(可带 Shift/Ctrl 语义与滚动定位)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `isShift?: boolean` — (可选) Shift(扩展选区)
  - `isCtrl?: boolean` — (可选) Ctrl(追加选区)
  - `makeSelectCellVisible?: boolean` — (可选) 是否滚动到可见
  - `skipBodyMerge?: boolean` — (可选) 是否跳过 body 合并格
- **返回** `void` — 无返回值

### `selectCells`

- **签名** `selectCells: (cellRanges: CellRange[]) => void`
- **作用** 按范围数组选中多块区域。
- **参数**
  - `cellRanges: CellRange[]` — 单元格范围数组 CellRange[]
- **返回** `void` — 无返回值

### `selectRow`

- **签名** `selectRow(row: number): void`
- **作用** 选中整行。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `selectCol`

- **签名** `selectCol(col: number): void`
- **作用** 选中整列。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `void` — 无返回值

### `clearSelected`

- **签名** `clearSelected: () => void`
- **作用** 清空选区。
- **返回** `void` — 无返回值

### `getSelectedCellRanges` @AI

- **签名** `getSelectedCellRanges: () => CellRange[]`
- **作用** 读取选中区域列表(CellRange[],AI 回读验证首选)。
- **返回** `CellRange[]` — 单元格范围数组

### `getSelectedCellInfos` @AI

- **签名** `getSelectedCellInfos: () => CellInfo[][]`
- **作用** 读取选中单元格信息矩阵(CellInfo[][])。
- **返回** `CellInfo[][]` — CellInfo[][]

### `startDragSelectCol`

- **签名** `startDragSelectCol(col: number): void`
- **作用** 开始列拖拽选择。
- **参数**
  - `col: number` — 列号(从 0 开始)
- **返回** `void` — 无返回值

### `dragSelectCol`

- **签名** `dragSelectCol(col: number, row: number): void`
- **作用** 拖拽过程中更新列选择。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `startDragSelectRow`

- **签名** `startDragSelectRow(row: number): void`
- **作用** 开始行拖拽选择。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `dragSelectRow`

- **签名** `dragSelectRow(row: number): void`
- **作用** 拖拽过程中更新行选择。
- **参数**
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `endDragSelect`

- **签名** `endDragSelect(): void`
- **作用** 结束拖拽选择。
- **返回** `void` — 无返回值

### `isCellRangeEqual`

- **签名** `isCellRangeEqual: (col: number, row: number, targetCol: number, targetRow: number) => boolean`
- **作用** 判定两个单元格范围是否相等。
- **参数**
  - `col: number` — 目标列
  - `row: number` — 目标行
  - `targetCol: number` — 对比目标列
  - `targetRow: number` — 对比目标行
- **返回** `boolean` — 布尔值(是否成立)

---

## H. 合并单元格(8)

### `mergeCells`

- **签名** `mergeCells(startCol: number, startRow: number, endCol: number, endRow: number): void`
- **作用** 合并矩形区域为一个大单元格。
- **参数**
  - `startCol: number` — 起始列号(含)
  - `startRow: number` — 起始行号(含)
  - `endCol: number` — 结束列号(含)
  - `endRow: number` — 结束行号(含)
- **返回** `void` — 无返回值

### `unmergeCells`

- **签名** `unmergeCells(startCol: number, startRow: number, endCol: number, endRow: number): void`
- **作用** 取消矩形区域的合并。
- **参数**
  - `startCol: number` — 起始列号(含)
  - `startRow: number` — 起始行号(含)
  - `endCol: number` — 结束列号(含)
  - `endRow: number` — 结束行号(含)
- **返回** `void` — 无返回值

### `getMergeCellRect`

- **签名** `getMergeCellRect: (col: number, row: number) => Rect`
- **作用** 读取单元格所属合并组的矩形。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getCellRange` @AI

- **签名** `getCellRange: (col: number, row: number) => CellRange`
- **作用** 读取单元格所属合并范围(CellRange)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `CellRange` — 单元格范围

### `hasCustomMerge`

- **签名** `hasCustomMerge: () => boolean`
- **作用** 是否配置了自定义合并。
- **返回** `boolean` — 布尔值(是否成立)

### `getCustomMerge`

- **签名** `getCustomMerge: (col: number, row: number) => undefined | (Omit<CustomMerge, 'style'> & { style?: FullExtendStyle`
- **作用** 获取单元格的自定义合并信息。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `undefined | (Omit<CustomMerge, 'style'> & { style?: FullExtendStyle` — undefined | (Omit<CustomMerge, 'style'> & { style?: FullExtendStyle

### `getCustomMergeValue`

- **签名** `getCustomMergeValue(col: number, row: number): any`
- **作用** 读取自定义合并单元格的合并值。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `any` — 任意值(记录/单元格值)

### `getCellRangeByField` @AI

- **签名** `getCellRangeByField(field: FieldDef, index: number): CellRange | null`
- **作用** 按字段名 + 记录索引获取单元格范围。
- **参数**
  - `field: FieldDef` — 字段名(与列定义的 field 键对应)
  - `index: number` — 记录索引
- **返回** `CellRange | null` — 单元格范围

---

## I. 树形与层级(6)

### `toggleHierarchyState`

- **签名** `toggleHierarchyState(col: number, row: number, recalculateColWidths?: boolean): void`
- **作用** 展开/折叠树形节点的层级。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `recalculateColWidths?: boolean` — (可选) 是否重算列宽(可选)
- **返回** `void` — 无返回值

### `getHierarchyState`

- **签名** `getHierarchyState(col: number, row: number): HierarchyState`
- **作用** 读取单元格所在节点的层级状态(展开/折叠)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `HierarchyState` — HierarchyState

### `getRecordHierarchyState`

- **签名** `getRecordHierarchyState(col: number, row: number): HierarchyState`
- **作用** 读取记录在树形结构中的层级状态。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `HierarchyState` — HierarchyState

### `setLoadingHierarchyState`

- **签名** `setLoadingHierarchyState(col: number, row: number): void`
- **作用** 设置节点的加载中状态(异步展开)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `expandAllTreeNode`

- **签名** `expandAllTreeNode(): void`
- **作用** 展开全部树节点。
- **返回** `void` — 无返回值

### `collapseAllTreeNode`

- **签名** `collapseAllTreeNode(): void`
- **作用** 折叠全部树节点。
- **返回** `void` — 无返回值

---

## J. 冻结行列(11)

### `setFrozenColCount`

- **签名** `setFrozenColCount: (count: number) => void`
- **作用** 设置左侧冻结列数。
- **参数**
  - `count: number` — 冻结列数
- **返回** `void` — 无返回值

### `getFrozenRowsHeight` @AI

- **签名** `getFrozenRowsHeight: () => number`
- **作用** 读取顶部冻结区总高度。
- **返回** `number` — 数值(像素/索引/数量)

### `getFrozenColsWidth` @AI

- **签名** `getFrozenColsWidth: () => number`
- **作用** 读取左侧冻结区总宽度。
- **返回** `number` — 数值(像素/索引/数量)

### `getFrozenColsContentWidth`

- **签名** `getFrozenColsContentWidth: () => number`
- **作用** 读取左侧冻结列内容总宽度。
- **返回** `number` — 数值(像素/索引/数量)

### `getFrozenColsOffset`

- **签名** `getFrozenColsOffset: () => number`
- **作用** 读取左侧冻结区偏移。
- **返回** `number` — 数值(像素/索引/数量)

### `getFrozenColsScrollLeft`

- **签名** `getFrozenColsScrollLeft: () => number`
- **作用** 读取冻结列滚动偏移。
- **返回** `number` — 数值(像素/索引/数量)

### `getBottomFrozenRowsHeight`

- **签名** `getBottomFrozenRowsHeight: () => number`
- **作用** 读取底部冻结区总高度。
- **返回** `number` — 数值(像素/索引/数量)

### `getRightFrozenColsWidth`

- **签名** `getRightFrozenColsWidth: () => number`
- **作用** 读取右侧冻结区总宽度。
- **返回** `number` — 数值(像素/索引/数量)

### `getRightFrozenColsContentWidth`

- **签名** `getRightFrozenColsContentWidth: () => number`
- **作用** 读取右侧冻结列内容总宽度。
- **返回** `number` — 数值(像素/索引/数量)

### `getRightFrozenColsOffset`

- **签名** `getRightFrozenColsOffset: () => number`
- **作用** 读取右侧冻结区偏移。
- **返回** `number` — 数值(像素/索引/数量)

### `getRightFrozenColsScrollLeft`

- **签名** `getRightFrozenColsScrollLeft: () => number`
- **作用** 读取右侧冻结列滚动偏移。
- **返回** `number` — 数值(像素/索引/数量)

---

## K. 渲染 / 刷新 / 更新选项(17)

### `render`

- **签名** `render: () => void`
- **作用** 同步重渲染表格。
- **返回** `void` — 无返回值

### `renderAsync`

- **签名** `renderAsync(): void`
- **作用** 异步重渲染(不阻塞主线程)。
- **返回** `void` — 无返回值

### `renderWithRecreateCells`

- **签名** `renderWithRecreateCells: () => void`
- **作用** 重建单元格并重渲染(布局结构变化时)。
- **返回** `void` — 无返回值

### `throttleInvalidate`

- **签名** `throttleInvalidate: () => void`
- **作用** 节流式失效并请求重绘(高频操作用)。
- **返回** `void` — 无返回值

### `refreshHeader`

- **签名** `refreshHeader(): void`
- **作用** 刷新表头(列定义变化后)。
- **返回** `void` — 无返回值

### `refreshRowColCount`

- **签名** `refreshRowColCount(): void`
- **作用** 刷新行列数量。
- **返回** `void` — 无返回值

### `updateOption` @AI

- **签名** `updateOption(options: ListTableConstructorOptions, updateConfig?: { clearColWidthCache?: boolean; clearRowHeightCache?: boolean; }): Promise<unknown>`
- **作用** 热更新表格配置项(可清列宽/行高缓存)。
- **参数**
  - `options: ListTableConstructorOptions` — 新配置 ListTableConstructorOptions
  - `updateConfig?: { clearColWidthCache?: boolean; clearRowHeightCache?: boolean; }` — (可选) {clearColWidthCache?, clearRowHeightCache?}
- **返回** `Promise<unknown>` — Promise(异步结果,见签名)

### `updatePagination`

- **签名** `updatePagination(pagination: IPagination): void`
- **作用** 更新分页配置。
- **参数**
  - `pagination: IPagination` — 分页配置(IPagination)
- **返回** `void` — 无返回值

### `setPixelRatio`

- **签名** `setPixelRatio: (pixelRatio: number) => void`
- **作用** 设置渲染像素比(高清屏适配)。
- **参数**
  - `pixelRatio: number` — 像素比
- **返回** `void` — 无返回值

### `release`

- **签名** `release(): void`
- **作用** 释放表格资源。
- **返回** `void` — 无返回值

### `addReleaseObj`

- **签名** `addReleaseObj: (releaseObj: { release: () => void; }) => void`
- **作用** 注册随表格一起释放的对象。
- **参数**
  - `releaseObj: { release: () => void; }` — { release: () => void }
- **返回** `void` — 无返回值

### `dispose`

- **签名** `dispose(): void`
- **作用** 销毁表格实例(释放事件/画布/容器)。
- **返回** `void` — 无返回值

### `clearCellStyleCache`

- **签名** `clearCellStyleCache: () => void`
- **作用** 清空单元格样式缓存。
- **返回** `void` — 无返回值

### `clearRowHeightCache`

- **签名** `clearRowHeightCache: () => void`
- **作用** 清空行高缓存。
- **返回** `void` — 无返回值

### `clearColWidthCache`

- **签名** `clearColWidthCache: () => void`
- **作用** 清空列宽缓存。
- **返回** `void` — 无返回值

### `clearCorrectTimer`

- **签名** `clearCorrectTimer(): void`
- **作用** 清除滚动校正定时器。
- **返回** `void` — 无返回值

### `syncColumnsStateFromLayoutMap`

- **签名** `syncColumnsStateFromLayoutMap(): void`
- **作用** 从布局映射同步列状态(列宽/顺序)。
- **返回** `void` — 无返回值

---

## L. 导出与图片(10)

### `exportImg`

- **签名** `exportImg: () => string`
- **作用** 导出整表为图片(dataURL)。
- **返回** `string` — 字符串(dataURL/文本等)

### `exportCellImg`

- **签名** `exportCellImg: (col: number, row: number, options?: { disableBackground?: boolean; disableBorder?: boolean; }) => string`
- **作用** 导出单个单元格为图片(可去背景/边框)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `options?: { disableBackground?: boolean; disableBorder?: boolean; }` — (可选) 可选配置对象(见签名类型)
- **返回** `string` — 字符串(dataURL/文本等)

### `exportCellRangeImg`

- **签名** `exportCellRangeImg: (cellRange: CellRange) => string`
- **作用** 导出单元格范围区域为图片。
- **参数**
  - `cellRange: CellRange` — 单元格范围 CellRange
- **返回** `string` — 字符串(dataURL/文本等)

### `exportCanvas`

- **签名** `exportCanvas: () => HTMLCanvasElement`
- **作用** 导出画布为 HTMLCanvasElement。
- **返回** `HTMLCanvasElement` — canvas 元素

### `exportToCsv` @AI

- **签名** `exportToCsv(options?: { delimiter?: string; isFormatNumber?: boolean; skipHeader?: boolean; columnKeys?: string[] }): Promise<{ data: string; download: () => void }>`
- **作用** 导出 CSV(自定义分隔符/是否格式化/是否含表头/指定列)。
- **参数**
  - `options?: { delimiter?: string; isFormatNumber?: boolean; skipHeader?: boolean; columnKeys?: string[] }` — (可选) 可选配置对象(见签名类型)
- **返回** `Promise<{ data: string; download: () => void }>` — Promise(异步结果,见签名)

### `exportToExcel` @AI

- **签名** `exportToExcel(options?: { formatExportFile?: (data: string) => Promise<{ download: () => void }>; isFormatNumber?: boolean; skipHeader?: boolean; columnKeys?: string[] }): Promise<{ download: () => void }>`
- **作用** 导出 Excel(可自定义导出文件回调)。
- **参数**
  - `options?: { formatExportFile?: (data: string) => Promise<{ download: () => void }>; isFormatNumber?: boolean; skipHeader?: boolean; columnKeys?: string[] }` — (可选) 可选配置对象(见签名类型)
- **返回** `Promise<{ download: () => void }>` — Promise(异步结果,见签名)

### `getImageBuffer`

- **签名** `getImageBuffer(col: number, row: number): { buffer: ArrayBuffer; width: number; height: number }`
- **作用** 获取单元格图片的二进制 buffer 与尺寸。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `{ buffer: ArrayBuffer; width: number; height: number }` — { buffer: ArrayBuffer; width: number; height: number }

### `measureText`

- **签名** `measureText: (text: string, font: { fontSize: number; fontWeight?: string | number; fontFamily: string; }) => ITextSize`
- **作用** 测量文本尺寸。
- **参数**
  - `text: string` — 文本
  - `font: { fontSize: number; fontWeight?: string | number; fontFamily: string; }` — 字体对象
- **返回** `ITextSize` — ITextSize

### `measureTextBounds`

- **签名** `measureTextBounds(text: string, font: { fontSize: number; fontFamily?: string; fontWeight?: string | number }): Rect`
- **作用** 测量文本边界矩形。
- **参数**
  - `text: string` — 文本
  - `font: { fontSize: number; fontFamily?: string; fontWeight?: string | number }` — 字体对象
- **返回** `Rect` — 矩形 {x,y,width,height}

### `hasAutoImageColumn`

- **签名** `hasAutoImageColumn(): boolean`
- **作用** 是否包含自动图片列。
- **返回** `boolean` — 布尔值(是否成立)

---

## M. 主题与像素比(2)

### `getTheme` @AI

- **签名** `getTheme(): ITableTheme`
- **作用** 读取当前主题对象。
- **返回** `ITableTheme` — 主题对象

### `updateTheme` @AI

- **签名** `updateTheme(theme: Partial<ITableTheme> | string, isReset?: boolean): void`
- **作用** 更新主题(可局部合并,isReset 是否重置为默认)。
- **参数**
  - `theme: Partial<ITableTheme> | string` — 主题对象或主题名
  - `isReset?: boolean` — (可选) 是否重置为默认主题
- **返回** `void` — 无返回值

---

## N. 事件与监听(8)

### `on`

- **签名** `on: <TYPE extends keyof TableEventHandlersEventArgumentMap>(type: TYPE, listener: TableEventListener<TYPE>) => EventListenerId`
- **作用** 绑定事件监听,返回监听 ID(用于 off 解绑)。
- **参数**
  - `type: TYPE` — 事件类型
  - `listener: TableEventListener<TYPE>` — 监听函数
- **返回** `EventListenerId` — 事件监听 ID(on 返回,可传给 off)

### `off`

- **签名** `off: (id: EventListenerId) => void`
- **作用** 按 ID 解绑事件监听。
- **参数**
  - `id: EventListenerId` — on 返回的监听 ID
- **返回** `void` — 无返回值

### `addEventListener`

- **签名** `addEventListener(type: string, callback: Function): void`
- **作用** 绑定事件监听(兼容 DOM 习惯)。
- **参数**
  - `type: string` — 事件类型
  - `callback: Function` — 监听函数
- **返回** `void` — 无返回值

### `removeEventListener`

- **签名** `removeEventListener(type: string, callback: Function): void`
- **作用** 解绑事件监听。
- **参数**
  - `type: string` — 事件类型
  - `callback: Function` — 监听函数
- **返回** `void` — 无返回值

### `fireListeners`

- **签名** `fireListeners: <TYPE extends keyof TableEventHandlersEventArgumentMap>(type: TYPE, event: TableEventHandlersEventArgumentMap[TYPE]) => TableEventHandlersReturnMap[TYPE][]`
- **作用** 手动触发指定事件的所有监听。
- **参数**
  - `type: TYPE` — 事件类型
  - `event: TableEventHandlersEventArgumentMap[TYPE]` — 事件载荷
- **返回** `TableEventHandlersReturnMap[TYPE][]` — TableEventHandlersReturnMap[TYPE][]

### `hasListeners`

- **签名** `hasListeners: (type: string) => boolean`
- **作用** 判定某事件是否有监听者。
- **参数**
  - `type: string` — 事件类型
- **返回** `boolean` — 布尔值(是否成立)

### `onVChartEvent`

- **签名** `onVChartEvent(type: string, callback: Function): void`
- **作用** 绑定图表(VChart)事件。
- **参数**
  - `type: string` — 图表事件类型
  - `callback: Function` — 回调函数
- **返回** `void` — 无返回值

### `offVChartEvent`

- **签名** `offVChartEvent(type: string, callback: Function): void`
- **作用** 解绑图表事件。
- **参数**
  - `type: string` — 图表事件类型
  - `callback: Function` — 回调函数
- **返回** `void` — 无返回值

---

## O. 图表与自定义布局(6)

### `getCustomRender`

- **签名** `getCustomRender: (col: number, row: number) => ICustomRender`
- **作用** 获取单元格自定义渲染 ICustomRender。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ICustomRender` — ICustomRender

### `getCustomLayout`

- **签名** `getCustomLayout: (col: number, row: number) => ICustomLayout`
- **作用** 获取单元格自定义布局 ICustomLayout。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `ICustomLayout` — ICustomLayout

### `checkReactCustomLayout`

- **签名** `checkReactCustomLayout: (removeAllContainer: () => void) => void`
- **作用** 检查 React 自定义布局容器并清理。
- **参数**
  - `removeAllContainer: () => void` — 移除全部容器的回调
- **返回** `void` — 无返回值

### `isListTable`

- **签名** `isListTable(): true`
- **作用** 判断是否为 ListTable。
- **返回** `true` — true

### `isPivotTable`

- **签名** `isPivotTable(): false`
- **作用** 判断是否为透视表。
- **返回** `false` — false

### `isPivotChart`

- **签名** `isPivotChart(): false`
- **作用** 判断是否为透视图表。
- **返回** `false` — false

---

## P. 坐标 / 几何 / 命中检测(20)

### `getCellRect` @AI

- **签名** `getCellRect: (col: number, row: number) => Rect`
- **作用** 读取单元格矩形(画布内绝对坐标,含表头/滚动)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getCellRelativeRect` @AI

- **签名** `getCellRelativeRect: (col: number, row: number) => Rect`
- **作用** 读取单元格矩形(相对可视区域,滚动偏移后)。AI 工具用它换算点击坐标。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getCellsRect`

- **签名** `getCellsRect: (startCol: number, startRow: number, endCol: number, endRow: number) => Rect`
- **作用** 读取矩形区域的包围矩形。
- **参数**
  - `startCol: number` — 起始列
  - `startRow: number` — 起始行
  - `endCol: number` — 结束列
  - `endRow: number` — 结束行
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getCellsRectWidth`

- **签名** `getCellsRectWidth(startCol: number, startRow: number, endCol: number, endRow: number): number`
- **作用** 计算矩形区域的宽度(像素)。
- **参数**
  - `startCol: number` — 起始列号(含)
  - `startRow: number` — 起始行号(含)
  - `endCol: number` — 结束列号(含)
  - `endRow: number` — 结束行号(含)
- **返回** `number` — 数值(像素/索引/数量)

### `getCellRangeRect`

- **签名** `getCellRangeRect: (cellRange: CellRange | CellAddress) => Rect`
- **作用** 读取单元格范围的矩形(绝对坐标)。
- **参数**
  - `cellRange: CellRange | CellAddress` — 范围或单元格地址
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getCellRangeRectWidth`

- **签名** `getCellRangeRectWidth(cellRange: CellRange): number`
- **作用** 计算单元格范围的宽度。
- **参数**
  - `cellRange: CellRange` — 单元格范围 CellRange
- **返回** `number` — 数值(像素/索引/数量)

### `getCellRangeRelativeRect` @AI

- **签名** `getCellRangeRelativeRect: (cellRange: CellRange | CellAddress) => Rect`
- **作用** 读取单元格范围的矩形(相对坐标)。
- **参数**
  - `cellRange: CellRange | CellAddress` — 范围或单元格地址
- **返回** `Rect` — 矩形 {x,y,width,height}

### `getRowAt` @AI

- **签名** `getRowAt: (absoluteY: number) => { top: number`
- **作用** 由绝对 Y 坐标命中行,返回 {top,row,bottom}。
- **参数**
  - `absoluteY: number` — 绝对 Y 坐标
- **返回** `{ top: number` — { top: number

### `getColAt` @AI

- **签名** `getColAt: (absoluteX: number) => { left: number`
- **作用** 由绝对 X 坐标命中列,返回 {left,col,right}。
- **参数**
  - `absoluteX: number` — 绝对 X 坐标
- **返回** `{ left: number` — { left: number

### `getCellAt` @AI

- **签名** `getCellAt: (absoluteX: number, absoluteY: number) => CellAddressWithBound`
- **作用** 由绝对 X/Y 坐标命中单元格地址。
- **参数**
  - `absoluteX: number` — 绝对 X 坐标
  - `absoluteY: number` — 绝对 Y 坐标
- **返回** `CellAddressWithBound` — CellAddressWithBound

### `getCellAtRelativePosition`

- **签名** `getCellAtRelativePosition: (absoluteX: number, absoluteY: number) => CellAddressWithBound`
- **作用** 由相对 X/Y 坐标命中单元格地址。
- **参数**
  - `absoluteX: number` — 相对 X 坐标
  - `absoluteY: number` — 相对 Y 坐标
- **返回** `CellAddressWithBound` — CellAddressWithBound

### `getColAtRelativePosition`

- **签名** `getColAtRelativePosition: (absoluteX: number) => number`
- **作用** 由相对 X 坐标命中列号。
- **参数**
  - `absoluteX: number` — 相对 X 坐标
- **返回** `number` — 数值(像素/索引/数量)

### `getRowAtRelativePosition`

- **签名** `getRowAtRelativePosition: (absoluteY: number) => number`
- **作用** 由相对 Y 坐标命中行号。
- **参数**
  - `absoluteY: number` — 相对 Y 坐标
- **返回** `number` — 数值(像素/索引/数量)

### `getTargetColAt`

- **签名** `getTargetColAt: (absoluteX: number) => ColumnInfo | null`
- **作用** 由绝对 X 坐标获取列信息(ColumnInfo)。
- **参数**
  - `absoluteX: number` — 绝对 X 坐标
- **返回** `ColumnInfo | null` — ColumnInfo | null

### `getTargetRowAt`

- **签名** `getTargetRowAt: (absoluteY: number) => RowInfo | null`
- **作用** 由绝对 Y 坐标获取行信息(RowInfo)。
- **参数**
  - `absoluteY: number` — 绝对 Y 坐标
- **返回** `RowInfo | null` — RowInfo | null

### `getTargetColAtConsiderRightFrozen`

- **签名** `getTargetColAtConsiderRightFrozen: (absoluteX: number, isConsider: boolean) => ColumnInfo | null`
- **作用** 由绝对 X 坐标获取列信息(考虑右侧冻结)。
- **参数**
  - `absoluteX: number` — 绝对 X 坐标
  - `isConsider: boolean` — 是否考虑右侧冻结
- **返回** `ColumnInfo | null` — ColumnInfo | null

### `getTargetRowAtConsiderBottomFrozen`

- **签名** `getTargetRowAtConsiderBottomFrozen: (absoluteY: number, isConsider: boolean) => RowInfo | null`
- **作用** 由绝对 Y 坐标获取行信息(考虑底部冻结)。
- **参数**
  - `absoluteY: number` — 绝对 Y 坐标
  - `isConsider: boolean` — 是否考虑底部冻结
- **返回** `RowInfo | null` — RowInfo | null

### `getContext` @AI

- **签名** `getContext: () => CanvasRenderingContext2D`
- **作用** 获取画布 2D 渲染上下文。
- **返回** `CanvasRenderingContext2D` — 画布 2D 上下文

### `getElement` @AI

- **签名** `getElement: () => HTMLElement`
- **作用** 获取表格根 DOM 元素。
- **返回** `HTMLElement` — DOM 元素

### `getContainer` @AI

- **签名** `getContainer: () => HTMLElement`
- **作用** 获取表格容器 DOM 元素。
- **返回** `HTMLElement` — DOM 元素

---

## R. 菜单 / 工具提示 / 交互 UI(7)

### `getMenuInfo` @AI

- **签名** `getMenuInfo(col: number, row: number, type: string): DropDownMenuEventInfo`
- **作用** 读取下拉/右键菜单信息。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `type: string` — 菜单类型
- **返回** `DropDownMenuEventInfo` — DropDownMenuEventInfo

### `showDropDownMenu` @AI

- **签名** `showDropDownMenu(col: number, row: number): void`
- **作用** 显示下拉菜单。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `setDropDownMenuHighlight`

- **签名** `setDropDownMenuHighlight(col: number, row: number, index: number): void`
- **作用** 设置下拉菜单高亮项。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `index: number` — 高亮项索引
- **返回** `void` — 无返回值

### `showTooltip`

- **签名** `showTooltip: (col: number, row: number, tooltipOptions?: TooltipOptions) => void`
- **作用** 显示工具提示(Tooltip)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
  - `tooltipOptions?: TooltipOptions` — (可选) Tooltip 配置
- **返回** `void` — 无返回值

### `showMoverLine`

- **签名** `showMoverLine: (col: number, row: number) => void`
- **作用** 显示拖拽移动指示线(列/行拖拽)。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `hideMoverLine`

- **签名** `hideMoverLine: (col: number, row: number) => void`
- **作用** 隐藏拖拽移动指示线。
- **参数**
  - `col: number` — 列号(从 0 开始)
  - `row: number` — 行号(从 0 开始)
- **返回** `void` — 无返回值

### `changeHeaderPosition`

- **签名** `changeHeaderPosition: (args: { source: CellAddress; target: CellAddress; movingColumnOrRow?: 'column' | 'row'; }) => boolean`
- **作用** 移动表头位置(列/行拖拽落位),返回是否成功。
- **参数**
  - `args: { source: CellAddress; target: CellAddress; movingColumnOrRow?: 'column' | 'row'; }` — 移动配置 {source, target, movingColumnOrRow}
- **返回** `boolean` — 布尔值(是否成立)

---

## Z. 运行时内部方法(_ 前缀,无 TS 声明)(42)

> 以下方法存在于实例原型上,但以下划线开头,属内部实现,**不建议业务/AI 直接调用**,仅在排查时参考。

### `_adjustCanvasSizeByOption`

- **签名** `_adjustCanvasSizeByOption(option: any): void`
- **作用** 按配置调整画布尺寸(内部)。

### `_adjustColWidth`

- **签名** `_adjustColWidth: (col: number, orgWidth: number) => number`
- **作用** 调整列宽(内部)。

### `_bindChartEvent`

- **签名** `_bindChartEvent(chart: any): void`
- **作用** 绑定图表事件(内部)。

### `_canDragHeaderPosition`

- **签名** `_canDragHeaderPosition: (col: number, row: number) => boolean`
- **作用** 判定表头是否可拖拽(内部)。

### `_canResizeColumn`

- **签名** `_canResizeColumn(col: number, row: number): boolean`
- **作用** 判定列是否可拖拽改宽(内部)。

### `_canResizeRow`

- **签名** `_canResizeRow: (col: number, row: number) => boolean`
- **作用** 判定行是否可拖拽改高(内部)。

### `_checkRowCol`

- **签名** `_checkRowCol(col: number, row: number): boolean`
- **作用** 校验行列号合法性(内部)。

### `_clearColRangeWidthsMap`

- **签名** `_clearColRangeWidthsMap: (col?: number) => void`
- **作用** 清空列范围宽度缓存(内部)。

### `_clearRowRangeHeightsMap`

- **签名** `_clearRowRangeHeightsMap: (row?: number) => void`
- **作用** 清空行范围高度缓存(内部)。

### `_colWidthDefineToPxWidth`

- **签名** `_colWidthDefineToPxWidth: (width: string | number) => number`
- **作用** 列宽定义转像素(内部)。

### `_dropDownMenuIsHighlight`

- **签名** `_dropDownMenuIsHighlight: (col: number, row: number, index: number) => boolean`
- **作用** 下拉菜单项是否高亮(内部)。

### `_getActiveChartInstance`

- **签名** `_getActiveChartInstance(): any`
- **作用** 获取活动图表实例(内部)。

### `_getBodyLayoutMap`

- **签名** `_getBodyLayoutMap: (col: number, row: number) => ColumnData | IndicatorData | SeriesNumberColumnData`
- **作用** 读取 body 布局映射(内部)。

### `_getCellStyle`

- **签名** `_getCellStyle: (col: number, row: number) => FullExtendStyle`
- **作用** 读取单元格样式(内部)。

### `_getColContentWidth`

- **签名** `_getColContentWidth: (col: number) => number`
- **作用** 读取列内容宽度(内部)。

### `_getColWidthLimits`

- **签名** `_getColWidthLimits(): { minWidth: number; maxWidth: number }`
- **作用** 读取列宽上下限(内部)。

### `_getComputedFrozenColCount`

- **签名** `_getComputedFrozenColCount: (frozenColCount: number) => number`
- **作用** 计算实际冻结列数(内部)。

### `_getHeaderCellBySortState`

- **签名** `_getHeaderCellBySortState: (sortState: SortState) => CellAddress | undefined`
- **作用** 按排序状态定位表头单元格(内部)。

### `_getHeaderLayoutMap`

- **签名** `_getHeaderLayoutMap: (col: number, row: number) => HeaderData | SeriesNumberColumnData`
- **作用** 读取表头布局映射(内部)。

### `_getLayoutCellId`

- **签名** `_getLayoutCellId: (col: number, row: number) => LayoutObjectId`
- **作用** 获取布局单元格 ID(内部)。

### `_getMaxFrozenWidth`

- **签名** `_getMaxFrozenWidth: () => number`
- **作用** 读取最大冻结宽度(内部)。

### `_getMaxRightFrozenWidth`

- **签名** `_getMaxRightFrozenWidth(): number`
- **作用** 读取右侧最大冻结宽度(内部)。

### `_getMouseAbstractPoint`

- **签名** `_getMouseAbstractPoint: (evt: TouchEvent | MouseEvent | undefined) => { x: number`
- **作用** 取鼠标抽象坐标点(内部)。

### `_getRangeSizeForContainerFit`

- **签名** `_getRangeSizeForContainerFit: (start: number, end: number, totalSize: number, type: 'col' | 'row') => number`
- **作用** 计算容器自适应区间尺寸(内部)。

### `_getSortFuncFromHeaderOption`

- **签名** `_getSortFuncFromHeaderOption(columns: ColumnsDefine | undefined, field: FieldDef, fieldKey?: FieldKeyDef): SortState['orderFn'] | undefined`
- **作用** 从表头配置取排序函数(内部)。

### `_getVisiableRect`

- **签名** `_getVisiableRect(): Rect`
- **作用** 读取可视矩形(内部,拼写同源码)。

### `_hasCustomRenderOrLayout`

- **签名** `_hasCustomRenderOrLayout(): boolean`
- **作用** 是否有自定义渲染/布局(内部)。

### `_hasField`

- **签名** `_hasField: (field: FieldDef, col: number, row: number) => boolean`
- **作用** 是否有某字段(内部)。

### `_hasHierarchyTreeHeader`

- **签名** `_hasHierarchyTreeHeader(): boolean`
- **作用** 是否有树形表头(内部)。

### `_makeVisibleCell`

- **签名** `_makeVisibleCell: (col: number, row: number) => void`
- **作用** 滚动使单元格可见(内部)。

### `_moveHeaderPosition`

- **签名** `_moveHeaderPosition(source: CellAddress, target: CellAddress): { sourceIndex: number`
- **作用** 移动表头位置(内部实现)。

### `_recreateSceneForStateChange`

- **签名** `_recreateSceneForStateChange(): void`
- **作用** 状态变更后重建场景(内部)。

### `_refreshHierarchyState`

- **签名** `_refreshHierarchyState(col: number, row: number, recalculateColWidths?: boolean): void`
- **作用** 刷新层级状态(内部)。

### `_resetFrozenColCount`

- **签名** `_resetFrozenColCount: () => void`
- **作用** 重置冻结列数(内部)。

### `_scheduleScrollToRowCorrect`

- **签名** `_scheduleScrollToRowCorrect(scrollTop: number): void`
- **作用** 调度滚动到行的校正(内部)。

### `_setColContentWidth`

- **签名** `_setColContentWidth: (col: number, width: number | string, clearCache?: boolean) => void`
- **作用** 设置列内容宽度(内部)。

### `_setColWidth`

- **签名** `_setColWidth: (col: number, width: number | string, clearCache?: boolean, skipCheckFrozen?: boolean) => void`
- **作用** 设置列宽(内部)。

### `_setFrozenColCount`

- **签名** `_setFrozenColCount: (count: number) => void`
- **作用** 设置冻结列数(内部)。

### `_setFrozenRowCount`

- **签名** `_setFrozenRowCount(count: number): void`
- **作用** 设置冻结行数(内部)。

### `_setRowHeight`

- **签名** `_setRowHeight: (row: number, height: number, clearCache?: boolean) => void`
- **作用** 设置行高(内部)。

### `_toRelativeRect`

- **签名** `_toRelativeRect(rect: Rect): Rect`
- **作用** 矩形转相对坐标(内部)。

### `_updateSize`

- **签名** `_updateSize: () => void`
- **作用** 更新尺寸(内部)。

---

## getter / setter 属性清单

VTable 实例把大量状态暴露为 ES getter/setter 属性,可直接读/写。下表按属性名合并列出。

| 属性 | 类型 | 读写 | 说明 |
|---|---|---|---|
| `_colRangeWidthsMap` | `Map<string, number>` | 读写 | 列范围宽度缓存(内部) |
| `_rowRangeHeightsMap` | `Map<string, number>` | 读写 | 行范围高度缓存(内部) |
| `allowFrozenColCount` | `number` | 只读 | 允许的最大冻结列数 |
| `autoFillHeight` | `boolean` | 读写 | 是否按容器高度自动填充 |
| `autoFillWidth` | `boolean` | 读写 | 是否按容器宽度自动填充 |
| `autoWrapText` | `boolean` | 读写 | 是否自动换行 |
| `bodyDomContainer` | `HTMLElement` | 只读 | body 容器 DOM |
| `bottomDomContainer` | `HTMLElement` | 只读 | 底部容器 DOM |
| `bottomFrozenRowCount` | `number` | 读写 | 底部冻结行数 |
| `canvas` | `HTMLCanvasElement` | 只读 | 表格 canvas 元素 |
| `colContentWidthsMap` | `Map<number, number>` | 读写 | 列内容宽度映射 |
| `colCount` | `number` | 读写 | 总列数 |
| `colWidthsLimit` | `{ minWidth: number; maxWidth: number }` | 读写 | 列宽限制 |
| `colWidthsMap` | `NumberMap<string|number>` | 读写 | 列宽映射表 |
| `columnHeaderLevelCount` | `number` | 只读 | 列表头层级数 |
| `columns` | `ColumnsDefine` | 只读 | 列定义 |
| `containerFit` | `{ width: boolean; height: boolean }` | 读写 | 是否随容器尺寸变化自适应 |
| `dataSource` | `DataSourceAPI` | 读写 | 数据源 API |
| `defaultColWidth` | `number` | 读写 | 默认列宽 |
| `defaultHeaderColWidth` | `number | (number|'auto')[]` | 读写 | 默认表头列宽 |
| `defaultHeaderRowHeight` | `number | (number|'auto')[]` | 读写 | 默认表头行高 |
| `defaultRowHeight` | `number` | 读写 | 默认行高 |
| `enableLineBreak` | `boolean` | 读写 | 是否启用换行 |
| `eventOptions` | `TableEventOptions | null` | 读写 | 事件配置 |
| `frozenBodyDomContainer` | `HTMLElement` | 只读 | 冻结 body 容器 DOM |
| `frozenBottomDomContainer` | `HTMLElement` | 只读 | 底部冻结容器 DOM |
| `frozenColCount` | `number` | 读写 | 冻结列数 |
| `frozenHeaderDomContainer` | `HTMLElement` | 只读 | 冻结表头容器 DOM |
| `frozenRowCount` | `number` | 读写 | 冻结行数 |
| `header` | `ColumnsDefine` | 读写 | 表头定义 |
| `headerDomContainer` | `HTMLElement` | 只读 | 表头容器 DOM |
| `heightAdaptiveMode` | `HeightAdaptiveModeDef` | 读写 | 行高自适应模式 |
| `heightMode` | `HeightModeDef` | 读写 | 行高模式 |
| `keyboardOptions` | `TableKeyboardOptions | null` | 读写 | 键盘交互配置 |
| `leftRowSeriesNumberCount` | `number` | 只读 | 左侧序号列数量 |
| `pixelRatio` | `number` | 只读 | 渲染像素比 |
| `records` | `any[]` | 只读 | 全部数据记录 |
| `recordsCount` | `number` | 只读 | 数据记录条数(不含表头) |
| `rightFrozenBodyDomContainer` | `HTMLElement` | 只读 | 右侧冻结 body 容器 DOM |
| `rightFrozenBottomDomContainer` | `HTMLElement` | 只读 | 右侧底部冻结容器 DOM |
| `rightFrozenColCount` | `number` | 读写 | 右侧冻结列数 |
| `rightFrozenHeaderDomContainer` | `HTMLElement` | 只读 | 右侧冻结表头容器 DOM |
| `rowCount` | `number` | 读写 | 总行数(表头 + 数据) |
| `rowHeaderLevelCount` | `number` | 只读 | 行表头层级数 |
| `rowHeightsMap` | `NumberRangeMap` | 读写 | 行高映射表 |
| `rowHierarchyType` | `HierarchyType` | 只读 | 树形结构类型(展开/折叠) |
| `scrollLeft` | `number` | 读写 | 水平滚动偏移 |
| `scrollTop` | `number` | 读写 | 垂直滚动偏移 |
| `sortState` | `SortState | SortState[]` | 只读 | 当前排序状态 |
| `theme` | `TableTheme` | 读写 | 当前主题对象 |
| `transpose` | `boolean` | 读写 | 是否转置 |
| `visibleColCount` | `number` | 只读 | 当前视口可见列数 |
| `visibleRowCount` | `number` | 只读 | 当前视口可见行数 |
| `widthAdaptiveMode` | `WidthAdaptiveModeDef` | 读写 | 列宽自适应模式 |
| `widthMode` | `WidthModeDef` | 读写 | 列宽模式(standard/adaptive) |
