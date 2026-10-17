# Scheduled translation via Codex

The existing LaunchAgent `org.qdaily.translate-cron` runs `tools/translate_cron.sh`
every 18,000 seconds. The batch limit remains 20. No scheduler reload is needed
when replacing this script. Do not kickstart a full batch for a migration test.

The launcher verifies Codex's saved ChatGPT authentication before queue materialization
or Kimi drafting. It does not source `claude.env`. Kimi's existing draft stage and
resumable draft files remain intact. `translate_codex.py` uses the official local
Codex CLI with ChatGPT authentication, strips API-key overrides, ignores user config,
and runs ephemeral read-only sessions from isolated temporary directories. It uses
`gpt-6-sol` with high reasoning, confirmed in the CLI's refreshed model catalog.

For each missing final output:

1. Polish the saved Kimi draft against the source, glossary, and both STYLE contracts;
   when no draft exists, Codex translates and polishes directly.
2. Check schema, minimum length, residual Chinese, and exact ordered image URLs.
3. Run an independent Grok fidelity-review session using the existing five-class rubric,
   glossary, and complete source/translation, plus omission, quotation and number
   checks. The isolated Grok adapter disables tools, edits, shell, MCP, web, memory,
   and subagents; API-key overrides are removed. It reuses existing browser/OIDC auth.
   Provider/authentication failures stop promotion; there is no Codex-review fallback.
4. Repair once for hard failures or HIGH findings; review again. A second failure
   leaves the article uncompleted, records it in needs-review.json and defer.json,
   and continues to the remaining queued articles. Provider/auth failures still stop the batch. LOW findings remain in receipts.
5. Run the existing deterministic QA in isolation. Atomically promote to the existing
   `en/<id>.json` format without overwriting any existing final file.

Editorial candidates are preserved in `data/translations/codex-candidates/` and reused
after reviewer failures when the source/style/glossary fingerprint still matches.
Review receipts live in `data/translations/codex-reviews/<id>.json`. Existing finals
are skipped without model calls. Provider failures stop the batch immediately. Each
Codex call has a 25-minute timeout and the stage stops starting new articles after
four hours. Partial completed files survive for the next run. Failure never advances
the successful-batch heartbeat. Queue errors are failures, not empty successful runs.

## Publication boundary

This migration writes local translation data only. It does not commit, push, deploy,
or execute the old Claude prompt's publication step. External publication requires
separate authorization. An accepted local translation is not a verified live page.
The legacy `translate_cron_prompt.md` and workflow remain for rollback but are no
longer invoked by this scheduled launcher.

## Checks and recovery

- `codex login status` must say `Logged in using ChatGPT`. Saved login status alone
  does not prove live access or quota. No API fallback, credits, or cap changes.
- `python3 tools/translate_codex.py --root "$PWD" --check-auth` checks saved auth only.
- Run tests from the migration task's `qdaily-repair` folder, where all provider calls
  are stubbed. A live pilot must use an isolated root and one existing queued input.
- The pre-migration launcher is retained in the migration task as
  `qdaily-repair/translate_cron.pre-codex.sh`; original pre-repair code is also retained.
- If login expires, the user must renew Codex login locally. If quota is exhausted,
  wait for the existing subscription window; never switch to an API key automatically.

The Grok adapter is derived from the installed `grok-opinion` skill wrapper. Its
finite input bound is 160,000 characters to accommodate a complete bilingual article
and glossary; its isolation and redaction behavior are unchanged. Wrapper regression
tests are included as `tools/test_translate_grok_review.sh`. Expired access-token
metadata alone does not establish a login failure because an existing refresh token
may be usable; let the authorized client perform normal refresh. A failed login
requires user action, never a replacement credential or API-key fallback.

Before each isolated Grok review, the adapter calls the official client’s read-only
`grok models` command in the temporary directory. This allows normal existing-login
refresh before strict mode prevents auth-file locking. No new login, grant, API key
or model generation is requested by that preflight. Preflight failure blocks review.

2026-10-16 first migrated scheduled run: started 19:26:11 Asia/Shanghai, ended
20:28:25 with 2 accepted (3660, 3551) and 3392 held after one repair. The initial
migration incorrectly stopped the whole batch on a quality failure; restored the
original defer-and-continue rule with regression tests. 3392 is now deferred with
its remaining HIGH findings, candidate preserved. No manual rerun or publication.

Known unapproved pilot 3699 is quarantined in `data/translations/held/3699/`: complete
translation, all historical review receipt copies, and a SHA-256 manifest. Canonical
review receipts and needs-review reasons remain intact. It is also deferred. Explicit
held IDs are excluded by queue selection, runner promotion/existing-output handling,
legacy collection, and final-output counts. This does not reclassify legacy files.
Release requires a deliberate repair and successful independent review; do not copy
held content into en/ or include held/ in an export.

Budget reporting repair: the four-hour stage limit is unchanged. Budget stops now
return exit 2 with BATCH-RESULT counts and BATCH-STATE status=partial,
reason=time_budget, remaining=N. The launcher logs PARTIAL and writes both lines
to the last-run heartbeat; this is not a successful/full completion. Unfinished IDs
remain queued and quality failures remain deferred. Operational failures return
exit 1 with a failed state and completed counts when available, never a partial
budget state. Full success requires exit 0 and an explicit complete state.
Validated with local provider/clock/notification fixtures; no live translation rerun.

2026-10-17 publication/pause: routine LaunchAgent org.qdaily.translate-cron is
disabled and unloaded at the user's request. Its plist and all pending/held files
are retained. Do not enable or manually invoke translations until requested.
The canonical Projects-FOB/qdaily_full_backup symlink resolves to the existing
external-volume checkout. English publication is build-only: published.json is
an explicit SHA-256 allowlist; render_translations.py rejects held/review-blocked
or changed files and never calls a model. Existing GitHub Pages deploy.yml builds
the /en/ archive and Pagefind index alongside the Chinese site. Updating the
allowlist requires a new eligibility review; do not glob candidates into a release.
