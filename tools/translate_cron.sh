#!/bin/bash
# QDaily scheduled translation batch — 20 articles per run, launchd-fired.
# Mirrors the todays-pick launcher pattern: internal-disk state, claude.env
# token (headless claude does NOT refresh the keychain OAuth token), lock to
# prevent overlap, Telegram + macOS alert on failure, resumable by design
# ("done" = data/translations/en/<id>.json exists; translate_todo skips them).
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJ="/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup"
STATE="$HOME/Library/Application Support/qdaily"
LOG="$HOME/Library/Logs/qdaily/translate_cron.log"
LOCK="$STATE/translate_cron.lock"
HEARTBEAT="$STATE/translate_last_run"   # epoch + result of the last completed batch
INTERVAL=18000                          # must match StartInterval in the plist
mkdir -p "$STATE" "$(dirname "$LOG")"

log(){ printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

alert(){ # $1 = message
  osascript -e "display notification \"$1\" with title \"QDaily 翻译批次 ⚠️\"" 2>/dev/null
  if [ -f "$STATE/telegram.env" ]; then
    . "$STATE/telegram.env"
    [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] && \
      curl -s --max-time 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=QDaily 翻译批次 ⚠️ $1" >/dev/null
  fi
}

# Volume present?
if [ ! -d "$PROJ/tools" ]; then
  log "FAIL: project volume not mounted"; alert "external volume not mounted; batch skipped"; exit 1
fi

# Overlap guard (a batch can take ~30-60 min; runs are 5 h apart, but be safe).
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  log "SKIP: previous batch (pid $(cat "$LOCK")) still running"; exit 0
fi
echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT

# Claude credential (same claude.env as todays-pick).
if [ -f "$STATE/claude.env" ]; then
  . "$STATE/claude.env"; export CLAUDE_CODE_OAUTH_TOKEN
else
  log "WARN: claude.env missing — keychain token will eventually expire headlessly"
fi

cd "$PROJ" || exit 1
STARTED=$(date +%s)
log "=== translate batch start ==="

# ── Queue + Kimi drafting run HERE, in bash, not inside the agent.
# Learned the hard way 2026-08-31: when the agent was told to run the Kimi
# driver itself, the driver outlived the Bash tool's timeout, so the agent
# backgrounded it, ended its turn ("I'll wait for the completion notification"),
# and the headless process exited — killing the child and producing no
# BATCH-RESULT. Two slots failed that way. Deterministic steps belong in the
# script; the agent should only do the part that needs judgment.
IDS_JSON="$(./.venv/bin/python tools/translate_todo.py --limit 20 --emit 2>>"$LOG" | tail -1)"
if [ -z "$IDS_JSON" ] || [ "$IDS_JSON" = "[]" ]; then
  log "queue empty — nothing to translate"
  printf '%s\nBATCH-RESULT: ok=0 failed=0 deferred=0 kimi=0 total_en=%s\n' \
    "$(date +%s)" "$(ls data/translations/en/*.json 2>/dev/null | wc -l | tr -d ' ')" > "$HEARTBEAT"
  exit 0
fi
IDS="$(printf '%s' "$IDS_JSON" | python3 -c 'import json,sys;print(" ".join(json.load(sys.stdin)))')"
log "queued: $IDS"

# Kimi pre-draft. Always exits 0; a quota-out or wedged Kimi just means fewer
# drafted ids and a Sonnet fallback for the rest.
KIMI_OUT="$(node tools/translate_draft_kimi.mjs $IDS --concurrency=3 2>&1)"
printf '%s\n' "$KIMI_OUT" >> "$LOG"
DRAFTED="$(printf '%s' "$KIMI_OUT" | grep -o 'DRAFTED_IDS=.*' | tail -1 | sed 's/^DRAFTED_IDS=//')"
[ -z "$DRAFTED" ] && DRAFTED="[]"
N_KIMI="$(printf '%s' "$DRAFTED" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
log "kimi drafted $N_KIMI of $(printf '%s' "$IDS" | wc -w | tr -d ' ')"

RUN_HEADER="RUN CONTEXT (authoritative — the deterministic steps are ALREADY DONE; do not redo them):
- Batch ids (in/<id>.json already materialized): $IDS
- Kimi has already drafted these ids into data/translations/out/drafts/<id>.txt: $DRAFTED
  Pass exactly that array as args.draftedIds. Do NOT run the Kimi driver yourself.
- Project root: $PROJ
- You are running unattended from launchd. Never ask a question, never end your turn
  with work still in progress, and NEVER background a long-running command — when your
  turn ends the process is killed. Run everything in the foreground and wait for it.
"

OUT="$(claude --no-session-persistence --permission-mode bypassPermissions \
        -p "$RUN_HEADER
$(cat tools/translate_cron_prompt.md)" < /dev/null 2>>"$LOG")"
printf '%s\n' "$OUT" >> "$LOG"

RESULT="$(printf '%s' "$OUT" | grep -o 'BATCH-RESULT: .*' | tail -1)"
if [ -z "$RESULT" ]; then
  log "FAIL: no BATCH-RESULT line (auth/spend-cap/crash?)"
  alert "batch produced no result — check $LOG (token expired? spend cap?)"
  exit 1
fi
log "$RESULT"
printf '%s\n%s\n' "$(date +%s)" "$RESULT" > "$HEARTBEAT"

# Overrun detection. launchd will NOT start a second instance of a label whose
# previous run is still going: it silently coalesces the missed fire — no log
# line, no error, just a slot that never happened. The 2026-08-31 04:51 batch ate
# 5.5 h (34 s of CPU; the rest was rate-limit waiting) and swallowed the 09:51
# fire that way. Nothing noticed. Now something does.
ELAPSED=$(( $(date +%s) - STARTED ))
if [ "$ELAPSED" -gt "$INTERVAL" ]; then
  log "OVERRUN: batch took ${ELAPSED}s > ${INTERVAL}s interval — launchd skipped at least one fire"
  alert "batch ran ${ELAPSED}s, longer than its ${INTERVAL}s slot — a scheduled run was skipped. Throughput is below the configured rate."
fi

case "$RESULT" in
  *"ok=0"*) alert "batch translated 0 articles — $RESULT" ;;
esac
exit 0
