"""
Discover China Digital Times posts that repost QDaily articles.

Strategy: hit CDT's WordPress REST API with multiple search terms, paginate
fully, then keep posts whose body contains at least one image hosted under
chinadigitaltimes.net/chinese/files/... that mirrors a QDaily filename.
That image-test is the strongest "is this a repost" signal — better than
title heuristics, since CDT also publishes opinion pieces ABOUT QDaily.

For each surviving post, match its title against our QDaily article index
(loaded from data/articles_extracted_*.jsonl) by exact or near-exact match.

Output: data/cdt_matches.tsv with one row per match — "article_id<TAB>cdt_url"
— suitable for piping into tools/fetch_cdt.py --batch.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# CDT's image URL pattern that mirrors QDaily filenames.
CDT_QD_IMG_RE = re.compile(
    r"chinadigitaltimes\.net/chinese/files/\d{4}/\d{2}/"
    r"\d{14}[A-Za-z0-9]+\.(?:jpe?g|png|gif|webp|JPG|JPEG|PNG|GIF|WEBP)-w\d+\.jpg",
    re.IGNORECASE,
)


def fetch_search_page(client: httpx.Client, term: str, page: int) -> list[dict]:
    """Return a page of WP /posts?search=<term>."""
    r = client.get(
        "https://chinadigitaltimes.net/chinese/wp-json/wp/v2/posts",
        params={"search": term, "per_page": 100, "page": page,
                "_fields": "id,link,title,content,date"},
    )
    if r.status_code == 400:
        # WP returns 400 when page exceeds total_pages
        return []
    r.raise_for_status()
    return r.json()


def all_search_hits(client: httpx.Client, term: str) -> list[dict]:
    """Paginate until exhausted."""
    out = []
    for page in range(1, 11):  # WP caps total_pages but we soft-cap at 10
        chunk = fetch_search_page(client, term, page)
        if not chunk:
            break
        out.extend(chunk)
        # Be polite.
        time.sleep(0.5)
    return out


def fetch_date_range(client: httpx.Client, after_iso: str, before_iso: str,
                     page: int, per_page: int = 25) -> tuple[list[dict], int]:
    """One page of /posts?after=...&before=..., returns (chunk, total_pages).

    per_page is capped low because CDT's WP REST is slow to serialise full
    post content; 100/page was timing out on every request. 25/page comes
    back within ~5-15s and lets us recover gracefully on timeout.
    """
    r = client.get(
        "https://chinadigitaltimes.net/chinese/wp-json/wp/v2/posts",
        params={"per_page": per_page, "page": page,
                "after": after_iso, "before": before_iso,
                "orderby": "date", "order": "desc",
                "_fields": "id,link,title,content,date"},
    )
    if r.status_code == 400:
        return [], 0
    r.raise_for_status()
    total_pages = int(r.headers.get("X-WP-TotalPages") or 0)
    return r.json(), total_pages


def crawl_date_range(client: httpx.Client, after_iso: str, before_iso: str,
                     image_filter: re.Pattern) -> list[dict]:
    """Paginate every post in the date range, filter by CDT QDaily image hotlinks."""
    keepers: list[dict] = []
    page = 1
    total_pages = None
    while True:
        chunk = None
        total_pages_this = None
        # Up to 4 attempts with exponential backoff before giving up on a page.
        for attempt in range(4):
            try:
                chunk, total_pages_this = fetch_date_range(client, after_iso, before_iso, page)
                break
            except httpx.HTTPError as e:
                wait = 5 * (2 ** attempt)
                print(f"  page {page}: {type(e).__name__} (attempt {attempt + 1}/4) — retrying in {wait}s",
                      flush=True)
                time.sleep(wait)
        if chunk is None:
            print(f"  page {page}: failed 4 times, aborting range", flush=True)
            break
        total_pages = total_pages_this if total_pages_this is not None else total_pages
        if not chunk:
            break
        kept_this_page = 0
        for post in chunk:
            content = post.get("content", {}).get("rendered", "") or ""
            if image_filter.search(content):
                keepers.append(post)
                kept_this_page += 1
        print(f"  page {page}/{total_pages or '?'}: {len(chunk)} posts, kept {kept_this_page}",
              flush=True)
        if total_pages and page >= total_pages:
            break
        page += 1
        # WP REST appears to cap total_pages at 100 — even with date narrowing,
        # very large ranges get truncated. Caller is responsible for slicing.
        if page > 200:
            break
        time.sleep(0.6)
    return keepers


def normalise(s: str) -> str:
    """Lowercase + strip punctuation/whitespace for title matching."""
    return re.sub(r"[\s　\W_]+", "", s.lower())


def load_qd_titles(records_glob: str) -> dict[str, int]:
    """Return {normalised_title: article_id} from the extracted records."""
    from glob import glob
    out: dict[str, int] = {}
    for p in sorted(glob(records_glob)):
        for line in Path(p).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("title") or ""
            if not t:
                continue
            key = normalise(t)
            if key:
                out[key] = r["id"]
    return out


def title_match(cdt_title: str, qd_index: dict[str, int]) -> int | None:
    """Find a matching QDaily article id, or None."""
    # CDT titles often look like "好奇心日报 | <real_title>" or "<source> | <real_title>"
    # — but the separator can be ASCII '|' OR full-width '｜'.
    raw = cdt_title
    # Try every "| <part>" segment and the raw title.
    candidates = [raw]
    for sep in ("|", "｜"):
        if sep in raw:
            for part in raw.split(sep):
                p = part.strip()
                if p:
                    candidates.append(p)
    for c in candidates:
        key = normalise(c)
        if key and key in qd_index:
            return qd_index[key]
    return None


def month_ranges(start: str, end: str):
    """Yield ('YYYY-MM-01T00:00:00', 'YYYY-(MM+1)-01T00:00:00') pairs."""
    from datetime import date, timedelta
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, m) <= (ey, em):
        after = f"{y:04d}-{m:02d}-01T00:00:00"
        # Next month
        if m == 12:
            ny, nm = y + 1, 1
        else:
            ny, nm = y, m + 1
        before = f"{ny:04d}-{nm:02d}-01T00:00:00"
        yield after, before
        y, m = ny, nm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl")
    ap.add_argument("--out", type=Path, default=Path("data/cdt_matches.tsv"))
    ap.add_argument("--start", default="2014-01",
                    help="Earliest YYYY-MM to crawl (default 2014-01)")
    ap.add_argument("--end", default="2019-05",
                    help="Latest YYYY-MM to crawl (default 2019-05)")
    ap.add_argument("--debug", action="store_true",
                    help="also print unmatched but image-confirmed posts")
    args = ap.parse_args()

    client = httpx.Client(headers=HEADERS, timeout=httpx.Timeout(90.0, connect=20.0),
                          follow_redirects=True)

    print("loading QDaily title index ...", flush=True)
    qd_index = load_qd_titles(args.records_glob)
    print(f"  {len(qd_index):,} unique titles loaded", flush=True)

    # Date-range crawl: iterate every month in QDaily's era and keep posts
    # whose body contains a CDT-hosted QDaily-pattern image.
    candidates: dict[int, dict] = {}
    for after, before in month_ranges(args.start, args.end):
        print(f"\ncrawling {after[:7]} ...", flush=True)
        try:
            keepers = crawl_date_range(client, after, before, CDT_QD_IMG_RE)
        except httpx.HTTPError as e:
            print(f"  error: {e}")
            continue
        for post in keepers:
            candidates.setdefault(post["id"], post)
    print(f"\ntotal candidate posts with QDaily image hotlinks: {len(candidates)}")

    matched: list[tuple[int, str, str]] = []
    unmatched: list[tuple[str, str]] = []
    for post in candidates.values():
        cdt_url = post["link"]
        from html import unescape
        title = re.sub(r"<[^>]+>", "", post["title"]["rendered"])
        title = unescape(title).strip()
        qd_id = title_match(title, qd_index)
        if qd_id is not None:
            matched.append((qd_id, cdt_url, title))
        else:
            unmatched.append((cdt_url, title))

    matched.sort(key=lambda x: x[0])
    with args.out.open("w", encoding="utf-8") as fh:
        fh.write("# article_id\tcdt_url\t# cdt_title (after #)\n")
        for qd_id, cdt_url, title in matched:
            fh.write(f"{qd_id}\t{cdt_url}\t# {title}\n")

    print(f"\nmatched to QDaily DB by title: {len(matched)}")
    print(f"unmatched (image-confirmed but title lookup failed): {len(unmatched)}")
    if args.debug and unmatched:
        print("\n=== unmatched ===")
        for u, t in unmatched:
            print(f"  {u}  →  {t}")
    print(f"\nwrote {len(matched)} matches to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
