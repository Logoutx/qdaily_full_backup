"""
Match today's hotspots to timeless QDaily archive pieces (model-free candidate
generation for the "not-so-random" daily digest).

Approach (v1, lexical): character-bigram overlap — no Chinese segmenter needed.
Each article is indexed on title (weighted) + category + a short body excerpt;
each hotspot's title is turned into bigrams and scored against the inverted
index. Longer pieces get a mild boost (proxy for "timeless / substantial").
Stubs, screenshot-only, and undated records are excluded.

This only proposes CANDIDATES. The daily curator (a single Claude session) later
picks ~20 and writes the editorial framing — that is the one step that needs
model judgment; this step is plain Python.

Body excerpts are stripped from body_html once and cached in
data/.search_excerpts.jsonl (gitignored) so re-runs are fast.

Input:  data/hotspots/<date>.json
Output: data/match_candidates/<date>.json
        = [{hotspot:{...}, candidates:[{id,title,publish_date,url,score,snippet}]}]

Usage:
  python tools/match_archive.py --date 2026-06-17
  python tools/match_archive.py --date 2026-06-17 --per-hotspot 8 --print
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CACHE = Path("data/.search_excerpts.jsonl")
EXCERPT_CHARS = 300
SITE = "https://www.qdaily.org"

_CLEAN = re.compile(r"[^0-9a-z一-鿿]+")


def bigrams(text: str) -> list[str]:
    """Char bigrams over each cleaned token; keep len>=3 alphanumeric tokens whole."""
    out: list[str] = []
    for tok in _CLEAN.sub(" ", (text or "").lower()).split():
        if tok.isascii():
            if len(tok) >= 3:
                out.append(tok)            # whole English/number word
            elif len(tok) == 2:
                out.append(tok)
            continue
        if len(tok) == 1:
            out.append(tok)
        for i in range(len(tok) - 1):
            out.append(tok[i:i + 2])
    return out


def build_excerpt_cache(recs: list[dict]) -> dict[int, str]:
    """Strip body_html → plain-text excerpt, cached by id (rebuild if count changed)."""
    cache: dict[int, str] = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate a truncated final line from a killed run
                cache[o["id"]] = o["x"]
    if len(cache) >= len(recs):
        return cache
    from bs4 import BeautifulSoup
    print(f"building excerpt cache ({len(recs) - len(cache)} new)...", file=sys.stderr)
    with CACHE.open("a", encoding="utf-8") as f:
        for r in recs:
            if r["id"] in cache:
                continue
            txt = BeautifulSoup(r.get("body_html") or "", "lxml").get_text(" ", strip=True)
            ex = txt[:EXCERPT_CHARS]
            cache[r["id"]] = ex
            f.write(json.dumps({"id": r["id"], "x": ex}, ensure_ascii=False) + "\n")
    return cache


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--per-hotspot", type=int, default=8)
    ap.add_argument("--min-score", type=float, default=2.5)
    ap.add_argument("--print", action="store_true", dest="echo")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_corpus import load_corpus
    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    recs = [r for r in recs
            if not r.get("is_stub") and not r.get("is_screenshot_only")
            and r.get("publish_date") and (r.get("title") or "").strip()]
    excerpts = build_excerpt_cache(recs)

    # inverted index: bigram -> list of (doc_idx, weight)
    index: dict[str, list[tuple[int, int]]] = defaultdict(list)
    docs = []
    for idx, r in enumerate(recs):
        docs.append(r)
        weighted = {}
        for bg in bigrams(r.get("title", "")):
            weighted[bg] = weighted.get(bg, 0) + 3
        for bg in bigrams(r.get("category", "")):
            weighted[bg] = weighted.get(bg, 0) + 2
        for bg in bigrams(excerpts.get(r["id"], "")):
            weighted[bg] = weighted.get(bg, 0) + 1
        for bg, w in weighted.items():
            index[bg].append((idx, w))
    print(f"indexed {len(docs):,} articles, {len(index):,} bigrams", file=sys.stderr)

    hotspots = json.loads(Path(f"data/hotspots/{args.date}.json").read_text(encoding="utf-8"))

    results = []
    for hs in hotspots:
        qbg = set(bigrams(hs["title"]))
        if len(qbg) < 2:
            continue
        scores: dict[int, float] = defaultdict(float)
        for bg in qbg:
            for idx, w in index.get(bg, ()):  # postings
                scores[idx] += w
        if not scores:
            continue
        ranked = []
        for idx, raw in scores.items():
            r = docs[idx]
            # normalize by query size; mild boost for substantial pieces
            norm = raw / (len(qbg) ** 0.5)
            length_boost = 1.0 + min(r.get("body_text_len", 0), 4000) / 8000.0
            ranked.append((norm * length_boost, idx))
        ranked.sort(reverse=True)
        cands = []
        for score, idx in ranked[: args.per_hotspot]:
            if score < args.min_score:
                break
            r = docs[idx]
            cands.append({
                "id": r["id"], "title": r["title"],
                "publish_date": r["publish_date"],
                "url": f"{SITE}/articles/{r['id']}/",
                "score": round(score, 2),
                "snippet": excerpts.get(r["id"], "")[:140],
            })
        if cands:
            results.append({"hotspot": hs, "candidates": cands})

    out = Path(f"data/match_candidates/{args.date}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"matched {len(results)}/{len(hotspots)} hotspots -> {out}")
    if args.echo:
        for r in results[:12]:
            print(f"\n● {r['hotspot']['title']}  [{r['hotspot']['source']}]")
            for c in r["candidates"][:3]:
                print(f"    {c['score']:>5}  ({c['publish_date']}) {c['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
