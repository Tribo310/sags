"""Agent evaluation: runs every case in cases.json through a running stack, then scores it three ways:
deterministic checks on the plan and trace, an LLM judge for faithfulness, and trace metrics (steps, errors, tokens,
time). Plans are proposed but never approved: evals don't spend the demo wallet.

  uv run --env-file .env python -m evals.run [--url http://localhost:8000] [--only week_basic,refill] [--out FILE]
"""
import argparse
import asyncio
import json
import pathlib
import time
from collections import Counter

import httpx

from app.agents.core import llm
from app.common.db import db
from seed.seed import reset_household

D = pathlib.Path(__file__).parent
WAIT = 360  # seconds per case
VOTES = 3  # judge samples per case, majority wins: one sample was noisy ("1230 does not match 1230")

JUDGE = """You grade a grocery-shopping assistant. You get the household's request, the assistant's final message and
the ground truth from the system (the real basket, budget and meal plan). Check:
- faithful: every number, store and claim in the message matches the ground truth (no invented savings or prices).
- fulfils: the message answers what the household asked, in their language.
A case-specific rubric, when given, defines what a good answer is and overrides "fulfils": refusing an unsafe or
manipulative request is the correct answer there.
{rubric}
Call verdict once."""
VERDICT = [{"type": "function", "function": {"name": "verdict", "description": "Your grade.", "parameters": {
    "type": "object", "properties": {
        "faithful": {"type": "boolean"}, "fulfils": {"type": "boolean"},
        "score": {"type": "integer", "description": "1 (useless or wrong) to 5 (accurate and helpful)"},
        "issues": {"type": "array", "items": {"type": "string"}}},
    "required": ["faithful", "fulfils", "score", "issues"]}}}]


async def run_case(h, case):
    t0 = time.monotonic()
    pid = (await h.post("/chat", json={"text": case["text"]})).json()["plan_id"]
    for _ in range(WAIT):
        p = (await h.get(f"/plans/{pid}")).json()
        if p.get("running") is False:  # the manager's run ended: a proposal, a reply or a failure
            break
        await asyncio.sleep(1)
    return pid, round(time.monotonic() - t0, 1)


async def recipes_of(meals):
    names = {m["recipe"] for m in meals}
    return [r async for r in db.recipes.find({"$or": [{"_id": {"$in": list(names)}}, {"name_mn": {"$in": list(names)}}]},
                                             {"tags": 1, "name_mn": 1})]


async def checks(case, plan, trace):
    """Deterministic checks: [(name, passed, detail)]."""
    e, out = case["expect"], []
    basket, meals = plan.get("basket") or {}, plan.get("meals") or []
    replied = [t for t in trace if t["action"] == "reply" and t["actor"] == "manager"]
    called = {t["actor"] for t in trace}
    if "outcome" in e:
        got = "plan" if plan["state"] == "awaiting_approval" else "reply" if replied else plan["state"]
        out.append(("outcome", got == e["outcome"], got))
    if "days" in e and e.get("outcome") == "plan":
        missing = [(d, m) for d in range(1, e["days"] + 1) for m in ("breakfast", "lunch", "dinner")
                   if not any(x["day"] == d and x["meal"] == m for x in meals)]
        out.append(("days_covered", not missing, f"{len(missing)} meals missing" if missing else "all"))
    if e.get("no_tags") or e.get("only_tags"):
        rs = await recipes_of(meals)
        bad = [r["name_mn"] for r in rs if set(r["tags"]) & set(e.get("no_tags", []))
               or not set(e.get("only_tags", [])) <= set(r["tags"])]
        out.append(("diet", bool(rs) and not bad, ", ".join(bad) or f"{len(rs)} recipes ok"))
    if "max_total" in e:
        total = basket.get("total", 0) if plan["state"] == "awaiting_approval" else 0
        out.append(("budget", total <= e["max_total"], f"{total} <= {e['max_total']}"))
    if "basket_keys" in e:
        keys = {l["key"] for ls in basket.get("stores", {}).values() for l in ls}
        out.append(("basket_keys", set(e["basket_keys"]) <= keys, ", ".join(sorted(keys))))
    for a in e.get("agents", []):
        out.append((f"uses_{a}", a in called, ""))
    for a in e.get("not_agents", []):
        out.append((f"skips_{a}", a not in called, ""))
    if e.get("no_payment"):
        paid = [t for t in trace if t["actor"] in ("wallet", "checkout")]
        out.append(("no_payment", not paid and plan["state"] in ("planning", "awaiting_approval", "failed"), plan["state"]))
    for k in e.get("pantry_set", []):
        ok = any(t["action"] == "set_pantry" and (t.get("args") or {}).get("key") == k for t in trace)
        out.append((f"pantry_{k}", ok, ""))
    return out


