import json

from app.common import llm
from app.common.db import audit, db, now
from app.common.optimizer import optimize
from app.common.policy import check_items
from app.common.state import transition
from app.planner import script, stores
from app.planner.mcp_client import call_json, connect

LLM_TOOLS = {"get_profile", "search_recipes", "search_products", "build_list", "nearest_branches", "get_route",
             "get_pantry", "set_pantry", "spending_stats", "price_history"}
MAX_STEPS = 20

SYSTEM = """You are a grocery shopping agent for a household in Ulaanbaatar. Reply in the user's language (Mongolian or English).
Goal: a meal plan and grocery list that fits the budget and diet, bought as cheaply as possible in at most policy.max_stores stores.
Steps:
1. get_profile: household size, diet, budget, location, remembered preferences.
2. search_recipes for suitable meals. Diet 'no_pork' -> exclude_tags ['pork']; 'vegetarian' -> pick only recipes tagged vegetarian.
   Plan breakfast, lunch and dinner for every requested day; cheap recipes may repeat.
3. build_list with recipe ids and servings (servings = household size x number of times that meal is eaten).
   It subtracts what is already at home (from_pantry). If the user says they have something, set_pantry first.
   If estimated_total > budget, swap expensive recipes for cheaper ones and rebuild.
4. quote_stores with the list's items (key + qty). If over_budget or items are missing, change recipes and repeat.
5. remember any lasting preference the user told you.
6. propose_plan with a short summary: meals per day, where to buy, total vs budget, what the pantry covered, main trade-offs.
If the user only wants specific products (e.g. a refill), skip recipes: search_products, then quote_stores with those keys.
For questions about past spending use spending_stats. For price trends or "is now a good time to buy" use price_history.
Never claim anything is ordered or paid. Checkout happens only after the user approves."""


def inline_refs(schema, defs=None):
    """Replace $ref/$defs with the referenced schema: local models' tool parsers often ignore $ref."""
    defs = schema.get("$defs", {}) if defs is None else defs
    if isinstance(schema, dict):
        if "$ref" in schema:
            return inline_refs(defs[schema["$ref"].split("/")[-1]], defs)
        return {k: inline_refs(v, defs) for k, v in schema.items() if k != "$defs"}
    if isinstance(schema, list):
        return [inline_refs(v, defs) for v in schema]
    return schema


def fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


LOCAL_TOOLS = [
    fn("quote_stores", "Ask every store agent (A2A) for live prices and stock, then pick the cheapest split within policy.max_stores.",
       {"items": {"type": "array", "items": {"type": "object", "properties": {
           "key": {"type": "string"}, "qty": {"type": "integer"}}, "required": ["key", "qty"]}}}, ["items"]),
    fn("propose_plan", "Finish planning and show the plan to the user for approval. Only after quote_stores returned a basket within budget.",
       {"summary": {"type": "string"}, "meals": {"type": "array", "items": {"type": "object", "properties": {
           "day": {"type": "integer"}, "meal": {"type": "string"}, "recipe": {"type": "string"}}}}}, ["summary", "meals"]),
    fn("remember", "Save a lasting user preference, e.g. 'does not eat fish'.", {"fact": {"type": "string"}}, ["fact"]),
]


async def quote_stores(ctx, items):
    policy = ctx["user"]["policy"]
    needed = {i["key"]: int(i["qty"]) for i in items}
    answered = await stores.quote_all([{"key": k, "qty": q} for k, q in needed.items()])
    quotes = {s: {l["key"]: l for l in r["lines"]} for s, r in answered.items() if s in policy["allowed_stores"]}
    if not quotes:
        return {"error": "no store agent answered"}
    lines = {l["key"]: {"key": l["key"], "category": l["category"], "qty": needed[l["key"]]}
             for q in quotes.values() for l in q.values()}
    removed = check_items(policy, list(lines.values()))  # enforced in code, not by the prompt
    for k in removed:
        needed.pop(k)
    best = optimize(needed, quotes, policy["max_stores"])
    # Savings vs buying everything at the priciest store that has it all (never negative: single stores are candidates).
    complete = [sum(q[k]["price"] * n for k, n in needed.items()) for q in quotes.values()
                if all(k in q and q[k]["in_stock"] for k in needed)]
    best["savings"] = max(complete) - best["total"] if complete else 0
    # Only a plan still being planned may change: never rewrite a basket the user is looking at or approved.
    r = await db.plans.update_one({"_id": ctx["plan_id"], "state": "planning"}, {"$set": {"quotes": quotes, "basket": best}})
    if not r.matched_count:
        return {"error": "plan is no longer being planned; stop"}
    return {"basket": best, "budget": policy["budget"], "over_budget": best["total"] > policy["budget"],
            "removed_by_policy": removed, "stores_answered": list(quotes),
            "full_basket_per_store": {s: sum(l["price"] * l["qty"] for l in q.values() if l["in_stock"])
                                      for s, q in quotes.items()}}


