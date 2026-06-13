"""
Submit qdaily.org URLs to the Internet Archive Wayback Machine (Save Page Now)
so the reconstruction is preserved/redundant in the public web archive.

Two modes:
  * anonymous (default): GET https://web.archive.org/save/<url>. No account, but
    heavily rate-limited by archive.org — fine for the ~60 key/navigation pages.
  * authenticated (--s3 or WAYBACK_S3 env): POST to the SPN2 endpoint with
    archive.org S3 keys (create a free account, then grab keys at
    https://archive.org/account/s3.php). Steadier throughput — needed for the
    full ~55k-article corpus.

Resumable: every URL we successfully hand to SPN is appended to
data/wayback_submitted.txt; reruns skip those. Gentle by default (sleeps between
requests). Dry-run unless --send.

Note: web.archive.org is GFW-blocked in mainland China, so this is for
preservation/redundancy, NOT China reachability.

Usage:
  # key navigation pages, anonymous, now:
  python tools/wayback_submit.py --scope key --send
  # full corpus, authenticated, gentle background (provide your S3 keys):
  WAYBACK_S3=ACCESSKEY:SECRET python tools/wayback_submit.py --scope all --send
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

SITE = "https://www.qdaily.org"
DONE_FILE = Path("data/wayback_submitted.txt")


def key_urls() -> list[str]:
    """Standing navigation pages: home, sections, every year, team."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_corpus import load_corpus  # reuse the exact dedup
    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    urls = [f"{SITE}/", f"{SITE}/long/", f"{SITE}/series/",
            f"{SITE}/search/", f"{SITE}/team/"]
    years = sorted({(r.get("publish_date") or "")[:4] for r in recs if r.get("publish_date")})
    urls += [f"{SITE}/{y}/" for y in years if y]
    return urls


def article_urls() -> list[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_corpus import load_corpus
    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    return [f"{SITE}/articles/{r['id']}/" for r in recs]


def load_done() -> set[str]:
    if DONE_FILE.exists():
        return set(DONE_FILE.read_text(encoding="utf-8").split())
    return set()


def mark_done(url: str) -> None:
    DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DONE_FILE.open("a", encoding="utf-8") as f:
        f.write(url + "\n")


def submit_anon(client: httpx.Client, url: str) -> tuple[bool, str]:
    """Anonymous Save Page Now. 200/redirect => accepted; 429 => throttled."""
    r = client.get(f"https://web.archive.org/save/{url}",
                   follow_redirects=False)
    if r.status_code in (200, 301, 302):
        return True, f"HTTP {r.status_code}"
    return False, f"HTTP {r.status_code}: {r.text[:120]}"


def submit_s3(client: httpx.Client, url: str, s3: str) -> tuple[bool, str]:
    """Authenticated SPN2. Returns job_id on success."""
    r = client.post(
        "https://web.archive.org/save",
        headers={"Accept": "application/json",
                 "Authorization": f"LOW {s3}"},
        data={"url": url, "capture_outlinks": "0", "skip_first_archive": "1"},
    )
    try:
        j = r.json()
    except Exception:
        return r.status_code == 200, f"HTTP {r.status_code}: {r.text[:120]}"
    if "job_id" in j:
        return True, f"job {j['job_id']}"
    return False, f"HTTP {r.status_code}: {str(j)[:160]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["key", "all"], default="key",
                    help="key = nav pages only; all = key pages + every article")
    ap.add_argument("--s3", default=os.environ.get("WAYBACK_S3", ""),
                    help="archive.org S3 'accesskey:secret' (or WAYBACK_S3 env). "
                         "Omit for anonymous mode.")
    ap.add_argument("--rate", type=float, default=0.0,
                    help="seconds to sleep between requests "
                         "(default: 8 anon / 10 authenticated)")
    ap.add_argument("--backoff", type=float, default=30.0,
                    help="seconds to wait when SPN2 session limit is hit")
    ap.add_argument("--max-retries", type=int, default=10,
                    help="how many times to wait-and-retry one url on session limit")
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="stop this run after N seconds of wall-clock (0 = no cap); "
                         "resume continues where it left off")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--send", action="store_true",
                    help="actually submit (otherwise dry-run)")
    args = ap.parse_args()

    urls = key_urls() if args.scope == "key" else key_urls() + article_urls()
    done = load_done()
    todo = [u for u in urls if u not in done]
    if args.limit:
        todo = todo[: args.limit]

    mode = "authenticated SPN2" if args.s3 else "anonymous SPN"
    rate = args.rate or (10.0 if args.s3 else 8.0)
    print(f"scope={args.scope} mode={mode} rate={rate}s "
          f"backoff={args.backoff}s max_retries={args.max_retries}")
    print(f"{len(urls):,} canonical URLs, {len(done):,} already submitted, "
          f"{len(todo):,} to go\n")
    if not args.send:
        print("dry-run (pass --send to submit). First few:")
        for u in todo[:8]:
            print(f"  {u}")
        return 0
    if args.scope == "all" and not args.s3:
        print("WARNING: full corpus over anonymous SPN will be throttled hard. "
              "Provide --s3 / WAYBACK_S3 for the ~55k run.\n")

    ok = fail = 0
    start = time.monotonic()
    with httpx.Client(timeout=90) as c:
        for i, url in enumerate(todo, 1):
            if args.max_seconds and time.monotonic() - start > args.max_seconds:
                print(f"\n[time budget {args.max_seconds:.0f}s reached — "
                      f"stopping at {i-1}/{len(todo)}; rerun to resume]")
                break
            # SPN2 caps how many of our captures may be in flight at once
            # ("error:user-session-limit"). When we hit it, the slot just needs
            # time to drain — wait and retry the SAME url rather than skipping.
            for attempt in range(1, args.max_retries + 2):
                try:
                    if args.s3:
                        good, msg = submit_s3(c, url, args.s3)
                    else:
                        good, msg = submit_anon(c, url)
                except httpx.HTTPError as e:
                    good, msg = False, f"{type(e).__name__}: {e}"
                if good or "user-session-limit" not in msg or attempt > args.max_retries:
                    break
                print(f"[{i}/{len(todo)}] .. session full, wait {args.backoff}s "
                      f"(retry {attempt}/{args.max_retries})")
                time.sleep(args.backoff)
            if good:
                ok += 1
                mark_done(url)
            else:
                fail += 1
            tag = "ok " if good else "ERR"
            print(f"[{i}/{len(todo)}] {tag} {url}  {msg}")
            if i < len(todo):
                time.sleep(rate)
    print(f"\ndone: {ok} submitted, {fail} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
