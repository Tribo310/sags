"""Dishes a household said it likes or won't eat, matched to our recipes by meaning, and foods it won't eat at all
(a diet rule). Shared by the manager (which saves them from the chat) and the meal planner (which must honour them)."""
import re

from app.common.db import db, now
from app.common.embed import embed

# A dish name matches a recipe when the vector search is clear about it: scores run 0.73-0.83 for right answers
# ("цуйван" -> Цуйван 0.768) and a wrong one scores about the same as the runner-up ("пицца": 0.742 vs 0.739).
MIN_SCORE, MIN_MARGIN = 0.72, 0.04
# "загас иддэггүй" is about an ingredient, not a dish: it becomes a diet rule (recipe tags), like the profile's no_pork
FOODS = {"загас": "fish", "гахай": "pork", "үхр": "beef", "үхэр": "beef", "хонь": "mutton", "тахиа": "chicken"}


def food_tag(dish):
    """The recipe tag a food word stands for ("загас" -> fish), when the dish is just that food."""
    words = dish.lower().split()
    tags = {tag for w in words for stem, tag in FOODS.items() if w.startswith(stem)}
    return tags.pop() if len(tags) == 1 and len(words) <= 2 else None


async def match_recipe(dish):
    """The recipe a dish name means, or None."""
    exact = await db.recipes.find_one({"name_mn": {"$regex": f"^{re.escape(dish.strip())}$", "$options": "i"}},
                                      {"name_mn": 1})
    if exact:
        return exact
    vec = (await embed([dish]))[0]
    hits = await (await db.recipes.aggregate([
        {"$vectorSearch": {"index": "recipes_vec", "path": "embedding", "queryVector": vec, "numCandidates": 40,
                           "limit": 2}},
        {"$project": {"name_mn": 1, "score": {"$meta": "vectorSearchScore"}}}])).to_list()
    if hits and hits[0]["score"] >= MIN_SCORE and (len(hits) < 2 or hits[0]["score"] - hits[1]["score"] >= MIN_MARGIN):
        return hits[0]
    return None


async def save(user_id, dish, liked):
    tag = None if liked else food_tag(dish)
    r = None if tag else await match_recipe(dish)
    key = {"user_id": user_id, **({"tag": tag} if tag else {"recipe_id": r["_id"]} if r else {"dish": dish.strip().lower()})}
    await db.favorites.update_one(key, {"$set": {"dish": dish.strip(), "liked": bool(liked), "ts": now(), "tag": tag,
                                                 "recipe_id": r and r["_id"], "recipe": r and r["name_mn"]}},
                                  upsert=True)
    if tag:
        return {"dish": dish, "liked": False, "diet_rule": f"no_{tag}", "note": "every recipe with it is excluded"}
    return {"dish": dish, "liked": bool(liked), "recipe": r and r["name_mn"],
            **({} if r else {"note": "not one of our recipes: remembered, but plans can't include it yet"})}


async def of(user_id):
    """(liked recipe ids, disliked recipe ids, diet rules, [(dish, liked)] for display)."""
    rows = [f async for f in db.favorites.find({"user_id": user_id}).sort("ts", -1)]
    liked = {f["recipe_id"] for f in rows if f.get("recipe_id") and f["liked"]}
    disliked = {f["recipe_id"] for f in rows if f.get("recipe_id") and not f["liked"]}
    rules = {f"no_{f['tag']}" for f in rows if f.get("tag")}
    return liked, disliked, rules, [(f.get("recipe") or f["dish"], f["liked"]) for f in rows]


LIKE = re.compile(r"((?:[^\s,.!?]+\s+){0,2}[^\s,.!?]+)\s+дуртай", re.I)
DISLIKE = re.compile(r"((?:[^\s,.!?]+\s+){0,2}[^\s,.!?]+)\s+(?:иддэггүй|идэхгүй|идэдгүй|дургүй)", re.I)
DATIVE = ("анд", "энд", "онд", "өнд", "нд", "д", "т")  # "цуйванд", "хуушуурт" -> the dish


FILLER = {"их", "маш", "үнэхээр", "бас", "ч", "бид", "би", "манайх", "манай", "огт"}


def stems(phrase):
    """Candidate dish names in a phrase, likeliest first: its last word, then the last two, each without a dative
    ending (longest bare form first: "цуйванд" -> "цуйван" before "цуйва"), fillers ("маш", "бид") dropped."""
    words = [w for w in phrase.split() if w.lower() not in FILLER]
    out = []
    for n in (1, 2):
        if len(words) >= n:
            w = " ".join(words[-n:])
            bare = [w[: -len(d)] for d in DATIVE if w.lower().endswith(d) and len(w) - len(d) >= 3]
            out += sorted(bare, key=len, reverse=True) + [w]
    return out


async def from_message(user_id, text):
    """Likes and won't-eats said in a household message, saved without waiting for the model to notice (it missed
    "бид загас иддэггүй"). Only what matches a recipe or a food is kept; the model saves other phrasings itself."""
    saved = []
    for pattern, liked in ((LIKE, True), (DISLIKE, False)):
        for m in pattern.finditer(text):
            cands = stems(m.group(1))
            # a food anywhere in the phrase wins over a recipe: "гахайн мах идэхгүй" is pork, not a dish called "мах"
            food = None if liked else next((c for c in reversed(cands) if food_tag(c)), None)
            for cand in [food] if food else cands:
                if food or await match_recipe(cand):
                    saved.append(await save(user_id, cand, liked))
                    break
    return saved
