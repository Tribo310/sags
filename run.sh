#!/usr/bin/env bash
# Starts MCP server, 3 store agents and the planner. Ctrl-C stops all.
# DEMO=1 ./run.sh  reseeds the DB and swaps the LLM for a deterministic script (app/planner/script.py).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "missing .env: cp .env.example .env and fill it in"; exit 1; }
R="uv run --env-file .env"
if [ "${DEMO:-}" = 1 ]; then $R python -m seed.seed; fi
trap 'kill 0' EXIT

$R python -m app.mcp_server &
for s in nomin:9101 emart:9102 sansar:9103; do
  STORE=${s%:*} PORT=${s#*:} $R python -m app.store_agent &
done

# Planner discovers store agents at startup: wait until their cards (and MCP) answer.
for url in localhost:9101/.well-known/agent-card.json localhost:9102/.well-known/agent-card.json \
           localhost:9103/.well-known/agent-card.json localhost:8100/mcp; do
  for _ in $(seq 60); do curl -s -o /dev/null "$url" && break; sleep 0.5; done
done

$R uvicorn app.planner.main:app --port 8000
