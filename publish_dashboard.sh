#!/usr/bin/env bash
# Rebuild dashboard HTML from school-practice-data.json and push to GitHub Pages.
# Repo: ilcheck/ilcheck.github.io (this directory).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "${DRY_RUN:-}" == "1" ]]; then
  echo "[DRY_RUN] Would build + commit + push. Building only."
  python3 build_dashboard.py --also-root
  echo "[DRY_RUN] site/index.html and index.html updated; skipping git."
  exit 0
fi

python3 build_dashboard.py --also-root

if [[ ! -d .git ]]; then
  echo "ERROR: $ROOT is not a git repo" >&2
  exit 1
fi

# Stage only the published page (data JSON stays local unless already tracked)
git add index.html
if git diff --cached --quiet; then
  echo "No changes to publish."
  exit 0
fi

STAMP="$(python3 -c 'from week_ranges import format_hkt_timestamp; print(format_hkt_timestamp())')"
git commit -m "Update weekly practice dashboard ${STAMP}"
git push origin main
echo "Published to origin main (${STAMP})"
