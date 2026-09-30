#!/usr/bin/env bash
# Starts MCP server, 3 store agents and the planner. Ctrl-C stops all.
# DEMO=1 ./run.sh  reseeds the DB and swaps the LLM for a deterministic script (app/agents/demo.py).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || {
  echo "missing .env: cp .env.example .env and fill it in"
  exit 1
}
R="uv run --env-file .env"
trap 'kill 0' EXIT

# Start local deps if they're down: Mongo (docker) and Ollama (embeddings + fallback LLM).
up() { (exec 3<>"/dev/tcp/localhost/$1") 2>/dev/null; }
wait_up() {
  for _ in $(seq 60); do
    up "$1" && return
    sleep 0.5
  done
  echo "$2 did not start on :$1"
  exit 1
}
# Local Mongo only when MONGO_URL points here (empty = db.py's localhost default); Atlas needs nothing started.
MONGO_URL=$(grep -m1 '^MONGO_URL=' .env | cut -d= -f2-) || true
case "${MONGO_URL:-localhost}" in
*localhost* | *127.0.0.1*)
  up 27018 || {
    echo "starting mongo"
    docker compose up -d
    wait_up 27018 mongo
  }
  ;;
esac
up 11434 || {
  echo "starting ollama"
  ollama serve >/dev/null 2>&1 &
  wait_up 11434 ollama
}

if [ "${DEMO:-}" = 1 ]; then $R python -m seed.seed; fi
# rebuild when missing or older than any frontend source (a pull otherwise serves a stale bundle)
if [ ! -f web/dist/index.html ] || [ -n "$(find web/src web/index.html web/package.json -newer web/dist/index.html -print -quit)" ]; then
  (cd web && npm install && npm run build)
fi

$R python -m app.mcp_server &
for s in nomin:9101 emart:9102 sansar:9103; do
  STORE=${s%:*} PORT=${s#*:} $R python -m app.agents.store.server &
done

# Planner discovers store agents at startup: wait until their cards (and MCP) answer.
for url in localhost:9101/.well-known/agent-card.json localhost:9102/.well-known/agent-card.json \
  localhost:9103/.well-known/agent-card.json localhost:8100/mcp; do
  for _ in $(seq 60); do
    curl -s -o /dev/null "$url" && break
    sleep 0.5
  done
done

$R uvicorn app.planner.main:app --port 8000
