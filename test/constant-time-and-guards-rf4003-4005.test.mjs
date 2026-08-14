// test/constant-time-and-guards-rf4003-4005.test.mjs
//
// Three small defects from the aria-web surface audit, each with the same shape:
// a control that looks present and is not doing the job its name implies.
//
// R-F4003 (C-82) — SECRET COMPARISONS WERE NOT CONSTANT-TIME.
//   `verifyToken` compared the HMAC signature with `!==` and `verifyPassword`
//   compared the PBKDF2 hash with `===`. Both short-circuit on the first
//   differing byte, so response time leaks a prefix-match length. The token case
//   is the one that matters: an attacker CONTROLS the candidate signature and can
//   iterate, which is the textbook precondition. Remote exploitation over a
//   network is impractical, but `timingSafeEqual` was already imported two files
//   away and the fix is two lines — this is cheap to close and a certain
//   pen-test finding to leave open.
//
// R-F4004 (C-83) — vetting.html HAD NO AUTH GUARD OF ANY KIND.
//   It is the only authenticated PRODUCT page with neither a server-side page
//   gate (operatorPages.mjs) nor a client-side `Auth.requireAuth()`. An anonymous
//   visitor received the full 93 KB chrome of a personnel-screening tool with
//   every panel failing. No data leaks — the nine APIs are all gated — but a
//   compliance product that renders a broken shell to strangers is not the
//   impression it needs to make. `Sidebar.init()` does NOT imply auth; the page
//   was relying on a function that never checked.
//
// R-F4005 (C-84) — NOTHING COMPARED THE ESCAPERS.
//   `public/js/app.js:574` records that the global escaper was missing the single
//   quote, that "every other escaper in the tree (15 of 17) already covered the
//   full set", and — the load-bearing part — that "nothing compared them". That
//   is still true: each page carries its own hand-rolled escaper and no test
//   asserts they agree. The audit found 15 of 16 function-declared escapers
//   correct, which is only reassuring until the next one is written by hand.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const USERS = fs.readFileSync(path.join(ROOT, 'lib', 'auth', 'users.mjs'), 'utf8');
const VETTING = fs.readFileSync(path.join(ROOT, 'public', 'vetting.html'), 'utf8');

// ── R-F4003 ──────────────────────────────────────────────────────────────────

/**
 * Strip whole-line comments so these guards inspect CODE, not prose.
 *
 * Without this the first version matched `expected !== sig` inside the comment
 * that EXPLAINS the removal and reported a correct fix as broken — the same trap
 * as R-F3988's literal check and R-F3999's identifier match. Documentation must
 * not be able to fail a test about behaviour.
 */
function codeOf(src) {
  return src.split(/\r?\n/).filter(l => !l.trim().startsWith('//')).join('\n');
}

function bodyOf(src, decl) {
  const at = src.indexOf(decl);
  if (at < 0) return '';
  return codeOf(src.slice(at, src.indexOf('\n}', at)));
}

