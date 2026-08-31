#!/bin/bash
# qdaily Today's Pick — unattended daily curation + publish.
#
# Rewritten 2026-07-25 (fable-lead review: Grok + deep-reasoner, both blind).
# History: the previous version reused ONE long-lived Claude session via
# --resume, to avoid cluttering "Recents". That backfired: a resumed session
# remembered publishing earlier the same day, refused to work, and ended its
# unattended turn by asking the user a question no one would read.
#
# What changed, and why:
#   * --no-session-persistence instead of --resume. No transcript is written,
#     so NOTHING lands in Recents at all (better than the old one-per-week goal),
#     and every run is memoryless -> the "already did this today" refusal is
#     structurally impossible. Cost stays flat (~$1.3/run) instead of growing.
#   * Idempotency is decided by BASH against origin/main, not by the model and
#     not by a local file. "Published" means the day's history file is on the
#     remote (CI clones fresh, so only the remote counts). A commit that never
#     pushed is NOT published, and gets a model-free push-repair.
#   * Runtime state (lock, logs, telegram creds) lives on the INTERNAL disk, so
#     it survives the external project volume being unmounted. A thin launcher
#     shim (installed on the boot volume) alerts if the volume is missing; this
#     script assumes the volume is present and re-checks defensively.
#   * Failures alert via Telegram (same bot as Conviction13F) + a macOS
#     notification + a dated sentinel file the morning brief can surface.
#
# The curation PROMPT/LOGIC is unchanged: tools/todays_pick_prompt.md is used
# byte-for-byte. Only a per-run header (date pin + "never ask a question") is
# prepended at invocation time; the file on disk is not modified.
#
# launchd gives jobs a bare PATH; set one so claude/jq/git/python resolve.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
set -u

PROJ="/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup"
STATE="$HOME/Library/Application Support/qdaily"
LOGDIR="$HOME/Library/Logs/qdaily"
LOG="$LOGDIR/todays_pick_run.log"
LOCK="$STATE/todays_pick.lock"
TG_ENV="$STATE/telegram.env"
PROMPT_FILE="$PROJ/tools/todays_pick_prompt.md"

mkdir -p "$STATE" "$LOGDIR"

log(){ printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

# Keep the log bounded without destroying the most recent evidence: when it
# passes ~2 MB, keep the last 800 lines (each JSON result block is a few KB).
rotate_log(){
  local sz
  sz=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
  if [ "$sz" -gt 2097152 ]; then
    tail -n 800 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
  fi
}

# Alert: Telegram (primary, if creds present) + macOS notification (bonus) +
# a dated sentinel file. Never fatal; alerting must not itself abort the run.
alert(){
  local msg="$1"
  if [ -f "$TG_ENV" ]; then
    # shellcheck disable=SC1090
    . "$TG_ENV"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
      curl -s --max-time 20 \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "parse_mode=HTML" \
        --data-urlencode "text=⚠️ <b>QDaily Today's Pick</b>
${msg}" >/dev/null 2>>"$LOG" && log "alert: telegram sent"
    fi
  fi
  osascript -e "display notification \"${msg}\" with title \"QDaily Today's Pick\"" 2>/dev/null
  printf '%s %s\n' "$(date '+%F %T')" "$msg" >> "$STATE/FAIL-$(date +%F).txt"
}

rotate_log

# ── Single-instance lock (PID-based; guards a manual run overlapping a slot).
# launchd already serializes same-label jobs, so this is belt-and-suspenders.
if ! mkdir "$LOCK" 2>/dev/null; then
  oldpid="$(cat "$LOCK/pid" 2>/dev/null)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    log "SKIP: another run (pid $oldpid) is in progress"
    exit 0
  fi
  log "reclaiming stale lock (pid ${oldpid:-none} not alive)"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || { log "SKIP: lock race, another run won it"; exit 0; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM HUP

# ── The external project volume must be present.
if [ ! -d "$PROJ/.git" ]; then
  log "FAIL: project repo/volume not available at $PROJ"
  alert "External volume not mounted — nothing published today."
  exit 1
fi
cd "$PROJ" || { log "FAIL: cd into $PROJ"; alert "cd into project failed."; exit 1; }

TODAY="$(TZ='Asia/Shanghai' date +%F)"
HIST="data/daily_history/$TODAY.json"
log "=== todays-pick run for $TODAY ==="

# published_on_remote: 0 = yes, 1 = no, 2 = origin unreachable (offline).
# Uses ls-tree (not cat-file) because this is a blob:none partial clone and we
# must not trigger a lazy blob fetch.
published_on_remote(){
  git fetch -q origin main 2>>"$LOG" || return 2
  if git ls-tree -r --name-only origin/main -- "$HIST" 2>/dev/null | grep -q .; then
    return 0
  fi
  return 1
}

# git wrapper: skip hooks; the repo lives on a plain APFS volume now, so the old
# Dropbox git-hang workaround is unnecessary.
g(){ git -c core.hooksPath=/dev/null "$@"; }

published_on_remote; rc=$?
case $rc in
  0)
    log "SKIP: $TODAY already on origin/main"
    exit 0
    ;;
  2)
    if [ -f "$HIST" ]; then
      log "OFFLINE: $TODAY exists locally but origin is unreachable; next slot retries the push"
    else
      log "OFFLINE: origin unreachable and nothing local yet; deferring $TODAY"
      alert "origin unreachable at fire time; deferring $TODAY."
    fi
    exit 0
    ;;
