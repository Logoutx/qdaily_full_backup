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
import re
import sys
import time
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


def cdx_lookup(client: httpx.Client, url: str) -> tuple[str, str | None, int | None]:
    """Return (kind, ts, length) where kind is one of:
       - "ok"     : found a 200 snapshot
       - "empty"  : CDX returned empty list (no snapshot exists)
       - "error"  : CDX request failed (transient — caller may retry)
    Distinguishing these matters for resume: 'empty' is a permanent
    skip; 'error' should be retried on the next run.
    """
    cdx = (
        "https://web.archive.org/cdx/search/cdx?"
        f"url={quote(url, safe='')}"
        "&filter=statuscode:200"
        "&output=json"
        "&limit=1"
    )
    try:
        r = http_get(client, cdx)
    except Exception:
        return "error", None, None
    if r.status_code != 200:
        return "error", None, None
    text = r.text.strip()
    if not text:
        return "empty", None, None
    if not text.startswith("["):
        # HTML error page from upstream — treat as transient.
        return "error", None, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "error", None, None
    if len(data) < 2:
        return "empty", None, None
    row = data[1]
    ts = row[1]
    try:
        length = int(row[6])
    except (ValueError, IndexError):
        length = None
    return "ok", ts, length


def asset_path(assets_root: Path, article_id: int, url: str) -> Path:
    ext = Path(urlparse(url).path).suffix.lower() or ".bin"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return assets_root / str(article_id) / f"{digest}{ext}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl")
    ap.add_argument("--scope", choices=("xsj", "long", "both", "all"), default="xsj")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--manifest", default="data/images.jsonl")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="requests per second (CDX is sensitive)")
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
    url_to_ids: dict[str, list[int]] = {}
    for r in record_map.values():
        if args.scope == "xsj" and not is_xsj(r):
            continue
        elif args.scope == "long" and not is_long(r):
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

    # Resume: skip URLs that finished cleanly. Transient errors
    # ("cdx-error", "fetch-error", "http-error") get retried.
    PERMANENT_STATUSES = {"ok", "no-snapshot", "skip-live"}
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

    # CDX is the bottleneck (rate-limited). One client for both endpoints.
    client = httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT)
    delay = 1.0 / max(args.rate, 0.1)

    n_ok = n_miss = n_err = n_skip = 0
    n_processed = 0
    todo = [u for u in url_to_ids if u not in seen_urls]

    with manifest_path.open("a", encoding="utf-8") as mfout:
        for url in todo:
            if args.limit and n_processed >= args.limit:
                break
            ids = url_to_ids[url]
            primary_id = ids[0]

            # Pass through medium.com / live URLs without saving
            host = urlparse(url).netloc.lower()
            if host == "medium.com" or host.endswith(".medium.com"):
                rec = {"url": url, "status": "skip-live",
                       "ts": None, "path": None, "length": None}
                mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                mfout.flush()
                n_skip += 1
                n_processed += 1
                continue

            # 1) CDX lookup
            kind, ts, cdx_length = cdx_lookup(client, url)
            time.sleep(delay)
            if kind == "error":
                rec = {"url": url, "status": "cdx-error",
                       "ts": None, "path": None, "length": None}
                mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                mfout.flush()
                n_err += 1
                n_processed += 1
                continue
            if kind == "empty":
                rec = {"url": url, "status": "no-snapshot",
                       "ts": None, "path": None, "length": None}
                mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                mfout.flush()
                n_miss += 1
                n_processed += 1
                if n_processed % 50 == 0:
                    print(f"  {n_processed:>5}/{len(todo):<5} "
                          f"ok={n_ok} miss={n_miss} err={n_err} skip={n_skip}",
                          flush=True)
                continue

            # 2) Fetch the image bytes
            wb = f"https://web.archive.org/web/{ts}id_/{url}"
            target = asset_path(assets_root, primary_id, url)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                r = http_get(client, wb)
            except Exception as e:
                rec = {"url": url, "status": "fetch-error", "ts": ts,
                       "path": None, "length": cdx_length, "error": str(e)[:120]}
                mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                mfout.flush()
                n_err += 1
                n_processed += 1
                time.sleep(delay)
                continue

            if r.status_code != 200 or len(r.content) < 200:
                rec = {"url": url, "status": "http-error", "ts": ts,
                       "http_status": r.status_code, "path": None,
                       "length": len(r.content)}
                mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                mfout.flush()
                n_err += 1
                n_processed += 1
                time.sleep(delay)
                continue

            # Atomic write
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(r.content)
            tmp.rename(target)

            # Hard-link the same blob under every other referencing article id
            # so resolve_url(article_id, url) finds it without a per-id refetch.
            for other_id in ids[1:]:
                link_target = asset_path(assets_root, other_id, url)
                link_target.parent.mkdir(parents=True, exist_ok=True)
                if link_target.exists():
                    continue
                try:
                    link_target.hardlink_to(target)
                except OSError:
                    # Fallback to a copy if FS doesn't support hard links
                    link_target.write_bytes(r.content)

            rec = {
                "url": url, "status": "ok", "ts": ts,
                "path": str(target.relative_to(Path("."))),
                "length": len(r.content),
                "linked_ids": ids,
            }
            mfout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            mfout.flush()
            n_ok += 1
            n_processed += 1
            time.sleep(delay)
            if n_processed % 50 == 0:
                print(f"  {n_processed:>5}/{len(todo):<5} "
                      f"ok={n_ok} miss={n_miss} err={n_err} skip={n_skip}",
                      flush=True)

    print()
    print(f"done. ok={n_ok}  miss={n_miss}  err={n_err}  skip={n_skip}  "
          f"({n_processed} processed this run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
