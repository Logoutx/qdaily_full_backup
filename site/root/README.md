# site/root/ — files served at the site root

Every file here is copied verbatim to the deploy output root, so it is reachable
at `https://www.qdaily.org/<filename>`. Use it for:

- **Search-engine ownership verification files.** Each console hands you a file
  to host (download it, drop it here, commit, deploy, then click "verify"):
  - Baidu 搜索资源平台 (ziyuan.baidu.com): `baidu_verify_*.html`
  - Bing Webmaster Tools: `BingSiteAuth.xml`
  - Sogou 站长平台: its verification file
  - 360 站长平台 (zhanzhang.so.com): its verification file
  - Shenma/神马 (zhanzhang.sm.cn): its verification file
  (Most consoles also offer a `<meta>` or DNS-TXT method if you prefer.)

- **IndexNow key** — `3a66386079699d3556cf7fa7054704b9.txt` (already here).
  Lets `tools/submit_search_engines.py` push URLs to Bing/Yandex via IndexNow.

After adding files: commit + push (the deploy copies them to the root), then
verify in each console.