describe('R-F4003 — secrets are compared in constant time', () => {

  it('THE DEFECT: the token signature is not compared with === / !==', () => {
    const body = bodyOf(USERS, 'export function verifyToken');
    assert.ok(body, 'verifyToken should exist');
    assert.doesNotMatch(body, /expected\s*!==\s*sig|sig\s*!==\s*expected/,
      'a short-circuiting comparison on an attacker-controlled signature leaks '
      + 'the prefix-match length through response timing');
    assert.match(body, /equalsConstantTime\(|timingSafeEqual\(/,
      'the signature check must route through a constant-time comparison');
  });

  it('the password hash is compared in constant time too', () => {
    const body = bodyOf(USERS, 'export function verifyPassword');
    assert.ok(body, 'verifyPassword should exist');
    assert.doesNotMatch(body, /candidate\s*===\s*hash|hash\s*===\s*candidate/,
      'the stored-hash comparison must not short-circuit');
    assert.match(body, /equalsConstantTime\(|timingSafeEqual\(/,
      'the hash check must route through a constant-time comparison');
  });

  it('the shared comparison really is timingSafeEqual, not a rename', () => {
    // Both call sites delegate, so the delegate is where the property lives. A
    // helper named equalsConstantTime that used === would satisfy the two
    // assertions above and change nothing — this is the one that makes them mean
    // something.
    const body = bodyOf(USERS, 'function equalsConstantTime');
    assert.ok(body, 'the shared constant-time helper should exist');
    assert.match(body, /timingSafeEqual\(/, 'the helper must use timingSafeEqual');
  });

  it('length mismatch is handled without throwing — timingSafeEqual requires equal lengths', () => {
    // The trap in this fix: timingSafeEqual THROWS on differing buffer lengths,
    // so a naive swap turns a malformed token into a 500 instead of a clean
    // rejection — and the throw itself is a length oracle.
    const body = bodyOf(USERS, 'function equalsConstantTime');
    assert.match(body, /\.length\s*!==\s*\w+\.length/,
      'the helper must compare buffer lengths before calling timingSafeEqual');
  });

  it('behaviour is unchanged: a good token verifies, a tampered one is rejected', async () => {
    // The capability check — the property that matters is that the fix did not
    // break authentication, which a source-only assertion cannot show.
    process.env.JWT_SECRET = process.env.JWT_SECRET
      || 'test_secret_constant_time_0123456789abcdef';
    const { createToken, verifyToken } = await import('../lib/auth/users.mjs');
    const good = createToken('user-rf4003', 'analyst');
    const claims = verifyToken(good);
    assert.equal(claims.userId, 'user-rf4003');
    assert.equal(claims.role, 'analyst');

    const [data, sig] = good.split('.');
    const flipped = sig.slice(0, -1) + (sig.endsWith('A') ? 'B' : 'A');
    assert.throws(() => verifyToken(`${data}.${flipped}`), /signature/i,
      'a tampered signature must still be rejected');
    assert.throws(() => verifyToken('rubbish'), /Malformed|signature/i,
      'a malformed token must be rejected cleanly, not crash');
    assert.throws(() => verifyToken(`${data}.tooshort`), /signature/i,
      'a short signature must be rejected, not throw a length error from crypto');
  });
});

// ── R-F4004 ──────────────────────────────────────────────────────────────────
describe('R-F4004 — vetting.html requires a session', () => {

  it('THE DEFECT: the page enforces auth before rendering', () => {
    assert.match(VETTING, /Auth\.requireAuth\(\)/,
      'vetting.html rendered its full chrome to anonymous visitors with every '
      + 'panel failing — Sidebar.init() does not check auth');
  });

  it('the guard runs before the data loads, not after', () => {
    // A guard that fires after the first fetch has already shown the broken
    // state it exists to prevent.
    const guardAt = VETTING.indexOf('Auth.requireAuth()');
    const firstLoad = VETTING.search(/authed\(|API\.(get|probe)\(/);
    assert.ok(guardAt > -1 && (firstLoad === -1 || guardAt < firstLoad),
      'the auth check must precede the first data call');
  });
});

// ── R-F4005 ──────────────────────────────────────────────────────────────────
describe('R-F4005 — every hand-rolled escaper covers the same characters', () => {

  const CHARS = [
    ['&', /&amp;/],
    ['<', /&lt;/],
    ['>', /&gt;/],
    ['"', /&quot;|&#0?34;/],
    ["'", /&#0?39;|&apos;|&#x27;/i],
  ];

  /** Every escaper-shaped function or arrow in the served front-end. */
  function findEscapers() {
    const files = [];
    const pub = path.join(ROOT, 'public');
    for (const f of fs.readdirSync(pub)) if (f.endsWith('.html')) files.push(path.join(pub, f));
    for (const f of fs.readdirSync(path.join(pub, 'js'))) if (f.endsWith('.js')) files.push(path.join(pub, 'js', f));
    for (const f of fs.readdirSync(path.join(pub, 'about'))) if (f.endsWith('.html')) files.push(path.join(pub, 'about', f));

    const found = [];
    for (const p of files) {
      const src = fs.readFileSync(p, 'utf8');
      // Both shapes exist in this tree and the audit's first pass missed the
      // second: `function esc(s) {...}` and `const esc = (s) => ...`.
      const patterns = [
        /function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{([\s\S]{0,700}?)\n\s*\}/g,
        /const\s+([A-Za-z_$][\w$]*)\s*=\s*\([^)]*\)\s*=>\s*([\s\S]{0,700}?);\s*\n/g,
      ];
      for (const re of patterns) {
        for (const m of src.matchAll(re)) {
          const [, name, body] = m;
          if (!/replace\s*\(/.test(body)) continue;
          if (!/&amp;|&lt;|&gt;/.test(body)) continue;      // escaper-shaped only
          found.push({ file: path.relative(ROOT, p), name, body });
        }
      }
    }
    return found;
  }

  it('finds the escapers at all — a guard over an empty set always passes', () => {
    // The failure mode this repo keeps hitting: a check whose universe is empty
    // certifies everything. If the scan stops matching, this fails loudly rather
    // than reporting parity across nothing.
    const found = findEscapers();
    assert.ok(found.length >= 12,
      `expected the known escaper population, found ${found.length} — the scan `
      + 'has gone blind and would certify parity it never measured');
  });

  it('THE DEFECT: every escaper covers all five characters', () => {
    const bad = [];
    for (const e of findEscapers()) {
      const missing = CHARS.filter(([, pat]) => !pat.test(e.body)).map(([c]) => c);
      // A markdown renderer escapes &<> and then ADDS markup; it is not an
      // attribute escaper and quote coverage is not meaningful for it. Identified
      // by name rather than by guessing from the body.
      if (/render|markdown|^md$/i.test(e.name)) continue;
      if (missing.length) bad.push(`${e.file} ${e.name}() missing ${missing.join(' ')}`);
    }
    assert.deepEqual(bad, [],
      'app.js:574 records that two escapers silently diverged because "nothing '
      + 'compared them". This is that comparison.');
  });
});