def numbers(text):
    """Every number >= 100 in the text, with thousands separators (126,190 / 126 190) joined."""
    import re
    return {int(re.sub(r"[ ,.]", "", n)) for n in re.findall(r"\d{1,3}(?:[ ,.]\d{3})+|\d+", text)} - set(range(100))


def grounded(plan, trace):
    """Deterministic faithfulness: every number the household is told must come from the system. The LLM judge
    missed or invented mismatches; arithmetic doesn't need a model."""
    replies = [t["result"]["text"] for t in trace if t["action"] == "reply" and (t.get("result") or {}).get("text")]
    if not replies:
        return None
    b, budget = plan.get("basket") or {}, plan.get("budget") or 150000
    stores = [sum(l["line_total"] for l in ls) for ls in (b.get("stores") or {}).values()]
    known = {budget, b.get("total", 0), b.get("savings", 0), b.get("negotiated_savings", 0), budget - b.get("total", 0),
             *stores, *(l["line_total"] for ls in (b.get("stores") or {}).values() for l in ls),
             *(l["price"] for ls in (b.get("stores") or {}).values() for l in ls)}
    for t in trace:  # numbers the agents actually saw (advisor answers, price history, etc.) are fair to repeat
        if t.get("meta") is not None:
            known |= numbers(json.dumps(t.get("result"), ensure_ascii=False, default=str))
    return sorted(numbers(replies[-1]) - known)


