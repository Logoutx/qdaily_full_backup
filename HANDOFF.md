# QDaily Archive — Cross-Mac Handoff

Live site: <https://www.qdaily.org>
Repo: <https://github.com/Logoutx/qdaily_full_backup>
Project root: `/Users/logoutx/My Drive/Projects/qdaily_full_backup` (Google Drive — same path on both Macs).

---

## 1. Pick up on the new Mac

```bash
cd "/Users/logoutx/My Drive/Projects/qdaily_full_backup"

# 1a. Get latest code
git pull origin main

# 1b. Rebuild venv (do NOT trust the synced .venv — symlinks are machine-specific)
rm -rf .venv && make venv

# 1c. Pagefind binary (also machine-specific; tiny download)
make pagefind

# 1d. Verify cache/ and assets/ have synced from Google Drive
ls cache/ | wc -l   # should be ~54,600
du -sh assets/      # should be ~65 MB

# 1e. (Optional) sanity-check render
make site

# 1f. Resume the image fetcher (long scope, 3-profile rotation)
nohup .venv/bin/python tools/fetch_images.py --scope long \
  > data/fetch_images_long.log 2>&1 &
echo $!  # save this PID
```

### Google Drive sync caveats

- **Wait until Drive finishes syncing** before resuming work. The `cache/`
  directory has ~54,600 HTML files (~7 GB) and `assets/` has thousands of
  small files; the Drive client takes a while to fetch them all on a fresh
  Mac. If you start the fetcher before sync is done, it'll repeatedly re-do
  work for assets that "should" exist but aren't visible yet.
- `.venv/`, `bin/pagefind`, `__pycache__/`, `.DS_Store` should NOT be
  trusted across machines. The first three are gitignored; `make venv` /
  `make pagefind` regenerate them locally.
- Anything that's in git (code, `data/articles.jsonl`, `data/images.jsonl`,
  `data/articles_extracted_*.jsonl`, `assets/**`) — get via `git pull`,
  don't rely on Drive. Drive can lag, git is authoritative.

---

## 2. State at handoff (2026-05-09)

### Background image fetcher
- Scope: `long` (3,145 长文章 → 26,916 unique image URLs)
- Random-batch profile rotation: `1 r/s × 2w` / `2 r/s × 4w` / `4 r/s × 3w`
- Throttle detection: ≥60 % errors in a batch triggers exponential
  backoff (30 s → 60 s → 120 s … capped at 600 s); clears on next clean
  batch.
- **Manifest right now** (`data/images.jsonl`, 8,401 lines):
  - `ok`                  1,018  ← downloaded to `assets/<id>/<sha1>.<ext>`
  - `no-snapshot-prefix`  4,110  ← Wayback truly has nothing (permanent)
  - `cdx-error`           1,786  ← transient; auto-retries on next run
  - `no-snapshot`         1,483  ← legacy entries, also auto-retried
  - `fetch-error`             4  ← also transient
- 50 长文章 currently load ≥1 image from local mirror; 33.6 % per-image
  coverage across those. As the fetcher chews through more URLs the rest
  fill in — fully resumable.
