"""
Apply phase for fetch_douban_batch.py output, using ONLY the local
.douban_cache/ tree — no further network calls to Douban.

Phase 1 (caching) ran via httpx and completed all 211 notes / 2,611 images
without bot resistance. This script finishes the alignment + placement
purely from on-disk cache, copying cached files to the QDaily asset path.
Honors the user request to stop scripted Douban traffic.

For each (article_id, douban_note_id) pair in douban_notes_scraped.json:
  1. Read each cached image's real dimensions via the `file` command.
  2. Run fetch_douban.find_alignment + the safe-no-anchor pattern from
     fetch_douban.process() — same alignment rules as the live tool.
  3. Copy cached files to assets/<qd_id>/<sha1_of_qd_url>.<ext>.
  4. Append rows to data/images.jsonl with source="douban".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import fetch_douban as fd  # noqa: E402

CACHE_DIR = Path(".douban_cache")


def cache_path(note_id: int, idx: int) -> Path:
    return CACHE_DIR / str(note_id) / f"{idx:03d}.jpg"


_DIM_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def read_dim(path: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run(["file", str(path)], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    matches = _DIM_RE.findall(out)
    if not matches:
        return None
    try:
        w, h = int(matches[-1][0]), int(matches[-1][1])
        if w <= 0 or h <= 0:
            return None
        return w, h
    except ValueError:
        return None


def asset_path(assets_root: Path, article_id: int, qdaily_url: str) -> Path:
    """Same digest scheme as render.py + fetch_images.py."""
    digest = hashlib.sha1(qdaily_url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(urlparse(qdaily_url).path).suffix.lower() or ".bin"
    return assets_root / str(article_id) / f"{digest}{suffix}"


def qdaily_images(rec: dict) -> list[str]:
    imgs = list(rec.get("images") or [])
    bi = rec.get("banner_image")
    if bi:
        imgs.append(bi)
    return [u for u in imgs if u and u.startswith(("http://", "https://"))]


def load_qd_records(data_dir: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for p in sorted(data_dir.glob("articles_extracted_*.jsonl")):
        for line in p.read_text(encoding="utf-8").split("\n"):
            if not line.strip(): continue
            try: r = json.loads(line)
            except json.JSONDecodeError: continue
            out[r["id"]] = r
    return out


def process_one(article_id: int, note_id: int, db_url_list: list[str],
                qd_rec: dict, assets_root: Path,
                manifest_appends: list[dict], min_anchors: int,
                max_avg_diff: float, allow_no_anchor: bool) -> dict:
    qd_urls = qdaily_images(qd_rec)
    if not qd_urls:
        return {"id": article_id, "status": "skip-no-images"}

    # Dimensions of cached Douban images, in order.
    db_imgs: list[dict] = []
    for idx, url in enumerate(db_url_list):
        p = cache_path(note_id, idx)
        if not p.exists():
            db_imgs.append({"i": idx, "url": url, "w": None, "h": None})
            continue
        dim = read_dim(p)
        if dim is None:
            db_imgs.append({"i": idx, "url": url, "w": None, "h": None})
            continue
        db_imgs.append({"i": idx, "url": url, "w": dim[0], "h": dim[1]})

    db_aspects = [
        (d["h"] / d["w"]) if (d.get("w") and d.get("h")) else None
        for d in db_imgs
    ]

    # QDaily anchors from any local files we already have.
    qd_aspects: list[float | None] = []
    for u in qd_urls:
        path = asset_path(assets_root, article_id, u)
        dim = read_dim(path) if path.exists() else None
        qd_aspects.append((dim[1] / dim[0]) if dim else None)
    n_anchors_raw = sum(1 for a in qd_aspects if a is not None)

    if n_anchors_raw >= min_anchors:
        align = fd.find_alignment(
            qd_aspects,
            [a if a is not None else 9.99 for a in db_aspects],
            min_anchors=min_anchors, max_avg_diff=max_avg_diff,
        )
        if align is None:
            return {"id": article_id, "status": "skip-no-alignment"}
        offset, n_anch, avg_diff = align
    elif allow_no_anchor:
        n_qd = len(qd_urls); n_db = len(db_imgs); diff = n_db - n_qd
        if diff == 0:
            offset = 0
        elif diff in (1, 2):
            first_a = db_aspects[0] or 9.99
            last_a = db_aspects[-1] or 9.99
            head_banner = first_a < 0.8
            tail_banner = last_a < 0.8
            if diff == 1 and head_banner:
                offset = 1
            elif diff == 2 and head_banner and tail_banner:
                offset = 1
            else:
                return {"id": article_id, "status": "skip-no-anchor-no-banner-pattern",
                        "n_qd": n_qd, "n_db": n_db, "diff": diff}
        else:
            return {"id": article_id, "status": "skip-no-anchor-count-diff",
                    "n_qd": n_qd, "n_db": n_db, "diff": diff}
        n_anch, avg_diff = 0, float("nan")
    else:
        return {"id": article_id, "status": "skip-no-anchor"}

    placed = 0
    skipped_existing = 0
    note_url = f"https://www.douban.com/note/{note_id}/"
    for i, u in enumerate(qd_urls):
        path = asset_path(assets_root, article_id, u)
        if path.exists():
            skipped_existing += 1
            continue
        j = i + offset
        if not (0 <= j < len(db_imgs)):
            continue
        src_cache = cache_path(note_id, j)
        if not src_cache.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_cache, path)
        size = path.stat().st_size
        manifest_appends.append({
            "url": u, "status": "ok", "ts": None,
            "path": str(path), "length": size,
            "linked_ids": [article_id],
            "source": "douban",
            "source_url": db_imgs[j]["url"],
            "source_note": note_url,
        })
        placed += 1

    return {"id": article_id, "status": "ok" if placed else "ok-already-complete",
            "offset": offset, "anchors": n_anch, "placed": placed,
            "skipped_existing": skipped_existing,
            "qd_total": len(qd_urls), "db_total": len(db_imgs)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scraped", type=Path, default=Path("data/douban_notes_scraped.json"))
    ap.add_argument("--manifest", type=Path, default=Path("data/images.jsonl"))
    ap.add_argument("--assets", type=Path, default=Path("assets"))
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--allow-no-anchor", action="store_true")
    ap.add_argument("--min-anchors", type=int, default=2)
    ap.add_argument("--max-avg-diff", type=float, default=0.02)
    args = ap.parse_args()

    scraped = json.loads(args.scraped.read_text(encoding="utf-8"))
    print(f"applying {len(scraped)} matched articles from local cache only "
          f"(no network) ...", flush=True)

    qd_records = load_qd_records(args.data_dir)
    manifest_appends: list[dict] = []
    summary: dict[str, int] = {}
    placed_total = 0

    for rec in scraped:
        aid = rec["qd"]
        if aid not in qd_records:
            summary["no-record"] = summary.get("no-record", 0) + 1
            continue
        db_urls = [img["url"] for img in rec.get("imgs") or []]
        if not db_urls:
            summary["skip-no-db-imgs"] = summary.get("skip-no-db-imgs", 0) + 1
            continue
        result = process_one(aid, rec["db"], db_urls, qd_records[aid],
                             args.assets, manifest_appends,
                             args.min_anchors, args.max_avg_diff,
                             args.allow_no_anchor)
        st = result.get("status") or "?"
        summary[st] = summary.get(st, 0) + 1
        if st == "ok":
            placed_total += result.get("placed", 0)
            print(f"  id={aid:>5}  offset={result['offset']:+d}  "
                  f"anchors={result.get('anchors')}  "
                  f"placed={result.get('placed')}/{result.get('qd_total')}",
                  flush=True)

    # Append all new images.jsonl rows in one shot.
    if manifest_appends:
        with args.manifest.open("a", encoding="utf-8") as fh:
            for row in manifest_appends:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("\n=== SUMMARY ===")
    for k, v in sorted(summary.items()):
        print(f"  {k}: {v}")
    print(f"  total images placed: {placed_total}")
    print(f"  manifest rows appended: {len(manifest_appends)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
