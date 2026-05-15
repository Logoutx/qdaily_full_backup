#!/bin/bash
# Daily auto-push: stage newly-fetched images + the updated images.jsonl,
# commit with a stat-rich message, and push to GitHub. The deploy workflow
# then mirrors the new files to R2 and rebuilds the site.
#
# Fired by ~/Library/LaunchAgents/org.qdaily.daily-push.plist at 06:00.
# If the Mac is asleep at fire time, launchd defers to the next wake.
#
# Idempotent: exits 0 with no work if there's nothing to commit.

set -uo pipefail

REPO="$HOME/code/qdaily_full_backup"
LOG="$REPO/data/daily_push.log"

cd "$REPO" || { echo "[$(date '+%F %T')] repo not found" >> "$LOG"; exit 1; }

{
    echo ""
    echo "=== $(date '+%F %T %z') daily push run ==="
} >> "$LOG"

# Always use /usr/bin/git — launchd doesn't inherit interactive shell PATH.
GIT=/usr/bin/git

# Stage new images and the manifest. (assets/ and data/images.jsonl are the
# only things the fetcher touches; everything else stays out of this commit.)
"$GIT" add assets/ data/images.jsonl >> "$LOG" 2>&1

# Nothing staged → exit cleanly.
if "$GIT" diff --cached --quiet; then
    echo "  no changes — skipping commit" >> "$LOG"
    exit 0
fi

# Build a short stat for the commit message.
NEW_FILES=$("$GIT" diff --cached --name-only --diff-filter=A -- 'assets/**' | wc -l | tr -d ' ')
JSONL_LINES=$("$GIT" diff --cached --numstat data/images.jsonl | awk '{print $1}')
JSONL_LINES=${JSONL_LINES:-0}

MSG="Mirror +${NEW_FILES} imgs from long-scope fetcher (daily auto-push)

${NEW_FILES} new assets, +${JSONL_LINES} images.jsonl rows since last commit.
"

# `git commit` with a HEREDOC for clean multi-line message.
"$GIT" commit -m "$MSG" >> "$LOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "  commit failed (rc=$RC) — aborting" >> "$LOG"
    exit "$RC"
fi

"$GIT" push >> "$LOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "  push failed (rc=$RC) — commit landed locally but not pushed" >> "$LOG"
    exit "$RC"
fi

echo "  pushed ($("$GIT" rev-parse --short HEAD))" >> "$LOG"
exit 0
