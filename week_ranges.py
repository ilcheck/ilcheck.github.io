#!/usr/bin/env python3
"""Compute prev/current Mon–Fri week ranges in Asia/Hong_Kong.

current = Monday–Friday of the ISO week containing *today* (HKT).
prev    = the previous Monday–Friday.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Tuple
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")


def today_hkt(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(HKT)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=HKT)
    else:
        now = now.astimezone(HKT)
    return now.date()


def monday_of(d: date) -> date:
    # Monday = 0 … Sunday = 6
    return d - timedelta(days=d.weekday())


def week_bounds(monday: date) -> Tuple[date, date]:
    """Return (Mon, Fri) for the week starting at monday."""
    return monday, monday + timedelta(days=4)


def prev_current_ranges(now: datetime | None = None) -> Dict[str, Dict[str, str]]:
    """Return {'prev': {'from','to'}, 'current': {'from','to'}} as ISO dates."""
    today = today_hkt(now)
    cur_mon = monday_of(today)
    prev_mon = cur_mon - timedelta(days=7)
    cur_from, cur_to = week_bounds(cur_mon)
    prev_from, prev_to = week_bounds(prev_mon)
    return {
        "prev": {"from": prev_from.isoformat(), "to": prev_to.isoformat()},
        "current": {"from": cur_from.isoformat(), "to": cur_to.isoformat()},
    }


def format_hkt_timestamp(now: datetime | None = None) -> str:
    """YYYY-MM-DD HH:MM HKT"""
    if now is None:
        now = datetime.now(HKT)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=HKT)
    else:
        now = now.astimezone(HKT)
    return now.strftime("%Y-%m-%d %H:%M HKT")


if __name__ == "__main__":
    ranges = prev_current_ranges()
    print("today (HKT):", today_hkt().isoformat())
    print("prev:   ", ranges["prev"]["from"], "→", ranges["prev"]["to"])
    print("current:", ranges["current"]["from"], "→", ranges["current"]["to"])
    print("stamp:  ", format_hkt_timestamp())
