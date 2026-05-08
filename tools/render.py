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
from urllib.parse import quote, urlparse

import re

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Match a single CJK character (Unified Ideographs + Extension A; covers
# all of modern Chinese plus most rare characters in QDaily content).
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


# --- Series definitions -----------------------------------------------------
#
# Each entry is (display_name, matcher). The matcher takes a record dict and
# returns True iff the article belongs to the series. Display order on the
# /series/ index page is determined dynamically by 长文章 ratio (see below).

# 卫星新闻's earlier set comes from the QDaily tag page snapshot
# (https://web.archive.org/web/20180322184409/http://www.qdaily.com:80/tags/47067.html);
# the column continued past that snapshot, so we also catch later articles
# that put '卫星新闻' in the title and don't double-count the
# 大公司头条 daily roundups.
_SATELLITE_TAG_IDS = {
    49281, 49320, 49362, 49407, 49415, 49505, 49570, 49695, 49797, 49908,
    49987, 50129, 50137, 50141, 50147, 50202, 50783, 50941, 51255, 51330,
}

# Articles the user pinned manually that don't match the standard pattern.
_TED_PIN_IDS = {54388}
_STORY_2017_IDS = {
    45493, 45992, 46778, 47549, 47595, 47663, 47668, 47670, 47749, 47786,
    47831, 47907, 48006, 48092, 48187, 48367, 48410, 49956,
}

# Per-column article-ID anchor sets, harvested from the QDaily
# /special_columns/<id>.html snapshots on Wayback (the "first page" each
# column page exposed before the site went down). For columns whose
# titles don't follow a reliable pattern, these sets ARE the membership.
_FOUNDER_SAYS_IDS = {  # 创始人说 — special_columns/6
    11220, 11227, 12311, 12521, 12736, 13068, 13232, 15178,
    15542, 15934, 16763, 16940, 17654, 17719, 17842, 18094,
    19719, 20259, 20431, 24425,
}
_MARKET_INVENTOR_IDS = {  # 市场发明家 — special_columns/7
    3699, 4173, 4370, 7172, 12299, 12692, 13092, 13117,
    14059, 17129, 17997, 18119, 18355, 18607, 19324, 20697,
    23491, 23816, 24152, 24515,
}
_REARVIEW_IDS = {  # 后视镜 — special_columns/11
    21598, 21599, 21600, 21649, 21651, 21662, 21729, 37100,
    37102, 37113, 37114, 37116, 37117, 37119,
}
_HOLLYWOOD_IDS = {  # 好莱坞报告 — special_columns/25
    12278, 12359, 12425, 12535, 12581, 12703, 12796, 12984,
    13124, 13276, 13905, 14002, 14194, 14363, 14633, 14871,
    15038, 15997, 21031, 31740,
}
_LAB_DATA_IDS = {  # 所长の大数据 — special_columns/33
    23285, 23355, 23395, 23441, 23487, 23638, 23650, 23786,
    23879, 23971, 24047, 24050, 24219, 24285, 24335, 24416,
    24500, 24517, 24626, 24673,
}
_WHY_READ_IDS = {  # 为什么读书 — special_columns/29
    23061, 23068, 23253, 23267, 23268, 23269, 23465, 23475,
    23478, 23532, 23710, 23716, 23717, 23821, 23824, 24128,
    24129, 24150, 24496, 24498,
}
_DISTRICT_42_IDS = {  # 42 区 — special_columns/34
    20988, 20994, 21067, 21234, 21249, 21255, 21535, 22597,
    22706, 22769, 23085, 23904, 24120, 24790,
}
_THINKING_22_IDS = {  # 22岁，他们在想什么 — special_columns/35
    28421, 28436, 28533, 28562, 28636, 28695, 28707, 28737,
    28851, 28911, 28915, 28920, 28974, 29045, 29099, 29148,
    29226, 29274, 29351,
}
_EUROPE_IDS = {  # 也许欧洲有答案 — special_columns/39
    29585, 30209, 30417, 30813, 30906, 31461, 32001,
}
_SOCIETY_YOUTH_IDS = {  # 这个社会，对年轻人太好了吗？ — special_columns/54
    37071, 37280, 37376, 37418, 37742, 37745, 37985,
}
# special_columns/41 — "2016 大公司数字化". Per user instruction, fold all
# 11 articles into the 年度观察 series (the renamed 年度报道).
_DIGITAL_CO_2016_IDS = {
    34053, 34166, 34296, 34457, 34559, 34693, 34794, 35104,
    35296, 35463, 35561,
}

_TED_TITLE_RE = re.compile(r"TED\s*201[789]\s*现场报道")
_ANNUAL_GAME_RE = re.compile(r"\d{4}\s*年度游戏")
_ANNUAL_TERMS = ("年度报道", "年度设计大赏", "年度公司", "年度图书", "年度报告")


def _series_match(name: str, r: dict) -> bool:
    title = r.get("title") or ""
    aid = r["id"]
    if name == "大公司头条":
        # 商业剪报 was the predecessor — merge it in per user instruction.
        return ("大公司头条" in title) or ("商业剪报" in title)
    if name == "今日娱乐":     return title.startswith("今日娱乐")
    if name == "「这世界」":    return title.startswith("「这世界」")
    if name == "看图":         return title.startswith("看图")
    if name == "今日应用":     return title.startswith("今日应用")
    if name == "「万物简史」":  return title.startswith("「万物简史」")
    if name == "「日本語」":    return title.startswith("「日本語」")
    if name == "「票房」":      return title.startswith("「票房」")
    if name == "「本周新片」":  return title.startswith("「本周新片」")
    if name == "浮华日报":     return "浮华日报" in title
    if name == "好奇心小数据":  return "好奇心小数据" in title
    if name == "乙方日报":     return "乙方日报" in title
    if name == "好奇心研究所":  return "好奇心研究所" in title
    if name == "好奇心辞典":   return "好奇心辞典" in title
    if name == "好奇心商业史":  return "好奇心商业史" in title
    if name == "100 个有想法的人":
        return "100 个有想法的人" in title or "100个有想法的人" in title
    if name == "这个人有好奇心":  return "这个人有好奇心" in title
    if name == "访谈录":       return "访谈录" in title
    if name == "上海时装周":    return "上海时装周" in title
    if name == "这个设计了不起": return "这个设计了不起" in title
    if name == "TED 现场报道":
        return bool(_TED_TITLE_RE.search(title)) or aid in _TED_PIN_IDS
    if name == "卫星新闻":
        # Tag-page set OR title-search set (excluding 大公司头条 dailies).
        if aid in _SATELLITE_TAG_IDS:
            return True
        return ("卫星新闻" in title) and ("大公司头条" not in title)
    if name == "2017 清退":    return aid in _STORY_2017_IDS
    if name == "年度观察":
        # Renamed from "年度报道" — now also includes the 11 articles of
        # the 2016 大公司数字化 column (special_columns/41), folded in
        # whole per user instruction.
        if aid in _DIGITAL_CO_2016_IDS:
            return True
        # Exclude 年度图书推荐 (treated as a separate annual reading-list
        # column, not part of the 年度观察 feature-length set), and
        # require the article to be a 长文章 — short year-end blurbs and
        # 大公司头条 daily roundups don't belong here.
        if "年度图书推荐" in title:
            return False
        if not r.get("is_long"):
            return False
        return any(term in title for term in _ANNUAL_TERMS) or bool(_ANNUAL_GAME_RE.search(title))
    if name == "房子和我们的生活":
        return "房子和我们的生活" in title
    # --- New series harvested from /special_columns/<id>.html snapshots --
    if name == "创始人说":               return aid in _FOUNDER_SAYS_IDS
    if name == "市场发明家":              return aid in _MARKET_INVENTOR_IDS
    if name == "后视镜":                 return aid in _REARVIEW_IDS
    if name == "好莱坞报告":              return aid in _HOLLYWOOD_IDS
    if name == "所长の大数据":             return aid in _LAB_DATA_IDS
    if name == "为什么读书":              return aid in _WHY_READ_IDS
    if name == "42 区":                 return aid in _DISTRICT_42_IDS
    if name == "22 岁，他们在想什么":       return aid in _THINKING_22_IDS
    if name == "也许欧洲有答案":           return aid in _EUROPE_IDS
    if name == "这个社会，对年轻人太好了吗？":  return aid in _SOCIETY_YOUTH_IDS
    if name == "历史上的今天":
        # Title pattern is highly reliable for this column; the snapshot's
        # first-page seed list (~20 IDs) is incomplete vs. the ~80 articles
        # actually in the corpus, so match by title.
        return "历史上的今天" in title
    if name == "Hack Your Life":
        # Catch-all column. Only claim articles not already classified
        # under any other series above.
        if "hack your life" not in title.lower():
            return False
        for other in SERIES_NAMES:
            if other == name:
                continue
            if _series_match(other, r):
                return False
        return True
    return False


SERIES_NAMES = [
    "大公司头条",
    "今日娱乐",
    "「这世界」",
    "看图",
    "今日应用",
    "「万物简史」",
    "「日本語」",
    "「票房」",
    "「本周新片」",
    "浮华日报",
    "好奇心小数据",
    "乙方日报",
    "好奇心研究所",
    "好奇心辞典",
    "好奇心商业史",
    "100 个有想法的人",
    "这个人有好奇心",
    "访谈录",
    "上海时装周",
    "这个设计了不起",
    "TED 现场报道",
    "卫星新闻",
    "2017 清退",
    "年度观察",
    "房子和我们的生活",
    # Newly added from QDaily /special_columns/<id> Wayback snapshots:
    "创始人说",
    "市场发明家",
    "后视镜",
    "好莱坞报告",
    "所长の大数据",
    "为什么读书",
    "42 区",
    "22 岁，他们在想什么",
    "也许欧洲有答案",
    "这个社会，对年轻人太好了吗？",
    "历史上的今天",
    "Hack Your Life",
]


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


# CJK author names that contain only Chinese characters and · / spaces
# (e.g. "黄俊杰", "黄俊杰 唐云路", "胡晓琪·").
_AUTHOR_CJK_RE = re.compile(r"^[一-鿿·\s]+$")


def split_authors(s: str) -> list[str]:
    """
    Split an author byline into one entry per individual author.

    Patterns observed in the corpus:
      "黄俊杰"                                           → 1 author
      "黄俊杰 唐云路"                                     → 2 (CJK + space)
      "MICHAEL MOSS、NEIL GOUGH"                         → 2 (、 separator)
      "黄自庚、潘姜汐熹、龚鉴"                            → 3
      "Kate Conger, Richard Fausset and Serge Kovaleski" → 3 (",", "and")
      "Michael Cieply ，Brooks Barnes"                    → 2 (fullwidth ，)
      "，郜艺"                                           → 1 (leading-comma typo)

    Western names with internal spaces (e.g. "Kate Conger") must NOT
    space-split — only CJK-only strings split on whitespace.
    """
    s = (s or "").strip()
    if not s:
        return []
    # Normalize all comma-like separators and " and " to a single comma,
    # then split.
    norm = s.replace("、", ",").replace("，", ",")
    norm = re.sub(r"\s+and\s+", ",", norm, flags=re.IGNORECASE)
    if "," in norm:
        return [p.strip() for p in norm.split(",") if p.strip()]
    # Pure-CJK byline with whitespace → split each token as its own author.
    if _AUTHOR_CJK_RE.match(s) and re.search(r"\s", s):
        return [p for p in s.split() if p]
    return [s]

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
    # Some legacy QDaily drafts have malformed src values like
    # `file://C:\Users\…\TempPic\GAZFYR0PEV@{RS1%$PA[N_6.tmp` that crash
    # urlparse on Python 3.12+ ("Invalid IPv6 URL" because of `[`).
    # If we can't parse the URL or it isn't http(s), drop the image —
    # it can't render in a browser anyway. Marked broken so the template
    # hides it.
    try:
        parsed = urlparse(orig)
    except ValueError:
        return orig, True
    if parsed.scheme not in ("http", "https"):
        return orig, True
    broken = is_broken_host(orig)
    # Live external CDNs — don't wrap in Wayback. The archive_ts we
    # carry on manually-imported Medium articles refers to the QDaily
    # snapshot URL, NOT to the Medium-hosted body/banner images. Those
    # Medium URLs are still live and were never crawled by Wayback at
    # the QDaily timestamp anyway, so wrapping them produces 404 stubs.
    host = parsed.netloc.lower()
    if host == "medium.com" or host.endswith(".medium.com"):
        return orig, broken
    if mode == "local":
        # Try local asset first
        ext = Path(parsed.path).suffix.lower() or ".bin"
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
    ap.add_argument("--base-url", default="/", help="URL prefix; '/' for the live site (qdaily.org) and local preview")
    ap.add_argument("--site-url", default="https://www.qdaily.org", help="absolute origin for RSS / canonical URLs")
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

    # In local image mode, copy mirrored Wayback assets so the
    # `../../assets/<id>/<digest>.<ext>` paths emitted by resolve_url()
    # resolve under public/assets/. We use copy (rather than symlink)
    # because GitHub Pages serves the artifact verbatim.
    assets_src = Path(args.assets)
    if args.image_mode == "local" and assets_src.exists():
        shutil.copytree(assets_src, out / "assets")

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
    # Author hyperlinking + dedicated /author/<name>/ page only for
    # purely-Chinese-character bylines. Names like "Kate Conger" or
    # "Selina 胡晨希" render as plain text and don't get a page.
    _PURE_CJK_AUTHOR_RE = re.compile(r"^[一-鿿]+$")

    def is_cjk_author(name: str) -> bool:
        return bool(name and _PURE_CJK_AUTHOR_RE.match(name))

    def author_url(name: str) -> str | None:
        # Returns None for non-CJK authors so templates can fall back
        # to plain text. The raw `name` goes into the URL path; the
        # browser percent-encodes it on the wire, GH Pages decodes
        # before lookup, and the on-disk dir is also named by `name`
        # (NOT the percent-encoded form — earlier bug).
        if not is_cjk_author(name):
            return None
        return url("author/" + quote(name, safe="") + "/")

    env.globals.update(
        site_title=args.site_title,
        site_description=args.site_description,
        site_url=args.site_url,
        url=url,
        author_url=author_url,
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
    # Article IDs to drop from the site entirely (no page rendered, no
    # listing entry, no search-index inclusion). Add an id here when an
    # article shouldn't appear in the public archive.
    EXCLUDED_IDS = {64091}

    record_map: dict = {}
    n_total = 0
    n_overrides = 0
    n_excluded = 0
    for path in record_files:
        for line in Path(path).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            rec = json.loads(line)
            n_total += 1
            if rec["id"] in EXCLUDED_IDS:
                n_excluded += 1
                continue
            if rec["id"] in record_map:
                n_overrides += 1
            record_map[rec["id"]] = rec
    records = list(record_map.values())
    print(
        f"loaded {n_total} records from {len(record_files)} file(s); "
        f"{len(records)} unique after dedup "
        f"(overrides applied: {n_overrides}, excluded: {n_excluded})"
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
        # Search index inputs (char-segmented, hidden block in article page).
        # Cap the body text shipped to Pagefind at SEARCH_BODY_CAP chars: with
        # ~50k articles and per-character CJK tokenization the in-memory
        # inverted index otherwise OOMs the GitHub-Actions runner during
        # `pagefind --site public`. The vast majority of QDaily pieces are
        # under this cap, and almost every meaningful term in a longer feature
        # appears within the first few thousand chars. Excerpt + visible body
        # are unaffected — readers still see the full article.
        SEARCH_BODY_CAP = 2500
        plain_body = _plain_text(body_html)
        title_seg = _segment(r["title"])
        body_seg = _segment(plain_body[:SEARCH_BODY_CAP])
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
    # Series whose articles never get the 长文章 chip even if the body
    # crosses the threshold (e.g. 大公司头条 daily roundups, which are
    # long by aggregation rather than by feature-reporting depth).
    LONG_EXCLUDED_SERIES = {"大公司头条"}
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
        if any(_series_match(s, r) for s in LONG_EXCLUDED_SERIES):
            r["is_long"] = False
            continue
        r["is_long"] = True

    # Pre-split author bylines once. Templates iterate r["authors"]
    # to render per-author hyperlinks; falls back to the raw string
    # when only one author was inferred (single Chinese name, single
    # Western name with internal spaces, etc.).
    for r in rendered:
        r["authors"] = split_authors(r.get("author"))

    # Years and counts
    years = sorted({r["publish_date"][:4] for r in rendered})
    years_with_counts = []
    for y in years:
        years_with_counts.append((y, sum(1 for r in rendered if r["publish_date"].startswith(y))))
    env.globals["years"] = years
    env.globals["has_long_index"] = any(r["is_long"] for r in rendered)

    # Compute series memberships once: name -> [records]
    series_articles: dict[str, list] = {name: [] for name in SERIES_NAMES}
    for r in rendered:
        for name in SERIES_NAMES:
            if _series_match(name, r):
                series_articles[name].append(r)

    series_stats = []
    for name in SERIES_NAMES:
        arts = series_articles.get(name, [])
        if not arts:
            continue
        n_total = len(arts)
        n_long = sum(1 for r in arts if r["is_long"])
        ratio = (n_long / n_total) if n_total else 0
        series_stats.append({"name": name, "total": n_total, "long": n_long, "ratio": ratio})
    # Home-page order: 长文章 ratio desc; ties broken by total count desc.
    series_stats.sort(key=lambda s: (-s["ratio"], -s["total"]))
    env.globals["has_series_index"] = bool(series_stats)

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
    # Home-page series ordering: explicit editorial sequence. Any series
    # not in this list falls to the tail in series_stats's existing order
    # (long-ratio desc) so a newly-added column doesn't silently vanish.
    HOME_SERIES_ORDER = [
        # Above-the-fold (the 11 series shown before the 查看所有栏目
        # toggle; combined with the always-pinned 只看长文章 link in the
        # template, this is the user-curated "top 12").
        "年度观察",
        "2017 清退",
        "好奇心商业史",
        "房子和我们的生活",
        "100 个有想法的人",
        "也许欧洲有答案",
        "好莱坞报告",
        "卫星新闻",
        "创始人说",
        "好奇心小数据",
        "好奇心辞典",
        # Rest — previous editorial order, with the above entries removed
        # so each series appears exactly once.
        "访谈录",
        "22 岁，他们在想什么",
        "这个社会，对年轻人太好了吗？",
        "市场发明家",
        "所长の大数据",
        "这个人有好奇心",
        "TED 现场报道",
        "上海时装周",
        "为什么读书",
        "42 区",
        "后视镜",
        "历史上的今天",
        "Hack Your Life",
        "好奇心研究所",
        "「日本語」",
        "大公司头条",
        "乙方日报",
        "浮华日报",
        "这个设计了不起",
        "「这世界」",
        "看图",
        "今日娱乐",
        "今日应用",
        "「万物简史」",
        "「票房」",
        "「本周新片」",
    ]
    by_name = {s["name"]: s for s in series_stats}
    home_series_stats = [by_name[k] for k in HOME_SERIES_ORDER if k in by_name]
    leftovers = [s for s in series_stats if s["name"] not in HOME_SERIES_ORDER]
    home_series_stats.extend(leftovers)
    # "最后 50 篇" excludes the 广告 category.
    home_latest = [r for r in rendered if r.get("category") != "广告"][:50]
    (out / "index.html").write_text(
        env.get_template("home.html").render(
            total=len(rendered),
            first_date=rendered[-1]["publish_date"],
            last_date=rendered[0]["publish_date"],
            latest=home_latest,
            years_with_counts=sorted(years_with_counts),
            long_total=len(long_articles),
            series_stats=home_series_stats,
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

    # Series pages
    if series_stats:
        series_dir = out / "series"
        series_dir.mkdir(parents=True, exist_ok=True)
        # /series/ — master index, ordered by 长文章 ratio desc
        (series_dir / "index.html").write_text(
            env.get_template("series_index.html").render(series_stats=series_stats),
            encoding="utf-8",
        )
        # /series/<name>/ — articles in this series, newest first
        for s in series_stats:
            name = s["name"]
            items = series_articles[name]
            items_sorted = sorted(items, key=lambda r: (r["publish_date"], r["id"]), reverse=True)
            n_long = s["long"]
            sd = series_dir / name
            sd.mkdir(parents=True, exist_ok=True)
            heading = f"{name} · {len(items_sorted)} 篇"
            (sd / "index.html").write_text(
                env.get_template("list.html").render(
                    heading=heading,
                    articles=items_sorted,
                    subnav=[("← 全部系列", url("series/"))],
                ),
                encoding="utf-8",
            )

    # Author pages — one per individual purely-CJK author, listing
    # every article they (co-)bylined newest-first.
    #
    # On-disk directory name is the RAW author name (UTF-8 bytes), NOT
    # the percent-encoded form. GitHub Pages URL-decodes the request
    # before lookup, so a request for `/author/%E9%BB%84%E4%BF%8A%E6
    # %9D%B0/` (黄俊杰) becomes a filesystem lookup for `黄俊杰` —
    # which only matches if the directory is literally named with the
    # CJK characters. An earlier version named the dir with the
    # percent-encoded slug, producing 1,715 directories that the live
    # site couldn't find.
    #
    # Non-CJK authors are skipped entirely: no page generated, no
    # hyperlink rendered (author_url() returns None for them).
    author_articles: dict[str, list] = defaultdict(list)
    for r in rendered:
        for a in r.get("authors") or []:
            if is_cjk_author(a):
                author_articles[a].append(r)
    author_dir = out / "author"
    author_dir.mkdir(parents=True, exist_ok=True)
    for name, items in author_articles.items():
        items_sorted = sorted(items, key=lambda r: (r["publish_date"], r["id"]),
                              reverse=True)
        ad = author_dir / name
        ad.mkdir(parents=True, exist_ok=True)
        heading = f"{name} · {len(items_sorted)} 篇"
        (ad / "index.html").write_text(
            env.get_template("list.html").render(
                heading=heading,
                articles=items_sorted,
                subnav=[],
            ),
            encoding="utf-8",
        )

    # Lightweight per-article index for the title/author search-scope tabs.
    # Pagefind handles full-text; for "title only" / "author only" we
    # client-side filter this small JSON.
    search_dir = out / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    articles_json = [
        {
            "id": r["id"],
            "title": r.get("title") or "",
            "author": r.get("author") or "",
            "date": r.get("publish_date") or "",
            "category": r.get("category") or "",
            "long": bool(r.get("is_long")),
        }
        for r in sorted(rendered, key=lambda r: (r["publish_date"], r["id"]), reverse=True)
    ]
    (search_dir / "articles.json").write_text(
        json.dumps(articles_json, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # Search page
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

    # CNAME — tells GitHub Pages to serve this artifact at the custom domain.
    # Derived from --site-url so it stays in sync with the canonical origin.
    # If the site_url isn't a custom domain (e.g. local preview pointing at
    # *.github.io), skip writing CNAME so GH Pages falls back to default.
    site_host = urlparse(args.site_url).hostname or ""
    if site_host and not site_host.endswith(".github.io"):
        (out / "CNAME").write_text(site_host + "\n")

    print(f"Rendered {len(rendered)} articles to {out}")
    print(f"  base_url={base_url}  image_mode={args.image_mode}")
    print(f"  screenshot-only stubs: {n_stubs_added}")
    print(f"  broken images (hidden via CSS): {total_broken}")
    print(f"  years: {', '.join(years)}")
    if series_stats:
        print(f"  series ({len(series_stats)}, sorted by 长文章 ratio):")
        for s in series_stats:
            print(f"    {s['name']:<14}  {s['total']:>5} ({s['long']:>3} long, {s['ratio']*100:>4.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
