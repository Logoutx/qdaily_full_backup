#!/usr/bin/env bash
#
# Legacy migration helper retained for provenance.
#
# Migration is complete. The active qdaily project lives at:
# /Users/logoutx/Library/CloudStorage/Dropbox/Projects/qdaily_full_backup
#
# GitHub remains the cross-Mac channel for code. On another Mac, clone the repo
# into Dropbox/Projects and then set com.dropbox.ignored=1 on .git.

set -euo pipefail

PROJECT="/Users/logoutx/Library/CloudStorage/Dropbox/Projects/qdaily_full_backup"

echo "Migration already complete: $PROJECT"
echo "No files moved."
