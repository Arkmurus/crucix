// test/operator-page-gate-encoding-rf2818.test.mjs
//
// R-F2818 — CAPABILITY test for the operator PAGE gate percent-encoding /
// duplicate-slash bypass. This drives a REAL Express app over a REAL socket,
// because the defect lived in Express's routing layer and was therefore
// structurally invisible to the existing table test.
//
// WHY THE EXISTING TEST WAS GREEN WHILE PRODUCTION WAS OPEN:
// test/operator-page-gate-matrix-rf2785.test.mjs calls requiredRoleForPage()
// with clean literals ('/admin.html'). It never asks Express to ROUTE anything,
// so it could not observe that `app.get('/admin.html', gate)` does not match
// `/%61dmin.html` — Express compares the raw, undecoded req.path — while
// express.static's `send` DOES decode and served the file. A table test is not
// a routing test. §3c: the test must invoke the path that was actually broken.
//
// REPRODUCED LIVE on imaria.io before the fix:
//   GET /admin.html            → 302 /signin.html
//   GET /%61dmin.html          → 200 <title>Admin | ARIA</title>
//   GET /%76ault.html          → 200
//   GET /%77a-connections.html → 200      ← R-F2705 WhatsApp pairing surface
//   GET //admin.html           → 200
//
// Run: node --test test/operator-page-gate-encoding-rf2818.test.mjs

import assert from 'node:assert/strict';
import http from 'node:http';
import { describe, it, before, after } from 'node:test';
import express from 'express';

import { operatorPageFor } from '../lib/auth/operatorPages.mjs';
import { isDoubleEncodedPath } from '../lib/auth/infraRoutes.mjs';

// The gate as server.mjs mounts it (server.mjs ~:1535). Kept in lockstep by the
// contract assertion at the bottom of this file, which fails if server.mjs stops
// routing pages through operatorPageFor().
function mountGate(app) {
  app.use((req, res, next) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') return next();
    if (isDoubleEncodedPath(req.path)) {
      return res.status(400).json({ error: 'Malformed request path' });
    }
    const page = operatorPageFor(req.path);
    if (!page) return next();
    // Stand-in for requirePageRole: this suite drives the ANONYMOUS case, which
    // must always redirect regardless of which role the page demands.
    return res.redirect(302, '/signin.html');
  });
}

let server;
let base;

before(async () => {
  const app = express();
  mountGate(app);
  // Stand-in for express.static: if a request reaches here the gate let it past.
  // Any operator page reaching this handler IS the bypass.
  app.use((req, res) => res.status(200).send('STATIC-SERVED'));
  await new Promise((resolve) => {
    server = http.createServer(app).listen(0, '127.0.0.1', resolve);
  });
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => server?.close());

/** Raw GET that does NOT let the client normalise the path away. */
function rawGet(pathname) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port: server.address().port, method: 'GET', path: pathname },
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

// Each entry is a URL form that MUST NOT reach express.static anonymously.
const BYPASS_FORMS = [
  ['/admin.html', 'plain (was already gated)'],
  ['/%61dmin.html', 'percent-encoded first char — THE live bypass'],
  ['/%41DMIN.HTML', 'percent-encoded + upper case'],
  ['/ADMIN.HTML', 'case-folded'],
  ['//admin.html', 'duplicate leading slash — live bypass'],
  ['/admin.html/', 'trailing slash'],
  ['/%76ault.html', 'percent-encoded vault — live bypass'],
  ['/vault.htm', 'the alias R-F2774 added'],
  ['/%77a-connections.html', 'percent-encoded WA pairing — live bypass'],
  ['/%61ria-brain', 'percent-encoded extensionless view page'],
  ['/%73ources.html', 'percent-encoded view page'],
  ['/leads.html', 'plain view page'],
];

describe('R-F2818 operator page gate — encoding + slash bypass', () => {
  for (const [form, why] of BYPASS_FORMS) {
    it(`anonymous GET ${form} is gated (${why})`, async () => {
      const res = await rawGet(form);
      assert.notEqual(res.body, 'STATIC-SERVED',
        `${form} reached express.static anonymously — GATE BYPASSED`);
      assert.equal(res.status, 302, `${form} should redirect anonymous callers`);
    });
  }

  it('double-encoded operator paths fail CLOSED with 400', async () => {
    // %2561 → '%61' → 'a'. Deliberate obfuscation has no honest use; the
    // /api/aria gate already 400s these (server.mjs:1468) and pages now match.
    const res = await rawGet('/%2561dmin.html');
    assert.equal(res.status, 400, 'double-encoded page path must fail closed');
    assert.notEqual(res.body, 'STATIC-SERVED');
  });

  it('non-operator pages still reach static (the gate is not over-broad)', async () => {
    for (const open of ['/index.html', '/signin.html', '/dashboard.html', '/news.html']) {
      const res = await rawGet(open);
      assert.equal(res.body, 'STATIC-SERVED', `${open} must stay publicly servable`);
    }
  });

  it('non-GET verbs are not swallowed by the page gate', async () => {
    const app2 = express();
    mountGate(app2);
    app2.use((req, res) => res.status(200).send('PASSED-THROUGH'));
    const srv = await new Promise((r) => {
      const s = http.createServer(app2).listen(0, '127.0.0.1', () => r(s));
    });
    const res = await new Promise((resolve, reject) => {
      const rq = http.request(
        { host: '127.0.0.1', port: srv.address().port, method: 'POST', path: '/admin.html' },
        (rs) => { let b = ''; rs.on('data', (c) => { b += c; }); rs.on('end', () => resolve({ status: rs.statusCode, body: b })); },
      );
      rq.on('error', reject); rq.end();
    });
    assert.equal(res.body, 'PASSED-THROUGH', 'POST must fall through to the API layers');
    srv.close();
  });
});

describe('R-F2818 anti-regression: server.mjs routes pages through the shared lookup', () => {
  it('server.mjs uses operatorPageFor + isDoubleEncodedPath, not per-route app.get', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const root = path.resolve(
      path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
    );
    const src = fs.readFileSync(path.join(root, 'server.mjs'), 'utf8');
    assert.ok(src.includes('operatorPageFor(req.path)'),
      'server.mjs must resolve operator pages through the shared normalising lookup');
    assert.ok(/OPERATOR_VIEW_PAGES\)\s*app\.get\(route/.test(src) === false,
      'the per-route app.get() registrations are the bypassable form — do not reintroduce them');
  });

  it('the page gate and the /api/aria gate share ONE normaliser', async () => {
    // The defect was born because R-F2802 hardened one gate and not its sibling.
    const { normalisePath } = await import('../lib/auth/infraRoutes.mjs');
    assert.equal(typeof normalisePath, 'function',
      'normalisePath must stay exported so both gates share it');
    assert.equal(normalisePath('/%61dmin.html'), '/admin.html');
    assert.equal(normalisePath('//admin.html'), '/admin.html');
    assert.equal(normalisePath('/ADMIN.HTML/'), '/admin.html');
  });
});
