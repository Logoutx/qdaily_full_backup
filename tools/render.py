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
import random
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
    if name == "NYT":
        # Licensed New York Times translation column. Membership is computed
        # from the article body (footer/photo-credit/byline) once during the
        # render loop and cached on the record as `_is_nyt`.
        return bool(r.get("_is_nyt"))
    if name == "Medium 授权":
        # Licensed Medium translation column ("本文由 Medium … 授权…发布").
        return bool(r.get("_is_medium"))
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
    # Licensed foreign-source translation columns (membership from body):
    "NYT",
    "Medium 授权",
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


# --- Foreign-source syndication detection (drives the NYT / Medium 授权 series) -
# QDaily ran two licensed-translation columns. Each translated piece carries a
# standard credit/footer we can match on:
#   * The New York Times  → footer "© <year> THE NEW YORK TIMES"; older 2014–15
#     pieces predate that footer but are still recognisable by the English
#     byline + a "… for The New York Times" photo credit + the 熊猫译社 team.
#   * Medium              → credit line "本文由 Medium … 授权《好奇心日报》发布".
# A handful of QDaily-original pieces merely *cite* the NYT in prose; those have
# CJK staff bylines and no footer/photo-credit, so they're correctly excluded.
_NYT_FOOTER_RE = re.compile(r"©\s*\d{0,4}\s*the new york times")
_NYT_IMG_CREDIT_RE = re.compile(r"(?:for|/)\s*the new york times")
_MEDIUM_CREDIT_RE = re.compile(r"由\s*Medium")

# In-article links to the defunct original site, e.g.
# https://www.qdaily.com/articles/52218.html — rewritten to the rebuilt site's
# own /articles/<id>/ URL. Matched anywhere in the href (re.search) so it also
# catches scheme-less hrefs (www.qdaily.com/...), sub-host forms
# (cms.qdaily.com/...), redirect wrappers, and hrefs with stray leading
# punctuation; the entire href is replaced, dropping any scheme/query/fragment.
# Note: requires the literal "qdaily.com/articles/" — so /display/articles/
# links (a different, non-rebuilt content type) are deliberately NOT matched.
_QDAILY_ARTICLE_LINK_RE = re.compile(
    r"(?:www\.|m\.)?qdaily\.com/articles/(\d+)\.html",
    re.I,
)


def detect_foreign_source(body_html: str, author: str) -> tuple[bool, bool]:
    """Return (is_nyt, is_medium) from a QDaily article's body + byline."""
    text = _plain_text(body_html)
    low = text.lower()
    is_medium = bool(_MEDIUM_CREDIT_RE.search(text)) and ("授权" in text)
    is_nyt = False
    if "the new york times" in low:
        if _NYT_FOOTER_RE.search(low):
            is_nyt = True
        else:
            author_cjk = bool(author) and any("一" <= c <= "鿿" for c in author)
            is_nyt = (
                ("熊猫译社" in text)
                or bool(_NYT_IMG_CREDIT_RE.search(low))
                or (not author_cjk)
            )
    return is_nyt, is_medium


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

# URLs that Wayback is known to have NO snapshot for (status no-snapshot-prefix
# in data/images.jsonl). Hot-linking web.archive.org for these only buys a
# doomed cross-origin request + latency before the onerror placeholder swap, so
# render routes them straight to the placeholder. Populated by main() via
# load_dead_image_urls(); empty by default so unit tests / ad-hoc calls behave.
DEAD_IMAGE_URLS: set[str] = set()


def wayback_im(orig: str, ts: str) -> str:
    return f"https://web.archive.org/web/{ts}im_/{orig}"


def load_dead_image_urls(manifest: Path) -> set[str]:
    """URLs whose LATEST fetch status is no-snapshot-prefix (Wayback has no
    archived copy). A URL recovered later (status ok) is excluded, so this only
    ever contains genuinely-unservable images."""
    if not manifest.exists():
        return set()
    latest: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        u = rec.get("url")
        if u:
            latest[u] = rec.get("status")
    return {u for u, s in latest.items() if s == "no-snapshot-prefix"}


