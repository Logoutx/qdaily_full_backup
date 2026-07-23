Refresh QDaily's "Today's Pick" for today and publish it. You are the daily curator (a model — so you do the curation yourself).

Working dir: /Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup  (cd there; `source .venv/bin/activate`)

STEP 1 — date. TODAY = `TZ='Asia/Shanghai' date +%F` (e.g. 2026-06-18).

STEP 2 — refresh exclusions + fetch + build candidates (deterministic):
  python tools/tag_pick_exclusions.py        # tags low-value articles -> data/pick_exclusions.json
  python tools/fetch_hotspots.py --date TODAY
  python tools/build_daily_picks.py --date TODAY
build_daily_picks already drops excluded ids from the cn-trend and longform pools; it writes data/daily_candidates/TODAY.json = {cn_candidates, global_trends, longform_pool}.

STEP 3 — curate 12 picks = 4 cn-trend + 4 global-trend + 4 longform. Read RANDOM_PAGE_PLAN.md for the method + skip gate. Load the corpus for verification/bodies:
  python: import sys; sys.path.insert(0,'tools'); from validate_corpus import load_corpus; recs,_,_=load_corpus('data/articles_extracted_*.jsonl'); by={r['id']:r for r in recs}  (article body is r['body_html']).
  Also load excluded ids: EX = set(map(int, json.load(open('data/pick_exclusions.json'))['excluded'])) — NEVER pick an id in EX (low-value: broken galleries, thin text, Apple-announcement blurbs, light financial reports).
  - cn-trend (4): from cn_candidates pick 4 DISTINCT articles genuinely about the trend; REJECT coincidental character-overlap (e.g. 梅西 matching 梅西百货/Macy's; a 世界波 trend matching a 世界-history piece).
  - global-trend (up to 4): for the most promising global_trends, reason about the THEME → 2-5 Chinese concept queries → search corpus titles+body_html → apply the SKIP GATE (concept-not-token; exact-entity for person/event trends; temporal: post-mid-2019 phenomena need a real precursor, never the named thing; transactional-intent → skip). SKIP any id in EX. Returning FEWER than 4 is correct; never force a coincidental match.
  - longform (4): pick 4 DIVERSE timeless reads from longform_pool.
  - SPILL: if global-trend < 4, backfill with extra distinct longforms so the total is exactly 12. No duplicate ids.

STEP 4 — write data/daily_picks.json:
  {"date":"TODAY","editor_id":"v1-auto","title":"<short Chinese digest title, ≤24 chars>","picks":[{"id","url":"https://www.qdaily.org/articles/<id>/","title","publish_date","bucket","hotspot","source"}]}
  (include hotspot+source for trend picks; omit them for longform.) Verify every id exists in the corpus, titles match, and NONE are in EX, before writing.

STEP 5 — publish (the history copy makes /daily/TODAY/ a PERMANENT page — CI clones fresh, so it must be committed):
  cp data/daily_picks.json data/daily_history/TODAY.json
  git add data/daily_picks.json data/daily_history/TODAY.json && git commit -m "Today's Pick: TODAY" && git push
  (data/daily_picks.json is in deploy.yml's trigger paths, so the push rebuilds + deploys. The renderer randomizes display order.)

STEP 6 — report: the 12 picks by bucket, how many global echoes survived the skip gate, and confirm the push succeeded.
