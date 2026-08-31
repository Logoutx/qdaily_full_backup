#!/bin/bash
# qdaily health check — alerts ONLY when something is wrong (silence = healthy).
# Run by launchd (org.qdaily.health-check) at 10:45 and 22:45 daily.
#
# Channels:
#   * macOS notification (always)
#   * Telegram (if ~/Library/Application Support/qdaily/telegram.env is set —
#     the same bot and same file the Today's Pick and translation jobs use)
#
# Each distinct problem alerts at most once per day (state in data/.health_alerted).
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJ="/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup"
STATE="$PROJ/data/.health_alerted"
PROBLEMS=()

notify() { # $1 = message
  osascript -e "display notification \"$1\" with title \"QDaily ⚠️\"" 2>/dev/null
  # Single credential source, shared with todays_pick_run.sh and translate_cron.sh.
  # Lives on the internal disk, never in this repo (which is public).
  local env="$HOME/Library/Application Support/qdaily/telegram.env"
  if [ -f "$env" ]; then
    # shellcheck disable=SC1090
    . "$env"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
      curl -s --max-time 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=QDaily ⚠️ $1" >/dev/null
    fi
  fi
}

add() { PROBLEMS+=("$1"); }

# 0. Project volume mounted at all?
if [ ! -d "$PROJ" ]; then
  notify "External volume not mounted — every qdaily job is dead. Reconnect 'iMac 1TB'."
  exit 0   # nothing else is checkable
fi
cd "$PROJ" || exit 0

TODAY="$(TZ='Asia/Shanghai' date +%F)"
HOUR="$(TZ='Asia/Shanghai' date +%H)"

# 1. Today's Pick ran? (only meaningful after the 10:00 slot has passed)
if [ "$HOUR" -ge 11 ]; then
  PDATE="$(python3 -c "import json;print(json.load(open('data/daily_picks.json')).get('date',''))" 2>/dev/null)"
  if [ "$PDATE" != "$TODAY" ]; then
    REASON="$(grep -E "401|403|Failed to authenticate|error" data/todays_pick.claude.log 2>/dev/null | tail -1 | cut -c1-90)"
    add "Today's Pick did not run today (still $PDATE). ${REASON:+Last error: $REASON}"
  fi
fi

# 2. launchd job health (non-zero last exit)
while read -r _pid code label; do
  [ "$code" != "0" ] && [ "$code" != "-" ] && add "launchd job $label failing (exit $code)."
done < <(launchctl list | awk '/org\.qdaily/ {print $1, $2, $3}')

# 3. Wayback: worker down while work remains (watchdog should have restarted it)
DONE="$(wc -l < data/wayback_submitted.txt 2>/dev/null | tr -d ' ')"; DONE=${DONE:-0}
if [ $((54762 - DONE)) -gt 20 ] && ! pgrep -f "wayback_submit.py" >/dev/null; then
  add "Wayback submitter down at $DONE/54762 and watchdog hasn't revived it."
fi

# 3b. Translation pipeline alive? The cron fires every 5 h and stamps
# $HEARTBEAT on completion. launchd silently coalesces a fire whose previous run
# is still going, so a wedged batch produces NO error anywhere — it just goes
# quiet. Anything past two intervals means the pipeline has stopped producing.
HB="$HOME/Library/Application Support/qdaily/translate_last_run"
QLEFT="$(./.venv/bin/python tools/translate_todo.py 2>/dev/null | awk -F'remaining: ' '/^queue:/{print $2+0}')"
if [ "${QLEFT:-0}" -gt 0 ]; then
  if [ ! -f "$HB" ]; then
    add "Translation cron has never recorded a completed batch (no heartbeat file)."
  else
    AGE=$(( $(date +%s) - $(head -1 "$HB") ))
    if [ "$AGE" -gt 39600 ]; then   # 11 h = two 5 h slots plus slack
      add "Translation cron stalled: last completed batch was $((AGE/3600)) h ago ($QLEFT articles still queued)."
    fi
  fi
fi

# 4. Latest deploy failed?
CONC="$(gh run list --workflow=deploy.yml --limit 1 --json conclusion -q '.[0].conclusion' 2>/dev/null)"
[ "$CONC" = "failure" ] && add "Latest GitHub Pages deploy FAILED — site may be stale. Check Actions."

# 5. Site up?
CODE="$(curl -so /dev/null -w '%{http_code}' -m 20 https://www.qdaily.org/ 2>/dev/null)"
case "$CODE" in 200|301|302) ;; *) add "www.qdaily.org returned HTTP ${CODE:-timeout}." ;; esac

# Dedup (one alert per problem per day) + send
touch "$STATE"
for p in "${PROBLEMS[@]}"; do
  key="$TODAY $(printf '%s' "$p" | shasum | cut -c1-10)"
  if ! grep -q "^$key" "$STATE" 2>/dev/null; then
    echo "$key $p" >> "$STATE"
    echo "$(date '+%F %T') ALERT: $p"
    notify "$p"
  else
    echo "$(date '+%F %T') (already alerted today): $p"
  fi
done
[ ${#PROBLEMS[@]} -eq 0 ] && echo "$(date '+%F %T') all healthy"
# keep state small
tail -200 "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
