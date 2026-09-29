import json
import os
import re
import uuid

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

_MODELS = [
    (AsyncOpenAI(base_url=os.getenv("LLM_BASE_URL") or None, api_key=os.getenv("LLM_API_KEY") or "none", timeout=60),
     os.getenv("LLM_MODEL")),
    (AsyncOpenAI(base_url=os.getenv("FALLBACK_BASE_URL", "http://localhost:11434/v1"), api_key="ollama", timeout=180),
     os.getenv("FALLBACK_MODEL", "qwen3:8b")),
]


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


async def chat(messages, tools):
    """Returns (assistant message, model used). Falls back to the local model on any primary failure."""
    err = RuntimeError("no LLM configured")
    for client, model in _MODELS:
        if not model:
            continue
        try:
            r = await client.chat.completions.create(model=model, messages=messages, tools=tools)
        except Exception as e:
            print(f"LLM {model} failed, trying next: {e!r}")
            err = e
            continue
        msg = r.choices[0].message
        if not msg.tool_calls and (calls := text_tool_calls(msg.content, {t["function"]["name"] for t in tools})):
            msg = ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": calls})
        return msg, model
    raise err
