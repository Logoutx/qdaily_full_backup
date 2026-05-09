"""
Fill missing 好奇心小数据 article images from Douban reposts.

Apply-phase script: pure Python, no Chrome. Discovery happens upstream
(by an operator driving the Claude-in-Chrome MCP) and is recorded in
``data/douban_notes.jsonl``.

Input lines (one per article we have a Douban repost for):
    {"article_id": int,
     "douban_note_url": str,
     "douban_images": [{"i": int, "w": int, "h": int, "url": str}, ...]}

The script:
  1. Loads each article's QDaily image list from ``data/articles_extracted_*.jsonl``.
  2. Computes aspect ratios for QDaily images that already exist locally
     ("anchors") by reading file headers via the ``file`` command.
  3. Finds the alignment offset k that minimizes |aspect_qd[i] - aspect_db[i+k]|
     over all anchors. Threshold: at least 2 anchors AND avg diff < 0.02
     (configurable). Articles with no anchors are skipped unless
     ``--allow-no-anchor`` is set, in which case len(douban)==len(qdaily)+0/1/2
     is required and offset=0/1/2 is tried.
  4. Downloads the Douban candidate for each missing QDaily index with a
     Referer header pointing at the note (Douban hotlink-protects).
  5. Saves to ``assets/<article_id>/<sha1>.<ext>`` matching ``asset_path``
     in fetch_images.py so render's ``--image-mode local`` picks them up.
  6. Updates ``data/images.jsonl``: each touched URL is rewritten with
     ``status=ok``, ``source=douban``, and the originating Douban URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def asset_path(assets_root: Path, article_id: int, url: str) -> Path:
    ext = Path(urlparse(url).path).suffix.lower() or ".bin"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return assets_root / str(article_id) / f"{digest}{ext}"


_DIM_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def read_dim(path: Path) -> tuple[int, int] | None:
    """Read image width x height via the macOS `file` command. Robust
    enough for JPEG / PNG / WebP without adding Pillow as a dependency.
    Returns the LAST NxN match in the description (skipping density 1x1)."""
    if not path.is_file() or path.stat().st_size < 100:
        return None
    out = subprocess.run(
        ["file", "-b", str(path)], capture_output=True, text=True, check=False
    ).stdout
    matches = _DIM_RE.findall(out)
    if not matches:
        return None
    w, h = matches[-1]
    return int(w), int(h)


def load_articles(data_dir: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for p in sorted(data_dir.glob("articles_extracted_*.jsonl")):
        with p.open() as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out[r["id"]] = r
    return out


def qdaily_images(rec: dict) -> list[str]:
    imgs = list(rec.get("images") or [])
    bi = rec.get("banner_image")
    if bi:
        imgs.append(bi)
    return [u for u in imgs if u and u.startswith(("http://", "https://"))]


def find_alignment(
    qd_aspects: list[float | None],
    db_aspects: list[float],
    *,
    min_anchors: int,
    max_avg_diff: float,
) -> tuple[int, int, float] | None:
    """Return (offset, n_anchors, avg_diff) or None.

    offset k means qd[i] should align with db[i + k]. Search k over a
    range that allows db to have leading banner / trailing promo images."""
    n_qd = len(qd_aspects)
    n_db = len(db_aspects)
    best: tuple[int, int, float] | None = None
    # Allow up to 3 leading and 3 trailing extras on Douban side.
    for k in range(-3, 4):
        if any(0 <= i + k < n_db for i in range(n_qd)) is False:
            continue
        diffs = []
        for i, a in enumerate(qd_aspects):
            if a is None:
                continue
            j = i + k
            if 0 <= j < n_db:
                diffs.append(abs(a - db_aspects[j]))
        if len(diffs) < min_anchors:
            continue
        avg = sum(diffs) / len(diffs)
        if avg > max_avg_diff:
            continue
        if best is None or avg < best[2]:
            best = (k, len(diffs), avg)
    return best


def download(client: httpx.Client, url: str, referer: str, dst: Path) -> int:
    """Stream-download to dst. Returns content-length."""
    headers = {"User-Agent": UA, "Referer": referer}
    with client.stream("GET", url, headers=headers, timeout=TIMEOUT) as r:
        r.raise_for_status()
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        size = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
                size += len(chunk)
        if size < 1000:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"too small ({size} B): {url}")
        tmp.replace(dst)
        return size


def update_manifest(
    manifest_path: Path,
    updates: dict[str, dict],
) -> int:
    """Rewrite manifest with the URLs in `updates` replaced. Returns
    the number of lines updated."""
    if not updates:
        return 0
    src = manifest_path
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    n = 0
    with src.open() as f, tmp.open("w") as out:
        for line in f:
            if not line.strip():
                out.write(line)
                continue
            r = json.loads(line)
            new = updates.get(r.get("url"))
            if new is not None:
                # Preserve existing linked_ids if any
                existing = set(r.get("linked_ids") or [])
                merged = sorted(existing | set(new.get("linked_ids") or []))
                new = dict(new)
                new["linked_ids"] = merged
                out.write(json.dumps(new, ensure_ascii=False) + "\n")
                n += 1
            else:
                out.write(line)
    tmp.replace(src)
    return n


def process(
    article_id: int,
    note_url: str,
    db_imgs: list[dict],
    *,
    rec: dict,
    assets_root: Path,
    manifest_updates: dict[str, dict],
    min_anchors: int,
    max_avg_diff: float,
    allow_no_anchor: bool,
    client: httpx.Client,
    dry_run: bool,
) -> dict:
    """Process one article. Returns a result dict for the run report."""
    qd_urls = qdaily_images(rec)
    if not qd_urls:
        return {"id": article_id, "status": "skip-no-images"}
    # Aspect ratio per QDaily image where local file exists.
    qd_aspects: list[float | None] = []
    for u in qd_urls:
        path = asset_path(assets_root, article_id, u)
        dim = read_dim(path) if path.exists() else None
        qd_aspects.append((dim[1] / dim[0]) if dim else None)
    db_imgs_sorted = sorted(db_imgs, key=lambda d: d["i"])
    # Skip Douban images with bogus 0x0 dimensions (failed to load on extract).
    db_aspects = [
        (d["h"] / d["w"]) if (d.get("w") and d.get("h")) else None
        for d in db_imgs_sorted
    ]
    n_anchors_raw = sum(1 for a in qd_aspects if a is not None)

    if n_anchors_raw >= min_anchors:
        # Aspect-anchor alignment requires non-null db_aspects too; skip
        # any offsets that would compare against a null db aspect.
        align = find_alignment(
            qd_aspects,
            [a if a is not None else 9.99 for a in db_aspects],
            min_anchors=min_anchors, max_avg_diff=max_avg_diff,
        )
        if align is None:
            return {"id": article_id, "status": "skip-no-alignment",
                    "anchors_available": n_anchors_raw}
        offset, n_anch, avg_diff = align
    elif allow_no_anchor:
        # SAFE-ONLY: require Douban count to match QDaily exactly with offset=0
        # OR count_diff == 1 (one banner) with offset=1
        # OR count_diff == 2 (banner + promo) with offset=1.
        # Refuse anything else to avoid silent misalignment.
        n_qd = len(qd_urls)
        n_db = len(db_imgs_sorted)
        diff = n_db - n_qd
        if diff == 0:
            offset = 0
        elif diff in (1, 2):
            # Tolerate +1 banner or +1 banner +1 promo IFF first/last imgs
            # look like banners (aspect < 0.8 — wider than ~5:4).
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
                        "n_qd": n_qd, "n_db": n_db, "diff": diff,
                        "head_a": round(first_a, 3), "tail_a": round(last_a, 3)}
        else:
            return {"id": article_id, "status": "skip-no-anchor-count-diff",
                    "n_qd": n_qd, "n_db": n_db, "diff": diff}
        n_anch, avg_diff = 0, float("nan")
    else:
        return {"id": article_id, "status": "skip-no-anchor",
                "anchors_available": n_anchors_raw}

    placed: list[tuple[int, str, str, int]] = []
    skipped_existing = 0
    for i, u in enumerate(qd_urls):
        path = asset_path(assets_root, article_id, u)
        if path.exists():
            skipped_existing += 1
            continue
        j = i + offset
        if not (0 <= j < len(db_imgs_sorted)):
            continue
        db_url = db_imgs_sorted[j]["url"]
        if dry_run:
            placed.append((i, u, db_url, 0))
            continue
        try:
            size = download(client, db_url, note_url, path)
        except Exception as e:  # noqa: BLE001
            return {"id": article_id, "status": "error",
                    "error": f"download {db_url} -> {path}: {e}",
                    "offset": offset, "anchors": n_anch, "avg_diff": avg_diff}
        placed.append((i, u, db_url, size))
        manifest_updates[u] = {
            "url": u,
            "status": "ok",
            "ts": None,
            "path": str(path),
            "length": size,
            "linked_ids": [article_id],
            "source": "douban",
            "source_url": db_url,
            "source_note": note_url,
        }
        time.sleep(0.3)

    return {
        "id": article_id,
        "status": "ok" if placed else "ok-already-complete",
        "offset": offset,
        "anchors": n_anch,
        "avg_diff": round(avg_diff, 5) if avg_diff == avg_diff else None,
        "placed": len(placed),
        "skipped_existing": skipped_existing,
        "qd_total": len(qd_urls),
        "db_total": len(db_imgs_sorted),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--notes", default="data/douban_notes.jsonl",
                    help="Path to discovery JSONL (default: data/douban_notes.jsonl)")
    ap.add_argument("--manifest", default="data/images.jsonl")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--only-id", type=int, default=None)
    ap.add_argument("--min-anchors", type=int, default=2)
    ap.add_argument("--max-avg-diff", type=float, default=0.02)
    ap.add_argument("--allow-no-anchor", action="store_true",
                    help="If a record has 0 known-local images, fall back to "
                         "count-based offset detection (less safe).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="-",
                    help="Where to write per-article JSONL report (default: stdout)")
    args = ap.parse_args()

    notes_path = Path(args.notes)
    if not notes_path.exists():
        print(f"notes file missing: {notes_path}", file=sys.stderr)
        return 2
    articles = load_articles(Path(args.data_dir))
    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"manifest missing: {manifest}", file=sys.stderr)
        return 2
    assets_root = Path(args.assets)

    report_fh = sys.stdout if args.report == "-" else open(args.report, "w")
    manifest_updates: dict[str, dict] = {}
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True) as client:
        with notes_path.open() as nf:
            note_lines = list(nf)
        for line in note_lines:
            if not line.strip():
                continue
            note = json.loads(line)
            aid = note["article_id"]
            if args.only_id is not None and aid != args.only_id:
                continue
            rec = articles.get(aid)
            if rec is None:
                report_fh.write(json.dumps(
                    {"id": aid, "status": "skip-unknown-article"}) + "\n")
                continue
            result = process(
                aid, note["douban_note_url"], note["douban_images"],
                rec=rec, assets_root=assets_root,
                manifest_updates=manifest_updates,
                min_anchors=args.min_anchors,
                max_avg_diff=args.max_avg_diff,
                allow_no_anchor=args.allow_no_anchor,
                client=client,
                dry_run=args.dry_run,
            )
            report_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            report_fh.flush()

    if not args.dry_run:
        n = update_manifest(manifest, manifest_updates)
        print(f"\nManifest: {n} URL(s) updated -> {manifest}", file=sys.stderr)
    if report_fh is not sys.stdout:
        report_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
