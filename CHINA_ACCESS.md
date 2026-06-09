# China accessibility & visibility

How to make www.qdaily.org load reliably from mainland China and get it indexed
by Chinese search engines. The site is static, behind Cloudflare, origin =
GitHub Pages (Tokyo). Reality checks up front:

- **web.archive.org is blocked in the mainland.** Not-yet-mirrored images
  (Wayback-primary) are broken there. The full image mirror → cdn.qdaily.org is
  the dominant image-accessibility fix; it's running. Once done, consider
  dropping the Wayback `data-wb` onerror fallback (CDN→placeholder) so China
  users don't eat a timeout on rare CDN misses.
- **ICP filing (备案) is infeasible** for this content (a regulator-shut outlet),
  so China-mainland CDN/hosting and Cloudflare's China Network are off the table.
  We optimize the overseas + Cloudflare path instead.
- **Domain-block risk:** the GFW could SNI/DNS-block `qdaily.org` regardless of
  hosting. Cloudflare's shared IPs help but don't guarantee it. A contingency
  mirror domain is possible but is whack-a-mole.

---

## 1. Cloudflare edge-caching (biggest accessibility win)

Today `curl -I https://www.qdaily.org` returns `cf-cache-status: DYNAMIC` — every
request proxies back to GitHub Pages in Tokyo (~1.2s TTFB even from overseas).
Caching the static HTML at Cloudflare's edge removes that per-request origin hop.

**In the Cloudflare dashboard** (zone `qdaily.org` → Caching → Cache Rules), add a
rule:
- **When:** `Hostname equals www.qdaily.org` (or `URI Path` not starting with
  `/cdn-cgi/`).
- **Then:** *Eligible for cache* = on; *Edge TTL* = Override → e.g. 1 day;
  *Browser TTL* = Respect origin. (This is the "Cache Everything" behavior.)
- Leave the R2 assets (`cdn.qdaily.org`) as-is — content-addressed, already
  immutable-cached.

**Purge on deploy** is already wired in `.github/workflows/deploy.yml` (the
"Purge Cloudflare cache" step). Activate it by adding two repo secrets:
- `CLOUDFLARE_ZONE_ID` — zone id from the Cloudflare dashboard overview.
- `CLOUDFLARE_API_TOKEN` — a token scoped to **Zone → Cache Purge → Purge**
  for this zone only.
Until both exist the step auto-skips (safe to merge now).

Verify after enabling: `curl -I https://www.qdaily.org` should show
`cf-cache-status: HIT` on the second request.

---

## 2. Chinese search-engine visibility

The global SEO is already solid (NewsArticle JSON-LD, og:article, sitemap,
zh-Hans). What's missing is submission to the engines Chinese users actually use.

**Onboard each console** (verify ownership by dropping their file into
`site/root/` → commit → deploy → click verify; see `site/root/README.md`):
- **Baidu** 搜索资源平台 — ziyuan.baidu.com (the big one). Verify, submit
  `sitemap.xml`, grab your **link-push token**.
- **Bing** Webmaster Tools — bing.com/webmasters (reachable in China; feeds
  Copilot). Submit sitemap. *Or* just use IndexNow (below).
- **Sogou** zhanzhang.sogou.com, **360** zhanzhang.so.com, **Shenma/神马**
  (mobile) zhanzhang.sm.cn — verify + submit sitemap.

**Push URLs for faster inclusion** with `tools/submit_search_engines.py`
(overseas sites crawl slowly; these APIs ask the engine to fetch directly):
```bash
# Bing + Yandex via IndexNow (key already hosted at /<key>.txt):
python tools/submit_search_engines.py --indexnow --send
# Baidu (token from ziyuan.baidu.com):
python tools/submit_search_engines.py --baidu-token YOUR_TOKEN --send
```
Robots already names Baiduspider / Sogou web spider / 360Spider / YisouSpider /
Bingbot explicitly.

---

## 3. Reachability testing (decide if a nearer origin is needed)

After enabling edge-caching, measure real mainland latency/availability from
several cities — these tools test FROM inside China:
- **itdog.cn** (多地 HTTP/ping), **17ce.com**, **boce.com**, **chinaz.com/ping**.

Test both:
- `https://www.qdaily.org/` and a deep article URL (e.g. `/articles/42/`)
- `https://cdn.qdaily.org/<id>/<digest>.webp` (a real image)

What to look for:
- High packet loss / timeouts in several cities → domain may be SNI-blocked
  (hosting changes won't fix that).
- Reachable but slow (>2–3s) everywhere → consider a nearer origin (HK/SG/JP
  object storage or VPS) in front of, or instead of, GitHub Pages; with edge
  caching on, Cloudflare absorbs most of this, so re-test before investing.

---

## Quick status
- [x] CN-crawler robots entries, `site/root/` passthrough, IndexNow key
- [x] Cloudflare purge-on-deploy step (needs the two secrets to activate)
- [x] `tools/submit_search_engines.py` (Baidu push + IndexNow)
- [ ] Enable the Cloudflare Cache Rule + add the two secrets
- [ ] Onboard Baidu/Bing/Sogou/360/Shenma + run the submitter
- [ ] Run mainland reachability tests; decide on a nearer origin
- [ ] (after mirror) drop the Wayback fallback for China
