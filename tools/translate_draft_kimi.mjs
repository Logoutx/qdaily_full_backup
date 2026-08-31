// Pre-draft QDaily articles with the Kimi CLI (Kimi K2.7 on a subscription, not
// per-token billing) so the translation workflow's Sonnet draft agent is skipped
// for these ids and only the Opus polish pass spends Claude quota.
//
// Ported from LatePost's scripts/translate-draft-kimi.mjs. QDaily differences:
// the source is data/translations/in/<id>.json (materialized by translate_todo.py),
// not a zh markdown post, and the sentinels are @@QD_*@@.
//
// Why this exists: the 2026-08-31 04:51 batch spent 5.5 hours wall-clock for 34
// seconds of CPU — it sat waiting on Claude rate limits, and launchd silently
// coalesced away the next fire. Moving the draft pass onto the Kimi subscription
// removes roughly half the Claude calls per article.
//
//   node tools/translate_draft_kimi.mjs <id> [<id>…]   # explicit ids
//   node tools/translate_draft_kimi.mjs --limit=20     # next 20 from the queue
//
// Sequential, one Kimi call per id: a quota cutoff or crash mid-run loses only the
// id in flight, and a rerun resumes (skips ids that already have a valid draft).

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync, spawn } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const IN = path.join(ROOT, 'data/translations/in');
const DRAFTS = path.join(ROOT, 'data/translations/out/drafts');
const STYLE_FILE = path.join(ROOT, 'data/translations/STYLE.md');
const GLOSSARY_FILE = path.join(ROOT, 'data/translations/glossary.json');
const CONTRACT_FILE = path.join(DRAFTS, '_contract.md');
const KIMI_BIN = process.env.KIMI_BIN || '/Users/logoutx/.kimi-code/bin/kimi';
// A 6k-char QDaily feature takes Kimi several minutes; the longest run past
// 20k chars. 10 minutes (LatePost's value, for shorter posts) timed out on a
// 10.6k-char article that was still working, so give it real headroom — the
// cost of a too-short timeout is a wasted full-length generation.
const KIMI_TIMEOUT_MS = 25 * 60 * 1000;
const PY = path.join(ROOT, '.venv/bin/python');

const argv = process.argv.slice(2);
const limitArg = argv.find((a) => a.startsWith('--limit='));
// Measured 2026-08-31: ~11 min for a 6.5k-char feature. Sequential (LatePost's
// design) that is ~3.7 h for a 20-article batch — longer than the cron interval
// it is supposed to fit inside. Kimi calls are I/O-bound waits, so run a few at
// once. Kept low: this is a subscription, not an API with a published rate.
const concArg = argv.find((a) => a.startsWith('--concurrency='));
const CONCURRENCY = concArg ? Math.max(1, parseInt(concArg.split('=')[1], 10)) : 3;
const explicitIds = argv.filter((a) => !a.startsWith('--'));

// Pull the draft contract out of STYLE.md (between its <!-- PROMPT:draft:… --> markers)
// so Kimi and the Sonnet fallback agent are held to the SAME contract.
function promptBlock(md, tag) {
  const m = md.match(new RegExp(`<!-- PROMPT:${tag}:start -->([\\s\\S]*?)<!-- PROMPT:${tag}:end -->`));
  if (!m) throw new Error(`STYLE.md is missing the PROMPT:${tag} block`);
  return m[1].trim().replace(/^```[a-z]*\n/, '').replace(/\n```$/, '').trim();
}

