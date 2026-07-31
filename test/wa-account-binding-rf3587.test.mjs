// test/wa-account-binding-rf3587.test.mjs
//
// R-F3587 — phone ↔ account binding, so only a verified imaria.io user may
// engage ARIA on WhatsApp.
//
// Before this there was no notion of a verified WhatsApp user at all:
// lib/auth/conversationKey.mjs still refers to "a future phone-number login".
// R-F3586 gated every engagement path but could only check an operator-curated
// allow-list, which proves nothing about who is holding the handset.
//
// A pairing code proves BOTH directions at once — signed in to imaria.io to mint
// it, holding the handset to send it — and, as a side effect, settles the LID
// question R-F3582 exposed: the pairing MESSAGE is the identity evidence, so
// whatever identifiers WhatsApp attaches are recorded rather than guessed.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  PAIRING_TTL_MS,
  extractPairingCode,
  identitiesFromMessage,
  newBinding,
  newPairing,
  pairingState,
  publicBindingView,
  resolveBoundUser,
} from '../lib/whatsapp/waBinding.mjs';

const LISTENER = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
const SERVER = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const code = (src) => src.split(/\r?\n/).filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*')).join('\n');

// ── The policy ──────────────────────────────────────────────────────────────

test('R-F3587 a pairing code is single-use', () => {
  const { pairing } = newPairing({ userId: 'u1', code: '123456' });
  assert.equal(pairingState(pairing).valid, true);
  pairing.usedAt = new Date().toISOString();
  assert.equal(pairingState(pairing).code, 'already_used',
    'a replayed code would let anyone who saw it bind their own handset');
});

test('R-F3587 a pairing code expires', () => {
  const { pairing } = newPairing({ userId: 'u1', code: '123456' });
  assert.equal(pairingState(pairing, Date.now() + PAIRING_TTL_MS + 1).code, 'expired');
});

test('R-F3587 a malformed code is refused at issue time', () => {
  assert.equal(newPairing({ userId: 'u1', code: '12345' }).ok, false);
  assert.equal(newPairing({ userId: 'u1', code: 'abcdef' }).ok, false);
  assert.equal(newPairing({ userId: '', code: '123456' }).ok, false);
});

test('R-F3587 ordinary chat does not look like a pairing code', () => {
  // A looser pattern would let any 6-digit run in normal conversation start a
  // pairing attempt against every outstanding code.
  assert.equal(extractPairingCode('I paid 12345678 euros'), null);
  assert.equal(extractPairingCode('12345'), null);
  assert.equal(extractPairingCode('call me'), null);
  // …while the ways a person actually types it still work.
  assert.equal(extractPairingCode('123456'), '123456');
  assert.equal(extractPairingCode('ARIA link 445566'), '445566');
});

test('R-F3587 a binding matches on ANY identifier form (the LID lesson)', () => {
  const { binding } = newBinding({
    userId: 'u1',
    identities: ['351900111222@s.whatsapp.net', '351900111222', '99887766@lid', '99887766'],
  });
  // The same person arriving under the OTHER addressing scheme must still resolve.
  assert.equal(resolveBoundUser([binding], ['99887766@lid'])?.userId, 'u1');
  assert.equal(resolveBoundUser([binding], ['351900111222@s.whatsapp.net'])?.userId, 'u1');
  assert.equal(resolveBoundUser([binding], ['someone-else@lid']), null);
});

test('R-F3587 a revoked binding stops resolving', () => {
  const { binding } = newBinding({ userId: 'u1', identities: ['x@lid'] });
  binding.revokedAt = new Date().toISOString();
  assert.equal(resolveBoundUser([binding], ['x@lid']), null,
    'revocation must take effect on the next message, not on the next restart');
});

test('R-F3587 identities are harvested from the message, not guessed', () => {
  const ids = identitiesFromMessage('sender@lid', {
    key: { participant: 'sender@lid', participantAlt: '351900111222@s.whatsapp.net', remoteJid: 'chat@lid' },
  });
  assert.ok(ids.includes('sender@lid'));
  assert.ok(ids.includes('351900111222@s.whatsapp.net'));
  assert.ok(ids.includes('351900111222'), 'the bare user part must be recorded too');
});

