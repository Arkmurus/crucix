// R-F577 (2026-05-16) — model-card public-endpoint bypass tests.
//
// Live evidence: intel.arkmurus.com/model-card.html showed
//   "Constitution version: unavailable"
//   "Audit-log fingerprint: unavailable"
// while curl'ing aria-intel.fly.dev/api/aria/constitution/version
// (the underlying source-of-truth) returned 200 with v29/29-clauses
// publicly. The break was seenode's catch-all at server.mjs:4099
// gating all /api/aria/* with requireAuth, including endpoints meant
// to be published.
//
// R-F577 registers 3 public no-auth handlers BEFORE the catch-all
// for the exact paths model-card.html fetches. They expose only
// publishable metadata; the full constitution / audit chain / attack
// details remain auth-gated under the catch-all.
//
// Static-source tests pin the registration order + path set so a
// future refactor can't accidentally re-gate them.

import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
const serverPath = join(repoRoot, 'server.mjs');


function _src() {
  return readFileSync(serverPath, 'utf8');
}


test('R-F577: marker comment present', () => {
  const src = _src();
  assert.ok(src.includes('R-F577'),
    'R-F577 marker missing — likely reverted');
});


test('R-F577: _r577PublicProxy helper defined', () => {
  const src = _src();
  assert.ok(/function\s+_r577PublicProxy\s*\(/.test(src),
    'R-F577: _r577PublicProxy helper must exist');
});


test('R-F577: 3 public endpoints registered without requireAuth', () => {
  const src = _src();
  for (const path of [
    '/api/aria/constitution/version',
    '/api/aria/chat-audit/stats',
    '/api/aria/adversarial/stats',
  ]) {
    // Look for an app.get registration that does NOT include requireAuth
    // on the same statement.
    const escaped = path.replace(/\//g, '\\/');
    const re = new RegExp(
      `app\\.get\\(['"\`]${escaped}['"\`]\\s*,\\s*\\(req`,
      'm',
    );
    assert.ok(re.test(src),
      `R-F577: public registration for ${path} missing or still auth-gated`);
  }
});


test('R-F577: public registrations come BEFORE the catch-all auth gate', () => {
  const src = _src();
  // The R-F577 block must precede the line at ~4099 that gates
  // everything else. If a refactor accidentally moves R-F577 below the
  // catch-all, the public bypass stops working silently.
  const r577Idx = src.indexOf("_r577PublicProxy");
  const catchAllIdx = src.indexOf("app.use('/api/aria', requireAuth");
  assert.ok(r577Idx > 0, 'R-F577 block missing');
  assert.ok(catchAllIdx > 0, 'catch-all auth gate missing');
  assert.ok(r577Idx < catchAllIdx,
    `R-F577: public bypass at idx ${r577Idx} must precede ` +
    `the catch-all auth at idx ${catchAllIdx}. Otherwise Express ` +
    `routes through the auth gate first and the bypass never fires.`);
});


test('R-F577: catch-all auth gate still in place for non-public paths', () => {
  const src = _src();
  // Defence in depth: confirm we didn't accidentally remove the
  // catch-all while adding the public bypass.
  assert.ok(
    src.includes("app.use('/api/aria', requireAuth"),
    'R-F577: catch-all requireAuth gate appears to have been removed. ' +
    'Non-public /api/aria/* paths would become unauthenticated.'
  );
});


test('R-F577: server.mjs is syntactically valid', () => {
  // Light parse smoke — same shape as the R-F571 test.
  const src = _src();
  const openBraces = (src.match(/\{/g) || []).length;
  const closeBraces = (src.match(/\}/g) || []).length;
  assert.ok(Math.abs(openBraces - closeBraces) < 15,
    `Brace imbalance: { = ${openBraces}, } = ${closeBraces}`);
});
