"""Meal-planner sub-agent: the household's goal -> meals for every day and a shopping list of what isn't at home.

The model chooses recipes; code does the bookkeeping it kept getting wrong (evals/): expanding a rotation over the
days, servings per recipe, diet rules and the budget check all live in plan_meals, one final tool instead of
build_list + finish."""
import hashlib
import json

from app.agents import demo
from app.agents.core.react import Agent, Tool, run
from app.agents.protocols.mcp import call_json, mcp_tools
from app.common import favorites
from app.common.db import audit, db, now

MEALS = ("breakfast", "lunch", "dinner")
DIETS = ["vegetarian", "no_pork", "no_fish", "no_beef", "no_mutton", "no_chicken"]

SYSTEM = """You are the meal-planning agent of a household grocery assistant in Ulaanbaatar. The manager agent gives
you a task. Work step by step with your tools:
1. Only if the task says the household already has something: search_products for its key, then set_pantry.
2. search_recipes for breakfast, lunch and dinner ideas. It already applies the household's diet. Prefer low
   cost_per_serving: stay near the target per serving in the facts.
3. plan_meals: for each meal, the recipe _ids to rotate through the days (e.g. breakfast: [a], lunch: [b, c],
   dinner: [c, d]). Cheap recipes should repeat. It works out servings, subtracts the pantry, builds the shopping
   list and checks the budget. If it says over budget, replace the recipes it names and call it again.
Stores sell whole packs: every extra ingredient costs a whole pack (a bottle of soy sauce for a spoonful). Few
recipes sharing the same ingredients are cheapest."""


def expand(days, rotation):
    """{meal: [recipe ids]} -> one entry per day and meal, cycling through each meal's recipes."""
    return [{"day": d, "meal": m, "recipe": rotation[m][(d - 1) % len(rotation[m])]}
            for d in range(1, days + 1) for m in MEALS]


def diet_violation(diet, tags):
    """The first diet rule the recipe's tags break, or None."""
    for rule in sorted(diet):
        if rule == "vegetarian" and "vegetarian" not in tags or rule.startswith("no_") and rule[3:] in tags:
            return rule
    return None


async def search_recipes(ctx, query, limit=10):
    exclude = [r[3:] for r in ctx["diet"] if r.startswith("no_")]
    r = await call_json(ctx["mcp"], "search_recipes", {"query": query, "exclude_tags": exclude or None,
                                                       "limit": max(int(limit), 10)})
    if "error" in r:
        return r
    missing = ctx["liked"] - {x["_id"] for x in r["recipes"]}
    if missing:  # favourites are always among the options, whatever the query was
        names = [x["name_mn"] async for x in db.recipes.find({"_id": {"$in": list(missing)}}, {"name_mn": 1})]
        more = await call_json(ctx["mcp"], "search_recipes", {"query": " ".join(names), "exclude_tags": exclude or None,
                                                              "limit": 10})
        r["recipes"] += [x for x in more.get("recipes", []) if x["_id"] in missing]
    home = set(await db.pantry.distinct("key", {"user_id": ctx["user"]["_id"], "amount": {"$gt": 0}}))
    # A recipe needing something no store sells (and not at home) can't be cooked from this list: not offered at all
    # the household's favourites first and marked; dishes they won't eat are never offered
    found = [{**{k: x[k] for k in ("_id", "name_mn", "tags", "cost_per_serving")},
              **({"favorite": True} if x["_id"] in ctx["liked"] else {})}
             for x in r["recipes"] if not diet_violation(ctx["diet"], x["tags"])
             and not set(x.get("unavailable") or []) - home and x["_id"] not in ctx["disliked"]]
    return {"recipes": sorted(found, key=lambda x: not x.get("favorite"))}


