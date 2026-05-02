"""
Import supplementary articles into the archive (e.g. PEKExpress on Medium).

Reads Medium's per-user RSS feed, picks the items whose URLs match TARGETS,
extracts an in-body byline if one is present (e.g. "作者：刘璐天 …"),
applies any per-URL author override, sanitises the body HTML, and emits one
record per article to data/articles_extracted_extra.jsonl.

The renderer's existing data/articles_extracted_*.jsonl glob picks the file
up automatically. A separate file (rather than appending to a per-year file)
keeps these survives across future extract.py runs that overwrite per-year
files for the QDaily core.

Usage:
    python tools/import_extras.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

# Each entry: URL fragment to match, optional per-URL overrides.
TARGETS: list[dict] = [
    {"suffix": "19bd7aed7a3c"},
    {"suffix": "126686f969d1"},
    {"suffix": "2aaabebab1e0", "author": "刘璐天 晏文静 唐云路 黄俊杰 罗骢 谢金萍 张智伟 周韶宏 杨宽"},
    {"suffix": "23f81eb65a3a"},
]
FEED_URL = "https://medium.com/feed/@PEKExpress"
ID_BASE = 9_000_000  # synthetic IDs above QDaily's range
OUT_PATH = Path("data/articles_extracted_extra.jsonl")

ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
    "ul", "ol", "li",
    "figure", "figcaption", "img",
    "a", "em", "strong", "b", "i", "u", "s", "br", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "div", "span",
}
ALLOWED_ATTRS = {
    "a": {"href", "title", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
}
BYLINE_PATTERNS = [
    re.compile(r"作者[：:]\s*([^\n。]+)"),
    re.compile(r"撰稿[：:]\s*([^\n。]+)"),
    re.compile(r"文[／/]\s*([^\n。]+)"),
]


def parse_byline(body_text: str) -> str | None:
    head = body_text[:600]
    for pat in BYLINE_PATTERNS:
        m = pat.search(head)
        if m:
            name = m.group(1).strip().rstrip("，,；; ").strip()
            # Sanity: bylines tend to be short
            if 1 <= len(name) <= 80:
                return name
    return None


def clean_body(html: str) -> tuple[str, list[str]]:
    """Sanitise Medium HTML; return (cleaned_html, image_urls)."""
    soup = BeautifulSoup(html, "lxml")
    for sel in ("script", "style", "noscript", "iframe"):
        for el in soup.select(sel):
            el.decompose()

    images: list[str] = []
    for el in list(soup.find_all(True)):
        if not el.parent:
            continue
        if el.name == "img":
            src = (el.get("src") or "").strip()
            if not src or src.startswith("data:"):
                el.decompose()
                continue
            el.attrs = {"src": src}
            if el.get("alt"):
                el["alt"] = el["alt"]
            images.append(src)
            continue
        if el.name not in ALLOWED_TAGS:
            el.unwrap()
            continue
        allowed = ALLOWED_ATTRS.get(el.name, set())
        for k in list(el.attrs):
            if k not in allowed:
                del el.attrs[k]
            elif k == "href" and (el["href"] or "").lower().startswith("javascript:"):
                del el["href"]

    body = soup.body or soup
    return body.decode_contents().strip(), images


def main() -> int:
    print(f"fetching {FEED_URL}")
    r = httpx.get(FEED_URL, follow_redirects=True, timeout=30.0,
                  headers={"User-Agent": "Mozilla/5.0 (qdaily-archive/0.1)"})
    r.raise_for_status()
    root = ET.fromstring(r.text)

    items_by_suffix = {}
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        for tgt in TARGETS:
            if tgt["suffix"] in link:
                items_by_suffix[tgt["suffix"]] = (item, tgt)
                break

    missing = [t["suffix"] for t in TARGETS if t["suffix"] not in items_by_suffix]
    if missing:
        print(f"warning: feed did not contain suffix(es): {missing}", file=sys.stderr)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    next_id = ID_BASE + 1
    n = 0
    with OUT_PATH.open("w", encoding="utf-8") as fout:
        for tgt in TARGETS:
            pair = items_by_suffix.get(tgt["suffix"])
            if not pair:
                continue
            item, target = pair
            link = (item.findtext("link") or "").split("?")[0].strip()
            title = (item.findtext("title") or "").strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            try:
                dt = parsedate_to_datetime(pub_raw)
            except Exception:
                dt = datetime.utcnow()
            content_html = (
                item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
                or ""
            )

            body_soup = BeautifulSoup(content_html, "lxml")
            body_text = body_soup.get_text(" ", strip=True)
            byline = parse_byline(body_text)
            author = target.get("author") or byline or "PEK Express"

            cleaned_html, images = clean_body(content_html)
            text_only = BeautifulSoup(cleaned_html, "lxml").get_text(strip=True)

            rec = {
                "id": next_id,
                "title": title,
                "category": "PEKExpress",
                "author": author,
                "publish_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "publish_date": dt.strftime("%Y-%m-%d"),
                "folder_date": dt.strftime("%Y-%m-%d"),
                "date_mismatch": False,
                "banner_image": images[0] if images else None,
                "body_html": cleaned_html,
                "body_text_len": len(text_only),
                "images": images,
                "is_stub": False,
                "is_screenshot_only": False,
                "source_path": link,
                "archive_url": link,
                "archive_ts": "",  # no Wayback rewrite (still-live source)
                "original_url": link,
                "screenshot_url": None,
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            next_id += 1
            n += 1
            print(f"  {rec['id']}  {rec['publish_date']}  {rec['author'][:30]:<30}  {title[:50]}")

    print(f"\nwrote {n} records to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