function nextIdsFromQueue(limit) {
  const res = spawnSync(PY, [path.join(ROOT, 'tools/translate_todo.py'), '--limit', String(limit), '--emit'], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  if (res.status !== 0) throw new Error(`translate_todo.py --emit failed: ${res.stderr || res.status}`);
  const m = res.stdout.match(/\[[\s\S]*\]/);
  if (!m) throw new Error(`translate_todo.py --emit printed no JSON array:\n${res.stdout}`);
  return JSON.parse(m[0]).map(String);
}

let ids;
if (explicitIds.length) ids = explicitIds;
else if (limitArg) ids = nextIdsFromQueue(parseInt(limitArg.split('=')[1], 10));
else {
  console.error('usage: node tools/translate_draft_kimi.mjs <id> [<id>…]   OR   --limit=N');
  process.exit(1);
}

fs.mkdirSync(DRAFTS, { recursive: true });

// Write the contract once per run (cheap; keeps it fresh if STYLE.md changed).
fs.writeFileSync(CONTRACT_FILE, promptBlock(fs.readFileSync(STYLE_FILE, 'utf8'), 'draft') + '\n');

const SENTINELS = ['@@QD_TITLE@@', '@@QD_EXCERPT@@', '@@QD_BODY@@'];

// Validate output shape without trusting Kimi: sentinels present, in order, body
// non-trivial, and no residual CJK run outside a 《》 title (a Kimi draft that
// quietly gave up mid-article is worse than no draft — Opus would polish the gap).
function validate(txt, src) {
  const idx = SENTINELS.map((s) => txt.indexOf(s));
  if (idx.some((i) => i === -1)) return 'sentinel marker(s) missing';
  if (!(idx[0] < idx[1] && idx[1] < idx[2])) return 'sentinel markers out of order';
  const body = txt.slice(idx[2] + SENTINELS[2].length).trim();
  if (body.length <= 200) return `body too short (${body.length} chars)`;
  const stripped = body.replace(/《[^》]*》/g, '');
  const cjk = stripped.match(/[一-鿿]{9,}/);
  if (cjk) return `untranslated Chinese run: "${cjk[0].slice(0, 20)}…"`;
  // Image parity is a hard contract: the polish pass is told to preserve images,
  // not to restore ones the draft already dropped.
  const nSrc = (src.body.match(/!\[[^\]]*\]\(/g) || []).length;
  const nOut = (body.match(/!\[[^\]]*\]\(/g) || []).length;
  if (nSrc !== nOut) return `image count ${nOut} != source ${nSrc}`;
  return null;
}

const stderrTail = (res) => {
  const msg = (res.error && res.error.message) || res.stderr || '';
  return msg ? msg.trim().split('\n').slice(-5).join('\n    ') : '(no stderr)';
};


let drafted = 0, skippedExisting = 0, skippedNoSource = 0, failed = 0;

// Circuit breaker. Kimi failures are usually systemic (quota out, auth expired,
// service down), not per-article — and each failure costs up to KIMI_TIMEOUT_MS.
// Without this, one wedged service turns a 20-article batch into hours of dead
// waiting before the Sonnet fallback ever runs. After MAX_CONSECUTIVE_FAILURES
// we stop launching new Kimi calls and let the workflow draft the rest.
const MAX_CONSECUTIVE_FAILURES = 2;
let consecutiveFailures = 0;
let abandoned = false;

function runKimi(instruction) {
  return new Promise((resolve) => {
    const child = spawn(KIMI_BIN, ['-p', instruction, '--add-dir', ROOT], { stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    let timer = setTimeout(() => { child.kill('SIGKILL'); }, KIMI_TIMEOUT_MS);
    child.stdout.on('data', () => {});
    child.stderr.on('data', (d) => { stderr += d; if (stderr.length > 8000) stderr = stderr.slice(-8000); });
    child.on('close', (code, signal) => { clearTimeout(timer); resolve({ code, signal, stderr }); });
    child.on('error', (err) => { clearTimeout(timer); resolve({ code: null, signal: null, stderr: err.message }); });
  });
}

// Kimi enforces a rolling 5-hour usage quota; when it is spent every further
// call returns 403 immediately. Measured 2026-08-31: the quota ran out after
// ~4 articles. Treat it as terminal on the FIRST occurrence — burning a second
// article's wait to confirm what the error already states is pure delay.
const QUOTA_RE = /usage limit|quota|\b403\b/i;

function noteFailure(id, why, res) {
  console.error(`✗ ${id}: ${why}\n    ${stderrTail(res)}`);
  failed++;
  if (QUOTA_RE.test(res.stderr || '')) {
    abandoned = true;
    console.error('! Kimi quota exhausted — remaining ids fall back to Sonnet');
    return;
  }
  if (++consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
    abandoned = true;
    console.error(`! giving up on Kimi after ${consecutiveFailures} consecutive failures — remaining ids fall back to Sonnet`);
  }
}

async function draftOne(id) {
  if (abandoned) return;
  const outFile = path.join(DRAFTS, `${id}.txt`);
  const srcFile = path.join(IN, `${id}.json`);

  if (!fs.existsSync(srcFile)) { console.warn(`! ${id}: no in/ source, skip`); skippedNoSource++; return; }
  const src = JSON.parse(fs.readFileSync(srcFile, 'utf8'));

  if (fs.existsSync(outFile)) {
    if (!validate(fs.readFileSync(outFile, 'utf8'), src)) {
      console.log(`- ${id}: valid draft already exists, skip`); skippedExisting++; return;
    }
    fs.unlinkSync(outFile); // stale/invalid draft from an interrupted run — redo it
  }

  const instruction = `You are doing a translation task. Read these three files: ${CONTRACT_FILE} (your complete translation contract — follow it exactly), ${GLOSSARY_FILE} (authoritative term renderings), and ${srcFile} (JSON with fields title, excerpt, category, type, body — the body is Chinese markdown). Translate the title, the excerpt, and the FULL body per the contract. Preserve every markdown image reference ![alt](url) in place: translate the alt text, keep the URL byte-for-byte. Do not omit, summarize, or abridge any part of the body. Write the result to ${outFile} in EXACTLY this format (literal sentinel lines): @@QD_TITLE@@ then the English title on one line, @@QD_EXCERPT@@ then the English excerpt on one line (blank line if none), @@QD_BODY@@ then the full translated markdown body. Write nothing else to that file and do not read or write any other files.`;

  const t0 = Date.now();
  console.log(`… ${id}: drafting with Kimi (${src.body.length} chars)`);
  const res = await runKimi(instruction);

  if (!fs.existsSync(outFile)) { noteFailure(id, 'kimi produced no output file', res); return; }
  const problem = validate(fs.readFileSync(outFile, 'utf8'), src);
  if (problem) { fs.unlinkSync(outFile); noteFailure(id, problem, res); return; }
  console.log(`✓ ${id}: drafted in ${Math.round((Date.now() - t0) / 1000)}s`);
  drafted++; consecutiveFailures = 0;
}

// Fixed-size worker pool over the id list.
const queue = ids.slice();
await Promise.all(
  Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
    while (queue.length && !abandoned) await draftOne(queue.shift());
  }),
);

console.log(`\nDrafted ${drafted}, skipped ${skippedExisting} (existing)${skippedNoSource ? ` + ${skippedNoSource} (no source)` : ''}, failed ${failed}${abandoned ? ' — Kimi abandoned, rest fall back to Sonnet' : ''}.`);

// Machine-readable manifest for the workflow's args.draftedIds: recomputed from
// disk so it reflects exactly what is usable right now, never a stale draft.
const draftedIds = ids.filter((id) => {
  const f = path.join(DRAFTS, `${id}.txt`);
  const s = path.join(IN, `${id}.json`);
  if (!fs.existsSync(f) || !fs.existsSync(s)) return false;
  return !validate(fs.readFileSync(f, 'utf8'), JSON.parse(fs.readFileSync(s, 'utf8')));
});
console.log(`DRAFTED_IDS=${JSON.stringify(draftedIds)}`);

// Exit 0 even when Kimi failed: falling back to Sonnet is a normal, healthy
// outcome, and the cron treats a non-zero exit as a batch failure.
process.exit(0);