async def plan_meals(ctx, breakfast, lunch, dinner, note="", over_budget_ok=False):
    rotation = {"breakfast": breakfast, "lunch": lunch, "dinner": dinner}
    if empty := [m for m, ids in rotation.items() if not ids]:
        return {"error": f"give at least one recipe _id for {', '.join(empty)}"}
    ids = {i for v in rotation.values() for i in v}
    recipes = {r["_id"]: r async for r in db.recipes.find({"_id": {"$in": list(ids)}},
                                                          {"name_mn": 1, "tags": 1, "ingredients.key": 1})}
    if unknown := sorted(ids - set(recipes)):  # models make up ids like "recipe_1"
        return {"error": f"unknown recipe ids {unknown}: use the _id values search_recipes returned"}
    if bad := {i: rule for i in ids if (rule := diet_violation(ctx["diet"], recipes[i]["tags"]))}:
        return {"error": f"these recipes break the diet: {bad}; pick others from search_recipes"}
    if refused := sorted(ids & ctx["disliked"]):
        return {"error": f"the household doesn't eat {refused}; pick others from search_recipes"}
    if ctx["liked"] and not ids & ctx["liked"] and not ctx.get("favorite_nudged"):
        ctx["favorite_nudged"] = True  # once: a favourite that can't fit the budget may be left out on the next call
        return {"error": f"the household's favourites {sorted(ctx['liked'])} aren't in this plan: include at least one "
                         "(search_recipes lists them) unless it can't fit the budget"}
    meals = expand(ctx["days"], rotation)
    counts = {}
    for m in meals:
        counts[m["recipe"]] = counts.get(m["recipe"], 0) + 1
    r = await call_json(ctx["mcp"], "build_list", {"user_id": ctx["user"]["_id"], "meals": [
        {"recipe_id": i, "servings": n * ctx["user"]["household"]} for i, n in counts.items()]})
    if "items" not in r:
        return r
    if r.get("unavailable"):  # the list would silently lack them
        return {"error": f"no store sells {', '.join(r['unavailable'])} and it isn't at home: pick recipes without it"}
    ctx["attempts"] = ctx.get("attempts", 0) + 1
    best = ctx.get("cheapest")
    if not best or r["estimated_total"] < best[0]:
        ctx["cheapest"] = best = (r["estimated_total"], rotation)
    if over_budget_ok and best[0] < r["estimated_total"]:  # hand back the cheapest try, not just the latest one
        return await plan_meals(ctx, **best[1], note=note, over_budget_ok=True)
    over = r["estimated_total"] > ctx["budget"]
    if over and not (over_budget_ok and ctx["attempts"] >= 3):
        # Name the recipes behind each pricey pack: "swap the priciest recipes" alone got the same plan back again.
        used_by = lambda key: [i for i in counts if key in {g["key"] for g in recipes[i]["ingredients"]}]  # noqa: E731
        culprits = [f"{i['name']} {i['est_cost']}₮ (for {', '.join(used_by(i['key']))})" for i in r["items"][:4]]
        return {"error": f"estimated_total {r['estimated_total']} is over the budget {ctx['budget']}. Priciest packs: "
                         f"{'; '.join(culprits)}. Replace those recipes, or reuse recipes you already have; call "
                         "plan_meals again"
                         + (". If it can't fit, pass over_budget_ok=true to hand back your cheapest plan"
                            if ctx["attempts"] >= 2 else "")}
    named = [{**m, "recipe": recipes[m["recipe"]]["name_mn"]} for m in meals]
    ctx["chosen"] = None if over else rotation  # what the plan cache keeps
    r["budget"] = ctx["budget"]
    await db.plans.update_one({"_id": ctx["plan_id"], "state": "planning"},
                              {"$set": {"shopping_list": r, "meals": named}})
    return {"days": ctx["days"], "recipes": sorted({m["recipe"] for m in named}), "items_to_buy": len(r["items"]),
            "estimated_total": r["estimated_total"], "budget": ctx["budget"],
            "from_pantry": [p["name"] for p in r["from_pantry"]], "nothing_to_buy": not r["items"], "note": note,
            **({"over_budget": True, "cheapest_found": r["estimated_total"]} if over else {})}


