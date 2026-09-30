# Сагс — agentic grocery shopping

Budget + days + household → meal plan → grocery list → live quotes from 3 store agents (A2A) → cheapest split →
user approval → sandbox checkout (reserve → hold → order → capture) with receipts, cancel/refund and a full audit log.

## Layout

```
app/
  agents/                 every agent, in one place
    core/react.py         ReAct loop shared by all agents: Thought -> Action -> Observation until a final tool
    core/llm.py           which LLM each agent uses (manager: always OyuLLM), with fallback
    protocols/a2a.py      A2A client: discovers store agents from their agent cards, sends them tasks
    protocols/mcp.py      MCP client: grocery-mcp tools as ReAct tools
    manager.py            Manager: talks to the household, orchestrates the sub-agents
    meal_planner.py       Sub-agent: meals for every day + shopping list
    procurement.py        Sub-agent: store quotes + negotiation over A2A, cheapest split
    advisor.py            Sub-agent: spending, price trends, pantry questions
    demo.py               DEMO=1 scripts standing in for each agent's LLM
    store/server.py       A2A store agent (run once per store: STORE=nomin PORT=9101)
    store/negotiator.py   the store's own sales agent (ReAct) behind the negotiate skill
    store/policy.py       each store's negotiation limits, enforced in code
  common/                 db (Mongo helpers), policy, state machine, optimizer, embed
  mcp_server.py           grocery-mcp: profile, recipes, list, branches, routes, policy-guarded wallet
  planner/                FastAPI app: HTTP/SSE API, auth, checkout saga
web/                      single-page UI
seed/                     catalog, recipes, branches + seed.py
scripts/                  probes, e2e demo, chaos (force out-of-stock), price (change a price -> alert)
tests/                    pure-logic tests (no services needed)
```

## Agents

```
household ─ chat ─> Manager (OyuLLM)
                     ├─ meal_planner (OyuLLM) ── MCP: profile, pantry, recipes, build_list
                     ├─ procurement (OpenAI) ─── A2A ──> nomin / emart / sansar store agents (OpenAI)
                     │                                    quote · negotiate (ReAct) · reserve · order · cancel
                     └─ advisor (OpenAI) ─────── MCP: spending_stats, price_history
approve ─> checkout saga (code, not an LLM): reserve -> wallet hold -> place order -> capture
```

- **Every agent is a ReAct loop** (`core/react.py`). Each step is a tool call with a required `thought` argument, so
  the reasoning is logged next to the action. Every step goes to `audit_log` and streams live to the UI timeline.
  `tool_choice=required` keeps models from stopping with an empty or made-up answer.
- **Manager** always uses OyuLLM. It decides who works on what, checks the results against the goal, and sends work
  back when it falls short. It ends with `propose_plan` or `reply`.
- **Sub-agents** share state through the plan document in MongoDB (shopping list, quotes, basket, offers). The
  manager passes short tasks, not data.
- **Models per agent** (`core/llm.py`): meal_planner uses OyuLLM (Mongolian dishes). procurement, advisor and the
  store negotiators use OpenAI (`OPENAI_API_KEY`, `OPENAI_MODEL`). Change one with `MODEL_<ROLE>=oyullm|openai|ollama`.
  Without an OpenAI key they use OyuLLM.
- **Guardrails are code, not prompts.** Examples: a meal plan must cover every day × 3 meals. The plan can't be
  proposed before negotiation, or when it's over budget. Store discounts stay within each store's limits. The wallet
  checks policy on every hold. Stock and money (`reserve`, `place_order`, `cancel`, wallet) never depend on an LLM.
- **Store negotiation:** each store sees its competitors' prices, looks for substitutes in its own catalog (vector
  search) and makes an offer. The offer is saved in `offers` (TTL), and `reserve` charges the agreed price.

## Evaluation

`evals/cases.json` holds scenarios: a week plan, a stated budget, a vegetarian week, a refill, a price question, a
prompt injection and a pantry case. `evals/run.py` runs them against a running stack and scores each one three ways:

- **checks (code):** outcome, every day × 3 meals covered, diet, budget, which agents were used, no payment, and
  every number told to the household must exist in the system's data.
- **LLM judge:** faithfulness and whether it answers the request. Three votes, majority wins. The verdict is
  advisory when the judge is the same model that wrote the message.
- **trace metrics:** steps per agent, tool errors, stalls, tokens, time. Live per-agent metrics: `GET /metrics/agents`.

```bash
uv run --env-file .env python -m evals.run --url http://localhost:8000   # results in evals/results/
```

Baseline vs now (same 7 cases):

| | baseline | now (2 runs) |
|---|---|---|
| code checks | 16/26 | 33/33, 33/33 |
| tool errors (all cases) | 110 | 4, 6 |
| avg tokens per case | 77k | 16k, 18k |
| avg time per case | 75 s | 22 s |

What moved the numbers: facts injected instead of guessed, tools whose arguments can only name real things (enums,
key checks), one `plan_meals` tool instead of servings maths plus a 21-entry list, compact tool results, and code
guards on budget, diet and coverage. The judge's own noise (gpt-4o-mini in Mongolian) is the main remaining variance.

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

Demo price drop: `uv run --env-file .env python -m scripts.price emart milk 3000` (alert in chat).
Unpaid reservations expire after `RESERVE_TTL` seconds (default 900, set at seed) and are restocked.
