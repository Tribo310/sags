import os

from openai import AsyncOpenAI

_client = AsyncOpenAI(base_url=os.getenv("EMBED_BASE_URL", "http://localhost:11434/v1"), api_key="ollama")
MODEL = os.getenv("EMBED_MODEL", "bge-m3")
DIMS = int(os.getenv("EMBED_DIMS", "1024"))


async def embed(texts):
    r = await _client.embeddings.create(model=MODEL, input=texts)
    return [d.embedding for d in r.data]
