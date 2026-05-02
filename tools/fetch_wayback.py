"""
Stage B — Fetch raw article HTML from the Wayback Machine.

For each article in data/articles.jsonl, GETs the Wayback `id_` variant of its
archived URL and writes the raw response to cache/<id>.html. Resumable: cached
IDs are skipped. Failures are appended to data/failures.jsonl.

The `id_` flag (e.g. /web/20190623143638id_/...) tells Wayback to serve the
original captured bytes without rewriting links or injecting its toolbar — much
cleaner input for Stage C extraction.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

UA = "qdaily-archive/0.1 (+contact: logoutx)"
TIMEOUT = httpx.Timeout(30.0, connect=15.0)


def wayback_id_url(archive_url: str, archive_ts: str) -> str:
    """Insert `id_` after the timestamp segment so Wayback returns raw bytes."""
    marker = f"/web/{archive_ts}/"
    if marker not in archive_url:
        return archive_url
    return archive_url.replace(marker, f"/web/{archive_ts}id_/", 1)


class TransientError(Exception):
    pass


@retry(
    retry=retry_if_exception_type(TransientError),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def fetch_one(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, follow_redirects=True)
    if r.status_code in (429, 500, 502, 503, 504, 520, 522, 524):
        raise TransientError(f"{r.status_code} on {url}")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/articles.jsonl")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--failures", default="data/failures.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="stop after N new fetches")
    ap.add_argument("--rate", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    cache_dir = Path(args.cache)
    fail_path = Path(args.failures)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fail_path.parent.mkdir(parents=True, exist_ok=True)

    # split('\n') — never splitlines(); see note in render.py.
    rows = [json.loads(l) for l in manifest_path.read_text(encoding="utf-8").split("\n") if l.strip()]
    print(f"Manifest: {len(rows)} articles")

    fetched_new = 0
    skipped_cached = 0
    failures = 0
    started = time.time()

    with httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT, http2=False) as client, \
         fail_path.open("a", encoding="utf-8") as ferr:
        for i, row in enumerate(rows):
            aid = row["id"]
            cache_file = cache_dir / f"{aid}.html"
            if cache_file.exists() and cache_file.stat().st_size > 0:
                skipped_cached += 1
                continue

            if args.limit is not None and fetched_new >= args.limit:
                break

            url = wayback_id_url(row["archive_url"], row["archive_ts"])

            t0 = time.time()
            try:
                r = fetch_one(client, url)
            except Exception as e:
                failures += 1
                ferr.write(json.dumps({
                    "id": aid, "url": url, "error": f"{type(e).__name__}: {e}",
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, ensure_ascii=False) + "\n")
                ferr.flush()
                print(f"  [{i+1}/{len(rows)}] id={aid} FAIL {type(e).__name__}")
            else:
                if r.status_code != 200:
                    failures += 1
                    ferr.write(json.dumps({
                        "id": aid, "url": url, "error": f"http_{r.status_code}",
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }, ensure_ascii=False) + "\n")
                    ferr.flush()
                    print(f"  [{i+1}/{len(rows)}] id={aid} HTTP {r.status_code}")
                else:
                    cache_file.write_bytes(r.content)
                    fetched_new += 1
                    if fetched_new <= 5 or fetched_new % 25 == 0:
                        print(f"  [{i+1}/{len(rows)}] id={aid} ok ({len(r.content):,} bytes)")

            elapsed = time.time() - t0
            sleep = max(0.0, args.rate - elapsed)
            if sleep:
                time.sleep(sleep)

    dur = time.time() - started
    print(f"\nDone in {dur:.1f}s.  fetched_new={fetched_new}  cached_skipped={skipped_cached}  failures={failures}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
