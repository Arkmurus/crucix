// test/wa-send-retry-rf2069.test.mjs
//
// CAPABILITY test for R-F2069 — proves the WhatsApp send-retry actually behaves:
// it invokes the real sendChunkWithRetry (the path that was silently dropping
// replies) with a fake socket and asserts the user-visible outcomes:
//   1. a transient failure (throw once, then succeed) → the reply STILL delivers
//   2. it never double-sends a chunk that already went out
//   3. a permanent failure → throws only AFTER exhausting attempts (so the caller
//      reports send_failed, never a silent drop)
//   4. a socket that's down then comes back (reconnect) → delivers on the new sock
//   5. backoffs are injectable so the retry adds no real wall-clock in tests
//
// Run: node test/wa-send-retry-rf2069.test.mjs

import { sendChunkWithRetry, SEND_RETRY_BACKOFFS_MS } from '../services/wa-listener/send-retry.mjs';

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}
const NO_WAIT = { backoffs: [0, 0, 0], sleep: () => Promise.resolve() };

// 1 + 2. Transient failure then success — delivers exactly once (no duplicate).
{
  let calls = 0;
  const sock = { sendMessage: async () => { calls++; if (calls === 1) throw new Error('connection closed'); return { key: { id: 'm1' } }; } };
  const res = await sendChunkWithRetry('chat@x', { text: 'hi' }, () => ({ sock, connected: true }), NO_WAIT);
  check('transient failure still delivers (returns the sent message)', res && res.key && res.key.id === 'm1');
  check('delivered after exactly 2 attempts (1 retry, no extra sends)', calls === 2);
}

// 3. Permanent failure — throws AFTER all attempts (1 initial + 3 retries = 4).
{
  let calls = 0;
  const sock = { sendMessage: async () => { calls++; throw new Error('still down'); } };
  let threw = null;
  try {
    await sendChunkWithRetry('chat@x', { text: 'hi' }, () => ({ sock, connected: true }), NO_WAIT);
  } catch (e) { threw = e; }
  check('permanent failure throws (caller can report send_failed — not silent)', threw instanceof Error);
  check('exhausted all attempts before throwing (1 + 3 retries = 4 sends)', calls === SEND_RETRY_BACKOFFS_MS.length + 1);
}

// 4. Down, then a reconnect swaps in a live socket — re-resolved each attempt.
{
  let attempt = 0;
  const liveSock = { sendMessage: async () => ({ key: { id: 'm2' } }) };
  const resolve = () => {
    attempt++;
    // first attempt: no socket (disconnected); after the backoff: reconnected
    return attempt === 1 ? { sock: null, connected: false } : { sock: liveSock, connected: true };
  };
  const res = await sendChunkWithRetry('chat@x', { text: 'hi' }, resolve, NO_WAIT);
  check('delivers on the NEW socket after a reconnect (re-resolves each attempt)', res && res.key.id === 'm2');
}

// 5. Disconnected the whole time — never throws a confusing error, surfaces the
//    not-connected condition after exhausting attempts.
{
  let threw = null;
  try {
    await sendChunkWithRetry('chat@x', { text: 'hi' }, () => ({ sock: null, connected: false }), NO_WAIT);
  } catch (e) { threw = e; }
  check('persistently disconnected → throws after retries (caller sees failure)', threw instanceof Error);
}

console.log(failures === 0 ? '\nR-F2069 capability tests: PASS' : `\nR-F2069 capability tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
