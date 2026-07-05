"""
Collect finished translations: parse data/translations/out/<id>.txt (the sentinel
format the Opus polish agent writes) into data/translations/en/<id>.json, then delete
the consumed .txt. Idempotent; never touches the zh corpus.

en/<id>.json = { id, title, excerpt, body (markdown), n_images, collected_from,
                 src_images } — body_html for rendering is produced later by render.py
(markdown -> html), so we keep the markdown here.

Usage:  python tools/translate_collect.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TROOT = Path("data/translations")
OUT = TROOT / "out"
EN = TROOT / "en"
IN = TROOT / "in"

T, E, B = "@@QD_TITLE@@", "@@QD_EXCERPT@@", "@@QD_BODY@@"


def parse(text: str) -> dict | None:
    if T not in text or B not in text:
        return None
    after_t = text.split(T, 1)[1]
    if E in after_t:
        title_part, rest = after_t.split(E, 1)
        excerpt_part, body = rest.split(B, 1)
    else:
        title_part, body = after_t.split(B, 1)
        excerpt_part = ""
    return {
        "title": title_part.strip(),
        "excerpt": excerpt_part.strip(),
        "body": body.strip("\n"),
    }


def count_images(md: str) -> int:
    return len(re.findall(r"!\[[^\]]*\]\(", md or ""))


def main() -> int:
    EN.mkdir(parents=True, exist_ok=True)
    written, bad = 0, []
    for f in sorted(OUT.glob("*.txt")):
        rid = f.stem
        parsed = parse(f.read_text(encoding="utf-8"))
        if not parsed or not parsed["body"].strip():
            bad.append(rid)
            continue
        src_imgs = []
        inp = IN / f"{rid}.json"
        if inp.exists():
            src = json.loads(inp.read_text(encoding="utf-8"))
            src_imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", src.get("body", ""))
        rec = {
            "id": int(rid),
            "title": parsed["title"],
            "excerpt": parsed["excerpt"],
            "body": parsed["body"],
            "n_images": count_images(parsed["body"]),
            "src_n_images": len(src_imgs),
        }
        (EN / f"{rid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        f.unlink()
        written += 1
    print(f"collected {written} -> en/  (unparseable left in out/: {len(bad)}{' '+','.join(bad) if bad else ''})")
    print(f"total EN posts: {len(list(EN.glob('*.json')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
