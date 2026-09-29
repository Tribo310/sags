import json
import os

from mcp import Client

from app.planner.checkout import CheckoutError

MCP_URL = os.getenv("MCP_URL", "http://localhost:8100/mcp")


def connect():
    return Client(MCP_URL)


async def call_json(mcp, name, args):
    """Call an MCP tool and decode its JSON result; tool errors become {"ok": False, "error": ...}."""
    r = await mcp.call_tool(name, args)
    text = r.content[0].text if r.content else "{}"
    return {"ok": False, "error": text} if r.is_error else json.loads(text)


class Wallet:
    """Sandbox wallet over MCP. Raises CheckoutError when the server refuses (policy, balance)."""

    def __init__(self, mcp):
        self.mcp = mcp

    async def _call(self, name, **args):
        d = await call_json(self.mcp, name, args)
        if not d.get("ok"):
            raise CheckoutError(f"{name}: {d.get('violations') or d.get('error')}")
        return d

    async def hold(self, plan_id, store, amount):
        return (await self._call("wallet_hold", plan_id=plan_id, store=store, amount=amount))["payment_id"]

    async def capture(self, payment_id):
        await self._call("wallet_capture", payment_id=payment_id)

    async def refund(self, payment_id):
        await self._call("wallet_refund", payment_id=payment_id)
