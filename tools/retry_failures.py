"""
Stage B-retry — second-chance fetch of articles in data/failures.jsonl.

The original fetch_wayback.py uses Wayback's `id_` raw-bytes flag for
clean inputs, but a small minority of snapshots fail it consistently
(RemoteProtocolError on chunked-read). This tool re-tries each failed
ID with two variants:

    1. /web/<ts>id_/<orig>          — the original raw-bytes attempt
    2. /web/<ts>/<orig>             — toolbar-wrapped (still serves
                                      QDaily's article DOM intact;
                                      extract.py now strips Wayback's
                                      url-prefix from img / href.)

A response is accepted only if its body contains the QDaily marker
class `.article-detail-bd`. Successful fetches are written to
cache/<id>.html so the next extract pass picks them up; persistent
failures stay in failures.jsonl and continue rendering as
screenshot-only stubs.
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
TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class TransientError(Exception):
    pass


def wayback_id_url(archive_url: str, archive_ts: str) -> str:
    marker = f"/web/{archive_ts}/"
    if marker not in archive_url:
        return archive_url
    return archive_url.replace(marker, f"/web/{archive_ts}id_/", 1)


@retry(
    retry=retry_if_exception_type(TransientError),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, follow_redirects=True)
    if r.status_code in (429, 500, 502, 503, 504, 520, 522, 524):
        raise TransientError(f"{r.status_code}")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/articles.jsonl")
    ap.add_argument("--failures", default="data/failures.jsonl")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--rate", type=float, default=0.7)
    args = ap.parse_args()

    manifest = {}
    for line in Path(args.manifest).read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        m = json.loads(line)
        manifest[m["id"]] = m

    failed_ids: set[int] = set()
    fail_path = Path(args.failures)
    if fail_path.exists():
        for line in fail_path.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                failed_ids.add(json.loads(line)["id"])
            except Exception:
                pass

    cache = Path(args.cache)
    todo = []
    for fid in sorted(failed_ids):
        cf = cache / f"{fid}.html"
        if cf.exists() and cf.stat().st_size > 0:
            continue  # already recovered (manual or earlier retry)
        if fid in manifest:
            todo.append(fid)

    print(f"failures known: {len(failed_ids)}; uncached + in manifest: {len(todo)}")
    if not todo:
        return 0

    recovered_via_id = recovered_via_no_id = 0
    still_failing = 0
    started = time.time()

    with httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT) as client:
        for i, fid in enumerate(todo):
            row = manifest[fid]
            url_id = wayback_id_url(row["archive_url"], row["archive_ts"])
            url_plain = row["archive_url"]
            saved = None
            errs: list[str] = []

            for label, url in (("id_", url_id), ("no_id_", url_plain)):
                t0 = time.time()
                try:
                    r = _get(client, url)
                except Exception as e:
                    errs.append(f"{label}:{type(e).__name__}")
                else:
                    if r.status_code != 200:
                        errs.append(f"{label}:http_{r.status_code}")
                    elif "article-detail-bd" not in r.text:
                        errs.append(f"{label}:no_body")
                    else:
                        cf = cache / f"{fid}.html"
                        cf.write_bytes(r.content)
                        saved = label
                        break
                # rate-limit between variant attempts
                elapsed = time.time() - t0
                sleep = max(0.0, args.rate - elapsed)
                if sleep:
                    time.sleep(sleep)

            if saved == "id_":
                recovered_via_id += 1
            elif saved == "no_id_":
                recovered_via_no_id += 1
            else:
                still_failing += 1

            if (i + 1) % 25 == 0 or i < 3 or saved:
                tag = saved or ("FAIL " + ",".join(errs))
                print(f"  [{i+1}/{len(todo)}] id={fid}  {tag}")

            time.sleep(args.rate)

    dur = time.time() - started
    print(
        f"\nDone in {dur:.1f}s.  "
        f"recovered_id_={recovered_via_id}  "
        f"recovered_no_id_={recovered_via_no_id}  "
        f"still_failing={still_failing}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
