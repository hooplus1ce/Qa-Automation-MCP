"""腾讯文档在线表格集成:传输层、解析工具、数据模型与业务门面。

原单文件模块的包化拆分;本模块再导出全部历史公开名,外部导入路径不变。
"""

from __future__ import annotations

from .manager import (
    TencentSheetManager,
    TestCaseManager,
    _state_guard,
    tencent_sheet_manager,
    testcase_manager,
)
from .models import (
    SheetBatchUpdateResult,
    SheetConnectResult,
    SheetDimensionResult,
    SheetQueryResult,
    SheetRowDetail,
    SheetUpdateResult,
    TestCaseBatchUpdateResult,
    TestCaseConnectResult,
    TestCaseDetail,
    TestCaseQueryResult,
    TestCaseUpdateResult,
)
from .parsing import normalize_header, parse_file_id
from .transport import (
    DEFAULT_MCP_URL,
    TencentDocError,
    TencentDocSheetClient,
    TencentSheetClient,
    _resolve_token,
)

__all__ = [
    "DEFAULT_MCP_URL",
    "TencentDocError",
    "TencentDocSheetClient",
    "TencentSheetClient",
    "TencentSheetManager",
    "TestCaseManager",
    "tencent_sheet_manager",
    "testcase_manager",
    "parse_file_id",
    "normalize_header",
    "_resolve_token",
    "_state_guard",
    "SheetConnectResult",
    "SheetRowDetail",
    "SheetQueryResult",
    "SheetUpdateResult",
    "SheetBatchUpdateResult",
    "SheetDimensionResult",
    "TestCaseConnectResult",
    "TestCaseDetail",
    "TestCaseQueryResult",
    "TestCaseUpdateResult",
    "TestCaseBatchUpdateResult",
]
