// test/vetting-portal-traversal-rf3682.test.mjs
//
// R-F3682 — CAPABILITY test for the UNAUTHENTICATED vetting-portal proxy
// traversal. Like R-F2818, this drives a REAL Express app over a REAL socket,
// because the defect lives in Express's routing/decoding layer: Express
// percent-DECODES a regex route's capture group, so `%2f` becomes `/` and the
// `..` segments survive into the upstream URL. A unit test that calls the
// validator with a clean literal cannot observe that decode, and would have
// stayed green while production was open (§3c).
//
// REPRODUCED LIVE on imaria.io before the fix:
//   GET /api/aria/cost/monthly/status                                      -> 401
//   GET /api/vetting-portal/..%2f..%2fapi%2faria%2fcost%2fmonthly%2fstatus  -> 200
//   {"month":"2026-08","spent_usd":21.483923,"cap_usd":300,...}
//
// The upstream stand-in below asserts the property that actually matters: the
// brain-bound fetch must never be reached with a rewritten path, because that
// fetch is where the service token gets attached.
//
// Run: node --test test/vetting-portal-traversal-rf3682.test.mjs

import assert from 'node:assert/strict';
import http from 'node:http';
import { describe, it, before, after } from 'node:test';
import express from 'express';

// The SHIPPED validator — not a copy. If server.mjs ever stops using it, the
// anti-regression block at the bottom fails.
import { isValidVettingPortalSuffix } from '../lib/vetting/portalPath.mjs';

let server;
/** Every suffix that reached the "upstream fetch" during a test. */
let forwarded = [];

before(async () => {
  const app = express();

  // Mirrors server.mjs's route registration exactly: same regex, same
  // req.params[0] read, same guard. The only substitution is the upstream
  // fetch — replaced by a recorder, so a forwarded traversal is observable.
  app.all(/^\/api\/vetting-portal\/(.+)$/, (req, res) => {
    const suffix = req.params[0];
    if (!isValidVettingPortalSuffix(suffix)) {
      return res.status(404).json({ error: 'Not found' });
    }
    forwarded.push(suffix);
    // Stand-in for `fetch(`${ARIA_SERVICE_URL}/api/vetting-portal/${suffix}`)`
    // with _ariaHeaders(). Reaching here means the service token would have
    // been attached to whatever path `suffix` resolves to.
    return res.status(200).json({ upstream: `/api/vetting-portal/${suffix}` });
  });

  await new Promise((resolve) => {
    server = http.createServer(app).listen(0, '127.0.0.1', resolve);
  });
});

after(() => server?.close());

/** Raw request that does NOT let the client normalise the path away. */
function raw(pathname, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port: server.address().port, method, path: pathname },
      (res) => {
        let body = '';
        res.on('data', (c) => { body += c; });
        res.on('end', () => resolve({ status: res.statusCode, body }));
      },
    );
    req.on('error', reject);
    req.end();
  });
}

// A realistic token: secrets.token_urlsafe(32) => 43 chars of [A-Za-z0-9_-].
const TOKEN = 'K7bQ2mZr8vXpL4nT9wYcJ1sHd0GfA6uE3iOoRbNqPxU'; // allowlist-secret (synthetic)

// Each entry MUST NOT reach the upstream fetch.
const TRAVERSAL_FORMS = [
  ['..%2f..%2fapi%2faria%2fcost%2fmonthly%2fstatus', 'THE live bypass — encoded ../..'],
  ['..%2F..%2FAPI%2Faria%2Fhealth', 'upper-case %2F'],
  ['..%2f..%2fapi%2faria%2fdd%2freports', 'encoded traversal at DD reports'],
  ['%2e%2e%2f%2e%2e%2fapi%2faria%2fhealth', 'fully encoded dots and slashes'],
  ['..%252f..%252fapi%252faria%252fhealth', 'double-encoded slash'],
  [`${TOKEN}%2f..%2f..%2fapi%2faria%2fhealth`, 'valid token then escape'],
  [`${TOKEN}/../../api/aria/health`, 'literal traversal after a valid token'],
  ['....%2f%2f..%2fapi%2faria%2fhealth', 'dot-padding variant'],
  [`${TOKEN}%00.jpg`, 'NUL truncation'],
  [`${TOKEN}%0d%0aX-Injected:%201`, 'CRLF header injection'],
  ['..%5c..%5capi%5caria%5chealth', 'backslash separators'],
  [`${TOKEN}/documents/../../api/aria/health`, 'escape from the documents route'],
];