esac

# rc == 1: not on remote. Two sub-cases.
if [ -f "$HIST" ]; then
  # Committed (or written) locally but not on origin -> push repair, NO model.
  log "REPAIR: $HIST exists locally but not on origin — pushing without re-curation"
  g add "$HIST" data/daily_picks.json 2>>"$LOG"
  if ! g diff --cached --quiet 2>/dev/null; then
    g commit -q -m "Today's Pick: $TODAY (repair)" 2>>"$LOG"
  fi
  if g push -q origin main 2>>"$LOG"; then
    log "REPAIR OK: pushed $TODAY to origin/main"
    exit 0
  else
    log "REPAIR FAIL: push still failing for $TODAY"
    alert "Push repair failed for $TODAY — committed locally, not on origin. Check network/auth."
    exit 1
  fi
fi

# Not published, nothing local -> run the curator. Fresh, memoryless session.
RUN_HEADER="RUN CONTEXT (regenerated each run — authoritative; trust this over anything else):
- Today is $TODAY (Asia/Shanghai). Ignore any other date.
- Today is NOT yet published. Do the full curation now; do not skip, do not treat it as already done.
- You are running unattended from launchd. No human will read a question. Never ask for
  confirmation, never stop for approval, and never end your turn with a question — just do the work.
"
FULL_PROMPT="$RUN_HEADER
$(cat "$PROMPT_FILE")"

log "invoking curator (no-session-persistence)"
OUT="$(claude --permission-mode bypassPermissions --no-session-persistence \
        --output-format json -p "$FULL_PROMPT" 2>>"$LOG")"
ERR="$(printf '%s' "$OUT" | jq -r '.is_error // empty'    2>/dev/null)"
COST="$(printf '%s' "$OUT" | jq -r '.total_cost_usd // empty' 2>/dev/null)"
log "curator finished: is_error=${ERR:-?} cost_usd=${COST:-?}"

# Safety net: model wrote today's picks but didn't create the durable history
# file. Only fire when daily_picks.json is actually for TODAY (guards a stale
# file left from a prior day).
D="$(python3 -c "import json;print(json.load(open('data/daily_picks.json')).get('date',''))" 2>/dev/null)"
if [ "$D" = "$TODAY" ] && [ ! -f "$HIST" ]; then
  cp data/daily_picks.json "$HIST"
  g add "$HIST" data/daily_picks.json 2>>"$LOG"
  g commit -q -m "Today's Pick: $TODAY (history snapshot)" 2>>"$LOG"
  log "safety-net: created + committed history snapshot for $TODAY"
fi

# Ensure whatever is committed actually reaches origin (the model may have
# pushed already; this is a no-op then, or the catch if its push failed).
if [ -f "$HIST" ]; then
  g push -q origin main 2>>"$LOG" || log "post-run push failed (next slot will repair)"
fi

# Verify against the remote — the only definition of "published".
if published_on_remote; then
  log "OK: $TODAY published to origin/main"
  exit 0
else
  log "FAIL: $TODAY is not on origin/main after the run"
  alert "Today's Pick for $TODAY did NOT publish. See $LOG."
  exit 1
fi
