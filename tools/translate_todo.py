"""
Build the QDaily zh->en translation queue and materialize per-article payloads.

Priority order (the user's brief):
  1. 2017清退   — the 2017 Beijing "低端人口" eviction coverage, content-matched
                  across the whole archive (date-windowed for precision).
  2. 年度观察   — ALL year-end series (Top 15 年度报道, 年度盘点, 趋势洞察,
                  年度公司/票房/营销/软件/话题, 年度图书推荐, 商业大新闻, ...).
  3. 好奇心商业史
  4. 100 个有想法的人
  5. the rest of 长文章 (>=4000 chars, pure-CJK byline, not a 《》/【】 reprint),
     newest-first.
Excluded everywhere: 大公司头条 and 商业剪报 (the daily news roundups).

Resumable: "done" = data/translations/en/<id>.json exists. Deferred ids
(data/translations/defer.json) are skipped. The queue is written to
data/translations/queue.json for the backstage.

Usage:
  python tools/translate_todo.py                      # summary + per-bucket counts
  python tools/translate_todo.py --limit 20 --emit    # materialize next 20, print ids JSON
  python tools/translate_todo.py --ids 48703 11879 --emit
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_corpus import load_corpus
from html_to_md import html_to_md, count_images
import render as R  # authoritative series membership (_series_match / SERIES_NAMES)

TROOT = Path("data/translations")
IN = TROOT / "in"
EN = TROOT / "en"

_PURE_CJK = re.compile(r"^[一-鿿\s·、，,；; ]+$")
EXCLUDE = re.compile(r"大公司头条|商业剪报")

# Priority buckets map our short labels to render.py's canonical series names.
# 2017清退 and 年度观察 are the real /series/ pages the user pointed to, so we
# reuse the site's exact membership rather than re-deriving it heuristically.
PRIORITY_SERIES = [
    ("2017清退", "2017 清退"),
    ("年度观察", "年度观察"),
    ("好奇心商业史", "好奇心商业史"),
    ("100个有想法的人", "100 个有想法的人"),
]
PRIORITY = [label for label, _ in PRIORITY_SERIES] + ["longform"]


def set_is_long(r: dict) -> None:
    """Replicate render.py's is_long rule exactly (年度观察 membership needs it)."""
    if (r.get("body_text_len") or 0) < 4000:
        r["is_long"] = False
    elif not _PURE_CJK.match(r.get("author") or ""):
        r["is_long"] = False
    elif (r.get("title") or "").startswith(("《", "【")):
        r["is_long"] = False
    elif EXCLUDE.search(r.get("title") or ""):  # 大公司头条 daily roundups
        r["is_long"] = False
    else:
        r["is_long"] = True


def usable(r: dict) -> bool:
    return (not r.get("is_stub") and not r.get("is_screenshot_only")
            and bool((r.get("title") or "").strip())
            and not EXCLUDE.search(r.get("title") or ""))


def bucket_of(r: dict) -> str | None:
    """First matching priority series (authoritative), else 'longform' / None."""
    for label, sname in PRIORITY_SERIES:
        if R._series_match(sname, r):
            return label
    if r.get("is_long"):
        return "longform"
    return None


def build_queue(recs: list[dict]) -> list[tuple[int, str]]:
    """Return [(id, bucket)] in priority order; within bucket, newest-first."""
    buckets: dict[str, list[dict]] = {b: [] for b in PRIORITY}
    seen: set[int] = set()
    for r in recs:
        if not usable(r):
            continue
        set_is_long(r)
        b = bucket_of(r)
        if b is None or r["id"] in seen:
            continue
        buckets[b].append(r)
        seen.add(r["id"])
    queue: list[tuple[int, str]] = []
    for b in PRIORITY:
        rs = sorted(buckets[b], key=lambda r: (r.get("publish_date") or ""), reverse=True)
        queue += [(r["id"], b) for r in rs]
    return queue


def first_para(md: str, limit: int = 120) -> str:
    for block in md.split("\n\n"):
        block = block.strip()
        if block and not block.startswith(("!", "#", ">", "-")):
            return block[:limit]
    return ""


def materialize(r: dict, bucket: str) -> int:
    md = html_to_md(r.get("body_html") or "")
    payload = {
        "id": r["id"],
        "title": r.get("title") or "",
        "excerpt": first_para(md),
        "category": r.get("category") or "",
        "type": bucket,
        "body": md,
        "n_images": count_images(md),
        "publish_date": r.get("publish_date") or "",
    }
    IN.mkdir(parents=True, exist_ok=True)
    (IN / f"{r['id']}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload["n_images"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="emit at most N ids (0 = all)")
    ap.add_argument("--ids", nargs="*", type=int, help="specific ids instead of the queue")
    ap.add_argument("--emit", action="store_true", help="materialize in/<id>.json + print ids JSON")
    args = ap.parse_args()

    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    byid = {r["id"]: r for r in recs}

    done = {int(p.stem) for p in EN.glob("*.json")} if EN.exists() else set()
    defer = set()
    dp = TROOT / "defer.json"
    if dp.exists():
        defer = {int(x) for x in json.loads(dp.read_text() or "[]")}

    full = build_queue(recs)
    TROOT.mkdir(parents=True, exist_ok=True)
    (TROOT / "queue.json").write_text(
        json.dumps([{"id": i, "bucket": b} for i, b in full], ensure_ascii=False),
        encoding="utf-8")

    # remaining = not yet translated, not deferred
    remaining = [(i, b) for i, b in full if i not in done and i not in defer]

    if args.ids:
        chosen = [(i, dict(full).get(i, "manual")) for i in args.ids if i in byid]
    else:
        chosen = remaining[: args.limit] if args.limit else remaining

    if args.emit:
        ids = []
        for i, b in chosen:
            n = materialize(byid[i], b)
            ids.append(str(i))
        print(json.dumps(ids, ensure_ascii=False))
        return 0

    # summary
    from collections import Counter
    tot = Counter(b for _, b in full)
    rem = Counter(b for _, b in remaining)
    print(f"queue: {len(full)} in scope | done: {len(done)} | deferred: {len(defer)} | remaining: {len(remaining)}\n")
    print(f"{'bucket':16} {'total':>7} {'remaining':>10}")
    for b in PRIORITY:
        print(f"{b:16} {tot.get(b,0):>7} {rem.get(b,0):>10}")
    print("\nnext up:")
    for i, b in remaining[:12]:
        r = byid[i]
        print(f"  {i}  [{b}]  ({r.get('publish_date')})  {(r.get('title') or '')[:42]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
