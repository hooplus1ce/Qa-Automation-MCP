"""表头规范化与文档链接解析工具。"""

from __future__ import annotations

import re
import urllib.parse


def normalize_header(text: str) -> str:
    """去除表头空格、常见中英文标点、下划线并转小写，便于弹性模糊匹配。"""
    if not text:
        return ""
    return re.sub(r"[\s\(\)（）\[\]【】_—\-:：#]+", "", str(text).strip().lower())


def parse_file_id(url_or_id: str) -> tuple[str, str | None]:
    """从文档 URL 或 file_id 中提取规范的 (file_id, tab_sheet_id)。"""
    raw = str(url_or_id or "").strip().strip("'\"")
    if not raw:
        return "", None

    if not raw.startswith("http://") and not raw.startswith("https://"):
        if "?" in raw or "#" in raw:
            raw = f"https://docs.qq.com/sheet/{raw}"
        else:
            return raw, None

    parsed = urllib.parse.urlparse(raw)
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        return "", None

    action_words = {"edit", "view", "preview", "sheet", "smartsheet", "doc", "form", "table"}
    if path_parts[-1].lower() in action_words and len(path_parts) > 1:
        candidate_id = path_parts[-2]
    else:
        candidate_id = path_parts[-1]

    file_id = candidate_id.split(".")[0]

    query_params = urllib.parse.parse_qs(parsed.query)
    tab = None
    for key in ("tab", "sub_id", "subid", "subId", "sheet_id", "sheetId", "padid"):
        if key in query_params and query_params[key]:
            tab = query_params[key][0]
            break

    if not tab and parsed.fragment:
        frag_params = urllib.parse.parse_qs(parsed.fragment)
        for key in ("tab", "sub_id", "subid", "subId", "sheet_id", "sheetId"):
            if key in frag_params and frag_params[key]:
                tab = frag_params[key][0]
                break

    return file_id, tab
