"""
Backstage quality filter for "Today's Pick": tag archive articles that should
NOT be surfaced as a daily pick, by a few low-value patterns. Writes
data/pick_exclusions.json {excluded: {id: [tags]}, counts, filters}; the picker
(tools/build_daily_picks.py) drops these ids from every bucket.

Filters (per the editorial brief):
  broken-gallery-thin : 10+ images, mostly broken, < 3000 chars  (a thin gallery
                        whose pictures don't even load).
  broken-no-text      : mostly-broken images and < 500 chars     (barely anything
                        left once the broken pictures are gone).
  apple-announcement  : short (< 2500 chars) Apple product launch / preview blurb.
  light-financial     : short (< 3000 chars) company financial-report write-up.

"Broken" mirrors render.py: an image counts as broken when it is NOT mirrored on
disk AND has no Wayback snapshot (in data/images.jsonl as no-snapshot-prefix) or
is a non-http URL — i.e. it renders as the placeholder.

Usage:  python tools/tag_pick_exclusions.py [--print]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

APPLE = re.compile(r"苹果|iphone|ipad|macbook|\bmac\b|apple watch|airpods|\bios\b|apple tv|imac|\bapple\b", re.I)
ANNOUNCE = re.compile(r"发布|推出|上市|开卖|开售|售价|预览|亮相|新款|新品|发布会|新机|新一代|更新|升级|搭载")
FIN = re.compile(r"财报|营收|净利润|净利|利润|财年|业绩|盈利|亏损|季度业绩|营业额|同比|环比|每股|Q[1-4]\b")


def load_dead() -> set[str]:
    dead: set[str] = set()
    p = Path("data/images.jsonl")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                o = json.loads(line)
                if o.get("status") == "no-snapshot-prefix" and o.get("url"):
                    dead.add(o["url"])
    return dead


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", dest="echo")
    args = ap.parse_args()

    sys.path.insert(0, "tools")
    from validate_corpus import load_corpus
    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    dead = load_dead()

    def broken_count(r: dict) -> tuple[int, int]:
        imgs = [u for u in (r.get("images") or []) if u]
        if r.get("banner_image"):
            imgs = [r["banner_image"]] + imgs
        imgs = list(dict.fromkeys(imgs))
        broken = 0
        for u in imgs:
            try:
                ext = Path(urlparse(u).path).suffix.lower() or ".bin"
            except ValueError:
                ext = ".bin"
            digest = hashlib.sha1(u.encode("utf-8")).hexdigest()[:16]
            on_disk = (Path("assets") / str(r["id"]) / f"{digest}{ext}").exists()
            if not on_disk and (u in dead or not u.startswith(("http://", "https://"))):
                broken += 1
        return len(imgs), broken

    excluded: dict[str, list[str]] = {}
    counts = {"broken-gallery-thin": 0, "broken-no-text": 0,
              "apple-announcement": 0, "light-financial": 0}
    for r in recs:
        title = r.get("title") or ""
        length = r.get("body_text_len") or 0
        n_img, n_broken = broken_count(r)
        frac = (n_broken / n_img) if n_img else 0.0
        tags = []
        if n_img >= 10 and frac > 0.5 and length < 3000:
            tags.append("broken-gallery-thin")
        if n_img >= 1 and frac > 0.5 and length < 500:
            tags.append("broken-no-text")
        if length < 2500 and APPLE.search(title) and ANNOUNCE.search(title) and not FIN.search(title):
            tags.append("apple-announcement")
        if length < 3000 and FIN.search(title):
            tags.append("light-financial")
        if tags:
            excluded[str(r["id"])] = tags
            for t in tags:
                counts[t] += 1

    out = Path("data/pick_exclusions.json")
    out.write_text(json.dumps({
        "filters": {
            "broken-gallery-thin": "10+ images, >50% broken, <3000 chars",
            "broken-no-text": ">50% broken images, <500 chars",
            "apple-announcement": "<2500 chars Apple product launch/preview",
            "light-financial": "<3000 chars company financial report",
        },
        "counts": counts,
        "n_excluded": len(excluded),
        "excluded": excluded,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tagged {len(excluded):,} / {len(recs):,} articles excluded from picks")
    for k, v in counts.items():
        print(f"  {k:22} {v:,}")
    print(f"wrote {out}")
    if args.echo:
        for sid, tags in list(excluded.items())[:15]:
            print(f"  {sid}: {tags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
