#!/usr/bin/env bash
# Run the current MCP Inspector v2 against this repository's stdio server.
# The web UI and Apps sandbox stay on loopback and are intended to be reached
# from a remote device through SSH local port forwarding.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSPECTOR_HOST="${INSPECTOR_HOST:-127.0.0.1}"
INSPECTOR_PORT="${INSPECTOR_PORT:-6274}"
SANDBOX_PORT="${MCP_SANDBOX_PORT:-6275}"

if [[ -z "${MCP_INSPECTOR_API_TOKEN:-}" ]]; then
  MCP_INSPECTOR_API_TOKEN="$(openssl rand -hex 32)"
fi
export HOST="$INSPECTOR_HOST"
export CLIENT_PORT="$INSPECTOR_PORT"
export MCP_SANDBOX_PORT="$SANDBOX_PORT"
export MCP_INSPECTOR_API_TOKEN

cat >&2 <<EOF
MCP Inspector v2
  Web UI:       http://127.0.0.1:${INSPECTOR_PORT}
  Apps sandbox: http://127.0.0.1:${SANDBOX_PORT}
  API token:    ${MCP_INSPECTOR_API_TOKEN}

Forward both ports from the remote device, then open:
  http://127.0.0.1:${INSPECTOR_PORT}/?MCP_INSPECTOR_API_TOKEN=${MCP_INSPECTOR_API_TOKEN}
EOF

exec npx @modelcontextprotocol/inspector@latest --web -- \
  uv run --project "$ROOT_DIR" fastmcp run "$ROOT_DIR/fastmcp.json" --no-banner
