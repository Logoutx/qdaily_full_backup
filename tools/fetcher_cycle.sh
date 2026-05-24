#!/bin/bash
# 72h-off / 48h-on cycle controller for the long-scope Wayback fetcher.
#
# Bumped from 48/48 → 72/48 on 2026-05-18: with only 48h cooldown, ON #1
# delivered ~1,500 OK before throttle (vs ~6,500 for ON #0), and started
# throttled almost immediately. Wayback's per-IP rate state wasn't fully
# clearing in 48h.
#
# Fired hourly by ~/Library/LaunchAgents/org.qdaily.fetcher-cycle.plist.
# Reads the cycle anchor from data/fetcher_cycle_state (Unix epoch seconds,
# marks the start of the FIRST OFF window). Computes the current position in
# the 120-hour cycle and reconciles:
#
#   pos < 72h   →  desired state OFF  (kill fetcher if running)
#   pos ≥ 72h   →  desired state ON   (launch fetcher if not running)
#
# Logs every decision to data/fetcher_cycle.log. Safe to run repeatedly;
# convergence-style — only acts when actual state differs from desired.

set -uo pipefail

REPO="$HOME/code/qdaily_full_backup"
STATE="$REPO/data/fetcher_cycle_state"
PIDFILE="$REPO/data/fetcher.pid"
LOG="$REPO/data/fetcher_cycle.log"
FETCH_LOG="$REPO/data/fetch_images_article.log"

cd "$REPO" || { echo "[$(date '+%F %T')] repo not found" >> "$LOG"; exit 1; }

if [ ! -f "$STATE" ]; then
    echo "[$(date '+%F %T')] ERROR: $STATE missing — cannot determine cycle anchor" >> "$LOG"
    exit 1
fi

T0=$(cat "$STATE")
NOW=$(date +%s)
ELAPSED=$((NOW - T0))
CYCLE=$((120 * 3600))        # 72h off + 48h on
OFF_LEN=$((72 * 3600))
POS=$((ELAPSED % CYCLE))

if [ "$POS" -lt "$OFF_LEN" ]; then
    DESIRED="off"
    REMAINING=$((OFF_LEN - POS))
else
    DESIRED="on"
    REMAINING=$((CYCLE - POS))
fi

# Detect a live fetcher (stale PID files are ignored via kill -0).
CURRENT_PID=""
if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        CURRENT_PID="$pid"
    fi
fi

ts=$(date '+%F %T')
hrs_left=$((REMAINING / 3600))

case "$DESIRED" in
on)
    if [ -n "$CURRENT_PID" ]; then
        echo "[$ts] ON window — fetcher running (PID $CURRENT_PID, ${hrs_left}h left in window)" >> "$LOG"
    else
        # Fresh start: double-fork via tools/daemonize.py to escape launchd's
        # process group. `nohup` alone is NOT enough for launchd-spawned
        # children — the previous version of this script restarted a new PID
        # every hour because launchd killed each one when this script exited.
        # daemonize.py prints the grandchild PID to stdout.
        new_pid=$(.venv/bin/python tools/daemonize.py \
            --log "$FETCH_LOG" \
            --chdir "$REPO" \
            -- .venv/bin/python tools/fetch_images.py --scope article)
        echo "${new_pid}" > "$PIDFILE"
        echo "[$ts] ON window — started fetcher PID ${new_pid} (${hrs_left}h left in window)" >> "$LOG"
    fi
    ;;
off)
    if [ -n "$CURRENT_PID" ]; then
        kill "$CURRENT_PID"
        # Wait up to 10s for graceful exit, then SIGKILL.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$CURRENT_PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$CURRENT_PID" 2>/dev/null
        rm -f "$PIDFILE"
        echo "[$ts] OFF window — killed fetcher PID $CURRENT_PID (${hrs_left}h until next ON)" >> "$LOG"
    else
        echo "[$ts] OFF window — no fetcher running (${hrs_left}h until next ON)" >> "$LOG"
    fi
    ;;
esac
