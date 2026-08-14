// test/upload-path-end-to-end-rf4017.test.mjs
//
// R-F4017 — CAPABILITY test for the document-upload proxy.
//
// WHY THIS EXISTS. R-F3997 inserted a counting Transform into the live streaming
// upload path:
//
//     req.pipe(_meter.stream);
//     body: Readable.toWeb(_meter.stream)
//
// and was verified by unit-testing the METER in isolation. That is exactly the
// gap CLAUDE.md §3c names: "a unit test that tests a helper does NOT count". The
// meter being correct says nothing about whether a real multipart upload still
// flows through the route, whether backpressure works, whether the upstream
// receives the bytes intact, or whether an aborted stream is reported as 413
// rather than as a proxy error.
//
// Document upload is a core customer path — the chat composer's attach button and
// the §22a contract-review flow both depend on it. Shipping a change to it with no
// end-to-end evidence was the weakest link in this workstream, and this closes it.
//
// The test drives the REAL route in a REAL server process against a stub
// upstream, and asserts the bytes arrive unaltered.
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { allowLoopbackNetwork } from './helpers/net_guard.mjs';
import { TIERS } from '../lib/billing/tiers.mjs';

// This test drives a REAL server process over loopback, so it needs real
// sockets. net_guard blocks network in tests by default and provides this
// sanctioned escape for exactly this case: loopback only, never the internet.
// Without it the file is permanently red under `npm test`, which is the
// anti-pattern this workstream has spent its time removing.
allowLoopbackNetwork();

const INTERNAL_TOKEN = 'rf4017-internal-token';
const WEB_PORT = 39231;
const STUB_PORT = 39232;

// R-F4020 — DERIVED from the tier table, never hardcoded. This file first used a
// literal 6 MiB because the free limit was 5 MB; raising it to 25 MB turned two
// correct tests red for no reason but a duplicated constant. A test that pins a
// number the product owns has to be edited every time the product changes, which
// is how it eventually gets edited WRONG. The internal-token caller has no JWT
// user, so its tier resolves to the default (free).
const FREE_LIMIT = TIERS.free.uploadBytesMax;
const OVER_LIMIT = FREE_LIMIT + 2 * 1024 * 1024;

let stub, server, received = [];

/** Minimal stand-in for the brain's /api/aria/extract-document. */
function startStub() {
  stub = createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      received.push({ url: req.url, bytes: body.length, body });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      // A REALISTIC brain response. The lead route derives its `verification`
      // outcome from what comes back here, so a stub that omits lead_id/token
      // would make every genuine reply 'not_required' and the oracle comparison
      // below would compare two artefacts instead of two real answers.
      res.end(JSON.stringify({
        ok: true,
        received_bytes: body.length,
        lead_id: 'stub-lead-1',
        verification: { token: 'stub-token', expires_at: '2099-01-01T00:00:00Z' },
      }));
    });
    req.on('error', () => { /* client aborted mid-stream — expected for the 413 case */ });
  });
  return new Promise((r) => stub.listen(STUB_PORT, r));
}

function multipart(boundary, filename, payload) {
  const head = Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\n`
    + 'Content-Type: application/octet-stream\r\n\r\n', 'utf8');
  const tail = Buffer.from(`\r\n--${boundary}--\r\n`, 'utf8');
  return Buffer.concat([head, payload, tail]);
}

async function upload(body, { withContentLength = true } = {}) {
  const boundary = '----rf4017boundary';
  const headers = {
    'Content-Type': `multipart/form-data; boundary=${boundary}`,
    Authorization: `Bearer ${INTERNAL_TOKEN}`,
  };
  if (withContentLength) headers['Content-Length'] = String(body.length);
  return fetch(`http://127.0.0.1:${WEB_PORT}/api/aria/extract-document`, {
    method: 'POST', headers, body,
  });
}

