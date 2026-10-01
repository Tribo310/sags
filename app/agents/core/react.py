"""ReAct loop shared by every agent: Thought -> Action -> Observation, repeated until the agent calls its final tool.

Every step is a tool call (tool_choice=required) whose required `thought` argument is the ReAct Thought, so the
reasoning is recorded next to the action it led to. Each step lands in the audit log as (actor=role, action=tool,
reason=thought, result=observation) and streams to the UI."""
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.agents.core import llm
from app.common.db import audit

THOUGHT = {"type": "string", "description": "Your reasoning: what you know so far, what you do now and why. 1-2 sentences."}
MAX_STALLS = 3  # replies without a tool call in a row before giving up
KEEP_RECENT = 12  # messages the model always sees in full
OLD_RESULT_CHARS = 300  # older tool results are cut to this much


def compact(messages):
    """What the model sees: recent messages in full, older tool results cut short. The stored conversation keeps
    everything; a long chat with the manager otherwise re-sends every old basket on every step."""
    cut = len(messages) - KEEP_RECENT
    return [{**m, "content": m["content"][:OLD_RESULT_CHARS] + " …[older result trimmed]"}
            if i < cut and m["role"] == "tool" and len(m["content"]) > OLD_RESULT_CHARS else m
            for i, m in enumerate(messages)]


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Awaitable[dict]]  # async fn(ctx, **args) -> observation
    params: dict = field(default_factory=dict)  # JSON-schema properties
    required: list = field(default_factory=list)
    final: bool = False  # a successful call ends the loop; its observation is the agent's answer

    def schema(self):
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": {
            "type": "object", "properties": {"thought": THOUGHT, **self.params}, "required": ["thought", *self.required]}}}


@dataclass
class Agent:
    role: str  # picks the model (llm.ROLES)
    system: str
    tools: list[Tool]
    max_steps: int = 12
    script: Callable | None = None  # DEMO=1 stand-in for llm.chat, same signature
    actor: str = ""  # name in the audit log, default role (the store agents share a role, not a name)

    def __post_init__(self):
        self.actor = self.actor or self.role


def off_enum(tool, args):
    """Top-level enum arguments outside their allowed values -> an error naming the allowed values."""
    for k, spec in tool.params.items():
        if "enum" in spec and k in args and args[k] not in spec["enum"]:
            return f"{k}={args[k]!r} is not allowed; use one of {spec['enum']}"
    return None


async def run(agent, ctx, task=None, messages=None, on_step=None):
    """Run the loop and return the final tool's observation, or {"error": ...} if the agent gave up.
    `messages` continues an earlier conversation and is updated in place; on_step(messages) runs after each step."""
    messages = [] if messages is None else messages
    if not messages:
        messages.append({"role": "system", "content": agent.system})
    if task:
        messages.append({"role": "user", "content": task})
    tools = {t.name: t for t in agent.tools}
    schemas = [t.schema() for t in agent.tools]
    chat = agent.script or llm.chat
    last, stalls, problem = None, 0, None
    for _ in range(agent.max_steps):
        t0 = time.monotonic()
        msg, model, usage = await chat(agent.role, compact(messages), schemas, "required")
        llm_ms = round((time.monotonic() - t0) * 1000)
        if not msg.tool_calls:  # an empty turn, prose or a made-up result: not an action. Not kept, the model copies it
            stalls += 1
            if stalls >= MAX_STALLS:
                break
            continue
        stalls_before, stalls = stalls, 0
        messages.append(msg.model_dump(exclude_none=True))
        answer = None
        for i, call in enumerate(msg.tool_calls):
            # the LLM call's cost goes on its first action; stalls before it count too
            meta = {"llm_ms": llm_ms, "prompt_tokens": (usage or {}).get("prompt"),
                    "completion_tokens": (usage or {}).get("completion"), "stalls": stalls_before} if i == 0 else {}
            name, args, thought = call.function.name, {}, msg.content or ""
            try:
                args = json.loads(call.function.arguments or "{}")
                thought = args.pop("thought", None) or thought
                sig = (name, json.dumps(args, sort_keys=True))
                if name not in tools:
                    result = {"error": f"unknown tool {name}; use one of {', '.join(tools)}"}
                elif bad := off_enum(tools[name], args):  # the server may not enforce enums; the loop does
                    result = {"error": bad}
                elif sig == last:  # small models loop on the call they just made
                    result = {"error": f"you just called {name} with the same arguments; use that result and move on"}
                else:
                    t1 = time.monotonic()
                    result = await tools[name].fn(ctx, **args)
                    meta["tool_ms"] = round((time.monotonic() - t1) * 1000)
                last = sig
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            await audit(ctx["plan_id"], agent.actor, name, args=args, result=result, reason=thought, model=model, meta=meta)
            if isinstance(result, dict) and result.get("error"):
                problem = result["error"]
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False, default=str)})
            if name in tools and tools[name].final and "error" not in result:
                answer = result
        if on_step:
            await on_step(messages)
        if answer is not None:
            return answer
    await audit(ctx["plan_id"], agent.actor, "error", result={"error": f"{agent.actor} gave up"})
    # the last problem it hit: "could not finish" alone made the manager tell the household it was "down"
    return {"error": f"{agent.actor} could not finish the task" + (f"; last problem: {problem[:300]}" if problem else "")}
