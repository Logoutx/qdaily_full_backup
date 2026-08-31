export const meta = {
  name: 'qdaily-translate-batch',
  description: 'Translate a batch of QDaily articles zh->en: Sonnet draft -> Opus polish (file-based, resumable)',
  phases: [
    { title: 'Draft', detail: 'Sonnet faithful translation (skipped for Kimi-drafted ids)', model: 'sonnet' },
    { title: 'Polish', detail: 'Opus editorial pass', model: 'opus' },
  ],
};

// Runs on the Max plan via Claude Code subagents. Invoke with:
//   Workflow({ scriptPath: 'tools/translate_batch.workflow.js',
//              args: { root: '<ABS QDAILY ROOT>', ids: ['48703','11879', ...] } })
//
// Each subagent reads its own inputs from disk (nothing large flows through args):
//   - glossary: <root>/data/translations/glossary.json
//   - prompts:  <root>/data/translations/STYLE.md   (PROMPT:draft / PROMPT:polish blocks)
//   - source:   <root>/data/translations/in/<id>.json  (materialized by tools/translate_todo.py)
// Draft (Sonnet) returns a structured translation in-workflow. Polish (Opus) writes the
// final result to <root>/data/translations/out/<id>.txt (sentinel format), then returns a
// small status. After the workflow:  python tools/translate_collect.py  (writes en/<id>.json).

let a0 = args;
if (typeof a0 === 'string') { try { a0 = JSON.parse(a0); } catch (e) { /* leave */ } }
a0 = a0 || {};
const root = a0.root;
const ids = (a0.ids || []).map(String);
// Ids already drafted by tools/translate_draft_kimi.mjs (Kimi subscription, not
// Claude quota). For those, stage 1 is a no-op and stage 2 reads the draft from
// data/translations/out/drafts/<id>.txt — halving Claude spend per article and,
// more importantly, keeping a 20-article batch inside one usage window.
const draftedIds = new Set((a0.draftedIds || []).map(String));
if (!root) throw new Error('args.root (absolute project path) is required');
if (!ids.length) throw new Error('args.ids (array of article ids) is required');

const DRAFT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { title: { type: 'string' }, excerpt: { type: 'string' }, body: { type: 'string' } },
  required: ['title', 'body'],
};
const STATUS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: { id: { type: 'string' }, title: { type: 'string' }, ok: { type: 'boolean' }, note: { type: 'string' } },
  required: ['id', 'ok'],
};

const draftPrompt = (id) => `You are translating ONE article for QDaily (好奇心日报, a Chinese feature publication), Chinese -> English. This is the faithful first-draft pass.

Do these reads first:
1. Read ${root}/data/translations/glossary.json — follow it EXACTLY for every company / product / term it lists.
2. Read ${root}/data/translations/STYLE.md and find the block between "<!-- PROMPT:draft:start -->" and "<!-- PROMPT:draft:end -->". Those are your authoritative rules — apply them in full. (Ignore the literal {{...}} placeholder tokens; use the real fields from the source file below.)
3. Read the source article ${root}/data/translations/in/${id}.json — JSON with fields: title, excerpt, category, type, body (markdown).

Translate the title, the excerpt, and the full markdown body. Preserve ALL markdown structure, paragraph breaks, blockquotes, 《》 titles, and EVERY image reference ![alt](url) in place — translate the alt text, keep the URL byte-for-byte. Do not omit, summarize, or embellish. Do not leave Chinese characters except inside an untranslatable 《》 work title.

Return via the structured-output tool: title (English), excerpt (English; empty string if none), body (the full translated markdown body only — no JSON, no code fences).`;

// Stage-1 output is either a { title, excerpt, body } draft (Sonnet agent) or the
// literal { fromDisk: true } for ids tools/translate_draft_kimi.mjs already drafted —
// point the polish prompt at the on-disk sentinel file instead of inlining it.
const draftSection = (id, draft) => draft.fromDisk
  ? `Read the draft from ${root}/data/translations/out/drafts/${id}.txt — it is sentinel-formatted (@@QD_TITLE@@/@@QD_EXCERPT@@/@@QD_BODY@@). Treat it as the draft to polish, not as the final file.`
  : `Here is the draft English translation to polish:

@@DRAFT_TITLE@@
${draft.title}
@@DRAFT_EXCERPT@@
${draft.excerpt || ''}
@@DRAFT_BODY@@
${draft.body}`;

const polishPrompt = (id, draft) => `You are QDaily's English editor-in-chief doing the FINAL pass on one article (Chinese -> English).

Do these reads first:
1. Read ${root}/data/translations/glossary.json — every term must match it.
2. Read ${root}/data/translations/STYLE.md and find the block between "<!-- PROMPT:polish:start -->" and "<!-- PROMPT:polish:end -->". Those are your authoritative editing rules (edit in the spirit of Orwell's "Politics and the English Language") — apply them in full.
3. Read the Chinese source ${root}/data/translations/in/${id}.json and verify the translation omits nothing and mistranslates nothing, and that EVERY image ![](url) in the source is present in the output.

${draftSection(id, draft)}

Produce the FINAL, publishable English. Keep QDaily's curious, analytical feature voice. Keep ALL markdown and every ![alt](url) image reference intact, with English alt text. No translator's notes, no code fences, no Chinese except inside an untranslatable 《》 title.

Then WRITE your final result to ${root}/data/translations/out/${id}.txt using the Write tool, in EXACTLY this format (literal sentinel lines, each on its own line):

@@QD_TITLE@@
<final English title on a single line>
@@QD_EXCERPT@@
<final English excerpt on a single line, or a blank line if none>
@@QD_BODY@@
<the full final markdown body, verbatim, as many lines as needed>

Write nothing else to that file — no commentary before @@QD_TITLE@@ or after the body.

Finally, return via the structured-output tool: id="${id}", ok=true (false only if you could not complete it), title=<final title>, note=<optional one-line flag for human review, else empty>.`;

log(`Translating ${ids.length} QDaily articles (Kimi/Sonnet draft -> Opus polish), root=${root}, pre-drafted=${draftedIds.size}`);

const results = await pipeline(
  ids,
  (id) =>
    draftedIds.has(id)
      ? { fromDisk: true } // pre-drafted by tools/translate_draft_kimi.mjs — no Sonnet call
      : agent(draftPrompt(id), { label: `draft:${id}`, phase: 'Draft', model: 'sonnet', schema: DRAFT_SCHEMA }),
  (draft, id) =>
    agent(polishPrompt(id, draft), { label: `polish:${id}`, phase: 'Polish', model: 'opus', schema: STATUS_SCHEMA }),
);

const ok = results.filter(Boolean);
const good = ok.filter((r) => r.ok);
log(`Done: ${good.length}/${ids.length} written to data/translations/out/ (flagged: ${ok.filter((r) => r.note).map((r) => r.id + ':' + r.note).join('; ') || 'none'})`);
return ok;
