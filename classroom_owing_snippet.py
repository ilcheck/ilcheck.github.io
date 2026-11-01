#!/usr/bin/env python3
"""Build Classroom-announcement snippets for 1B組／4C／5B (done < 3)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "school-practice-data.json"

# platformClass → display label
TARGETS = [("1GB", "1B組"), ("4C", "4C"), ("5B", "5B")]


def given_name(full: str) -> str:
    """去姓：中文去掉首字；拉丁多詞取末詞（SAMIR）；其餘原樣。"""
    name = (full or "").strip()
    if not name:
        return name
    if re.search(r"[A-Za-z]", name) and not re.search(r"[\u4e00-\u9fff]", name):
        parts = re.split(r"\s+", name)
        return parts[-1] if parts else name
    # Chinese / mixed: drop first CJK surname char when present
    if re.match(r"^[\u4e00-\u9fff]", name) and len(name) >= 2:
        return name[1:]
    return name


def is_excluded_1b_name(name: str) -> bool:
    """1B組交功課名單永遠排除 SAMIR／MUHAMMAD SAMIR（只限智愛交付）。"""
    n = re.sub(r"\s+", " ", (name or "").strip().upper())
    return n in {"SAMIR", "MUHAMMAD SAMIR"} or n.endswith(" SAMIR") and "MUHAMMAD" in n


def owing(students: List[Dict[str, Any]], platform_class: str) -> List[Dict[str, Any]]:
    out = []
    for s in students:
        if s.get("platformClass") != platform_class:
            continue
        if int(s.get("done") or 0) >= 3:
            continue
        if platform_class == "1GB" and is_excluded_1b_name(str(s.get("name") or "")):
            continue
        out.append(s)
    out.sort(key=lambda s: (int(s.get("id") or 0), str(s.get("name") or "")))
    return out


def line_for(label: str, item: str, students: List[Dict[str, Any]], use_given: bool) -> str:
    if not students:
        return f"·{item} — （無）"
    if use_given:
        body = "、".join(given_name(str(s.get("name") or "")) for s in students)
    else:
        body = "、".join(str(int(s.get("id") or 0)) for s in students)
    return f"·{item} — {body}"


def build(data: Dict[str, Any]) -> str:
    weeks = data.get("weeks") or {}
    blocks: List[str] = []
    gen = data.get("generated") or ""
    blocks.append(f"【智愛欠交名單｜done＜3｜{gen}】")
    blocks.append("網站：https://ilcheck.github.io/")
    for key, item in (("prev", "上周智愛"), ("current", "智愛")):
        week = weeks.get(key) or {}
        students = week.get("students") or []
        blocks.append("")
        blocks.append(f"— {item}（{week.get('from')}～{week.get('to')}，應做 {week.get('practiceTotal')}）—")
        for pc, label in TARGETS:
            use_given = pc == "1GB"
            xs = owing(students, pc)
            blocks.append(f"{label}")
            blocks.append(line_for(label, item, xs, use_given))
    return "\n".join(blocks) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DATA)
    args = ap.parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    sys.stdout.write(build(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
