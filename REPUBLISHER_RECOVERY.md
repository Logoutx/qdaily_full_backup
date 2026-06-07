# QDaily image recovery from republishers

Many in-article images were never archived by Wayback (~153k of 186k image
URLs are `no-snapshot`). Some QDaily articles were re-published **with images**
on third-party sites, which can serve as a recovery source.

## Method

1. Crawl a republisher's official 好奇心日报 account/column.
2. Each republished article links the original (`qdaily.com/articles/<id>.html`)
   → **exact** article-id mapping (no fuzzy headline matching needed).
3. Extract body images from the article's own content section only
   (e.g. Manager Today `section.widget.picture`) — this **excludes** site
   chrome, ads, related-article thumbnails, and author pics.
4. Map images to the QDaily article's body-image slots **in reading order**
   and write to `assets/<qid>/<sha1(qdaily_url)[:16]><ext>` so the local-asset
   renderer (`--image-mode local`) serves them (mirrored to R2 on deploy).

### Mapping caveat (why this isn't fully automatic)
- Republishers often **member-gate / truncate** long articles (fewer images).
- Some **split or add** images, so counts differ from QDaily's.
- Image *upload dates* on the republisher CDN are NOT reliable story-membership
  signals (verified: 'old-dated' images were still genuine story photos).
- Safe 1:1 mapping is only certain when the republisher's body-image count
  **equals** QDaily's body-image count.

## Source 1 — Manager Today 經理人 (managertoday.com.tw/columnist/view/1305)

Official 好奇心日報 column, **46 articles** (all exact-mapped to QDaily ids).
Same account also mirrored at 數位時代 `bnext.com.tw/author/1305` (same group/id).

### Recovery buckets

| Bucket | Articles | Meaning |
|--------|---------:|---------|
| EXACT (MT==QD body) | 1 | safe 1:1 — **recovered** |
| trunc (MT<QD body)  | 7 | MT gated/partial — prefix-map (needs review) |
| over (MT>QD body)   | 1 | MT split/added — ambiguous offset |
| gated (MT=0 imgs)   | 11 | MT imported no body images — unrecoverable here |
| already complete    | 25 | QDaily images already recovered from Wayback |

### Per-article detail

