from app.common.favorites import food_tag


def test_a_food_you_wont_eat_becomes_a_diet_rule():
    assert food_tag("загас") == "fish" and food_tag("Загасан хоол") == "fish" and food_tag("гахайн мах") == "pork"
    assert food_tag("цуйван") is None  # a dish, matched to a recipe instead
    assert food_tag("тахиа загас") is None  # two foods: ambiguous, the model saves them one by one


def test_dish_names_are_found_in_phrases():
    from app.common.favorites import DISLIKE, LIKE, stems
    assert "цуйван" in stems(LIKE.search("Бид цуйванд их дуртай").group(1))
    assert "хуушуур" in stems(LIKE.search("хуушуурт маш дуртай шүү").group(1))
    assert "загас" in stems(DISLIKE.search("Бид загас иддэггүй").group(1))
    assert "гахайн мах" in stems(DISLIKE.search("гахайн мах огт идэхгүй").group(1))


def test_plan_cache_key_is_exact(monkeypatch):
    import asyncio
    from app.agents import meal_planner

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def __aiter__(self):
            async def gen():
                for r in self.rows:
                    yield r
            return gen()

    class Coll:
        def __init__(self, rows): self.rows = rows
        def find(self, *a, **k): return Cursor(self.rows)
        async def find_one(self, *a, **k): return self.rows[0] if self.rows else None

    class DB:
        pantry = Coll([{"key": "eggs", "amount": 6}])
        scout_runs = Coll([{"ts": "2026-10-01T03:00"}])
    monkeypatch.setattr(meal_planner, "db", DB())

    def key(text, budget=60000, days=3):
        ctx = {"text": text, "days": days, "user": {"_id": "u", "household": 2}, "diet": {"no_pork"}, "budget": budget,
               "liked": set(), "disliked": set()}
        return asyncio.run(meal_planner.cache_key(ctx))
    assert key("3 хоногийн хоол, 60 мянга") == key("  3 хоногийн  ХООЛ, 60 мянга ")  # same words, other spacing/case
    assert key("3 хоногийн хоол, 60 мянга") != key("3 хоногийн хоол, 6 мянга")  # 0.94 for embeddings
    assert key("3 хоногийн хоол, 60 мянга") != key("3 хоногийн хоол, 60 мянга", budget=6000)
