Translate the batch of QDaily articles named in the RUN CONTEXT above, zh→en. Work from the project root you were started in.

This mirrors the LatePost `translate-latepost` pipeline: **Kimi draft → Opus polish → deterministic QA → adversarial fidelity review**. The queue step and the Kimi draft pass have ALREADY been run for you by the cron script — the ids and the drafted-ids array are in the RUN CONTEXT. Do not re-run them.

NEVER background a long-running command. This is a headless run: when your turn ends the process is killed, and anything still running dies with it, producing no result. Run every command in the foreground and wait for it to finish.

STEP 1 — read the rules ONCE:
  - data/translations/glossary.json — authoritative zh→en renderings, incl. the qdaily_series block for column-name suffixes.
  - data/translations/STYLE.md — both PROMPT blocks (draft + polish).

STEP 2 — draft (where needed) + polish, via the workflow:
  Workflow({ scriptPath: 'tools/translate_batch.workflow.js',
             args: { root: '<the project root from RUN CONTEXT>',
                     ids: [<the batch ids>],
                     draftedIds: [<exactly the array from RUN CONTEXT>] } })
Ids in draftedIds skip the Sonnet stage; Opus polishes the Kimi draft from disk. The rest are Sonnet-drafted then polished. Wait for it to finish and note any `ok:false` or `note`.

STEP 3 — collect + QA:
  ./.venv/bin/python tools/translate_collect.py
  ./.venv/bin/python tools/translate_qa.py --write <the batch ids>
Pass the ids as SEPARATE arguments, never as one quoted string.
If an id hard-fails QA, re-translate it once (fix the reported problem), collect + QA again. Still failing → append it to data/translations/defer.json and move on; do not loop.

STEP 4 — adversarial fidelity review (Grok). Deterministic QA cannot see fact corruption when the English is fluent. Run over the QA-clean ids, in sub-batches of ~8–10:
  grok -p "$(cat data/translations/grok-review.md)

  Review these batch ids: <space-separated ids>." --cwd . --no-memory --always-approve < /dev/null
Treat HIGH-severity findings as real defects: add those ids to data/translations/needs-review.json and re-polish them. Note LOW findings in your output for a human. If the grok CLI is unavailable or errors, say so explicitly in your report and continue — do not silently skip this gate.

COPYRIGHT: publisher book excerpts (a "书籍摘录" heading) are excluded from the queue deterministically by translate_todo.py — you should not see them. If one still reaches you, or an article is otherwise substantially a Chinese translation of an English-language original, do NOT translate it: append its id (as a string) to data/translations/defer.json and count it as skipped, not failed. Note the distinction: a 读书笔记 / reading-notes column, where QDaily's own writers discuss and recommend books, IS QDaily's own writing — translate it normally.

STEP 5 — publish the data:
  git add data/translations/ && git commit -m "Translate <N> articles zh->en (scheduled batch)" && git push
(data/translations is data-only; this does not trigger a site deploy.)

STEP 6 — final line of your output, exactly this shape:
  BATCH-RESULT: ok=<n_translated> failed=<n_failed> deferred=<n_deferred> kimi=<n_from_draftedIds> total_en=<count of data/translations/en/*.json>
