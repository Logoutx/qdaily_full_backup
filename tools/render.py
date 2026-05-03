"""
Stage E — Render the static site from data/articles_extracted.jsonl.

Output goes to public/. Image URLs are resolved at render time:
  --image-mode wayback : rewrite to https://web.archive.org/web/<ts>im_/<orig>
                         (default; works today, no extra fetch)
  --image-mode local   : look for assets/<id>/<sha1>.<ext>; fall back to wayback
                         if not present.

Internal-host images (121.201.7.32:8001) are not in Wayback. They render with
data-broken="1" so the CSS hides them.
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import re

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Match a single CJK character (Unified Ideographs + Extension A; covers
# all of modern Chinese plus most rare characters in QDaily content).
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


def _segment(text: str) -> str:
    """Insert spaces around every CJK character so Pagefind treats each as
    its own token. Latin words and digits remain whole.

    This replaced an earlier jieba-word-segmenter approach: jieba's
    tokenization is context-dependent — the same compound (小红书,
    美团外卖, AI 芯片, 字节跳动 …) can split differently across articles,
    so an auto-quoted exact-phrase query at search time would miss many
    real occurrences. Per-character CJK tokenization gives strict
    substring matching on Chinese (the natural mental model) while
    preserving Latin word boundaries via existing whitespace.

    "苹果iPhone发布会" -> "苹 果 iPhone 发 布 会"
    """
    if not text:
        return ""
    return _CJK_RE.sub(lambda m: " " + m.group(0) + " ", text).replace("  ", " ").strip()


def _plain_text(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)

BROKEN_HOSTS = {"121.201.7.32:8001"}


def wayback_im(orig: str, ts: str) -> str:
    return f"https://web.archive.org/web/{ts}im_/{orig}"


def is_broken_host(url: str) -> bool:
    try:
        return urlparse(url).netloc in BROKEN_HOSTS
    except Exception:
        return False


def resolve_url(orig: str, ts: str, mode: str, article_id: int, assets_root: Path) -> tuple[str | None, bool]:
    """
    Returns (resolved_url, is_broken). If broken host, resolved_url is the
    Wayback URL anyway (so a click-through still has a chance) but the
    `is_broken` flag tells the template to hide the inline image.
    """
    if not orig:
        return None, False
    broken = is_broken_host(orig)
    if mode == "local":
        # Try local asset first
        ext = Path(urlparse(orig).path).suffix.lower() or ".bin"
        digest = hashlib.sha1(orig.encode("utf-8")).hexdigest()[:16]
        local = assets_root / str(article_id) / f"{digest}{ext}"
        if local.exists():
            # rel path from public/articles/<id>/index.html → public/assets/<id>/file
            return f"../../assets/{article_id}/{digest}{ext}", broken
    if not ts:
        # No Wayback timestamp — this is an externally-sourced article
        # (e.g. Medium) whose original images are still live. Pass through.
        return orig, broken
    return wayback_im(orig, ts), broken


def resolve_body(body_html: str, ts: str, article_id: int, mode: str, assets_root: Path) -> tuple[str, int]:
    """Rewrite <img src> in body_html. Returns (rewritten_html, broken_count)."""
    if not body_html:
        return "", 0
    soup = BeautifulSoup(body_html, "lxml")
    # decode_contents on the parsed soup unwraps the auto-added <html><body>
    broken_count = 0
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            img.decompose()
            continue
        new, broken = resolve_url(src, ts, mode, article_id, assets_root)
        img["src"] = new or src
        if broken:
            img["data-broken"] = "1"
            broken_count += 1
        if "loading" not in img.attrs:
            img["loading"] = "lazy"
    body = soup.body or soup
    return body.decode_contents(), broken_count


def rfc822(date_str: str) -> str:
    # date_str is YYYY-MM-DD or full timestamp; assume UTC
    try:
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        dt = datetime.utcnow()
    return email.utils.format_datetime(dt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl",
                    help="glob pattern for per-year extracted record files")
    ap.add_argument("--manifest", default="data/articles.jsonl",
                    help="full manifest (kept for compatibility; not used for stub synthesis)")
    ap.add_argument("--templates", default="site/templates")
    ap.add_argument("--static", default="site/static")
    ap.add_argument("--out", default="public")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--image-mode", choices=("wayback", "local"), default="wayback")
    ap.add_argument("--base-url", default="/", help="URL prefix; '/' for local preview, '/qdaily_full_backup/' for GitHub Pages")
    ap.add_argument("--site-url", default="https://logoutx.github.io", help="absolute origin for RSS")
    ap.add_argument("--site-title", default="QDaily 好奇心日报存档")
    ap.add_argument("--site-description", default="好奇心日报所刊发内容存档，通过 Internet Archive 重建。")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Reset output (keep simple; cheap for now)
    for child in out.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    # Copy static
    static_src = Path(args.static)
    if static_src.exists():
        shutil.copytree(static_src, out / "static")

    base_url = args.base_url if args.base_url.endswith("/") else args.base_url + "/"

    def url(path: str) -> str:
        if path.startswith("/"):
            path = path[1:]
        return base_url + path

    env = Environment(
        loader=FileSystemLoader(args.templates),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.globals.update(
        site_title=args.site_title,
        site_description=args.site_description,
        site_url=args.site_url,
        url=url,
    )

    # Load records from one-file-per-year layout.
    # NOTE: split on '\n' only — body_html may contain U+2028/U+2029 (valid
    # inside a JSON string per RFC 8259, but str.splitlines() treats them
    # as line breaks and would split a record in half).
    import glob
    record_files = sorted(glob.glob(args.records_glob))
    if not record_files:
        # Backwards-compatible fallback: single legacy file.
        legacy = Path("data/articles_extracted.jsonl")
        if legacy.exists():
            record_files = [str(legacy)]
    # Dedup by id, keeping the LAST occurrence across files. Glob is sorted
    # alphabetically, so files like articles_extracted_2017.jsonl precede
    # articles_extracted_extra.jsonl — meaning manually-curated overrides
    # in the _extra file win over auto-extracted Wayback content.
    record_map: dict = {}
    n_total = 0
    n_overrides = 0
    for path in record_files:
        for line in Path(path).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            rec = json.loads(line)
            n_total += 1
            if rec["id"] in record_map:
                n_overrides += 1
            record_map[rec["id"]] = rec
    records = list(record_map.values())
    print(
        f"loaded {n_total} records from {len(record_files)} file(s); "
        f"{len(records)} unique after dedup (overrides applied: {n_overrides})"
    )

    # Stubs for unrecoverable articles are now baked into articles_extracted.jsonl
    # by extract.py (records with is_screenshot_only=True). render.py just
    # iterates whatever is there.
    n_stubs_added = sum(1 for r in records if r.get("is_screenshot_only"))

    if not records:
        print("No records to render.")
        return 0

    # Resolve image URLs and prepare per-article view objects
    assets_root = Path(args.assets)
    rendered = []
    total_broken = 0
    for r in records:
        body_html, broken_in_body = resolve_body(
            r.get("body_html") or "", r["archive_ts"], r["id"], args.image_mode, assets_root
        )
        banner = r.get("banner_image")
        banner_resolved, banner_broken = (None, False)
        if banner:
            banner_resolved, banner_broken = resolve_url(banner, r["archive_ts"], args.image_mode, r["id"], assets_root)
        total_broken += broken_in_body + (1 if banner_broken else 0)
        # Search index inputs (jieba-segmented, hidden block in article page)
        plain_body = _plain_text(body_html)
        title_seg = _segment(r["title"])
        body_seg = _segment(plain_body)
        excerpt = (plain_body[:140] + "…") if len(plain_body) > 140 else plain_body

        rendered.append({
            **r,
            "body_html_resolved": body_html,
            "banner_image_resolved": banner_resolved,
            "banner_broken": banner_broken,
            "publish_rfc822": rfc822(r.get("publish_time") or r["publish_date"]),
            "title_seg": title_seg,
            "body_seg": body_seg,
            "excerpt": excerpt,
        })

    # Sort newest-first for indexes
    rendered.sort(key=lambda r: (r["publish_date"], r["id"]), reverse=True)

    # Tag long articles. The 长文章 designation is meant for QDaily's
    # original feature reporting, so we exclude:
    #   * articles below the body-text-length threshold
    #   * foreign-source pieces (any non-CJK characters in the byline,
    #     e.g. 'Kate Conger, Richard Fausset and Serge F. Kovaleski')
    #   * historical-essay reprint series whose titles are wrapped in
    #     《…》 or 【…】 brackets (e.g. the 2019-05-04 五四 reprints
    #     of 胡适 / 陈独秀 / 周作人 essays)
    LONG_THRESHOLD = 4000
    AUTHOR_PURE_CJK_RE = re.compile(r"^[一-鿿\s·、，,；; ]+$")
    REPRINT_TITLE_RE = re.compile(r"^[《【]")
    for r in rendered:
        if (r.get("body_text_len") or 0) < LONG_THRESHOLD:
            r["is_long"] = False
            continue
        author = r.get("author") or ""
        title = r.get("title") or ""
        if not AUTHOR_PURE_CJK_RE.match(author):
            r["is_long"] = False
            continue
        if REPRINT_TITLE_RE.match(title):
            r["is_long"] = False
            continue
        r["is_long"] = True

    # Years and counts
    years = sorted({r["publish_date"][:4] for r in rendered})
    years_with_counts = []
    for y in years:
        years_with_counts.append((y, sum(1 for r in rendered if r["publish_date"].startswith(y))))
    env.globals["years"] = years
    env.globals["has_long_index"] = any(r["is_long"] for r in rendered)

    # Article pages
    for r in rendered:
        page_dir = out / "articles" / str(r["id"])
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            env.get_template("article.html").render(article=r),
            encoding="utf-8",
        )

    # Index of long articles (used by /long/, year subnav, home button)
    long_articles = [r for r in rendered if r["is_long"]]
    long_by_year: dict[str, list] = defaultdict(list)
    for r in long_articles:
        long_by_year[r["publish_date"][:4]].append(r)
    years_with_long = sorted(long_by_year.keys())

    # Home
    (out / "index.html").write_text(
        env.get_template("home.html").render(
            total=len(rendered),
            first_date=rendered[-1]["publish_date"],
            last_date=rendered[0]["publish_date"],
            latest=rendered[:50],
            years_with_counts=sorted(years_with_counts),
            long_total=len(long_articles),
        ),
        encoding="utf-8",
    )

    # Year pages
    by_year = defaultdict(list)
    for r in rendered:
        by_year[r["publish_date"][:4]].append(r)
    for y, items in by_year.items():
        items.sort(key=lambda r: (r["publish_date"], r["id"]))
        months = sorted({r["publish_date"][5:7] for r in items})
        subnav = [(m, url(f"{y}/{m}/")) for m in months]
        long_in_year = sum(1 for r in items if r["is_long"])
        if long_in_year:
            subnav.append((f"只看长文章 ({long_in_year})", url(f"long/{y}/")))
        page_dir = out / y
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            env.get_template("list.html").render(
                heading=f"{y} 年 · {len(items)} 篇",
                articles=items, subnav=subnav,
            ),
            encoding="utf-8",
        )
        # Month pages
        by_month = defaultdict(list)
        for r in items:
            by_month[r["publish_date"][5:7]].append(r)
        for m, mitems in by_month.items():
            mitems.sort(key=lambda r: (r["publish_date"], r["id"]))
            mp = out / y / m
            mp.mkdir(parents=True, exist_ok=True)
            (mp / "index.html").write_text(
                env.get_template("list.html").render(
                    heading=f"{y} 年 {m} 月 · {len(mitems)} 篇",
                    articles=mitems, subnav=[("← 返回 " + y, url(y + "/"))],
                ),
                encoding="utf-8",
            )

    # Long-article aggregate pages
    if long_articles:
        long_dir = out / "long"
        long_dir.mkdir(parents=True, exist_ok=True)
        # /long/ — all long articles (newest first), with year subnav
        long_sorted = sorted(long_articles, key=lambda r: (r["publish_date"], r["id"]), reverse=True)
        all_subnav = [(y, url(f"long/{y}/")) for y in years_with_long]
        (long_dir / "index.html").write_text(
            env.get_template("list.html").render(
                heading=f"长文章 · {len(long_articles)} 篇",
                articles=long_sorted, subnav=all_subnav,
            ),
            encoding="utf-8",
        )
        # /long/<YEAR>/ — long articles for that year (oldest first, like year pages)
        for y, items in long_by_year.items():
            items_sorted = sorted(items, key=lambda r: (r["publish_date"], r["id"]))
            other = [(yy, url(f"long/{yy}/")) for yy in years_with_long if yy != y]
            sub = [("← 全部长文章", url("long/"))] + other
            yp = long_dir / y
            yp.mkdir(parents=True, exist_ok=True)
            (yp / "index.html").write_text(
                env.get_template("list.html").render(
                    heading=f"{y} 年长文章 · {len(items)} 篇",
                    articles=items_sorted, subnav=sub,
                ),
                encoding="utf-8",
            )

    # Search page
    search_dir = out / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "index.html").write_text(
        env.get_template("search.html").render(total=len(rendered)),
        encoding="utf-8",
    )

    # RSS feed (latest 200)
    (out / "feed.xml").write_text(
        env.get_template("feed.xml").render(
            latest=rendered[:200],
            build_date=email.utils.format_datetime(datetime.utcnow()),
        ),
        encoding="utf-8",
    )

    # No-jekyll: prevent GH Pages from running Jekyll
    (out / ".nojekyll").write_text("")

    print(f"Rendered {len(rendered)} articles to {out}")
    print(f"  base_url={base_url}  image_mode={args.image_mode}")
    print(f"  screenshot-only stubs: {n_stubs_added}")
    print(f"  broken images (hidden via CSS): {total_broken}")
    print(f"  years: {', '.join(years)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
