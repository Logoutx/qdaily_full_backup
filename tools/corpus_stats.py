"""
Layer-2 aggregate-drift tripwire for the QDaily corpus.

Computes corpus-wide statistics over the same deduped record set render.py uses,
and either writes them as a baseline (--update) or checks the current corpus
against the committed baseline within tolerances (--check, the default).

Mass damage to thousands of records — the failure mode that invariant checks on
individual records can miss when each damaged record is still "well-formed" —
moves an aggregate. So a change that, say, drops every banner or empties bodies
in 2017 trips this even if validate_corpus still passes. Baseline updates are
deliberate, reviewed commits (data/corpus_stats.baseline.json).

Usage:
    python tools/corpus_stats.py --update     # write the baseline
    python tools/corpus_stats.py --check       # compare to baseline (CI default)
    python tools/corpus_stats.py               # same as --check
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_corpus import load_corpus  # noqa: E402
from render import detect_foreign_source  # noqa: E402

BASELINE = Path("data/corpus_stats.baseline.json")


def body_bucket(nchar: int) -> str:
    for hi, label in [(0, "0"), (200, "1-200"), (500, "201-500"),
                      (1000, "501-1000"), (2000, "1001-2000"), (5000, "2001-5000")]:
        if nchar <= hi:
            return label
    return "5000+"


def img_bucket(k: int) -> str:
    if k <= 2:
        return str(k)
    if k <= 5:
        return "3-5"
    if k <= 10:
        return "6-10"
    return "11+"


def compute() -> dict:
    recs, dup_ids, n_overrides = load_corpus("data/articles_extracted_*.jsonl")
    per_year: Counter = Counter()
    per_quarter: Counter = Counter()
    body_hist: Counter = Counter()
    img_hist: Counter = Counter()
    flags = Counter()
    authors: set[str] = set()
    total_image_refs = 0

    for r in recs:
        pd = r.get("publish_date") or ""
        yr = pd[:4]
        per_year[yr] += 1
        if len(pd) >= 7 and pd[5:7].isdigit():
            q = (int(pd[5:7]) - 1) // 3 + 1
            per_quarter[f"{yr}_Q{q}"] += 1
        body_hist[body_bucket(r.get("body_text_len", 0))] += 1
        imgs = r.get("images") or []
        total_image_refs += len(imgs)
        img_hist[img_bucket(len(imgs))] += 1

        if r.get("is_stub"):
            flags["is_stub"] += 1
        if r.get("is_screenshot_only"):
            flags["is_screenshot_only"] += 1
        if (r.get("author") or "").strip():
            flags["with_author"] += 1
            authors.add(r["author"].strip())
        if (r.get("category") or "").strip():
            flags["with_category"] += 1
        if r.get("banner_image"):
            flags["with_banner"] += 1
        if r.get("like_count") is not None:
            flags["with_like_count"] += 1
        if r.get("date_mismatch"):
            flags["date_mismatch"] += 1
        nyt, med = detect_foreign_source(r.get("body_html") or "", r.get("author") or "")
        if nyt:
            flags["foreign_nyt"] += 1
        if med:
            flags["foreign_medium"] += 1
        for u in ([r["banner_image"]] if r.get("banner_image") else []) + list(imgs):
            if not str(u).startswith(("http://", "https://")):
                flags["records_or_urls_nonhttp_img"] += 1
                break

    return {
        "total_records": len(recs),
        "extra_overrides": n_overrides,
        "distinct_authors": len(authors),
        "total_image_refs": total_image_refs,
        "flags": dict(flags),
        "per_year": dict(per_year),
        "per_quarter": dict(per_quarter),
        "body_len_hist": dict(body_hist),
        "images_per_article_hist": dict(img_hist),
    }


def _flatten(d: dict, prefix: str = "") -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def tol(base: int) -> float:
    """Allowed absolute deviation: 0.5% of the baseline, or 20, whichever larger."""
    return max(20.0, abs(base) * 0.005)


def check(cur: dict) -> int:
    if not BASELINE.exists():
        print(f"FAIL: no baseline at {BASELINE}. Run --update first.", file=sys.stderr)
        return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    fb, fc = _flatten(base), _flatten(cur)
    drift = []
    for key in sorted(set(fb) | set(fc)):
        b = fb.get(key, 0)
        c = fc.get(key, 0)
        if abs(c - b) > tol(b):
            drift.append((key, b, c))

    print(f"corpus_stats --check: total_records {fc.get('total_records')} "
          f"(baseline {fb.get('total_records')})")
    if not drift:
        print("PASS: all metrics within tolerance.")
        return 0
    print(f"\nFAIL: {len(drift)} metric(s) outside tolerance "
          f"(±max(20, 0.5%)):", file=sys.stderr)
    print(f"  {'metric':<40} {'baseline':>10} {'current':>10} {'delta':>10}",
          file=sys.stderr)
    for key, b, c in drift:
        print(f"  {key:<40} {b:>10} {c:>10} {c - b:>+10}", file=sys.stderr)
    print("\nIf this change is intended, re-baseline with: "
          "python tools/corpus_stats.py --update", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true",
                    help="write data/corpus_stats.baseline.json from the current corpus")
    ap.add_argument("--check", action="store_true",
                    help="compare current corpus to the baseline (default)")
    args = ap.parse_args()

    cur = compute()
    if args.update:
        BASELINE.write_text(json.dumps(cur, ensure_ascii=False, indent=2,
                                       sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE}: total_records={cur['total_records']}, "
              f"{len(_flatten(cur))} metrics")
        return 0
    return check(cur)


if __name__ == "__main__":
    raise SystemExit(main())
