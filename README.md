# Сагс — agentic grocery shopping

Budget + days + household → meal plan → grocery list → live quotes from 3 store agents (A2A) → cheapest split →
user approval → sandbox checkout (reserve → hold → order → capture) with receipts, cancel/refund and a full audit log.

## Layout

```
app/
  common/        shared: db (Mongo helpers), policy, state machine, optimizer, llm, embed
  mcp_server.py  grocery-mcp: profile, recipes, list, branches, routes, policy-guarded wallet
  store_agent.py A2A store agent (run once per store: STORE=nomin PORT=9101)
  planner/       FastAPI app: agent loop, A2A client, checkout saga, MCP client
  web/           single-page UI
seed/            catalog, recipes, branches + seed.py
scripts/         probes, e2e demo, chaos (force out-of-stock)
tests/           pure-logic tests (no services needed)
```

## Run

```bash
cp .env.example .env              # fill in OyuLLM / OyuVoice
docker compose up -d              # MongoDB Atlas Local (vector search, transactions, change streams)
OLLAMA_CONTEXT_LENGTH=32768 ollama serve &
ollama pull bge-m3 && ollama pull qwen3:8b
uv sync
uv run --env-file .env python -m seed.seed
./run.sh                          # http://localhost:8000
uv run python scripts/demo_e2e.py # end-to-end check
uv run pytest -q
```
