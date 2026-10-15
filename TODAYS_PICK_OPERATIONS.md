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
Stop on unexpected branch state, local edits, upstream changes or failed checks.
Never reset, stash, force-push, skip hooks or silently merge concurrent work.

A push to main triggers the existing `.github/workflows/deploy.yml` pipeline.
Confirm the workflow succeeds and the homepage plus `/daily/YYYY-MM-DD/` are live.
Keep prior dated digests. Do not manufacture editions for missed past days.
The scheduled task cannot modify source code, workflows, credentials or other
features. Failures or requests for source repairs should be reported in the chat.

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
