"""
Two-phase batch recovery for newly-discovered Douban reposts.

Inputs:
  data/douban_notes_scraped.json — output from the Chrome scraper, one record
    per matched article: {"qd": int, "db": int, "title": str, "imgs": [{"url": str}, ...]}
    (no w/h — Douban's HTML doesn't carry them)

Phase 1: cache each Douban image to .douban_cache/<note_id>/<idx>.jpg, read
its real dimensions on disk (via the `file` command, same as fetch_douban.py).

Phase 2: feed (article_id, note_url, [{i,w,h,url}, ...]) into the existing
fetch_douban.py.process() function — that handles alignment, downloads to
the QDaily asset path (will use cache to skip re-download if same sha1),
and updates data/images.jsonl.

Safe-only by default: --allow-no-anchor is passed through to .process(), so
articles without local anchors only get recovered when the count+banner
pattern matches strictly.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

# Import alignment + placement from the existing tool.
sys.path.insert(0, str(Path(__file__).parent))
import fetch_douban as fd  # noqa: E402

CACHE_DIR = Path(".douban_cache")
UA = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36"


def cache_path(note_id: int, idx: int) -> Path:
    return CACHE_DIR / str(note_id) / f"{idx:03d}.jpg"


def download_for_dim(client: httpx.Client, url: str, dst: Path, referer: str) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 1000:
        return dst.stat().st_size
    r = client.get(url, headers={"User-Agent": UA, "Referer": referer}, timeout=30.0)
    r.raise_for_status()
    dst.write_bytes(r.content)
    return len(r.content)


_DIM_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def read_dim(path: Path) -> tuple[int, int] | None:
    """Return (w, h) by reading the image header via `file`. None on failure."""
    try:
        out = subprocess.run(["file", str(path)], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    matches = _DIM_RE.findall(out)
    if not matches:
        return None
    w, h = matches[-1]
    try:
        return int(w), int(h)
    except ValueError:
        return None


def cache_one_note(client, note_id, db_imgs):
    """Download every douban image for a note into the cache, fill in w/h."""
    enriched = []
    referer = f"https://www.douban.com/note/{note_id}/"
    for idx, img in enumerate(db_imgs):
        path = cache_path(note_id, idx)
        try:
            download_for_dim(client, img["url"], path, referer)
        except Exception as e:
            enriched.append({"i": idx, "url": img["url"], "w": None, "h": None,
                             "error": f"download: {e}"})
            continue
        dim = read_dim(path)
        if dim is None:
            enriched.append({"i": idx, "url": img["url"], "w": None, "h": None,
                             "error": "no-dims"})
            continue
        w, h = dim
        enriched.append({"i": idx, "url": img["url"], "w": w, "h": h})
        # Slight inter-image delay to be polite to img CDNs.
    return note_id, enriched


def load_qd_records(data_dir: Path) -> dict[int, dict]:
    """{article_id: extracted_record}, last-wins across files."""
    out = {}
    for p in sorted(data_dir.glob("articles_extracted_*.jsonl")):
        for line in p.read_text(encoding="utf-8").split("\n"):
            if not line.strip(): continue
            try: r = json.loads(line)
            except json.JSONDecodeError: continue
            out[r["id"]] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scraped", type=Path, default=Path("data/douban_notes_scraped.json"))
    ap.add_argument("--manifest", type=Path, default=Path("data/images.jsonl"))
    ap.add_argument("--assets", type=Path, default=Path("assets"))
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--allow-no-anchor", action="store_true",
                    help="Fall back to count+banner pattern when no local anchors exist.")
    ap.add_argument("--min-anchors", type=int, default=2)
    ap.add_argument("--max-avg-diff", type=float, default=0.02)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cache-workers", type=int, default=6,
                    help="Concurrent Douban image cache downloads")
    ap.add_argument("--only-id", type=int, default=None,
                    help="Process just one QDaily article id (for testing)")
    args = ap.parse_args()

    scraped = json.loads(args.scraped.read_text(encoding="utf-8"))
    if args.only_id is not None:
        scraped = [s for s in scraped if s["qd"] == args.only_id]
    print(f"matched articles to process: {len(scraped)}", flush=True)

    # Phase 1: cache + dim-read each Douban image. Parallelise per-note.
    print("\nphase 1: caching Douban images + reading dimensions ...", flush=True)
    dim_client = httpx.Client(timeout=30.0, follow_redirects=True)
    notes_with_dims: dict[int, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=args.cache_workers) as pool:
        futures = {
            pool.submit(cache_one_note, dim_client, rec["db"], rec["imgs"]): rec
            for rec in scraped
        }
        done = 0
        for fut in as_completed(futures):
            rec = futures[fut]
            try:
                note_id, enriched = fut.result()
                notes_with_dims[rec["db"]] = enriched
            except Exception as e:
                print(f"  ERR caching {rec['db']}: {e}", flush=True)
            done += 1
            if done % 25 == 0 or done == len(scraped):
                print(f"  {done}/{len(scraped)} notes cached", flush=True)
    dim_client.close()

    # Phase 2: alignment + placement via fetch_douban.py.process().
    print("\nphase 2: alignment + asset placement ...", flush=True)
    qd_records = load_qd_records(args.data_dir)
    place_client = httpx.Client(timeout=fd.TIMEOUT, follow_redirects=True)
    manifest_updates: dict[str, dict] = {}
    summary = {"ok": 0, "ok-already-complete": 0, "skip-no-images": 0,
               "skip-no-anchor": 0, "skip-no-anchor-count-diff": 0,
               "skip-no-anchor-no-banner-pattern": 0,
               "skip-no-alignment": 0, "error": 0, "no-record": 0}
    placed_total = 0
    for rec in scraped:
        aid = rec["qd"]
        db = rec["db"]
        note_url = f"https://www.douban.com/note/{db}/"
        if aid not in qd_records:
            summary["no-record"] += 1
            continue
        db_imgs = notes_with_dims.get(db) or []
        db_imgs = [d for d in db_imgs if d.get("w") and d.get("h")]
        if not db_imgs:
            summary["skip-no-images"] += 1
            continue
        result = fd.process(
            aid, note_url, db_imgs,
            rec=qd_records[aid],
            assets_root=args.assets,
            manifest_updates=manifest_updates,
            min_anchors=args.min_anchors,
            max_avg_diff=args.max_avg_diff,
            allow_no_anchor=args.allow_no_anchor,
            client=place_client,
            dry_run=args.dry_run,
        )
        st = result.get("status") or "?"
        summary[st] = summary.get(st, 0) + 1
        placed_total += result.get("placed", 0) or 0
        if st == "ok":
            print(f"  id={aid:>5}  offset={result.get('offset'):+d}  "
                  f"anchors={result.get('anchors')}  placed={result.get('placed')}/{result.get('qd_total')}",
                  flush=True)
    place_client.close()

    # Phase 3: rewrite images.jsonl with updates (already-handled by fetch_douban
    # in its main(), but we go direct via update_manifest).
    if manifest_updates and not args.dry_run:
        print("\nphase 3: rewriting images.jsonl ...", flush=True)
        # Append any URLs not already in the manifest, rewrite existing rows.
        existing_urls = set()
        for line in args.manifest.read_text(encoding="utf-8").split("\n"):
            if not line.strip(): continue
            try: existing_urls.add(json.loads(line)["url"])
            except: pass
        rewrites = {u: v for u, v in manifest_updates.items() if u in existing_urls}
        appends = [v for u, v in manifest_updates.items() if u not in existing_urls]
        if rewrites:
            n_rewritten = fd.update_manifest(args.manifest, rewrites)
            print(f"  rewrote {n_rewritten} existing rows", flush=True)
        if appends:
            with args.manifest.open("a", encoding="utf-8") as fh:
                for v in appends:
                    fh.write(json.dumps(v, ensure_ascii=False) + "\n")
            print(f"  appended {len(appends)} new rows", flush=True)

    # Summary
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        if v: print(f"  {k}: {v}")
    print(f"  images placed total: {placed_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
