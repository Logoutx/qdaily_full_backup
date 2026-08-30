Translate the next batch of QDaily articles zh→en. You are the whole pipeline for this batch (draft AND editorial polish). Work from the project root you were started in.

STEP 1 — queue (20 per batch). Run:
  ./.venv/bin/python tools/translate_todo.py --limit 20 --emit
It prints a JSON array of ids and materializes data/translations/in/<id>.json for each. If it prints [], report "queue empty" and stop.

STEP 2 — read the rules ONCE:
  - data/translations/glossary.json — authoritative zh→en renderings, incl. the qdaily_series block for column-name suffixes.
  - data/translations/STYLE.md — both PROMPT blocks (draft + polish). Apply BOTH standards: faithful first, then edit in the Orwell mode.

STEP 3 — for EACH of the 20 ids, one at a time:
  a. Read data/translations/in/<id>.json (title, excerpt, category, type, body markdown).
  b. Translate title, excerpt, and full body. Preserve ALL markdown structure and EVERY image reference ![alt](url) — translate alt/captions, keep URLs byte-for-byte. No Chinese characters except inside an untranslatable 《》 work title. Do not omit, summarize, or embellish.
  c. Self-verify against the source before writing: image count identical; no dropped paragraphs or links; glossary terms and series suffix exact; numbers (亿/万) converted correctly; source-side factual errors preserved faithfully (note them, never silently "fix" facts — only obvious typos in proper names may be normalized).
  d. Write data/translations/out/<id>.txt in EXACTLY this sentinel format:
     @@QD_TITLE@@
     <final English title, one line>
     @@QD_EXCERPT@@
     <final English excerpt, one line, or blank line>
     @@QD_BODY@@
     <full final markdown body>
     Nothing before @@QD_TITLE@@ or after the body.

STEP 4 — collect + QA:
  ./.venv/bin/python tools/translate_collect.py
  ./.venv/bin/python tools/translate_qa.py --write <the 20 ids>
If an id hard-fails QA, re-translate it once (fix the reported problem), collect + QA again. Still failing → leave it flagged; do not loop.

COPYRIGHT GATE: if an article's body is substantially a Chinese translation of an English-language original (书摘/book-excerpt features, reprinted foreign essays), do NOT translate it — that would reconstruct the copyrighted original prose. Instead append its id (as a string) to the data/translations/defer.json array (preserve existing entries, valid JSON) and count it as skipped, not failed. QDaily's own reporting and interviews are fine even when they quote briefly.

STEP 5 — publish the data:
  git add data/translations/ && git commit -m "Translate <N> articles zh->en (scheduled batch)" && git push
(data/translations is data-only; this does not trigger a site deploy.)

STEP 6 — final line of your output, exactly this shape:
  BATCH-RESULT: ok=<n_translated> failed=<n_failed> total_en=<count of data/translations/en/*.json>
