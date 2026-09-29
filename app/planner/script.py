"""DEMO=1: a deterministic stand-in for the LLM. It walks the same tool sequence the prompt asks for, so a live
demo can't stall on a slow or confused model. Same signature as llm.chat."""
import json
import os
import uuid

from openai.types.chat import ChatCompletionMessage

ON = os.getenv("DEMO") == "1"
DAYS = 7
MODEL = "demo-script"


def call(name, **args):
    return ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]})


def say(text):
    return ChatCompletionMessage.model_validate({"role": "assistant", "content": text})


def pick(recipes, household):
    """Cheapest breakfast every morning; the two cheapest other recipes alternate for lunch and dinner."""
    by_cost = sorted(recipes, key=lambda r: r["cost_per_serving"])
    breakfast = [r for r in by_cost if "breakfast" in r["tags"]][:1]
    mains = [r for r in by_cost if "breakfast" not in r["tags"]][:2] or by_cost[:2]
    breakfast = breakfast or mains[:1]
    meals = [{"day": d, "meal": "breakfast", "recipe": breakfast[0]["name_mn"]} for d in range(1, DAYS + 1)]
    for d in range(1, DAYS + 1):
        meals += [{"day": d, "meal": "lunch", "recipe": mains[d % len(mains)]["name_mn"]},
                  {"day": d, "meal": "dinner", "recipe": mains[(d + 1) % len(mains)]["name_mn"]}]
    servings = {}
    for r, n in [(breakfast[0], DAYS)] + [(r, 2 * DAYS / len(mains)) for r in mains]:
        servings[r["_id"]] = servings.get(r["_id"], 0) + round(n * household)
    return meals, [{"recipe_id": k, "servings": v} for k, v in servings.items()]


async def chat(messages, tools):
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    turn = messages[last_user:]
    names = {c["id"]: c["function"]["name"] for m in turn for c in m.get("tool_calls", [])}
    done = [(names[m["tool_call_id"]], json.loads(m["content"])) for m in turn if m["role"] == "tool"]
    got = dict(done)  # tool -> latest result
    user_id = messages[0]["content"].rsplit("user_id: ", 1)[1].strip()

    if "get_profile" not in got:
        return call("get_profile", user_id=user_id), MODEL
    profile = got["get_profile"]
    if "search_recipes" not in got:
        exclude = ["pork"] if "no_pork" in profile.get("diet", []) else []
        return call("search_recipes", query="хямд өглөөний цай, өдрийн болон оройн хоол", exclude_tags=exclude, limit=20), MODEL
    recipes = got["search_recipes"]["recipes"]
    if "vegetarian" in profile.get("diet", []):
        recipes = [r for r in recipes if "vegetarian" in r["tags"]]
    if not recipes:
        return say("Тохирох жор олдсонгүй."), MODEL
    meals, servings = pick(recipes, profile["household"])
    if "build_list" not in got:
        return call("build_list", user_id=user_id, meals=servings), MODEL
    listed = got["build_list"]
    quotes = [r for n, r in done if n == "quote_stores"]
    missing = quotes[-1].get("basket", {}).get("missing", []) if quotes else []
    if not quotes or (missing and len(quotes) == 1):  # re-quote once without items no store has
        return call("quote_stores", items=[{"key": i["key"], "qty": i["qty"]} for i in listed["items"]
                                           if i["key"] not in missing]), MODEL
    for r in (quotes[-1], got.get("propose_plan", {})):
        if r.get("error"):
            return say(r["error"]), MODEL
    b = quotes[-1]["basket"]
    text = (f"{DAYS} хоногийн төлөвлөгөө: {', '.join(sorted({m['recipe'] for m in meals}))}. "
            f"{', '.join(b['stores'])} дэлгүүрээс нийт {b['total']:,}₮ / {quotes[-1]['budget']:,}₮.")
    if listed.get("from_pantry"):
        text += f" Гэрт байгаа {len(listed['from_pantry'])} төрлийн барааг хасав."
    if b.get("savings"):
        text += f" Хамгийн үнэтэй дэлгүүрээс {b['savings']:,}₮ хямд."
    if missing:
        text += f" Дууссан тул хасав: {', '.join(missing)}."
    return call("propose_plan", summary=text, meals=meals), MODEL
