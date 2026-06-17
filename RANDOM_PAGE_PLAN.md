# "Not-so-random" daily page — design & plan

Surface QDaily's timeless archive against today's news: each day an agent reads
current hotspots, finds archive pieces that *echo* them, and publishes a fresh
curated digest (front page + a dated permanent page + RSS) so search engines have
new, honestly-dated content that funnels link-equity into the deep archive.

## Decisions (2026-06-17)
- **SEO vehicle: BOTH** — a homepage "Today's Pick" block (for visitors) **and** a
  durable dated digest page `/daily/<YYYY-MM-DD>/` (the artifact Google indexes).
- **Hotspot sources: whatever's reachable** — fetcher tries several free feeds,
  uses what responds. Confirmed: **Google Trends RSS** (free, no key). Candidates
  needing a live check on the Mac: vvhan (weibo/zhihu/baidu), tophubdata (token).
- **Scope: lean v1, local-test first** — prove the pipeline + indexing before
  embeddings or the multi-editor tournament. **Do NOT auto-deploy** until verified
  locally.

## Honesty rule (non-negotiable, archive integrity)
Never re-date archive articles. They keep their true 2014–2019 dates. The digest
is a clearly-editorial curation layer dated *today*; that is the only "fresh"
content. Re-dating old URLs would violate Google guidelines and the archive's
integrity bar.

## Auth / cost model
No billed API key (per the captioning decision). The curation step that needs
model judgment runs as **one scheduled Claude session per day** — single run,
locked, no fan-out (this is the size that's fine; the nine-session sprawl came
from `parallel()` fan-out, which we do NOT use here). Deterministic steps (fetch,
search, emit) are plain Python with no model call.

## v1 pipeline (one locked daily run)
1. **`tools/fetch_hotspots.py`** (model-free) — pull from reachable feeds →
   `data/hotspots/<date>.json` = `[{source, rank, title, hot, url}]`. Multi-source
   with per-source try/except; dedupe near-identical titles.
2. **`tools/match_archive.py`** (model-free) — for each hotspot, query the existing
   **Pagefind** index / corpus for candidate archive articles (lexical v1;
   embeddings later). Emit ranked candidates per hotspot.
3. **Curate + frame** (the one model step, daily Claude session) — from candidates
   pick 20 timeless "echoes", write per-pick framing (why this old piece speaks to
   today) → `data/daily_picks.json` `{date, editor_id, picks:[{id,url,hotspot,blurb}]}`.
4. **`tools/render.py` additions** (model-free) — when `data/daily_picks.json`
   exists: render a homepage "Today's Pick" block, a permanent `/daily/<date>/` digest
   page (new URL, dated today, links to archive pieces w/ their real dates), add
   the digest to the sitemap, and emit RSS entries (the digest as one item +/or
   the 20 picks framed as "featured today" with original dates noted).
5. **Deploy** — commit `data/daily_picks.json` (+ any hotspots cache) → push → CI
   redeploys. **v1: manual push after local review**, automate later.

## Instrumentation (needed from day one for the tournament)
- Tag every digest + pick with `editor_id`.
- Measure downstream: Google Search Console impressions/clicks per `/daily/<date>/`
  and per featured archive URL; GoatCounter pageviews. Rolling ~30-day window
  (Google indexing lags 2–6 weeks).

## v2 (later, not now)
- Semantic matching: precompute embeddings of all ~54k articles once; embed the
  day's hotspots; nearest-neighbor for true "resonance".
- Multi-editor tournament: 2–3 distinct selection strategies on **fixed rotation**
  (~10 digests each/month, cleaner attribution than a live bandit at this volume);
  after a month, kill the clear loser. Caveat: 20/day × 30d split 3 ways vs a
  weeks-long indexing lag = directional, not statistically clean.

## Audience/engine note
QDaily's natural readers are Chinese-reading; in the mainland that's Baidu (Google
+ Weibo are GFW-blocked there). The fresh-digest play helps all engines, and
Baidu/Bing/IndexNow are already wired — the digest URLs should be pushed there too,
not just aimed at Google.

## English translation (decided 2026-06-17 — NOT started)
Reuse the LatePost zh→en pipeline (`/Users/logoutx/Projects-FOB/LatePost2026/translation/`)
— ~80% generic, runs on **Max-plan subagents (no billed API key)**:
- **Reuse as-is:** two-pass Workflow (Sonnet draft → Opus polish), the pure-function
  QA core (`translation/core/qa.mjs` — dropped-image / untranslated-CJK / truncation /
  glossary-adherence / forbidden-rendering checks), resumable "EN file exists = done".
