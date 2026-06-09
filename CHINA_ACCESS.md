# China accessibility & visibility — runbook + status

Make www.qdaily.org load reliably from mainland China and get it indexed by the
search engines Chinese users actually use. The site is static, fronted by
Cloudflare (proxied), origin = GitHub Pages (Tokyo); images on Cloudflare R2 at
cdn.qdaily.org.

## Final status (2026-06-09)

| Lever | Status |
|---|---|
| **Cloudflare edge-caching** of HTML (accessibility) | ✅ **live** — Cache Rule on, `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ZONE_ID` secrets set, purge-on-deploy runs. Verified `cf-cache-status: HIT` on home + article pages; TTFB ~0.9s→~0.6s. |
| **Image mirror → cdn.qdaily.org** (kills Wayback dependence) | ⏳ gentle background fetch (`tools/fetch_missing_leads.py --scope all`, ~128k URLs, 1–3 days) |
| **Bing** | ✅ verified + sitemap submitted + 54,761 URLs pushed via IndexNow |
| **Yandex** | ✅ 54,761 URLs via IndexNow |
| **Baidu** | ✅ site verified; submit the **flat** sitemap `https://www.qdaily.org/sitemap-flat.xml` (NOT `sitemap.xml`) |
| **Google** | ✅ chunked index `sitemap.xml` + robots |

### Hard constraints (won't change)
- **web.archive.org is blocked in the mainland.** Not-yet-mirrored images
  (Wayback-primary) won't load there → the image mirror is the real fix. After it
  finishes, consider dropping the Wayback `data-wb` onerror fallback
  (CDN→placeholder) so China users don't eat a timeout on rare CDN misses.
- **ICP filing (备案) is infeasible** for this content (a regulator-shut outlet).
  So: no China-mainland CDN/hosting, no Cloudflare China Network, and **Baidu
  throttles us hard** — `今日提交上限: 0` (API/manual push quota is 0; the Baidu
  push token is useless for us) and `存量文件上限: 1` (one stored sitemap file).
  Treat Baidu as best-effort; **Bing + IndexNow is the strongest China-reachable
  channel** and already has the full corpus.
- **Domain-block risk:** the GFW could SNI/DNS-block `qdaily.org` regardless of
  hosting. Cloudflare's shared IPs help but don't guarantee it.

---

## Runbook (how it was set up / how to redo)

### Part A — Cloudflare edge-caching ✅ done
1. Cloudflare dash → **qdaily.org** → **Caching → Cache Rules → Create rule**:
   - When: **Hostname equals `www.qdaily.org`**.
   - Then: **Eligible for cache** on; **Edge TTL** = Override → 1 day; **Browser
     TTL** = Respect origin. (Leave `cdn.qdaily.org` as-is — content-addressed.)
2. Repo secrets (Settings → Secrets and variables → Actions):
   - `CLOUDFLARE_ZONE_ID` (zone Overview), `CLOUDFLARE_API_TOKEN` (scoped **Zone →
     Cache Purge → Purge**, this zone only).
3. `.github/workflows/deploy.yml` has a **Purge Cloudflare cache** step that runs
   after each Pages deploy (conditioned on `env`, not `secrets` — see CI note
   below). Verify: `curl -I https://www.qdaily.org/` twice → 2nd is `HIT`.

### Part B — Baidu + Bing ✅ done
Ownership verification for every console = drop their file into **`site/root/`**
(served at the site root by render) → commit → push → wait for the green deploy →
`curl` the file returns 200 → click Verify. See `site/root/README.md`.

- **Baidu** 搜索资源平台 (ziyuan.baidu.com): verified via `baidu_verify_*.html`.
  Submit the **flat** sitemap (Baidu **rejects index-type** sitemaps, and our
  `sitemap.xml` is an index): paste `https://www.qdaily.org/sitemap-flat.xml` into
  **普通收录 → sitemap → 提交**. Don't bother with API推送 — quota is 0 for non-ICP.
- **Bing** (bing.com/webmasters): verified, sitemap submitted, plus IndexNow.

Push URLs (overseas sites crawl slowly; these ask the engine to fetch directly):
```bash
# Bing + Yandex via IndexNow (key already hosted at /<key>.txt — no account):
python tools/submit_search_engines.py --indexnow --send
# Baidu (NOT useful for us — non-ICP daily quota is 0):
# python tools/submit_search_engines.py --baidu-token YOUR_TOKEN --send
```
IndexNow note: the first batch may return `403 SiteVerificationNotCompleted`
(key-validation lag) — just re-run; it's idempotent.

### Part C — Sogou / 360 / Shenma (optional, not done)
Same flow: zhanzhang.sogou.com, zhanzhang.so.com, zhanzhang.sm.cn → verify via a
file in `site/root/` → submit the **flat** sitemap. Lower priority / more signup
friction.

### Part D — Reachability testing (do after the mirror; not done)
Test FROM inside China via **itdog.cn**, **17ce.com**, **boce.com**:
- `https://www.qdaily.org/`, a deep article (e.g. `/articles/88/`), and a real
  image `https://cdn.qdaily.org/<id>/<digest>.webp`.
- Many-city timeouts/loss → likely SNI-blocked (hosting won't fix). Reachable but
  slow everywhere → consider a HK/SG/JP origin; with edge-caching on, Cloudflare
  absorbs most latency, so re-test before investing.

---

## Implementation notes (in-repo)
- **`tools/render.py`**: explicit CN-crawler robots entries (Baiduspider / Sogou
  web spider / 360Spider / Haosou / Yisou / Bingbot); a `site/root/` → site-root
  passthrough (markdown excluded); and `sitemap-flat.xml` — a single `<urlset>`
  capped at Baidu's 50,000-URL / 10 MB per-file limit (covers ~50k of ~55k in the
  one slot Baidu allows). Google/Bing keep using the chunked index `sitemap.xml`.
- **`site/root/`**: IndexNow key `<hex>.txt` + the Baidu verification file.
- **`tools/submit_search_engines.py`**: Baidu link-push + IndexNow (Bing/Yandex),
  builds canonical URLs from the deduped corpus; dry-run by default.
- **CI gotcha (fixed):** the `secrets` context is **not allowed in `if:`** —
  using it voids the whole workflow ("No jobs were run", 0s). The purge step maps
  secrets to job-level `env` and conditions on `env` instead.
- **Deploys are slow right now** (~10–40 min): each re-syncs R2 while the image
  mirror keeps adding files, and rebuilds the 55k-page Pagefind index.

## Remaining (optional)
- [ ] Part C: Sogou / 360 / Shenma
- [ ] Part D: mainland reachability tests; decide on a nearer origin
- [ ] After the image mirror finishes: consolidated re-deploy + drop the Wayback
      `data-wb` fallback for China
