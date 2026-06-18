"""
Audit Cloudflare DNS for dangling A/AAAA records (subdomain-takeover risk),
following up the Cloudflare Security Insights "Dangling A Record" alerts.

The site's ONLY legitimate address records are the apex + www pointing at
GitHub Pages, and cdn (a CNAME to R2). Any other A/AAAA record — especially one
pointing to an IP you no longer control — is a takeover candidate: remove it or
repoint it. This script lists every record and flags the suspects.

Auth: a Cloudflare API token with **Zone → DNS → Read** (the repo's existing
CLOUDFLARE_API_TOKEN is Cache-Purge-only and will NOT work — make a new one at
https://dash.cloudflare.com/profile/api-tokens). Read-only; it changes nothing.

Usage:
  export CF_DNS_TOKEN=...            # DNS:Read token
  python tools/cf_dns_audit.py                 # auto-finds the qdaily.org zone
  python tools/cf_dns_audit.py --domain qdaily.org --zone <zone_id>
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

API = "https://api.cloudflare.com/client/v4"

# GitHub Pages anycast addresses (apex A + AAAA). These are the only IPs the
# site's address records should resolve to.
GH_PAGES = {
    "185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153",
    "2606:50c0:8000::153", "2606:50c0:8001::153",
    "2606:50c0:8002::153", "2606:50c0:8003::153",
}


def get(client: httpx.Client, path: str, **params) -> dict:
    r = client.get(f"{API}{path}", params=params)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise SystemExit(f"Cloudflare API error: {j.get('errors')}")
    return j


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("CF_DNS_TOKEN")
                    or os.environ.get("CF_API_TOKEN", ""))
    ap.add_argument("--zone", default=os.environ.get("CF_ZONE_ID", ""))
    ap.add_argument("--domain", default="qdaily.org")
    args = ap.parse_args()
    if not args.token:
        raise SystemExit("Set CF_DNS_TOKEN (a Zone→DNS→Read API token).")

    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {args.token}"}) as c:
        zone = args.zone
        if not zone:
            zones = get(c, "/zones", name=args.domain).get("result", [])
            if not zones:
                raise SystemExit(f"Zone not found for {args.domain} "
                                 "(does the token cover this zone?).")
            zone = zones[0]["id"]
            print(f"zone {args.domain} = {zone}")

        records, page = [], 1
        while True:
            j = get(c, f"/zones/{zone}/dns_records", per_page=100, page=page)
            records += j["result"]
            info = j.get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1

    addr = [r for r in records if r["type"] in ("A", "AAAA")]
    flagged = [r for r in addr if r["content"] not in GH_PAGES]
    ok = [r for r in addr if r["content"] in GH_PAGES]
    cnames = [r for r in records if r["type"] == "CNAME"]

    print(f"\n{len(records)} records total | {len(addr)} A/AAAA "
          f"({len(ok)} GitHub-Pages, {len(flagged)} other) | {len(cnames)} CNAME\n")

    if flagged:
        print("⚠️  REVIEW these A/AAAA records — not GitHub-Pages IPs (possible "
              "dangling / takeover risk). Delete if unused, or repoint:")
        for r in flagged:
            proxied = "proxied" if r.get("proxied") else "DNS-only"
            print(f"   {r['type']:4} {r['name']:32} → {r['content']:24} "
                  f"[{proxied}, ttl {r.get('ttl')}]  id={r['id']}")
    else:
        print("✅ No unexpected A/AAAA records — every address record points to "
              "GitHub Pages.")

    print("\nCNAMEs (informational; cdn → R2 is expected):")
    for r in cnames:
        proxied = "proxied" if r.get("proxied") else "DNS-only"
        print(f"   {r['name']:32} → {r['content']}  [{proxied}]")

    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
