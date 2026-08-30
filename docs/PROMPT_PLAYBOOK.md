# Prompt & loop playbook for this repo

How to open, aim, and automate Claude Code sessions on the QDaily archive.
Everything here is distilled from what already worked in this repo (and from
the incidents that didn't). CLAUDE.md carries the always-loaded rules; this
file is the how-to you copy templates from.

---

## 1. Goal setting: the initiative one-pager

`RANDOM_PAGE_PLAN.md` is the house pattern — every multi-session initiative
gets one before code is written. Template:

```markdown
# <Initiative> — design & plan

<One paragraph: what, for whom, why now.>

## Decisions (<date>)
- **<axis>: <choice>** — <one-line reason>.        # append, never rewrite

## Invariants (non-negotiable)
<The rules that survive every pivot — e.g. the honesty rule.>

## Cost model
<Which steps are model-free Python, which need one Claude session,
 which run as a Workflow batch. Default: model only where judgment is.>

## v1 pipeline
1. `tools/<x>.py` (model-free) — …
2. <the one model step> — …

## Build order
v1a <step> (local test) → v1b … → v1c … → only then go live.
Gate between steps: <what must be verified before advancing>.

## Definition of done (v1)
- [ ] <observable check — a command, a URL, a number>
- [ ] <QA gate passes>

## Instrumentation (from day one)
<What to measure so v2 decisions are empirical, not vibes.>

## v2 (later, not now)
<Parking lot. Keeping it here is what keeps v1 lean.>

## Kill criteria
<What result means "stop investing" — cf. TOPYS: 150-sample, 0 hits, stop.>
```

Two habits that make this work:

- **Dated, append-only decisions.** "Decisions (2026-06-17)" entries let a
  future session (or a future you) trust the doc without replaying the debate.
- **Explicit gates in the build order.** "Do NOT auto-deploy until verified
  locally" written in the plan is what keeps an eager session from wiring the
  cron on day one.

Campaign-style work (recovery sweeps, audits) instead ends with a **tally
table + strategic takeaway** (see REPUBLISHER_RECOVERY.md) — the takeaway is
what compounds ("check for filename preservation first").

---

## 2. Opening a session: the kickoff prompt

Sessions that start with a paragraph of context beat sessions that start with
a sentence. Template — five fields, most of them one line:

```text
GOAL: <one sentence, with a number if possible>
CONTEXT: read <plan doc / section> first. Relevant: <files/tools>.
CONSTRAINTS: <invariants that apply here — honesty rule, no billed key,
  batch sizes, "don't touch assets/ staging (daily push owns it)">
DONE WHEN: <observable — command output, page renders, QA gate green>
VERIFY BY: <the exact commands — validate_corpus.py, translate_qa.py,
  make site + eyeball URL X>
NON-GOALS: <what NOT to do — the v2 parking lot, files not to touch>
```

Filled example (a real session shape from this repo):

```text
GOAL: recover missing body images for QDaily articles from stockfeel.com.tw
  (~235 republished articles, WP REST author 73).
CONTEXT: read REPUBLISHER_RECOVERY.md — especially the mapping caveat and the
  Manager Today buckets. Corpus loads via tools/validate_corpus.py:load_corpus.
CONSTRAINTS: faithful images only (original bytes); exact filename mapping
  only — no positional guessing without hand-verification; write to
  assets/<id>/<sha1[:16]><ext>; append provenance to recovered_republisher.jsonl.
DONE WHEN: recovered images render locally via --image-mode local on 3
  spot-checked articles, and the tally table in the doc is updated.
VERIFY BY: make site && make serve, spot-check /articles/42318/.
NON-GOALS: don't start on digitaling/sohu; don't deploy — daily push handles it.
```

Why each field earns its line:

- **GOAL with a number** turns "make progress" into "know when you're done".
- **CONTEXT as reading list** — pointing at the plan doc replays every prior
  decision for free. This is why the one-pagers pay rent.
- **DONE WHEN / VERIFY BY** — a session that knows its acceptance test runs it
  unprompted and reports pass/fail instead of "should work now".
- **NON-GOALS** — the cheapest scope-creep insurance there is.

For big features, run the kickoff in **plan mode** first and have the session
write/update the one-pager before any code. End long sessions by updating
`HANDOFF.md` (gitignored) with state + next step — the next session's CONTEXT
line then costs one pointer.

---

## 3. Batch/subagent prompts: the contract checklist

The two production workflows (`translate_batch.workflow.js`,
`wf_alt_caption.js`) converged on the same prompt anatomy. Reuse it for any
new fan-out task:

1. **Role + scope, one line.** "You write accessibility alt text for … ONE
   batch of images."
2. **Numbered read-first steps** with exact paths/commands (`sed -n '301,400p'
   data/alt_worklist.jsonl`). Agents fetch their own inputs from disk —
   nothing large flows through the prompt or args.
3. **Exact output contract**: sentinel format or file path spelled out
   byte-for-byte (`@@QD_TITLE@@…`), or a `schema:` for structured return.
   Never "return the translation" — always the envelope.
4. **Per-item failure rule**: "if Read fails, use an empty alt for that url;
   write every url anyway." One bad item must not sink a batch.
5. **Self-check before returning**: "verify EVERY image ![…](url) from the
   source is present in the output." Cheap, catches the classic drop.
6. **Small structured status back** (`{id, ok, note}`), not prose — the
   workflow script does the bookkeeping.

Supporting rules:

- **Prompts live in versioned files, not in code.** STYLE.md's
  `<!-- PROMPT:draft:start -->` blocks are the model: edit prompts without
  touching the workflow, diff them in git, and they take effect next batch.
  New model-facing prompts (curator, captions) should get the same treatment.
- **Batch sizing**: ~10 vision items per agent on downscaled thumbs; text
  batches sized so one item's failure wastes little. When unsure, A/B a small
  run first (`wf_ab_test.js` pattern: 15 diverse items, two models,
  side-by-side table) — that's how Sonnet was picked for captions.
- **Model tiering**: Haiku for mechanical, Sonnet for volume work, Opus for
  judgment/polish. Two-pass (Sonnet draft → Opus polish) beats one Opus pass
  per token for translation-shaped work.
- **args + scriptPath gotcha**: args can arrive as a JSON string — parse
  defensively (translate_batch does) rather than baking constants that a
  scheduler must edit in source.

---

## 4. Loop coding: the three loop layers

This repo already runs three kinds of loops. Name the layer before building —
each has different tooling and different safety rules.

| Layer | Cadence | Model? | Existing examples | Right tool |
|---|---|---|---|---|
| **Machine loop** | minutes–daily | never | `daily_push.sh` (launchd 06:00), CI deploy+validate, `fetcher_cycle.sh`, `health_check.sh` | launchd / cron / Actions |
| **Editorial loop** | daily, one shot | one session | Today's Pick curator (launchd → headless `claude -p` on `todays_pick_prompt.md`) | scheduled headless session, versioned prompt |
| **Grind loop** | until a queue is dry | one bounded session/batch per iteration | zh→en backfill (launchd 5h headless cron — live), alt-caption sweep | headless cron (unattended) / `/loop` (attended) |

Rules (each learned the hard way once):

1. **One model loop at a time.** The nine-session sprawl came from fanning out
   sessions; fan out *inside* one Workflow instead.
2. **State on disk, never in the conversation.** Queue/cursor files
   (`queue.json`, done = output-file-exists) make every iteration resumable
   and every crash harmless.
3. **Budget every iteration**: `--limit N` items or `--max-seconds` wall clock
   (wayback_submit pattern), so a scheduled run can't overrun its window.
4. **QA gate between produce and publish** (`translate_qa.py` before collect;
   "verify ids exist" before daily_picks.json lands).
5. **Commit is the checkpoint.** Each iteration ends in a small commit — the
   loop can die anywhere and lose ≤1 iteration.
6. **Explicit stop condition**: queue empty, or K consecutive no-op
   iterations → stop, don't idle forever.
7. **Kill switch**: `touch data/.pause` → loop exits at the next iteration
   boundary. Cheaper than hunting a runaway scheduler.
8. **Machine loops stay model-free.** If a step is deterministic, it's Python
   under launchd/CI — a model in the loop is only for judgment steps.
9. **Monitor the output, not the process.** "Is `daily_picks.json` dated
   today?" catches every upstream failure mode at once — auth expiry, volume
   unmounted, scheduler dead — where exit codes catch only their own step.
   (health_check.sh check #1 is exactly this; it's the check that matters.)
10. **Alerts must reach you off-machine.** The 2026-07/08 outages (7 days
    around the scheduler conversion, then 17 days of expired-token failures)
    happened *with* local checks in place — a macOS toast on an unattended
    Mac is silence. Wire the Telegram channel (`data/.telegram`), then **test
    it by forcing a failure**; an untested alert path is a decorative one.
    For belt-and-braces, a dead-man's switch (e.g. healthchecks.io pinged at
    the end of each successful run) alerts even when the whole Mac is off.
11. **Expiring credentials are scheduled outages.** Headless `claude -p`
    doesn't refresh the keychain token — use `claude setup-token` → claude.env
    for anything launchd runs. Audit every unattended job for "what here can
    expire?" (tokens, certs, disk, API quotas) the day you wire it.
12. **Every headless model run ends in a machine-parseable result line**
    (`BATCH-RESULT: ok=… failed=…`) that the wrapping script greps for:
    absence ⇒ alert (auth death, crash, spend cap — all caught at once);
    and a *healthy* empty result must say so explicitly (`queue=empty`),
    so done never masquerades as dead. `translate_cron.sh` is the
    reference; `todays_pick_run.sh` should adopt a `PICK-RESULT` line too —
    instant detection beats waiting for the 10:45 freshness check.

### Recipe A — zh→en backfill drip (LIVE since 2026-08-30; refine it)

Implemented as launchd every 5h → `tools/translate_cron.sh` →
`tools/translate_cron_prompt.md`: queue via `translate_todo.py --limit 20
--emit`, one headless session does draft+polish per STYLE.md, bounded QA
retry (once, then flag), commit+push, and — the key move — a final
**`BATCH-RESULT: ok=<n> failed=<n> total_en=<n>`** line that the shell
greps for and alerts on when absent. That result-line contract (rule 12)
is what the 17-day pick outage lacked; treat `translate_cron.sh` as the
reference implementation for every future headless run. Refinements:

- **Queue-empty currently looks like death.** The prompt says report
  "queue empty" and stop — which emits no BATCH-RESULT line, so once the
  backfill finishes (~a month at 4×20/day) every 5h run will fire the
  failure alert. Emit `BATCH-RESULT: ok=0 failed=0 queue=empty` instead,
  and have the shell treat `queue=empty` as success (alert once —
  "backfill complete" — then unload or thin the schedule).
- **The two model loops can collide.** The 5h cadence will sometimes
  overlap the ~10:00 pick session; `translate_cron.lock` only guards
  against itself. Share one lock (e.g. `$STATE/claude_headless.lock`,
  wait-or-skip) across both runners — that's invariant #4 for schedulers —
  and guard the git race: both push to main, so `git pull --rebase` before
  push (or retry-on-reject) in both prompts.
- **Watch late-batch drift at 20/session.** One context now holds 20
  translations; QA pass-rate by batch position (first 5 vs last 5) tells
  you whether 20 is free or 10 was safer — the wf_ab_test muscle, applied
  to batch size.
- **The architecture change deserves a dated decision entry.** The cron
  replaced Sonnet-draft→Opus-polish (two models, Workflow) with one
  session wearing both hats; it lives only in a commit message. Record it
  in STYLE.md with the why, and spot-check a few cron outputs against
  Workflow outputs before deleting the old path.
- **defer.json is accumulating classes** (safety-classifier, copyright
  gate) as bare ids — keep a reason per id (or a sidecar note) so a future
  sweep can revisit deliberately instead of re-litigating each one.

### Recipe B — alt-caption sweep

Same shape; the cursor already exists (`data/alt_caption_cursor.txt`). The
iteration reads the cursor, runs `wf_alt_caption.js` for the next window,
`merge_alt_parts.py`, advances the cursor, commits `data/image_alts.json`.
Prefer passing the window via `args` over editing START/END in source — the
source edit makes every run a diff on a tracked file.

### Recipe C — the daily curator (running since 2026-07; harden it)

This loop now has the right shape: the prompt is versioned
(`tools/todays_pick_prompt.md`), launchd execs a boot-volume shim (mount
check + claude.env auth) → `todays_pick_run.sh`, which resumes ONE reused
headless session; picks land in `daily_picks.json` plus a permanent
`daily_history/<date>.json`, and `health_check.sh` watches freshness.
Three refinements worth making:

- **Rotate the reused session.** Resuming the same session forever means the
  conversation grows (and gets summarized) every day — slow drift, rising
  cost, and one stale id away from a fresh-start surprise. A monthly
  `rm data/.claude_session_id_todayspick` (or after ~30 runs) keeps each
  month's context clean while preserving the one-entry-in-Recents win.
- **Narrow `bypassPermissions` to an allowlist.** The curator reads hotspot
  titles from external feeds — untrusted text — inside a session that may run
  any command. `--allowedTools` (or a project settings.json allowlist)
  covering exactly the python/git commands in the prompt gives the same
  unattended flow with a far smaller blast radius if a feed ever carries
  something prompt-shaped.
- **Point `editor_id` at the prompt version** (e.g. `v1-<short git hash of
  todays_pick_prompt.md>`): prompt tweaks then become attributable in GSC
  data, which is the instrumentation the planned editor-tournament needs.

### Recipe D — deploy babysitter (occasional, attended)

After landing something user-visible: `/loop 5m` → check the Actions run for
main; when green, `curl -sI https://www.qdaily.org/ | grep cf-cache-status`
twice (expect MISS→HIT), spot-check the changed page, report, stop. Bounded:
give up with a diagnosis after ~5 failed iterations. (Deploys run 10–40 min —
poll accordingly, don't tight-loop.)

### Which mechanism, when

- **`/loop N`** — you're at the keyboard, want progress reports, might steer.
- **Routine / scheduled session** — unattended recurring (the curator; an
  overnight drip). One firing = one bounded iteration, never "run until done".
- **Workflow `resumeFromRunId`** — a batch died mid-run; resume replays the
  finished agents from cache and only re-runs the rest.
- **launchd / CI** — no judgment in the step. Keep the model out of it.

---

## 5. Session hygiene (small, compounding)

- **CLAUDE.md is the memory.** When a session teaches you a rule ("Baidu
  rejects index sitemaps"), it goes in CLAUDE.md's gotchas the same day —
  that's the difference between paying for a lesson once and annually.
- **`HANDOFF.md`** (gitignored) at end of long sessions: current state, next
  step, open questions. The next kickoff's CONTEXT line is then one pointer.
- **Ask for options before large refactors** ("propose 2–3 approaches with
  trade-offs, wait for my pick") — the 5-method matching bake-off judged by
  adversarial lenses is the deluxe version and it produced the best design
  decision in the repo.
- **Permissions**: consider a `.claude/settings.json` allowlist for the
  read-only commands every session runs (`python tools/validate_corpus.py`,
  `make serve`, `git status/diff/log`) to cut prompt fatigue on the Mac.