| QDaily | MgrToday | QD body | QD missing | MT imgs | bucket | title |
|-------:|---------:|--------:|-----------:|--------:|--------|-------|
| 53005 | 56120 | 13 | 13 | 13 | EXACT | 全球最时髦的酒店之一 ACE Hotel，这生意是怎么做起来 |
| 63954 | 57818 | 24 | 0 | 23 | complete | 好看的包装设计到处都有，这家英国设计公司更擅长“好卖”的 |
| 63120 | 57583 | 4 | 0 | 4 | complete | 人类为什么要睡觉？睡得少可能会改变基因 | TED 2019 |
| 59553 | 57475 | 3 | 0 | 1 | complete | 合适的音乐的确使人身心愉快，但谁能解释完整的原因？ |
| 62195 | 57450 | 1 | 0 | 0 | complete | 调查称睡眠不足会使人自私，就是字面意义上的“无暇他顾” |
| 61930 | 57375 | 4 | 0 | 4 | complete | 日本的星巴克烘焙工坊开业，折纸、樱花和木质是关键词 |
| 61283 | 57291 | 0 | 0 | 0 | complete | 玩具反斗城破产重组一年半，前高管们要复活它重新营业 |
| 61279 | 57290 | 4 | 0 | 2 | complete | 星巴克在上海开了主打酒饮和轻食的新店，咖啡不是主角 |
| 53570 | 57196 | 1 | 0 | 0 | complete | 苹果的自动驾驶进展不顺利，现在才开始合作改装园区内的接驳车 |
| 60070 | 57139 | 1 | 0 | 1 | complete | 万事达把mastercard从Logo中去掉，因为觉得人们已 |
| 58604 | 56938 | 0 | 0 | 0 | complete | iPhone XR 上市一个月，苹果已经补贴经销商降价出售 |
| 57260 | 56766 | 1 | 0 | 0 | complete | 全家母公司入股唐吉诃德，也要做折扣超市 |
| 56730 | 56689 | 0 | 0 | 0 | complete | 星巴克调整组织架构应对业绩增速放缓，并且明年还要关 150  |
| 55102 | 56391 | 1 | 0 | 1 | complete | 星巴克说明年全面淘汰塑料吸管，但目前还没完美替代品 |
| 49408 | 56363 | 1 | 0 | 1 | complete | 高露洁跟苹果合作出了电动牙刷，只在苹果网店卖 |
| 52664 | 56028 | 2 | 0 | 2 | complete | 赛百味不到三年要关 1695 家美国门店，遇到了什么问题？ |
| 51992 | 55942 | 3 | 0 | 5 | complete | 要关更多实体店，阿迪达斯 CEO 说网店最重要 |
| 50807 | 55774 | 9 | 0 | 6 | complete | “金拱门”有了新玩法，在加拿大被分解成了指路牌 |
| 48320 | 55466 | 9 | 0 | 7 | complete | 大家都在靠网红卖货，为什么这款包包可以不靠宣传走红亚洲？|  |
| 48363 | 55467 | 2 | 0 | 1 | complete | 试行了三个月，优衣库现在要在全球推出半定制服务 |
| 47545 | 55444 | 2 | 0 | 2 | complete | 促进地域再生，无印良品与东京丰岛区将合作打造新街区 |
| 47928 | 55413 | 6 | 0 | 6 | complete | 比尔·盖茨每年都会推荐 5 本书，这是他今年的书单 |
| 47953 | 55412 | 11 | 0 | 10 | complete | 星巴克为什么在上海开了一家全球最大的“咖啡奇幻乐园”？ |
| 47936 | 55411 | 2 | 0 | 5 | complete | 无印良品替巴黎人准备了圣诞礼物，是3.7万多支彩色笔拼成的像 |
| 47594 | 55375 | 0 | 0 | 0 | complete | 便利店能便利到多远？全家说他们要做洗衣房生意 |
| 46708 | 55260 | 7 | 0 | 4 | complete | 「这世界」因为 Google 放错了汉堡里芝士片的位置，一堆 |
| 22456 | 52096 | 9 | 9 | 0 | gated(MT=0) | 商业世界运行的规则，来自他 80 年前的 3 个创新 | 这 |
| 20648 | 51916 | 9 | 9 | 0 | gated(MT=0) | 麦当劳的野心和焦虑，都藏在它 50 多年来历次包装更新里 | |
| 29047 | 52834 | 5 | 5 | 0 | gated(MT=0) | 「日本語」日本列车是如何变身足汤馆、高级餐厅和居酒屋的 |
| 28306 | 52803 | 5 | 5 | 0 | gated(MT=0) | 迪士尼花 55 亿美元在上海造的这个乌托邦，能帮它赌赢中国市 |
| 48243 | 55452 | 3 | 3 | 0 | gated(MT=0) | 「更新」花了 661 亿美元，迪士尼从福克斯那里买了啥？|  |
| 33547 | 53422 | 3 | 3 | 0 | gated(MT=0) | 如果CEO在广告里承诺员工福利，你会更信任这公司吗？ |
| 29014 | 52798 | 3 | 3 | 0 | gated(MT=0) | 「这世界」专家说，运动的减肥效果其实没那么大 |
| 31734 | 53178 | 2 | 2 | 0 | gated(MT=0) | 95到10年出生的美国人怎么看待工作？这里有份报告 |
| 12102 | 52684 | 15 | 2 | 0 | gated(MT=0) | 迪士尼乐园 60 周年，它是如何成长为 150 亿美元的大生 |
| 40460 | 54586 | 1 | 1 | 0 | gated(MT=0) | 日本便利店市场趋于饱和，开迷你便利店成了新机会 |
| 21380 | 51986 | 1 | 1 | 0 | gated(MT=0) | 关于苹果今天发布的财报，你可能感兴趣的 10 个事实 |
| 60399 | 57197 | ? | ? | 0 | not-in-corpus |  |
| 53968 | 56208 | 4 | 4 | 7 | over(MT>QD) | 舒尔茨彻底离开星巴克，他在 31 年里把卖咖啡豆的小店变成  |
| 50723 | 55775 | 25 | 25 | 12 | trunc(MT<QD) | 亚马逊无人便利店要开到更多城市了，它有什么不一样的？ |
| 36393 | 54689 | 19 | 19 | 8 | trunc(MT<QD) | 这家公司卖出 1200 万个“杯缘子”扭蛋，营销没花一毛钱｜ |
| 51545 | 55883 | 15 | 15 | 9 | trunc(MT<QD) | 针对教育市场的新 iPad 来了，变化不大，平板电脑诞生 8 |
| 52817 | 56091 | 15 | 11 | 7 | trunc(MT<QD) | 包装设计如何创造价值？日本设计师德田祐司说，还是要讲好故事 |
| 42318 | 54692 | 10 | 10 | 1 | trunc(MT<QD) | 宜家都难以撼动的日本家具市场，这个叫 NITORI 的凭什么 |
| 39470 | 54332 | 10 | 10 | 1 | trunc(MT<QD) | 为什么是达美乐而不是 Google，成了 2004 年至今美 |
| 39000 | 54257 | 6 | 6 | 4 | trunc(MT<QD) | 一风堂上市，这个日本拉面店如何一路火到了纽约、新加坡、香港和 |

## Recovered & live (13 images, 1 article)

| QDaily | imgs | confidence | title |
|-------:|-----:|------------|-------|
| 53005 | 13/13 | high (exact count, cross-checked vs topys.cn/26645) | 全球最时髦的酒店之一 ACE Hotel |

## Identified & held for review (not deployed)

Member-gated Manager Today articles with no QDaily banner — first-K body images
in reading order can be prefix-mapped safely, but held pending manual review of
ordering. Re-fetchable from `data/managertoday_mapping.json`.

| QDaily | recoverable | MgrToday | title |
|-------:|------------:|---------:|-------|
| 50723 | 12 of 25 | 55775 | 亚马逊无人便利店 (Amazon Go) |
| 51545 | 9 of 15  | 55883 | 针对教育市场的新 iPad |
| 42318 | 1 of 10  | 54692 | NITORI 日本家具 |
| 39470 | 1 of 10  | 54332 | 达美乐 vs Google |


## Other republishers found (not yet harvested)

- **TOPYS** `topys.cn` — mainland creative platform; confirmed carrying QDaily
  articles with full image sets on a single CDN date (clean). Promising for scale.
- **股感 StockFeel** `stockfeel.com.tw/author/qdaily/` — QDaily author page (finance).
- **數英 digitaling** `digitaling.com/company/articles/11479` — QDaily company page.
- **數位時代 bnext** `bnext.com.tw/author/1305` — same content as Manager Today.

## Recommended next steps

1. Harvest **TOPYS** (likely the largest mainland mirror) the same way.
2. For Manager Today `trunc`/`over` articles, recover with manual order review
   (≈42 more images across 8 articles) — or pull them from TOPYS where the image
   set is complete and single-dated.
3. Cross-source agreement (MT vs TOPYS image counts) raises confidence enough to
   auto-wire even non-exact cases.