describe('R-F3682 vetting-portal proxy — traversal cannot reach the brain', () => {
  for (const [form, why] of TRAVERSAL_FORMS) {
    it(`refuses /api/vetting-portal/${form.slice(0, 46)} (${why})`, async () => {
      forwarded = [];
      const res = await raw(`/api/vetting-portal/${form}`);

      assert.equal(forwarded.length, 0,
        `suffix was FORWARDED upstream with the service token: ${forwarded[0]}`);
      assert.equal(res.status, 404,
        'a refused suffix must return the portal\'s indistinguishable 404');
      // Must not reflect attacker-controlled input back to the caller.
      assert.ok(!res.body.includes('api/aria'),
        'response body must not echo the attempted upstream path');
    });
  }

  it('the specific live-proven exploit string cannot reach cost/monthly/status', async () => {
    forwarded = [];
    const res = await raw(
      '/api/vetting-portal/..%2f..%2fapi%2faria%2fcost%2fmonthly%2fstatus',
    );
    assert.equal(res.status, 404);
    assert.equal(forwarded.length, 0);
    assert.ok(!res.body.includes('spent_usd'),
      'the live leak returned spent_usd — it must be unreachable');
  });
});

describe('R-F3682 the gate is not over-broad — real portal traffic still works', () => {
  it('GET /{token} proxies through', async () => {
    forwarded = [];
    const res = await raw(`/api/vetting-portal/${TOKEN}`);
    assert.equal(res.status, 200, 'a genuine applicant link must still resolve');
    assert.deepEqual(forwarded, [TOKEN]);
  });

  it('POST /{token}/documents proxies through', async () => {
    forwarded = [];
    const res = await raw(`/api/vetting-portal/${TOKEN}/documents`, 'POST');
    assert.equal(res.status, 200, 'a referee upload must still resolve');
    assert.deepEqual(forwarded, [`${TOKEN}/documents`]);
  });

  it('token length is not pinned to exactly 43 chars', async () => {
    // A future secrets.token_urlsafe(N) change must not 404 every applicant.
    for (const t of ['a'.repeat(16), 'b'.repeat(43), 'c'.repeat(128)]) {
      forwarded = [];
      const res = await raw(`/api/vetting-portal/${t}`);
      assert.equal(res.status, 200, `${t.length}-char token should be accepted`);
    }
  });
});

describe('R-F3682 validator unit contract', () => {
  it('accepts only the two documented shapes', () => {
    assert.equal(isValidVettingPortalSuffix(TOKEN), true);
    assert.equal(isValidVettingPortalSuffix(`${TOKEN}/documents`), true);
    assert.equal(isValidVettingPortalSuffix(`${TOKEN}/other`), false,
      'an undocumented sub-path must fail closed');
    assert.equal(isValidVettingPortalSuffix(`${TOKEN}/documents/x`), false);
  });

  it('rejects non-strings, empty, control chars and short tokens', () => {
    for (const bad of [undefined, null, 42, {}, '', 'short', `${TOKEN}\n`, `${TOKEN}\0`]) {
      assert.equal(isValidVettingPortalSuffix(bad), false,
        `${JSON.stringify(bad)} must be refused`);
    }
  });
});

describe('R-F3682 anti-regression: server.mjs uses the shared validator', () => {
  it('server.mjs imports and calls isValidVettingPortalSuffix, and keeps no inline copy', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const root = path.resolve(
      path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
    );
    const src = fs.readFileSync(path.join(root, 'server.mjs'), 'utf8');

    assert.ok(src.includes("from './lib/vetting/portalPath.mjs'"),
      'server.mjs must import the shared validator');
    assert.ok(src.includes('isValidVettingPortalSuffix(suffix)'),
      'the proxy must gate on the shared validator — an inline regex is a copy '
      + 'the test cannot see, which is how this class of defect survives');

    // The unguarded shape that was live: params[0] interpolated straight into
    // the upstream URL with no preceding validation.
    const handler = src.slice(src.indexOf("app.all(/^\\/api\\/vetting-portal"));
    const guardIdx = handler.indexOf('isValidVettingPortalSuffix');
    const fetchIdx = handler.indexOf('${ARIA_SERVICE_URL}/api/vetting-portal/');
    assert.ok(guardIdx > -1 && fetchIdx > -1 && guardIdx < fetchIdx,
      'the validator must run BEFORE the token-bearing upstream fetch');
  });

  it('the refusal is wired to the brain, not just the console (§21a)', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const root = path.resolve(
      path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
    );
    const src = fs.readFileSync(path.join(root, 'server.mjs'), 'utf8');
    assert.ok(src.includes("errorTracker.record('vetting_portal_proxy', 'suffix_rejected'"),
      'a refused traversal must reach the brain');
    // status 403 is load-bearing: classifyError maps it to SEVERITY.AUTH, which
    // is on the ESCALATE list. A plain Error classifies TRANSIENT and is dropped
    // before the wire, i.e. it would look wired and be dark.
    assert.ok(/rejected\.status\s*=\s*403/.test(src),
      'the rejection Error must carry status 403 so it escalates, not TRANSIENT');
  });
});
