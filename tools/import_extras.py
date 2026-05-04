"""
Import supplementary articles from local markdown files.

Each input file starts with a meta block:

    Title: ...
    Date: 2017 年 11 月 27 日
    Author: ...
    Link: http://www.qdaily.com/articles/<id>.html
    Subtitle: ...    (optional)

followed by a blank line and the article body in markdown. The QDaily ID
parsed from `Link:` becomes the record id, so when Phase 2 later extracts
the same article from Wayback, render.py's dedup keeps this manually-
curated version (the per-year file would be alphabetically earlier than
data/articles_extracted_extra.jsonl, so the manual record wins).

Edit SOURCES to add more files.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import markdown as md_lib
from bs4 import BeautifulSoup

DOWNLOADS = Path("/Users/logoutx/Downloads")
SOURCES: list[Path] = [
    DOWNLOADS / "11 月 27 日，新建庄见闻. 转自《好奇心日报》，11 月 27 日，2 点发布。作者唐云路。源链接…  by PEK Express  Medium.md",
    DOWNLOADS / "48 小时之内，25 日之前，最后两天. 北京大兴火灾后，政府发动 40 天清理行动。《好奇心日报》2017 年 11…  by PEK Express  Medium.md",
    DOWNLOADS / "“人文关怀”，和皮村多出来的 96 小时. 转自《好奇心日报》，11 月 28 日 8 点发布，作者刘璐天。源链接…  by PEK Express  Medium.md",
    DOWNLOADS / "晚上 8 点半，连续 7 天，距离首都机场 2 公里. 转自《好奇心日报》，2017 年 12 月 2 日 22 点发布，3 日下午…  by PEK Express  Medium.md",
]
OUT_PATH = Path("data/articles_extracted_extra.jsonl")
# These four articles all belong to QDaily's "城市" beat. If a future
# import covers other beats, lift this into a per-source field instead.
DEFAULT_CATEGORY = "城市"

META_KEYS = {"Title", "Date", "Author", "Link", "Subtitle"}
META_LINE_RE = re.compile(r"^([A-Za-z]+):\s*(.*)$")
DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
ID_RE = re.compile(r"/(?:articles|cards)/(\d+)(?:\.html)?")
SUBTITLE_PREFIX = "Subtitle: "

# Boilerplate lines emitted by Medium's "Save as markdown" / page-source
# tooling; strip before converting markdown -> HTML.
BOILERPLATE_LINE_RE = re.compile(
    r"^\s*Press enter or click to view image in full size\s*$\n?",
    re.MULTILINE,
)


def parse_meta_and_body(text: str) -> tuple[dict, str]:
    """Split the leading Key: Value block from the markdown body."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    meta: dict[str, str] = {}
    while i < len(lines):
        m = META_LINE_RE.match(lines[i])
        if not m:
            break
        key = m.group(1)
        if key not in META_KEYS:
            break
        meta[key] = m.group(2).strip()
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return meta, "\n".join(lines[i:])


def parse_chinese_date(s: str) -> datetime:
    m = DATE_RE.search(s)
    if not m:
        raise ValueError(f"could not parse Chinese date: {s!r}")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return datetime(y, mo, d)


def article_id_from_link(link: str) -> int | None:
    m = ID_RE.search(link)
    return int(m.group(1)) if m else None


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    md = md_lib.Markdown(extensions=["extra", "sane_lists", "smarty"])

    # Look up the Wayback URL+timestamp from the main manifest so footer can
    # render a 'Wayback 快照' link alongside the dead 原文链接. 47595 used
    # the m.qdaily.com mobile-cards URL which Wayback never crawled — for
    # that one we fall back to leaving archive_url empty.
    main_manifest = {}
    main_path = Path("data/articles.jsonl")
    if main_path.exists():
        for line in main_path.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            main_manifest[r["id"]] = r

    n = 0
    with OUT_PATH.open("w", encoding="utf-8") as fout:
        for src in SOURCES:
            if not src.exists():
                print(f"  MISSING: {src}", file=sys.stderr)
                continue
            text = src.read_text(encoding="utf-8")
            meta, body_md = parse_meta_and_body(text)

            title = meta.get("Title", "").strip()
            author = meta.get("Author", "").strip()
            link = meta.get("Link", "").strip()
            subtitle = meta.get("Subtitle", "").strip()
            try:
                dt = parse_chinese_date(meta.get("Date", ""))
            except ValueError:
                print(f"  WARN: bad date in {src.name!r}", file=sys.stderr)
                continue
            article_id = article_id_from_link(link)
            if article_id is None:
                print(f"  WARN: no /articles/<id>.html in Link: {link!r}", file=sys.stderr)
                continue

            # Strip Medium boilerplate before rendering.
            body_md = BOILERPLATE_LINE_RE.sub("", body_md)

            md.reset()
            body_html_inner = md.convert(body_md)
            if subtitle:
                body_html_inner = (
                    f'<p class="subtitle">{subtitle}</p>\n' + body_html_inner
                )

            soup = BeautifulSoup(body_html_inner, "lxml")
            images = [
                (img.get("src") or "").strip()
                for img in soup.find_all("img")
                if (img.get("src") or "").strip()
            ]
            text_only = soup.get_text(" ", strip=True)
            excerpt = subtitle or (
                text_only[:140] + "…" if len(text_only) > 140 else text_only
            )

            # Pull Wayback metadata from the main manifest if available
            # (LampScript backed up the QDaily side too, so we already know
            # a working snapshot for these IDs — except 47595 which was
            # the mobile-cards URL Wayback never crawled).
            main_rec = main_manifest.get(article_id, {})
            archive_url = main_rec.get("archive_url", "") or ""
            archive_ts = main_rec.get("archive_ts", "") or ""

            rec = {
                "id": article_id,
                "title": title,
                "category": DEFAULT_CATEGORY,
                "author": author,
                "publish_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "publish_date": dt.strftime("%Y-%m-%d"),
                "folder_date": dt.strftime("%Y-%m-%d"),
                "date_mismatch": False,
                "banner_image": images[0] if images else None,
                "body_html": body_html_inner,
                "body_text_len": len(text_only),
                "images": images,
                "is_stub": False,
                "is_screenshot_only": False,
                "like_count": None,
                "source_path": src.name,
                "archive_url": archive_url,
                "archive_ts": archive_ts,
                "original_url": link,
                "screenshot_url": None,
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            print(
                f"  id={article_id}  date={rec['publish_date']}  "
                f"author_len={len(author):>3}  imgs={len(images):>2}  "
                f"body_text_len={len(text_only):>5}  title_len={len(title)}"
            )

    print(f"\nwrote {n} records to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
