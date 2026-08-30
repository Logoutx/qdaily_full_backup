# CLAUDE.md — QDaily archive (www.qdaily.org)

Static archive rebuild of 好奇心日报 (QDaily, 2014–2019, ~54k articles),
extracted from Wayback Machine. Python pipeline → static HTML → GitHub Pages
behind Cloudflare, images on R2 (cdn.qdaily.org), Pagefind search.

## Non-negotiable invariants (read before changing anything)

1. **Honesty rule / archive integrity**: NEVER re-date archive articles. They
   keep their true 2014–2019 dates. Only the daily digest layer is dated today.
2. **Faithful images only**: recovered images must be the original bytes (or
   visually verified matches). No Traditional-Chinese re-typeset infographics
   in the Simplified corpus.
3. **No billed API key.** All model work runs as Claude sessions/subagents on
   the Max plan. Deterministic steps are plain Python — no model calls.
4. **One model session at a time — no session fan-out.** A `parallel()` fan-out
   once caused a nine-session sprawl. Batch fan-out INSIDE one Workflow is fine.
5. **Local-test before deploy.** New pipelines render locally and get eyeballed
   before anything auto-deploys. Don't wire a new cron/auto-push until verified.
6. **Translations are labeled machine-translated; Chinese stays authoritative.**
   Every batch passes `tools/translate_qa.py` before collection.

## Build & verify

```bash
make site          # render + Pagefind index          (venv: make venv first)
make serve         # preview at localhost:8765
python tools/validate_corpus.py        # Layer 1: corpus invariants (CI gate)
python tools/corpus_stats.py --check   # Layer 2: drift vs baseline (CI gate)
python tools/translate_qa.py --write --all   # deterministic zh→en QA gate
```

CI: `validate.yml` (data-correctness gate, every PR) and `deploy.yml`
(main-branch push → R2 sync → render → Pagefind → Pages → Cloudflare purge).
**Deploys take 10–40 min.** Local venv may be Python 3.14; CI is 3.12
(embeddings need ≤3.12 — see RANDOM_PAGE_PLAN.md).

## Data flow

- Corpus: `data/articles_extracted_<YYYY_Qn>.jsonl` (quarterly shards) +
  `articles_extracted_extra.jsonl`; exclusions in `data/excluded_ids.txt`.
- `data/images.jsonl` is in **Git LFS** (deploy checkout needs `lfs: true`).
- `assets/<id>/<sha1[:16]><ext>` = recovered images, mirrored to R2 on deploy.
- Daily 06:00 launchd job on the Mac commits new fetcher images
  (`tools/daily_push.sh` → "Mirror +N imgs" commits). Don't fight it: keep
  unrelated work out of `assets/` + `data/images.jsonl` staging.
- Today's Pick: launchd → `tools/launch_todays_pick.sh` (boot-volume shim:
  mount check + claude.env auth) → `todays_pick_run.sh` (one REUSED headless
  Claude session, id in `data/.claude_session_id_todayspick`) runs the prompt
  in `tools/todays_pick_prompt.md`: `fetch_hotspots.py` + `build_daily_picks.py`
  (model-free candidates) → curator applies the "don't force it" skip gate →
  commits `data/daily_picks.json` + a permanent `data/daily_history/<date>.json`
  → deploy. `tools/health_check.sh` (launchd 10:45/22:45) alerts on staleness.
- zh→en: `translate_todo.py --limit N --emit` → `data/translations/in/<id>.json`
  → Workflow `tools/translate_batch.workflow.js` (Sonnet draft → Opus polish;
  prompts live in `data/translations/STYLE.md` between `<!-- PROMPT:* -->`
  markers — **edit prompts there, not in the .js**) → `out/<id>.txt` →
  `translate_collect.py` → `en/<id>.json`. Resumable: done = `en/<id>.json` exists.
  Scheduled: launchd every 5h → `tools/translate_cron.sh` (lock, claude.env,
  alerts) runs `tools/translate_cron_prompt.md` headless — one session does
  draft+polish for 20 articles, ends with a `BATCH-RESULT: ok=… failed=…` line
  the shell asserts on (no line ⇒ alert).
- Alt text: `build_alt_worklist.py` → `tools/wf_alt_caption.js` (Sonnet won the
  A/B vs Haiku — `wf_ab_test.js`) → `data/alt_parts/*.tsv` → `merge_alt_parts.py`.

## Conventions

- **Plan docs** carry dated decision entries ("Decisions (2026-06-17)"), build
  order with gates, and a v2 parking lot. Campaign docs end with a tally table
  + strategic takeaway. See `docs/PROMPT_PLAYBOOK.md` for the templates.
- **Commits**: what + why + numbers ("Recover 396 in-article images: …").
- **Batch jobs** must be file-based, idempotent, resumable (cursor/queue on
  disk, "output exists = done"), with a per-run budget (`--limit`,
  `--max-seconds`) so scheduled runs can't overrun.
- `HANDOFF.md` (gitignored) is the scratch handoff doc between local sessions.

## Gotchas already paid for (don't rediscover)

- GitHub Actions: `secrets` context is invalid in `if:` — map to `env` first.
- Baidu: non-ICP quota is 0 (push API useless); it rejects index sitemaps —
  submit `sitemap-flat.xml`. Bing+IndexNow is the strongest China channel.
- web.archive.org is GFW-blocked → never hot-link Wayback for CN users.
- Workflow `args` with `scriptPath`: args may arrive as a JSON **string** —
  parse defensively (see translate_batch.workflow.js) or bake constants.
- Vision batches: caption from downscaled thumbs, ~10 images/agent.
- Headless `claude -p` does **not** refresh the keychain OAuth token — its
  expiry silently killed 17 days of scheduled picks (2026-07-29→08-15). Mint a
  long-lived token with `claude setup-token` → claude.env (see
  `tools/claude.env.template`); never rely on the keychain for launchd jobs.
- A macOS notification on an unattended Mac is not an alert. Configure the
  Telegram channel in health_check (`data/.telegram`) and test it by forcing a
  failure; monitor **output freshness** (picks date == today), not exit codes.
- Ops scripts live on an external volume — launchd must exec the boot-volume
  shim (`launch_todays_pick.sh`), which alerts if the volume isn't mounted.
- The safety classifier can block a rare article's translation — after 2
  attempts, defer the id (`data/translations/defer.json`) and move on.
- **Copyright gate**: 书摘/book-excerpt articles are zh translations of English
  originals — translating them "back" would reconstruct copyrighted prose.
  Defer, never translate (the cron prompt self-defers this class).

## Docs index

| Doc | What |
|---|---|
| `RANDOM_PAGE_PLAN.md` | Today's Pick design, decisions, skip-gate rules |
| `REPUBLISHER_RECOVERY.md` | Image-recovery campaign log + source playbook |
| `CHINA_ACCESS.md` | China reachability + SEO runbook |
| `MISSING_AUTHORS.md` | Byline/team-page research |
| `docs/PROMPT_PLAYBOOK.md` | Goal/prompt templates + loop recipes for Claude sessions |
