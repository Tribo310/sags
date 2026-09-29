"""DEMO=1: deterministic stand-ins for the LLMs, one script per agent role. They walk the same ReAct steps the prompts
ask for, so a live demo can't stall on a slow or confused model. Same signature as llm.chat."""
import json
import os
import re
import uuid

from openai.types.chat import ChatCompletionMessage

ON = os.getenv("DEMO") == "1"
DAYS = 7
MODEL = "demo-script"
STORE_NAMES = {"nomin": "Номин", "emart": "Имарт", "sansar": "Сансар"}
MEAL_NAMES = {"breakfast": "өглөө", "lunch": "өдөр", "dinner": "орой"}


def call(name, thought, **args):
    return ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
         "function": {"name": name, "arguments": json.dumps({"thought": thought, **args}, ensure_ascii=False)}}]})


def results(messages):
    """[(tool name, observation)] for this run of the agent, in order."""
    names = {c["id"]: c["function"]["name"] for m in messages for c in m.get("tool_calls", [])}
    return [(names[m["tool_call_id"]], json.loads(m["content"])) for m in messages if m["role"] == "tool"]


def pick(recipes):
    """Cheapest breakfast every morning; the two cheapest other recipes alternate for lunch and dinner."""
    by_cost = sorted(recipes, key=lambda r: r["cost_per_serving"])
    breakfast = [r["_id"] for r in by_cost if "breakfast" in r["tags"]][:1]
    mains = [r["_id"] for r in by_cost if "breakfast" not in r["tags"]][:2] or [r["_id"] for r in by_cost[:2]]
    return {"breakfast": breakfast or mains[:1], "lunch": mains, "dinner": mains[::-1]}


def facts(messages):
    """The household facts the manager's turn carries (manager.turn)."""
    text = next(m["content"] for m in reversed(messages) if m["role"] == "user")
    return json.loads(re.search(r"\[household facts from the system: (\{.*\})\]", text, re.S).group(1))


def manager(messages):
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    got = dict(results(messages[last_user:]))
    p = facts(messages)
    budget = p["budget"]
    if "meal_planner" not in got:
        diet = ", ".join(p.get("diet", [])) or "хязгааргүй"
        return call("meal_planner", "Цэс, жагсаалтыг хоол төлөвлөгч агентад даалгана.", days=DAYS,
                    task=f"{DAYS} хоногийн цэс: {p['household']} хүн, хоол: {diet}, төсөв {budget}₮.")
    meals = got["meal_planner"]
    if meals.get("error") or meals.get("nothing_to_buy"):
        return call("reply", "Худалдаж авах зүйл алга эсвэл цэс гарсангүй.", text=meals.get("error") or
                    f"Гэрт байгаа хүнс {DAYS} хоногийн цэсэнд хүрэлцэнэ, одоогоор юу ч авах шаардлагагүй.")
    if "procurement" not in got:
        return call("procurement", "Жагсаалтыг худалдан авалтын агентад өгч, дэлгүүрүүдтэй тохиролцуулна.",
                    task=f"Жагсаалтыг хамгийн хямдаар ав, төсөв {budget}₮.")
    deal = got["procurement"]
    for r in (deal, got.get("propose_plan", {})):
        if r.get("error"):
            return call("reply", "Төлөвлөгөө гаргах боломжгүй, шалтгааныг хэлнэ.", text=r["error"])
    text = (f"{DAYS} хоногийн төлөвлөгөө: {', '.join(meals['recipes'])}. "
            f"{', '.join(STORE_NAMES.get(s, s) for s in deal['stores'])} дэлгүүрээс нийт {deal['total']:,}₮ / {budget:,}₮.")
    if deal.get("saved_by_negotiation"):
        text += f" Дэлгүүрийн агентууд үнээ буулгаж {deal['saved_by_negotiation']:,}₮ хэмнэв."
    if meals.get("from_pantry"):
        text += f" Гэрт байгаа {len(meals['from_pantry'])} төрлийн барааг хасав."
    if deal.get("saved_vs_priciest_store"):
        text += f" Хамгийн үнэтэй дэлгүүрээс {deal['saved_vs_priciest_store']:,}₮ хямд."
    if deal.get("note"):
        text += f" {deal['note']}"
    return call("propose_plan", "Сагс төсөвт багтсан, бүх бараа олдсон: төлөвлөгөөг санал болгоно.", summary=text)


def meal_planner(messages):
    got = dict(results(messages))
    if "search_recipes" not in got:
        return call("search_recipes", "Хямд өглөө, өдөр, оройн хоолны жор хайна.",
                    query="хямд өглөөний цай, өдрийн болон оройн хоол", limit=20)
    return call("plan_meals", "Хамгийн хямд жорууд давтагдана; гэрт байгааг хасч жагсаалт гаргана.",
                **pick(got["search_recipes"]["recipes"]))


def procurement(messages):
    done = results(messages)
    quotes = [r for n, r in done if n == "quote_stores"]
    if not quotes:
        return call("quote_stores", "Бүх дэлгүүрээс жагсаалтын үнийг асууна.")
    dropped = quotes[0].get("missing", []) if quotes else []
    if dropped and len(quotes) == 1:  # no store had these: quote again without them
        return call("quote_stores", f"{', '.join(dropped)} аль ч дэлгүүрт алга, хасаад дахин асууна.", drop=dropped)
    if quotes[-1].get("error"):
        return call("finish", "Үнэ авч чадсангүй.", note=quotes[-1]["error"])
    if not any(n == "negotiate" for n, _ in done[-1:]):
        return call("negotiate", "Дэлгүүр бүрт өрсөлдөгчийн үнийг үзүүлж хямдрал хүснэ.")
    note = f"Дууссан тул хасав: {', '.join(dropped)}." if dropped else ""
    return call("finish", "Хэлэлцээр дууслаа, сагсыг менежерт буцаана.", note=note)


def advisor(messages):
    got = dict(results(messages))
    if "spending_stats" not in got:
        return call("spending_stats", "Зарцуулалтын статистикийг харна.")
    return call("finish", "Тоонууд бэлэн.", answer=json.dumps(got["spending_stats"], ensure_ascii=False)[:500])


SCRIPTS = {"manager": manager, "meal_planner": meal_planner, "procurement": procurement, "advisor": advisor}


async def chat(role, messages, tools, tool_choice=None):
    return SCRIPTS[role](messages), MODEL, None
