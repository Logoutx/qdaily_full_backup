#!/bin/bash
# Internal-disk launcher for QDaily Today's Pick.
#
# This thin shim is INSTALLED ON THE BOOT VOLUME (~/Library/Application Support/
# qdaily/) and is what the launchd plist actually execs. Its only job: confirm
# the external project volume is mounted, then hand off to the real script that
# lives in the repo. If the volume is missing, launchd could not otherwise exec
# a script that lives on that volume — the run would fail silently. Here we can
# still fire an alert.
#
# Source of truth is tools/launch_todays_pick.sh in the repo; reinstall with:
#   cp "<repo>/tools/launch_todays_pick.sh" \
#      "$HOME/Library/Application Support/qdaily/launch_todays_pick.sh"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJ="/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup"
STATE="$HOME/Library/Application Support/qdaily"
LOGDIR="$HOME/Library/Logs/qdaily"
LOG="$LOGDIR/todays_pick_run.log"
TG_ENV="$STATE/telegram.env"
mkdir -p "$STATE" "$LOGDIR"

log(){ printf '%s [launcher] %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

if [ ! -x "$PROJ/tools/todays_pick_run.sh" ]; then
  log "FAIL: external volume not mounted / script missing at $PROJ"
  if [ -f "$TG_ENV" ]; then
    # shellcheck disable=SC1090
    . "$TG_ENV"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
      curl -s --max-time 20 \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=⚠️ QDaily Today's Pick: external volume not mounted at fire time; nothing published." \
        >/dev/null 2>>"$LOG"
    fi
  fi
  osascript -e 'display notification "External volume not mounted" with title "QDaily Today'\''s Pick"' 2>/dev/null
  exit 1
fi

# Claude credential for headless runs. A launchd job does NOT inherit a login
# shell env, and headless `claude -p` does NOT refresh the keychain OAuth token
# the desktop app uses — that token expiring on 2026-07-29 silently killed every
# run for 17 days. `claude setup-token` mints a long-lived token; store it as
#   CLAUDE_CODE_OAUTH_TOKEN=<token>
# in claude.env (internal disk, never committed) and every run picks it up.
CLAUDE_ENV="$STATE/claude.env"
if [ -f "$CLAUDE_ENV" ]; then
  # shellcheck disable=SC1090
  . "$CLAUDE_ENV"
  export CLAUDE_CODE_OAUTH_TOKEN
else
  log "WARN: $CLAUDE_ENV missing — relying on keychain, which expires and does not auto-refresh headlessly"
fi

exec /bin/bash "$PROJ/tools/todays_pick_run.sh"