- The fetcher writes to `data/images.jsonl` and `assets/`; **commit and
  push periodically** so the live site picks up newly-mirrored images
  (the deploy workflow re-renders with `--image-mode local` and includes
  whatever's in `assets/`). Suggested cadence: every ~24 h while it runs.

### Live site
- Custom domain `www.qdaily.org` is live. CSS/JS path migration done.
- 37 series total. Above-the-fold "top 12" is user-curated:
  `只看长文章 / 年度观察 / 2017 清退 / 好奇心商业史 / 房子和我们的生活 /
  100 个有想法的人 / 也许欧洲有答案 / 好莱坞报告 / 卫星新闻 /
  创始人说 / 好奇心小数据 / 好奇心辞典`. The other 25 are behind
  the `查看所有栏目` toggle, in the editorial order in
  `tools/render.py:HOME_SERIES_ORDER`.
- The dead `原始截图: <url>` link at the bottom of every article is
  gone (the sinaimg.cn screenshots all 404 now). The notice on the 190
  screenshot-only stub articles still says "下方为原始截图" — discuss
  whether to also strip that figure.

### What's pinned vs auto-matched per series
- Pinned-by-ID (one snapshot of the original /special_columns/ page):
  创始人说 (20), 市场发明家 (20), 后视镜 (14), 好莱坞报告 (20),
  所长の大数据 (20), 为什么读书 (20), 42 区 (14),
  22 岁，他们在想什么 (19), 也许欧洲有答案 (7),
  这个社会，对年轻人太好了吗？ (7), and the 11 articles of column 41
  (`2016 大公司数字化`) folded into 年度观察.
- Pattern-matched (regex/substring on title): everything else, e.g.
  好奇心商业史, 好奇心小数据, 历史上的今天 (86 by title), etc. See
  `tools/render.py:_series_match`.

---

## 3. Open todos (priority order)

### TODO 1 — fill missing images for "好奇心小数据" (518 articles, 2,169 missing images)

The user's hypothesis: most missing 小数据 images can be recovered by
finding a Douban repost, since Douban often archives full image sets.
Worked example: article 60488 has 10/18 missing; the title can be found
on Douban via `site:douban.com <title>` search (the user did this manually).

**Blocker found:** Douban is now serving an anti-bot challenge to
non-browser clients. A direct `httpx.get(...)` returns ~2.9 KB of
challenge JS instead of article HTML, regardless of UA spoofing.
Wayback doesn't have the specific Douban note URLs we tried.
Digitaling reposts work via direct fetch but their image counts don't
match QDaily's (Digitaling adds editorial intros + section banners), so
naive position-based matching would silently swap images and produce
wrong fixes — not acceptable for an archive.

**Options to choose from when picking this back up:**

- **Option A — best fidelity, most setup.** Connect Claude-in-Chrome on
  the new Mac, sign into Douban there. Drive Douban from your logged-in
  tab via the MCP, scrape per-article image lists, verify counts/order
  match QDaily before downloading. ~30 s/article × 518 ≈ 4 h
  unattended. Highest match accuracy.
- **Option B — multi-source, partial coverage.** Try Douban (via Chrome)
  → Digitaling → Sohu → WeChat-archive in order per article. Only fill
  QDaily's missing slots when image counts line up cleanly; skip articles
  where automated matching is ambiguous. Faster but covers maybe 60–70 %.
- **Option C — interactive curation.** Generate a checklist (article URL
  + 1–2 candidate Douban URLs from search) per article. User skims,
  pastes back the chosen URL per article; agent does per-article scrape
  via Chrome. Trade reviewer time for accuracy.
- **Option D — drop it.** Skip the manual fix; rely on whatever
  the background fetcher mirrors. Note that 小数据 articles are NOT
  长文章, so the current `--scope long` run will never cover them — would
  need a separate `--scope xsj` (or similar) run.

User leaned toward A or C. **Open question for the new session:** which
one to do, and whether to do an `--scope xsj` fetcher pass first to grab
whatever Wayback DOES still have for 小数据 (the pilot only covered
~600 of 2,489 小数据 image URLs).

Inventory snapshot is in the script at the top of this section's research
in `data/images.jsonl`. To regenerate the "top-N missing 小数据 articles"
list:

```python
# scripts/list_xsj_gaps.py — quick inline (paste into a .venv python)
import json, glob, hashlib
from pathlib import Path
from urllib.parse import urlparse
def fn(u):
    ext = Path(urlparse(u).path).suffix.lower() or '.bin'
    return hashlib.sha1(u.encode()).hexdigest()[:16] + ext
records = {}
for p in sorted(glob.glob('data/articles_extracted_*.jsonl')):
    for line in Path(p).read_text().split('\n'):
        if line.strip():
            r = json.loads(line); records[r['id']] = r
for r in records.values():
    if '好奇心小数据' not in (r.get('title') or ''): continue
    imgs = [u for u in (r.get('images') or []) + ([r.get('banner_image')] if r.get('banner_image') else [])
            if u and u.startswith(('http://','https://'))]
    miss = [u for u in imgs if not (Path('assets')/str(r['id'])/fn(u)).exists()]
    if miss:
        print(f'{r["id"]}\t{len(miss)}/{len(imgs)}\t{r["title"][:80]}')
```

### TODO 2 — clean up the screenshot-only stub UI

190 articles are `is_screenshot_only=True` in the extracted data
(Wayback couldn't capture the body, so the only "content" was a sinaimg
screenshot URL). The article footer's dead screenshot link is already
gone (commit 56d08ac), but the stub article body still embeds the
broken sinaimg `<img>` plus the notice.

Decide: strip the figure too (leaves just title + meta + "Wayback Machine
未能完整保存这篇文章的正文" notice + click-through to Wayback URL),
or keep as-is? Lives in `site/templates/article.html:18-26`.

### TODO 3 — column rescan for IDs that errored

When discovering /special_columns/ pages, IDs `19, 84, 94` returned
`RemoteProtocolError` from the Wayback CDX API (transient throttle, not a
hard miss). Other ranges `1-3, 5, 10, 21, 26-27, 32, 42, 45, 48, 50,
52-53, 55, 60, 64, 66-67, 70-71, 73-83, 85-100` returned empty results
that may or may not be real.

If you want the long tail of small columns, retry these via
`https://archive.org/wayback/available` with longer cooldowns. The
49 columns we already imported are saved at
`/tmp/qdaily_columns_data.json` on the OLD Mac — won't survive the move,
but the 49 themselves are all already represented in `tools/render.py`
matchers, so this isn't blocking.

### TODO 4 — push periodically while fetcher runs

The path filter on `.github/workflows/deploy.yml` includes
`assets/**` and `data/images.jsonl`, so every commit-and-push triggers
a fresh build with the latest local images. Suggested cadence: once a
day while the fetcher's running. `git status` before pushing — only
include `data/images.jsonl` and `assets/`, leave anything else alone.

### TODO 5 — process check before any new run

`make all` / `make site` is fine to re-run any time, but the image
fetcher should NOT have two copies running concurrently (they'd race
on `data/images.jsonl` and waste CDX requests). Always `ps aux | grep
fetch_images` first.

---

## 4. Pipeline cheat sheet

```
source/        ← LampScript backup (Drive-synced, gitignored)
   ↓ tools/inventory.py    (parse → data/articles.jsonl, ~54,742 rows)
data/articles.jsonl
   ↓ tools/fetch_wayback.py  (download → cache/<id>.html, resumable)
cache/         ← raw Wayback HTML (gitignored, Drive-synced)
   ↓ tools/extract.py       (parse HTML → data/articles_extracted_<year>.jsonl)
data/articles_extracted_*.jsonl  (in git)
   ↓ tools/render.py        (--image-mode local → public/)
public/                                            (gitignored, regenerable)
   ↓ pagefind                (search index → public/pagefind/)
public/

# Out-of-band, runs in background:
data/articles_extracted_*.jsonl
   ↓ tools/fetch_images.py --scope long
assets/<id>/<sha1>.<ext>  +  data/images.jsonl  (both in git)
```

CI on push: `.github/workflows/deploy.yml` runs `tools/render.py
--base-url "/" --site-url https://www.qdaily.org --image-mode local`,
then Pagefind index, then deploys to GitHub Pages with the CNAME.

---

## 5. Things NOT to do

- **Don't `git push --force`** — main is the deploy source for the live
  site.
- **Don't commit `cache/`, `public/`, `.venv/`, `bin/`** — already
  gitignored, but Drive-sync sometimes makes them visible-but-unstaged.
- **Don't delete `data/articles_extracted_*.jsonl`** — they're the
  rendered-input source-of-truth and contain the screenshot-only stub
  metadata that took multi-stage logic to produce.
- **Don't change `_ANNUAL_TERMS` in `tools/render.py`** — it matches
  literal text inside QDaily article titles ("Top 15 年度报道"), not the
  series display name. The display name was renamed to 年度观察, but the
  title text wasn't.
- **Don't run two `fetch_images.py` processes concurrently.**

---

## 6. Misc

- Dead Mac: project was working on `/Users/logoutx/My Drive/Projects/qdaily_full_backup`
  on a Mac running Darwin 25.4.0 / Python 3.14. Image-fetch process was
  killed cleanly at 2026-05-09 ~22:30 PT before this handoff was written.
- `gh` CLI is signed in as `Logoutx` on the old Mac. On the new Mac:
  `brew install gh && gh auth login` first time.
- A local preview server (`python -m http.server 8765 --directory public`)
  was running and was killed too. Restart any time with `make serve`.
