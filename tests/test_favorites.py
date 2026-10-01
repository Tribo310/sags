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
