"""
Stage C — Extract clean article records from cached Wayback HTML.

For every article in data/articles.jsonl that has a cache/<id>.html, this
parses the QDaily article template, pulls metadata, sanitizes the body, and
emits one JSON record to data/articles_extracted.jsonl.

Selectors (validated against the 2014 sample):
  title:    .article-detail-hd .title
  category: .article-detail-hd .category-title
  author:   .article-detail-bd .author-share .author a:not(.avatar)
  date:     .article-detail-bd .author-share .author span.date:not(.smart-date)
  body:     .article-detail-bd  (with the metadata + boilerplate stripped)

Body sanitization:
  * remove: scripts, styles, share widgets, audio-player chrome, related links
  * whitelist tags: p, h2-h4, blockquote, ul/ol/li, figure, figcaption, img,
    a, em, strong, br, hr, table-family
  * strip event handlers, inline styles, javascript: hrefs
  * collect every <img src> into `images` (Wayback `id_` returns original URLs)

Articles whose cleaned body has < 40 chars of text and zero images are flagged
`is_stub=True` — Stage E renders them with the screenshot as fallback.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

# --- selectors ---
SEL_TITLE = ".article-detail-hd h2.title, .article-detail-hd .title"
# .category-title wraps BOTH category and title; the category itself is in
# .category-title .category (a <p> with iconfont span + a <span> holding the name)
SEL_CATEGORY = ".article-detail-hd .category-title .category"
SEL_BANNER = ".article-detail-hd img.banner"
SEL_AUTHOR_DIV = ".article-detail-bd .author-share .author"
SEL_DATE_SPANS = ".article-detail-bd .author-share .author span.date"
SEL_BODY = ".article-detail-bd"

# Selectors removed wholesale from the body before sanitisation
STRIP_SELECTORS = [
    ".author-share",
    ".com-share-favor",
    ".share-favor-bd",
    ".com-related-articles",
    ".com-article-comments",
    ".related-comments-content",
    ".article-detail-ft",
    # audio player chrome (49/50 articles have an empty .embed-mask block)
    ".embed-mask", ".embed-control",
    "script", "style", "noscript",
]

ALLOWED_TAGS = {
    "p", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
    "ul", "ol", "li",
    "figure", "figcaption", "img",
    "a", "em", "strong", "b", "i", "u", "s",
    "br", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "div", "span",  # kept but classes/styles stripped
}

# Attributes preserved per tag
ALLOWED_ATTRS = {
    "a": {"href", "title", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "*": set(),  # nothing else by default
}

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?")
DATE_SHORT_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class Extracted:
    id: int
    title: str
    category: str | None
    author: str | None
    publish_time: str | None        # full timestamp from page when available
    publish_date: str               # YYYY-MM-DD (page date if known, else folder date)
    folder_date: str                # YYYY-MM-DD from source repo path (for cross-check)
    date_mismatch: bool             # True if folder date != publish date
    banner_image: str | None        # hero image at top of page
    body_html: str
    body_text_len: int
    images: list[str]
    is_stub: bool
    source_path: str
    archive_url: str
    archive_ts: str
    original_url: str
    screenshot_url: str | None


def _text(el: Tag | None) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _author_name(soup: BeautifulSoup) -> str | None:
    # First <a> inside .author that's NOT the avatar link
    div = soup.select_one(SEL_AUTHOR_DIV)
    if not div:
        return None
    for a in div.find_all("a"):
        classes = a.get("class") or []
        if "avatar" in classes:
            continue
        name = a.get_text(strip=True)
        if name:
            return name
    # fallback: text of .author minus child date/avatar
    clone = BeautifulSoup(str(div), "lxml")
    for el in clone.select("span.date, .avatar"):
        el.decompose()
    txt = clone.get_text(" ", strip=True)
    return txt or None


def _publish_time(soup: BeautifulSoup) -> str | None:
    # Pick the .date span that has actual text (skip empty .smart-date)
    for el in soup.select(SEL_DATE_SPANS):
        if "smart-date" in (el.get("class") or []):
            continue
        t = el.get_text(strip=True)
        if t and DATE_RE.match(t):
            return t
    # fallback: any non-empty .date or .smart-date
    for el in soup.select(SEL_DATE_SPANS):
        t = el.get_text(strip=True)
        if t and DATE_RE.match(t):
            return t
    return None


def _img_url(img: Tag) -> str:
    """QDaily uses lazyload: real URL is in data-src; src is often blank."""
    for attr in ("data-src", "data-original", "src"):
        v = (img.get(attr) or "").strip()
        if v and not v.startswith("data:"):
            return v
    return ""


def _clean_body(body: Tag) -> tuple[str, int, list[str]]:
    """Return (sanitized html, plain text length, image src list)."""
    work = BeautifulSoup(str(body), "lxml")
    root = work.select_one(SEL_BODY) or work

    # 1. Strip wholesale
    for sel in STRIP_SELECTORS:
        for el in root.select(sel):
            el.decompose()

    # 2. Walk every tag: drop or unwrap
    images: list[str] = []
    for el in list(root.find_all(True)):
        if not el.parent:
            continue  # already removed via parent decompose

        if el.name == "img":
            url = _img_url(el)
            if not url:
                el.decompose()
                continue
            # Normalise to a single src attribute for the rendered output
            el.attrs = {"src": url}
            if alt := el.get("alt"):
                el["alt"] = alt
            images.append(url)
            continue

        if el.name not in ALLOWED_TAGS:
            # Unwrap unknown tags (keeps inner text/markup)
            el.unwrap()
            continue

        # Filter attributes
        allowed = ALLOWED_ATTRS.get(el.name, set()) | ALLOWED_ATTRS["*"]
        for attr in list(el.attrs.keys()):
            if attr not in allowed:
                del el.attrs[attr]
            elif attr == "href":
                href = (el.attrs[attr] or "").strip()
                if href.lower().startswith("javascript:"):
                    del el.attrs[attr]

    # 3. Drop empty wrappers (div/span with no text and no images)
    for el in list(root.find_all(["div", "span"])):
        if not el.find("img") and not el.get_text(strip=True):
            el.decompose()

    # 4. Inner HTML of the body container
    inner = root.decode_contents()
    text_len = len(BeautifulSoup(inner, "lxml").get_text(strip=True))
    return inner.strip(), text_len, images


def extract_one(record: dict, html: str) -> Extracted | None:
    soup = BeautifulSoup(html, "lxml")

    title = _text(soup.select_one(SEL_TITLE)) or record.get("title") or ""
    category = _text(soup.select_one(SEL_CATEGORY)) or record.get("category")
    author = _author_name(soup) or record.get("author")
    pub_time = _publish_time(soup)

    banner = soup.select_one(SEL_BANNER)
    banner_image = _img_url(banner) if banner else None

    pub_date = record["original_date"]
    if pub_time:
        m = DATE_SHORT_RE.match(pub_time)
        if m:
            pub_date = m.group(0)

    folder_date = record["original_date"]
    body_el = soup.select_one(SEL_BODY)
    if body_el is None:
        return None

    body_html, body_text_len, images = _clean_body(body_el)
    is_stub = body_text_len < 40 and not images

    return Extracted(
        id=record["id"],
        title=title,
        category=category,
        author=author,
        publish_time=pub_time,
        publish_date=pub_date,
        folder_date=folder_date,
        date_mismatch=(pub_date != folder_date),
        banner_image=banner_image,
        body_html=body_html,
        body_text_len=body_text_len,
        images=images,
        is_stub=is_stub,
        source_path=record["source_path"],
        archive_url=record["archive_url"],
        archive_ts=record["archive_ts"],
        original_url=record["original_url"],
        screenshot_url=record.get("screenshot_url"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="data/articles.jsonl")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="data/articles_extracted.jsonl")
    ap.add_argument("--errors", default="data/extract_errors.jsonl")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    # split('\n') — body_html in JSONL may contain U+2028.
    manifest = [json.loads(l) for l in Path(args.manifest).read_text(encoding="utf-8").split("\n") if l.strip()]
    cache = Path(args.cache)

    out_path = Path(args.out)
    err_path = Path(args.errors)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a tmp file and atomically rename — prevents readers (and
    # cloud-sync agents like Google Drive) from ever observing a partial file.
    tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_err = err_path.with_suffix(err_path.suffix + ".tmp")

    n_in = n_out = n_no_cache = n_err = n_stub = n_mismatch = 0
    with tmp_out.open("w", encoding="utf-8") as fout, tmp_err.open("w", encoding="utf-8") as ferr:
        for row in manifest:
            n_in += 1
            if args.limit is not None and n_out >= args.limit:
                break
            cf = cache / f"{row['id']}.html"
            if not cf.exists():
                n_no_cache += 1
                continue
            try:
                rec = extract_one(row, cf.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                n_err += 1
                ferr.write(json.dumps({"id": row["id"], "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False) + "\n")
                continue
            if rec is None:
                n_err += 1
                ferr.write(json.dumps({"id": row["id"], "error": "no body element"}, ensure_ascii=False) + "\n")
                continue
            if rec.is_stub:
                n_stub += 1
            if rec.date_mismatch:
                n_mismatch += 1
            fout.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            n_out += 1

    # Atomic rename: only after the full write succeeds.
    tmp_out.replace(out_path)
    tmp_err.replace(err_path)

    print(f"manifest_in={n_in}  extracted={n_out}  no_cache={n_no_cache}  errors={n_err}  stubs={n_stub}  date_mismatches={n_mismatch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
