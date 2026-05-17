"""
Recover missing QDaily article images from China Digital Times reposts.

CDT preserves the original QDaily filename in their re-hosted image URL,
e.g. https://chinadigitaltimes.net/chinese/files/2017/12/<qdaily_filename>.jpg
mirrors http://img.qdaily.com/uploads/<qdaily_filename> verbatim. That makes
alignment trivial — no aspect-ratio anchors needed — and recovery safe.

For one article:
    python tools/fetch_cdt.py --id 48092 --cdt-url https://chinadigitaltimes.net/chinese/574165.html

For a batch (mapping file with one "article_id  cdt_url" pair per line):
    python tools/fetch_cdt.py --batch data/cdt_matches.tsv

Outputs per article:
  * assets/<article_id>/<sha1_16>.<ext>  — downloaded images
  * data/images.jsonl                    — one new row per URL, source="cdt"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TIMEOUT = httpx.Timeout(45.0, connect=15.0)

# CDT's WordPress lazy-loads images; the post-content container wraps the body.
# These selectors are tried in order until one yields >0 images.
BODY_SELECTORS = [
    ".et_pb_post_content",
    ".entry-content",
    "article.post",
    ".post-content",
]

CDT_FILE_RE = re.compile(
    r"^https?://(?:www\.)?chinadigitaltimes\.net/chinese/files/\d{4}/\d{2}/(.+?)$",
    re.IGNORECASE,
)
QDAILY_UPLOADS_RE = re.compile(
    r"^https?://img\.qdaily\.com/uploads/(.+)$",
    re.IGNORECASE,
)


def cdt_basename_to_qdaily_filename(cdt_basename: str) -> str:
    """
    CDT stores qdaily files as "<qdaily_filename>.jpg" — the trailing .jpg is
    added by WordPress regardless of the original extension. Strip it (case-
    insensitive) only when the *penultimate* extension hints at an image, so
    we don't accidentally chop trailing chars off filenames like "foo.jpg"
    that *don't* have CDT's added suffix.
    """
    m = re.search(r"\.(?:jpe?g|png|gif|webp|JPG|JPEG|PNG|GIF|WEBP)-w\d+\.jpg$",
                  cdt_basename)
    if m:
        return cdt_basename[: -4]  # strip ".jpg"
    # Pattern without -wNNN suffix (rare): strip a trailing duplicate .jpg only
    # when there are two image-extension-like segments back-to-back.
    m = re.search(r"\.(?:jpe?g|png|gif|webp)\.jpg$", cdt_basename, re.I)
    if m:
        return cdt_basename[: -4]
    return cdt_basename


def extract_cdt_image_urls(html: str) -> list[str]:
    """Return CDT-hosted image URLs in document order (deduped while preserving order)."""
    soup = BeautifulSoup(html, "lxml")
    body = None
    for sel in BODY_SELECTORS:
        body = soup.select_one(sel)
        if body and body.find_all("img"):
            break
    if not body:
        body = soup
    out: list[str] = []
    seen: set[str] = set()
    for img in body.find_all("img"):
        # CDT loads via data-src (lazy) and src (eager); prefer data-src.
        u = (img.get("data-src") or img.get("src") or "").strip()
        if not u or u.startswith("data:"):
            continue
        m = CDT_FILE_RE.match(u)
        if not m:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def qdaily_filename(url: str) -> str | None:
    """For an http://img.qdaily.com/uploads/<filename> URL, return <filename>."""
    m = QDAILY_UPLOADS_RE.match(url)
    return m.group(1) if m else None


def build_match_map(qdaily_urls: list[str], cdt_urls: list[str]) -> dict[str, str]:
    """Return {qdaily_url: cdt_url} by exact filename match (case-insensitive)."""
    # Index CDT URLs by lower-cased qdaily filename (stripped of CDT's .jpg).
    cdt_by_fn: dict[str, str] = {}
    for cu in cdt_urls:
        m = CDT_FILE_RE.match(cu)
        if not m:
            continue
        basename = m.group(1)
        fn = cdt_basename_to_qdaily_filename(basename).lower()
        cdt_by_fn[fn] = cu

    result: dict[str, str] = {}
    for qu in qdaily_urls:
        fn = qdaily_filename(qu)
        if not fn:
            continue
        cu = cdt_by_fn.get(fn.lower())
        if cu:
            result[qu] = cu
    return result


