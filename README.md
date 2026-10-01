# Сагс — agentic grocery shopping

Tell it, in Mongolian, what you need: *"7 хоногийн хүнсний жагсаалт гарга. 150 мянган төгрөг, 2 хүн, гахайн мах
иддэггүй."* (a week of groceries, 150k ₮, 2 people, no pork). A team of agents takes it from there:

1. **Manager** reads the request and the household profile, and splits the work.
2. **Meal planner** plans 3 meals for every day from the recipe base (skipping what the pantry already has) and builds
   the shopping list.
3. **Procurement** asks 3 store agents (nomin, emart, sansar) for quotes over **A2A**, negotiates discounts with each
   store's own sales agent, and picks the cheapest split across stores.
4. You get a plan with the basket, prices and savings. **Approve** it.
5. **Checkout** (plain code, no LLM) runs a saga per store: reserve stock → wallet hold → place order → capture. A
   failure at any step rolls back. You get receipts, can cancel/refund, and see a pickup trip through the stores on
   a map.

The manager remembers what the household says it likes or won't eat ("цуйван дуртай", "загас иддэггүй") and the
meal planner honours it. You can talk to it by voice and have replies read aloud. Every agent step is logged to an
audit trail and streamed live to the UI. Store prices are real, collected by the price scout (below).

| Requirement | Where |
|---|---|
| Agentic behaviour | ReAct loop for every agent (`app/agents/core/react.py`), manager delegating to sub-agents |
| MongoDB | Atlas Local: vector search (recipes, products), transactions (checkout), change streams (live UI, price alerts), TTL (offers, unpaid reservations), time series (price history) |
| MCP | `grocery-mcp` (`app/mcp_server.py`): profile, recipes, products, list, pantry, spending, price history, branches, routes, trip, policy-guarded wallet |
| Agent2Agent | Store agents (`app/agents/store/server.py`) with agent cards: quote · negotiate · reserve · order · cancel |

## Setup

Needs Python 3.13 + [uv](https://docs.astral.sh/uv/), Node + npm, Docker and [Ollama](https://ollama.com).

```bash
cp .env.example .env
uv sync
ollama pull bge-m3 && ollama pull qwen3:8b    # embeddings + local fallback LLM
docker compose up -d                          # MongoDB Atlas Local on :27018
uv run --env-file .env python -m seed.seed    # catalog, recipes, branches, indexes
```

Optional, for real store prices instead of the seeded ones: `uv run --env-file .env python -m app.agents.scout` (see
[the price scout](#real-prices-the-price-scout)).

Fill in `.env` (everything else has working defaults):

| Variable | |
|---|---|
| `LLM_API_KEY` | OyuLLM key. The manager and meal planner always use it. |
| `OPENAI_API_KEY` | Optional. Procurement, advisor and store negotiators use OpenAI; empty → they use OyuLLM. |
| `JWT_SECRET` | Required, ≥ 32 chars: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `OYUVOICE_API_KEY` | Optional, voice input (speech to text) and spoken replies. The OyuLLM key works. `TTS_VOICE` picks the voice. |
| `EMBED_API_KEY` | Optional. Set it with `EMBED_BASE_URL=https://api.oyu.so/v1` to get bge-m3 embeddings from the oyu API instead of local Ollama (same vectors, faster). |
| `MONGO_URL` | Defaults to the local container. An Atlas URL works too. |

Check the LLM providers and embeddings answer: `uv run --env-file .env python -m scripts.check_llm`.

## Run

```bash
./run.sh            # open http://localhost:8000, sign up, chat
```

`run.sh` starts Mongo (when `MONGO_URL` is local) and Ollama if they're down, rebuilds `web/` when its sources
changed, then starts:

| Service | Port |
|---|---|
| grocery-mcp (MCP server) | 8100 |
| store agents nomin / emart / sansar (A2A) | 9101 / 9102 / 9103 |
| planner (API + UI) | 8000 |

Ctrl-C stops everything. Services bind to localhost; `HOST=0.0.0.0` exposes them (the MCP and store agents have no
auth).

- **Demo mode:** `DEMO=1 ./run.sh` reseeds the DB and swaps every LLM for a deterministic script
  (`app/agents/demo.py`). Same tools, same ReAct steps, no API keys used: safe for a live demo.
- **Frontend dev:** with `run.sh` up, `cd web && npm run dev` serves the UI on :5173 with hot reload, proxying API
  calls to :8000.

### Things to try

```bash
uv run --env-file .env python -m scripts.price emart milk 3000   # price drop -> alert in chat
uv run --env-file .env python -m scripts.chaos emart milk        # sold out at one store -> checkout reroutes
uv run --env-file .env python -m scripts.chaos all milk          # sold out everywhere -> agent replans
```

Unpaid reservations expire after `RESERVE_TTL` seconds (default 900, set at seed) and are restocked.

### API

`POST /chat` (start or continue a plan) · `GET /plans`, `GET /plans/{id}` · `POST /plans/{id}/approve` ·
`POST /plans/{id}/cancel` · `GET /me/stream` (SSE: agent steps, plan updates, alerts) · `GET /me/stats`,
`GET /me/pantry` · `GET /me/favorites`, `DELETE /me/favorites/{id}` · `POST /stt`, `POST /tts` (voice) ·
`GET /metrics/agents`. All need a login cookie (`/auth/*`).

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
    scout/                Price scout: crawls real store listings, matches them to the catalog (CLI)
    demo.py               DEMO=1 scripts standing in for each agent's LLM
    store/server.py       A2A store agent (run once per store: STORE=nomin PORT=9101)
    store/negotiator.py   the store's own sales agent (ReAct) behind the negotiate skill
    store/policy.py       each store's negotiation limits, enforced in code
  common/                 db (Mongo helpers), policy, state machine, optimizer, embed, favorites
  mcp_server.py           grocery-mcp: profile, recipes, list, branches, routes, policy-guarded wallet
  planner/                FastAPI app: HTTP/SSE API, auth, checkout saga
web/                      single-page UI
seed/                     catalog, recipes, branches + seed.py
scripts/                  probes, e2e demo, chaos (force out-of-stock), price (change a price -> alert)
tests/                    pure-logic tests (no services needed)
```

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

## Tests

```bash
uv run pytest -q    # pure logic: optimizer, policy, state machine, checkout, auth, ReAct loop, scout, favorites... no services needed
```
