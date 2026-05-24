// test/wa-doc-followup-rf853.test.mjs
//
// Capability test for R-F853 — WhatsApp document follow-up injection in
// lib/whatsapp/waListener.mjs.
//
// Symptom (operator, 2026-05-24, 4+ attempts): "Aria, do an analysis of this
// contract" on WhatsApp → ARIA: "no document text in my context." The contract
// was sent as one message; the question came in a SEPARATE text message that
// carried no documentMessage, so the attach branch (if _docMsg || _imgMsg)
// never fired and ARIA honestly reported no document.
//
// Fix: symmetric to the existing image-OCR buffer — when a text follow-up
// references a document AND a recent extracted document exists from the same
// sender, inject it as an [ATTACHED DOCUMENT] block.
//
// Run: node test/wa-doc-followup-rf853.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { findRecentDocForReference } from '../lib/whatsapp/waListener.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'lib', 'whatsapp', 'waListener.mjs'),
  'utf8',
);

let failures = 0;
function check(label, cond) {
  if (cond) { console.log(`  ✓ ${label}`); }
  else { console.log(`  ✗ ${label}`); failures++; }
}

const CHAT = 'group@g.us';
const SENDER = 'Antonio';
const CONTRACT = '[Document: supply_contract.pdf]\n' + 'PAYMENT TERMS: 50% on signature, 50% on delivery. '.repeat(20);

console.log('R-F853 — recent-document injection for WhatsApp text follow-ups\n');

// ── Runtime: the exported pure helper ─────────────────────────────────────
console.log('findRecentDocForReference():');

check('returns the recent contract when the follow-up references "this contract"',
  findRecentDocForReference(
    [{ groupId: CHAT, senderName: SENDER, text: CONTRACT }],
    CHAT, SENDER, 'Aria, analyse this contract and its payment structure',
  ) === CONTRACT);

check('true negative: plain greeting with a stored doc → null',
  findRecentDocForReference(
    [{ groupId: CHAT, senderName: SENDER, text: CONTRACT }],
    CHAT, SENDER, 'are you online?',
  ) === null);

check('returns null when no document was shared by this sender',
  findRecentDocForReference(
    [{ groupId: CHAT, senderName: SENDER, text: 'just a normal chat line' }],
    CHAT, SENDER, 'analyse this contract',
  ) === null);

check('ignores a "Document shared:" placeholder (no real extracted text)',
  findRecentDocForReference(
    [{ groupId: CHAT, senderName: SENDER, text: '[Document: x.pdf] Document shared: x.pdf' }],
    CHAT, SENDER, 'review this agreement',
  ) === null);

check('scopes to the same sender (does not leak another sender’s doc)',
  findRecentDocForReference(
    [{ groupId: CHAT, senderName: 'SomeoneElse', text: CONTRACT }],
    CHAT, SENDER, 'analyse this contract',
  ) === null);

// ── Static-source: the fix is wired into the handler correctly ────────────
console.log('\nwaListener.mjs wiring:');

check('exports findRecentDocForReference', /export function findRecentDocForReference/.test(SRC));
check('handler calls the helper with messageStore + sender + text',
  /findRecentDocForReference\(messageStore, chatId, senderName, text\)/.test(SRC));
check('injection only fires when no attachment already present (!attachedBlock guard)',
  /if \(!attachedBlock\) \{\s*\n\s*const _recentDocText = findRecentDocForReference/.test(SRC));
check('injected block uses the [ATTACHED DOCUMENT] envelope',
  /\[ATTACHED DOCUMENT — recently shared by \$\{senderName\}/.test(SRC));
check('restamps the partial-extraction banner when the stored doc exceeds MAX_DOC_CHARS',
  /_docBody\.length > MAX_DOC_CHARS[\s\S]{0,200}stampPartialExtraction/.test(SRC));
check('doc-injection is placed AFTER the image-OCR buffer block (symmetry)',
  SRC.indexOf('_consumeOCRBuffer(chatId)') < SRC.indexOf('findRecentDocForReference(messageStore'));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