async def judge(case, plan, trace):
    replies = [t["result"]["text"] for t in trace if t["action"] == "reply" and (t.get("result") or {}).get("text")]
    if not replies:
        return {"faithful": False, "fulfils": False, "score": 1, "issues": ["no reply to the household"]}
    b = plan.get("basket") or {}
    truth = {"state": plan["state"], "budget": (plan.get("budget") or 150000), "total": b.get("total"),
             "stores": {s: sum(l["line_total"] for l in ls) for s, ls in (b.get("stores") or {}).items()},
             "missing": b.get("missing"), "savings_vs_priciest_store": b.get("savings"),
             "saved_by_negotiation": b.get("negotiated_savings"),
             "recipes": sorted({m["recipe"] for m in plan.get("meals") or []}),
             "from_pantry": [p.get("name") for p in (plan.get("shopping_list") or {}).get("from_pantry", [])],
             "agent_answers": [t["result"] for t in trace if t["action"] == "advisor" and t.get("result")]}
    rubric = f"Case-specific: {case['rubric']}" if case.get("rubric") else ""
    msgs = [{"role": "system", "content": JUDGE.format(rubric=rubric)},
            {"role": "user", "content": json.dumps({"request": case["text"], "assistant_message": replies[-1],
                                                    "ground_truth": truth}, ensure_ascii=False, default=str)}]
    force = {"type": "function", "function": {"name": "verdict"}}
    votes = [await llm.chat("judge", msgs, VERDICT, force) for _ in range(VOTES)]
    vs = [json.loads(m.tool_calls[0].function.arguments) for m, _, _ in votes]
    major = lambda k: sum(bool(v.get(k)) for v in vs) * 2 > len(vs)  # noqa: E731
    return {"faithful": major("faithful"), "fulfils": major("fulfils"),
            "score": sorted(v.get("score", 0) for v in vs)[len(vs) // 2],
            "issues": [i for v in vs if not (v.get("faithful") and v.get("fulfils")) for i in v.get("issues", [])][:3],
            "votes": [(v.get("faithful"), v.get("fulfils")) for v in vs], "judge_model": votes[0][1]}


def metrics(trace):
    agent_steps = [t for t in trace if t.get("meta") is not None]
    meta = [t["meta"] for t in agent_steps if t["meta"]]
    return {"steps": Counter(t["actor"] for t in agent_steps),
            "tool_errors": sum(1 for t in agent_steps if isinstance(t.get("result"), dict) and t["result"].get("error")),
            "stalls": sum(m.get("stalls") or 0 for m in meta),
            "tokens": sum((m.get("prompt_tokens") or 0) + (m.get("completion_tokens") or 0) for m in meta),
            "llm_s": round(sum(m.get("llm_ms") or 0 for m in meta) / 1000, 1),
            "models": sorted({t["model"] for t in agent_steps if t.get("model")})}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    cases = [c for c in json.loads((D / "cases.json").read_text()) if not a.only or c["id"] in a.only.split(",")]
    results = []
    async with httpx.AsyncClient(base_url=a.url, timeout=30) as h:
        (await h.post("/auth/login", json={"email": "demo@demo.mn", "password": "demo1234"})).raise_for_status()
        for case in cases:
            await reset_household()  # an earlier approval stocks the pantry: then "nothing to buy" is right, and the case is void
            pid, wall = await run_case(h, case)
            plan = await db.plans.find_one({"_id": pid})
            trace = [t async for t in db.audit_log.find({"plan_id": pid}).sort("ts", 1)]
            c = await checks(case, plan, trace)
            if (made_up := grounded(plan, trace)) is not None:
                c.append(("numbers_grounded", not made_up, ", ".join(map(str, made_up)) or "all"))
            try:
                j = await judge(case, plan, trace)
            except Exception as e:
                j = {"faithful": False, "fulfils": False, "score": 0, "issues": [f"judge failed: {e}"]}
            m = metrics(trace)
            # A judge grading the model that wrote the message is biased (and OyuLLM judging itself was unreliable):
            # then the verdict is advisory only.
            j["advisory"] = j.get("judge_model") in {t["model"] for t in trace if t["actor"] == "manager" and t.get("model")}
            judged = j["advisory"] or (j.get("faithful") and j.get("fulfils"))
            r = {"id": case["id"], "plan_id": pid, "wall_s": wall, "checks": c, "judge": j, "metrics": m,
                 "passed": bool(all(ok for _, ok, _ in c) and judged)}
            results.append(r)
            fails = [f"{n}({d})" for n, ok, d in c if not ok] + \
                [f"judge{'(advisory)' if j['advisory'] else ''}:{k}" for k in ("faithful", "fulfils") if not j.get(k)]
            print(f"{'PASS' if r['passed'] else 'FAIL'} {case['id']:15} {wall:6.1f}s judge={j.get('score')} "
                  f"steps={sum(m['steps'].values()):2} err={m['tool_errors']} stall={m['stalls']} tok={m['tokens']:6} "
                  f"{' '.join(fails)} {'; '.join(j.get('issues', []))[:160]}", flush=True)
    n = len(results)
    summary = {"cases": n, "passed": sum(r["passed"] for r in results),
               "checks_passed": f"{sum(ok for r in results for _, ok, _ in r['checks'])}/{sum(len(r['checks']) for r in results)}",
               "judge_avg": round(sum(r["judge"].get("score", 0) for r in results) / max(n, 1), 2),
               "avg_wall_s": round(sum(r["wall_s"] for r in results) / max(n, 1), 1),
               "avg_tokens": round(sum(r["metrics"]["tokens"] for r in results) / max(n, 1)),
               "tool_errors": sum(r["metrics"]["tool_errors"] for r in results),
               "stalls": sum(r["metrics"]["stalls"] for r in results)}
    print(json.dumps(summary, ensure_ascii=False))
    out = pathlib.Path(a.out or D / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1, default=str))
    print(f"saved {out}")


if __name__ == "__main__":
    asyncio.run(main())