def is_broken_host(url: str) -> bool:
    try:
        return urlparse(url).netloc in BROKEN_HOSTS
    except Exception:
        return False


def resolve_url(orig: str, ts: str, mode: str, article_id: int, assets_root: Path,
                asset_base_url: str = "") -> tuple[str | None, bool]:
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
            if asset_base_url:
                # Absolute CDN URL — assets/ is NOT copied into public/.
                # asset_base_url is normalised to no-trailing-slash by main().
                return f"{asset_base_url}/{article_id}/{digest}{ext}", broken
            # rel path from public/articles/<id>/index.html → public/assets/<id>/file
            return f"../../assets/{article_id}/{digest}{ext}", broken
    # No local asset. If Wayback is known to have no snapshot, don't emit a
    # doomed web.archive.org URL — mark broken so the caller uses the
    # placeholder immediately (no failed cross-origin request, no latency).
    if orig in DEAD_IMAGE_URLS:
        return orig, True
    if not ts:
        # No Wayback timestamp — this is an externally-sourced article
        # (e.g. Medium) whose original images are still live. Pass through.
        return orig, broken
    return wayback_im(orig, ts), broken


def mp4_for(orig: str, article_id: int, mode: str, assets_root: Path,
            asset_base_url: str = "") -> str | None:
    """If a GIF/animated-WebP was converted to MP4 (tools/convert_*), return the
    MP4's URL so the caller can emit <video> instead of <img>. Keyed by the same
    sha1(url) digest, so it's found regardless of the original odd extension."""
    if mode != "local" or not orig:
        return None
    digest = hashlib.sha1(orig.encode("utf-8")).hexdigest()[:16]
    if not (assets_root / str(article_id) / f"{digest}.mp4").exists():
        return None
    if asset_base_url:
        return f"{asset_base_url}/{article_id}/{digest}.mp4"
    return f"../../assets/{article_id}/{digest}.mp4"


def webp_for(orig: str, article_id: int, mode: str, assets_root: Path,
             asset_base_url: str = "") -> str | None:
    """URL of the WebP variant (assets/<id>/<digest>.webp) if it exists, for use
    as a <picture><source type="image/webp">. Same sha1(url) digest keying as the
    base asset, so it's found regardless of the base's odd extension."""
    if mode != "local" or not orig:
        return None
    digest = hashlib.sha1(orig.encode("utf-8")).hexdigest()[:16]
    if not (assets_root / str(article_id) / f"{digest}.webp").exists():
        return None
    if asset_base_url:
        return f"{asset_base_url}/{article_id}/{digest}.webp"
    return f"../../assets/{article_id}/{digest}.webp"


def resolve_picture(orig: str, ts: str, mode: str, article_id: int,
                    assets_root: Path, asset_base_url: str = "") -> dict:
    """Resolve one image to a responsive <picture> spec:
        {src, webp, wb, broken}
    where `src` is the primary (cdn.qdaily.org when mirrored, else the Wayback
    im_ URL when not), `webp` is the WebP <source> URL or None, and `wb` is the
    Wayback fallback used by the onerror chain — set ONLY for mirrored assets, so
    a CDN/R2 miss falls back to Wayback instead of the primary already being
    Wayback. Broken/known-dead images return broken=True (caller -> placeholder).
    """
    src, broken = resolve_url(orig, ts, mode, article_id, assets_root, asset_base_url)
    if not src or broken:
        return {"src": src, "webp": None, "wb": None, "broken": bool(broken)}
    # Is `src` our own mirrored asset (vs a Wayback im_ URL or a live external)?
    on_disk = False
    if mode == "local":
        try:
            ext = Path(urlparse(orig).path).suffix.lower() or ".bin"
        except ValueError:
            ext = ".bin"
        digest = hashlib.sha1(orig.encode("utf-8")).hexdigest()[:16]
        on_disk = (assets_root / str(article_id) / f"{digest}{ext}").exists()
    if on_disk:
        return {
            "src": src,
            "webp": webp_for(orig, article_id, mode, assets_root, asset_base_url),
            "wb": wayback_im(orig, ts) if ts else None,
            "broken": False,
        }
    # Not mirrored: `src` is already Wayback (or a live external) — it IS the
    # fallback; onerror goes straight to the placeholder.
    return {"src": src, "webp": None, "wb": None, "broken": False}


