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

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

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
    ap.add_argument("--records", default="data/articles_extracted.jsonl")
    ap.add_argument("--manifest", default="data/articles.jsonl",
                    help="full manifest; entries missing from --records are rendered as screenshot-only stubs")
    ap.add_argument("--templates", default="site/templates")
    ap.add_argument("--static", default="site/static")
    ap.add_argument("--out", default="public")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--image-mode", choices=("wayback", "local"), default="wayback")
    ap.add_argument("--base-url", default="/", help="URL prefix; '/' for local preview, '/qdaily_full_backup/' for GitHub Pages")
    ap.add_argument("--site-url", default="https://logoutx.github.io", help="absolute origin for RSS")
    ap.add_argument("--site-title", default="QDaily 好奇心日报存档")
    ap.add_argument("--site-description", default="好奇心日报文章存档,通过 Internet Archive 重建。")
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

    # Load records.
    # NOTE: split on '\n' only — body_html may contain U+2028/U+2029 (valid
    # inside a JSON string per RFC 8259, but str.splitlines() treats them
    # as line breaks and would split a record in half).
    records = []
    for line in Path(args.records).read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        records.append(json.loads(line))

    # Synthesize screenshot-only stubs for manifest entries we never extracted
    # (Wayback transport failure or Wayback-stub captures with no body).
    n_stubs_added = 0
    if Path(args.manifest).exists():
        extracted_ids = {r["id"] for r in records}
        for line in Path(args.manifest).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            m = json.loads(line)
            if m["id"] in extracted_ids:
                continue
            records.append({
                "id": m["id"],
                "title": m.get("title") or f"#{m['id']}",
                "category": m.get("category"),
                "author": m.get("author"),
                "publish_time": None,
                "publish_date": m["original_date"],
                "folder_date": m["original_date"],
                "date_mismatch": False,
                "banner_image": None,
                "body_html": "",
                "body_text_len": 0,
                "images": [],
                "is_stub": True,
                "is_screenshot_only": True,
                "source_path": m.get("source_path", ""),
                "archive_url": m.get("archive_url", ""),
                "archive_ts": m.get("archive_ts", ""),
                "original_url": m.get("original_url", ""),
                "screenshot_url": m.get("screenshot_url"),
            })
            n_stubs_added += 1

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
        rendered.append({
            **r,
            "body_html_resolved": body_html,
            "banner_image_resolved": banner_resolved,
            "banner_broken": banner_broken,
            "publish_rfc822": rfc822(r.get("publish_time") or r["publish_date"]),
        })

    # Sort newest-first for indexes
    rendered.sort(key=lambda r: (r["publish_date"], r["id"]), reverse=True)

    # Years and counts
    years = sorted({r["publish_date"][:4] for r in rendered})
    years_with_counts = []
    for y in years:
        years_with_counts.append((y, sum(1 for r in rendered if r["publish_date"].startswith(y))))
    env.globals["years"] = years

    # Article pages
    for r in rendered:
        page_dir = out / "articles" / str(r["id"])
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            env.get_template("article.html").render(article=r),
            encoding="utf-8",
        )

    # Home
    (out / "index.html").write_text(
        env.get_template("home.html").render(
            total=len(rendered),
            first_date=rendered[-1]["publish_date"],
            last_date=rendered[0]["publish_date"],
            latest=rendered[:50],
            years_with_counts=sorted(years_with_counts),
        ),
        encoding="utf-8",
    )

    # Year pages
    by_year = defaultdict(list)
    for r in rendered:
        by_year[r["publish_date"][:4]].append(r)
    for y, items in by_year.items():
        items.sort(key=lambda r: (r["publish_date"], r["id"]))
        # subnav: months in this year
        months = sorted({r["publish_date"][5:7] for r in items})
        subnav = [(m, url(f"{y}/{m}/")) for m in months]
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
