"""
Mirror article images from Wayback into the repo.

For each unique image URL referenced by the in-scope articles:
  1. Query Wayback CDX for any 200-captured snapshot of that URL.
  2. If one exists, fetch the bytes via /web/<ts>id_/<orig> and save to
     assets/<article_id>/<sha1>.<ext>. (sha1 is the same digest format
     render.py's resolve_url() expects, so --image-mode=local picks
     these up automatically.)
  3. Write a manifest line per URL to data/images.jsonl with
     {url, ts, status, path, length, http_status} so the run is
     resumable — already-recorded URLs skip re-querying CDX.

Scope is controlled by --scope:
  --scope xsj     only 好奇心小数据 articles (pilot)
  --scope long    only 长文章
  --scope both    长文章 + 小数据 (full)
  --scope all     every article (likely unwanted, but supported)

A single URL may appear in many articles. We download once, but link it
under each article_id that referenced it so the local-asset lookup in
resolve_url() finds it regardless of which article you're rendering.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

UA = "qdaily-archive/0.1 (+contact: logoutx)"
TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# Hard cutoff: keep in sync with tools/extract.py — anything published after
# QDaily's actual final article (id=64092, 2019-05-27) is treated as
# out-of-archive and gets no image fetching.
QDAILY_FINAL_DATE = "2019-05-27"

# Same long-article rules as render.py — keep in sync.
LONG_THRESHOLD = 4000
AUTHOR_PURE_CJK_RE = re.compile(r"^[一-鿿\s·、，,；; ]+$")
REPRINT_TITLE_RE = re.compile(r"^[《【]")


def is_long(r: dict) -> bool:
    if (r.get("body_text_len") or 0) < LONG_THRESHOLD:
        return False
    if not AUTHOR_PURE_CJK_RE.match(r.get("author") or ""):
        return False
    if REPRINT_TITLE_RE.match(r.get("title") or ""):
        return False
    title = r.get("title") or ""
    if ("大公司头条" in title) or ("商业剪报" in title):
        return False
    return True


def is_xsj(r: dict) -> bool:
    return "好奇心小数据" in (r.get("title") or "")


def is_article(r: dict) -> bool:
    """Standard editorial articles: same exclusions as is_long (CJK author,
    not a reprint, not a 大公司头条/商业剪报 news brief) but WITHOUT the
    4000-char length threshold. So is_long ⊂ is_article — this scope picks up
    every real piece QDaily wrote, just not the daily news-brief digests or
    syndicated reprints. Used by the 48/48 cycle once --scope long exhausts."""
    if not AUTHOR_PURE_CJK_RE.match(r.get("author") or ""):
        return False
    if REPRINT_TITLE_RE.match(r.get("title") or ""):
        return False
    title = r.get("title") or ""
    if ("大公司头条" in title) or ("商业剪报" in title):
        return False
    return True


class TransientError(Exception):
    pass


@retry(
    retry=retry_if_exception_type(TransientError),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def http_get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, follow_redirects=True)
    if r.status_code in (429, 500, 502, 503, 504, 520, 522, 524):
        raise TransientError(f"{r.status_code} on {url}")
    return r


def _cdx_call(client: httpx.Client, cdx_url: str) -> tuple[str, list]:
    """Returns (state, rows) where state ∈ {"ok", "empty", "error"}
    and rows excludes the header row."""
    try:
        r = http_get(client, cdx_url)
    except Exception:
        return "error", []
    if r.status_code != 200:
        return "error", []
    text = r.text.strip()
    if not text:
        return "empty", []
    if not text.startswith("["):
        # HTML "Temporarily Offline" or similar — treat as transient.
        return "error", []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "error", []
    if len(data) < 2:
        return "empty", []
    return "ok", data[1:]


# QDaily image transform suffix: ".jpg-w600", ".jpeg-WebpWebW640", etc.
# Strip back to the bare ".ext" form so prefix-match catches every
# transform variant Wayback might have captured.
_TRANSFORM_SUFFIX_RE = re.compile(r"(\.[A-Za-z0-9]+)-[A-Za-z0-9]+$")


def cdx_lookup(
    client: httpx.Client, url: str
) -> tuple[str, str | None, int | None, str | None]:
    """Find best 200-snapshot for `url`.

    Two-stage search:
      1. exact-URL CDX query
      2. on empty: prefix-match on the base file path (querystring
         stripped, transform suffix like "-w600" / "-WebpWebW640"
         stripped) — catches the case where Wayback only captured
         a different size/format variant of the same master image.

    Returns (kind, ts, length, fetch_url) where:
      - kind   ∈ {"ok", "empty", "error"}
      - fetch_url is the URL to actually retrieve from Wayback (may
        differ from `url` when the prefix fallback located a variant)
    """
    # 1) Exact match
    cdx = (
        "https://web.archive.org/cdx/search/cdx?"
        f"url={quote(url, safe='')}"
        "&filter=statuscode:200&output=json&limit=1"
    )
    state, rows = _cdx_call(client, cdx)
    if state == "ok":
        row = rows[0]
        try:
            length = int(row[6])
        except (ValueError, IndexError):
            length = None
        return "ok", row[1], length, url
    if state == "error":
        return "error", None, None, None
    # 2) Prefix fallback — strip querystring + transform suffix.
    base = url.split("?", 1)[0]
    base = _TRANSFORM_SUFFIX_RE.sub(r"\1", base)
    # CDX `matchType=prefix` is a true prefix match — DO NOT append `*`,
    # which the API interprets literally and returns nothing.
    cdx = (
        "https://web.archive.org/cdx/search/cdx?"
        f"url={quote(base, safe='')}"
        "&matchType=prefix"
        "&filter=statuscode:200"
        "&filter=mimetype:image/.*"
        "&output=json&limit=1"
    )
    state, rows = _cdx_call(client, cdx)
    if state == "ok":
        row = rows[0]
        try:
            length = int(row[6])
        except (ValueError, IndexError):
            length = None
        # row[2] is the original URL — use that for the id_ fetch so
        # Wayback returns the exact captured bytes.
        return "ok", row[1], length, row[2]
    if state == "error":
        return "error", None, None, None
    # 2-stage exhausted — Wayback truly has nothing.
    return "empty", None, None, None


def asset_path(assets_root: Path, article_id: int, url: str) -> Path:
    ext = Path(urlparse(url).path).suffix.lower() or ".bin"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return assets_root / str(article_id) / f"{digest}{ext}"


class RateLimiter:
    """Token-bucket-ish thread-safe gate. Holds the global request rate
    constant regardless of how many workers compete for it — the point
    of concurrency here is to overlap network latency, NOT to fire
    more requests/sec at Wayback (which would just trigger harder
    throttling)."""

    def __init__(self, rps: float):
        self.interval = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if wait_for > 0:
            time.sleep(wait_for)


def process_one(
    url: str,
    ids: list[int],
    client: httpx.Client,
    limiter: RateLimiter,
    assets_root: Path,
    asset_lock: threading.Lock,
) -> dict:
    """Worker function: do one URL end-to-end and return its manifest
    record. Designed to be called from a ThreadPoolExecutor — the
    only shared state is `limiter` (rate-limited request gate) and
    `asset_lock` (serializes filesystem mkdir + hardlink fanout to
    avoid any racy double-creates)."""
    primary_id = ids[0]

    # Pass through medium.com / live URLs without saving.
    host = urlparse(url).netloc.lower()
    if host == "medium.com" or host.endswith(".medium.com"):
        return {"url": url, "status": "skip-live",
                "ts": None, "path": None, "length": None}

    # 1) CDX lookup (exact, then prefix fallback)
    limiter.wait()
    kind, ts, cdx_length, fetch_url = cdx_lookup(client, url)
    if kind == "error":
        return {"url": url, "status": "cdx-error",
                "ts": None, "path": None, "length": None}
    if kind == "empty":
        return {"url": url, "status": "no-snapshot-prefix",
                "ts": None, "path": None, "length": None}

    # 2) Fetch the image bytes
    limiter.wait()
    wb = f"https://web.archive.org/web/{ts}id_/{fetch_url}"
    try:
        r = http_get(client, wb)
    except Exception as e:
        return {"url": url, "status": "fetch-error", "ts": ts,
                "path": None, "length": cdx_length, "error": str(e)[:120]}

    if r.status_code != 200 or len(r.content) < 200:
        return {"url": url, "status": "http-error", "ts": ts,
                "http_status": r.status_code, "path": None,
                "length": len(r.content)}

    # 3) Filesystem fan-out — one canonical write under primary_id,
    #    hard-linked under every other referencing article id. Held
    #    under asset_lock so two workers writing different URLs that
    #    happen to share the same article folder don't race on mkdir.
    with asset_lock:
        target = asset_path(assets_root, primary_id, url)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(r.content)
        tmp.rename(target)
        for other_id in ids[1:]:
            link_target = asset_path(assets_root, other_id, url)
            link_target.parent.mkdir(parents=True, exist_ok=True)
            if link_target.exists():
                continue
            try:
                link_target.hardlink_to(target)
            except OSError:
                link_target.write_bytes(r.content)

    return {
        "url": url, "status": "ok", "ts": ts,
        "path": str(target.relative_to(Path("."))),
        "length": len(r.content),
        "linked_ids": ids,
        "fetch_url": fetch_url if fetch_url != url else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl")
    ap.add_argument("--scope", choices=("xsj", "long", "article", "both", "all"), default="xsj")
    ap.add_argument("--ids-file",
                    help="Optional: file with one article id per line (ignored "
                    "if blank/#-prefix). When set, only those articles are "
                    "in-scope and --scope is ignored.")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--manifest", default="data/images.jsonl")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="GLOBAL requests per second — shared across "
                    "workers. Don't crank this; CDX throttles aggressively.")
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel workers. Hides network latency without "
                    "raising the request rate (rate is global, not per-worker).")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N URLs (0 = no limit)")
    args = ap.parse_args()

    # Load + dedup records (last-wins, matches render.py)
    record_map: dict[int, dict] = {}
    for path in sorted(glob.glob(args.records_glob)):
        for line in Path(path).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            record_map[r["id"]] = r

    # Build {url: [article_ids]} for scoped articles
    only_ids: set[int] | None = None
    if args.ids_file:
        only_ids = set()
        for line in Path(args.ids_file).read_text(encoding="utf-8").split("\n"):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                only_ids.add(int(line))
            except ValueError:
                continue
        print(f"--ids-file: filtering to {len(only_ids):,} specific article ids",
              flush=True)

    url_to_ids: dict[str, list[int]] = {}
    n_skipped_post_cutoff = 0
    for r in record_map.values():
        # Hard date cutoff — skip anything after QDaily's actual final date.
        pd = r.get("publish_date") or ""
        if pd and pd > QDAILY_FINAL_DATE:
            n_skipped_post_cutoff += 1
            continue
        if only_ids is not None:
            if r["id"] not in only_ids:
                continue
        elif args.scope == "xsj" and not is_xsj(r):
            continue
        elif args.scope == "long" and not is_long(r):
            continue
        elif args.scope == "article" and not is_article(r):
            continue
        elif args.scope == "both" and not (is_xsj(r) or is_long(r)):
            continue
        # 'all' falls through with no filter
        imgs = list(r.get("images") or [])
        b = r.get("banner_image")
        if b:
            imgs.append(b)
        for u in imgs:
            if not u or not u.startswith(("http://", "https://")):
                continue
            url_to_ids.setdefault(u, []).append(r["id"])

    n_urls = len(url_to_ids)
    print(f"scope={args.scope}: {n_urls:,} unique image URLs across "
          f"{sum(len(v) for v in url_to_ids.values()):,} references", flush=True)
    if n_skipped_post_cutoff:
        print(f"  (skipped {n_skipped_post_cutoff} article(s) dated after "
              f"{QDAILY_FINAL_DATE} — out of archive)", flush=True)

    # Resume: skip URLs that finished cleanly. Transient errors
    # ("cdx-error", "fetch-error", "http-error") get retried.
    # NOTE: legacy "no-snapshot" records (from before the prefix
    # fallback existed) are NOT in this set — they get re-tried on
    # this pass with the new two-stage logic. Only "no-snapshot-prefix"
    # (failed BOTH exact and prefix lookups) is permanent.
    PERMANENT_STATUSES = {"ok", "no-snapshot-prefix", "skip-live"}
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    seen_urls: set[str] = set()
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("status") in PERMANENT_STATUSES:
                    seen_urls.add(rec["url"])
            except (json.JSONDecodeError, KeyError):
                continue
    print(f"resume: {len(seen_urls):,} URLs already finished; "
          f"{n_urls - len(seen_urls):,} to go", flush=True)

    assets_root = Path(args.assets)

    # One client shared across workers. httpx.Client is thread-safe
    # for concurrent reads/writes per the docs.
    client = httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT)
    limiter = RateLimiter(args.rate)
    asset_lock = threading.Lock()
    write_lock = threading.Lock()

    todo = [u for u in url_to_ids if u not in seen_urls]
    if args.limit:
        todo = todo[: args.limit]

    # Three traffic profiles, chosen randomly per batch. The slow profile
    # gets us through Wayback's throttle windows when the faster two start
    # collecting cdx-errors; mixing all three keeps the request shape from
    # looking like a clean stair-step.
    PROFILES = [
        {"rate": 1.0, "workers": 2},
        {"rate": 2.0, "workers": 4},
        {"rate": 4.0, "workers": 3},
    ]

    # Throttle detection: if a batch has >= THROTTLE_PCT% cdx-errors,
    # back off with exponentially growing cooldowns before the next batch.
    THROTTLE_PCT = 60        # % of batch results that are errors
    COOLDOWN_BASE = 30.0     # initial backoff seconds
    COOLDOWN_MAX = 600.0     # cap at 10 minutes
    COOLDOWN_RESET_OK = 20   # consecutive non-error results to reset backoff

    n_ok = n_miss = n_err = n_skip = 0
    n_processed = 0
    batch_num = 0
    consecutive_throttles = 0

    print(f"random-profile mode: alternating between "
          f"{PROFILES[0]} and {PROFILES[1]}", flush=True)
    print(f"throttle detection: backoff when ≥{THROTTLE_PCT}% errors in a batch",
          flush=True)

    with manifest_path.open("a", encoding="utf-8") as mfout:
        offset = 0
        while offset < len(todo):
            profile = random.choice(PROFILES)
            batch_size = random.randint(80, 200)
            batch = todo[offset : offset + batch_size]
            offset += len(batch)
            batch_num += 1

            limiter = RateLimiter(profile["rate"])
            tag = (f"batch {batch_num}: {len(batch)} URLs, "
                   f"{profile['rate']} req/s × {profile['workers']}w")
            print(f"\n▸ {tag}", flush=True)

            batch_ok = 0
            batch_err = 0

            with ThreadPoolExecutor(max_workers=profile["workers"]) as ex:
                futs = {
                    ex.submit(process_one, u, url_to_ids[u], client,
                              limiter, assets_root, asset_lock): u
                    for u in batch
                }
                for fut in as_completed(futs):
                    rec = fut.result()
                    with write_lock:
                        mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        mfout.flush()
                        n_processed += 1
                        s = rec["status"]
                        if s == "ok":
                            n_ok += 1
                            batch_ok += 1
                        elif s in ("no-snapshot-prefix",):
                            n_miss += 1
                            batch_ok += 1  # legit miss, not throttle
                        elif s == "skip-live":
                            n_skip += 1
                            batch_ok += 1
                        else:
                            n_err += 1
                            batch_err += 1
                        if n_processed % 50 == 0:
                            print(f"  {n_processed:>5}/{len(todo):<5} "
                                  f"ok={n_ok} miss={n_miss} err={n_err} "
                                  f"skip={n_skip}", flush=True)

            # Throttle detection
            err_pct = (batch_err / len(batch) * 100) if batch else 0
            if err_pct >= THROTTLE_PCT:
                consecutive_throttles += 1
                cooldown = min(COOLDOWN_BASE * (2 ** (consecutive_throttles - 1)),
                               COOLDOWN_MAX)
                print(f"  ⚠ batch {batch_num}: {err_pct:.0f}% errors — "
                      f"throttled (streak {consecutive_throttles}), "
                      f"cooling down {cooldown:.0f}s", flush=True)
                time.sleep(cooldown)
            else:
                if consecutive_throttles:
                    print(f"  ✓ throttle cleared after {consecutive_throttles} "
                          f"consecutive throttled batches", flush=True)
                consecutive_throttles = 0
                # Normal inter-batch pause (1–4s)
                gap = random.uniform(1.0, 4.0)
                print(f"  ✓ batch {batch_num} done ({batch_ok} ok, "
                      f"{batch_err} err) — pausing {gap:.1f}s", flush=True)
                time.sleep(gap)

    print()
    print(f"done. ok={n_ok}  miss={n_miss}  err={n_err}  skip={n_skip}  "
          f"({n_processed} processed this run, {batch_num} batches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
