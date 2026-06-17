"""
Fetch current hotspots from several free public feeds and write a normalized
snapshot for the daily "not-so-random" digest pipeline.

Multi-source by design: each source is tried independently and failures are
tolerated (feeds come and go / rate-limit), so the run uses whatever responds.
No API key required.

Sources (v1) — both Chinese and global, since QDaily covered global tech /
business / culture and many archive pieces echo worldwide trends:
  * 60s aggregator      — weibo / zhihu / douyin / toutiao hot lists (CN)
                          (open-source github.com/vikiboss/60s, on Cloudflare, no key)
  * Google Trends RSS   — multiple geos (US/GB/HK/TW/JP…), free, no key
  * Hacker News         — top stories via the official Firebase API (global tech,
                          free, no key) — strong match for QDaily's tech coverage

Output: data/hotspots/<date>.json = [{source, rank, title, hot, url}]

Usage:
  python tools/fetch_hotspots.py                 # all sources, today (UTC)
  python tools/fetch_hotspots.py --geo US,CN     # Trends geos
  python tools/fetch_hotspots.py --date 2026-06-17
  python tools/fetch_hotspots.py --print         # also echo to stdout
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) qdaily-hotspots/1.0"
OUTDIR = Path("data/hotspots")


def _client() -> httpx.Client:
    return httpx.Client(timeout=15, headers={"User-Agent": UA},
                        follow_redirects=True)


def google_trends(client: httpx.Client, geo: str) -> list[dict]:
    """Google Trends daily/trending RSS. Free, no key."""
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    r = client.get(url)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for i, item in enumerate(root.iterfind(".//item"), 1):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = (item.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip()
        link = (item.findtext("link") or "").strip()
        out.append({"source": f"google-trends-{geo}", "rank": i,
                    "title": title, "hot": traffic, "url": link})
    return out


def s60s(client: httpx.Client, kind: str) -> list[dict]:
    """60s open hot-list aggregator. kind in {weibo, zhihu, douyin, toutiao}."""
    r = client.get(f"https://60s.viki.moe/v2/{kind}")
    r.raise_for_status()
    items = r.json().get("data") or []
    out = []
    for i, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        if not title:
            continue
        hot = it.get("hot_value") or it.get("hot_value_desc") or ""
        out.append({"source": f"60s-{kind}", "rank": i, "title": title,
                    "hot": str(hot), "url": it.get("link") or ""})
    return out


def hacker_news(client: httpx.Client, n: int = 30) -> list[dict]:
    """Hacker News top stories via the official Firebase API (global tech)."""
    ids = client.get("https://hacker-news.firebaseio.com/v0/topstories.json").json()[:n]
    out = []
    for i, sid in enumerate(ids, 1):
        it = client.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json").json() or {}
        title = (it.get("title") or "").strip()
        if not title:
            continue
        out.append({"source": "hacker-news", "rank": i, "title": title,
                    "hot": str(it.get("score") or ""),
                    "url": it.get("url") or f"https://news.ycombinator.com/item?id={sid}"})
    return out


SOURCES = {
    "60s-weibo": lambda c: s60s(c, "weibo"),
    "60s-zhihu": lambda c: s60s(c, "zhihu"),
    "60s-douyin": lambda c: s60s(c, "douyin"),
    "60s-toutiao": lambda c: s60s(c, "toutiao"),
    "hacker-news": lambda c: hacker_news(c, 30),
}


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t.lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geo", default="US,GB,HK,TW,JP",
                    help="comma list of Google Trends geos (global breadth)")
    ap.add_argument("--date", default="", help="YYYY-MM-DD (default: pass via cron; "
                    "falls back to file mtime ordering). Used only for the filename.")
    ap.add_argument("--print", action="store_true", dest="echo")
    args = ap.parse_args()

    if not args.date:
        print("note: no --date given; writing to data/hotspots/latest.json",
              file=sys.stderr)
    fname = f"{args.date}.json" if args.date else "latest.json"

    all_items: list[dict] = []
    report: list[str] = []
    with _client() as c:
        for geo in [g.strip() for g in args.geo.split(",") if g.strip()]:
            try:
                items = google_trends(c, geo)
                all_items += items
                report.append(f"  google-trends-{geo}: {len(items)}")
            except Exception as e:
                report.append(f"  google-trends-{geo}: FAILED ({type(e).__name__})")
        for name, fn in SOURCES.items():
            try:
                items = fn(c)
                all_items += items
                report.append(f"  {name}: {len(items)}")
            except Exception as e:
                report.append(f"  {name}: FAILED ({type(e).__name__})")

    # dedupe by normalized title, keep best (lowest) rank / first source seen
    seen: dict[str, dict] = {}
    for it in all_items:
        k = _norm(it["title"])
        if k and (k not in seen or it["rank"] < seen[k]["rank"]):
            seen[k] = it
    deduped = sorted(seen.values(), key=lambda x: (x["source"], x["rank"]))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTDIR / fname
    out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"sources:")
    print("\n".join(report))
    print(f"wrote {len(deduped)} unique hotspots -> {out_path}")
    if args.echo:
        for it in deduped[:25]:
            print(f"  [{it['source']} #{it['rank']}] {it['title']}  ({it['hot']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