- **Adapt:** input loader (QDaily `articles_extracted_*.jsonl` + `body_html`→markdown,
  vs LatePost's `.md`+YAML); output + `render.py` bilingual emit (`/en/articles/<id>/`,
  `hreflang`, language switcher); a **QDaily-specific glossary + STYLE.md** (seed from
  LatePost but different outlet/era — 好奇心日报 2014–19 lifestyle/tech/culture).
- **Scope (decided): daily picks (ongoing) + one-time ~3k 长文章 backfill.** NOT the
  full 54k (months of Max-window grind, diminishing returns on minor/dated pieces).
  Translating the picks → English digest + EN article versions is the highest-leverage
  Google move (English search volume ≫ Chinese-on-Google) and ties straight into this
  project.
- **Integrity:** label clearly "machine-translated"; Chinese stays authoritative; QA gate
  enforced. Same one-run-at-a-time / no-fan-out discipline as everything else here.

## Global-trend matching (empirically decided 2026-06-17)
Lexical bigram matching fails on English trends (archive is Chinese). A 5-method
empirical bake-off (judged by 3 adversarial lenses on today's real trends) picked
**`conceptual-bridge-with-lexical-prefilter`** (≈0.80 meaningful-precision; baseline
lexical = 0.00):
1. Curator (the daily Claude session — free on Max plan) reasons about the trend's
   **theme**, not its words.
2. Expand to 2–5 **Chinese concept queries** (reuse LatePost glossary for entity names).
3. **Lexical pre-filter** via existing `tools/match_archive.py` bigram index (cheap recall).
4. Curator **reads candidate bodies** and keeps only genuine echoes (precision).
5. **"Don't force it" skip gate** — publish only if ALL hold, else `matched=false`:
   concept-not-token; exact-entity for person/event trends; temporal (post-~2019 needs a
   real precursor, never the named thing); reader-intent ("best deals now" → skip);
   **two solid recs or none**. Verify ids exist before publishing.
- **No-echo classes (always skip):** post-2019 named events/people (e.g. Epstein),
  Western-niche sports (NFL/rugby), tech that didn't exist pre-2019 (on-device LLMs).
- **Strong echoes:** US science/research-policy, consumer-tech & pricing, indie web.
- **Embeddings = v2 recall upgrade** (multilingual NN catches zero-shared-character
  echoes) — uninstallable on the current Python 3.14 venv; needs a ≤3.12 venv + ~120MB
  model. Improves recall, not judgment (steps 4–5 stay).

## Daily composition (decided 2026-06-17) — 12 picks = 4 + 4 + 4
Each day's "Today's Pick" is **12 articles** in three internal buckets:
- **4 China-trend** — echoes of Weibo/Zhihu/Douyin/Toutiao hot search (lexical
  matcher works well here; same-language).
- **4 global-trend** — echoes of Hacker News / Google Trends, via the
  conceptual-bridge method above.
- **4 serendipity longform** — random timeless 长文章 (the original "random page"
  idea); no trend needed, pure evergreen discovery.

Display is a **uniform 12-tile grid** (front-page tile format, 4-up × 3 rows) —
the bucket is an INTERNAL selection rule, NOT shown as a label (consistent with the
"no trend reference / no explanatory prose" decisions). Add an internal `bucket`
field (`cn-trend` | `global-trend` | `longform`) per pick in `daily_picks.json` for
the editor-tournament attribution; it is not rendered.

**Binding constraint + fallback:** the **global-trend** bucket is the one that may
not fill — most days fewer than 4 global trends have a *meaningful* archive echo,
and the skip gate forbids forcing weak matches. Rule: fill each trend bucket only
with matches that pass the gate; **any shortfall spills into the serendipity-longform
bucket** (always available, ~3k 长文章), so the page is always 12 but never padded
with junk. So the real floor is "≥0 trend echoes + longform backfill = 12"; on a
strong news day it's a clean 4 + 4 + 4.

## Build order
v1a fetcher (now, local test) → v1b matcher (local test) → v1c render/template +
RSS (local render, eyeball) → v1d wire one daily scheduled session → only then go
live + instrument.
