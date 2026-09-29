"""MCP client: the agents' tools that live in grocery-mcp (app/mcp_server.py)."""
import json
import os

from mcp import Client

from app.agents.core.react import Tool

MCP_URL = os.getenv("MCP_URL", "http://localhost:8100/mcp")


def connect():
    return Client(MCP_URL)


async def call_json(mcp, name, args):
    """Call an MCP tool and decode its JSON result; tool errors become {"ok": False, "error": ...}."""
    r = await mcp.call_tool(name, args)
    text = r.content[0].text if r.content else "{}"
    return {"ok": False, "error": text} if r.is_error else json.loads(text)


def inline_refs(schema, defs=None):
    """Replace $ref/$defs with the referenced schema: local models' tool parsers often ignore $ref."""
    defs = schema.get("$defs", {}) if defs is None else defs
    if isinstance(schema, dict):
        if "$ref" in schema:
            return inline_refs(defs[schema["$ref"].split("/")[-1]], defs)
        return {k: inline_refs(v, defs) for k, v in schema.items() if k != "$defs"}
    if isinstance(schema, list):
        return [inline_refs(v, defs) for v in schema]
    return schema


async def mcp_tools(mcp, names, view=None):
    """The named MCP tools as ReAct tools. user_id is always the session's user, never one the LLM was talked into,
    so it isn't shown to the model at all. view(result) trims what the model sees (errors pass through untouched)."""
    def make(name, pin):
        async def fn(ctx, **args):
            r = await call_json(ctx["mcp"], name, {**args, **({"user_id": ctx["user"]["_id"]} if pin else {})})
            return view(r) if view and "error" not in r else r
        return fn

    out = []
    for t in (await mcp.list_tools()).tools:
        if t.name not in names:
            continue
        schema = inline_refs(t.input_schema)
        props = dict(schema.get("properties", {}))
        pin = props.pop("user_id", None) is not None
        out.append(Tool(t.name, t.description or "", make(t.name, pin), props,
                        [r for r in schema.get("required", []) if r != "user_id"]))
    return out