def sha1_short(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def ext_for(qdaily_url: str) -> str:
    parsed = urlparse(qdaily_url)
    suffix = Path(parsed.path).suffix
    return suffix.lower() if suffix else ".bin"


def existing_keys(manifest_path: Path) -> set[str]:
    """URLs that already have a permanent status (ok or no-snapshot-prefix)."""
    keys: set[str] = set()
    if not manifest_path.exists():
        return keys
    for line in manifest_path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") == "ok":
            keys.add(rec["url"])
    return keys


def load_article(records_glob: str, article_id: int) -> dict | None:
    """Return the latest extracted record for article_id (extras override per-quarter)."""
    from glob import glob
    rec = None
    for p in sorted(glob(records_glob)):
        for line in Path(p).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("id") == article_id:
                rec = r  # last-wins
    return rec


def process_one(client: httpx.Client, article_id: int, cdt_url: str,
                manifest_path: Path, assets_root: Path,
                records_glob: str) -> dict:
    """Fetch CDT article, match images, download what matches. Returns stats."""
    rec = load_article(records_glob, article_id)
    if rec is None:
        return {"id": article_id, "status": "no-record", "ok": 0, "matched": 0, "qdaily_count": 0}

    qdaily_urls: list[str] = list(rec.get("images") or [])
    if rec.get("banner_image"):
        qdaily_urls.append(rec["banner_image"])
    # Dedup while preserving order
    qdaily_urls = list(dict.fromkeys(qdaily_urls))

    try:
        r = client.get(cdt_url)
        r.raise_for_status()
    except Exception as e:
        return {"id": article_id, "status": f"cdt-fetch-error: {type(e).__name__}",
                "ok": 0, "matched": 0, "qdaily_count": len(qdaily_urls)}

    cdt_imgs = extract_cdt_image_urls(r.text)
    matches = build_match_map(qdaily_urls, cdt_imgs)

    # Skip URLs we already have.
    have = existing_keys(manifest_path)
    todo = [(qu, cu) for qu, cu in matches.items() if qu not in have]

    out_dir = assets_root / str(article_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    new_rows: list[dict] = []
    ok = err = 0
    for qu, cu in todo:
        digest = sha1_short(qu)
        ext = ext_for(qu)
        out_path = out_dir / f"{digest}{ext}"
        try:
            img_r = client.get(cu)
            img_r.raise_for_status()
            out_path.write_bytes(img_r.content)
            new_rows.append({
                "url": qu,
                "status": "ok",
                "ts": None,
                "path": f"assets/{article_id}/{digest}{ext}",
                "length": len(img_r.content),
                "source": "cdt",
                "cdt_url": cu,
            })
            ok += 1
        except Exception as e:
            new_rows.append({
                "url": qu,
                "status": "cdt-fetch-error",
                "ts": None,
                "path": None,
                "length": None,
                "reason": f"{type(e).__name__}: {e}",
            })
            err += 1
        # Be polite to CDT.
        time.sleep(0.5)

    # Append all new rows atomically.
    if new_rows:
        with manifest_path.open("a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "id": article_id,
        "status": "ok",
        "qdaily_count": len(qdaily_urls),
        "cdt_count": len(cdt_imgs),
        "matched": len(matches),
        "downloaded": ok,
        "errors": err,
        "skipped_already_have": len(matches) - len(todo),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, help="single QDaily article id")
    ap.add_argument("--cdt-url", help="CDT article URL (with --id)")
    ap.add_argument("--batch", type=Path,
                    help="TSV file: each row 'article_id<TAB>cdt_url'")
    ap.add_argument("--manifest", type=Path, default=Path("data/images.jsonl"))
    ap.add_argument("--assets", type=Path, default=Path("assets"))
    ap.add_argument("--records-glob", default="data/articles_extracted_*.jsonl")
    args = ap.parse_args()

    pairs: list[tuple[int, str]] = []
    if args.id and args.cdt_url:
        pairs.append((args.id, args.cdt_url))
    elif args.batch:
        for line in args.batch.read_text(encoding="utf-8").split("\n"):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            try:
                pairs.append((int(parts[0]), parts[1].strip()))
            except ValueError:
                continue
    else:
        print("usage: --id N --cdt-url URL  OR  --batch FILE", file=sys.stderr)
        return 2

    client = httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    total = {"matched": 0, "downloaded": 0, "errors": 0}
    for aid, url in pairs:
        result = process_one(client, aid, url, args.manifest, args.assets, args.records_glob)
        line = (f"id={aid}  qdaily={result.get('qdaily_count','?')}  "
                f"cdt={result.get('cdt_count','?')}  matched={result.get('matched','?')}  "
                f"dl={result.get('downloaded','?')}  err={result.get('errors','?')}  "
                f"skip={result.get('skipped_already_have','?')}  "
                f"[{result.get('status')}]")
        print(line)
        total["matched"] += result.get("matched", 0) or 0
        total["downloaded"] += result.get("downloaded", 0) or 0
        total["errors"] += result.get("errors", 0) or 0

    if len(pairs) > 1:
        print(f"\nTOTAL: matched={total['matched']}  downloaded={total['downloaded']}  errors={total['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
