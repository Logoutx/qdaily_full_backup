"""
Deterministic QA gate for QDaily translations (no tokens). Flags:
  - dropped/added images     (en image count != source image count)
  - residual Chinese         (CJK chars beyond a tiny tolerance for 《》 titles)
  - truncation/summarization (en body much shorter than expected vs source)
  - forbidden renderings     (抖音->TikTok, 微信->WhatsApp/WeChat-miss, 和平精英->PUBG)
  - empty / missing title or body

Hard failures are written to data/translations/needs-review.json {id: [reasons]} so the
batch runner can re-translate them ("review" mode). Soft warnings print with --warn.

Usage:
  python tools/translate_qa.py --write <id> <id> ...   # QA the given ids, persist failures
  python tools/translate_qa.py --write --all           # QA every en/<id>.json
  python tools/translate_qa.py --warn  <id> ...        # also show soft warnings
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TROOT = Path("data/translations")
EN = TROOT / "en"
IN = TROOT / "in"
NEEDS = TROOT / "needs-review.json"

CJK = re.compile(r"[一-鿿]")
WORK_TITLE = re.compile(r"《[^》]*》")
IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
FORBIDDEN = [
    (re.compile(r"\bTikTok\b"), "抖音 mistranslated as TikTok (should be Douyin)"),
    (re.compile(r"\bPUBG\b"), "和平精英/绝地求生 -> PUBG (use Game for Peace / PUBG Mobile per source)"),
]


def qa_one(rid: str) -> tuple[list[str], list[str]]:
    """Return (hard_failures, soft_warnings)."""
    enp = EN / f"{rid}.json"
    if not enp.exists():
        return ([f"no en/{rid}.json (did not translate)"], [])
    en = json.loads(enp.read_text(encoding="utf-8"))
    body = en.get("body") or ""
    title = en.get("title") or ""
    hard, soft = [], []

    if not title.strip():
        hard.append("empty title")
    if len(body.strip()) < 200:
        hard.append(f"body too short ({len(body)} chars) — likely truncated/summarized")

    # image parity vs source
    src_n = en.get("src_n_images")
    en_n = len(IMG.findall(body))
    if src_n is None:
        inp = IN / f"{rid}.json"
        if inp.exists():
            src_n = len(IMG.findall(json.loads(inp.read_text())["body"]))
    if src_n is not None and en_n != src_n:
        hard.append(f"image count {en_n} != source {src_n}")

    # residual Chinese (allow chars inside 《》 work titles)
    stripped = WORK_TITLE.sub("", title + "\n" + body)
    cjk = CJK.findall(stripped)
    if len(cjk) > 8:
        hard.append(f"{len(cjk)} Chinese chars remain outside 《》 titles")
    elif cjk:
        soft.append(f"{len(cjk)} stray Chinese char(s): {''.join(cjk[:10])}")

    # truncation vs source length (English is usually >= source char count for zh)
    inp = IN / f"{rid}.json"
    if inp.exists():
        src_body = json.loads(inp.read_text())["body"]
        if src_body and len(body) < 0.5 * len(src_body):
            soft.append(f"en body {len(body)} vs src {len(src_body)} chars — check for dropped sections")

    for rx, msg in FORBIDDEN:
        if rx.search(body):
            soft.append(msg)

    return hard, soft


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true", help="persist hard failures to needs-review.json")
    ap.add_argument("--warn", action="store_true", help="also print soft warnings")
    args = ap.parse_args()

    ids = args.ids
    if args.all:
        ids = sorted(p.stem for p in EN.glob("*.json"))
    if not ids:
        print("no ids given (use --all or list ids)")
        return 1

    failures: dict[str, list[str]] = {}
    clean = 0
    for rid in ids:
        hard, soft = qa_one(str(rid))
        if hard:
            failures[str(rid)] = hard
            print(f"  FAIL {rid}: {'; '.join(hard)}")
        else:
            clean += 1
        if args.warn and soft:
            print(f"  warn {rid}: {'; '.join(soft)}")

    print(f"\nQA: {clean}/{len(ids)} clean, {len(failures)} hard-failed")

    if args.write:
        existing = {}
        if NEEDS.exists():
            try:
                existing = json.loads(NEEDS.read_text() or "{}")
            except json.JSONDecodeError:
                existing = {}
        # refresh: drop ids we just re-checked, then add current failures
        for rid in ids:
            existing.pop(str(rid), None)
        existing.update(failures)
        NEEDS.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"needs-review.json now lists {len(existing)} id(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
