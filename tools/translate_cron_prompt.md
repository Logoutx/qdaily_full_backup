Translate the next batch of QDaily articles zh→en. Work from the project root you were started in.

This mirrors the LatePost `translate-latepost` pipeline: **Kimi draft → Opus polish → deterministic QA → adversarial fidelity review**. Kimi does the first pass on a subscription, so a 20-article batch fits inside one Claude usage window instead of stalling for hours on rate limits.

STEP 1 — queue (20 per batch). Run:
  ./.venv/bin/python tools/translate_todo.py --limit 20 --emit
It prints a JSON array of ids and materializes data/translations/in/<id>.json for each. If it prints [], report "queue empty" and stop.

STEP 2 — read the rules ONCE:
  - data/translations/glossary.json — authoritative zh→en renderings, incl. the qdaily_series block for column-name suffixes.
  - data/translations/STYLE.md — both PROMPT blocks (draft + polish).

STEP 3 — pre-draft with Kimi (keeps the batch off Claude quota):
  node tools/translate_draft_kimi.mjs <the ids…> --concurrency=3
It writes data/translations/out/drafts/<id>.txt per article and validates each one (sentinels, length, image parity, no untranslated Chinese run); invalid drafts are deleted so the next stage re-drafts them properly. It runs 3 Kimi calls at a time (~8-10 min each). Kimi's own rolling 5-hour quota runs out after roughly 4 articles, so expect it to draft the first few and then stop — the rest fall back to the workflow's Sonnet draft, which is the normal, healthy outcome, not a failure. It abandons immediately on a quota error and after 2 consecutive failures of any other kind. Its last line is `DRAFTED_IDS=[…]` — the ids that have a usable draft. If Kimi is out of quota or the driver fails outright, continue with an empty drafted list; the workflow Sonnet-drafts those ids instead.

STEP 4 — draft (where needed) + polish, via the workflow:
  Workflow({ scriptPath: 'tools/translate_batch.workflow.js',
             args: { root: '<absolute project root>', ids: [<the ids>], draftedIds: [<from DRAFTED_IDS>] } })
Ids in draftedIds skip the Sonnet stage; Opus polishes the Kimi draft from disk. Wait for it to finish and note any `ok:false` or `note`.

STEP 5 — collect + QA:
  ./.venv/bin/python tools/translate_collect.py
  ./.venv/bin/python tools/translate_qa.py --write <the 20 ids>
Pass the ids as SEPARATE arguments, never as one quoted string.
If an id hard-fails QA, re-translate it once (fix the reported problem), collect + QA again. Still failing → append it to data/translations/defer.json and move on; do not loop.

STEP 6 — adversarial fidelity review (Grok). Deterministic QA cannot see fact corruption when the English is fluent. Run over the QA-clean ids, in sub-batches of ~8–10:
  grok -p "$(cat data/translations/grok-review.md)

  Review these batch ids: <space-separated ids>." --cwd . --no-memory --always-approve < /dev/null
Treat HIGH-severity findings as real defects: add those ids to data/translations/needs-review.json and re-polish them. Note LOW findings in your output for a human. If the grok CLI is unavailable, say so in your report and continue — do not silently skip this gate.

COPYRIGHT: publisher book excerpts (a "书籍摘录" heading) are now excluded from the queue deterministically by translate_todo.py — you should not see them. If one still reaches you, or an article is otherwise substantially a Chinese translation of an English-language original, do NOT translate it: append its id (as a string) to data/translations/defer.json and count it as skipped, not failed. Note the distinction: a 读书笔记 / reading-notes column, where QDaily's own writers discuss and recommend books, IS QDaily's own writing — translate it normally.

STEP 7 — publish the data:
  git add data/translations/ && git commit -m "Translate <N> articles zh->en (scheduled batch)" && git push
(data/translations is data-only; this does not trigger a site deploy.)

STEP 8 — final line of your output, exactly this shape:
  BATCH-RESULT: ok=<n_translated> failed=<n_failed> deferred=<n_deferred> kimi=<n_kimi_drafted> total_en=<count of data/translations/en/*.json>
