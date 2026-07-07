/**
 * R-F2422 — aria-wa static-review fixes (source-assertion; the listener boots a
 * live socket on import so handlers aren't unit-invokable — same pattern as
 * test_rf2096_wa_ux.mjs).
 *   1. §25: askARIA timeout/error branch marks the rid failed so the holding/
 *      apology sendReply does NOT mask a non-answer as delivered_real_answer.
 *   2. dedup runs BEFORE the media dispatch (refired media not processed twice).
 *   3. the connection watchdog is re-armed on every startListener() (not once).
 * Run: node --test services/wa-listener/test_rf2422_wa_fixes.mjs
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SRC = fs.readFileSync(path.resolve('services/wa-listener/aria_wa_listener.mjs'), 'utf-8');

describe('R-F2422 — aria-wa §25 masking + dedup order + watchdog re-arm', () => {
  it('1. askARIA timeout/error branch calls _markFailedOutcome(rid) after reporting the failure', () => {
    const askAria = SRC.slice(
      SRC.indexOf('async function askARIA('),
      SRC.indexOf('async function askARIAAsync('),
    );
    assert.ok(askAria.length > 0, 'askARIA function must exist before askARIAAsync');
    assert.ok(
      /reportOutcome\('wa', rid, 'chat_response', outcome[\s\S]{0,500}_markFailedOutcome\(rid\)/.test(askAria),
      'askARIA must call _markFailedOutcome(rid) after reporting the timeout/error outcome',
    );
  });

  it('2. dedup gate runs BEFORE the media dispatch, and only once', () => {
    const dedupIdx = SRC.indexOf('if (_isDuplicateMessage(chatId, senderJid, msg.messageTimestamp))');
    const mediaIdx = SRC.indexOf('const docMsg = msg.message?.documentMessage');
    assert.ok(dedupIdx > -1, 'the in-loop dedup check must exist');
    assert.ok(mediaIdx > -1, 'the media dispatch (docMsg) must exist');
    assert.ok(dedupIdx < mediaIdx, 'dedup must run BEFORE the media dispatch');
    const calls = (SRC.match(/if \(_isDuplicateMessage\(chatId, senderJid, msg\.messageTimestamp\)\)/g) || []).length;
    assert.equal(calls, 1, `dedup must be MOVED, not duplicated (found ${calls} in-loop checks)`);
  });

  it('3. _startWatchdog() is called at the end of startListener (re-armed on every start)', () => {
    assert.ok(
      /onMessagesUpsert\(sock, null, ev\)\);\s*(?:\/\/[^\n]*\n\s*)*_startWatchdog\(\);/.test(SRC),
      'startListener must call _startWatchdog() at its end',
    );
    const armCalls = (SRC.match(/_startWatchdog\(\);/g) || []).length;
    assert.ok(armCalls >= 2, `watchdog must be armed at boot AND in startListener (found ${armCalls} calls)`);
  });
});
