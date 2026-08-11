// test/localhost-bypass-forgery-rf3833.test.mjs
//
// R-F3833 — CAPABILITY test for the forgeable localhost bypass.
//
// The defect only exists in the interaction between `app.set('trust proxy', 1)`
// and a caller-supplied `X-Forwarded-For`, so this drives a REAL Express app
// with trust-proxy ON over REAL sockets. A unit test on the helper cannot
// observe Express deriving req.ip from the header, which is the whole defect.
//
// The exploit needs a NON-loopback TCP peer whose X-Forwarded-File says
// loopback. That is reproduced literally: the fixture binds 0.0.0.0 and the test
// connects to this host's own LAN address, so the kernel reports a non-loopback
// peer while the header claims 127.0.0.1.
//
// Run: node --test test/localhost-bypass-forgery-rf3833.test.mjs

import assert from 'node:assert/strict';
import http from 'node:http';
import os from 'node:os';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, before, after } from 'node:test';
import express from 'express';

import { isSameProcessPeer, localhostBypassAllowed } from '../lib/auth/localhostBypass.mjs';

/** This host's first non-internal IPv4, or null when the box has only loopback. */
function lanAddress() {
  for (const addrs of Object.values(os.networkInterfaces())) {
    for (const a of addrs || []) {
      if (a.family === 'IPv4' && !a.internal) return a.address;
    }
  }
  return null;
}

const LAN = lanAddress();

let server;
let port;

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}

before(async () => {
  const app = express();
  // The production setting that makes req.ip forgeable (server.mjs).
  app.set('trust proxy', 1);

  // Mirrors the SHIPPED gate: bypass on the real peer, else demand a credential.
  app.get('/gated', (req, res) => {
    if (localhostBypassAllowed(req)) {
      return res.status(200).json({ bypassed: true, reqIp: req.ip, peer: req.socket.remoteAddress });
    }
    if (req.headers.authorization === 'Bearer good-token') {
      return res.status(200).json({ bypassed: false, authenticated: true });
    }
    return res.status(401).json({ error: 'Authentication required' });
  });

  // Reports what each source SAYS, so the test can prove they diverge.
  app.get('/whoami', (req, res) => res.json({
    reqIp: req.ip,
    peer: req.socket.remoteAddress,
    sameProcess: isSameProcessPeer(req),
  }));

  // The PRE-FIX gate, verbatim from server.mjs:5551. Kept so the exploit is
  // demonstrated rather than asserted — a test that only exercises the fixed
  // helper never shows the reader what was actually wrong.
  app.get('/gated-old', (req, res) => {
    const ip = req.ip || req.socket?.remoteAddress || '';
    const bypassDisabled = (process.env.ARIA_DISABLE_LOCALHOST_BYPASS || '').toLowerCase() === '1';
    if (!bypassDisabled && (ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1')) {
      return res.status(200).json({ bypassed: true });
    }
    return res.status(401).json({ error: 'Authentication required' });
  });

  await new Promise((resolve) => {
    server = http.createServer(app).listen(0, '0.0.0.0', resolve);
  });
  port = server.address().port;
});

after(() => server?.close());

function get(host, pathname, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host, port, path: pathname, method: 'GET', headers }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, json: JSON.parse(body || '{}') }));
    });
    req.on('error', reject);
    req.end();
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// The exploit, end to end. This is the assertion that FAILED before the fix.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3833 the pre-fix gate really was exploitable (why this matters)', () => {
  it('the OLD req.ip gate grants a remote peer full bypass on one header', {
    skip: LAN ? false : 'no non-loopback IPv4 on this host — cannot stage a remote peer',
  }, async () => {
    const r = await get(LAN, '/gated-old', { 'X-Forwarded-For': '127.0.0.1' });
    assert.equal(r.status, 200,
      'if this is not 200 the fixture is not reproducing the defect, and every '
      + 'other assertion in this file is proving nothing');
    assert.equal(r.json.bypassed, true,
      'THE DEFECT: a non-loopback peer was treated as same-process');

    // Same peer, no header — the gate correctly refuses. So the header alone is
    // the entire exploit.
    const honest = await get(LAN, '/gated-old');
    assert.equal(honest.status, 401, 'without the forged header the same peer is refused');
  });
});

