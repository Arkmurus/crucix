// services/wa-listener/send-retry.mjs
//
// R-F2069 — bounded retry+backoff for a SINGLE WhatsApp chunk send.
//
// Extracted from aria_wa_listener.mjs as a standalone, dependency-free module so
// it can be UNIT/CAPABILITY tested with a fake socket. The listener itself can't
// be imported in a test (it self-starts: connects to WhatsApp + binds a port), so
// every prior listener test is structural-only. This module makes the actual
// retry BEHAVIOUR provable — see test/wa-send-retry-rf2069.test.mjs.
//
// Why it exists: a transient WhatsApp send failure (brief disconnect mid-send, a
// socket swapped by a reconnect, a momentary rate-limit) used to PERMANENTLY drop
// the reply — the caller caught the throw, reported send_failed, and gave up. We
// now re-resolve the live socket on EVERY attempt (a reconnect reassigns the
// socket, so re-reading it picks up the new one) and retry with backoff before
// declaring failure.

export const SEND_RETRY_BACKOFFS_MS = [400, 1200, 3000];   // 3 retries after the initial try

// Send one chunk with bounded retry.
//   chatId       — destination jid
//   content      — passed straight to sock.sendMessage (e.g. { text } or { image, caption })
//   resolveSock  — () => ({ sock, connected }). Re-called each attempt. When
//                  connected===false we wait out the backoff (giving a reconnect a
//                  chance) instead of hammering a dead socket. A missing sock is
//                  treated the same way.
//   opts.backoffs      — override the backoff schedule (tests inject [] / short delays)
//   opts.sleep         — override the delay fn (tests inject a no-op)
//   opts.onAttemptFail — (attemptNo, totalAttempts, err) => void, for logging
//
// Per-chunk by design: the caller loops over chunks, so an already-sent chunk is
// never re-sent on a later chunk's failure → no duplicate WhatsApp messages.
// Returns the sendMessage result on success; throws the last error only AFTER all
// attempts are exhausted (never silently swallows).
export async function sendChunkWithRetry(chatId, content, resolveSock, opts = {}) {
  const backoffs = opts.backoffs || SEND_RETRY_BACKOFFS_MS;
  const sleep = opts.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const onAttemptFail = opts.onAttemptFail || (() => {});
  let lastErr;
  for (let attempt = 0; attempt <= backoffs.length; attempt++) {
    if (attempt > 0) await sleep(backoffs[attempt - 1]);
    const { sock, connected } = resolveSock() || {};
    if (!sock || connected === false) {
      lastErr = lastErr || new Error('socket not connected');
      continue;
    }
    try {
      return await sock.sendMessage(chatId, content);
    } catch (e) {
      lastErr = e;
      onAttemptFail(attempt + 1, backoffs.length + 1, e);
    }
  }
  throw lastErr || new Error('send failed after retries');
}
