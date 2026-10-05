#!/usr/bin/env python3
"""CLI utility to quickly scan Tencent Docs for strikethrough and red fields."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from qa_automation.tencent_sheet.scanner import scan_sheet_styled_fields  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description="Scan Tencent Docs for deleted/red fields in V8 memory.")
    parser.add_argument("url", nargs="?", default="https://docs.qq.com/sheet/DS0ppaEFjc2tob1RQ", help="Tencent Doc URL or file_id")
    parser.add_argument("--tab", "-t", default="000005", help="Tab ID (e.g. 000005 for BOM, 000004 for Material)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    print(f"[*] Scanning {args.url} (Tab: {args.tab})...")
    result = await scan_sheet_styled_fields(url_or_file_id=args.url, tab_id=args.tab)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"[+] Scan completed in {result.get('scan_elapsed_ms')} ms!")
    struck_rows = result.get("struck_rows", [])
    print(f"\n--- Strikethrough (Deleted) Fields: {len(struck_rows)} ---")
    if not struck_rows:
        print("  (None)")
    else:
        for r in struck_rows:
            print(f"  Row {r.get('row_index')}: [序号 {r.get('index')}] {r.get('code')} ({r.get('name')}) - 类型: {r.get('type')}")

    red_cells = result.get("red_cells", [])
    print(f"\n--- Red Highlighted Cells: {len(red_cells)} ---")
    if not red_cells:
        print("  (None)")
    else:
        for c in red_cells[:15]:
            print(f"  Row {c.get('row_index')}, Col {c.get('col_index')}: {c.get('value')} (color: {c.get('color')})")
        if len(red_cells) > 15:
            print(f"  ... and {len(red_cells) - 15} more red cells")


if __name__ == "__main__":
    asyncio.run(main())
