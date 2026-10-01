"""Which LLM each agent thinks with. The manager always uses OyuLLM; every other agent gets the provider that suits its
job, falling back to OyuLLM when that provider isn't configured or fails."""
import json
import os
import re
import uuid

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

PROVIDERS = {  # name -> (base_url, api_key, model); a provider without a model or key is not configured
    "oyullm": (os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")),
    "openai": (os.getenv("OPENAI_BASE_URL"), os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
    "ollama": (os.getenv("FALLBACK_BASE_URL", "http://localhost:11434/v1"), "ollama", os.getenv("FALLBACK_MODEL")),
}
# Why each agent uses what it uses:
#   manager       OyuLLM, required: it talks to the household in Mongolian and owns the plan.
#   meal_planner  OyuLLM: Mongolian dishes, recipe names and household habits.
#   procurement   OpenAI: arithmetic over prices and long, exact item lists in tool arguments.
#   advisor       OpenAI: reads spending statistics and price series.
#   store         OpenAI: pricing within hard limits against competitor prices.
#   judge         OpenAI: evals/ grades the agents with a different model than the ones being graded.
#   scout         OpenAI: matching messy store listings (Mongolian, Russian, brands, pack sizes) to catalog items.
#   clarifier     OyuLLM: is a Mongolian message clear enough to act on, and if not, the question to ask.
ROLES = {"manager": "oyullm", "meal_planner": "oyullm", "procurement": "openai", "advisor": "openai", "store": "openai",
         "judge": "openai", "scout": "openai", "clarifier": "oyullm"}
LOCKED = {"manager"}  # can't be moved off OyuLLM with MODEL_<ROLE>

_clients = {}


def _client(name):
    if name not in _clients:
        base, key, _ = PROVIDERS[name]
        _clients[name] = AsyncOpenAI(base_url=base or None, api_key=key or "none",
                                     timeout=180 if name == "ollama" else 60)
    return _clients[name]


def configured(name):
    _, key, model = PROVIDERS.get(name, (None, None, None))
    return bool(key and model)


def chain(role):
    """Providers to try for a role, best first: its own (MODEL_<ROLE> overrides the default), then OyuLLM, then local."""
    locked = role in LOCKED and os.getenv("UNLOCK_MANAGER") != "1"  # unlock only to compare models in evals
    own = ROLES.get(role, "oyullm") if locked else os.getenv(f"MODEL_{role.upper()}", ROLES.get(role, "oyullm"))
    return [p for p in dict.fromkeys([own, "oyullm", "ollama"]) if configured(p)]


def _value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v.strip("'\"")


def text_tool_calls(content, names):
    """Gemma sometimes writes calls as text, `[set_pantry(key=будаа, amount=2)]`, with unquoted strings the server's
    tool parser rejects. Turn that back into tool_calls. Returns None unless the whole reply is known calls."""
    m = re.fullmatch(r"\s*\[(.*)\]\s*", content or "", re.S)
    calls = re.findall(r"(\w+)\((.*?)\)(?:\s*,\s*|$)", m.group(1), re.S) if m else []
    if not calls or any(n not in names for n, _ in calls):
        return None
    # ponytail: values can't contain ", x=" or ")"; fine for keys/ids/numbers the tools take
    return [{"id": "call_" + uuid.uuid4().hex[:12], "type": "function", "function": {"name": n, "arguments": json.dumps(
        {k.strip(): _value(v.strip()) for k, v in (p.split("=", 1) for p in re.split(r",\s*(?=\w+\s*=)", a) if "=" in p)},
        ensure_ascii=False)}} for n, a in calls]


async def chat(role, messages, tools, tool_choice=None):
    """Returns (assistant message, model used, token usage) from the first provider in the role's chain that answers.
    tool_choice: "required" or {"type": "function", "function": {"name": ...}} to force a tool call."""
    err = RuntimeError(f"no LLM configured for {role}")
    for name in chain(role):
        model = PROVIDERS[name][2]
        try:
            r = await _client(name).chat.completions.create(model=model, messages=messages, tools=tools,
                                                            **({"tool_choice": tool_choice} if tool_choice else {}))
        except Exception as e:
            print(f"LLM {name}/{model} failed for {role}, trying next: {e!r}")
            err = e
            continue
        msg = r.choices[0].message
        if not msg.tool_calls and (calls := text_tool_calls(msg.content, {t["function"]["name"] for t in tools})):
            msg = ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": calls})
        usage = {"prompt": r.usage.prompt_tokens, "completion": r.usage.completion_tokens} if r.usage else None
        return msg, model, usage
    raise err


async def translate(text, source="en", target="mn"):
    """Orchu, the oyu translation model: {text, source, target} -> {"translation"}. None when it isn't available.
    ₮ is dropped by the model, so money goes through as "MNT" (it comes back as "төгрөг")."""
    base, key, _ = PROVIDERS["oyullm"]
    if not (base and key):
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as h:
            r = await h.post(f"{base.rstrip('/')}/translations", headers={"Authorization": f"Bearer {key}"},
                             json={"text": text.replace("₮", " MNT"), "source": source, "target": target})
        r.raise_for_status()
        return r.json().get("translation") or None
    except Exception as e:
        print(f"translation failed: {e!r}")
        return None