describe('R-F3833 a 6PN-adjacent peer cannot forge loopback', () => {
  it('req.ip and the socket peer genuinely diverge under a spoofed XFF', {
    skip: LAN ? false : 'no non-loopback IPv4 on this host — cannot stage a remote peer',
  }, async () => {
    const r = await get(LAN, '/whoami', { 'X-Forwarded-For': '127.0.0.1' });
    assert.equal(r.json.reqIp, '127.0.0.1',
      'trust proxy must have derived req.ip FROM the forged header — if this fails '
      + 'the fixture is not reproducing production and the test below proves nothing');
    assert.notEqual(r.json.peer, '127.0.0.1', 'the real TCP peer must not be loopback');
    assert.equal(r.json.sameProcess, false,
      'the shipped helper must read the peer, not req.ip');
  });

  it('the forged request is REFUSED, not bypassed', {
    skip: LAN ? false : 'no non-loopback IPv4 on this host',
  }, async () => {
    const r = await get(LAN, '/gated', { 'X-Forwarded-For': '127.0.0.1' });
    assert.equal(r.status, 401,
      'a remote peer claiming X-Forwarded-For: 127.0.0.1 was granted the bypass');
    assert.notEqual(r.json.bypassed, true);
  });

  it('every XFF spoofing shape is refused', {
    skip: LAN ? false : 'no non-loopback IPv4 on this host',
  }, async () => {
    for (const xff of [
      '127.0.0.1', '::1', '::ffff:127.0.0.1', '127.0.0.1, 10.0.0.9',
      '10.0.0.9, 127.0.0.1', '127.000.000.001', 'localhost',
    ]) {
      const r = await get(LAN, '/gated', { 'X-Forwarded-For': xff });
      assert.equal(r.status, 401, `X-Forwarded-For: ${xff} was granted the bypass`);
    }
  });

  it('X-Real-IP and Forwarded are not honoured either', {
    skip: LAN ? false : 'no non-loopback IPv4 on this host',
  }, async () => {
    for (const headers of [
      { 'X-Real-IP': '127.0.0.1' },
      { Forwarded: 'for=127.0.0.1' },
      { 'X-Client-IP': '127.0.0.1' },
    ]) {
      const r = await get(LAN, '/gated', headers);
      assert.equal(r.status, 401, `${JSON.stringify(headers)} was granted the bypass`);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Deterministic everywhere, including a loopback-only CI box. Covers the same
// property as the staged-peer tests above so the suite is never blind to it.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3833 the helper never consults a header-derived value', () => {
  it('a loopback req.ip with a remote peer is NOT same-process', () => {
    assert.equal(isSameProcessPeer({ ip: '127.0.0.1', socket: { remoteAddress: '10.0.0.5' } }), false);
    assert.equal(isSameProcessPeer({ ip: '::1', socket: { remoteAddress: 'fdaa:0:1::3' } }), false);
  });

  it('a remote req.ip with a loopback peer IS same-process', () => {
    assert.equal(isSameProcessPeer({ ip: '8.8.8.8', socket: { remoteAddress: '127.0.0.1' } }), true);
  });

  it('accepts every loopback form Node reports, and nothing adjacent', () => {
    for (const ok of ['127.0.0.1', '::1', '::ffff:127.0.0.1']) {
      assert.equal(isSameProcessPeer({ socket: { remoteAddress: ok } }), true, ok);
    }
    for (const no of [
      '127.0.0.2', '127.1.1.1', '0.0.0.0', '10.0.0.1', '192.168.1.5',
      'fdaa:0:1::3', '', undefined, '::ffff:127.0.0.2', ' 127.0.0.1',
    ]) {
      assert.equal(isSameProcessPeer({ socket: { remoteAddress: no } }), false, String(no));
    }
  });

  it('falls back to the deprecated req.connection alias', () => {
    assert.equal(isSameProcessPeer({ connection: { remoteAddress: '127.0.0.1' } }), true);
  });

  it('a malformed req cannot throw its way past the gate', () => {
    for (const bad of [undefined, null, {}, { socket: null }, { socket: {} }]) {
      assert.equal(isSameProcessPeer(bad), false);
    }
  });
});

describe('R-F3833 ARIA_DISABLE_LOCALHOST_BYPASS is honoured at every gate', () => {
  const loopback = { socket: { remoteAddress: '127.0.0.1' } };

  it('kills the bypass when set to 1, restores it when cleared', () => {
    const prior = process.env.ARIA_DISABLE_LOCALHOST_BYPASS;
    try {
      delete process.env.ARIA_DISABLE_LOCALHOST_BYPASS;
      assert.equal(localhostBypassAllowed(loopback), true);
      process.env.ARIA_DISABLE_LOCALHOST_BYPASS = '1';
      assert.equal(localhostBypassAllowed(loopback), false,
        'the operator kill switch must work — two of the five gates ignored it');
      process.env.ARIA_DISABLE_LOCALHOST_BYPASS = '0';
      assert.equal(localhostBypassAllowed(loopback), true);
    } finally {
      if (prior === undefined) delete process.env.ARIA_DISABLE_LOCALHOST_BYPASS;
      else process.env.ARIA_DISABLE_LOCALHOST_BYPASS = prior;
    }
  });

  it('is read at call time, not frozen at module load', () => {
    const prior = process.env.ARIA_DISABLE_LOCALHOST_BYPASS;
    try {
      process.env.ARIA_DISABLE_LOCALHOST_BYPASS = '1';
      const off = localhostBypassAllowed(loopback);
      delete process.env.ARIA_DISABLE_LOCALHOST_BYPASS;
      const on = localhostBypassAllowed(loopback);
      assert.equal(off, false);
      assert.equal(on, true, 'flipping the switch must not need a restart');
    } finally {
      if (prior === undefined) delete process.env.ARIA_DISABLE_LOCALHOST_BYPASS;
      else process.env.ARIA_DISABLE_LOCALHOST_BYPASS = prior;
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// NO REGRESSION. This is the half that matters most: genuine same-process
// callers (the embedded Telegram bot -> /api/data, WA listener cross-calls) must
// keep working. CLAUDE.md §23 — prove it, do not assume it.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3833 genuine same-process callers are unaffected', () => {
  it('a real loopback connection still bypasses', async () => {
    const r = await get('127.0.0.1', '/gated');
    assert.equal(r.status, 200);
    assert.equal(r.json.bypassed, true, 'same-process tooling must not start 401ing');
  });

  it('still bypasses even when a proxy header claims a remote address', async () => {
    // The old code would have DENIED this (req.ip becomes 8.8.8.8). Keying off
    // the peer is strictly more correct here, not merely stricter.
    const r = await get('127.0.0.1', '/gated', { 'X-Forwarded-For': '8.8.8.8' });
    assert.equal(r.status, 200);
    assert.equal(r.json.bypassed, true);
    assert.equal(r.json.reqIp, '8.8.8.8', 'req.ip did follow the header — and was ignored');
  });

  it('a remote peer with a valid credential still authenticates normally', {
    skip: LAN ? false : 'no non-loopback IPv4 on this host',
  }, async () => {
    const r = await get(LAN, '/gated', { Authorization: 'Bearer good-token' });
    assert.equal(r.status, 200);
    assert.equal(r.json.authenticated, true, 'the fix must not break ordinary auth');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Anti-regression: the fixtures prove the helper. THIS proves all five gates use
// it — otherwise the test is green while production is open (§23).
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3833 anti-regression: every gate uses the shared helper', () => {
  const read = (f) => fs.readFileSync(path.join(repoRoot(), f), 'utf8');

  it('server.mjs and waListener.mjs import it', () => {
    assert.ok(read('server.mjs').includes("from './lib/auth/localhostBypass.mjs'"),
      'server.mjs must import the shared helper');
    assert.ok(read('lib/whatsapp/waListener.mjs').includes('localhostBypass.mjs'),
      'waListener.mjs must import the shared helper');
  });

  it('no gate decides the bypass from req.ip any more', () => {
    for (const f of ['server.mjs', 'lib/whatsapp/waListener.mjs']) {
      const src = read(f);
      // The exact vulnerable shape: an `ip` derived from req.ip, then compared
      // against a loopback literal.
      const vulnerable = /const\s+ip\s*=\s*req\.ip\s*\|\|[\s\S]{0,400}?===?\s*'127\.0\.0\.1'/;
      assert.ok(!vulnerable.test(src),
        `${f} still decides a localhost bypass from the forgeable req.ip`);
    }
  });

  it('all five gates call localhostBypassAllowed', () => {
    const server = read('server.mjs');
    const wa = read('lib/whatsapp/waListener.mjs');
    for (const [src, marker, file] of [
      [server, 'function requireAuth', 'server.mjs requireAuth'],
      [server, 'function requireInfraRole', 'server.mjs requireInfraRole'],
      [server, 'function requirePageRole', 'server.mjs requirePageRole'],
      [server, "app.get('/events'", 'server.mjs /events'],
      [wa, 'function _waRequireAuth', 'waListener _waRequireAuth'],
      [wa, 'function _waQrAuthOK', 'waListener _waQrAuthOK'],
    ]) {
      const at = src.indexOf(marker);
      assert.ok(at > -1, `gate not found: ${file}`);
      const body = src.slice(at, at + 1200);
      assert.ok(body.includes('localhostBypassAllowed'),
        `${file} does not use the shared bypass helper`);
    }
  });

  it('the two gates that ignored the kill switch now honour it', () => {
    // _waRequireAuth and _waQrAuthOK had no ARIA_DISABLE_LOCALHOST_BYPASS check
    // at all. They inherit it by construction now, via the shared helper.
    const wa = read('lib/whatsapp/waListener.mjs');
    for (const marker of ['function _waRequireAuth', 'function _waQrAuthOK']) {
      const body = wa.slice(wa.indexOf(marker), wa.indexOf(marker) + 900);
      assert.ok(body.includes('localhostBypassAllowed'),
        `${marker} must route through the helper so the kill switch reaches it`);
    }
  });
});
