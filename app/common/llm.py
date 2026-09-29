import os

from openai import AsyncOpenAI

_MODELS = [
    (AsyncOpenAI(base_url=os.getenv("LLM_BASE_URL") or None, api_key=os.getenv("LLM_API_KEY") or "none", timeout=60),
     os.getenv("LLM_MODEL")),
    (AsyncOpenAI(base_url=os.getenv("FALLBACK_BASE_URL", "http://localhost:11434/v1"), api_key="ollama", timeout=180),
     os.getenv("FALLBACK_MODEL", "qwen3:8b")),
]


async def chat(messages, tools):
    """Returns (assistant message, model used). Falls back to the local model on any primary failure."""
    err = RuntimeError("no LLM configured")
    for client, model in _MODELS:
        if not model:
            continue
        try:
            r = await client.chat.completions.create(model=model, messages=messages, tools=tools)
            return r.choices[0].message, model
        except Exception as e:
            err = e
    raise err
