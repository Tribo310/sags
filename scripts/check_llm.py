import asyncio
import os

from openai import AsyncOpenAI

TOOL = {"type": "function", "function": {
    "name": "get_price", "description": "Get the price of a grocery item",
    "parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}}}


async def check(name, base, key, model):
    try:
        c = AsyncOpenAI(base_url=base, api_key=key or "none", timeout=60)
        r = await c.chat.completions.create(
            model=model, tools=[TOOL],
            messages=[{"role": "user", "content": "Сүүний үнэ хэд вэ? Use the tool."}])
        m = r.choices[0].message
        print(f"{name}: tool_calls={m.tool_calls} content={m.content!r}")
    except Exception as e:
        print(f"{name}: FAILED {type(e).__name__}: {e}")


async def main():
    if os.getenv("LLM_MODEL"):
        await check("oyullm", os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"), os.environ["LLM_MODEL"])
    await check("fallback", os.environ["FALLBACK_BASE_URL"], "ollama", os.environ["FALLBACK_MODEL"])
    e = await AsyncOpenAI(base_url=os.environ["EMBED_BASE_URL"], api_key="ollama").embeddings.create(
        model=os.environ["EMBED_MODEL"], input=["сүү"])
    print("embedding dims:", len(e.data[0].embedding))


asyncio.run(main())
