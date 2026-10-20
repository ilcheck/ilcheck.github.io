#!/usr/bin/env python3
"""Build site/index.html (and optionally root index.html) from school-practice-data.json.

UX matches the published dashboard:
  - week toggle prev / current
  - class blocks collapsed by default; click header to open
  - columns by done count 0..(practiceTotal-1)
  - name format 班別(學號)人名 with lowercase class letter e.g. 1a(3)鍾鳴妤
  - headers P1–3: 1A組…3D組 (1GB→1B組); P4–6: 4A–6C
  - subheader「全班 N 人」only
  - update timestamp YYYY-MM-DD HH:MM HKT
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from week_ranges import format_hkt_timestamp

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "school-practice-data.json"
ROSTER_PATH = ROOT / "student-roster.json"
SITE_DIR = ROOT / "site"
SITE_INDEX = SITE_DIR / "index.html"
ROOT_INDEX = ROOT / "index.html"

CLASS_ORDER = [
    "1GA", "1GB", "1GC", "1GD",
    "2GA", "2GB", "2GC", "2GD",
    "3GA", "3GB", "3GC", "3GD",
    "4A", "4B", "4C",
    "5A", "5B", "5C",
    "6A", "6B", "6C",
]


def display_header(platform_class: str) -> str:
    """1GA→1A組, 1GB→1B組, 4A→4A."""
    pc = (platform_class or "").strip()
    m = re.match(r"^(\d)G([A-Da-d])$", pc)
    if m:
        return f"{m.group(1)}{m.group(2).upper()}組"
    m = re.match(r"^([4-6])([A-Ca-c])$", pc)
    if m:
        return f"{m.group(1)}{m.group(2).upper()}"
    # fallback: if looks like 1A / 3D from schoolClass style for G1–3
    m = re.match(r"^([1-3])([A-Da-d])$", pc)
    if m:
        return f"{m.group(1)}{m.group(2).upper()}組"
    return pc


def load_roster_sizes(roster_path: Path, data: Dict[str, Any]) -> Dict[str, int]:
    if roster_path.exists():
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        sizes: Dict[str, int] = Counter()
        for stu in roster.values():
            pc = stu.get("platformClass") or ""
            if pc:
                sizes[pc] += 1
        return dict(sizes)
    # Fallback: infer from欠交 lists (under-counts full roster — last resort)
    sizes = Counter()
    for week in data.get("weeks", {}).values():
        for s in week.get("students", []):
            pc = s.get("platformClass") or ""
            if pc:
                sizes[pc] = max(sizes[pc], 0)  # placeholder
    # Cannot know full size; use max seen seat id? Better leave from students unique names — skip
    # Use count of unique students seen across weeks as weak estimate
    seen = {pc: set() for pc in CLASS_ORDER}
    for week in data.get("weeks", {}).values():
        for s in week.get("students", []):
            pc = s.get("platformClass") or ""
            if pc not in seen:
                seen[pc] = set()
            seen[pc].add((s.get("id"), s.get("name")))
    return {pc: len(ids) for pc, ids in seen.items() if ids}


def practice_total_for_week(week: Dict[str, Any]) -> int:
    if week.get("practiceTotal"):
        return int(week["practiceTotal"])
    totals = [int(s["total"]) for s in week.get("students", []) if s.get("total") is not None]
    if totals:
        return Counter(totals).most_common(1)[0][0]
    return 5


def build_embed_payload(data: Dict[str, Any], roster_sizes: Dict[str, int]) -> Dict[str, Any]:
    updated = data.get("generated") or data.get("updated") or format_hkt_timestamp()
    # Ensure HKT label
    if updated and "HKT" not in str(updated):
        updated = f"{updated} HKT" if re.match(r"\d{4}-\d{2}-\d{2}", str(updated)) else updated

    weeks_out = {}
    for key in ("prev", "current"):
        week = dict(data.get("weeks", {}).get(key) or {})
        if not week:
            continue
        pt = practice_total_for_week(week)
        week["practiceTotal"] = pt
        # Keep week["total"] as 欠交 count (schema)
        week["total"] = len(week.get("students") or [])
        weeks_out[key] = week

    # classOrder: prefer fixed order intersecting roster / data
    present = set(roster_sizes) | {
        s.get("platformClass")
        for w in weeks_out.values()
        for s in w.get("students", [])
    }
    class_order = [c for c in CLASS_ORDER if c in present] or list(CLASS_ORDER)
    headers = {c: display_header(c) for c in class_order}
    sizes = {c: int(roster_sizes.get(c, 0)) for c in class_order}

    return {
        "updated": updated,
        "weeks": weeks_out,
        "classOrder": class_order,
        "headers": headers,
        "rosterSizes": sizes,
    }


HTML_SHELL = r"""<!DOCTYPE html>
<html lang="zh-Hant-HK">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>智愛週練習欠交一覽 · 全校</title>
<style>
:root {
  --bg:#f4f6f8; --card:#fff; --ink:#1a1d21; --muted:#5c6570; --line:#e2e6eb;
  --c0:#b42318; --c1:#c4320a; --c2:#b54708; --c3:#175cd3; --c4:#027a48; --accent:#146c94;
}
*{box-sizing:border-box}
body{margin:0;font-family:"PingFang HK","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;background:var(--bg);color:var(--ink);line-height:1.45}
header{background:linear-gradient(135deg,#0b3d5c,#146c94);color:#fff;padding:1.35rem 1.25rem 1.1rem}
header h1{margin:0 0 .35rem;font-size:1.3rem}
header p{margin:0;opacity:.92;font-size:.92rem}
.toolbar{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;margin-top:.9rem}
.seg{display:inline-flex;background:rgba(255,255,255,.15);border-radius:999px;padding:3px}
.seg button{border:0;background:transparent;color:#fff;padding:.35rem .9rem;border-radius:999px;font:inherit;font-weight:600;cursor:pointer;opacity:.85}
.seg button.active{background:#fff;color:#0b3d5c;opacity:1}
.meta{font-size:.82rem;opacity:.9}
main{padding:1.1rem;max-width:1400px;margin:0 auto}
.class-block{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:.7rem;overflow:hidden;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.class-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.75rem 1rem;background:#fafbfc;cursor:pointer;user-select:none;border:0;width:100%;font:inherit;text-align:left;color:inherit}
.class-head:hover{background:#f0f3f6}
.class-head h2{margin:0;font-size:1.02rem;display:flex;align-items:center;gap:.5rem}
.chev{display:inline-block;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid var(--muted);transition:transform .15s}
.class-block.open .chev{transform:rotate(180deg)}
.class-head .stat{color:var(--muted);font-size:.82rem}
.class-body{display:none;border-top:1px solid var(--line)}
.class-block.open .class-body{display:block}
.grid{display:grid;grid-template-columns:repeat(var(--cols,5),minmax(0,1fr))}
@media(max-width:960px){.grid{grid-template-columns:1fr}}
.col{border-right:1px solid var(--line);min-height:4rem}
.col:last-child{border-right:none}
@media(max-width:960px){.col{border-right:none;border-bottom:1px solid var(--line)} .col:last-child{border-bottom:none}}
.col-h{padding:.55rem .7rem;font-size:.8rem;font-weight:700;border-bottom:1px solid var(--line)}
.col[data-n="0"] .col-h{color:var(--c0);background:#fef3f2}
.col[data-n="1"] .col-h{color:var(--c1);background:#fff4ed}
.col[data-n="2"] .col-h{color:var(--c2);background:#fffaeb}
.col[data-n="3"] .col-h{color:var(--c3);background:#eff8ff}
.col[data-n="4"] .col-h{color:var(--c4);background:#ecfdf3}
ul.roster{list-style:none;margin:0;padding:.45rem .7rem .65rem;font-size:.88rem}
ul.roster li{padding:.22rem 0;border-bottom:1px dashed #eef1f4}
ul.roster li:last-child{border-bottom:none}
.empty{padding:.85rem .7rem;color:var(--muted);font-size:.84rem}
footer{text-align:center;color:var(--muted);font-size:.78rem;padding:.4rem 1rem 1.4rem}
.summary{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.8rem 1rem;margin-bottom:1rem;font-size:.9rem}
</style>
</head>
<body>
<header>
  <h1>智愛週練習欠交一覽 · 全校</h1>
  <p>1GA–6C · 按完成次數分欄 · 名單格式：班別(學號)人名</p>
  <div class="toolbar">
    <div class="seg" id="weekSeg" role="group" aria-label="週次">
      <button type="button" data-week="prev">上周（周一至五）</button>
      <button type="button" data-week="current">當周（周一至五）</button>
    </div>
    <span class="meta" id="rangeMeta"></span>
  </div>
</header>
<main>
  <div class="summary" id="summary"></div>
  <div id="classes"></div>
</main>
<footer>資料來自智愛中文平台教師中心 · 每日 7:30（香港時間）自動更新 · 班別預設收合，撳標題展開</footer>
<script type="application/json" id="data">__DATA_JSON__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);

function schoolLine(s) {
  let sc = (s.schoolClass || s.platformClass || '').trim().replace(/\s+/g,'');
  if (sc.length >= 2 && /[A-Za-z]$/.test(sc)) sc = sc.slice(0,-1) + sc.slice(-1).toLowerCase();
  else sc = sc.toLowerCase();
  return sc + '(' + s.id + ')' + s.name;
}

function practiceTotal(week) {
  if (week.practiceTotal != null) return Number(week.practiceTotal);
  if (week.students && week.students.length && week.students[0].total != null)
    return Number(week.students[0].total);
  return 5;
}

function render(weekKey) {
  const week = DATA.weeks[weekKey];
  if (!week) return;
  const pTotal = practiceTotal(week);
  document.getElementById('rangeMeta').textContent =
    '查閱：' + week.from + ' 至 ' + week.to + ' · 更新：' + DATA.updated;
  document.querySelectorAll('#weekSeg button').forEach(b => {
    b.classList.toggle('active', b.dataset.week === weekKey);
  });

  const byClass = {};
  for (const s of week.students) {
    const pc = s.platformClass;
    (byClass[pc] ||= []).push(s);
  }

  let totalRoster = 0;
  for (const label of DATA.classOrder) {
    totalRoster += Number(DATA.rosterSizes[label] || 0);
  }
  let lt3 = 0;
  for (const s of week.students) {
    if (Number(s.done) < 3) lt3 += 1;
  }
  const ge3 = Math.max(0, totalRoster - lt3);
  document.getElementById('summary').textContent =
    (weekKey === 'prev' ? '上周' : '當周') + '已完成3次以上人數合共' + ge3 + '人。按班別標題展開明細。';

  const root = document.getElementById('classes');
  root.innerHTML = '';
  const colMax = Math.max(0, pTotal - 1);
  for (const label of DATA.classOrder) {
    const list = byClass[label] || [];
    const header = DATA.headers[label] || label;
    const size = DATA.rosterSizes[label] || '?';
    const byDone = {};
    for (const s of list) {
      const d = Number(s.done);
      (byDone[d] ||= []).push(s);
    }
    const block = document.createElement('section');
    block.className = 'class-block';
    block.dataset.class = label;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'class-head';
    btn.innerHTML = '<h2><span class="chev"></span>' + header + '</h2>' +
      '<span class="stat">全班 ' + size + ' 人</span>';
    btn.addEventListener('click', () => block.classList.toggle('open'));

    const body = document.createElement('div');
    body.className = 'class-body';
    const grid = document.createElement('div');
    grid.className = 'grid';
    grid.style.setProperty('--cols', String(colMax + 1));

    for (let n = 0; n <= colMax; n++) {
      const col = document.createElement('div');
      col.className = 'col';
      col.dataset.n = String(Math.min(n, 4));
      const students = byDone[n] || [];
      const h = document.createElement('div');
      h.className = 'col-h';
      h.textContent = '已完成 ' + n + ' 次（' + students.length + '）';
      col.appendChild(h);
      if (!students.length) {
        const e = document.createElement('div');
        e.className = 'empty';
        e.textContent = '無';
        col.appendChild(e);
      } else {
        const ul = document.createElement('ul');
        ul.className = 'roster';
        students.sort((a,b) => String(a.schoolClass||'').localeCompare(String(b.schoolClass||'')) || (Number(a.id)-Number(b.id)) || String(a.name).localeCompare(String(b.name),'zh-Hant'));
        for (const s of students) {
          const li = document.createElement('li');
          li.textContent = schoolLine(s);
          ul.appendChild(li);
        }
        col.appendChild(ul);
      }
      grid.appendChild(col);
    }
    body.appendChild(grid);
    block.appendChild(btn);
    block.appendChild(body);
    root.appendChild(block);
  }
}

document.getElementById('weekSeg').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-week]');
  if (b) render(b.dataset.week);
});

render('prev');
</script>
</body>
</html>
"""


def render_html(payload: Dict[str, Any]) -> str:
    # Compact JSON inside the script tag (keep Chinese unescaped)
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Guard against accidental </script> in names
    data_json = data_json.replace("</", "<\\/")
    return HTML_SHELL.replace("__DATA_JSON__", data_json)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build 智愛欠交 dashboard HTML from JSON")
    ap.add_argument("--data", type=Path, default=DATA_PATH)
    ap.add_argument("--roster", type=Path, default=ROSTER_PATH)
    ap.add_argument("--out", type=Path, default=SITE_INDEX, help="Primary output (default site/index.html)")
    ap.add_argument(
        "--also-root",
        action="store_true",
        help="Also write/overwrite repo root index.html (local preview / Pages)",
    )
    ap.add_argument(
        "--root-index",
        type=Path,
        default=ROOT_INDEX,
        help="Root index path when --also-root",
    )
    args = ap.parse_args(argv)

    if not args.data.exists():
        print(f"ERROR: data file not found: {args.data}", file=sys.stderr)
        return 1

    data = json.loads(args.data.read_text(encoding="utf-8"))
    if "weeks" not in data:
        print("ERROR: school-practice-data.json missing 'weeks'", file=sys.stderr)
        return 1

    roster_sizes = load_roster_sizes(args.roster, data)
    payload = build_embed_payload(data, roster_sizes)
    html = render_html(payload)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(html)} bytes, updated={payload['updated']})")

    if args.also_root:
        args.root_index.write_text(html, encoding="utf-8")
        print(f"Wrote {args.root_index}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