async def propose_plan(ctx, summary, meals):
    plan = await db.plans.find_one({"_id": ctx["plan_id"]})
    b, policy = plan.get("basket"), ctx["user"]["policy"]
    if not b:
        return {"error": "call quote_stores first"}
    if not b["stores"]:
        return {"error": "nothing to buy: the pantry already covers this plan. Tell the user instead of proposing an order."}
    if b["total"] > policy["budget"]:
        return {"error": f"total {b['total']} is over budget {policy['budget']}; change recipes"}
    if b["missing"]:
        return {"error": f"items {b['missing']} are not available; replace them"}
    lon, lat = ctx["user"]["location"]["coordinates"]
    # Map info is nice-to-have: a failed lookup must not block the proposal.
    found = await call_json(ctx["mcp"], "nearest_branches", {"lon": lon, "lat": lat, "stores": list(b["stores"])})
    branches = found.get("branches", [])
    for br in branches:
        to_lon, to_lat = br["location"]["coordinates"]
        br["route"] = await call_json(ctx["mcp"], "get_route", {"from_lon": lon, "from_lat": lat, "to_lon": to_lon, "to_lat": to_lat})
    await transition(ctx["plan_id"], "planning", "awaiting_approval", summary=summary, meals=meals,
                     branches=branches, budget=policy["budget"])
    await audit(ctx["plan_id"], "agent", "reply", result={"text": summary})
    ctx["done"] = True
    return {"ok": True, "total": b["total"]}


async def remember(ctx, fact):
    await db.agent_memory.insert_one({"user_id": ctx["user"]["_id"], "fact": fact, "ts": now()})
    return {"ok": True}


LOCAL = {"quote_stores": quote_stores, "propose_plan": propose_plan, "remember": remember}


async def save(plan_id, messages):
    """Persist the conversation after every step so a crash mid-run keeps the context for the next message."""
    await db.plans.update_one({"_id": plan_id}, {"$set": {"messages": messages}})


async def run_agent(plan_id, text):
    plan = await db.plans.find_one({"_id": plan_id})
    user = await db.users.find_one({"_id": plan["user_id"]})
    messages = plan.get("messages") or [{"role": "system", "content": f"{SYSTEM}\nuser_id: {user['_id']}"}]
    messages.append({"role": "user", "content": text})
    async with connect() as mcp:
        tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                   "parameters": inline_refs(t.input_schema)}}
                 for t in (await mcp.list_tools()).tools if t.name in LLM_TOOLS] + LOCAL_TOOLS
        ctx = {"plan_id": plan_id, "user": user, "mcp": mcp, "done": False}
        for _ in range(MAX_STEPS):
            msg, model = await (script.chat if script.ON else llm.chat)(messages, tools)
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                await audit(plan_id, "agent", "reply", result={"text": msg.content, "model": model})
                break
            for call in msg.tool_calls:
                name, args = call.function.name, None
                try:
                    args = json.loads(call.function.arguments or "{}")
                    if name in LOCAL:
                        result = await LOCAL[name](ctx, **args)
                    elif name in LLM_TOOLS:
                        if "user_id" in args:  # the session's user, never one the LLM was talked into
                            args["user_id"] = ctx["user"]["_id"]
                        result = await call_json(mcp, name, args)
                    else:  # the LLM never gets wallet tools, even if it hallucinates them
                        result = {"error": f"unknown tool {name}"}
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
                await audit(plan_id, "agent", name, args=args, result=result, reason=msg.content)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
            await save(plan_id, messages)
            if ctx["done"]:
                break
        else:
            await audit(plan_id, "agent", "error", result={"error": f"gave up after {MAX_STEPS} steps"})
    await save(plan_id, messages)
