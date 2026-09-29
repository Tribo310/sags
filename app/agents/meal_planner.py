"""Meal-planner sub-agent: the household's goal -> meals for every day and a shopping list of what isn't at home.

The model chooses recipes; code does the bookkeeping it kept getting wrong (evals/): expanding a rotation over the
days, servings per recipe, diet rules and the budget check all live in plan_meals, one final tool instead of
build_list + finish."""
from app.agents import demo
from app.agents.core.react import Agent, Tool, run
from app.agents.protocols.mcp import call_json, mcp_tools
from app.common.db import db

MEALS = ("breakfast", "lunch", "dinner")
DIETS = ["vegetarian", "no_pork", "no_fish", "no_beef", "no_mutton", "no_chicken"]

SYSTEM = """You are the meal-planning agent of a household grocery assistant in Ulaanbaatar. The manager agent gives
you a task. Work step by step with your tools:
1. Only if the task says the household already has something: search_products for its key, then set_pantry.
2. search_recipes for breakfast, lunch and dinner ideas. It already applies the household's diet. Prefer low
   cost_per_serving: stay near the target per serving in the facts.
3. plan_meals: for each meal, the recipe _ids to rotate through the days (e.g. breakfast: [a], lunch: [b, c],
   dinner: [c, d]). Cheap recipes should repeat. It works out servings, subtracts the pantry, builds the shopping
   list and checks the budget. If it says over budget, swap the priciest recipes for cheaper ones and call it again."""


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
    return {"recipes": [{k: x[k] for k in ("_id", "name_mn", "tags", "cost_per_serving")}
                        for x in r["recipes"] if not diet_violation(ctx["diet"], x["tags"])]}


async def plan_meals(ctx, breakfast, lunch, dinner, note=""):
    rotation = {"breakfast": breakfast, "lunch": lunch, "dinner": dinner}
    if empty := [m for m, ids in rotation.items() if not ids]:
        return {"error": f"give at least one recipe _id for {', '.join(empty)}"}
    ids = {i for v in rotation.values() for i in v}
    recipes = {r["_id"]: r async for r in db.recipes.find({"_id": {"$in": list(ids)}}, {"name_mn": 1, "tags": 1})}
    if unknown := sorted(ids - set(recipes)):  # models make up ids like "recipe_1"
        return {"error": f"unknown recipe ids {unknown}: use the _id values search_recipes returned"}
    if bad := {i: rule for i in ids if (rule := diet_violation(ctx["diet"], recipes[i]["tags"]))}:
        return {"error": f"these recipes break the diet: {bad}; pick others from search_recipes"}
    meals = expand(ctx["days"], rotation)
    counts = {}
    for m in meals:
        counts[m["recipe"]] = counts.get(m["recipe"], 0) + 1
    r = await call_json(ctx["mcp"], "build_list", {"user_id": ctx["user"]["_id"], "meals": [
        {"recipe_id": i, "servings": n * ctx["user"]["household"]} for i, n in counts.items()]})
    if "items" not in r:
        return r
    if r["estimated_total"] > ctx["budget"]:
        return {"error": f"estimated_total {r['estimated_total']} is over the budget {ctx['budget']}. Priciest items: "
                         f"{', '.join(i['name'] for i in r['items'][:4])}. Swap the recipes that use them for cheaper "
                         "ones and call plan_meals again"}
    named = [{**m, "recipe": recipes[m["recipe"]]["name_mn"]} for m in meals]
    r["budget"] = ctx["budget"]
    await db.plans.update_one({"_id": ctx["plan_id"], "state": "planning"},
                              {"$set": {"shopping_list": r, "meals": named}})
    return {"days": ctx["days"], "recipes": sorted({m["recipe"] for m in named}), "items_to_buy": len(r["items"]),
            "estimated_total": r["estimated_total"], "budget": ctx["budget"],
            "from_pantry": [p["name"] for p in r["from_pantry"]], "nothing_to_buy": not r["items"], "note": note}


async def agent(mcp):
    ids = {"type": "array", "items": {"type": "string"}, "description": "recipe _ids, rotated over the days"}
    return Agent("meal_planner", SYSTEM, max_steps=12, script=demo.chat if demo.ON else None, tools=[
        *await mcp_tools(mcp, {"get_pantry", "set_pantry", "search_products"}),
        Tool("search_recipes", "Semantic recipe search, already filtered to the household's diet. Returns _id, "
             "Mongolian name, tags and cost_per_serving at the cheapest known prices.", search_recipes,
             {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
        Tool("plan_meals", "Final step: the recipes for each meal, rotated over the days. Builds the shopping list "
             "(minus the pantry) and checks the budget; saved for the procurement agent.", plan_meals,
             {"breakfast": ids, "lunch": ids, "dinner": ids, "note": {"type": "string"}},
             ["breakfast", "lunch", "dinner"], final=True),
    ])


def brief(ctx, task):
    """The task plus the numbers the model otherwise guesses: who eats, the diet, what a serving may cost."""
    u, servings = ctx["user"], ctx["user"]["household"] * ctx["days"] * 3
    return (f"{task}\n\nFacts (from the system, authoritative):\n- days: {ctx['days']}, household: {u['household']} people, "
            f"diet: {', '.join(sorted(ctx['diet'])) or 'none'}\n- servings in total: about {servings}\n"
            f"- budget: {ctx['budget']}₮, so recipes should average at most {ctx['budget'] * 0.9 // servings:.0f}₮ per serving")


async def delegate(ctx, task, days, diet=None):
    """diet: rules for this plan on top of the profile's (e.g. a vegetarian week)."""
    # A new meal plan replaces the old one: a failed run must not leave a list behind for procurement.
    await db.plans.update_one({"_id": ctx["plan_id"], "state": "planning"},
                              {"$unset": {"shopping_list": "", "meals": ""}})
    ctx = {**ctx, "days": max(1, int(days)),
           "diet": set(ctx["user"].get("diet", [])) | {d for d in diet or [] if d in DIETS}}
    return await run(await agent(ctx["mcp"]), ctx, brief(ctx, task))
