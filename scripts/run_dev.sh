#!/usr/bin/env bash
# 启动 vtable-mcp 开发预览(浏览器体验全部交互式 UI)
# 用法: bash scripts/run_dev.sh [--dev-port PORT] [--mcp-port PORT]
set -euo pipefail
cd "$(dirname "$0")/.."

DEV_PORT="${1:-9090}"
MCP_PORT="${2:-9000}"

uv run fastmcp dev apps server.py --dev-port "$DEV_PORT" --mcp-port "$MCP_PORT"
