#!/usr/bin/env bash
#
# One-time migration: move the ACTIVE qdaily project out of ~/code into
# ~/Projects-FOB. (The active copy is NOT in Google Drive; the stale Drive copy
# at "~/My Drive/Projects/qdaily_full_backup" is a separate, commit-less
# duplicate that YOU should delete in Finder to stop Drive sync churn.)
#
# Run this ONLY after every background job is finished:
#   - the Wayback archiver (tools/wayback_submit.py) is done, and
#   - the alt-caption scheduled run has reached completion.
# It refuses to run while the archiver is still alive.
#
# The .venv is rebuilt because its stored paths are absolute and break on move.
#
set -euo pipefail

SRC="/Users/logoutx/code/qdaily_full_backup"
DST="/Users/logoutx/Projects-FOB/qdaily_full_backup"

if [ ! -d "$SRC" ]; then echo "ABORT: source $SRC not found (already moved?)."; exit 1; fi
if [ -e "$DST" ]; then echo "ABORT: destination $DST already exists."; exit 1; fi
if pgrep -f wayback_submit.py >/dev/null 2>&1; then
  echo "ABORT: wayback_submit.py is still running. Let it finish (or 'pkill -f wayback_submit') first."
  exit 1
fi

echo "Moving $SRC -> $DST (same volume; instant) ..."
mkdir -p "$(dirname "$DST")"
mv "$SRC" "$DST"
cd "$DST"

echo "Rebuilding .venv ..."
rm -rf .venv
python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt pillow

echo
echo "OK: project now at $DST; git intact:"
git -C "$DST" log --oneline -1
echo
echo "REMAINING MANUAL STEPS (cannot be scripted):"
echo "  1. App → Scheduled panel: DELETE the now-finished tasks"
echo "     'qdaily-alt-caption-batch' and 'qdaily-wayback-watchdog'"
echo "     (they reference the old $SRC path and will error if left enabled)."
echo "  2. Finder: delete the stale Google Drive copy to stop sync churn:"
echo "       /Users/logoutx/My Drive/Projects/qdaily_full_backup"
echo "  3. Reopen the project from its new home: $DST"
