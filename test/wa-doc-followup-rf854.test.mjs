// test/wa-doc-followup-rf854.test.mjs
//
// Capability test for R-F854 — recent-document re-attach in the CANONICAL
// WhatsApp listener: services/wa-listener/aria_wa_listener.mjs (the file the
// aria-wa Fly app actually runs; R-F853 mistakenly patched the legacy
// lib/whatsapp/waListener.mjs which Dockerfile.wa does not copy).
//
// Symptom (operator, 2026-05-24): "Aria, analyse this contract" on WhatsApp →
// "no document in my context." Root cause in the canonical listener: a shared
// document is POSTed to /api/aria/read-document (brain absorbs facts) but the
// extracted text is NOT kept locally, so a follow-up MENTION calls
// /api/aria/chat with no [ATTACHED DOCUMENT] block. Fix: cache the extracted
// text per-sender on read, re-attach it on a doc-referencing follow-up.
//
// The module is the container ENTRYPOINT (top-level app.listen + startListener)
// so it cannot be imported in a test without binding a port / opening a WA
// socket. Per the repo convention (see wa-listener-teardown-rf461), we use
// static-source guards on the real code PLUS a runtime check of the REAL
// _DOC_REF_PATTERN extracted from source.
//
// Run: node test/wa-doc-followup-rf854.test.mjs

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

console.log('R-F854 — canonical WA listener recent-document re-attach\n');

// ── Static-source: the cache + helpers + wiring are present in the real code ─
console.log('wiring (services/wa-listener/aria_wa_listener.mjs):');
check('defines the per-sender recent-doc cache (_recentDocs Map)', /const _recentDocs = new Map\(\)/.test(SRC));
check('defines _cacheRecentDoc', /function _cacheRecentDoc\(chatId, senderName, filename, text\)/.test(SRC));
check('defines _recentDocForFollowup', /function _recentDocForFollowup\(chatId, senderName, question\)/.test(SRC));
// R-F862 refactored the inline cache call into a _cacheText intermediate (so a
// >8MB byte-truncated doc gets a PARTIAL EXTRACTION banner before caching), so
// the old adjacency regex no longer matched. Assert the real flow:
// result.extracted_text → _cacheText → _cacheRecentDoc.
check('document path caches the extracted text (result.extracted_text → _cacheText → _cacheRecentDoc)',
  /_cacheText = \(result\.extracted_text/.test(SRC)
  && /_cacheRecentDoc\(chatId, senderName, filename, _cacheText\)/.test(SRC));
check('mention handler re-attaches via _recentDocForFollowup',
  /_recentDocForFollowup\(chatId, senderName, q\)/.test(SRC));
check('re-attached doc uses the [ATTACHED DOCUMENT] envelope',
  /\[ATTACHED DOCUMENT — "\$\{_doc\.filename\}" recently shared by \$\{senderName\}/.test(SRC));
check('truncation past MAX_DOC_CHARS prepends a PARTIAL EXTRACTION banner',
  /body\.length > MAX_DOC_CHARS[\s\S]{0,160}\[!PARTIAL EXTRACTION/.test(SRC));
check('cache ignores short/placeholder text (<200 chars guard)',
  /if \(!text \|\| text\.length < 200\) return;/.test(SRC));
check('has a TTL so a stale doc is not re-attached forever',
  /_RECENT_DOC_TTL_MS/.test(SRC) && /Date\.now\(\) - entry\.ts > _RECENT_DOC_TTL_MS/.test(SRC));

// ── Runtime: exercise the REAL _DOC_REF_PATTERN extracted from source ────────
console.log('\n_DOC_REF_PATTERN (extracted from source):');
const m = SRC.match(/_DOC_REF_PATTERN = (\/[\s\S]*?\/[gimsuy]*)\s*;/);
check('pattern literal found in source', !!m);
if (m) {
  // eslint-disable-next-line no-eval
  const DOC_REF = eval(m[1]);
  check('matches "analyse this contract"', DOC_REF.test('analyse this contract and its payment structure'));
  check('matches "review the agreement"', DOC_REF.test('review the agreement please'));
  check('matches "is the payment safe"', DOC_REF.test('is the payment structure safe for both parties'));
  check('does NOT match a plain greeting', !DOC_REF.test('are you online?'));
  check('does NOT match an unrelated question', !DOC_REF.test('what is the capital of France'));
}

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
