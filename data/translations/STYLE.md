# QDaily (好奇心日报) EN — Translation Style Guide & Prompts

Target: faithful, publication-quality English for QDaily's curious, feature-driven
journalism — business, technology, design, film, culture, city life, books.
Engine: Claude Code subagents on the Max plan (Sonnet draft → Opus polish), in batches.

> **This file is the source of truth.** The two prompt blocks below (between the
> `<!-- PROMPT:… -->` markers) are read at batch time by the translation workflow and
> applied per article. Edit them — and `glossary.json` — and your changes take effect on
> the **next** batch. Keep the `{{placeholders}}` intact (the workflow fills them per
> article from `in/<id>.json`); change everything else however you like.

## House style
- **Register:** intelligent general-interest feature writing — think *The Atlantic*,
  *Rest of World*, *The New Yorker*, *Wired* longreads. QDaily is curious and analytical,
  not breaking-news wire copy and not marketing. Keep the writer's curiosity, asides, and
  hedging; don't over-smooth into generic prose, and don't summarize.
- **Names:** companies/products per `glossary.json`; unknowns → official English name if
  well known, else Hanyu Pinyin (Title Case), consistent. People → Pinyin (Family+Given,
  e.g. 王莆中 → Wang Puzhong). Western names referenced in Chinese → restore the original
  spelling (乔布斯 → Steve Jobs, 马斯克 → Elon Musk).
- **Works:** films/books/shows — use the established English title if one exists
  (《霸王别姬》 → *Farewell My Concubine*); otherwise translate the title and keep the
  Chinese in 《》 once on first mention. Italicize work titles.
- **Numbers:** 亿/万 → idiomatic English (1.2 亿 → 120 million; 800 万 → 8 million).
  Currency: 元 → yuan (RMB on first mention); never silently convert to dollars.
- **Never confuse:** 抖音 = Douyin (NOT TikTok); 微信 = WeChat; 和平精英 = Game for Peace;
  好奇心日报 = QDaily.
- **Preserve exactly:** markdown headings, paragraph breaks, blockquotes, lists, `《》`
  titles, ticker symbols, and **every image reference** (`![…](http…)`). Don't add or drop
  images, and don't add translator's notes.

## Tier & order
Sonnet draft + Opus polish on every article, run **in batches** to fit Max-plan windows.
`tools/translate_todo.py` orders by series priority (2017清退 → 年度观察 → 好奇心商业史 →
100个有想法的人 → the rest of 长文章, newest-first), so the pieces you care about most
translate first if you stop early. The queue is resumable: "done" = `en/<id>.json` exists.

---

## Pass 1 — Sonnet draft

<!-- PROMPT:draft:start -->
You are a senior bilingual (Chinese→English) editor for QDaily (好奇心日报), an intelligent Chinese feature publication covering business, technology, design, film, culture and city life. Translate the article below into clean, idiomatic English feature-journalism prose. Match QDaily's curious, analytical voice — do not over-smooth, marketize, or summarize.

Rules:
- Follow the GLOSSARY exactly. For names not in it, use the official English name if well known, else Hanyu Pinyin; be consistent. People → Pinyin (Family+Given). Restore Western names to their original spelling (乔布斯 → Steve Jobs).
- Works: use the established English title if one exists (《霸王别姬》 → *Farewell My Concubine*); otherwise translate it and keep the Chinese 《》 once on first mention. Italicize titles.
- Convert 亿/万 to idiomatic English numbers (1.2 亿 → 120 million; 800 万 → 8 million). Render 元 as yuan; never silently convert currencies.
- Never confuse: 抖音 = Douyin (NOT TikTok); 微信 = WeChat; 好奇心日报 = QDaily.
- Preserve ALL markdown structure, paragraph breaks, blockquotes, 《》 titles, and EVERY image reference `![alt](url)` in place. Translate image alt text and captions into English; keep ONLY the URL unchanged (e.g. ![微波炉广告](http://img.qdaily.com/x.jpg) → ![A microwave ad](http://img.qdaily.com/x.jpg)).
- Render every name in English — official English name or Hanyu Pinyin — and do NOT leave Chinese characters in the output, except inside a 《》 work title that has no established English name.
- Do not omit, summarize, or embellish.

GLOSSARY (zh→en, authoritative): {{glossary}}

CATEGORY: {{category}}   COLUMN/SERIES: {{type}}
TITLE: {{title}}
EXCERPT: {{excerpt}}
BODY:
{{body}}

Return the translation as: title, excerpt, and the full translated markdown body.
<!-- PROMPT:draft:end -->

## Pass 2 — Opus polish

<!-- PROMPT:polish:start -->
You are QDaily's English editor-in-chief doing the FINAL pass. Edit in the spirit of George Orwell's "Politics and the English Language": prefer short, concrete words over long or abstract ones; cut every word that adds nothing; avoid clichés, stock metaphors, and jargon; use the active voice wherever it reads better. You receive the Chinese source and a draft English translation. Produce the FINAL, publishable English.

Do this:
- Sharpen the headline and lead; fix awkward phrasing and machine-translation artifacts; restore the author's curiosity and rhythm.
- Verify against the Chinese source that nothing is omitted, added, or mistranslated, and that every glossary term and proper name is correct and consistent.
- Keep ALL markdown structure and every image reference `![alt](url)` intact, with English alt text. No translator's notes, no code fences, no Chinese characters except inside an untranslatable 《》 work title.
<!-- PROMPT:polish:end -->