test('R-F3587 the public view never echoes raw identifiers', () => {
  const { binding } = newBinding({ userId: 'u1', identities: ['351900111222@s.whatsapp.net'] });
  const view = publicBindingView(binding);
  assert.equal(view.identityCount, 1);
  assert.equal(JSON.stringify(view).includes('351900111222'), false,
    'R-F3578 removed phone identifiers from these surfaces; the view must not restore them');
});

// ── The wiring ──────────────────────────────────────────────────────────────

test('R-F3587 a bound account authorises regardless of the allow-list', () => {
  const fn = code(LISTENER).slice(code(LISTENER).indexOf('function _waSenderAllowed'));
  assert.match(fn.slice(0, 600), /_waBoundUser\(senderJid, msg\)/,
    'binding must be consulted first — the allow-list is only a bootstrap');
});

test('R-F3587 pairing is the ONLY thing an unverified sender can do', () => {
  const src = code(LISTENER);
  const pair = src.indexOf('const _code = extractPairingCode(text)');
  const gate = src.indexOf('if (!_waSenderAllowed(senderJid, msg))');
  assert.ok(pair > 0 && gate > 0);
  assert.ok(pair < gate,
    'the pairing attempt must be evaluated BEFORE the refusal, or a code from an '
    + 'unverified sender is dropped and nobody can ever bind');
});

test('R-F3587 an unknown code is not acknowledged (no guessing oracle)', () => {
  const src = code(LISTENER);
  const block = src.slice(src.indexOf('const _code = extractPairingCode(text)'), src.indexOf('if (!_waSenderAllowed(senderJid, msg))'));
  // A real-but-stale code gets a reply; an unknown one must not, or the replies
  // reveal which codes exist.
  assert.match(block, /already been used/);
  assert.match(block, /has expired/);
  assert.match(block, /else if \(_pending\)/,
    'the stale-code reply must be conditional on the code EXISTING');
});

test('R-F3587 a used code is burned and persisted immediately', () => {
  const src = code(LISTENER);
  const block = src.slice(src.indexOf('const _b = newBinding('), src.indexOf('const _b = newBinding(') + 900);
  assert.match(block, /usedAt = new Date\(\)/, 'single-use must be recorded');
  assert.match(block, /_persistBindings\(\)/, 'a binding lost on restart silently un-verifies the user');
});

test('R-F3587 only aria-web can mint a code, and only for the signed-in user', () => {
  const src = code(SERVER);
  const route = src.slice(src.indexOf("app.post('/api/wa/binding/code'"), src.indexOf("app.get('/api/wa/binding'"));
  assert.match(route, /requireAuth/, 'minting must require an authenticated session');
  assert.match(route, /findUserById\(req\.user\?\.userId\)/,
    'the code must be bound to the CALLER, never to a userId taken from the body');
  assert.doesNotMatch(route, /req\.body\?\.userId|req\.body\.userId/,
    'accepting a userId from the body would let any signed-in user bind a handset '
    + "to someone else's account");
});

test('R-F3587 aria-web never invents a code the listener did not store', () => {
  const src = code(SERVER);
  const route = src.slice(src.indexOf("app.post('/api/wa/binding/code'"), src.indexOf("app.get('/api/wa/binding'"));
  assert.match(route, /if \(!r\.ok\) return res\.status\(r\.status\)/,
    'a code shown to the user but not persisted can never be honoured');
});

test('R-F3587 enforcement is opt-in and defaults OFF', () => {
  // Turning this on before the operator's own binding is proven would silence
  // ARIA exactly as R-F3582 did. Enabling must be a deliberate act.
  assert.match(code(LISTENER), /WA_REQUIRE_VERIFIED_SENDER = process\.env\.WA_REQUIRE_VERIFIED_SENDER === '1'/);
});

test('R-F3587 the binding store failing to load is LOUD, not silent', () => {
  const src = LISTENER.slice(LISTENER.indexOf('function _loadBindings'), LISTENER.indexOf('function _persistBindings'));
  assert.match(src, /console\.error/,
    'an unreadable binding file un-verifies everyone — that must not look like '
    + '"ARIA stopped replying"');
});

test('R-F3587 the binding file is written atomically', () => {
  const src = LISTENER.slice(LISTENER.indexOf('function _persistBindings'), LISTENER.indexOf('function _persistBindings') + 800);
  assert.match(src, /renameSync/, 'a torn write would drop bindings');
});
