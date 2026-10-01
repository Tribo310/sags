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

## Real prices: the price scout

`app/agents/scout/` collects real products and prices from the stores' own public web endpoints. It runs from the
command line, not inside a household's request:

- **Nomin:** reads the grocery collections (about 5,300 products) from the route the site's category pages use.
- **Emart:** uses the site's own search, one query per catalog item.
- **Sansar:** has no online shop, so it sells nothing in the catalog.

The requests use an identified user agent and are spaced 2 s apart.

**How matching works:**
1. Code picks candidates for each catalog item with a keyword filter, then embedding similarity.
2. The scout agent (ReAct, OpenAI) decides which candidate is the same everyday product, and reads its pack size in
   our unit (for example "910гр" of milk is 0.91 l).
3. Code checks plausibility. Unsure or implausible matches go to a review queue and are not applied.
4. Accepted matches are written to `products` exactly as the store sells them: name, pack, price, sku, url.
   Products sold by weight use 100 g steps at the real per-kg price.

Anything without a real match is removed, including simulated price history. Stores quote in their own packs:
2 l of milk is 3 packs of 0.91 l at one store and 2 packs of 1 l at another.

```bash
uv run --env-file .env python -m seed.seed --catalog          # catalog only: keeps users, plans, history
uv run --env-file .env python -m app.agents.scout --dry-run   # crawl + match, show results, change nothing
uv run --env-file .env python -m app.agents.scout             # crawl + match + write real products
```

Raw listings are stored in `crawl_raw`, and every decision (with the agent's reason) in `crawl_matches`.

**It runs by itself once a day.** Every planner process checks every 10 minutes whether today's run (at
`SCOUT_HOUR`, default 03:00 Ulaanbaatar time) is done. A lock document in MongoDB lets exactly one process crawl, and
`scout_runs` records each run, so it runs once a day even with several planners on a shared database. A day missed
while everything was down is caught up by the next planner that starts. The basket shows when the prices were
last refreshed. `SCOUT_DAILY=0` turns it off for a process.

**Caches (exact keys, never semantic).** The scout reuses yesterday's decision for an item when both stores show the
same candidates, with today's prices and checks (`--fresh` decides again). The meal planner reuses the dishes of an
earlier plan when the household's words, numbers, diet, favourites, pantry and price date are all identical; the list
and budget check are rebuilt now (`plan_cache`, 2-day TTL). A semantic cache was measured and rejected: "60 мянга"
and "6 мянга", or "авмаар" and "авмааргүй", embed closer (0.94) than two phrasings of one question.

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
