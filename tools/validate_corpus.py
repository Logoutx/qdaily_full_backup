"""
Layer-1 data-correctness gate for the QDaily archive.

Loads the committed extraction corpus (data/articles_extracted_*.jsonl), deduped
exactly the way render.py loads it, and asserts the invariants that the pipeline
relies on but never checked. Exits non-zero (with a precise per-rule report) if
any HARD invariant is violated — so a parsing/normalization change or a bad merge
that damages records fails CI instead of silently shipping.

Hard invariants (calibrated to pass clean on the current corpus):
  * required fields present and correctly typed; id is a positive int
  * ids unique across the deduped set
  * publish_date is YYYY-MM-DD, parses, and is <= QDAILY_FINAL_DATE
  * archive_ts is 14 digits
  * is_stub      == (body_text_len < 40 and not images)
  * date_mismatch == (publish_date != folder_date)
  * body_text_len == len(plaintext(body_html))   [extract.py formula]
        (except the documented KNOWN_BTL_EXCEPTIONS)
  * is_screenshot_only => empty body_html + screenshot_url; else title non-empty
  * no WeChat AppID (wx[a-f0-9]{16}) left in body_html
  * no Wayback wrapper (web.archive.org/web/<digits>) in banner_image / images[]
        (image fields only — a Wayback link in body prose is legitimate content)

Warn-only metrics (printed, never fatal): non-http(s) image URLs.

Usage:  python tools/validate_corpus.py
"""
from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

QDAILY_FINAL_DATE = "2019-05-27"
BASE_EXCLUDED_IDS = {64091}

# Records whose stored body_text_len differs from a fresh recompute by a few
# chars (pre-existing whitespace/parse artifacts, not corruption). Allowlisted so
# only NEW round-trip drift fails. Re-audit if this list goes stale.
KNOWN_BTL_EXCEPTIONS = {47595, 47668, 47670, 47831}

