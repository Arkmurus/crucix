// The WA /status command must be gated on ROLE, not merely on being allowed to
// talk to ARIA — and a refused user must be told, not soothed.
//
// SOURCE-CONTRACT tests, deliberately. aria_wa_listener.mjs opens a Baileys
// socket and an Express server at import time, so it cannot be imported in a
// unit test; the repo already uses this shape (test/prospector-360-rf3651-*).
// The behaviour itself lives in lib/whatsapp/waCapability.mjs and is unit-tested
// in test/wa-capability-policy.test.mjs — what is asserted HERE is that the
// listener actually calls it, which is the half that source review keeps missing.

import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WA = fs.readFileSync(path.join(ROOT, 'services/wa-listener/aria_wa_listener.mjs'), 'utf8');

/** Body of a `case 'x': { ... }` block, by brace matching. */
function caseBlock(src, label) {
  const start = src.indexOf(`case '${label}': {`);
  assert.notEqual(start, -1, `case '${label}' not found`);
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unterminated case '${label}'`);
}

describe('/status is admin-gated', () => {
  const body = caseBlock(WA, 'status');

  it('asks the shared policy, not a local re-implementation', () => {
    assert.match(body, /maySeeSystemInternals\(\s*_waRole\(/,
      'must gate via waCapability.maySeeSystemInternals(_waRole(...)) — a second '
      + 'copy of the rule is a second rule (the waBinding/waGovernance doctrine)');
  });

  it('refuses before reading any state', () => {
    const gate = body.indexOf('maySeeSystemInternals');
    const firstRead = Math.min(
      ...['messagesHeard', 'brainGet(', 'messageStore']
        .map((t) => { const i = body.indexOf(t); return i === -1 ? Number.MAX_SAFE_INTEGER : i; }),
    );
    assert.ok(gate < firstRead,
      'the role check must run BEFORE any system state is gathered, so a refused '
      + 'sender cannot cause internals to be read at all');
  });

  it('declines honestly instead of returning a vague status', () => {
    assert.match(body, /restricted to administrators/i);
    assert.match(body, /not going to give you a vague answer/i,
      'an ordinary user must be refused, not handed a softened summary — a '
      + 'fabricated "all good" is the dishonesty this refusal exists to avoid');
  });

  it('reports the brain as unreachable rather than omitting it', () => {
    assert.match(body, /UNREACHABLE/,
      'a status that silently drops the half it could not read is worse than one '
      + 'that reports the gap');
  });

  it('reports measured values, not a hardcoded verdict', () => {
    for (const probe of ['isConnected', 'messagesHeard', 'messageStore.length', 'brainGet(']) {
      assert.ok(body.includes(probe), `status must read ${probe} live`);
    }
  });
});

describe('admin identity is bound-account based and fails closed', () => {
  it('_waRole resolves through the bound account, never the phone number', () => {
    assert.match(WA, /function _waRole\([^)]*\)\s*\{\s*return roleForBinding\(_waBoundUser\(/,
      '_waRole must derive from _waBoundUser (imaria.io account), because a '
      + 'handset can be lent, spoofed or re-issued and an account cannot');
  });

  it('the admin list is opt-in — unset grants nobody', () => {
    assert.match(WA, /ARIA_WA_ADMIN_USER_IDS\s*=\s*\(process\.env\.ARIA_WA_ADMIN_USER_IDS \|\| ''\)/,
      'defaults to empty so no one is admin until the operator says so');
  });
});

describe('/status is advertised only to those who may run it', () => {
  it('help appends the admin section behind the same gate', () => {
    const help = WA.slice(WA.indexOf("case 'help':"), WA.indexOf("case 'help':") + 2500);
    assert.match(help, /maySeeSystemInternals\(_waRole\(senderJid\)\)/,
      'listing /status to everyone invites a refusal that reads as a malfunction');
    assert.match(help, /\/status —/);
  });
});
