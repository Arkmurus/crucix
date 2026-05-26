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
// R-F912 generalised the R-F854 per-(chat,sender) single-doc cache into a
// per-CHAT multi-doc list + group-aware lookup. Two live failures 2026-05-26:
// three uploads overwrote to one, and a group questioner (Ari) differed from
// the uploader (Antonio) so the sender-keyed lookup missed entirely.
console.log('wiring (services/wa-listener/aria_wa_listener.mjs):');
check('defines the recent-doc cache (_recentDocs Map)', /const _recentDocs = new Map\(\)/.test(SRC));
check('defines _cacheRecentDoc', /function _cacheRecentDoc\(chatId, senderName, filename, text\)/.test(SRC));
check('R-F912: defines _recentDocsForFollowup keyed by CHAT (not sender)',
  /function _recentDocsForFollowup\(chatId, question\)/.test(SRC));
check('R-F912: per-chat multi-doc list (cap _MAX_DOCS_PER_CHAT)',
  /_MAX_DOCS_PER_CHAT/.test(SRC) && /_recentDocs\.set\(chatId,/.test(SRC));
check('R-F912: collective reference pattern (_MULTI_DOC_PATTERN)',
  /_MULTI_DOC_PATTERN/.test(SRC));
// R-F862 refactored the inline cache call into a _cacheText intermediate (so a
// >8MB byte-truncated doc gets a PARTIAL EXTRACTION banner before caching).
// Assert the real flow: result.extracted_text → _cacheText → _cacheRecentDoc.
check('document path caches the extracted text (result.extracted_text → _cacheText → _cacheRecentDoc)',
  /_cacheText = \(result\.extracted_text/.test(SRC)
  && /_cacheRecentDoc\(chatId, senderName, filename, _cacheText\)/.test(SRC));
check('mention handler re-attaches via _recentDocsForFollowup',
  /_recentDocsForFollowup\(chatId, q\)/.test(SRC));
check('R-F912: re-attaches EACH doc in an [ATTACHED DOCUMENT] envelope with its uploader',
  /\[ATTACHED DOCUMENT — "\$\{_doc\.filename\}" recently shared by \$\{_doc\.sender\}/.test(SRC));
check('truncation past the budget prepends a PARTIAL EXTRACTION banner',
  /body\.length > budget[\s\S]{0,160}\[!PARTIAL EXTRACTION/.test(SRC));
check('cache ignores short/placeholder text (<200 chars guard)',
  /if \(!text \|\| text\.length < 200\) return;/.test(SRC));
check('has a TTL so a stale doc is not re-attached forever (pruned by _pruneChatDocs)',
  /_RECENT_DOC_TTL_MS/.test(SRC) && /d\.ts >= cutoff/.test(SRC));

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

// ── Runtime: _MULTI_DOC_PATTERN — the live "analyse all contracts" case ──────
console.log('\n_MULTI_DOC_PATTERN (extracted from source):');
const mm = SRC.match(/_MULTI_DOC_PATTERN = (\/[\s\S]*?\/[gimsuy]*)\s*;/);
check('pattern literal found in source', !!mm);
if (mm) {
  // eslint-disable-next-line no-eval
  const MULTI = eval(mm[1]);
  check('matches "analysis of all contracts" → attach ALL', MULTI.test('give me your analysis of all contracts'));
  check('matches "both agreements"', MULTI.test('compare both agreements'));
  check('matches "review the documents"', MULTI.test('review the documents for red flags'));
  check('does NOT match a singular "this agreement"', !MULTI.test('what is the term of this agreement'));
}

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
