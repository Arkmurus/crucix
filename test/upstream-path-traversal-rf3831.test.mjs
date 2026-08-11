// test/upstream-path-traversal-rf3831.test.mjs
//
// R-F3831 / R-F3832 — CAPABILITY test for the two NAMED-param path traversals
// that R-F3682 missed.
//
// Like R-F3682 this drives a REAL Express app over a REAL socket, because the
// defect lives in Express's decoding layer: `%2f` in the URL becomes a literal
// `/` in `req.params`. A unit test calling the validator with a clean literal
// cannot observe that decode and would stay green while production is open
// (CLAUDE.md §3c).
//
// The upstream fetch is replaced by a recorder in both fixtures, so a forwarded
// traversal is directly observable — reaching the recorder is exactly the moment
// the service token would have been attached to an attacker-chosen path.
//
// Run: node --test test/upstream-path-traversal-rf3831.test.mjs

import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, before, after } from 'node:test';
import express from 'express';

// The SHIPPED validators — not copies. The anti-regression block at the bottom
// asserts server.mjs actually uses them.
import { isValidSessionId, isValidWaAccountId } from '../lib/http/upstreamSegment.mjs';

const BRAIN = 'http://brain.internal:8000';
const WA = 'http://wa.internal:5070';

let server;
/** Every upstream URL the fixture would have fetched, with its token. */
let forwarded = [];

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}

before(async () => {
  const app = express();

  // ── Fixture 1: the three /api/aria/conversations/:sessionId proxies ────────
  // Mirrors server.mjs's post-fix construction: validator, then encode, then
  // interpolate. `requireAuth` is stubbed to an ordinary signed-in account,
  // which is the real precondition (self-serve signup auto-approves to active).
  const conversations = (suffix, method) => (req, res) => {
    const sid = req.params.sessionId;
    if (!isValidSessionId(sid)) return res.status(400).json({ error: 'Invalid session id' });
    const url = `${BRAIN}/api/aria/conversations/${encodeURIComponent(sid)}${suffix}?user_id=attacker`;
    forwarded.push({ url: new URL(url).href, token: 'ARIA_SERVICE_TOKEN', method });
    return res.status(200).json({ ok: true });
  };
  app.get('/api/aria/conversations/:sessionId', conversations('/detail', 'GET'));
  app.delete('/api/aria/conversations/:sessionId', conversations('', 'DELETE'));
  app.put('/api/aria/conversations/:sessionId/title', conversations('/title', 'PUT'));

  // ── Fixture 2: the three /api/wa-listener/accounts/:id proxies ─────────────
  const waAccount = (suffix, method) => (req, res) => {
    const id = req.params.id;
    if (!isValidWaAccountId(id)) return res.status(400).json({ error: 'Invalid account id' });
    const url = WA + '/api/wa-listener/accounts/' + encodeURIComponent(id) + suffix;
    forwarded.push({ url: new URL(url).href, token: 'ARIA_INTERNAL_TOKEN', method });
    return res.status(200).json({ ok: true });
  };
  app.get('/api/wa-listener/accounts/:id', waAccount('', 'GET'));
  app.get('/api/wa-listener/accounts/:id/qr', waAccount('/qr', 'GET'));
  app.delete('/api/wa-listener/accounts/:id', waAccount('', 'DELETE'));

  await new Promise((resolve) => {
    server = http.createServer(app).listen(0, '127.0.0.1', resolve);
  });
});

after(() => server?.close());

/** Raw request — the client must not normalise the path away before it is sent. */
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

// ─────────────────────────────────────────────────────────────────────────────
// The mechanism, demonstrated. This is the assertion that FAILED before the fix:
// it is the exact construction server.mjs:5223/5239/5264 and :1491/1505/1520 used.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3831 the vulnerable construction really does escape (why this matters)', () => {
  it('raw concatenation lets ..%2f walk out of the conversations prefix', () => {
    const decoded = decodeURIComponent('..%2f..%2fdd%2freport%2fvictim');
    assert.equal(decoded, '../../dd/report/victim', 'Express hands the handler a real slash');

    const vulnerable = new URL(`${BRAIN}/api/aria/conversations/${decoded}/detail`).href;
    assert.equal(vulnerable, `${BRAIN}/api/dd/report/victim/detail`,
      'WHATWG collapses the .. segments — this is the live exploit');
    assert.ok(!vulnerable.includes('/conversations/'),
      'the request no longer addresses the conversations API at all');

    const fixed = new URL(
      `${BRAIN}/api/aria/conversations/${encodeURIComponent(decoded)}/detail`,
    ).href;
    assert.ok(fixed.startsWith(`${BRAIN}/api/aria/conversations/`),
      'encodeURIComponent is the containment guarantee');
  });
});

