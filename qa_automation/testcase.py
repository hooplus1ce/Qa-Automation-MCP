"""测试用例管理门面（已迁移至 tencent_sheet，保留本模块以实现无缝向后兼容）。"""

from __future__ import annotations

from .tencent_sheet import (
    DEFAULT_MCP_URL,
    SheetBatchUpdateResult,
    SheetConnectResult,
    SheetQueryResult,
    SheetRowDetail,
    SheetUpdateResult,
    TencentDocError,
    TencentDocSheetClient,
    TencentSheetClient,
    TencentSheetManager,
    TestCaseBatchUpdateResult,
    TestCaseConnectResult,
    TestCaseDetail,
    TestCaseManager,
    TestCaseQueryResult,
    TestCaseUpdateResult,
    normalize_header,
    parse_file_id,
    tencent_sheet_manager,
    testcase_manager,
)

__all__ = [
    "DEFAULT_MCP_URL",
    "TencentDocError",
    "TencentDocSheetClient",
    "TencentSheetClient",
    "TestCaseManager",
    "TencentSheetManager",
    "testcase_manager",
    "tencent_sheet_manager",
    "parse_file_id",
    "normalize_header",
    "TestCaseConnectResult",
    "SheetConnectResult",
    "TestCaseDetail",
    "SheetRowDetail",
    "TestCaseQueryResult",
    "SheetQueryResult",
    "TestCaseUpdateResult",
    "SheetUpdateResult",
    "TestCaseBatchUpdateResult",
    "SheetBatchUpdateResult",
]
