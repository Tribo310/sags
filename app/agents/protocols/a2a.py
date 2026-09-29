"""A2A client: discovers the store agents from their agent cards and sends them tasks."""
import asyncio
import os

import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest

URLS = [u for u in os.getenv("STORE_AGENT_URLS", "http://localhost:9101,http://localhost:9102,http://localhost:9103").split(",") if u]
AGENTS = {}  # store id (agent card name) -> A2A client
SKILLS = {}  # store id -> skill ids its agent card advertises
TIMEOUT = 20  # seconds; a reserve transaction can take a while
NEGOTIATE_TIMEOUT = 60  # the store's own LLM takes a few steps


def _ints(x):
    """A2A data parts are protobuf Structs: every number arrives as float."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, dict):
        return {k: _ints(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_ints(v) for v in x]
    return x


async def discover():
    """Read each agent card; keep the agents that advertise a quote_basket skill."""
    async with httpx.AsyncClient(timeout=5) as h:
        for url in URLS:
            try:
                card = await A2ACardResolver(h, url).get_agent_card()
            except Exception as e:
                print(f"A2A discovery failed for {url}: {e}")
                continue
            if any(s.id == "quote_basket" for s in card.skills):
                AGENTS[card.name] = await create_client(card, ClientConfig(httpx_client=httpx.AsyncClient(timeout=NEGOTIATE_TIMEOUT)))
                SKILLS[card.name] = {s.id for s in card.skills}
    return list(AGENTS)


def can(store, skill):
    return skill in SKILLS.get(store, ())


async def call(store, payload, timeout=TIMEOUT):
    if store not in AGENTS:  # agent started late or restarted: look again
        await discover()
    if store not in AGENTS:
        raise ConnectionError(f"store agent {store} is not reachable")

    async def once():
        msg = new_data_message(payload, role=Role.ROLE_USER)
        async for r in AGENTS[store].send_message(SendMessageRequest(message=msg)):
            return _ints(get_data_parts(r.message.parts)[0])
    return await asyncio.wait_for(once(), timeout)


async def quote_all(items):
    """Ask every store in parallel; a store that errors or times out is skipped."""
    if len(AGENTS) < len(URLS):  # retry agents that were down earlier
        await discover()
    names = list(AGENTS)
    res = await asyncio.gather(*(call(s, {"skill": "quote_basket", "items": items}) for s in names),
                               return_exceptions=True)
    return {s: r for s, r in zip(names, res) if isinstance(r, dict) and r.get("ok")}
