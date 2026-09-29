#!/usr/bin/env bash
# Starts MCP server, 3 store agents and the planner. Ctrl-C stops all.
set -euo pipefail
cd "$(dirname "$0")"
R="uv run --env-file .env"
trap 'kill 0' EXIT
$R python -m app.mcp_server.server &
STORE=nomin PORT=9101 $R python -m app.store_agent.server &
STORE=emart PORT=9102 $R python -m app.store_agent.server &
STORE=sansar PORT=9103 $R python -m app.store_agent.server &
sleep 3
$R uvicorn app.planner.main:app --port 8000
