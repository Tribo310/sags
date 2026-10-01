"""uv run --env-file .env python -m app.agents.scout [--items milk,eggs] [--no-crawl] [--dry-run]

Crawls Nomin's grocery listing, searches Emart per item, lets the scout agent match each catalog item and writes
the accepted real products to the catalog. --dry-run matches but leaves the catalog alone; --no-crawl reuses the last
Nomin listing; --from-run RUN applies an earlier run's matches."""
import argparse
import asyncio

from app.agents.scout.agent import load_items, scout
from app.common.db import db, now


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default="", help="comma-separated catalog keys (default: all)")
    ap.add_argument("--no-crawl", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-run", default="", help="apply an earlier run's matches instead of matching again")
    ap.add_argument("--fresh", action="store_true", help="let the agent decide again even where yesterday's candidates "
                                                         "are unchanged (ignores the decision cache)")
    a = ap.parse_args()
    items = load_items(set(a.items.split(",")) - {""})
    started = now()
    summary, matches = await scout(items, crawl=not a.no_crawl, write=not a.dry_run, from_run=a.from_run or None,
                                   fresh=a.fresh)
    if not a.dry_run and not a.items and not a.from_run:  # a full manual run counts as the day's run
        await db.scout_runs.insert_one({"ts": started, "finished": now(), "ok": True, "summary": summary, "manual": True})
    review = [m for m in matches if m["status"] == "review"]
    if review:
        print("\nto review (not applied):")
        for m in review:
            print(f"  {m['store']:6} {m['key']:12} {m.get('our_price')}₮  {m.get('name')}  ({m.get('reason', '')[:80]})")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
