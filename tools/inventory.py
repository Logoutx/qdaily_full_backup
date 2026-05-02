"""
Stage A — Inventory.

Walks the source qdaily_backup checkout and emits one JSON record per article
to data/articles.jsonl. Idempotent: rerunning produces the same output.

Source format (per .md file, exactly 3 lines):
  好奇心原文链接：[<label>](https://www.qdaily.com/articles/<ID>.html)
  WebArchive归档链接：[<label>](http://web.archive.org/web/<TS>/http://www.qdaily.com:80/articles/<ID>.html)
  ![image](<screenshot URL>)

Filename:  <title>_<category>_好奇心日报-<author>.md
Path:      <year>/<month>/<day>/<filename>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

ORIGINAL_RE = re.compile(
    r"好奇心原文链接[:：]\s*\[[^\]]*\]\((https?://(?:www\.)?qdaily\.com/articles/(\d+)\.html)\)"
)
ARCHIVE_RE = re.compile(
    r"WebArchive归档链接[:：]\s*\[[^\]]*\]\((https?://web\.archive\.org/web/(\d+)/[^)]+)\)"
)
SCREENSHOT_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
FILENAME_TAIL = "_好奇心日报-"


@dataclass
class Article:
    id: int
    title: str
    category: str | None
    author: str | None
    original_url: str
    archive_url: str
    archive_ts: str
    screenshot_url: str | None
    original_date: str  # YYYY-MM-DD from folder path
    source_path: str   # relative to source root


def parse_filename(stem: str) -> tuple[str, str | None, str | None]:
    """
    Returns (title, category, author).
    Pattern: <title>_<category>_好奇心日报-<author>
    Falls back gracefully if the pattern doesn't match.
    """
    if FILENAME_TAIL in stem:
        head, author = stem.rsplit(FILENAME_TAIL, 1)
        author = author.strip() or None
        # head = <title>_<category> — split on the last underscore
        if "_" in head:
            title, category = head.rsplit("_", 1)
            return title.strip(), (category.strip() or None), author
        return head.strip(), None, author
    return stem.strip(), None, None


def parse_md(path: Path) -> str | None:
    """Return None on parse failure, else an Article-as-jsonl line."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"__ERROR__\t{path}\tread:{e}"

    m_orig = ORIGINAL_RE.search(text)
    m_arch = ARCHIVE_RE.search(text)
    m_shot = SCREENSHOT_RE.search(text)

    if not m_orig or not m_arch:
        return f"__ERROR__\t{path}\tmissing_link"

    original_url = m_orig.group(1)
    article_id = int(m_orig.group(2))
    archive_url = m_arch.group(1)
    archive_ts = m_arch.group(2)
    screenshot_url = m_shot.group(1) if m_shot else None

    parts = path.parts
    # source/<year>/<month>/<day>/<file>.md  → take last 4
    if len(parts) < 4:
        return f"__ERROR__\t{path}\tbad_path"
    year, month, day = parts[-4], parts[-3], parts[-2]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return f"__ERROR__\t{path}\tbad_date_path"

    title, category, author = parse_filename(path.stem)

    art = Article(
        id=article_id,
        title=title,
        category=category,
        author=author,
        original_url=original_url,
        archive_url=archive_url,
        archive_ts=archive_ts,
        screenshot_url=screenshot_url,
        original_date=f"{year}-{month}-{day}",
        source_path="/".join(parts[parts.index(year):]),
    )
    return json.dumps(asdict(art), ensure_ascii=False)


def walk(source_root: Path, year: str | None) -> list[Path]:
    base = source_root / year if year else source_root
    return sorted(p for p in base.rglob("*.md") if p.name != "years.md" and not p.name.endswith("/.md") and not p.parent.name == p.stem)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="source", help="path to qdaily_backup checkout")
    parser.add_argument("--year", default=None, help="restrict to a year, e.g. 2014")
    parser.add_argument("--out", default="data/articles.jsonl")
    parser.add_argument("--errors", default="data/inventory_errors.tsv")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    out_path = Path(args.out)
    err_path = Path(args.errors)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path.parent.mkdir(parents=True, exist_ok=True)

    md_files = walk(source, args.year)
    print(f"Found {len(md_files)} candidate .md files under {source}/{args.year or ''}")

    seen_ids: set[int] = set()
    dupes: list[tuple[int, str]] = []
    cat_counts: Counter[str] = Counter()
    month_counts: Counter[str] = Counter()
    ok = 0
    errors: list[str] = []

    with out_path.open("w", encoding="utf-8") as fout:
        for p in md_files:
            # Skip month-index files like 2014/04/04.md (lives directly under a YYYY/MM/ dir)
            rel_parts = p.relative_to(source).parts
            if len(rel_parts) == 3:  # year/month/<file>.md = month index
                continue

            line = parse_md(p)
            if line is None or line.startswith("__ERROR__"):
                errors.append(line or f"__ERROR__\t{p}\tnone")
                continue
            rec = json.loads(line)
            aid = rec["id"]
            if aid in seen_ids:
                dupes.append((aid, rec["source_path"]))
                continue
            seen_ids.add(aid)
            cat_counts[rec.get("category") or "(none)"] += 1
            month_counts[rec["original_date"][:7]] += 1
            fout.write(line + "\n")
            ok += 1

    with err_path.open("w", encoding="utf-8") as ferr:
        for e in errors:
            ferr.write(e + "\n")
        for aid, sp in dupes:
            ferr.write(f"__DUPE__\t{aid}\t{sp}\n")

    print(f"Wrote {ok} records to {out_path}")
    print(f"Errors: {len(errors)}    Duplicates skipped: {len(dupes)}    (see {err_path})")
    print("\nTop 10 categories:")
    for cat, n in cat_counts.most_common(10):
        print(f"  {n:>5}  {cat}")
    print("\nArticles per month:")
    for m in sorted(month_counts):
        print(f"  {m}  {month_counts[m]:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