const SESSION_TRAVERSALS = [
  ['..%2f..%2fdd%2freport%2fvictim%3fx=', 'encoded ../.. into another tenant\'s DD report'],
  ['..%2F..%2FDD%2Freport%2Fvictim', 'upper-case %2F'],
  ['%2e%2e%2f%2e%2e%2fapi%2faria%2fhealth', 'fully encoded dots and slashes'],
  ['..%252f..%252fvault', 'double-encoded slash'],
  ['user_1_abc%2f..%2f..%2fvault%2fcase%2f9', 'valid-looking prefix then escape'],
  ['../../vault/case/9', 'literal traversal'],
  ['..%5c..%5cvault', 'backslash separators'],
  ['sess_1%00.json', 'NUL truncation'],
  ['sess_1%0d%0aX-Injected:%201', 'CRLF header injection'],
  ['..%2f..%2fadmin%2fpurge', 'reach for an operator-gated path'],
];

describe('R-F3831 conversations proxy — traversal cannot reach the brain', () => {
  for (const [form, why] of SESSION_TRAVERSALS) {
    for (const method of ['GET', 'DELETE']) {
      it(`${method} refuses ${form.slice(0, 42)} (${why})`, async () => {
        forwarded = [];
        const res = await raw(`/api/aria/conversations/${form}`, method);
        assert.equal(forwarded.length, 0,
          `FORWARDED with the service token: ${forwarded[0]?.url}`);
        // 400 = the validator refused it. 404 = the traversal added segments so
        // the route never matched at all (what a LITERAL `../../` does — Express
        // only percent-decodes, it does not re-split on the decoded slash). Both
        // are refusals; asserting only 400 would fail on a request that is safe.
        assert.ok(res.status === 400 || res.status === 404,
          `expected a refusal, got ${res.status}`);
      });
    }

    it(`PUT title refuses ${form.slice(0, 42)}`, async () => {
      forwarded = [];
      await raw(`/api/aria/conversations/${form}/title`, 'PUT');
      for (const f of forwarded) {
        assert.ok(f.url.startsWith(`${BRAIN}/api/aria/conversations/`),
          `escaped the prefix: ${f.url}`);
      }
    });
  }

  it('the P0 exploit string cannot delete another tenant\'s DD report', async () => {
    forwarded = [];
    const res = await raw(
      '/api/aria/conversations/..%2f..%2fdd%2freport%2fvictim_run_id%3fx=', 'DELETE',
    );
    assert.equal(forwarded.length, 0, 'a cross-tenant DELETE reached the brain');
    assert.equal(res.status, 400);
    assert.ok(!res.body.includes('dd/report'), 'must not echo the attempted upstream path');
  });
});

