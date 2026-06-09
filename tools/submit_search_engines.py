"""
Push the archive's URLs to Chinese-relevant search engines for faster inclusion.

Baidu and Bing crawl overseas static sites slowly; these push APIs ask them to
fetch listed URLs directly instead of waiting to be discovered.

  * Baidu 链接提交 (link push): POST newline-joined URLs to data.zz.baidu.com.
    Needs your site token from ziyuan.baidu.com (site already verified there).
  * IndexNow (Bing + Yandex): POST a JSON urlList with a key hosted at
    https://www.qdaily.org/<key>.txt (see site/root/). No account needed.

Builds the URL list from the deduped corpus (every /articles/<id>/ plus the key
section pages). Dry-run by default — prints what it would send.

Usage:
    # preview
    python tools/submit_search_engines.py
    # Bing/Yandex via IndexNow (key auto-detected from site/root/)
    python tools/submit_search_engines.py --indexnow --send
    # Baidu (token from ziyuan.baidu.com)
    python tools/submit_search_engines.py --baidu-token XXXX --send
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import httpx

SITE = "https://www.qdaily.org"
HOST = "www.qdaily.org"


def all_urls() -> list[str]:
    """Canonical URLs: every article + the standing section pages."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_corpus import load_corpus  # reuse the exact dedup
    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    urls = [f"{SITE}/", f"{SITE}/long/", f"{SITE}/series/", f"{SITE}/search/"]
    years = sorted({(r.get("publish_date") or "")[:4] for r in recs if r.get("publish_date")})
    urls += [f"{SITE}/{y}/" for y in years if y]
    urls += [f"{SITE}/articles/{r['id']}/" for r in recs]
    return urls


def find_indexnow_key() -> str | None:
    for p in Path("site/root").glob("*.txt"):
        name = p.stem
        if len(name) >= 8 and all(c in "0123456789abcdef" for c in name.lower()):
            return name
    return None


def push_baidu(urls: list[str], token: str, send: bool) -> None:
    api = f"http://data.zz.baidu.com/urls?site={SITE}&token={token}"
    body = "\n".join(urls).encode("utf-8")
    print(f"[baidu] {len(urls):,} urls -> {api.split('token=')[0]}token=***")
    if not send:
        print("[baidu] dry-run (pass --send to submit)")
        return
    r = httpx.post(api, content=body,
                   headers={"Content-Type": "text/plain"}, timeout=60)
    print(f"[baidu] HTTP {r.status_code}: {r.text[:300]}")


def push_indexnow(urls: list[str], key: str, send: bool) -> None:
    endpoint = "https://api.indexnow.org/indexnow"
    key_loc = f"{SITE}/{key}.txt"
    # IndexNow accepts up to 10,000 URLs per request.
    batches = [urls[i:i + 10000] for i in range(0, len(urls), 10000)]
    print(f"[indexnow] {len(urls):,} urls in {len(batches)} batch(es); "
          f"key={key} keyLocation={key_loc}")
    if not send:
        print("[indexnow] dry-run (pass --send to submit)")
        return
    with httpx.Client(timeout=60) as c:
        for i, batch in enumerate(batches, 1):
            payload = {"host": HOST, "key": key, "keyLocation": key_loc,
                       "urlList": batch}
            r = c.post(endpoint, json=payload,
                       headers={"Content-Type": "application/json"})
            print(f"[indexnow] batch {i}: HTTP {r.status_code} {r.text[:120]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baidu-token", default="")
    ap.add_argument("--indexnow", action="store_true",
                    help="submit to Bing/Yandex via IndexNow")
    ap.add_argument("--indexnow-key", default="",
                    help="override; default = the hex *.txt key in site/root/")
    ap.add_argument("--send", action="store_true",
                    help="actually submit (otherwise dry-run)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    urls = all_urls()
    if args.limit:
        urls = urls[: args.limit]
    print(f"built {len(urls):,} canonical URLs\n")

    did = False
    if args.baidu_token:
        push_baidu(urls, args.baidu_token, args.send)
        did = True
    if args.indexnow:
        key = args.indexnow_key or find_indexnow_key()
        if not key:
            print("[indexnow] no key found in site/root/*.txt; skipping",
                  file=sys.stderr)
        else:
            push_indexnow(urls, key, args.send)
            did = True
    if not did:
        print("Nothing submitted. Pass --baidu-token and/or --indexnow "
              "(add --send to actually push).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
