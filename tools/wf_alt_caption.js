export const meta = {
  name: 'alt-caption',
  description: 'Vision pass: read each on-disk QDaily photo + article context, write Simplified-Chinese alt text',
  phases: [
    { title: 'Caption', detail: 'one agent per batch of images; each reads its slice of the worklist, captions, writes a TSV part file' },
  ],
}

// ---- Run parameters (edited per invocation; args passthrough is unreliable
// with scriptPath, so these are baked-in constants). Lines are 1-based,
// inclusive, into the worklist JSONL: {url, asset, id, title, ctx, kind}. ----
const WORKLIST = 'data/alt_worklist.jsonl'
const OUTDIR = 'data/alt_parts'
const START = 1        // first worklist line to process — updated by scheduler each run
const END = 100        // last worklist line to process (inclusive) — updated by scheduler each run
const BATCH = 10       // images per agent (100/10 = 10 agents per run); assets are ~512px thumbs now

const ranges = []
let i = 1
for (let s = START; s <= END; s += BATCH) {
  ranges.push({ i: i++, start: s, count: Math.min(BATCH, END - s + 1) })
}

const RESULT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    i: { type: 'integer' },
    n_written: { type: 'integer', description: 'number of url<TAB>alt lines written to the part file' },
    part_file: { type: 'string' },
    sample: { type: 'string', description: 'one example "url => alt" for spot-checking' },
  },
  required: ['i', 'n_written', 'part_file'],
}

phase('Caption')

const results = await parallel(ranges.map((rg) => () => {
  const end = rg.start + rg.count - 1
  const part = `${OUTDIR}/part_${rg.start}.tsv`
  return agent(
    `You write accessibility alt text for photos in an archived 好奇心日报 (QDaily) news site.\n\n` +
    `STEP 1 — get your batch. Run this Bash command and read its output:\n` +
    `    sed -n '${rg.start},${end}p' ${WORKLIST}\n` +
    `Each output line is a JSON object: {"url","asset","id","title","ctx","kind"}.\n` +
    `"asset" is a local image file path; "title"/"ctx" describe the article the photo belongs to; ` +
    `"kind" is "banner" (lead photo) or "body" (in-article photo).\n\n` +
    `STEP 2 — caption each one. For EVERY line, use the Read tool on its "asset" path to actually LOOK at the image, ` +
    `then write ONE alt text describing what is VISIBLE in the photo, in Simplified Chinese:\n` +
    `  - Factual and concrete: name the people/objects/scene/setting actually shown. ~12–40 Chinese characters.\n` +
    `  - Use the article title/ctx only to disambiguate what you see (e.g. who a person likely is, what a chart is about) — ` +
    `do NOT just restate the headline, and do NOT invent details not visible.\n` +
    `  - For charts/infographics/screenshots: say it is a chart/界面/截图 and summarize what it shows.\n` +
    `  - No surrounding quotes, no "图片/照片显示" filler, no newlines or TAB characters inside the alt.\n` +
    `  - If Read fails or the image is blank/unreadable, use an empty alt for that url.\n\n` +
    `STEP 3 — write your results. Use the Write tool to create the file:\n` +
    `    ${part}\n` +
    `with ONE line per image in the exact format:  <url><TAB><alt>\n` +
    `(the original url from the JSON, then a single literal tab, then the alt text). ` +
    `Write every url from your batch, even if a few alts are empty. Do not write a header.\n\n` +
    `Then return {i:${rg.i}, n_written:<count>, part_file:"${part}", sample:"<one url => alt>"}.`,
    { label: `caption:${rg.i}`, phase: 'Caption', schema: RESULT_SCHEMA, model: 'sonnet' }
  )
}))

const ok = results.filter(Boolean)
const written = ok.reduce((s, r) => s + (r.n_written || 0), 0)
log(`captioned ${written} images across ${ok.length}/${ranges.length} batches`)
return { batches: ok.length, requested: ranges.length, written, parts: ok.map((r) => r.part_file) }
