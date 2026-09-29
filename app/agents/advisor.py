"""Advisor sub-agent: answers questions about the household's spending, price trends and pantry."""
from app.agents import demo
from app.agents.core.react import Agent, Tool, run
from app.agents.protocols.mcp import mcp_tools

SYSTEM = """You are the advisor agent of a household grocery assistant in Ulaanbaatar. The manager agent asks you a
question about past spending, price trends, whether now is a good time to buy something, or what is running low at
home. Look it up with your tools (search_products gives a product's key for price_history), then finish with a short,
concrete answer backed by the numbers you found. Don't guess numbers."""


async def finish(ctx, answer):
    return {"answer": answer}


async def agent(mcp):
    return Agent("advisor", SYSTEM, max_steps=8, script=demo.chat if demo.ON else None, tools=[
        *await mcp_tools(mcp, {"spending_stats", "price_history", "search_products", "get_pantry"}),
        Tool("finish", "Done: give the manager your answer.", finish, {"answer": {"type": "string"}}, ["answer"],
             final=True),
    ])


async def delegate(ctx, question):
    return await run(await agent(ctx["mcp"]), ctx, question)
