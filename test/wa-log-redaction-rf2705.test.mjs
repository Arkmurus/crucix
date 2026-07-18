// R-F2705 — aria-wa log redaction: signal/session key material must NEVER
// reach the log stream.
// ═══════════════════════════════════════════════════════════════════════════
// INCIDENT (2026-07-18, 07:27:53 UTC): aria-wa emitted a serialized WhatsApp
// session object containing privKey / rootKey / ratchet / identity material.
// Root cause: `_waLogFields` handed objects to pino unredacted (verbatim for a
// lone object arg; JSON.stringify for objects among multiple args), and pino
// had no `redact`. The most plausible caller is the process-level
// `unhandledRejection`/`uncaughtException` handler, which passes a raw
// reason/error object straight to console.error.
//
// This test drives the REAL redactor (`redactSecrets`, the exact function the
// live `_waLogFields` calls on both branches) with a realistic nested Baileys
// creds + libsignal session object, and proves no key bytes survive. It also
// source-asserts that the live logging path actually calls the redactor on BOTH
// branches, so the wiring cannot silently regress.
//
// Verified-by: capability (drives the real redactSecrets) + source-contract
// (pins the two live call sites). The listener module itself can't be imported
// standalone (pino/baileys/redis live only in the aria-wa app), so the wiring is
// pinned by source-contract — same constraint as test_rf1837 / test_rf1551.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { redactSecrets } from '../services/wa-listener/log-redact.mjs';

// A sentinel that must NEVER appear in any serialized log line.
const SENTINEL = 'SENTINEL_KEY_BYTES_DO_NOT_LEAK';

// A realistic nested Baileys creds + libsignal session graph. Key material is
// carried both as secret-named string fields AND as raw Buffers/Uint8Arrays, so
// the test exercises both redaction nets (key-name + byte).
function makeSessionObject() {
  return {
    // ── libsignal session record shape (privKey / rootKey / ratchet) ──
    sessions: {
      '447700900123.0': {
        currentRatchet: {
          rootKey: Buffer.from(SENTINEL + '_root'),
          ephemeralKeyPair: {
            privKey: Buffer.from(SENTINEL + '_eph_priv'),
            pubKey: Buffer.from('pub-not-secret-but-bytes'),
          },
          lastRemoteEphemeralKey: Buffer.from('remote-eph'),
          // non-secret-named field holding raw bytes — must still be scrubbed by
          // the byte net (proves the two nets are independent).
          previousCounter: Buffer.from('ctr-bytes-' + SENTINEL),
        },
        indexInfo: { remoteIdentityKey: Buffer.from(SENTINEL + '_idk'), baseKeyType: 1 },
        chainKey: { index: 3, key: Buffer.from(SENTINEL + '_chain') },
      },
    },
    // ── Baileys creds shape ──
    creds: {
      noiseKey: { private: Buffer.from(SENTINEL + '_noise_priv'), public: Buffer.from('noise-pub') },
      signedIdentityKey: { private: Buffer.from(SENTINEL + '_sid_priv'), public: Buffer.from('sid-pub') },
      signedPreKey: {
        keyPair: { private: Buffer.from(SENTINEL + '_spk_priv'), public: Buffer.from('spk-pub') },
        signature: Buffer.from(SENTINEL + '_sig'),
        keyId: 1,
      },
      advSecretKey: SENTINEL + '_adv_secret_string',
      registrationId: 12345,
      me: { id: '447700900123@s.whatsapp.net', name: 'ARIA' },
    },
    // ── a non-secret-named container holding raw bytes — proves the byte net
    //    is independent of the key-name net (nothing on this path is secret-named) ──
    payload: { note: 'connection state', blob: Buffer.from('bytes-' + SENTINEL) },
    // ── ordinary debug context that MUST survive ──
    msg: 'connection.update',
    level: 'error',
    accountId: 'default',
    groupName: 'Ops Room',
  };
}

