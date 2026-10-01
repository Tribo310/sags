"""Clarification gate: before the manager plans or buys anything, one focused model call checks that the household's
message says enough to act on. If it doesn't, or can be read several ways, the household gets one short question
instead of a guess.

A separate call rather than a line in the manager's prompt: told to ask when unclear, the manager still turned
"мах авмаар байна" into a three-day plan with beef and mutton, "будаа хэрэгтэй" into 10 kg of rice and
"тэрийг нь авъя", with nothing before it, into a made-up menu (evals/)."""
import json

from app.agents.core import llm

SYSTEM = """You check whether a household's message to a grocery assistant in Ulaanbaatar says enough to act on.
Call verdict once. kind: food_request = asks to plan meals or buy food; anything else is never unclear.
It is clear when the kind of help is known and its essentials are in the message, the conversation or the facts:
- Food or meals for some time: the number of days ("маргааш"/"өнөөдөр" = 1, "долоо хоног" = 7, "3 хоног" = 3).
  Household size, diet and budget are in the facts: never ask them. Guests mentioned without a number: ask how many.
- Specific products: which product and how much (a number, a pack, a size). facts.sold lists what the stores carry:
  a word that fits one of them is that product ("сүү" = Сүү, "талх" = Талх), a word that fits several is ambiguous
  ("мах" -> Үхрийн мах, Хонины мах, Тахианы мах...?). Options for which product are names from facts.sold.
- Always clear: a question (prices, spending, stores, the plan, what to cook from home), a change to the current
  plan, a like or dislike, an answer to the assistant's last question, a greeting, anything off-topic.
- "тэр", "үүнийг", "тэрийг нь" point into the conversation: unclear only if nothing there says what they mean.
When it is unclear or can be read several ways: one short question in the household's language. Give 2-4 options when
the possible answers are few (e.g. ["1 өдөр", "3 өдөр", "7 хоног"]), none when the answer is open. Never ask what the
message, the conversation or the facts already say."""

# Only a request to plan or buy food can be unclear: told so in the prompt, OyuLLM still answered "Монголын
# ерөнхийлөгч хэн бэ?" with "Та хүнс авах тухай асуудалтай юу?". The kind is what the code checks.
KINDS = ["food_request", "question", "plan_change", "preference", "answer", "greeting", "off_topic"]
VERDICT = [{"type": "function", "function": {"name": "verdict", "description": "Is the message clear enough to act on?",
            "parameters": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": KINDS, "description": "what the message is"},
                "clear": {"type": "boolean"},
                "missing": {"type": "string", "description": "what is missing or ambiguous (empty when clear)"},
                "question": {"type": "string", "description": "the one question to ask (empty when clear)"},
                "options": {"type": "array", "items": {"type": "string"}, "description": "0 or 2-4 short answers"}},
                "required": ["kind", "clear", "missing", "question", "options"]}}}]


async def check(text, facts, conversation):
    """None when the message is clear; else {"question", "options", "missing", "model"}. Never blocks on a failure:
    a broken gate must not stop the assistant."""
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"message": text, "facts": facts, "conversation": conversation},
                                                   ensure_ascii=False, default=str)}]
    try:
        msg, model, _ = await llm.chat("clarifier", msgs, VERDICT, {"type": "function", "function": {"name": "verdict"}})
        v = json.loads(msg.tool_calls[0].function.arguments)
    except Exception as e:
        print(f"clarification gate skipped: {e!r}")
        return None
    question = (v.get("question") or "").strip()
    if v.get("kind") != "food_request" or v.get("clear", True) or not question:
        return None
    options = [o.strip() for o in v.get("options") or [] if o and o.strip()]
    return {"question": question, "options": options if 2 <= len(options) <= 4 else [],
            "missing": v.get("missing", ""), "model": model}