async def agent(mcp):
    ids = {"type": "array", "items": {"type": "string"}, "description": "recipe _ids, rotated over the days"}
    return Agent("meal_planner", SYSTEM, max_steps=12, script=demo.chat if demo.ON else None, tools=[
        *await mcp_tools(mcp, {"get_pantry", "set_pantry", "search_products"}),
        Tool("search_recipes", "Semantic recipe search, already filtered to the household's diet. Returns _id, "
             "Mongolian name, tags and cost_per_serving at the cheapest known prices.", search_recipes,
             {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
        Tool("plan_meals", "Final step: the recipes for each meal, rotated over the days. Builds the shopping list "
             "(minus the pantry) and checks the budget; saved for the procurement agent.", plan_meals,
             {"breakfast": ids, "lunch": ids, "dinner": ids, "note": {"type": "string"},
              "over_budget_ok": {"type": "boolean", "description": "only after several over-budget tries: hand back "
                                                                    "the cheapest plan you found anyway"}},
             ["breakfast", "lunch", "dinner"], final=True),
    ])


def brief(ctx, task):
    """The task plus the numbers the model otherwise guesses: who eats, the diet, what a serving may cost."""
    u, servings = ctx["user"], ctx["user"]["household"] * ctx["days"] * 3
    return (f"{task}\n\nFacts (from the system, authoritative):\n- days: {ctx['days']}, household: {u['household']} people, "
            f"diet: {', '.join(sorted(ctx['diet'])) or 'none'}\n- servings in total: about {servings}\n"
            f"- budget: {ctx['budget']}₮, so recipes should average at most {ctx['budget'] * 0.9 // servings:.0f}₮ per serving"
            + (f"\n- favourite dishes (search_recipes marks them): include them when the budget allows: "
               f"{', '.join(ctx['liked'])}" if ctx["liked"] else ""))


async def cache_key(ctx):
    """Everything a meal plan depends on, exactly: the household's own words, the plan's numbers and diet, its
    favourites, what's at home and which day's prices. A semantic match was tried and rejected: "60 мянга" and
    "6 мянга" embed at 0.94, closer than two phrasings of the same question."""
    home = sorted([(p["key"], round(p["amount"], 2))
                   async for p in db.pantry.find({"user_id": ctx["user"]["_id"], "amount": {"$gt": 0}})])
    prices = await db.scout_runs.find_one({"ok": True}, sort=[("ts", -1)])
    raw = json.dumps({"text": " ".join(ctx.get("text", "").lower().split()), "days": ctx["days"],
                      "household": ctx["user"]["household"], "diet": sorted(ctx["diet"]), "budget": ctx["budget"],
                      "liked": sorted(ctx["liked"]), "disliked": sorted(ctx["disliked"]), "home": home,
                      "prices": str(prices and prices["ts"])}, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def from_cache(ctx, key):
    """The same request as before: reuse the dishes, rebuild the list and budget check on today's numbers."""
    hit = await db.plan_cache.find_one({"_id": key})
    if not hit:
        return None
    r = await plan_meals(ctx, **hit["rotation"], note="same request as before: dishes reused")
    if "error" in r or r.get("over_budget"):
        return None
    await audit(ctx["plan_id"], "meal_planner", "plan_meals", args=hit["rotation"], result=r, model="cache",
                reason="Same request, household, pantry and prices as an earlier plan: reused its dishes; the list and "
                       "the budget check were rebuilt now.")
    return r


async def delegate(ctx, task, days, diet=None):
    """diet: rules for this plan on top of the profile's (e.g. a vegetarian week)."""
    # A new meal plan replaces the old one: a failed run must not leave a list behind for procurement.
    await db.plans.update_one({"_id": ctx["plan_id"], "state": "planning"},
                              {"$unset": {"shopping_list": "", "meals": ""}})
    liked, disliked, rules, _ = await favorites.of(ctx["user"]["_id"])
    ctx = {**ctx, "days": max(1, int(days)), "liked": liked, "disliked": disliked,
           "diet": set(ctx["user"].get("diet", [])) | rules | {d for d in diet or [] if d in DIETS}}
    first = ctx.get("meal_plans", 1) <= 1 and ctx.get("text")  # a second try comes with changes: plan afresh
    key = await cache_key(ctx) if first else None
    if key and (hit := await from_cache(ctx, key)):
        return hit
    result = await run(await agent(ctx["mcp"]), ctx, brief(ctx, task))
    if key and "error" not in result and ctx.get("chosen"):
        await db.plan_cache.replace_one({"_id": key}, {"rotation": ctx["chosen"], "ts": now()}, upsert=True)
    return result
