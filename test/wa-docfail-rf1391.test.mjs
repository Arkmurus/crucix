// test/wa-docfail-rf1391.test.mjs
//
// Capability test for R-F1391/R-F1392/R-F1393 — the wrong-document P0 of
// 2026-06-07: "CIS of VCR S.L_.pdf" failed extraction (brain 503), the failed
// doc never entered the recent-doc cache, and the operator's follow-up
// ("Aria can you make a full investigation on these companies mentioned on
// this document?") re-attached the PREVIOUS day's ATNA NDA with a
// MUST-review-verbatim instruction — ARIA confidently reviewed the WRONG
// document.
//
// Per repo convention (wa-doc-followup-rf854) the listener is the container
// entrypoint and cannot be imported; we use static-source guards PLUS runtime
// checks of the REAL cache/follow-up functions extracted from source.
//
// Run: node test/wa-docfail-rf1391.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
  'utf8',
);

let failures = 0;
function check(label, cond) {
  if (cond) { console.log(`  ✓ ${label}`); }
  else { console.log(`  ✗ ${label}`); failures++; }
}

console.log('R-F1391/92/93 — failed-read marker, store-blip tolerance, POST retry\n');

// ── Static-source wiring ─────────────────────────────────────────────────────
console.log('wiring (services/wa-listener/aria_wa_listener.mjs):');
check('R-F1391: defines _cacheFailedDocRead',
  /function _cacheFailedDocRead\(chatId, senderName, filename, error\)/.test(SRC));
check('R-F1391: failure branch records the failed read',
  /_cacheFailedDocRead\(chatId, senderName, filename, _docErr\)/.test(SRC));
check('R-F1391: follow-up lookup gives the newest FAILED doc dominance',
  /if \(newest\.failed\)/.test(SRC));
check('R-F1391: mention path attaches a DOCUMENT READ FAILURE block, never the stale doc',
  /DOCUMENT READ FAILURE/.test(SRC) && /MUST NOT review, summarise, or answer from any OTHER document/.test(SRC));
check('R-F1392: doc poll tolerates transient not_found (3-consecutive streak)',
  /notFoundStreak/.test(SRC) && /\+\+notFoundStreak >= 3\) throw new Error\('extraction job expired'\)/.test(SRC));
check('R-F1392: chat poll tolerates transient not_found (3-consecutive streak)',
  /\+\+notFoundStreak >= 3\) throw new Error\('chat job expired'\)/.test(SRC));
check('R-F1393: read-document POST retried up to 3 attempts',
  /R-F1393 read-document POST attempt \$\{attempt \+ 1\}\/3 failed/.test(SRC));
check('R-F1393: falls back to ONE sync-mode read after retries exhausted',
  /trying sync mode once/.test(SRC));
check('R-F1393: non-retryable (auth/4xx) errors are NOT retried',
  /if \(e\.retryable === false\) throw e;/.test(SRC));

// ── Runtime: extract the real cache + follow-up functions from source ───────
function extractFn(name) {
  const start = SRC.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found in source`);
  let i = SRC.indexOf('{', start), depth = 0;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}') { depth--; if (depth === 0) break; }
  }
  return SRC.slice(start, i + 1);
}
function extractRegexConst(name) {
  const m = SRC.match(new RegExp(`const ${name} = (\\/.*?\\/[a-z]*);`));
  if (!m) throw new Error(`const ${name} not found in source`);
  return m[1];
}

const api = new Function(`
  const _RECENT_DOC_TTL_MS = 24 * 60 * 60 * 1000;
  const _MAX_DOCS_PER_CHAT = 6;
  const _recentDocs = new Map();
  function _persistRecentDocs() {}
  const _DOC_REF_PATTERN = ${extractRegexConst('_DOC_REF_PATTERN')};
  const _MULTI_DOC_PATTERN = ${extractRegexConst('_MULTI_DOC_PATTERN')};
  ${extractFn('_pruneChatDocs')}
  ${extractFn('_cacheRecentDoc')}
  ${extractFn('_cacheFailedDocRead')}
  ${extractFn('_recentDocsForFollowup')}
  return { _recentDocs, _cacheRecentDoc, _cacheFailedDocRead, _recentDocsForFollowup };
`)();

console.log('\nruntime (REAL functions extracted from source):');
const CHAT = 'group@g.us';
const OPERATOR_Q = 'Aria can you make a full investigation on these companies mentioned on this document?';

// Reproduce the live 2026-06-07 state: yesterday's NDA read OK, today's CIS failed.
api._cacheRecentDoc(CHAT, 'Antonio', 'ATNA Systems - unsigned.pdf', 'NDA BODY '.repeat(50));
api._cacheFailedDocRead(CHAT, 'Antonio', 'CIS of VCR S.L_.pdf', 'Brain error (503) on /api/aria/read-document');

const docs = api._recentDocsForFollowup(CHAT, OPERATOR_Q);
check('operator’s exact follow-up returns ONLY the failed marker (no stale NDA)',
  docs.length === 1 && docs[0].failed === true && docs[0].filename === 'CIS of VCR S.L_.pdf');
check('failure marker carries the error for the honesty block',
  /503/.test(docs[0].error));

// Escape hatch: explicitly naming the OLDER successfully-read doc still works.
const named = api._recentDocsForFollowup(CHAT, 'review the ATNA agreement again');
check('explicitly naming the older OK doc still attaches it',
  named.length === 1 && !named[0].failed && named[0].filename === 'ATNA Systems - unsigned.pdf');

// A successful re-send of the same filename replaces the failure marker.
api._cacheRecentDoc(CHAT, 'Antonio', 'CIS of VCR S.L_.pdf', 'CIS BODY '.repeat(50));
const after = api._recentDocsForFollowup(CHAT, OPERATOR_Q);
check('successful re-send replaces the marker (follow-up gets the real doc)',
  after.length >= 1 && after.every(d => !d.failed)
  && after.some(d => d.filename === 'CIS of VCR S.L_.pdf' && d.text.length > 200));

// Only a failed doc in the chat → the marker is still surfaced (not []).
const CHAT2 = 'solo@g.us';
api._cacheFailedDocRead(CHAT2, 'Antonio', 'lonely.pdf', 'timeout');
const solo = api._recentDocsForFollowup(CHAT2, 'review this document please');
check('single failed doc surfaces the marker',
  solo.length === 1 && solo[0].failed === true);

console.log(failures ? `\n${failures} check(s) FAILED` : '\nALL CHECKS PASSED');
process.exit(failures ? 1 : 0);
