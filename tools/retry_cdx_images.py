"""
Gentle retry pass for image URLs that previously hit transient Wayback
errors (cdx-error / fetch-error / http-error) in data/images.jsonl.

Unlike fetch_images.py's random-profile crawler, this runs at a single,
deliberately slow request rate with conservative concurrency and hard
backoff — the goal is to slip *under* Wayback's throttle/defense rather
than push through it. It reuses fetch_images.py's proven CDX lookup,
asset save, and per-URL worker so recovered images land in
assets/<id>/<sha1><ext> exactly like the main pipeline (so
render.py --image-mode local picks them up with no further work).

Resumable: re-reads data/images.jsonl on each start. A URL is "done"
once any line records it as ok / no-snapshot-prefix / skip-live, so
re-running only re-attempts URLs still stuck on a transient error.

Usage:
    python tools/retry_cdx_images.py --rate 1.0 --workers 2
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from fetch_images import (
    UA,
    TIMEOUT,
    QDAILY_FINAL_DATE,
    RateLimiter,
    process_one,
)

RETRIABLE = {"cdx-error", "fetch-error", "http-error"}
PERMANENT = {"ok", "no-snapshot-prefix", "skip-live"}


def load_outstanding(manifest: Path) -> set[str]:
    """URLs whose latest meaningful state is a transient error."""
    done: set[str] = set()
    err: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        s, u = rec.get("status"), rec.get("url")
        if not u:
            continue
        if s in PERMANENT:
            done.add(u)
            err.discard(u)
        elif s in RETRIABLE and u not in done:
            err.add(u)
    return err


def map_urls_to_ids(records_glob: str, want: set[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for path in sorted(glob.glob(records_glob)):
        for line in Path(path).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            pd = r.get("publish_date") or ""
            if pd and pd > QDAILY_FINAL_DATE:
                continue
            imgs = list(r.get("images") or [])
            if r.get("banner_image"):
                imgs.append(r["banner_image"])
            for u in imgs:
                if u in want:
                    out.setdefault(u, []).append(r["id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl")
    ap.add_argument("--manifest", default="data/images.jsonl")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="GLOBAL requests/sec (keep low to avoid throttling)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    outstanding = load_outstanding(manifest_path)
    url_to_ids = map_urls_to_ids(args.records_glob, outstanding)
    todo = [u for u in url_to_ids]
    orphans = len(outstanding) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"outstanding transient-error URLs: {len(outstanding):,} "
          f"({orphans} not referenced by any in-scope article, skipped)",
          flush=True)
    print(f"retrying {len(todo):,} URLs at {args.rate} req/s × {args.workers}w",
          flush=True)
    if not todo:
        print("nothing to do.")
        return 0

    client = httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT)
    limiter = RateLimiter(args.rate)
    asset_lock = threading.Lock()
    write_lock = threading.Lock()

    # Backoff identical in spirit to fetch_images, but triggered gentler.
    THROTTLE_PCT = 50
    COOLDOWN_BASE = 30.0
    COOLDOWN_MAX = 600.0

    n_ok = n_miss = n_err = 0
    n_done = 0
    streak = 0
    batch_num = 0

    with manifest_path.open("a", encoding="utf-8") as mfout:
        offset = 0
        while offset < len(todo):
            batch = todo[offset:offset + random.randint(40, 80)]
            offset += len(batch)
            batch_num += 1
            b_err = 0
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(process_one, u, url_to_ids[u], client,
                                  limiter, Path(args.assets), asset_lock): u
                        for u in batch}
                for fut in as_completed(futs):
                    rec = fut.result()
                    with write_lock:
                        mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        mfout.flush()
                    n_done += 1
                    s = rec["status"]
                    if s == "ok":
                        n_ok += 1
                    elif s == "no-snapshot-prefix":
                        n_miss += 1
                    elif s in RETRIABLE:
                        n_err += 1
                        b_err += 1
            err_pct = (b_err / len(batch) * 100) if batch else 0
            print(f"  batch {batch_num}: {len(batch)} urls | "
                  f"cumulative ok={n_ok} miss={n_miss} err={n_err} "
                  f"({n_done}/{len(todo)})", flush=True)
            if err_pct >= THROTTLE_PCT:
                streak += 1
                cd = min(COOLDOWN_BASE * (2 ** (streak - 1)), COOLDOWN_MAX)
                print(f"  ⚠ {err_pct:.0f}% errors — backing off {cd:.0f}s "
                      f"(streak {streak})", flush=True)
                time.sleep(cd)
            else:
                streak = 0
                time.sleep(random.uniform(2.0, 5.0))

    print(f"\ndone. recovered={n_ok}  still-missing={n_miss}  "
          f"still-error={n_err}  ({n_done} processed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
