#!/usr/bin/env python3
"""Parse teacher per-student report HTML → school-practice-data.json.

Expected report: r_teachers_report_student.php (per-student rows), NOT the
class-aggregate「本地練習學生表現統計」summary table.

CLI:
  python3 apply_reports.py \\
    --prev-html scrape-inbox/prev.html \\
    --curr-html scrape-inbox/current.html
  python3 apply_reports.py --dump-sample scrape-inbox/current.html
  python3 apply_reports.py ... --rebuild-roster

When student-roster.json exists (and --rebuild-roster is not set), only `done`
is taken from HTML; name / id / classes / total come from the roster.
Do NOT invent student names.
students 含欠交與做滿；week["total"]＝欠交人數，week["complete"]＝做滿人數。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from week_ranges import format_hkt_timestamp, prev_current_ranges

ROOT = Path(__file__).resolve().parent
ROSTER_PATH = ROOT / "student-roster.json"
OUT_PATH = ROOT / "school-practice-data.json"

# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

try:
    from bs4 import BeautifulSoup  # type: ignore

    def _soup(html: str):
        return BeautifulSoup(html, "html.parser")

    HAS_BS4 = True
except ImportError:  # pragma: no cover
    from html.parser import HTMLParser

    HAS_BS4 = False

    class _TableCollector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables: List[List[List[str]]] = []
            self._cur: Optional[List[List[str]]] = None
            self._row: Optional[List[str]] = None
            self._cell: Optional[List[str]] = None
            self._in_cell = False

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self._cur = []
            elif tag == "tr" and self._cur is not None:
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []
                self._in_cell = True

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self._in_cell:
                text = "".join(self._cell or []).strip()
                text = re.sub(r"\s+", " ", text)
                self._row.append(text)
                self._cell = None
                self._in_cell = False
            elif tag == "tr" and self._row is not None and self._cur is not None:
                if self._row:
                    self._cur.append(self._row)
                self._row = None
            elif tag == "table" and self._cur is not None:
                if self._cur:
                    self.tables.append(self._cur)
                self._cur = None

        def handle_data(self, data):
            if self._in_cell and self._cell is not None:
                self._cell.append(data)

    def _soup(html: str):  # type: ignore
        return html  # unused path when no bs4; tables via collector


def _table_own_rows(table):
    """Yield <tr> belonging to this table (not nested), via tbody/thead/tfoot."""
    for child in table.children:
        name = getattr(child, "name", None)
        if name == "tr":
            yield child
        elif name in ("tbody", "thead", "tfoot"):
            for tr in child.find_all("tr", recursive=False):
                yield tr


def extract_tables(html: str) -> List[List[List[str]]]:
    """Return list of tables; each table is list of rows; each row list of cell texts."""
    if HAS_BS4:
        soup = _soup(html)
        tables = []
        for table in soup.find_all("table"):
            rows = []
            for tr in _table_own_rows(table):
                # Direct cells only — header cells may wrap tiny nested tables
                cells = tr.find_all(["th", "td"], recursive=False)
                if not cells:
                    continue
                rows.append(
                    [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in cells]
                )
            if rows:
                tables.append(rows)
        return tables
    collector = _TableCollector()
    collector.feed(html)
    return collector.tables


def extract_platform_ids_from_html(html: str) -> Dict[Tuple[str, str], str]:
    """Best-effort: map (name or class+id) hints from hrefs containing student ids.

    i-Learner student links often look like:
      ...student_id=1004175...  or  ...sid=1004175...  or  ...id=1004175...
    We also scrape nearby text when possible via BeautifulSoup.
    Returns a loose dict used only as a secondary match aid: platformId -> raw.
    """
    ids = set(re.findall(r"(?:student_id|sid|stu_id|studentid|student)=(\d{5,})", html, re.I))
    ids |= set(re.findall(r"/student[^\"'\s]*=(\d{5,})", html, re.I))
    return {i: i for i in ids}


# ---------------------------------------------------------------------------
# Column detection (flexible — per-student report)
# ---------------------------------------------------------------------------

# TODO: Once a real per-student HTML sample is confirmed, tighten these aliases
# and prefer exact header strings from r_teachers_report_student.php.
HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    # class / 班別
    "class": (
        "平台班別",
        "班別",
        "班級",
        "班",
        "class",
        "classname",
        "班名",
    ),
    # school / seat number 學號 (roster "id")
    "seat": (
        "學號",
        "座號",
        "編號",
        "學生編號",
        "班號",
        "no",
        "num",
        "number",
        "student no",
        "student number",
    ),
    # platform student id
    "platform_id": (
        "學生id",
        "學生 id",
        "平台id",
        "平台 id",
        "platform id",
        "platformstudentid",
        "studentid",
        "student_id",
        "sid",
    ),
    # name
    "name": (
        "姓名",
        "名字",
        "學生姓名",
        "學生名字",
        "name",
        "student name",
    ),
    # done count — 「已完成練習數目」etc.
    "done": (
        "已完成練習數目",
        "已完成練習數",
        "已完成數目",
        "已完成",
        "完成數目",
        "完成數",
        "完成練習",
        "完成次數",
        "練習完成數目",
        "done",
        "completed",
        "finish",
    ),
    # total expected practices
    "total": (
        "總練習數",
        "總練習數目",
        "練習總數",
        "應做練習",
        "應完成",
        "總數",
        "total",
        "assigned",
    ),
}


def _norm_header(h: str) -> str:
    h = h.strip().lower()
    h = re.sub(r"[\s　_:：\-–—/／()（）\[\]【】]", "", h)
    # strip trailing percentage annotations like 完成率%
    h = re.sub(r"\d+(\.\d+)?%?", "", h)
    return h


def _score_header(cell: str, aliases: Tuple[str, ...]) -> int:
    n = _norm_header(cell)
    if not n:
        return 0
    best = 0
    for a in aliases:
        an = _norm_header(a)
        if not an:
            continue
        if n == an:
            best = max(best, 100)
        elif an in n or n in an:
            best = max(best, 60)
    return best


def detect_columns(header_row: List[str]) -> Dict[str, int]:
    """Map logical field → column index. Requires at least done + (platform_id|seat|name)."""
    scores: Dict[str, List[Tuple[int, int]]] = {k: [] for k in HEADER_ALIASES}
    for idx, cell in enumerate(header_row):
        for key, aliases in HEADER_ALIASES.items():
            sc = _score_header(cell, aliases)
            if sc:
                scores[key].append((sc, idx))

    mapping: Dict[str, int] = {}
    used = set()
    # Prefer higher scores; resolve conflicts by picking best unused column
    for key in ("done", "platform_id", "seat", "name", "class", "total"):
        candidates = sorted(scores[key], key=lambda x: (-x[0], x[1]))
        for sc, idx in candidates:
            if idx in used:
                continue
            # Avoid mistaking 「未完成」 for done: check raw header
            raw = header_row[idx]
            if key == "done" and ("未完成" in raw or "未交" in raw):
                continue
            if key == "done" and sc < 60:
                continue
            if key == "class" and "學校班別" in header_row[idx] and any(
                "平台班別" in h for h in header_row
            ):
                continue
            mapping[key] = idx
            used.add(idx)
            break
    return mapping


def _parse_int(text: str) -> Optional[int]:
    if text is None:
        return None
    t = str(text).strip()
    # "3 (60%)" / "3(60%)" / "3／5"
    m = re.match(r"^\s*(\d+)\s*", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else None


def _parse_done_total(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse '4 / 5 (0.8%)' or '4/5' → (done, total)."""
    if text is None:
        return None, None
    t = str(text).strip()
    m = re.match(r"^\s*(\d+)\s*[/／]\s*(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    return _parse_int(t), None


def _parse_name_seat(text: str) -> Tuple[str, Optional[int]]:
    """Parse '鍾鳴妤 (4)' → ('鍾鳴妤', 4)."""
    if not text:
        return "", None
    t = str(text).strip()
    m = re.match(r"^(.*?)\s*[\(（]\s*(\d+)\s*[\)）]\s*$", t)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return t, None


def _row_platform_id_from_html(html: str, name: str, seat: Optional[int]) -> Optional[int]:
    """Find student= id on the name anchor."""
    if not name:
        return None
    esc = re.escape(name)
    if seat is not None:
        pat = re.compile(
            rf'href=["\'][^"\']*[?&]student=(\d{{5,}})[^"\']*["\'][^>]*>\s*{esc}\s*[\(（]\s*{seat}\s*[\)）]',
            re.I,
        )
        m = pat.search(html)
        if m:
            return int(m.group(1))
    pat = re.compile(
        rf'href=["\'][^"\']*[?&]student=(\d{{5,}})[^"\']*["\'][^>]*>\s*{esc}\b',
        re.I,
    )
    m = pat.search(html)
    return int(m.group(1)) if m else None


def is_class_summary_table(header_row: List[str]) -> bool:
    """Detect the WRONG report level (class-aggregate 表現統計)."""
    joined = " ".join(header_row)
    markers = ("各班別人數", "參與人數", "參與率", "平均分數", "累積總分")
    hits = sum(1 for m in markers if m in joined)
    # also: has 班別 but no 姓名/學號
    has_name = any(_score_header(c, HEADER_ALIASES["name"]) >= 60 for c in header_row)
    has_seat = any(_score_header(c, HEADER_ALIASES["seat"]) >= 60 for c in header_row)
    if hits >= 2 and not has_name and not has_seat:
        return True
    return False


def pick_student_table(
    tables: List[List[List[str]]],
) -> Tuple[List[str], List[List[str]], Dict[str, int]]:
    """Choose the best per-student table; raise SystemExit on failure."""
    candidates = []
    summary_hits = 0
    for t_idx, table in enumerate(tables):
        if len(table) < 2:
            continue
        # try first few rows as header (some reports have title rows)
        for h_idx in range(min(3, len(table))):
            header = table[h_idx]
            if is_class_summary_table(header):
                summary_hits += 1
                continue
            mapping = detect_columns(header)
            if "done" not in mapping:
                continue
            if not any(k in mapping for k in ("platform_id", "seat", "name")):
                continue
            body = [r for r in table[h_idx + 1 :] if len(r) >= len(header) - 1]
            score = 100 * len(body) + 10 * len(mapping)
            if "platform_id" in mapping:
                score += 50
            if "seat" in mapping:
                score += 20
            if "name" in mapping:
                score += 10
            candidates.append((score, t_idx, h_idx, header, body, mapping))

    if not candidates:
        msg = [
            "ERROR: No per-student table found in HTML.",
            "",
            "Expected a student-level report (r_teachers_report_student.php) with columns like:",
            "  班別 / 學號 / 姓名 / 已完成練習數目  (and optionally 總練習數 / 學生ID)",
            "",
            "NOT the class-aggregate「本地練習學生表現統計」table",
            "  (班別 / 各班別人數 / 參與人數 / 已完成練習數目(完成率%) / …).",
            "",
            f"Found {len(tables)} table(s); class-summary-like headers seen: {summary_hits}.",
            "Re-run with --dump-sample <html> to print detected headers.",
        ]
        raise SystemExit("\n".join(msg))

    candidates.sort(key=lambda x: -x[0])
    _, t_idx, h_idx, header, body, mapping = candidates[0]
    return header, body, mapping


def parse_report_html(path: Path) -> List[Dict[str, Any]]:
    """Parse one HTML file → list of {platformStudentId?, class?, seat?, name?, done, total?}."""
    html = path.read_text(encoding="utf-8", errors="replace")
    if not html.strip():
        raise SystemExit(f"ERROR: empty HTML file: {path}")

    tables = extract_tables(html)
    if not tables:
        raise SystemExit(
            f"ERROR: no <table> found in {path}. "
            "Save the full per-student report HTML (r_teachers_report_student.php)."
        )

    header, body, mapping = pick_student_table(tables)
    platform_ids_in_page = extract_platform_ids_from_html(html)

    rows: List[Dict[str, Any]] = []
    for raw in body:
        def cell(key: str) -> str:
            idx = mapping.get(key)
            if idx is None or idx >= len(raw):
                return ""
            return raw[idx]

        joined = " ".join(raw)
        if "所選擇日期學生表現統計" in joined or "請按個別學生" in joined:
            continue

        done_cell = cell("done")
        done, total_from_done = _parse_done_total(done_cell)
        if done is None:
            continue  # skip total/summary rows
        total = _parse_int(cell("total")) if "total" in mapping else None
        if total is None:
            total = total_from_done
        seat = _parse_int(cell("seat")) if "seat" in mapping else None
        platform_id = None
        if "platform_id" in mapping:
            platform_id = _parse_int(cell("platform_id"))
        name_raw = cell("name").strip() if "name" in mapping else ""
        name, seat_from_name = _parse_name_seat(name_raw)
        if seat is None:
            seat = seat_from_name
        klass = cell("class").strip() if "class" in mapping else ""

        # Heuristic: if a cell looks like platform id in seat column (large number)
        if platform_id is None and seat is not None and seat >= 100000:
            platform_id = seat
            seat = None

        if platform_id is None and name:
            platform_id = _row_platform_id_from_html(html, name, seat)

        rec: Dict[str, Any] = {"done": done}
        if total is not None:
            rec["total"] = total
        if platform_id is not None:
            rec["platformStudentId"] = platform_id
        if seat is not None:
            rec["id"] = seat
        if name:
            rec["name"] = name
        if klass:
            rec["platformClass"] = klass
        rows.append(rec)

    if not rows:
        raise SystemExit(
            f"ERROR: parsed table headers {header!r} (mapping={mapping}) "
            f"but got 0 student rows from {path}."
        )

    # Soft warning if page had platform ids in links but we didn't map them
    if platform_ids_in_page and not any("platformStudentId" in r for r in rows):
        print(
            f"NOTE: {path.name}: found {len(platform_ids_in_page)} platform id(s) in links "
            "but no platform-id column; matching will use 學號/姓名.",
            file=sys.stderr,
        )

    print(
        f"OK {path.name}: {len(rows)} student rows; columns={mapping}; header={header}",
        file=sys.stderr,
    )
    return rows


# ---------------------------------------------------------------------------
# Roster merge
# ---------------------------------------------------------------------------

CLASS_ORDER = [
    "1GA", "1GB", "1GC", "1GD",
    "2GA", "2GB", "2GC", "2GD",
    "3GA", "3GB", "3GC", "3GD",
    "4A", "4B", "4C",
    "5A", "5B", "5C",
    "6A", "6B", "6C",
]


def load_roster(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # keys are platformStudentId as strings
    return {str(k): v for k, v in data.items()}


def save_roster(path: Path, roster: Dict[str, Dict[str, Any]]) -> None:
    # stable sort by platformStudentId
    ordered = {k: roster[k] for k in sorted(roster.keys(), key=lambda x: int(x) if x.isdigit() else x)}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def platform_to_school_class(platform_class: str) -> str:
    """1GA→1A, 1GB→1B, 4A→4A."""
    pc = (platform_class or "").strip()
    m = re.match(r"^(\d)G([A-Da-d])$", pc)
    if m:
        return m.group(1) + m.group(2).upper()
    return pc


def rebuild_roster_from_rows(
    row_sets: List[List[Dict[str, Any]]],
    old: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build/refresh roster entries from parsed HTML rows. Keeps old fields when possible."""
    # Prefer rows that have platformStudentId
    by_pid: Dict[str, Dict[str, Any]] = {}
    for rows in row_sets:
        for r in rows:
            pid = r.get("platformStudentId")
            if pid is None:
                continue
            key = str(pid)
            entry = dict(old.get(key, {}))
            entry["platformStudentId"] = int(pid)
            if r.get("name"):
                entry["name"] = r["name"]
            if r.get("platformClass"):
                entry["platformClass"] = r["platformClass"]
                entry["schoolClass"] = platform_to_school_class(r["platformClass"])
            if r.get("id") is not None:
                entry["id"] = int(r["id"])
            if r.get("total") is not None:
                entry["total"] = int(r["total"])
            else:
                entry.setdefault("total", old.get(key, {}).get("total", 5))
            entry.setdefault("name", old.get(key, {}).get("name", ""))
            entry.setdefault("platformClass", old.get(key, {}).get("platformClass", ""))
            entry.setdefault("schoolClass", platform_to_school_class(entry.get("platformClass", "")))
            entry.setdefault("id", old.get(key, {}).get("id", 0))
            by_pid[key] = entry

    if by_pid:
        return by_pid

    # No platform IDs — cannot safely rebuild a platformStudentId-keyed roster
    raise SystemExit(
        "ERROR: --rebuild-roster needs a platform student id column (or sid= in links "
        "mapped to rows). HTML had no platformStudentId fields. Keep existing roster "
        "or supply a better per-student export."
    )


def match_done(
    roster: Dict[str, Dict[str, Any]],
    rows: List[Dict[str, Any]],
    allow_partial: bool = False,
) -> Dict[str, int]:
    """Return platformStudentId(str) → done. Only for students found in HTML."""
    # Indexes
    by_pid: Dict[str, Dict[str, Any]] = {}
    by_class_seat: Dict[Tuple[str, int], Dict[str, Any]] = {}
    by_class_name: Dict[Tuple[str, str], Dict[str, Any]] = {}
    by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for r in rows:
        if "platformStudentId" in r:
            by_pid[str(r["platformStudentId"])] = r
        pc = (r.get("platformClass") or "").strip()
        if r.get("id") is not None and pc:
            by_class_seat[(pc, int(r["id"]))] = r
            # also try school-class form
            by_class_seat[(platform_to_school_class(pc), int(r["id"]))] = r
        if r.get("name") and pc:
            by_class_name[(pc, r["name"])] = r
            by_class_name[(platform_to_school_class(pc), r["name"])] = r
        if r.get("name"):
            by_name[r["name"]].append(r)

    done_map: Dict[str, int] = {}
    unmatched_roster = 0
    for key, stu in roster.items():
        r = None
        if key in by_pid:
            r = by_pid[key]
        else:
            pc = (stu.get("platformClass") or "").strip()
            sc = (stu.get("schoolClass") or "").strip()
            seat = stu.get("id")
            name = stu.get("name") or ""
            if seat is not None:
                r = by_class_seat.get((pc, int(seat))) or by_class_seat.get((sc, int(seat)))
            if r is None and name:
                r = by_class_name.get((pc, name)) or by_class_name.get((sc, name))
            if r is None and name and len(by_name.get(name, [])) == 1:
                r = by_name[name][0]
        if r is None:
            unmatched_roster += 1
            continue
        done_map[key] = int(r["done"])

    matched = len(done_map)
    print(
        f"Matched done for {matched}/{len(roster)} roster students "
        f"({unmatched_roster} unmatched → treated as done=0).",
        file=sys.stderr,
    )
    if matched == 0:
        raise SystemExit(
            "ERROR: could not match any HTML rows to student-roster.json. "
            "Check that the HTML is a per-student report with 學號/姓名/班別 "
            "or platform student id. Use --dump-sample to inspect headers."
        )
    # Full-school reports should match most of the roster. A tiny match rate
    # usually means the wrong HTML (or a single-class export) was saved.
    if len(roster) >= 20:
        rate = matched / len(roster)
        if rate < 0.5 and not allow_partial:
            raise SystemExit(
                f"ERROR: only matched {matched}/{len(roster)} ({rate:.0%}) roster "
                "students. Refusing to treat the rest as done=0. "
                "Save the full-school per-student report, or pass --allow-partial."
            )
    return done_map


def week_payload(
    roster: Dict[str, Dict[str, Any]],
    done_map: Dict[str, int],
    from_d: str,
    to_d: str,
    html_totals: List[int],
) -> Dict[str, Any]:
    """Build one week object; students 含欠交與做滿（done 0..practiceTotal）。"""
    # Practice total: prefer HTML total column mode; else roster total mode
    if html_totals:
        practice_total = Counter(html_totals).most_common(1)[0][0]
    else:
        practice_total = Counter(
            int(s.get("total", 5)) for s in roster.values()
        ).most_common(1)[0][0]

    students = []
    missing = 0
    complete = 0
    for key, stu in roster.items():
        total = int(stu.get("total", practice_total))
        # If this week's HTML declared a different total, prefer week practice_total
        # for the欠交 threshold (mid-week windows may have total < roster.total).
        if html_totals:
            total = practice_total
        done = int(done_map.get(key, 0))  # unmatched → 0 (欠交)
        if done >= total:
            complete += 1
        else:
            missing += 1
        students.append(
            {
                "platformClass": stu.get("platformClass", ""),
                "schoolClass": stu.get("schoolClass")
                or platform_to_school_class(stu.get("platformClass", "")),
                "id": int(stu.get("id", 0)),
                "name": stu.get("name", ""),
                "done": done,
                "total": total,
            }
        )

    # Sort: classOrder, then schoolClass, id, name
    rank = {c: i for i, c in enumerate(CLASS_ORDER)}

    def sort_key(s: Dict[str, Any]):
        pc = s.get("platformClass") or ""
        return (
            rank.get(pc, 999),
            pc,
            s.get("schoolClass") or "",
            int(s.get("id") or 0),
            s.get("name") or "",
        )

    students.sort(key=sort_key)
    return {
        "from": from_d,
        "to": to_d,
        "total": missing,  # 欠交人數（與既有 schema／晨檢對比語意一致）
        "complete": complete,  # 做滿人數（done >= practiceTotal）
        "practiceTotal": practice_total,
        "students": students,
    }


def dump_sample(path: Path) -> None:
    html = path.read_text(encoding="utf-8", errors="replace")
    tables = extract_tables(html)
    print(f"File: {path}")
    print(f"Tables found: {len(tables)}")
    for i, table in enumerate(tables):
        print(f"\n=== Table {i} ({len(table)} rows) ===")
        for h_idx in range(min(3, len(table))):
            header = table[h_idx]
            summary = is_class_summary_table(header)
            mapping = detect_columns(header)
            print(f"  row[{h_idx}] headers ({len(header)}): {header}")
            print(f"           class_summary={summary}  mapping={mapping}")
        if len(table) > 1:
            print(f"  sample body row: {table[min(1, len(table)-1)][:12]}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Parse i-Learner per-student teacher report HTML into "
            "school-practice-data.json（含欠交與做滿學生；week.total 仍＝欠交人數）。"
        )
    )
    ap.add_argument("--prev-html", type=Path, help="上周 Mon–Fri report HTML")
    ap.add_argument("--curr-html", "--current-html", type=Path, dest="curr_html",
                    help="當周 Mon–Fri report HTML")
    ap.add_argument(
        "--rebuild-roster",
        action="store_true",
        help="Rebuild student-roster.json from HTML (requires platform student ids)",
    )
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow match rate < 50%% (unmatched roster students still get done=0)",
    )
    ap.add_argument(
        "--dump-sample",
        type=Path,
        metavar="HTML",
        help="Print first table headers found and exit (no write)",
    )
    ap.add_argument(
        "--roster",
        type=Path,
        default=ROSTER_PATH,
        help=f"Roster path (default: {ROSTER_PATH.name})",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=OUT_PATH,
        help=f"Output JSON (default: {OUT_PATH.name})",
    )
    args = ap.parse_args(argv)

    if args.dump_sample:
        if not args.dump_sample.exists():
            print(f"ERROR: file not found: {args.dump_sample}", file=sys.stderr)
            return 1
        dump_sample(args.dump_sample)
        return 0

    if not args.prev_html or not args.curr_html:
        ap.error("require --prev-html and --curr-html (or use --dump-sample)")

    for p in (args.prev_html, args.curr_html):
        if not p.exists():
            print(f"ERROR: HTML not found: {p}", file=sys.stderr)
            return 1

    prev_rows = parse_report_html(args.prev_html)
    curr_rows = parse_report_html(args.curr_html)

    roster = load_roster(args.roster)
    if args.rebuild_roster or not roster:
        if not roster and not args.rebuild_roster:
            print("No roster found — attempting to build from HTML…", file=sys.stderr)
        roster = rebuild_roster_from_rows([prev_rows, curr_rows], roster)
        # Fill seat/name from rows without overwriting platform keys already set
        save_roster(args.roster, roster)
        print(f"Wrote roster ({len(roster)} students) → {args.roster}", file=sys.stderr)
    else:
        print(f"Using existing roster ({len(roster)} students).", file=sys.stderr)

    # Only update done from HTML; never invent names
    prev_done = match_done(roster, prev_rows, allow_partial=args.allow_partial)
    curr_done = match_done(roster, curr_rows, allow_partial=args.allow_partial)

    ranges = prev_current_ranges()
    prev_week = week_payload(
        roster,
        prev_done,
        ranges["prev"]["from"],
        ranges["prev"]["to"],
        [r["total"] for r in prev_rows if "total" in r],
    )
    curr_week = week_payload(
        roster,
        curr_done,
        ranges["current"]["from"],
        ranges["current"]["to"],
        [r["total"] for r in curr_rows if "total" in r],
    )

    out = {
        "generated": format_hkt_timestamp(),
        "weeks": {"prev": prev_week, "current": curr_week},
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.out}: prev欠交={prev_week['total']} 做滿={prev_week['complete']} "
        f"current欠交={curr_week['total']} 做滿={curr_week['complete']} "
        f"(practiceTotal prev={prev_week['practiceTotal']} curr={curr_week['practiceTotal']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