describe('R-F4017 — a real upload still flows through the metered proxy', () => {

  before(async () => {
    await startStub();
    server = spawn(process.execPath, ['server.mjs'], {
      cwd: new URL('..', import.meta.url).pathname.slice(1),
      env: {
        ...process.env,
        PORT: String(WEB_PORT),
        NODE_ENV: 'development',
        JWT_SECRET: 'rf4017-secret-not-a-real-one-0123456789ab',
        ARIA_INTERNAL_TOKEN: INTERNAL_TOKEN,
        ARIA_SERVICE_URL: `http://127.0.0.1:${STUB_PORT}`,
        ARIA_API_TOKEN: '',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    // Wait for the app to answer rather than sleeping a guessed interval.
    const deadline = Date.now() + 90_000;
    for (;;) {
      try {
        const r = await fetch(`http://127.0.0.1:${WEB_PORT}/healthz`);
        if (r.ok) break;
      } catch { /* not up yet */ }
      if (Date.now() > deadline) throw new Error('server did not start');
      await new Promise((r) => setTimeout(r, 500));
    }
  });

  after(async () => {
    if (server) { server.kill('SIGKILL'); await once(server, 'exit').catch(() => {}); }
    if (stub) await new Promise((r) => stub.close(r));
  });

  it('THE CAPABILITY: a normal upload reaches the upstream with its bytes intact', async () => {
    received = [];
    const payload = Buffer.alloc(64 * 1024, 0xab);      // 64 KiB, well under any cap
    const body = multipart('----rf4017boundary', 'note.txt', payload);
    const res = await upload(body);

    assert.equal(res.status, 200, 'a legal upload must still succeed');
    assert.equal(received.length, 1, 'the upstream must receive exactly one request');
    assert.equal(received[0].bytes, body.length,
      'every byte must arrive — the meter must not truncate or re-chunk lossily');
    assert.ok(received[0].body.includes(payload),
      'the file payload must survive the proxy unaltered');
  });

  it('a large-but-legal upload streams through (backpressure holds)', async () => {
    // Comfortably under the free-tier limit and large enough to cross many
    // chunk boundaries, which is where a naive pipe would deadlock or drop.
    received = [];
    const payload = Buffer.alloc(3 * 1024 * 1024, 0x5a);
    const body = multipart('----rf4017boundary', 'big.bin', payload);
    const res = await upload(body);
    assert.equal(res.status, 200);
    assert.equal(received[0].bytes, body.length, 'all 3 MiB must arrive');
  });

  it('an oversized upload is refused as 413, not as a proxy error', async () => {
    // Refused with the status that tells the user it is THEIR request, not our
    // outage.
    const payload = Buffer.alloc(OVER_LIMIT, 0x11);
    const body = multipart('----rf4017boundary', 'toobig.bin', payload);
    const res = await upload(body);
    assert.equal(res.status, 413, 'an oversized upload must be 413');
    const j = await res.json().catch(() => ({}));
    assert.match(String(j.error || ''), /too large/i,
      'the refusal must name the size problem');
  });

  it('THE BYPASS IS CLOSED: an oversized CHUNKED upload is still refused', async () => {
    // The C-78 defect: no Content-Length meant no measurement. This is the whole
    // reason the meter exists, driven end to end rather than in isolation.
    received = [];
    const payload = Buffer.alloc(OVER_LIMIT, 0x22);
    const body = multipart('----rf4017boundary', 'chunked.bin', payload);
    const res = await upload(body, { withContentLength: false });
    assert.notEqual(res.status, 200,
      'a chunked oversized upload must NOT succeed — that was the bypass');
    assert.equal(res.status, 413, 'and it must be reported as too large');
  });

  it('a chunked upload UNDER the limit still works — the fix did not ban chunking', async () => {
    // The failure mode of a careless fix: bound the body by refusing every
    // request without a Content-Length, breaking legitimate streaming clients.
    received = [];
    const payload = Buffer.alloc(128 * 1024, 0x33);
    const body = multipart('----rf4017boundary', 'ok-chunked.bin', payload);
    const res = await upload(body, { withContentLength: false });
    assert.equal(res.status, 200, 'a legal chunked upload must still succeed');
    assert.equal(received[0].bytes, body.length);
  });

  // ── R-F4018 (C-93) — the lead drop, proven by BEHAVIOUR ────────────────────
  //
  // The source-text guard for this could not tell a live call from a disabled
  // one: an adversarial probe that changed `errorTracker.record(...)` to
  // `false && errorTracker.record(...)` left it green. Whether a dropped lead is
  // actually dropped is a behavioural question, so it is asked behaviourally —
  // does the upstream receive the submission or not.

  async function postLead(extra) {
    return fetch(`http://127.0.0.1:${WEB_PORT}/api/leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({
        name: 'Real Prospect', email: 'prospect@example.com',
        company: 'Acme', country: 'UK', role: 'Analyst', use_case: 'Compliance advisory',
      }, extra || {})),
    });
  }

  it('a genuine lead reaches the upstream', async () => {
    received = [];
    const res = await postLead();
    assert.equal(res.status, 200, 'a genuine access request must succeed');
    assert.ok(received.some((r) => r.url.includes('/leads/inbound')),
      'the submission must reach the brain');
  });

  it('THE DROP IS REAL: a filled decoy never reaches the upstream', async () => {
    received = [];
    const res = await postLead({ lead_confirm_blank: 'http://spam.example' });
    assert.equal(res.status, 200, 'the bot must see an ordinary success');
    assert.equal(received.length, 0,
      'a decoy-filled submission must NOT be forwarded — if it is, the honeypot '
      + 'is decorative and the pipeline is still being polluted');
  });

  it('the bot response is byte-identical to a genuine one — no oracle', async () => {
    received = [];
    const good = await postLead({ email: 'another@example.com' });
    const goodBody = await good.text();
    received = [];
    const bot = await postLead({ email: 'third@example.com', lead_confirm_blank: 'x' });
    const botBody = await bot.text();
    assert.equal(bot.status, good.status);
    assert.equal(botBody, goodBody,
      'a different response would tell a bot it was detected, and would let an '
      + 'attacker probe which addresses have already been targeted');
  });
});
