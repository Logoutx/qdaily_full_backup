"""
Build the work-list for LLM alt-text generation.

For every article, collect the images that ACTUALLY exist on disk under
assets/<id>/ (the live/CDN set) — the banner/lead image plus in-article body
images — and emit one JSONL record per UNIQUE image URL with the context an
LLM needs to caption it well (the article title + a short text excerpt).

"Skip broken photo links" is enforced here: an image only enters the work-list
if its asset file is present on disk. Animations (GIF/MP4) are skipped — they
can't be meaningfully captioned as a still and Read can't render the mp4.

Output: data/alt_worklist.jsonl, records:
  {"url", "asset", "id", "title", "ctx", "kind"}   kind in {banner, body}

Already-captioned URLs (present in data/image_alts.json) are excluded so the
list only contains outstanding work — re-run any time to see what's left.

Usage: python tools/build_alt_worklist.py
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "tools")
from fetch_images import asset_path  # noqa: E402

RASTER = (".jpg", ".jpeg", ".png", ".webp")  # captionable stills
SKIP = (".gif", ".mp4")                        # animations -> skip


def _suffix_kind(p: Path) -> str:
    s = p.suffix.lower()
    # qdaily filenames look like ...jpg-w600 -> suffix is ".jpg-w600"
    base = s.split("-", 1)[0]
    if base in RASTER:
        return "raster"
    if base in SKIP:
        return "skip"
    return "other"


_TAG = re.compile(r"<[^>]+>")


def _excerpt(body_html: str, n: int = 110) -> str:
    txt = _TAG.sub(" ", body_html or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:n]


def main() -> int:
    assets = Path("assets")
    done: set[str] = set()
    alts_path = Path("data/image_alts.json")
    if alts_path.exists():
        for k, v in json.loads(alts_path.read_text(encoding="utf-8")).items():
            alt = v.get("alt") if isinstance(v, dict) else v
            if alt:
                done.add(k)

    seen: set[str] = set()
    out = []
    n_banner = n_body = n_skip_anim = n_broken = 0
    for f in sorted(glob.glob("data/articles_extracted_*.jsonl")):
        for line in Path(f).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("is_screenshot_only"):
                continue
            aid = r["id"]
            title = r.get("title") or ""
            ctx = _excerpt(r.get("body_html") or "")
            items = []
            if r.get("banner_image"):
                items.append((r["banner_image"], "banner"))
            for u in (r.get("images") or []):
                items.append((u, "body"))
            for url_, kind in items:
                if url_ in seen or url_ in done:
                    continue
                try:
                    p = asset_path(assets, aid, url_)
                except ValueError:
                    n_broken += 1
                    continue
                if not p or not p.exists():
                    n_broken += 1
                    continue
                k = _suffix_kind(p)
                if k == "skip":
                    n_skip_anim += 1
                    continue
                if k != "raster":
                    continue
                seen.add(url_)
                out.append({
                    "url": url_,
                    "asset": str(p),
                    "id": aid,
                    "title": title,
                    "ctx": ctx,
                    "kind": kind,
                })
                if kind == "banner":
                    n_banner += 1
                else:
                    n_body += 1

    dest = Path("data/alt_worklist.jsonl")
    with dest.open("w", encoding="utf-8") as fh:
        for rec in out:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"worklist: {len(out):,} unique images "
          f"(banner={n_banner:,} body={n_body:,}); "
          f"already-done={len(done):,} skipped-animation={n_skip_anim:,} "
          f"not-on-disk(broken)={n_broken:,}")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
