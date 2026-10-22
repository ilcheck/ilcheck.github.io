#!/usr/bin/env bash
# Morning pipeline: scrape 智愛 teacher reports → apply_reports → publish dashboard.
# Credentials: NEVER pass passwords on the CLI. Prefer KRU MCP terminal_run:
#   item=ilearner
#   secretEnv: ILEARNER_USERNAME=username, ILEARNER_PASSWORD=password
#   command: bash /workspace/i-learner/scrape_and_publish.sh
# Or KRU_FILL=1 with paired browser bridge + agent credential_fill.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

OVERALL_SEC="${SCRAPE_OVERALL_SEC:-720}"  # ~12 minutes
SKIP_PUBLISH="${SKIP_PUBLISH:-0}"
DRY_RUN="${DRY_RUN:-0}"

log() { echo "[scrape_and_publish] $*" >&2; }
err() { echo "[scrape_and_publish] ERROR: $*" >&2; }

cleanup() {
  local ec=$?
  if [[ $ec -ne 0 ]]; then
    err "failed (exit $ec)"
  fi
  exit "$ec"
}
trap cleanup EXIT

if [[ ! -d node_modules/playwright ]]; then
  err "Playwright not installed. Run: cd $ROOT && npm install && npx playwright install chromium"
  exit 2
fi

log "start (DRY_RUN=$DRY_RUN SKIP_PUBLISH=$SKIP_PUBLISH HEADED=${HEADED:-0} KRU_FILL=${KRU_FILL:-0})"
log "overall timeout ${OVERALL_SEC}s"

# Hard wall clock for the scrape step
set +e
timeout --signal=TERM "${OVERALL_SEC}s" env SCRAPE_TIMEOUT_MS=$((OVERALL_SEC * 1000 - 30000)) \
  node scrape_reports.mjs
scrape_ec=$?
set -e

if [[ $scrape_ec -eq 124 ]]; then
  err "scrape timed out after ${OVERALL_SEC}s"
  exit 124
fi
if [[ $scrape_ec -ne 0 ]]; then
  err "scrape_reports.mjs exited $scrape_ec"
  exit "$scrape_ec"
fi

prev="$ROOT/scrape-inbox/prev.html"
curr="$ROOT/scrape-inbox/current.html"
if [[ ! -s "$prev" || ! -s "$curr" ]]; then
  err "missing scrape-inbox HTML (prev/current)"
  exit 3
fi

# Gate publish on looking like a real report (skip for DRY_RUN stubs)
looks_real() {
  local f="$1"
  local sz
  sz=$(wc -c <"$f")
  if [[ "$sz" -lt 5000 ]]; then
    return 1
  fi
  if grep -q 'id="login_form"' "$f"; then
    return 1
  fi
  if grep -q 'DRY_RUN placeholder' "$f"; then
    return 1
  fi
  # Need at least two of these markers
  local hits=0
  grep -q '已完成練習' "$f" && hits=$((hits + 1))
  grep -q '班別' "$f" && hits=$((hits + 1))
  grep -q '學號' "$f" && hits=$((hits + 1))
  grep -q '姓名' "$f" && hits=$((hits + 1))
  grep -q 'r_teachers_report_student' "$f" && hits=$((hits + 1))
  [[ "$hits" -ge 2 ]]
}

if [[ "$DRY_RUN" == "1" ]]; then
  log "DRY_RUN: skip apply_reports + publish (structure OK)"
  exit 0
fi

log "apply_reports.py"
python3 apply_reports.py --prev-html scrape-inbox/prev.html --curr-html scrape-inbox/current.html

if [[ "$SKIP_PUBLISH" == "1" ]]; then
  log "SKIP_PUBLISH=1: built data only"
  exit 0
fi

if ! looks_real "$prev" || ! looks_real "$curr"; then
  err "HTML failed real-report gate; refusing to publish. Inspect scrape-inbox/*.html"
  exit 4
fi

log "publish_dashboard.sh"
bash publish_dashboard.sh
log "success"
