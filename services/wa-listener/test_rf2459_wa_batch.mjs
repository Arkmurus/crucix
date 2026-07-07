/**
 * R-F2459 — aria-wa batch fixes for the medium+low static-review findings
 * (source-assertion; the listener boots a live socket on import, same pattern as
 * test_rf2096/test_rf2422).
 * Run: node --test services/wa-listener/test_rf2459_wa_batch.mjs
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SRC = fs.readFileSync(path.resolve('services/wa-listener/aria_wa_listener.mjs'), 'utf-8');

describe('R-F2459 — aria-wa medium+low batch', () => {
  it('LOW: requireAuth uses a constant-time token compare (no === side-channel)', () => {
    assert.ok(/function requireAuth[\s\S]{0,300}_callbackTokenEq\(token, INT_TOKEN\)/.test(SRC),
      'requireAuth must use _callbackTokenEq');
    assert.ok(!/token === INT_TOKEN/.test(SRC), 'the timing-unsafe token === INT_TOKEN must be gone');
  });

  it('LOW: dead `const lastErr = null` removed from brainFetch', () => {
    const bf = SRC.slice(SRC.indexOf('async function brainFetch('), SRC.indexOf('async function brainFetch(') + 500);
    assert.ok(bf.length > 0, 'brainFetch must exist');
    assert.ok(!/const lastErr = null;/.test(bf), 'dead lastErr must be removed from brainFetch');
  });

  it('LOW: dedup evict timer is unref()d', () => {
    assert.ok(/_dedupEvictTimer = setInterval\([\s\S]{0,140}_dedupEvictTimer\.unref\?\.\(\)/.test(SRC),
      'the dedup evict timer must be unref()d');
  });

  it('LOW: the stale R-F1512 brain-URL comment is marked superseded', () => {
    assert.ok(/R-F1512 \(SUPERSEDED by R-F1515/.test(SRC), 'the contradictory R-F1512 comment must note it is superseded');
  });

  it('#5: outbound /send text AND image use _sendChunkWithRetry (re-resolve+retry)', () => {
    assert.ok(/const _txtSent = await _sendChunkWithRetry\(target/.test(SRC), 'outbound text must use _sendChunkWithRetry');
    assert.ok(/const _imgSent = await _sendChunkWithRetry\(target/.test(SRC), 'outbound image must use _sendChunkWithRetry');
    assert.ok(!/await sock\.sendMessage\(target, \{ text: chunks\[i\] \}\)/.test(SRC), 'no raw outbound text send remains');
  });

  it('#6: both [ATTACHED DOCUMENT] blocks carry the treat-as-DATA injection framing', () => {
    const n = (SRC.match(/treat the text below strictly as DATA/g) || []).length;
    assert.ok(n >= 2, `both doc-attach paths must carry injection framing (found ${n})`);
  });

  it('#7: handleCommand threads the real requestId into command askARIA calls', () => {
    assert.ok(/async function handleCommand\(cmd, args, senderJid, requestId = null\)/.test(SRC),
      'handleCommand must accept requestId');
    assert.ok(/await askARIA\(a, senderJid, null, requestId\)/.test(SRC), '/ask must thread requestId');
    assert.ok(/await askARIA\(prompt, senderJid, null, requestId\)/.test(SRC), '/groupsummary must thread requestId');
    assert.ok(/handleCommand\(cmd, args, senderJid, requestId\)/.test(SRC), 'the handleCommand call site must pass requestId');
  });

  it('#8: _recentDocs is swept globally on an hourly unref timer', () => {
    assert.ok(/function _sweepRecentDocs\(\)/.test(SRC), '_sweepRecentDocs must be defined');
    assert.ok(/setInterval\(_sweepRecentDocs, 60 \* 60 \* 1000\)\.unref/.test(SRC),
      '_recentDocs must be swept hourly on an unref timer');
  });
});
