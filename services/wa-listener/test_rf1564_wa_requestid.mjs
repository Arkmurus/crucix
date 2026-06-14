/**
 * R-F1564 — WA proprioception: requestId threaded through image/OCR + doc-overview
 * ═══════════════════════════════════════════════════════════════════════════
 * Verifies that the OCR/image result deliveries and the document-overview
 * deliveries now pass a `requestId` to sendReply, so reportOutcome fires for
 * those flows (§25 delivery-health). Pre-R-F1564 these sends had NO requestId,
 * so the `if (requestId)` guard inside sendReply skipped reportOutcome — WA
 * delivery health was blind on the operator's highest-pain flows (doc review,
 * image OCR).
 *
 * The message handlers boot the WA socket on import and are not unit-invokable
 * in isolation (they live inside startListener's messages.upsert closure with
 * live socket/Baileys deps), so — mirroring test_rf1551_resilience.mjs — these
 * are source-assertion tests that prove the requestId is now propagated and that
 * reportOutcome is reachable for these flows.
 */

import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SOURCE_PATH = path.resolve('services/wa-listener/aria_wa_listener.mjs');
let source = '';
try {
  source = fs.readFileSync(SOURCE_PATH, 'utf-8');
} catch (e) {
  console.error(`Cannot read source at ${SOURCE_PATH}: ${e.message}`);
  process.exit(1);
}

// ── helpers ────────────────────────────────────────────────────────────────
// Extract the body of a function declaration by brace-matching from its opening
// brace. Good enough for these single-definition functions.
function extractFunctionBody(src, signatureNeedle) {
  const start = src.indexOf(signatureNeedle);
  assert.ok(start !== -1, `could not locate "${signatureNeedle}"`);
  const braceStart = src.indexOf('{', start);
  assert.ok(braceStart !== -1, `no opening brace after "${signatureNeedle}"`);
  let depth = 0;
  for (let i = braceStart; i < src.length; i++) {
    const c = src[i];
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return src.slice(braceStart, i + 1);
    }
  }
  throw new Error(`unbalanced braces after "${signatureNeedle}"`);
}

describe('R-F1564 — WA requestId propagation for image/OCR + doc-overview', () => {

  it('reportOutcome is gated on requestId (precondition for the fix to matter)', () => {
    const body = extractFunctionBody(source, 'async function sendReply(');
    // The success + failure branches only report when requestId is present.
    assert.ok(/if\s*\(requestId\)\s*\{[\s\S]*?reportOutcome\([^)]*'delivered_real_answer'/.test(body),
      'sendReply success branch must call reportOutcome under if(requestId)');
    assert.ok(/if\s*\(requestId\)\s*\{[\s\S]*?reportOutcome\([^)]*'send_failed'/.test(body),
      'sendReply failure branch must call reportOutcome under if(requestId)');
  });

  it('_handleOcrResult accepts a requestId parameter', () => {
    const sigMatch = source.match(/async function _handleOcrResult\(([^)]*)\)/);
    assert.ok(sigMatch, '_handleOcrResult must exist');
    assert.ok(/\brequestId\b/.test(sigMatch[1]),
      '_handleOcrResult signature must include requestId so OCR deliveries can report');
  });

  it('every _handleOcrResult CALL site passes requestId through', () => {
    const calls = [...source.matchAll(/_handleOcrResult\(([^;]*?)\)\s*;/gs)]
      // skip the function definition itself (matched by "function _handleOcrResult")
      .filter(m => !source.slice(Math.max(0, m.index - 30), m.index).includes('function'));
    assert.ok(calls.length >= 1, 'expected at least one _handleOcrResult call site');
    for (const c of calls) {
      const args = c[1];
      assert.ok(/\brequestId\b/.test(args),
        `_handleOcrResult call must pass requestId; got args: ${args.trim().slice(0, 120)}`);
    }
  });

  it('OCR-result deliveries inside _handleOcrResult pass requestId (real answers)', () => {
    const body = extractFunctionBody(source, 'async function _handleOcrResult(');
    // The "Image read" extraction delivery and the "Analysis" delivery are the
    // real deliverables and must carry requestId.
    assert.ok(/sendReply\(\s*chatId\s*,\s*`🖼 \*Image read\*[\s\S]*?`\s*,\s*requestId\)/.test(body),
      'the OCR "Image read" extraction send must pass requestId');
    assert.ok(/sendReply\(\s*chatId\s*,\s*`🧠 \*Analysis:\*[\s\S]*?`\s*,\s*requestId\)/.test(body),
      'the OCR "Analysis" send (final answer) must pass requestId');
    // askARIA for the analysis must forward requestId too (so the async path
    // could correlate the outcome).
    assert.ok(/askARIA\(analysisPrompt,\s*senderJid,\s*chatId,\s*requestId\)/.test(body),
      'image-analysis askARIA must forward requestId');
  });

  it('the image-analysis interim "Analysing…" send is intentionally UNreported', () => {
    const body = extractFunctionBody(source, 'async function _handleOcrResult(');
    // It must NOT pass requestId, and must carry the documented exclusion note.
    assert.ok(/_Analysing the image content[\s\S]*?…_`\)\.catch/.test(body),
      'the interim "Analysing…" send must remain WITHOUT requestId');
    assert.ok(/interim\/progress ack[\s\S]*?WITHOUT requestId/i.test(body),
      'the interim "Analysing…" exclusion must be documented in a comment');
  });

  it('document-overview deliveries pass requestId', () => {
    // The four doc-result branches: inline caption review, overview, read-ok,
    // and no-text — all final answers for the doc flow.
    assert.ok(/sendReply\(chatId,\s*`🧠 \*ARIA — document overview\*[\s\S]*?`\s*\.slice\([^)]*\)\s*,\s*requestId\)/.test(source),
      'the document-overview send must pass requestId');
    assert.ok(/askARIA\(_reviewMsg,\s*senderJid,\s*chatId,\s*requestId\)/.test(source),
      'the inline doc+caption review askARIA must forward requestId');
    // The multi-part review delivery reports on the LAST chunk only.
    assert.ok(/_pi === _parts\.length - 1 \? requestId : undefined/.test(source),
      'the multi-part doc review must report the outcome on the final chunk only');
    // The "couldn't read it just now" doc-failure final answer reports too.
    assert.ok(/couldn't read it just now[\s\S]*?,\s*requestId\)\.catch/.test(source),
      'the doc read-failure final answer must pass requestId');
  });

  it('the WA per-message requestId is in scope for the media block', () => {
    // requestId is derived once per message (msg.key.id) BEFORE the media
    // (image/document) processing block — proving the same id scheme is reused.
    const ridDef = source.indexOf('const requestId = msg.key.id');
    const mediaBlock = source.indexOf('Media processing — IMAGES + DOCUMENTS');
    assert.ok(ridDef !== -1, 'per-message requestId must be derived from msg.key.id');
    assert.ok(mediaBlock !== -1, 'media-processing block must exist');
    assert.ok(ridDef < mediaBlock,
      'requestId must be defined before the media block so it is in scope there');
  });
});
