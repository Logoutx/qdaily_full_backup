# Adversarial fidelity review — batch pass (Grok)

The third and final gate of the QDaily translation pipeline, after **Kimi/Sonnet draft → Opus polish → deterministic QA** (`tools/translate_qa.py`). The deterministic gate already covers images, residual Chinese, truncation, and forbidden renderings. Your job is the class of fact corruption a checker CANNOT see — where the English is fluent and every number survives, but a fact is wrong. A different model lineage catches what same-family self-review misses.

For each article id you are given, read both files in the repo:
- `data/translations/in/<id>.json` — the Chinese source (fields: title, excerpt, category, body)
- `data/translations/en/<id>.json` — the published English translation (same field names)

Hunt ONLY for these five classes. Do **not** comment on style, word choice, or numbers — other gates own those:

1. **Entity swap** — a company, person, place, product, or work title in the EN that is not the one the source names (e.g. source 华为 rendered as a different firm; a book/film title mapped to the wrong work).
2. **Polarity / negation flip** — the EN reverses the source's sense: 否认→"confirmed", 上涨→"fell", 拒绝→"agreed", a dropped "not", a "more than" that became "less than".
3. **Attribution swap** — a statement, opinion, quote, or action assigned to the wrong actor. QDaily features quote many sources per piece; this is the most likely defect here.
4. **Causal reversal** — the EN inverts cause and effect, or "A because B" becomes "B because A".
5. **Meaning-changing qualifier loss** — a dropped hedge/scope word that changes the claim: 据传/可能/计划/拟 stated as fact; "in one city" widened to "nationwide"; "expected to" turned into "did".

QDaily-specific notes:
- Chinese company/app names must follow `data/translations/glossary.json`. A rendering that contradicts the glossary is an **entity swap** (class 1), not a style quibble.
- These are 2014–2019 articles. Do not flag a claim as wrong because it was overtaken by later events — faithfulness to the source is the only test.
- Source-side factual errors are to be preserved, not corrected. If the EN "fixes" something the Chinese got wrong, that is a finding.

For each finding: `id · class · zh evidence (quote) · en text (quote) · one-line why · severity` (HIGH = changes what the article asserts; LOW = subtle/arguable). If an article is a fair rendering, mark it **clean** — be a skeptic, but do not invent problems.

End with exactly two things: a per-id table `| id | verdict (clean / N findings) |`, and a RANKED list of every HIGH-severity finding across the batch, most damaging first, one line each.

Read only the in/ and en/ files for the batch ids you are given. Nothing else.
