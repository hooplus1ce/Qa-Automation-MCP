#!/usr/bin/env bash
# 打包 vtable-mcp 为可分发的 zip
# 用法: bash scripts/package.sh [版本号]
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-0.1.0}"
OUT="dist/vtable-mcp-${VERSION}.zip"
mkdir -p dist
rm -f "$OUT"

# 打包内容:源码 + 文档 + 脚本(排除 venv/缓存/运行数据)
zip -r "$OUT" \
  server.py \
  mcp_server/ \
  automation_profiles.py \
  tool_metrics.py \
  vtable_js.py \
  vtable_playwright.py \
  sample_data.py \
  pyproject.toml \
  fastmcp.json \
  README.md \
  scripts/ \
  data/.gitkeep \
  -x "*.pyc" "*__pycache__*" ".venv/*" "data/*.json" "dist/*"

echo "✅ 已生成: $OUT"
echo "   解压后: uv sync && uv run fastmcp run fastmcp.json"
