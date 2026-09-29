import asyncio
import json

from app.planner import script

RECIPES = [
    {"_id": "eggs", "name_mn": "Өндөг", "tags": ["breakfast"], "cost_per_serving": 1000},
    {"_id": "soup", "name_mn": "Шөл", "tags": ["lunch"], "cost_per_serving": 1500},
    {"_id": "rice", "name_mn": "Будаа", "tags": ["lunch"], "cost_per_serving": 1800},
    {"_id": "steak", "name_mn": "Стейк", "tags": ["dinner"], "cost_per_serving": 9000},
]


def run(results):
    """Feed the script tool results in order; return the tool names it called and the last message."""
    msgs = [{"role": "system", "content": "...\nuser_id: demo"}, {"role": "user", "content": "7 хоног"}]
    calls = []
    for result in results + [None]:
        msg, _ = asyncio.run(script.chat(msgs, []))
        if not msg.tool_calls:
            return calls, msg
        c = msg.tool_calls[0]
        calls.append((c.function.name, json.loads(c.function.arguments)))
        if result is None:
            return calls, msg
        msgs += [msg.model_dump(exclude_none=True),
                 {"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)}]


def test_walks_plan_and_requotes_without_sold_out_items():
    listed = {"items": [{"key": "egg", "qty": 2}, {"key": "rice", "qty": 1}], "from_pantry": [{"key": "salt"}]}
    sold_out = {"basket": {"stores": {}, "missing": ["rice"], "total": 0}, "budget": 150000}
    ok = {"basket": {"stores": {"emart": []}, "missing": [], "total": 90000, "savings": 5000}, "budget": 150000}
    calls, _ = run([{"household": 2, "diet": ["no_pork"]}, {"recipes": RECIPES}, listed, sold_out, ok])
    assert [n for n, _ in calls] == ["get_profile", "search_recipes", "build_list", "quote_stores", "quote_stores", "propose_plan"]
    assert calls[1][1]["exclude_tags"] == ["pork"]
    servings = {m["recipe_id"]: m["servings"] for m in calls[2][1]["meals"]}
    assert servings == {"eggs": 14, "soup": 14, "rice": 14}  # 7 days x 2 people; steak is too expensive
    assert calls[4][1]["items"] == [{"key": "egg", "qty": 2}]
    summary = calls[5][1]["summary"]
    assert "90,000₮" in summary and "rice" in summary and "5,000₮" in summary
    assert len(calls[5][1]["meals"]) == 21


def test_stops_with_message_on_error():
    calls, msg = run([{"household": 2}, {"recipes": RECIPES}, {"items": []}, {"error": "no store agent answered"}])
    assert msg.content == "no store agent answered"
