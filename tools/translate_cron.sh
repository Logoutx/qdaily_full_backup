#!/bin/bash
# Scheduled translation batch: Kimi draft -> Codex polish + fidelity review.
# Existing 20-article / five-hour cadence. Local output only; no automatic Git push.
# Codex reuses ChatGPT subscription auth; no Claude token or API-key fallback.
set -o pipefail

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

cd "$PROJ" || exit 1
STARTED=$(date +%s)
log "=== translate batch start (Codex, local-only) ==="
if ! ./.venv/bin/python tools/translate_codex.py --root "$PROJ" --check-auth >>"$LOG" 2>&1; then
  log "FAIL: Codex ChatGPT authentication unavailable"
  alert "Codex ChatGPT login required; batch stopped before drafting"
  exit 1
fi

# ── Queue + Kimi drafting run HERE, in bash, not inside the agent.
# Learned the hard way 2026-08-31: when the agent was told to run the Kimi
# driver itself, the driver outlived the Bash tool's timeout, so the agent
# backgrounded it, ended its turn ("I'll wait for the completion notification"),
# and the headless process exited — killing the child and producing no
# BATCH-RESULT. Two slots failed that way. Deterministic steps belong in the
# script; the agent should only do the part that needs judgment.
if ! IDS_JSON="$(./.venv/bin/python tools/translate_todo.py --limit 20 --emit 2>>"$LOG" | tail -1)"; then
  log "FAIL: queue generation failed"
  alert "queue generation failed — check $LOG"
  exit 1
fi
# Only a valid empty array means completion; a failed/invalid queue is not success.
if ! IDS="$(printf '%s' "$IDS_JSON" | python3 -c 'import json,sys; a=json.load(sys.stdin); assert isinstance(a,list) and all(isinstance(x,str) and x.isascii() and x.isdigit() for x in a); print(" ".join(a))' 2>>"$LOG")"; then
  log "FAIL: invalid queue output"
  alert "invalid queue output — check $LOG"
  exit 1
fi
if [ -z "$IDS" ]; then
  log "queue empty — nothing to translate"
  printf '%s\nBATCH-RESULT: ok=0 failed=0 deferred=0 kimi=0 total_en=%s\n' \
    "$(date +%s)" "$(ls data/translations/en/*.json 2>/dev/null | wc -l | tr -d ' ')" > "$HEARTBEAT"
  exit 0
fi
log "queued: $IDS"

# Kimi pre-draft. Always exits 0; a quota-out or wedged Kimi just means fewer
# drafted ids and a Codex fallback for the rest.
KIMI_OUT="$(node tools/translate_draft_kimi.mjs $IDS --concurrency=3 2>&1)"
printf '%s\n' "$KIMI_OUT" >> "$LOG"
DRAFTED="$(printf '%s' "$KIMI_OUT" | grep -o 'DRAFTED_IDS=.*' | tail -1 | sed 's/^DRAFTED_IDS=//')"
[ -z "$DRAFTED" ] && DRAFTED="[]"
N_KIMI="$(printf '%s' "$DRAFTED" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
log "kimi drafts available (new or reused): $N_KIMI of $(printf '%s' "$IDS" | wc -w | tr -d ' ')"

RUN_STATUS=0
OUT="$(./.venv/bin/python -u tools/translate_codex.py --root "$PROJ" $IDS 2>&1)" || RUN_STATUS=$?
printf '%s\n' "$OUT" >> "$LOG"
RESULT="$(printf '%s' "$OUT" | grep -E '^BATCH-RESULT: ok=[0-9]+ failed=[0-9]+ deferred=[0-9]+ kimi=[0-9]+ total_en=[0-9]+$' | tail -1)"
# Outcome handling: an explicit budget stop is partial, never full success.
BATCH_STATE="$(printf '%s\n' "$OUT" | grep -E '^BATCH-STATE: status=[a-z_]+ reason=[a-z_]+ remaining=[0-9]+$' | tail -1)"
if [ "$RUN_STATUS" -eq 2 ] && [ -n "$RESULT" ] && \
   printf '%s\n' "$BATCH_STATE" | grep -Eq '^BATCH-STATE: status=partial reason=time_budget remaining=[1-9][0-9]*$'; then
  log "PARTIAL: four-hour stage budget reached; unfinished articles remain queued; $RESULT; $BATCH_STATE"
  printf '%s\n%s\n%s\n' "$(date +%s)" "$RESULT" "$BATCH_STATE" > "$HEARTBEAT"
  alert "partial batch: four-hour budget reached; unfinished articles remain queued — $RESULT"
  exit 2
fi
if [ "$RUN_STATUS" -ne 0 ] || [ -z "$RESULT" ] || \
   [ "$BATCH_STATE" != 'BATCH-STATE: status=complete reason=queue_processed remaining=0' ]; then
  log "FAIL: Codex translation stage exited $RUN_STATUS; ${RESULT:-no valid batch summary}; ${BATCH_STATE:-no valid batch state}"
  alert "Codex translation stage failed; completed local files preserved — check $LOG"
  exit 1
fi
log "$RESULT; $BATCH_STATE"
printf '%s\n%s\n%s\n' "$(date +%s)" "$RESULT" "$BATCH_STATE" > "$HEARTBEAT"

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
