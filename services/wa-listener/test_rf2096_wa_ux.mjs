/**
 * R-F2096 — WA UX fixes from the 2026-06-28 full DD (source-assertion; the
 * listener boots a live socket on import so handlers aren't unit-invokable).
 *   1. Image+mention success no longer falls through to a duplicate ungrounded reply.
 *   2. Voice transcription failure is no longer a silent drop (§25): reports to the
 *      brain always, replies to the user only in always-reply mode (R-F2061).
 * Run: node --test services/wa-listener/test_rf2096_wa_ux.mjs
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SRC = fs.readFileSync(path.resolve('services/wa-listener/aria_wa_listener.mjs'), 'utf-8');

describe('R-F2096 — WA image double-reply + voice §25', () => {
  it('image success path continues (no fall-through to the mention handler)', () => {
    // The _handleOcrResult call must be immediately followed by a continue.
    assert.ok(/_handleOcrResult\([^;]*\);\s*(\/\/[^\n]*\n\s*)*continue;/.test(SRC),
      'the image success path must `continue` after _handleOcrResult');
  });
  it('_reportVoiceFailure is defined and emits the wa_voice_failed brain signal', () => {
    assert.ok(/async function _reportVoiceFailure\(/.test(SRC), '_reportVoiceFailure must be defined');
    const fn = SRC.slice(SRC.indexOf('async function _reportVoiceFailure('));
    assert.ok(/signal_type:\s*'wa_voice_failed'/.test(fn.slice(0, 600)),
      '_reportVoiceFailure must emit signal_type wa_voice_failed');
    assert.ok(/if \(VOICE_ALWAYS_REPLY\)/.test(fn.slice(0, 900)),
      'user reply must be gated on VOICE_ALWAYS_REPLY (R-F2061)');
  });
  it('both voice failure branches call _reportVoiceFailure (no silent drop)', () => {
    // The else (transcription failed) and the catch (exception) branches.
    const count = (SRC.match(/_reportVoiceFailure\(groupName, chatId,/g) || []).length;
    assert.ok(count >= 2, `both voice failure branches must report (found ${count})`);
  });
});