test('R-F2705 capability: no key bytes survive redaction of a Baileys session object', () => {
  const out = redactSecrets(makeSessionObject());
  const serialized = JSON.stringify(out);
  assert.ok(
    !serialized.includes(SENTINEL),
    'redacted output must not contain any sentinel key bytes — a leak survived',
  );
  // Raw Buffers must be gone entirely (no {"type":"Buffer","data":[...]}).
  assert.ok(!serialized.includes('"type":"Buffer"'), 'no raw Buffer should be serialized');
  assert.ok(!/\[\s*\d+\s*,\s*\d+/.test(serialized), 'no raw byte array should be serialized');
});

test('R-F2705 capability: credential containers wiped wholesale, ordinary fields survive', () => {
  const out = redactSecrets(makeSessionObject());
  // A container whose KEY name is credential-shaped is redacted wholesale — so a
  // secret field added inside it later can never leak (defense in depth).
  assert.equal(out.creds, '[REDACTED]', 'the whole creds blob must be redacted wholesale');
  // currentRatchet matches the key-name net ("ratchet") → wiped wholesale, so
  // nothing inside it (rootKey, ephemeralKeyPair, chainKey) can ever leak.
  assert.equal(out.sessions['447700900123.0'].currentRatchet, '[REDACTED]',
    'the ratchet container is redacted wholesale');
  // The byte net is independent of the key-name net: a raw Buffer under a fully
  // non-secret path (payload.blob) is still scrubbed.
  assert.equal(out.payload.blob, '[REDACTED:bytes]', 'raw bytes scrubbed regardless of key name');
  assert.equal(out.payload.note, 'connection state', 'non-secret siblings survive');
  // Debug context under NON-secret keys is preserved — redaction must not blind the logs.
  assert.equal(out.msg, 'connection.update');
  assert.equal(out.level, 'error');
  assert.equal(out.accountId, 'default');
  assert.equal(out.groupName, 'Ops Room');
});

test("R-F2705 reproduces the incident path: a plain-object 'reason' through the multi-arg branch", () => {
  // unhandledRejection(reason) where reason is a plain object carrying session
  // state → the live else-branch does JSON.stringify(redactSecrets(a)).
  const reason = { message: 'stream errored', ...makeSessionObject() };
  const line = JSON.stringify(redactSecrets(reason)); // exact live transform
  assert.ok(!line.includes(SENTINEL), 'plain-object rejection must not leak key bytes');
  assert.ok(line.includes('stream errored'), 'the human-readable reason must survive');
});

test('R-F2705 redaction is NON-MUTATING (live creds object untouched)', () => {
  const original = makeSessionObject();
  redactSecrets(original);
  assert.ok(Buffer.isBuffer(original.sessions['447700900123.0'].currentRatchet.rootKey),
    'the input rootKey Buffer must be unchanged after redaction');
  assert.equal(original.creds.advSecretKey, SENTINEL + '_adv_secret_string',
    'the input advSecretKey must be unchanged after redaction');
});

test('R-F2705 redaction is circular-safe and depth-bounded (cannot hang boot)', () => {
  const a = { secret: Buffer.from(SENTINEL) };
  a.self = a; // cycle
  let out;
  assert.doesNotThrow(() => { out = redactSecrets(a); });
  const line = JSON.stringify(out);
  assert.ok(!line.includes(SENTINEL));
  assert.ok(line.includes('[Circular]'), 'a cycle must resolve to [Circular], not throw');
});

test('R-F2705 NEVER throws — a hostile getter fails closed, BigInt is serializable', () => {
  const hostile = {};
  Object.defineProperty(hostile, 'privKey', { enumerable: true, get() { throw new Error('boom'); } });
  Object.defineProperty(hostile, 'trap', { enumerable: true, get() { throw new Error('boom'); } });
  hostile.count = 7n; // BigInt — not JSON-serializable if left raw
  let out;
  assert.doesNotThrow(() => { out = redactSecrets(hostile); });
  // Must be JSON-serializable (no throw) — this is what pino does with it.
  assert.doesNotThrow(() => JSON.stringify(out));
  assert.equal(out.privKey, '[REDACTED]', 'secret-named getter never even invoked');
  assert.equal(out.trap, '[unreadable]', 'a throwing non-secret getter fails closed');
  assert.equal(out.count, '7', 'BigInt stringified');
});

test('R-F2705 wiring: the live _waLogFields calls redactSecrets on BOTH branches', () => {
  const src = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
    'utf8',
  );
  assert.match(src, /import \{ redactSecrets \} from '\.\/log-redact\.mjs'/,
    'the listener must import redactSecrets');
  const fn = src.slice(src.indexOf('function _waLogFields'));
  const body = fn.slice(0, fn.indexOf('\n}\n'));
  // Lone-object passthrough branch must return a redacted object, not the raw arg.
  assert.match(body, /return redactSecrets\(args\[0\]\)/,
    'the single-object branch must redact before returning to pino');
  // Multi-arg branch must redact inside the JSON.stringify call.
  assert.match(body, /JSON\.stringify\(redactSecrets\(a\)\)/,
    'the multi-arg branch must redact before JSON.stringify');
});
