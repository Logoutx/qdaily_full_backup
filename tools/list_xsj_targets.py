"""Emit JSONL of 好奇心小数据 articles that need image recovery.

Modes:
  --mode partial   articles with >=1 missing AND >=1 local image (anchor-able)
  --mode full      articles with all images missing (no anchors)

Each line:
  {"id": int, "title": str, "missing": int, "have": int, "total": int}

Default sort: id ascending (so resumable runs are predictable).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse


def fn(u: str) -> str:
    ext = Path(urlparse(u).path).suffix.lower() or ".bin"
    return hashlib.sha1(u.encode("utf-8")).hexdigest()[:16] + ext


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("partial", "full"), default="partial")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--exclude-ids", default="",
                    help="Comma-separated article IDs to skip (e.g. already done).")
    args = ap.parse_args()

    excluded: set[int] = {int(x) for x in args.exclude_ids.split(",") if x.strip()}
    records: dict[int, dict] = {}
    for p in sorted(Path(args.data_dir).glob("articles_extracted_*.jsonl")):
        with p.open() as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    records[r["id"]] = r

    assets_root = Path(args.assets)
    rows = []
    for r in records.values():
        if r["id"] in excluded:
            continue
        title = r.get("title") or ""
        if "好奇心小数据" not in title:
            continue
        imgs = list(r.get("images") or [])
        bi = r.get("banner_image")
        if bi:
            imgs.append(bi)
        imgs = [u for u in imgs if u and u.startswith(("http://", "https://"))]
        if not imgs:
            continue
        miss = [u for u in imgs if not (assets_root / str(r["id"]) / fn(u)).exists()]
        if not miss:
            continue
        have = len(imgs) - len(miss)
        if args.mode == "partial" and have == 0:
            continue
        if args.mode == "full" and have != 0:
            continue
        rows.append({
            "id": r["id"],
            "title": title,
            "missing": len(miss),
            "have": have,
            "total": len(imgs),
        })

    rows.sort(key=lambda x: x["id"])
    for r in rows:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"# {len(rows)} articles emitted (mode={args.mode})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