describe('R-F3832 WA listener proxy — traversal cannot reach the listener', () => {
  const WA_TRAVERSALS = [
    ['..%2f..%2fmessages', 'read every account\'s messages (aria_wa_listener.mjs:3710)'],
    ['..%2f..%2fapi%2fwa-listener%2fbinding%2fvictim', 'unlink another tenant\'s WhatsApp'],
    ['..%2f..%2fstatus', 'listener topology'],
    ['..%2f..%2fgroups', 'every group the number belongs to'],
    ['wa_1_abcdef%2f..%2f..%2fmessages', 'valid-looking prefix then escape'],
    ['..%5c..%5cstatus', 'backslash separators'],
  ];

  for (const [form, why] of WA_TRAVERSALS) {
    it(`refuses accounts/${form.slice(0, 40)} (${why})`, async () => {
      for (const [p, method] of [
        [`/api/wa-listener/accounts/${form}`, 'GET'],
        [`/api/wa-listener/accounts/${form}/qr`, 'GET'],
        [`/api/wa-listener/accounts/${form}`, 'DELETE'],
      ]) {
        forwarded = [];
        await raw(p, method);
        for (const f of forwarded) {
          assert.ok(f.url.startsWith(`${WA}/api/wa-listener/accounts/`),
            `escaped with the internal token: ${f.url}`);
        }
      }
    });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Not over-broad. A gate that breaks real traffic gets reverted, and the revert
// takes the fix with it.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3831 real traffic still works', () => {
  it('accepts every session-id shape the nine live minters produce', async () => {
    const REAL = [
      'antoniocorrei25gmailcom_1754835200000_k3f9a', // public/aria.html:783
      'email_compose_1754835200000',                  // lib/aria/emailReader.mjs:230
      'wa_group_OpsTeam',                             // lib/whatsapp/waListener.mjs
      'eval_9f2c1a7b3d',                              // routes/aria.py:3541
      'dd_9f2c1a7b3d4e',                              // dd_orchestrator.py
      'client_antonio@example.com',                   // main.py:5050 — email, has @ and .
      'tui_1754835200',                               // aria_tui.py:446
      'proactive',
      'u_abc123',
    ];
    for (const sid of REAL) {
      assert.equal(isValidSessionId(sid), true, `${sid} is a REAL id and must be accepted`);
      forwarded = [];
      const res = await raw(`/api/aria/conversations/${encodeURIComponent(sid)}`);
      assert.equal(res.status, 200, `${sid} must still resolve`);
      assert.equal(forwarded.length, 1);
      assert.ok(forwarded[0].url.startsWith(`${BRAIN}/api/aria/conversations/`));
    }
  });

  it('accepts a genuine wa account id', async () => {
    forwarded = [];
    const res = await raw('/api/wa-listener/accounts/wa_1754835200000_k3f9az');
    assert.equal(res.status, 200);
    assert.equal(forwarded.length, 1);
  });

  it('length is not pinned so a future id-format change cannot 404 everyone', () => {
    assert.equal(isValidSessionId('a'), true);
    assert.equal(isValidSessionId('a'.repeat(200)), true);
    assert.equal(isValidSessionId('a'.repeat(201)), false, 'but it is bounded');
  });
});

describe('R-F3831 validator unit contract', () => {
  it('refuses every path-boundary character and control byte', () => {
    for (const bad of [
      undefined, null, 42, {}, '', '../x', 'a/b', 'a\\b', 'a%2fb', 'a%b',
      'a\0b', 'a\nb', 'a\rb', 'a\x7f', '..', 'x..y',
    ]) {
      assert.equal(isValidSessionId(bad), false, `${JSON.stringify(bad)} must be refused`);
      assert.equal(isValidWaAccountId(bad), false, `${JSON.stringify(bad)} must be refused`);
    }
  });

  it('the wa allowlist is tighter than the session one — its shape is enumerable', () => {
    assert.equal(isValidSessionId('client_a@b.com'), true);
    assert.equal(isValidWaAccountId('client_a@b.com'), false,
      'a wa account id is wa_<ts>_<rand> and never contains @ or .');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Anti-regression: the fixtures above prove the validators work. THIS proves
// production uses them. Without it the test is green while the hole is open —
// the precise failure mode CLAUDE.md §23 was written for.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3831/R-F3832 anti-regression: server.mjs is actually wired', () => {
  const src = () => fs.readFileSync(path.join(repoRoot(), 'server.mjs'), 'utf8');

  it('server.mjs imports the shared validators', () => {
    assert.ok(src().includes("from './lib/http/upstreamSegment.mjs'"),
      'server.mjs must import the shared validators, not inline a copy');
  });

  it('no conversations proxy interpolates a RAW sessionId into the brain URL', () => {
    const s = src();
    // The exact vulnerable shapes that were live at :5223, :5239 and :5264.
    for (const vulnerable of [
      '/api/aria/conversations/${sid}/detail',
      '/api/aria/conversations/${sid}?user_id=',
      '/api/aria/conversations/${sid}/title',
    ]) {
      assert.ok(!s.includes(vulnerable),
        `raw interpolation is back at: ${vulnerable}`);
    }
  });

  it('no wa-listener proxy concatenates a RAW req.params.id', () => {
    const s = src();
    assert.ok(!s.includes("'/api/wa-listener/accounts/' + req.params.id"),
      'raw concatenation is back on the wa account proxy');
  });

  it('each of the six handlers validates BEFORE the token-bearing fetch', () => {
    const s = src();
    const handlers = [
      ["app.get('/api/aria/conversations/:sessionId'", 'isValidSessionId', 'ariaProxy('],
      ["app.delete('/api/aria/conversations/:sessionId'", 'isValidSessionId', 'fetch('],
      ["app.put('/api/aria/conversations/:sessionId/title'", 'isValidSessionId', 'fetch('],
      ["app.get('/api/wa-listener/accounts/:id'", 'isValidWaAccountId', 'fetch('],
      ["app.get('/api/wa-listener/accounts/:id/qr'", 'isValidWaAccountId', 'fetch('],
      ["app.delete('/api/wa-listener/accounts/:id'", 'isValidWaAccountId', 'fetch('],
    ];
    for (const [marker, guard, sink] of handlers) {
      const at = s.indexOf(marker);
      assert.ok(at > -1, `handler not found: ${marker}`);
      const body = s.slice(at, at + 1400);
      const guardIdx = body.indexOf(guard);
      const sinkIdx = body.indexOf(sink);
      assert.ok(guardIdx > -1, `${marker} does not call ${guard}`);
      assert.ok(sinkIdx > -1, `${marker} sink ${sink} not found`);
      assert.ok(guardIdx < sinkIdx,
        `${marker}: the validator must run BEFORE the token-bearing ${sink}`);
      assert.ok(body.includes('encodeURIComponent'),
        `${marker}: the segment must also be encoded — the validator is defence in depth`);
    }
  });

  it('a refused segment reaches the brain, not just the console (§21a)', () => {
    const s = src();
    assert.ok(s.includes("errorTracker.record('proxy_path', 'segment_rejected'"),
      'a refused traversal must be wired to the brain');
    assert.ok(/segmentRejected\.status\s*=\s*403/.test(s),
      'the rejection Error must carry status 403 so classifyError escalates it as AUTH '
      + 'rather than dropping it as TRANSIENT — otherwise it looks wired and is dark');
  });
});
