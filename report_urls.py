#!/usr/bin/env python3
"""Build 智愛 teacher per-student report URLs for prev/current weeks."""
from __future__ import annotations

from typing import Dict
from urllib.parse import urlencode

from week_ranges import prev_current_ranges

BASE = "https://chinese.i-learner.com.hk/chinese/r_teachers_report_student.php"

# Fixed school / scope params from the live report link the user confirmed.
DEFAULTS = {
    "timep": "4",          # custom date range
    "level": "4",          # as used on the working link (keep unless UI needs otherwise)
    "class": "0",          # all classes
    "sch": "3147",         # school id
    "student": "0",        # all students
    "week": "0,1,2,3,4",   # Mon–Fri
}


def report_url(date_from: str, date_to: str, **overrides) -> str:
    params = {**DEFAULTS, "from": date_from, "to": date_to, **overrides}
    # Preserve week commas unescaped for parity with the live link
    q = urlencode(params, safe=",")
    return f"{BASE}?{q}"


def prev_current_report_urls(now=None) -> Dict[str, Dict[str, str]]:
    ranges = prev_current_ranges(now)
    out: Dict[str, Dict[str, str]] = {}
    for key, span in ranges.items():
        out[key] = {
            "from": span["from"],
            "to": span["to"],
            "url": report_url(span["from"], span["to"]),
        }
    return out


if __name__ == "__main__":
    for k, v in prev_current_report_urls().items():
        print(f"{k}: {v['from']} → {v['to']}")
        print(v["url"])
        print()
