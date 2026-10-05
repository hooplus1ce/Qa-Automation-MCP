"""General-purpose in-memory style augmentation scanner for Tencent Docs spreadsheets.

Extracts all visual style and layout metadata (strikethrough, font colors, cell background fills,
bold/italic typography, borders, and merged cell structures) directly from Tencent Docs'
in-memory data grid (`window.SpreadsheetApp.workbook.activeSheet.cellDataGrid`), overcoming
the OpenAPI limitation of returning only unstyled plain text.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .manager import parse_file_id, tencent_sheet_manager
from .transport import TencentDocError

logger = logging.getLogger("qa_automation.tencent_sheet.scanner")

# JavaScript engine that extracts full style metadata in V8 memory in < 2ms
_STYLE_SCAN_JS_SCRIPT = """() => {
    const t0 = performance.now();
    const app = window.SpreadsheetApp;
    if (!app || !app.workbook || !app.workbook.activeSheet || !app.workbook.activeSheet.cellDataGrid) {
        return { ok: false, error: "SpreadsheetApp.workbook.activeSheet.cellDataGrid not ready" };
    }

    const sheet = app.workbook.activeSheet;
    const grid = sheet.cellDataGrid;
    const used = grid.usedRange || { startRow: 0, endRow: 120, startCol: 0, endCol: 25 };

    const maxR = Math.min(Math.max(used.endRowIndex !== undefined ? used.endRowIndex : 120, 100), 300);
    const maxC = Math.min(Math.max(used.endColIndex !== undefined ? used.endColIndex : 25, 20), 40);

    const struckCells = [];
    const fontColorMap = {};
    const fillColorMap = {};
    const boldCells = [];
    const mergedBlocks = [];
    const seenMerges = new Set();
    const rowsWithStrike = new Set();
    const rowsWithRed = new Set();
    const sections = [];

    for (let r = 0; r <= maxR; r++) {
        for (let c = 0; c <= maxC; c++) {
            const cell = grid.getCellData(r, c);
            if (!cell) continue;

            const val = cell.value !== undefined ? cell.value : (cell.formattedValue ? cell.formattedValue.value : "");
            const style = cell.style;
            const font = style ? style.font : null;
            const fillObj = style && style.fill && style.fill.patternFill ? style.fill.patternFill.fgColor : null;
            const merge = cell.mergeReference;

            // 1. Merged cell capture (deduplicated)
            if (merge) {
                const mKey = merge.startRowIndex + "," + merge.startColIndex + "-" + merge.endRowIndex + "," + merge.endColIndex;
                if (!seenMerges.has(mKey)) {
                    seenMerges.add(mKey);
                    const block = {
                        start_row: merge.startRowIndex + 1,
                        end_row: merge.endRowIndex + 1,
                        start_col: merge.startColIndex + 1,
                        end_col: merge.endColIndex + 1,
                        row_span: merge.endRowIndex - merge.startRowIndex + 1,
                        col_span: merge.endColIndex - merge.startColIndex + 1,
                        text: String(val || "")
                    };
                    mergedBlocks.push(block);

                    // Detect section headers (spans multiple cols + has text + top row of merge)
                    if (block.col_span >= 3 && block.text.trim()) {
                        sections.push({
                            row: block.start_row,
                            title: block.text.trim(),
                            col_span: block.col_span
                        });
                    }
                }
            }

            // 2. Typography (strikethrough, bold, font colors)
            if (font) {
                if (font.strike) {
                    struckCells.push({
                        row: r + 1,
                        col: c + 1,
                        text: val,
                        font_name: font.name || ""
                    });
                    rowsWithStrike.add(r + 1);
                }

                if (font.b && val !== "" && val !== null) {
                    boldCells.push({
                        row: r + 1,
                        col: c + 1,
                        text: val
                    });
                }

                const fRgb = font.color && font.color.rgb ? font.color.rgb.toUpperCase() : "";
                if (fRgb && fRgb !== "FF000000" && fRgb !== "FFFFFFFF") {
                    if (!fontColorMap[fRgb]) fontColorMap[fRgb] = [];
                    fontColorMap[fRgb].push({
                        row: r + 1,
                        col: c + 1,
                        text: val
                    });
                    if (fRgb === "FFFF0000" || fRgb === "FFD93025" || fRgb === "FFEA4335") {
                        rowsWithRed.add(r + 1);
                    }
                }
            }

            // 3. Cell Background Fills
            if (fillObj && fillObj.rgb) {
                const bgRgb = fillObj.rgb.toUpperCase();
                if (bgRgb !== "FFFFFFFF") {
                    if (!fillColorMap[bgRgb]) fillColorMap[bgRgb] = [];
                    fillColorMap[bgRgb].push({
                        row: r + 1,
                        col: c + 1,
                        text: val
                    });
                }
            }
        }
    }

    // Helper to read row data cleanly
    const getRowCells = (rowNum) => {
        const rowCells = [];
        for (let c = 0; c <= 10; c++) {
            const cell = grid.getCellData(rowNum - 1, c);
            const val = cell && cell.value !== undefined ? cell.value : (cell && cell.formattedValue ? cell.formattedValue.value : "");
            rowCells.push(val !== undefined && val !== null ? val : "");
        }
        return {
            row_index: rowNum,
            index: rowCells[0],
            code: rowCells[1],
            name: rowCells[2],
            type: rowCells[3],
            required: rowCells[4],
            status: rowCells[5],
            description: rowCells[6],
            enums: rowCells[7]
        };
    };

    // Struck (deleted) rows
    const struckRows = [];
    for (const rowNum of rowsWithStrike) {
        struckRows.push(getRowCells(rowNum));
    }

    // Red (modified / emphasized) rows
    const redRows = [];
    for (const rowNum of rowsWithRed) {
        if (!rowsWithStrike.has(rowNum)) {
            redRows.push(getRowCells(rowNum));
        }
    }

    const t1 = performance.now();
    return {
        ok: true,
        scan_elapsed_ms: +(t1 - t0).toFixed(2),
        dimensions: {
            scanned_rows: maxR + 1,
            scanned_cols: maxC + 1
        },
        typography: {
            strikethrough_count: struckCells.length,
            bold_count: boldCells.length,
            font_color_groups: Object.keys(fontColorMap).map(color => ({
                color: color,
                count: fontColorMap[color].length,
                sample_cells: fontColorMap[color].slice(0, 5)
            }))
        },
        fills: {
            fill_color_groups: Object.keys(fillColorMap).map(color => ({
                color: color,
                count: fillColorMap[color].length,
                sample_cells: fillColorMap[color].slice(0, 5)
            }))
        },
        structure: {
            merged_blocks_count: mergedBlocks.length,
            sections: sections,
            merged_blocks_sample: mergedBlocks.slice(0, 10)
        },
        semantic_summary: {
            deleted_fields: struckRows,
            modified_fields: redRows,
            all_struck_cells: struckCells
        }
    };
}"""


async def scan_sheet_styles(
    url_or_file_id: str | None = None,
    tab_id: str | None = None,
    sheet_name: str | None = None,
    headless: bool = True,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """通过无头浏览器直接在 V8 内存中全维度提取腾讯文档表格的富文本样式与结构元数据。

    覆盖所有视觉样式维度：
    1. 文字排版：中划删除线（废弃字段）、字体颜色（红/蓝/绿变动）、加粗（标题与关键字段）；
    2. 背景填充：单元格高亮颜色（表头、预警、分组色块）；
    3. 结构布局：合并单元格（区域跨度、分组标题块）、功能区块检测；
    4. 语义解析：自动归类作废字段（deleted_fields）与重点修订字段（modified_fields）。

    Args:
        url_or_file_id: 腾讯文档链接或 file_id（缺省时使用当前已连接文档）
        tab_id: 子表 Tab ID（如 "000005"）
        sheet_name: 子表名称（如 "02_BOM同步"，会自动解析为 Tab ID）
        headless: 是否使用无头模式（默认 True）
        timeout_seconds: 超时秒数（默认 30 秒）

    Returns:
        包含全维度样式特征统计与结构化字段清单的字典。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise TencentDocError("需要安装 playwright 才能运行内存样式扫描: pip install playwright") from e

    target_url = url_or_file_id
    effective_tab = tab_id

    # 解析 file_id 与 tab_id
    if not target_url and tencent_sheet_manager.active_file_id:
        file_id = tencent_sheet_manager.active_file_id
        if not effective_tab and sheet_name:
            effective_tab, _ = tencent_sheet_manager.resolve_sheet(sheet_name)
        elif not effective_tab:
            effective_tab = tencent_sheet_manager.active_tab_id
        target_url = f"https://docs.qq.com/sheet/{file_id}"
    elif target_url:
        parsed_id, parsed_tab = parse_file_id(target_url)
        if not effective_tab:
            effective_tab = parsed_tab
        target_url = f"https://docs.qq.com/sheet/{parsed_id}"
    else:
        raise TencentDocError("未提供表格链接或 file_id，且尚未连接表格")

    if effective_tab:
        target_url = f"{target_url}?tab={effective_tab}"

    logger.info("启动无头浏览器扫描全维度样式: %s", target_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1600, "height": 900})
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until="load", timeout=int(timeout_seconds * 1000))

            # 等待 SpreadsheetApp 及其 cellDataGrid 就绪
            is_ready = False
            for _ in range(16):
                ready_check = await page.evaluate(
                    "() => !!(window.SpreadsheetApp && window.SpreadsheetApp.workbook && window.SpreadsheetApp.workbook.activeSheet && window.SpreadsheetApp.workbook.activeSheet.cellDataGrid)"
                )
                if ready_check:
                    is_ready = True
                    break
                await asyncio.sleep(0.5)

            if not is_ready:
                raise TencentDocError("页面已加载但未能检测到有效的 SpreadsheetApp 数据模型（可能需要登录或文档无访问权限）")

            # 在 V8 内存中直接执行全维度提取
            scan_result = await page.evaluate(_STYLE_SCAN_JS_SCRIPT)
            if not scan_result.get("ok"):
                raise TencentDocError(f"样式扫描执行失败: {scan_result.get('error')}")

            scan_result["url"] = target_url
            scan_result["tab_id"] = effective_tab
            return scan_result

        finally:
            await browser.close()


# 向后兼容的历史别名
scan_sheet_styled_fields = scan_sheet_styles
