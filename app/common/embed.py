import os

from openai import AsyncOpenAI

# bge-m3 locally (Ollama) or on the oyu API: the same model gives the same vectors, so stored ones stay valid
_client = AsyncOpenAI(base_url=os.getenv("EMBED_BASE_URL", "http://localhost:11434/v1"),
                      api_key=os.getenv("EMBED_API_KEY") or "ollama")
MODEL = os.getenv("EMBED_MODEL", "bge-m3")
DIMS = int(os.getenv("EMBED_DIMS", "1024"))


_cache = {}  # text -> vector; search queries and product names repeat a lot within a plan
CACHE_SIZE = 2048


async def embed(texts):
    todo = [t for t in dict.fromkeys(texts) if t not in _cache]
    if todo:
        r = await _client.embeddings.create(model=MODEL, input=todo)
        if len(_cache) + len(todo) > CACHE_SIZE:
            _cache.clear()  # ponytail: crude eviction, fine at this size
        _cache.update(zip(todo, (d.embedding for d in r.data)))
    return [_cache[t] for t in texts]
