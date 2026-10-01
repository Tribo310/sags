from datetime import datetime, timezone

from app.agents.scout import schedule


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_due_moment_is_the_last_3am_in_ulaanbaatar(monkeypatch):
    monkeypatch.setattr(schedule, "HOUR", 3)
    # 03:00 UB = 19:00 UTC the day before
    assert schedule.due_since(utc(2026, 9, 30, 20, 0)) == utc(2026, 9, 30, 19, 0)  # 04:00 UB on Oct 1 -> Oct 1 03:00
    assert schedule.due_since(utc(2026, 9, 30, 18, 0)) == utc(2026, 9, 29, 19, 0)  # 02:00 UB -> yesterday's 03:00
