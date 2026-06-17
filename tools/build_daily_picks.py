"""
Assemble the daily "Today's Pick" CANDIDATE bundle (deterministic / model-free).

Three buckets feed the final 12-pick page (4 cn-trend + 4 global-trend + 4 longform):
  * cn-trend     — lexical (char-bigram) matches of Chinese-language hot-search
                   trends against the archive. Reliable: same language.
  * global-trend — raw global trends (Hacker News / Google US-GB-JP). Lexical
                   can't cross scripts, so these are left for the model curator
                   to bridge conceptually (see RANDOM_PAGE_PLAN.md).
  * longform     — a random, date-seeded sample of timeless 长文章 (serendipity).

Output: data/daily_candidates/<date>.json — consumed by the curator (a Claude
session) which applies disambiguation + the "don't force it" skip gate + the
spill rule and writes the final data/daily_picks.json.

Usage:
  python tools/build_daily_picks.py --date 2026-06-17
  python tools/build_daily_picks.py --date 2026-06-17 --print
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_corpus import load_corpus
from match_archive import bigrams, build_excerpt_cache

SITE = "https://www.qdaily.org"
CN_TREND_POOL = 16        # cn-trend candidate articles to surface for the curator
LONGFORM_POOL = 24        # random longform pool size
PER_TREND = 3             # lexical candidates kept per cn trend
MIN_SCORE = 3.0           # lexical floor (below this = coincidental noise)

# cn-language trend sources (lexical-matchable): the CN platforms + the
# Chinese-language Google Trends geos. Everything else is "global".
_AUTHOR_PURE_CJK = re.compile(r"^[一-鿿\s·、，,；; ]+$")


def is_cn_source(src: str) -> bool:
    return src.startswith("60s-") or src in ("google-trends-HK", "google-trends-TW")


def is_longform(r: dict) -> bool:
    # Mirrors render.py's 长文章 rule (LONG_THRESHOLD=4000, pure-CJK byline,
    # not a 《…》/【…】 reprint).
    if (r.get("body_text_len") or 0) < 4000:
        return False
    if not _AUTHOR_PURE_CJK.match(r.get("author") or ""):
        return False
    if (r.get("title") or "").startswith(("《", "【")):
        return False
    return True


def card(r: dict, **extra) -> dict:
    return {"id": r["id"], "title": r["title"], "publish_date": r["publish_date"],
            "category": r.get("category", ""), "url": f"{SITE}/articles/{r['id']}/", **extra}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--print", action="store_true", dest="echo")
    args = ap.parse_args()

    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    recs = [r for r in recs
            if not r.get("is_stub") and not r.get("is_screenshot_only")
            and r.get("publish_date") and (r.get("title") or "").strip()]
    excerpts = build_excerpt_cache(recs)

    # Inverted char-bigram index (title×3 + category×2 + excerpt×1) — same shape
    # as tools/match_archive.py.
    index: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for idx, r in enumerate(recs):
        weighted: dict[str, int] = {}
        for bg in bigrams(r.get("title", "")):
            weighted[bg] = weighted.get(bg, 0) + 3
        for bg in bigrams(r.get("category", "")):
            weighted[bg] = weighted.get(bg, 0) + 2
        for bg in bigrams(excerpts.get(r["id"], "")):
            weighted[bg] = weighted.get(bg, 0) + 1
        for bg, w in weighted.items():
            index[bg].append((idx, w))

    def lexical(query: str) -> list[tuple[float, int]]:
        qbg = set(bigrams(query))
        if len(qbg) < 2:
            return []
        scores: dict[int, float] = defaultdict(float)
        for bg in qbg:
            for idx, w in index.get(bg, ()):
                scores[idx] += w
        ranked = []
        for idx, raw in scores.items():
            norm = raw / (len(qbg) ** 0.5)
            boost = 1.0 + min(recs[idx].get("body_text_len", 0), 4000) / 8000.0
            ranked.append((round(norm * boost, 2), idx))
        ranked.sort(reverse=True)
        return ranked

    hotspots = json.loads(Path(f"data/hotspots/{args.date}.json").read_text(encoding="utf-8"))

    # --- cn-trend candidates (lexical) ---
    cn_candidates = []
    cn_seen: set[int] = set()
    for hs in hotspots:
        if not is_cn_source(hs["source"]):
            continue
        recs_out = []
        for score, idx in lexical(hs["title"]):
            if score < MIN_SCORE:
                break
            recs_out.append(card(recs[idx], score=score))
            if len(recs_out) >= PER_TREND:
                break
        if recs_out:
            cn_candidates.append({"trend": hs["title"], "source": hs["source"], "recs": recs_out})
            cn_seen.update(c["id"] for c in recs_out)
    # Trim to the strongest distinct trends (cap the pool the curator sees).
    cn_candidates.sort(key=lambda t: t["recs"][0]["score"], reverse=True)
    cn_candidates = cn_candidates[:CN_TREND_POOL]

    # --- global trends (raw; curator bridges conceptually) ---
    global_trends = [{"trend": hs["title"], "source": hs["source"], "hot": hs.get("hot", "")}
                     for hs in hotspots if not is_cn_source(hs["source"])]

    # --- longform serendipity pool (date-seeded random, excludes cn-trend picks) ---
    longs = [r for r in recs if is_longform(r) and r["id"] not in cn_seen]
    rng = random.Random(int(args.date.replace("-", "")))
    rng.shuffle(longs)
    longform_pool = [card(r) for r in longs[:LONGFORM_POOL]]

    bundle = {
        "date": args.date,
        "buckets": {"cn_trend": 4, "global_trend": 4, "longform": 4},
        "cn_candidates": cn_candidates,
        "global_trends": global_trends,
        "longform_pool": longform_pool,
    }
    out = Path(f"data/daily_candidates/{args.date}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"cn-trend candidate trends: {len(cn_candidates)} | "
          f"global trends: {len(global_trends)} | longform pool: {len(longform_pool)}")
    print(f"wrote {out}")
    if args.echo:
        print("\n-- top cn-trend candidates --")
        for t in cn_candidates[:6]:
            top = t["recs"][0]
            print(f"  「{t['trend'][:24]}」 → ({top['publish_date']}) {top['title'][:36]}  s={top['score']}")
        print("\n-- longform serendipity sample --")
        for c in longform_pool[:6]:
            print(f"  ({c['publish_date']}) {c['title'][:42]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
