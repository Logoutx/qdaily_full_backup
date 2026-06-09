"""
Leads-first image recovery.

Fetch the LEAD image (banner, else first body image) for every article whose
lead is not already on disk and not already known-dead — pulling Wayback's copy
into assets/<id>/<sha1><ext> so render serves it from cdn.qdaily.org instead of
hot-linking web.archive.org. Whatever Wayback doesn't have is recorded as
no-snapshot-prefix, after which render routes it straight to the placeholder
(no doomed Wayback request).

Gentle by design (single slow rate, low concurrency, hard backoff) to slip under
Wayback's throttle. Reuses fetch_images.py's CDX lookup / fetch / asset save /
manifest record, so recovered leads land exactly where render expects them.

Resumable: recomputes the candidate set on each start. A lead drops out once its
asset exists on disk or its status becomes ok / no-snapshot-prefix / skip-live.

Usage:
    python -u tools/fetch_missing_leads.py --rate 1.2 --workers 2
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
from urllib.parse import urlparse

import httpx

from fetch_images import UA, TIMEOUT, RateLimiter, process_one, asset_path

# Statuses that mean "don't bother (re)fetching this lead".
SETTLED = {"ok", "no-snapshot-prefix", "skip-live"}
BROKEN_HOSTS = {"121.201.7.32:8001"}


def latest_status(manifest: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not manifest.exists():
        return out
    for line in manifest.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        u = rec.get("url")
        if u:
            out[u] = rec.get("status")
    return out


def build_candidates(records_glob: str, assets: Path, status: dict[str, str],
                     scope: str = "leads") -> dict[str, list[int]]:
    """url -> [article ids] for missing, fetch-worth images.

    scope="leads": only each article's lead image (banner, else first body img).
    scope="all":   the banner plus every body image — the full mirror set.
    Skips images already on disk, already-settled (ok/no-snapshot/skip-live),
    broken hosts, and live externals (medium)."""
    out: dict[str, list[int]] = {}
    for f in sorted(glob.glob(records_glob)):
        for line in Path(f).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("is_screenshot_only"):
                continue
            imgs = r.get("images") or []
            if scope == "all":
                urls = ([r["banner_image"]] if r.get("banner_image") else []) + list(imgs)
            else:
                lead = r.get("banner_image") or (imgs[0] if imgs else None)
                urls = [lead] if lead else []
            for u in urls:
                if not u or not u.startswith(("http://", "https://")):
                    continue
                host = urlparse(u).netloc.lower()
                if host in BROKEN_HOSTS or host == "medium.com" or host.endswith(".medium.com"):
                    continue
                try:
                    p = asset_path(assets, r["id"], u)
                except ValueError:
                    continue
                if p and p.exists():
                    continue
                if status.get(u) in SETTLED:
                    continue
                out.setdefault(u, []).append(r["id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl")
    ap.add_argument("--manifest", default="data/images.jsonl")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--scope", choices=("leads", "all"), default="leads",
                    help="'leads' = banner/first image per article; "
                         "'all' = banner + every body image (full mirror)")
    ap.add_argument("--rate", type=float, default=1.2,
                    help="GLOBAL requests/sec (keep low to avoid throttling)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    manifest = Path(args.manifest)
    assets = Path(args.assets)
    status = latest_status(manifest)
    url_to_ids = build_candidates(args.records_glob, assets, status, args.scope)
    todo = list(url_to_ids)
    if args.limit:
        todo = todo[: args.limit]
    print(f"missing-image candidates (scope={args.scope}): {len(url_to_ids):,}"
          + (f" (limited to {len(todo):,})" if args.limit else ""), flush=True)
    if not todo:
        print("nothing to do.")
        return 0
    print(f"fetching at {args.rate} req/s x {args.workers}w", flush=True)

    client = httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT)
    limiter = RateLimiter(args.rate)
    asset_lock = threading.Lock()
    write_lock = threading.Lock()

    THROTTLE_PCT = 50
    COOLDOWN_BASE = 30.0
    COOLDOWN_MAX = 600.0

    n_ok = n_miss = n_err = n_done = 0
    streak = batch_num = 0

    with manifest.open("a", encoding="utf-8") as mfout:
        offset = 0
        while offset < len(todo):
            batch = todo[offset:offset + random.randint(40, 80)]
            offset += len(batch)
            batch_num += 1
            b_err = 0
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(process_one, u, url_to_ids[u], client,
                                  limiter, assets, asset_lock): u for u in batch}
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
                    else:
                        n_err += 1
                        b_err += 1
            err_pct = (b_err / len(batch) * 100) if batch else 0
            print(f"  batch {batch_num}: {len(batch)} urls | "
                  f"cumulative ok={n_ok} miss={n_miss} err={n_err} "
                  f"({n_done}/{len(todo)})", flush=True)
            if err_pct >= THROTTLE_PCT:
                streak += 1
                cd = min(COOLDOWN_BASE * (2 ** (streak - 1)), COOLDOWN_MAX)
                print(f"  !! {err_pct:.0f}% errors - backing off {cd:.0f}s "
                      f"(streak {streak})", flush=True)
                time.sleep(cd)
            else:
                streak = 0
                time.sleep(random.uniform(2.0, 5.0))

    print(f"\ndone. recovered={n_ok}  no-snapshot={n_miss}  "
          f"transient-err={n_err}  ({n_done} processed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
