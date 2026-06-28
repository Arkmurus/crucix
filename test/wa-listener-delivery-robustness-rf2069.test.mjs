// test/wa-listener-delivery-robustness-rf2069.test.mjs
//
// Guard test for R-F2069 — WA delivery robustness + structural gate lock on the
// CANONICAL listener (services/wa-listener/aria_wa_listener.mjs, the Dockerfile.wa
// entry point).
//
// Two failure CLASSES this locks shut:
//
//   1. SILENT REPLY DROP. sendReply (and the R-F1413 async-callback delivery)
//      used to do a bare `sock.sendMessage()` in the chunk loop: one transient
//      failure (brief disconnect mid-send, socket swapped by a reconnect, a
//      momentary rate-limit) was caught, reported send_failed, and the reply was
//      gone forever. R-F2069 routes every chunk through `_sendChunkWithRetry`,
//      which re-resolves the live socket and retries with backoff. This test
//      asserts BOTH delivery paths use it, so a future edit can't reintroduce a
//      bare un-retried send.
//
//   2. UNINVITED MEDIA REVIEW (the R-F2061 regression). Every NEW media path must
//      gate on `_ariaCalled` (ARIA named in the caption) BEFORE downloading +
//      reviewing — otherwise she reviews shared photos/docs uninvited again. The
//      image and document download sites MUST be preceded by the gate. The voice
//      path is the DOCUMENTED exception (no caption → R-F963 implicit mention),
//      so it is gated by `audioMsg && !text.trim()` instead, which we also assert.
//
// And it locks the delivery-OUTCOME wires (§25 / R-F1965) that make a drop visible.
//
// Run: node test/wa-listener-delivery-robustness-rf2069.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
  'utf8',
);
const RETRY_SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'send-retry.mjs'),
  'utf8',
);

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// ── 1. The retry primitive is a real bounded-backoff loop (extracted module) ───
// Behaviour is proven separately in test/wa-send-retry-rf2069.test.mjs; here we
// lock its structure + that the listener actually imports it.
check('send-retry.mjs exports sendChunkWithRetry', RETRY_SRC.includes('export async function sendChunkWithRetry('));
check('retry uses a backoff schedule', RETRY_SRC.includes('SEND_RETRY_BACKOFFS_MS = ['));
check('retry loops over multiple attempts', /for \(let attempt = 0;/.test(RETRY_SRC));
check('retry re-resolves the socket each attempt (resolveSock call inside loop)', RETRY_SRC.includes('resolveSock()'));
check('retry throws only AFTER attempts are exhausted (no silent swallow)', RETRY_SRC.includes('throw lastErr'));
check('listener imports sendChunkWithRetry from ./send-retry.mjs',
  /import \{ sendChunkWithRetry \} from '\.\/send-retry\.mjs'/.test(SRC));

// ── 2. BOTH delivery paths route through the retry helper (no bare send) ───────
const sendReplyIdx = SRC.indexOf('async function sendReply(');
const sendReplyBody = SRC.slice(sendReplyIdx, SRC.indexOf('\n}', sendReplyIdx));
check('sendReply found', sendReplyIdx > -1);
check('sendReply chunk loop uses _sendChunkWithRetry (not bare sock.sendMessage)',
  sendReplyBody.includes('_sendChunkWithRetry(') &&
  !/await _s\.sendMessage\(chatId, \{ text: chunks\[i\] \}\)/.test(sendReplyBody));

const callbackIdx = SRC.indexOf("app.post('/api/wa-listener/callback'");
const callbackBody = SRC.slice(callbackIdx, SRC.indexOf('\n});', callbackIdx));
check('R-F1413 async-callback delivery endpoint found', callbackIdx > -1);
check('callback delivery loop uses _sendChunkWithRetry (not bare _dsock.sendMessage)',
  callbackBody.includes('_sendChunkWithRetry(') &&
  !callbackBody.includes('_dsock.sendMessage('));

// ── 3. Mention-gate locked on the image + document download paths (R-F2061) ────
const imgIdx = SRC.indexOf('if (imgMsg) {');
const imgDownloadIdx = SRC.indexOf('downloadMediaMessage(', imgIdx);
check('image path entry found', imgIdx > -1 && imgDownloadIdx > imgIdx);
check('image path gates on _ariaCalled BEFORE downloadMediaMessage',
  SRC.slice(imgIdx, imgDownloadIdx).includes('if (!_ariaCalled) continue'));

const docIdx = SRC.indexOf('if (docMsg) {');
const docDownloadIdx = SRC.indexOf('downloadMediaMessage(', docIdx);
check('document path entry found', docIdx > -1 && docDownloadIdx > docIdx);
check('document path gates on _ariaCalled BEFORE downloadMediaMessage',
  SRC.slice(docIdx, docDownloadIdx).includes('if (!_ariaCalled) continue'));

// Voice path = documented exception (no caption → implicit mention, R-F963).
check('voice path entry-gated by (audioMsg && !text.trim()) — not a silent ungated review',
  SRC.includes('if (audioMsg && !text.trim())'));

// Defensive: if a NEW (4th) media download site is added, this count breaks and
// forces whoever adds it to add a gate assertion above (intentional friction).
const downloadCount = (SRC.match(/downloadMediaMessage\(/g) || []).length;
check(`exactly 3 known media-download sites (image/doc/voice) — got ${downloadCount}`,
  downloadCount === 3);

// ── 4. Delivery-OUTCOME wires intact (§25 / R-F1965) — a drop stays visible ────
check('sendReply still reports a real-answer outcome on success',
  sendReplyBody.includes("'delivered_real_answer'"));
check('sendReply still honours the R-F1965 failure-truth guard (_failedOutcomeReqIds)',
  sendReplyBody.includes('_failedOutcomeReqIds.has(requestId)'));
check('sendReply still reports send_failed on terminal failure',
  sendReplyBody.includes("'send_failed'"));

console.log(failures === 0 ? '\nR-F2069 tests: PASS' : `\nR-F2069 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
