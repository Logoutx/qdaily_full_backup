"""Emit JSONL of long-article QDaily articles needing image recovery.

Reuses is_long() criteria from fetch_images.py.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from pathlib import Path
from urllib.parse import urlparse

LONG_THRESHOLD = 4000
AUTHOR_PURE_CJK_RE = re.compile(r"^[一-鿿\s·、，,；; ]+$")
REPRINT_TITLE_RE = re.compile(r"^[《【]")

def is_long(r):
    if (r.get("body_text_len") or 0) < LONG_THRESHOLD: return False
    if not AUTHOR_PURE_CJK_RE.match(r.get("author") or ""): return False
    if REPRINT_TITLE_RE.match(r.get("title") or ""): return False
    title = r.get("title") or ""
    if "大公司头条" in title or "商业剪报" in title: return False
    return True

def fn(u):
    ext = Path(urlparse(u).path).suffix.lower() or ".bin"
    return hashlib.sha1(u.encode()).hexdigest()[:16] + ext

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--missing-only", action="store_true",
                    help="Only emit articles with at least one missing image")
    args = ap.parse_args()

    rows = []
    total_long = 0
    for p in sorted(Path(args.data_dir).glob("articles_extracted_*.jsonl")):
        with p.open() as f:
            for line in f:
                if not line.strip(): continue
                r = json.loads(line)
                if not is_long(r): continue
                total_long += 1
                imgs = list(r.get("images") or [])
                bi = r.get("banner_image")
                if bi: imgs.append(bi)
                imgs = [u for u in imgs if u and u.startswith(("http://","https://"))]
                miss = [u for u in imgs if not (Path(args.assets)/str(r["id"])/fn(u)).exists()]
                if args.missing_only and not miss: continue
                rows.append({
                    "id": r["id"], "title": r["title"],
                    "missing": len(miss), "have": len(imgs)-len(miss),
                    "total": len(imgs),
                })
    rows.sort(key=lambda x: x["id"])
    for r in rows:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"# {len(rows)} long articles emitted (total long: {total_long})", file=sys.stderr)

if __name__ == "__main__":
    main()
