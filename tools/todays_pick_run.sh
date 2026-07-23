#!/bin/bash
# qdaily Today's Pick — daily curation via ONE REUSED Claude session.
# Converted 2026-07-15 from the Claude scheduled-task `qdaily-todays-pick-daily`,
# which minted a brand-new Claude session (cluttering Recents on every Mac) each
# day. This curation genuinely needs a model, so instead of dropping Claude we
# reuse a single session: the first run captures its session_id, and every run
# after that resumes the same session — so Recents shows one ongoing entry.
#
# The prompt/logic is UNCHANGED: it is the verbatim body of the old SKILL.md,
# kept in tools/todays_pick_prompt.md.
#
# launchd gives jobs a bare PATH; set one so claude/jq/git resolve.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJ="/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup"
cd "$PROJ" || exit 1

PROMPT="$(cat tools/todays_pick_prompt.md)"
SID_FILE="data/.claude_session_id_todayspick"
LOG="data/todays_pick.claude.log"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') todays-pick run ===" >> "$LOG"

# bypassPermissions: this is an unattended job that runs python + git push, just
# as the scheduled-task did autonomously. Without it every tool call would block
# waiting for an approval that no one is there to give.
if [ -s "$SID_FILE" ]; then
  SID="$(cat "$SID_FILE")"
  echo "resuming session $SID" >> "$LOG"
  claude --resume "$SID" --permission-mode bypassPermissions -p "$PROMPT" >> "$LOG" 2>&1
else
  echo "no stored session; starting fresh and capturing session_id" >> "$LOG"
  OUT="$(claude --permission-mode bypassPermissions --output-format json -p "$PROMPT" 2>> "$LOG")"
  printf '%s\n' "$OUT" >> "$LOG"
  SID="$(printf '%s' "$OUT" | jq -r '.session_id // empty')"
  if [ -n "$SID" ]; then
    printf '%s' "$SID" > "$SID_FILE"
    echo "stored session_id $SID" >> "$LOG"
  else
    echo "WARNING: could not parse session_id; next run will start a fresh session again" >> "$LOG"
  fi
fi

# Fallback: ensure the day's picks landed in the durable digest history
# (normally the curation prompt commits it; this catches a missed step so
# /daily/<date>/ pages never vanish again).
D="$(python3 -c "import json;print(json.load(open('data/daily_picks.json')).get('date',''))" 2>/dev/null)"
if [ -n "$D" ] && [ ! -f "data/daily_history/$D.json" ]; then
  mkdir -p data/daily_history
  cp data/daily_picks.json "data/daily_history/$D.json"
  git add "data/daily_history/$D.json"
  git commit -q -m "Daily digest history: $D" && git push -q
  echo "history snapshot fallback committed for $D" >> "$LOG"
fi