def _add_class(img, cls: str) -> None:
    """Append a CSS class to a bs4 <img> (class attr is stored as a list)."""
    existing = img.get("class") or []
    if isinstance(existing, str):
        existing = existing.split()
    if cls not in existing:
        existing.append(cls)
    img["class"] = existing


def load_image_alts(path: Path) -> dict:
    """Map original image URL -> LLM-generated alt text.

    Populated by tools/gen_image_alts.py (vision pass over the on-disk asset
    files). Keyed by the ORIGINAL image URL exactly as it appears in the
    article records, so render lookups need no path math. Missing file -> {}.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # Accept either {url: alt} or {url: {"alt": ...}} shapes.
    out = {}
    for k, v in data.items():
        alt = v.get("alt") if isinstance(v, dict) else v
        if alt:
            out[k] = alt.strip()
    return out


def resolve_body(body_html: str, ts: str, article_id: int, mode: str, assets_root: Path,
                 asset_base_url: str = "", placeholder_url: str = "",
                 base_url: str = "/", alt_map: dict | None = None) -> tuple[str, int]:
    """Rewrite <img src> in body_html. Returns (rewritten_html, broken_count).

    Missing images fall back to the skyline placeholder two ways:
      * known-broken hosts -> src is set to the placeholder up-front (no
        doomed network request), tagged .img-missing.
      * everything else -> an onerror handler swaps to the placeholder if
        the image 404s at load time (e.g. a Wayback snapshot that vanished).
    """
    if not body_html:
        return "", 0
    soup = BeautifulSoup(body_html, "lxml")
    # decode_contents on the parsed soup unwraps the auto-added <html><body>
    broken_count = 0
    onerror_js = (
        f"this.onerror=null;this.src='{placeholder_url}';"
        "this.classList.add('img-missing')"
    ) if placeholder_url else ""
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            img.decompose()
            continue
        # GIF / animated-WebP converted to MP4 -> emit an autoplaying muted
        # loop <video> (visually identical to the GIF, a fraction of the size).
        mp4 = mp4_for(src, article_id, mode, assets_root, asset_base_url)
        if mp4:
            video = soup.new_tag("video")
            video["src"] = mp4
            for attr in ("autoplay", "loop", "muted", "playsinline"):
                video[attr] = ""
            video["preload"] = "metadata"
            _add_class(video, "body-video")
            img.replace_with(video)
            continue
        pic = resolve_picture(src, ts, mode, article_id, assets_root, asset_base_url)
        if pic["broken"] and placeholder_url:
            # Known dead — point straight at the placeholder.
            img["src"] = placeholder_url
            _add_class(img, "img-missing")
            broken_count += 1
        else:
            img["src"] = pic["src"] or src
            if pic["broken"]:
                img["data-broken"] = "1"
                broken_count += 1
            else:
                # CDN -> Wayback (if mirrored) -> placeholder, via the qdImg JS.
                if pic["wb"]:
                    img["data-wb"] = pic["wb"]
                img["onerror"] = "qdImg(this)"
                # LLM alt (keyed by original src); overrides any empty original.
                if alt_map:
                    alt = alt_map.get(src)
                    if alt:
                        img["alt"] = alt
                # Wrap in <picture> with a WebP <source> when a variant exists.
                if pic["webp"]:
                    picture = soup.new_tag("picture")
                    source = soup.new_tag("source")
                    source["type"] = "image/webp"
                    source["srcset"] = pic["webp"]
                    img.replace_with(picture)
                    picture.append(source)
                    picture.append(img)
        if "loading" not in img.attrs:
            img["loading"] = "lazy"
    # Rewrite links to the defunct original site back into the archive:
    # https://www.qdaily.com/articles/<id>.html -> {base_url}articles/<id>/
    for a in soup.find_all("a", href=True):
        m = _QDAILY_ARTICLE_LINK_RE.search(a["href"])
        if m:
            a["href"] = f"{base_url}articles/{m.group(1)}/"
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
    ap.add_argument("--asset-base-url", default="",
                    help="If set (e.g. https://cdn.qdaily.org), emit absolute CDN "
                         "URLs for local-mode images and DO NOT copy assets/ into "
                         "public/. Used in CI to keep the Pages artifact small.")
    ap.add_argument("--base-url", default="/", help="URL prefix; '/' for the live site (qdaily.org) and local preview")
    ap.add_argument("--site-url", default="https://www.qdaily.org", help="absolute origin for RSS / canonical URLs")
    ap.add_argument("--site-title", default="QDaily 好奇心日报存档")
    ap.add_argument("--site-description", default="好奇心日报所刊发内容存档，通过 Internet Archive 重建。")
    ap.add_argument("--image-alts", default="data/image_alts.json",
                    help="JSON map of original-image-URL -> alt text "
                         "(LLM-generated by tools/gen_image_alts.py)")
    ap.add_argument("--image-manifest", default="data/images.jsonl",
                    help="fetch manifest; URLs marked no-snapshot-prefix are "
                         "routed to the placeholder instead of web.archive.org")
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

    # Passthrough files served at the SITE ROOT (public/<rel>): search-engine
    # ownership-verification files (Baidu/Bing/Sogou/360/Shenma), the IndexNow
    # key, and /.well-known/ files (e.g. security.txt). Drop a file into
    # site/root/ and it lands at /<rel>, subdirectories preserved.
    root_src = Path("site/root")
    if root_src.exists():
        for f in root_src.rglob("*"):
            if f.is_file() and f.suffix.lower() != ".md":
                dest = out / f.relative_to(root_src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    # In local image mode, copy mirrored Wayback assets so the
    # `../../assets/<id>/<digest>.<ext>` paths emitted by resolve_url()
    # resolve under public/assets/. We use copy (rather than symlink)
    # because GitHub Pages serves the artifact verbatim.
    # Skipped when --asset-base-url is set: in that case resolve_url() emits
    # absolute CDN URLs (e.g. https://cdn.qdaily.org/...) and the assets are
    # uploaded out-of-band to R2 by tools/upload_assets_r2.py.
    assets_src = Path(args.assets)
    asset_base_url = args.asset_base_url.rstrip("/")
    if args.image_mode == "local" and assets_src.exists() and not asset_base_url:
        shutil.copytree(assets_src, out / "assets")

    base_url = args.base_url if args.base_url.endswith("/") else args.base_url + "/"

    def url(path: str) -> str:
        if path.startswith("/"):
            path = path[1:]
        return base_url + path

    # Absolute canonical origin (no trailing slash), e.g. https://www.qdaily.org.
    # Used for <link rel="canonical">, Open Graph og:url, JSON-LD @id, and the
    # XML sitemap — search engines and AI crawlers need fully-qualified URLs.
    site_url_base = args.site_url.rstrip("/")

    def canon(path: str) -> str:
        """Absolute URL for a site-relative path (e.g. 'articles/31/')."""
        p = path[1:] if path.startswith("/") else path
        return f"{site_url_base}/{p}" if p else f"{site_url_base}/"

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

    # Cache-buster: short content hash for files under site/static. Templates
    # call `static_v('style.css')` and append `?v={{ ... }}` to the asset URL,
    # so any change to the file invalidates browser caches automatically.
    _static_v_cache: dict[str, str] = {}
    def static_v(name: str) -> str:
        if name in _static_v_cache:
            return _static_v_cache[name]
        p = Path(args.static) / name
        v = hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else ""
        _static_v_cache[name] = v
        return v

    env.globals.update(
        site_title=args.site_title,
        site_description=args.site_description,
        site_url=site_url_base,
        url=url,
        canon=canon,
        author_url=author_url,
        static_v=static_v,
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
    # listing entry, no search-index inclusion). The base set is literal;
    # bulk exclusions (e.g. empty placeholder articles) live in
    # data/excluded_ids.txt — one id per line, '#' starts a comment.
    EXCLUDED_IDS = {64091}
    _excl_file = Path("data/excluded_ids.txt")
    if _excl_file.exists():
        for _ln in _excl_file.read_text(encoding="utf-8").split("\n"):
            _ln = _ln.split("#", 1)[0].strip()
            if _ln.isdigit():
                EXCLUDED_IDS.add(int(_ln))

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
    global DEAD_IMAGE_URLS
    DEAD_IMAGE_URLS = load_dead_image_urls(Path(args.image_manifest))
    if DEAD_IMAGE_URLS:
        print(f"loaded {len(DEAD_IMAGE_URLS):,} known-no-snapshot image URLs "
              f"-> placeholder (no Wayback request)")
    alt_map = load_image_alts(Path(args.image_alts))
    if alt_map:
        print(f"loaded {len(alt_map):,} LLM image alt texts from {args.image_alts}")
    rendered = []
    total_broken = 0
    placeholder_url = url("static/placeholder.webp")
    for r in records:
        body_html, broken_in_body = resolve_body(
            r.get("body_html") or "", r["archive_ts"], r["id"], args.image_mode, assets_root,
            asset_base_url, placeholder_url, base_url, alt_map,
        )
        banner = r.get("banner_image")
        banner_resolved, banner_broken = (None, False)
        banner_webp = banner_wb = None
        if banner:
            _bp = resolve_picture(
                banner, r["archive_ts"], args.image_mode, r["id"], assets_root, asset_base_url,
            )
            banner_resolved, banner_broken = _bp["src"], _bp["broken"]
            banner_webp, banner_wb = _bp["webp"], _bp["wb"]
        # Tile fallback: when there's no usable banner, promote the first
        # non-broken inline image to act as the tile thumbnail on list pages.
        # Kept separate from banner_image_resolved so the article page itself
        # doesn't gain a duplicate lead image.
        tile_banner_resolved = banner_resolved if (banner_resolved and not banner_broken) else None
        tile_src = banner if tile_banner_resolved else None
        if not tile_banner_resolved:
            for img_url in (r.get("images") or []):
                # Skip animations (GIF/WebP -> MP4) — they can't be a still tile.
                if mp4_for(img_url, r["id"], args.image_mode, assets_root, asset_base_url):
                    continue
                fb_url, fb_broken = resolve_url(
                    img_url, r["archive_ts"], args.image_mode, r["id"], assets_root, asset_base_url,
                )
                if fb_url and not fb_broken:
                    tile_banner_resolved = fb_url
                    tile_src = img_url
                    break
        # WebP/Wayback-fallback spec for the tile's chosen image.
        tile_webp = tile_wb = None
        if tile_src:
            _tp = resolve_picture(
                tile_src, r["archive_ts"], args.image_mode, r["id"], assets_root, asset_base_url,
            )
            tile_webp, tile_wb = _tp["webp"], _tp["wb"]
        # LLM alt for the lead image (banner on the article page; same photo on
        # the card). Skipped automatically for broken images (no resolved src).
        banner_alt = alt_map.get(banner, "") if (banner and not banner_broken) else ""
        tile_alt = alt_map.get(tile_src, "") if tile_src else ""
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

        # Foreign-source column membership (NYT / Medium 授权), from the raw body.
        is_nyt, is_medium = detect_foreign_source(r.get("body_html") or "", r.get("author") or "")

        rendered.append({
            **r,
            "_is_nyt": is_nyt,
            "_is_medium": is_medium,
            "body_html_resolved": body_html,
            "banner_image_resolved": banner_resolved,
            "banner_broken": banner_broken,
            "banner_alt": banner_alt,
            "banner_webp": banner_webp,
            "banner_wb": banner_wb,
            "tile_banner_resolved": tile_banner_resolved,
            "tile_alt": tile_alt,
            "tile_webp": tile_webp,
            "tile_wb": tile_wb,
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

    # Per-article SEO metadata: ISO-8601 publish time (Beijing, +08:00),
    # a social/OG image (best available banner, else the site logo), and a
    # schema.org NewsArticle JSON-LD blob. Done here because it needs the
    # split authors + is_long tag computed above.
    logo_abs = f"{site_url_base}/static/qdaily.png"
    for r in rendered:
        pt = r.get("publish_time") or ""
        if len(pt) >= 19:
            iso = pt[:10] + "T" + pt[11:19] + "+08:00"
        else:
            iso = r["publish_date"] + "T00:00:00+08:00"
        r["iso_published"] = iso
        og_image = r.get("tile_banner_resolved") or logo_abs
        r["og_image"] = og_image
        canonical = canon(f"articles/{r['id']}/")
        authors = r.get("authors") or []
        if authors:
            author_ld = [{"@type": "Person", "name": a} for a in authors]
        else:
            author_ld = {"@type": "Organization", "name": "好奇心日报"}
        jsonld = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": r["title"][:110],
            "datePublished": iso,
            "dateModified": iso,
            "author": author_ld,
            "publisher": {
                "@type": "Organization",
                "name": "好奇心日报存档",
                "logo": {"@type": "ImageObject", "url": logo_abs},
            },
            "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
            "inLanguage": "zh-Hans",
            "description": r.get("excerpt") or r["title"],
        }
        if r.get("tile_banner_resolved"):
            jsonld["image"] = [r["tile_banner_resolved"]]
        if r.get("category"):
            jsonld["articleSection"] = r["category"]
        # Escape `</` so the blob can't break out of the <script> element.
        r["jsonld"] = json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")

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
    env.globals["has_team_index"] = Path("data/team_members.json").exists()

    # Article pages
    for r in rendered:
        page_dir = out / "articles" / str(r["id"])
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            env.get_template("article.html").render(
                article=r,
                canonical=canon(f"articles/{r['id']}/"),
                og_image=r["og_image"],
                og_type="article",
            ),
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
        "NYT",
        "Medium 授权",
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
    # Today's Pick — optional daily curation surfaced on the home page. Driven
    # by data/daily_picks.json ({date, title, picks:[{id,...}]}); the picks are
    # existing archive articles, so they already carry every tile field. No-op
    # when the file is absent.
    todays_pick = None
    picks_path = Path("data/daily_picks.json")
    if picks_path.exists():
        dp = json.loads(picks_path.read_text(encoding="utf-8"))
        rid = {r["id"]: r for r in rendered}
        pick_articles = [rid[p["id"]] for p in dp.get("picks", []) if p.get("id") in rid]
        if pick_articles:
            # Randomize display order (date-seeded → stable per day, reshuffled
            # each new day) so the cn-trend / global-trend / longform buckets
            # aren't visually grouped. The data file stays grouped for attribution.
            random.Random(dp.get("date", "")).shuffle(pick_articles)
            todays_pick = {"date": dp.get("date", ""), "title": dp.get("title", ""),
                           "articles": pick_articles}
    (out / "index.html").write_text(
        env.get_template("home.html").render(
            total=len(rendered),
            first_date=rendered[-1]["publish_date"],
            last_date=rendered[0]["publish_date"],
            latest=home_latest,
            todays_pick=todays_pick,
            years_with_counts=sorted(years_with_counts),
            long_total=len(long_articles),
            series_stats=home_series_stats,
            canonical=canon(""),
        ),
        encoding="utf-8",
    )

    # Permanent dated digest page for the day's picks — the honestly-dated,
    # indexable artifact (auto-picked up by the sitemap walk as an index.html;
    # its <lastmod> is set to today below, not the archive's 2019 date).
    if todays_pick:
        ddir = out / "daily" / todays_pick["date"]
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "index.html").write_text(
            env.get_template("daily.html").render(
                pick=todays_pick,
                canonical=canon(f"daily/{todays_pick['date']}/"),
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
                canonical=canon(f"{y}/"),
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
                    canonical=canon(f"{y}/{m}/"),
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
                canonical=canon("long/"),
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
                    canonical=canon(f"long/{y}/"),
                ),
                encoding="utf-8",
            )

    # Series pages
    if series_stats:
        series_dir = out / "series"
        series_dir.mkdir(parents=True, exist_ok=True)
        # /series/ — master index, ordered by 长文章 ratio desc
        (series_dir / "index.html").write_text(
            env.get_template("series_index.html").render(
                series_stats=series_stats, canonical=canon("series/"),
            ),
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
                    canonical=canon("series/" + quote(name, safe="") + "/"),
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
                canonical=canon("author/" + quote(name, safe="") + "/"),
            ),
            encoding="utf-8",
        )

    # Team / authors page — driven by data/team_members.json (built offline by
    # tools/team/*; tenure derived from monthly aboutus.html snapshots + article
    # history). Current members first (in-job at 2019-05 shutdown), historical
    # behind a toggle; both ranked by tenure length.
    team_path = Path("data/team_members.json")
    if team_path.exists():
        team = json.loads(team_path.read_text(encoding="utf-8"))
        current = [m for m in team if m.get("is_current")]
        historical = [m for m in team if not m.get("is_current")]
        team_dir = out / "team"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "index.html").write_text(
            env.get_template("team.html").render(
                current=current, historical=historical,
                all_count=len(team), canonical=canon("team/"),
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
        env.get_template("search.html").render(
            total=len(rendered), canonical=canon("search/"),
        ),
        encoding="utf-8",
    )

    # RSS feed (latest 200)
    (out / "feed.xml").write_text(
        env.get_template("feed.xml").render(
            latest=rendered[:200],
            digest=todays_pick,
            build_date=email.utils.format_datetime(datetime.utcnow()),
        ),
        encoding="utf-8",
    )

    # ---- SEO: XML sitemap (chunked + index), robots.txt, llms.txt ----------
    # Walk every generated index.html and map it back to its absolute URL.
    # Article pages carry their publish_date as <lastmod>; navigation pages
    # use the newest article date. Non-ASCII path segments (series/author
    # names) are percent-encoded so the <loc> is a valid URL.
    def _xml_escape(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))

    id_to_date = {r["id"]: r["publish_date"] for r in rendered}
    newest_date = rendered[0]["publish_date"] if rendered else "2019-05-27"
    sitemap_entries: list[tuple[str, str]] = []
    for idx_file in sorted(out.rglob("index.html")):
        rel_dir = idx_file.parent.relative_to(out)
        rel = "" if str(rel_dir) == "." else rel_dir.as_posix() + "/"
        segs = [s for s in rel.split("/") if s]
        lastmod = newest_date
        if len(segs) == 2 and segs[0] == "articles" and segs[1].isdigit():
            lastmod = id_to_date.get(int(segs[1]), newest_date)
        elif len(segs) == 2 and segs[0] == "daily":
            lastmod = segs[1]  # /daily/<YYYY-MM-DD>/ — the digest IS fresh today
        loc = site_url_base + "/" + quote(rel, safe="/")
        sitemap_entries.append((loc, lastmod))

    # Processing instruction that makes browsers render the XML as a table
    # (see site/static/sitemap.xsl). Crawlers ignore it.
    xsl_pi = f'<?xml-stylesheet type="text/xsl" href="{url("static/sitemap.xsl")}"?>\n'

    # 10k URLs/chunk (~0.9 MB each). Google's hard limit is 50k/50 MB, but
    # smaller files fetch far more reliably — a 45k/4.1 MB chunk intermittently
    # returned "Couldn't fetch" in Search Console while a 10k chunk always
    # succeeded. Smaller chunks also render faster in the human XSL view.
    CHUNK = 10000
    chunks = [sitemap_entries[i:i + CHUNK] for i in range(0, len(sitemap_entries), CHUNK)] or [[]]
    for ci, chunk in enumerate(chunks, 1):
        rows = "\n".join(
            f"  <url><loc>{_xml_escape(loc)}</loc><lastmod>{lm}</lastmod></url>"
            for loc, lm in chunk
        )
        (out / f"sitemap-{ci}.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            + xsl_pi +
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{rows}\n</urlset>\n",
            encoding="utf-8",
        )
    index_rows = "\n".join(
        f"  <sitemap><loc>{site_url_base}/sitemap-{ci}.xml</loc>"
        f"<lastmod>{newest_date}</lastmod></sitemap>"
        for ci in range(1, len(chunks) + 1)
    )
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + xsl_pi +
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{index_rows}\n</sitemapindex>\n",
        encoding="utf-8",
    )

    # Flat single-file sitemap for Baidu: 百度 rejects index-type sitemaps, and
    # throttles non-备案 (non-ICP) sites to a single stored sitemap file. So emit
    # one <urlset> capped at Baidu's per-file limit (50,000 URLs / <10 MB).
    # Submit https://www.qdaily.org/sitemap-flat.xml in 普通收录 → sitemap.
    BAIDU_MAX = 50000
    flat = sitemap_entries[:BAIDU_MAX]
    flat_rows = "\n".join(
        f"  <url><loc>{_xml_escape(loc)}</loc><lastmod>{lm}</lastmod></url>"
        for loc, lm in flat
    )
    (out / "sitemap-flat.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + xsl_pi +
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{flat_rows}\n</urlset>\n",
        encoding="utf-8",
    )
    if len(sitemap_entries) > BAIDU_MAX:
        print(f"sitemap-flat.xml: capped at {BAIDU_MAX:,}/{len(sitemap_entries):,} "
              f"URLs (Baidu per-file limit)")

    # robots.txt — allow everyone, including AI crawlers, and point to the
    # sitemap. This archive *wants* to be indexed and cited.
    ai_agents = [
        "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-Web",
        "anthropic-ai", "PerplexityBot", "Perplexity-User", "Google-Extended",
        "CCBot", "Applebot-Extended", "Bytespider", "Amazonbot", "cohere-ai",
        "Meta-ExternalAgent", "DuckAssistBot", "YouBot", "Diffbot",
    ]
    # Chinese search engines (Baidu/Sogou/360/Shenma) + Bing. Covered by '*'
    # already, but named explicitly so intent is unambiguous to those crawlers.
    cn_agents = [
        "Baiduspider", "Sogou web spider", "360Spider", "HaosouSpider",
        "YisouSpider", "Bingbot", "Sosospider",
    ]
    robots_lines = ["User-agent: *", "Allow: /", ""]
    for a in ai_agents + cn_agents:
        robots_lines += [f"User-agent: {a}", "Allow: /", ""]
    robots_lines += [f"Sitemap: {site_url_base}/sitemap.xml", ""]
    (out / "robots.txt").write_text("\n".join(robots_lines), encoding="utf-8")

    # llms.txt — emerging convention giving AI agents a concise, linkable
    # map of the site. https://llmstxt.org/
    n_articles = len(rendered)
    llms = f"""# 好奇心日报存档 (QDaily Archive)

> {args.site_description} {n_articles:,} 篇文章，覆盖 {rendered[-1]['publish_date'] if rendered else ''} 至 {newest_date}。原站 qdaily.com（好奇心日报）已于 2019 年停运，本站是基于 Internet Archive 等渠道重建的完整存档，由黄俊杰维护。

## Browse

- [首页 / 最新文章]({site_url_base}/): the latest 50 pieces
- [长文章]({site_url_base}/long/): QDaily's original long-form features
- [系列]({site_url_base}/series/): editorial columns and series
- [搜索]({site_url_base}/search/): full-text search
- [RSS]({site_url_base}/feed.xml)
- [Sitemap]({site_url_base}/sitemap.xml): all {n_articles:,} article URLs

## By year

{chr(10).join(f"- [{y}]({site_url_base}/{y}/)" for y in years)}

## Notes

- Article URLs mirror the original: {site_url_base}/articles/<id>/
- Content is in Simplified Chinese (zh-Hans).
- Each article page carries schema.org NewsArticle metadata.
"""
    (out / "llms.txt").write_text(llms, encoding="utf-8")

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
    print(f"  base_url={base_url}  image_mode={args.image_mode}"
          + (f"  asset_base_url={asset_base_url}" if asset_base_url else ""))
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
