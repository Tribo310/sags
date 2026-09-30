import asyncio
import json

from app.agents import demo

RECIPES = [
    {"_id": "eggs", "name_mn": "Өндөг", "tags": ["breakfast"], "cost_per_serving": 1000},
    {"_id": "soup", "name_mn": "Шөл", "tags": ["lunch"], "cost_per_serving": 1500},
    {"_id": "rice", "name_mn": "Будаа", "tags": ["lunch"], "cost_per_serving": 1800},
    {"_id": "steak", "name_mn": "Стейк", "tags": ["dinner"], "cost_per_serving": 9000},
]
FACTS = {"manager": 'task\n\n[household facts from the system: {"household": 2, "diet": ["no_pork"], "budget": 150000}]',
         "meal_planner": "task\n\nFacts (from the system, authoritative):\n- days: 7, household: 2 people, diet: no_pork\n"}


def run(role, results):
    """Feed one agent's script tool results in order; return the (tool, args) it called, thoughts stripped."""
    msgs = [{"role": "system", "content": "..."}, {"role": "user", "content": FACTS.get(role, "task")}]
    calls = []
    for result in results + [None]:
        msg, _, _ = asyncio.run(demo.chat(role, msgs, []))
        c = msg.tool_calls[0]
        args = json.loads(c.function.arguments)
        assert args.pop("thought")  # every ReAct step carries its reasoning
        calls.append((c.function.name, args))
        if result is None:
            return calls
        msgs += [msg.model_dump(exclude_none=True),
                 {"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)}]


def test_manager_orchestrates_planner_then_procurement():
    meals = {"recipes": ["Шөл", "Өндөг"], "from_pantry": ["давс"], "nothing_to_buy": False}
    deal = {"total": 90000, "stores": {"emart": 90000}, "saved_vs_priciest_store": 5000, "saved_by_negotiation": 2000, "missing": []}
    calls = run("manager", [meals, deal])
    assert [n for n, _ in calls] == ["meal_planner", "procurement", "propose_plan"]
    assert "2 хүн" in calls[0][1]["task"] and "no_pork" in calls[0][1]["task"]
    summary = calls[2][1]["summary"]
    assert "90,000₮" in summary and "2,000₮" in summary and "5,000₮" in summary and "Имарт" in summary


def test_manager_replies_when_pantry_covers_everything():
    calls = run("manager", [{"nothing_to_buy": True, "recipes": []}])
    assert calls[-1][0] == "reply" and "шаардлагагүй" in calls[-1][1]["text"]


def test_manager_replies_with_procurement_error():
    calls = run("manager", [{"recipes": ["Шөл"]}, {"error": "no store agent answered"}])
    assert calls[-1] == ("reply", {"text": "no store agent answered"})


def test_meal_planner_rotates_the_cheapest_recipes():
    calls = run("meal_planner", [{"recipes": RECIPES}])
    assert [n for n, _ in calls] == ["search_recipes", "plan_meals"]
    assert calls[1][1] == {"breakfast": ["eggs"], "lunch": ["soup", "rice"], "dinner": ["rice", "soup"]}  # no steak


def test_procurement_requotes_without_sold_out_items_then_negotiates():
    sold_out = {"missing": ["rice"], "total": 0}
    ok = {"missing": [], "total": 9000}
    calls = run("procurement", [sold_out, ok, {"total": 9000}])
    assert [n for n, _ in calls] == ["quote_stores", "quote_stores", "negotiate", "finish"]
    assert calls[1][1]["drop"] == ["rice"]
    assert "rice" in calls[3][1]["note"]


def test_rotation_covers_every_day_and_meal():
    from app.agents.meal_planner import expand
    week = expand(7, {"breakfast": ["a"], "lunch": ["b", "c"], "dinner": ["c", "b"]})
    assert len(week) == 21 and {(m["day"], m["meal"]) for m in week} == {(d, m) for d in range(1, 8)
                                                                        for m in ("breakfast", "lunch", "dinner")}
    assert [m["recipe"] for m in week if m["meal"] == "lunch"][:3] == ["b", "c", "b"]


def test_diet_rules():
    from app.agents.meal_planner import diet_violation
    assert diet_violation({"vegetarian"}, ["chicken", "lunch"]) == "vegetarian"
    assert diet_violation({"no_pork"}, ["pork"]) == "no_pork"
    assert diet_violation({"no_pork", "vegetarian"}, ["vegetarian", "soup"]) is None


def test_summary_must_state_the_real_total():
    from app.agents.manager import states_total
    assert states_total("Нийт 122,870₮", 122870) and states_total("нийт 122 870 төгрөг", 122870)
    assert not states_total("Meals for the week, total cost vs budget", 122870)


def test_language_guard():
    from app.agents.manager import cyrillic, wrong_language
    assert cyrillic("овъёосны будаа хаанаас авах билээ") and not cyrillic("3-day plan: Буурцагтай шөл. Total 33,810")
    assert wrong_language({"mongolian": True}, "3-day plan: Буурцагтай шөл, Ногооны шөл. Total 33,810 ₮, buy from Nomin")
    assert wrong_language({"mongolian": True}, "Овъёосны будааг Номиноос 4,390₮-өөр авна.") is None
    assert wrong_language({"mongolian": True}, "Sansar-аас 92,170 төгрөг, Emart-аас 48,380 төгрөг авна.") is None
    assert wrong_language({"mongolian": False}, "3-day plan") is None


def test_question_detection():
    from app.agents.manager import QUESTION
    for q in ("овъёосны будаа хаанаас авах билээ", "Нийт хэд вэ?", "Номиноос авч болох уу", "where is it?"):
        assert QUESTION.search(q), q
    for c in ("Номиноос авъя", "7 хоногийн хүнс, 2 хүн", "Цагаан хоол болгоод дахин гарга"):
        assert not QUESTION.search(c), c


def test_english_answer_to_mongolian_household_is_translated(monkeypatch):
    import asyncio
    from app.agents import manager
    from app.agents.core import llm

    async def orchu(text, source="en", target="mn"):
        return "Нийт 33810 төгрөг, Номин, Эмартаас авна."
    monkeypatch.setattr(llm, "translate", orchu)
    ctx = {"mongolian": True}
    assert asyncio.run(manager.in_their_language(ctx, "Total 33,810 MNT, buy from Nomin and Emart.")) == \
        ("Нийт 33810 төгрөг, Номин, Эмартаас авна.", None)

    async def down(text, source="en", target="mn"):
        return None
    monkeypatch.setattr(llm, "translate", down)
    text, err = asyncio.run(manager.in_their_language(ctx, "Total 33,810 MNT"))
    assert err and "Mongolian" in err["error"]  # Orchu down: the model is asked again
