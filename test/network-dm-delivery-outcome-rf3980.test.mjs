// R-F3980 / C-69 — the ARIA Network DM reply was the one web chat surface with
// no delivery-outcome wire.
//
// §25 requires every surface that produces a result for a user to report whether
// the intended result was actually produced. `_ariaChannelReply` produces ARIA's
// answer inside the Network DM thread and reported nothing on any path:
//
//   server.mjs:8648   catch (e) { console.warn('[network] ARIA reply failed:'...) }
//                     -> user gets "⚠️ I could not reach my analysis engine"
//   server.mjs:8646   || 'I could not produce a reply just now — try rephrasing?'
//                     -> an EMPTY brain result rendered as a polite non-answer
//   server.mjs:8721   .catch(e => console.warn('[network] ARIA channel reply failed'))
//                     -> total failure; the user gets NOTHING at all
//
// §21b is explicit that console logging is DARK, not wired. So the brain could
// not tell a working Network DM from one answering every user with an apology,
// and the §25 self-heal loop had nothing to act on.
//
// Everything needed was already present and simply not called:
//   * `reportOutcome(surface, requestId, intendedResult, actualOutcome,
//      latencyMs, detail)` — server.mjs:3437, the §25 poster (retries once,
//      fire-and-forget, never throws)
//   * `classifyDeliveryOutcome(result)` / `degradedDetail(result)` —
//      lib/aria/deliveryOutcome.mjs, imported at server.mjs:49
//
// R-F1965 built that classifier precisely because a DEGRADED brain answer comes
// back as HTTP 200 and reads like a success; the web chat path already uses it
// at server.mjs:5029. Reusing it here rather than inventing a second
// classification is the point — two would drift.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { classifyDeliveryOutcome, degradedDetail } from '../lib/aria/deliveryOutcome.mjs';

function ariaChannelReplySource() {
  const server = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  const start = server.indexOf('async function _ariaChannelReply(');
  assert.ok(start > -1, '_ariaChannelReply not found');
  const end = server.indexOf('\n  io.on(\'connection\'', start);
  assert.ok(end > start, 'could not bound _ariaChannelReply');
  return server.slice(start, end);
}

test('R-F3980: the Network DM reply reports a delivery outcome at all', () => {
  const fn = ariaChannelReplySource();
  assert.match(
    fn, /reportOutcome\(/,
    'the Network DM reply still reports nothing — the brain cannot tell a '
    + 'working surface from one answering every user with an apology',
  );
});

test('R-F3980: it reuses the shared §25 classifier, not a second one', () => {
  const fn = ariaChannelReplySource();
  assert.match(
    fn, /classifyDeliveryOutcome\(/,
    'a DEGRADED brain answer returns HTTP 200 and reads like success (R-F1965); '
    + 'without the shared classifier this surface would log those as delivered',
  );
});

test('R-F3980: the failure path reports an error, not silence', () => {
  const fn = ariaChannelReplySource();
  const catchIdx = fn.indexOf('catch (e)');
  assert.ok(catchIdx > -1, 'the catch block moved');
  const catchBlock = fn.slice(catchIdx, catchIdx + 600);
  assert.match(
    catchBlock, /reportOutcome\(/,
    'the catch still only console.warns — the user is shown "I could not reach '
    + 'my analysis engine" while the brain learns nothing',
  );
});

test('R-F3980: an empty brain result is not reported as a delivered answer', () => {
  const fn = ariaChannelReplySource();
  // The polite non-answer fallback must not be classified as success.
  assert.match(
    fn, /empty_response|no_answer/,
    'an empty result still renders as "I could not produce a reply just now" '
    + 'and is reported (or not reported) as if it were an answer',
  );
});

test('R-F3980: the outer .catch — total failure, user gets nothing — is wired', () => {
  const server = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  const idx = server.indexOf('_ariaChannelReply(uid, safeText)');
  assert.ok(idx > -1, 'the call site moved');
  const block = server.slice(idx, idx + 500);
  assert.match(
    block, /reportOutcome\(/,
    'when _ariaChannelReply itself rejects the user receives NOTHING and the '
    + 'brain is told nothing — the worst of the three paths',
  );
});

// ── the classifier contract this leans on (R-F1965), re-pinned here ──────────

test('R-F3980: a degraded result is not a delivered answer', () => {
  assert.equal(classifyDeliveryOutcome({ response: 'x', degraded: true,
                                         degradation_reason: 'llm_down' }), 'error');
  assert.equal(classifyDeliveryOutcome({ response: 'x', llm_failure: true,
                                         llm_error_kind: 'timeout' }), 'timeout_fallback');
  assert.equal(classifyDeliveryOutcome({ response: 'a real answer' }),
               'delivered_real_answer');
  assert.equal(degradedDetail({ response: 'x', degraded: true,
                                degradation_reason: 'llm_down' }), 'llm_down');
});
