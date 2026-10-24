# Today’s Pick operations

Codex took over daily curation and publication on 2026-10-15. The working checkout
remains `/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup`.

## Schedule and publication

Codex heartbeat `qdaily-today-s-pick` runs at 10:00, 14:00 and 18:00 Asia/Shanghai.
The first slot publishes one edition; later slots retry only if needed. An edition
already on remote main must not be recurred or overwritten. Verify the corresponding
GitHub Actions deployment and live dated page before calling publication complete.

The legacy LaunchAgent `org.qdaily.todays-pick` was disabled and unloaded on
2026-10-15 after the Codex automation became active. Its plist, launcher, old Claude
prompt and credentials are retained for rollback; scheduled Codex runs must never
invoke the old runner or read Claude/Telegram credential files. Other QDaily jobs,
including translation and Wayback work, were not transferred or modified.

## Editorial rules

Use the established exclusion tagging, hotspot fetcher and candidate builder with
`.venv/bin/python`. Read candidate article bodies and the conceptual-match rules in
`RANDOM_PAGE_PLAN.md`. Aim for 4 China-trend picks, up to 4 global-trend picks and
at least 4 timeless longforms. Backfill any trend shortfall with eligible longforms;
exactly 12 unique, nonexcluded corpus IDs are required. Failed feeds and weak matches
are not a reason to invent news or force relevance. Article titles, URLs and original
publication dates must match the corpus. Only the digest receives today's date.
New editions use `editor_id: codex-v1`.

Validate corpus identities, exclusions, bucket counts, original dates and all 12
article links before publication. Render the digest and homepage templates to a
scratch destination rather than overwriting the existing full `public/` build.
Write identical copies to `data/daily_picks.json` and
`data/daily_history/YYYY-MM-DD.json`. In routine runs, stage and commit only those
2 files, preserve unrelated translation files, and do not push unrelated commits.
Stop on unexpected branch state, tracked source edits, unrelated unpushed commits,
upstream changes or failed checks. Ordinary unstaged modifications to only
`data/translations/defer.json` and `data/translations/needs-review.json` are permitted:
the independent translation batch updates these runtime review records. Verify that
`defer.json` is a valid JSON list of numeric article IDs and `needs-review.json` is
a valid JSON object mapping numeric IDs to lists of strings. Preserve both files
without editing, staging, committing or publishing them. Staged changes, deletions,
renames and other tracked changes do not qualify. Recheck status and the exact
2-file staged allowlist before each commit and push. These 2 runtime files also do
not prevent read-only verification of an already-published edition.
Never reset, stash, force-push, skip hooks or silently merge concurrent work.

A push to main triggers the existing `.github/workflows/deploy.yml` pipeline.
Confirm the workflow succeeds and the homepage plus `/daily/YYYY-MM-DD/` are live.
Keep prior dated digests. Do not manufacture editions for missed past days.
The scheduled task cannot modify source code, workflows, credentials or other
features. Failures or requests for source repairs should be reported in the chat.

## Health monitoring

`tools/health_check.sh` checks the local `data/daily_picks.json` edition date
against Asia/Shanghai today. It retains its existing hour >= 11 grace period:
of the documented 10:45/22:45 health-check slots, only 22:45 checks daily-pick
freshness. This patch does not change the schedule or start either curator.

The checker now names the active Codex task `qdaily-today-s-pick` as the place
to inspect the current run result. There is no checked-in machine-readable
Codex run-status contract, so the checker does not infer whether that task ran,
failed authentication, or completed publication. The Today's Pick check never
reads the retired `data/todays_pick.claude.log` or AI-provider credential files.
A stale, missing, unreadable or invalid-dated edition still alerts. A current local date only
establishes local output freshness; remote main, Actions and the live dated
page still need the separate publication verification described above.

Run the isolated regression suite with:

```sh
bash -n tools/health_check.sh
python3 -m unittest discover -s tools -p 'test_health*.py'
```

Applying the repository change does not update an already-running Mac checkout.
Before local rollout, reconcile any uncommitted health-check changes, including
the separate intentional-translation-pause fix. Do not reset or overwrite those
changes. The combined local fix also skips translation heartbeat alerts when launchd
explicitly disables the translation job. If status cannot be read, existing
stall checks remain active. Other notification behavior is unchanged. Preserve the
disabled translation LaunchAgent, then dry-run with notifications stubbed before
relying on the installed checker.

## Takeover verification

The previous published edition was 2026-10-07. Legacy run markers showed repeated
curator failures through 2026-10-15. On takeover, the existing pipeline produced
206 hotspots; Weibo was unavailable while 9 other feed groups returned data.
The 2026-10-15 edition contains 4 China-trend picks, 2 global-trend picks and
6 longforms. Payload and local template checks verified 12 unique nonexcluded
corpus IDs, canonical titles/URLs, original 2014–2019 dates and correct links.
The homepage curation credit changes to Codex; the historical site-building credit
in the footer remains intact. Publication completion is recorded by the deployment
result and live URL, not this preparation note.
