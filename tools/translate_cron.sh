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
OUT="$(claude --no-session-persistence --permission-mode bypassPermissions \
        -p "$(cat tools/translate_cron_prompt.md)" < /dev/null 2>>"$LOG")"
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