REQUIRED_FIELDS: list[tuple[str, type | tuple[type, ...]]] = [
    ("id", int), ("title", str), ("publish_date", str), ("folder_date", str),
    ("body_html", str), ("body_text_len", int), ("images", list),
    ("is_stub", bool), ("is_screenshot_only", bool),
    ("archive_ts", str), ("archive_url", str), ("original_url", str),
    ("source_path", str),
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^\d{14}$")
APPID_RE = re.compile(r"wx[a-f0-9]{16}\b")
WB_WRAP_RE = re.compile(r"web\.archive\.org/web/\d+")
MAX_EXAMPLES = 8


def load_excluded() -> set[int]:
    excl = set(BASE_EXCLUDED_IDS)
    f = Path("data/excluded_ids.txt")
    if f.exists():
        for ln in f.read_text(encoding="utf-8").split("\n"):
            ln = ln.split("#", 1)[0].strip()
            if ln.isdigit():
                excl.add(int(ln))
    return excl


def load_corpus(records_glob: str) -> tuple[list[dict], list[int], int]:
    """Dedup by id, last-wins across sorted files, minus excluded — mirrors
    render.py so validation sees exactly the rendered record set.

    Returns (deduped_records, intra_quarterly_dup_ids, n_extra_overrides):
      * dup_ids — ids appearing 2+ times among the auto-extracted *quarterly*
        files (an extraction/bucketing bug; render would silently last-wins it).
      * n_extra_overrides — ids in articles_extracted_extra.jsonl that override a
        quarterly record (a legitimate, intentional pattern; reported as info).
    Note: the '_extra' file is matched by suffix, NOT substring — every file is
    named 'articles_extracted_*', so 'extra' in name would match them all.
    """
    excl = load_excluded()
    rm: dict[int, dict] = {}
    quarterly_counts: Counter = Counter()
    extra_overrides = 0
    for path in sorted(glob.glob(records_glob)):
        is_extra = Path(path).name.endswith("_extra.jsonl")
        for ln in Path(path).read_text(encoding="utf-8").split("\n"):
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("id") in excl:
                continue
            if not is_extra:
                quarterly_counts[r["id"]] += 1
            if is_extra and r["id"] in rm:
                extra_overrides += 1
            rm[r["id"]] = r
    dup_ids = sorted(i for i, c in quarterly_counts.items() if c > 1)
    return list(rm.values()), dup_ids, extra_overrides


def plain_len(html: str) -> int:
    return len(BeautifulSoup(html or "", "lxml").get_text(strip=True))


def main() -> int:
    records_glob = "data/articles_extracted_*.jsonl"
    recs, dup_ids, n_overrides = load_corpus(records_glob)
    n = len(recs)
    if not n:
        print("FAIL: no records loaded", file=sys.stderr)
        return 1

    viol: dict[str, list[str]] = {}
    counts: Counter = Counter()

    def fail(rule: str, rid, detail: str = "") -> None:
        counts[rule] += 1
        if len(viol.setdefault(rule, [])) < MAX_EXAMPLES:
            viol[rule].append(f"id={rid} {detail}".rstrip())

    warn: dict[str, int] = Counter()
    nonhttp_schemes: Counter = Counter()
    stale_allow: list[int] = []

    # Duplicate ids within the auto-extracted quarterly files (computed pre-dedup,
    # so the last-wins merge can't mask them).
    for d in dup_ids:
        fail("duplicate_id_in_quarterly", d)

    for r in recs:
        rid = r.get("id")

        # required fields present & typed
        for field, typ in REQUIRED_FIELDS:
            if field not in r:
                fail("missing_field", rid, field)
            elif r[field] is not None and not isinstance(r[field], typ):
                # bool is a subclass of int — guard id/body_text_len explicitly
                if typ is int and isinstance(r[field], bool):
                    fail("wrong_type", rid, f"{field} is bool")
                elif not isinstance(r[field], typ):
                    fail("wrong_type", rid, f"{field}={type(r[field]).__name__}")
        if isinstance(rid, int) and rid <= 0:
            fail("nonpositive_id", rid)

        pd = r.get("publish_date") or ""
        if not DATE_RE.match(pd):
            fail("bad_publish_date", rid, repr(pd))
        elif pd > QDAILY_FINAL_DATE:
            fail("date_after_cutoff", rid, pd)

        # archive_ts is 14 digits for Wayback-sourced records; empty is allowed
        # for externally-sourced (_extra / Medium) records with no snapshot.
        ts = r.get("archive_ts") or ""
        if ts and not TS_RE.match(ts):
            fail("bad_archive_ts", rid, repr(ts))

        imgs = r.get("images") or []
        # derived-field formulas
        if bool(r.get("is_stub")) != (r.get("body_text_len", 0) < 40 and not imgs):
            fail("is_stub_formula", rid)
        if bool(r.get("date_mismatch")) != (r.get("publish_date") != r.get("folder_date")):
            fail("date_mismatch_formula", rid)

        # screenshot-only vs normal
        if r.get("is_screenshot_only"):
            if (r.get("body_html") or "").strip():
                fail("screenshot_has_body", rid)
            if not r.get("screenshot_url"):
                # A few unrecoverable stubs have neither body nor screenshot —
                # a legitimate "blank" state, tracked not failed.
                warn["screenshot_only_without_url"] += 1
        else:
            if not (r.get("title") or "").strip():
                fail("empty_title", rid)
            # body round-trip
            recomputed = plain_len(r.get("body_html"))
            if recomputed != r.get("body_text_len", 0):
                if rid not in KNOWN_BTL_EXCEPTIONS:
                    fail("body_text_len_roundtrip", rid,
                         f"stored={r.get('body_text_len')} got={recomputed}")
            elif rid in KNOWN_BTL_EXCEPTIONS:
                stale_allow.append(rid)

        # leaked secrets / wayback wrappers
        if APPID_RE.search(r.get("body_html") or ""):
            fail("wechat_appid_leak", rid)
        for u in ([r["banner_image"]] if r.get("banner_image") else []) + list(imgs):
            if WB_WRAP_RE.search(str(u)):
                fail("wayback_in_image_url", rid, str(u)[:80])
                break

        # warn-only: non-http image urls
        rec_has_nonhttp = False
        for u in ([r["banner_image"]] if r.get("banner_image") else []) + list(imgs):
            u = str(u)
            if not u.startswith(("http://", "https://")):
                rec_has_nonhttp = True
                if u.startswith("//"):
                    nonhttp_schemes["//"] += 1
                elif u.startswith("file:"):
                    nonhttp_schemes["file:"] += 1
                elif u.startswith("/"):
                    nonhttp_schemes["root-relative"] += 1
                else:
                    nonhttp_schemes["other"] += 1
        if rec_has_nonhttp:
            warn["records_with_nonhttp_image"] += 1

    # ---- report ----
    hard_total = sum(counts.values())
    print(f"validate_corpus: {n:,} deduped records checked")
    print(f"  intra-quarterly duplicate ids: {len(dup_ids)}; "
          f"_extra overrides: {n_overrides}")
    if warn:
        print("  warn-only metrics:")
        print(f"    records with non-http image URL: "
              f"{warn.get('records_with_nonhttp_image', 0):,}  "
              f"(urls by kind: {dict(nonhttp_schemes)})")
        if warn.get("screenshot_only_without_url"):
            print(f"    screenshot-only stubs without a screenshot_url: "
                  f"{warn['screenshot_only_without_url']:,}")
    if stale_allow:
        print(f"  NOTE: {len(stale_allow)} allowlisted body_text_len id(s) now "
              f"match exactly — prune KNOWN_BTL_EXCEPTIONS: {sorted(stale_allow)}")

    if not hard_total:
        print("PASS: all hard invariants hold.")
        return 0

    print(f"\nFAIL: {hard_total} hard-invariant violation(s) across "
          f"{len(counts)} rule(s):", file=sys.stderr)
    for rule in sorted(counts, key=lambda k: -counts[k]):
        print(f"\n  [{rule}] x{counts[rule]}", file=sys.stderr)
        for ex in viol[rule]:
            print(f"      {ex}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
