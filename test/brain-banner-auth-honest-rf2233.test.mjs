/**
 * R-F2233 — aria-brain dashboard banner must tell AUTH-401 apart from UNREACHABLE.
 * ═══════════════════════════════════════════════════════════════════════════
 * Operator symptom: "one computer shows 4 endpoints unreachable, another shows 6".
 * Root cause: aria-brain.html fetched ~24 operator-only panels client-side; ~19
 * are auth-gated and return 401 to a signed-out / flapped-token browser. The old
 * fetchJson() funnelled 401 into _fetchFailures and screamed "N endpoints
 * unreachable" — so the number was really "how many auth-gated panels did THIS
 * session fail to authenticate", which legitimately differs per computer.
 *
 * This is a CAPABILITY test: it loads the REAL public/js/app.js (API.probe) and
 * the REAL fetchJson/banner block out of public/aria-brain.html, runs them in a
 * sandbox with a controllable fetch, and asserts the CLASSIFICATION + banner text
 * for each status class — including the exact "6 auth-gated → 0 unreachable"
 * reproduction of the operator's symptom.
 *
 * Run: node --test test/brain-banner-auth-honest-rf2233.test.mjs
 */
import fs from 'fs';
import path from 'path';
import vm from 'node:vm';
// R-F3839 — the banner escapes the failure reason now; escapeHtml lives outside
// this slice, so the sandbox needs the page's own definition.
import { escapeHtmlSource } from './helpers/aria_brain_page.mjs';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..');
function read(rel) { return fs.readFileSync(path.join(ROOT, rel), 'utf-8'); }

// ── Extract the REAL fetchJson/banner block from aria-brain.html ────────────
const html = read('public/aria-brain.html');
const start = html.indexOf('const _fetchFailures = new Map();');
const end = html.indexOf('function pct(v, digits=0)');
assert.ok(start > 0 && end > start, 'could not locate fetchJson block in aria-brain.html');
const brainBlock = html.slice(start, end);
assert.ok(brainBlock.includes('_trackAuthGated'), 'R-F2233 _authGated split missing from source');
assert.ok(brainBlock.includes('API.probe'), 'fetchJson must use API.probe, not API.get');

const appJs = read('public/js/app.js');
assert.ok(appJs.includes('async probe('), 'R-F2233 API.probe missing from app.js');

// ── Build a sandbox that hosts the real API + fetchJson, with stubbable fetch ─
function makeSandbox({ token = '' } = {}) {
  const bannerEl = { style: {}, innerHTML: '' };
  let nextResponse = null; // set per-call by the test
  const sandbox = {
    console,
    setTimeout, clearTimeout, AbortController,
    localStorage: { getItem: (k) => (k === 'crucix_token' ? token : null), removeItem() {}, setItem() {} },
    window: { location: { href: '', assign() {}, replace() {} } },
    document: { getElementById: (id) => (id === 'fetch-failure-banner' ? bannerEl : null) },
    // controllable fetch — each test sets sandbox.__resp
    fetch: async (_url, _opts) => {
      const r = nextResponse;
      if (r && r.__abort) { const e = new Error('aborted'); e.name = 'AbortError'; throw e; }
      if (r && r.__neterr) { throw new Error('network down'); }
      return {
        status: r.status, ok: r.status >= 200 && r.status < 300,
        json: async () => { if (r.__badjson) throw new Error('bad json'); return r.body; },
      };
    },
    setResp: (r) => { nextResponse = r; },
    _bannerEl: bannerEl,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(appJs, sandbox, { filename: 'app.js' });
  // app.js exposes API/Auth as `const` (block-less top level) — re-export to global
  vm.runInContext('this.API = API; this.window.API = API;', sandbox);
  vm.runInContext(escapeHtmlSource(), sandbox, { filename: 'aria-brain.escapeHtml' });
  vm.runInContext(brainBlock, sandbox, { filename: 'brain.fetchJson' });
  return sandbox;
}

async function drive(sandbox, path_, resp) {
  sandbox.setResp(resp);
  return await vm.runInContext(`fetchJson(${JSON.stringify(path_)})`, sandbox);
}
// Marshal across the vm realm boundary to native arrays (else deepStrictEqual
// rejects the vm's foreign Array.prototype even when contents match).
const failures = (s) => JSON.parse(vm.runInContext('JSON.stringify([..._fetchFailures.keys()])', s));
const authGated = (s) => JSON.parse(vm.runInContext('JSON.stringify([..._authGated])', s));
// R-F2876 — 403 has its OWN bucket now: a 401 is fixed by signing in, a 403 never is.
const forbidden = (s) => JSON.parse(vm.runInContext('JSON.stringify([..._forbidden])', s));
const bannerHtml = (s) => s._bannerEl.innerHTML;
const bannerShown = (s) => s._bannerEl.style.display !== 'none';

describe('R-F2233 honest banner: 401 auth is NOT unreachable', () => {
  it('API.probe returns HTTP status and does NOT auto-logout on 401', async () => {
    const s = makeSandbox({ token: 'tok' });
    let loggedOut = false;
    vm.runInContext('Auth.logout = () => { globalThis.__loggedOut = true; };', s);
    s.setResp({ status: 401, body: { detail: 'Not authenticated' } });
    const res = await vm.runInContext(`API.probe('/api/aria/cost/monthly')`, s);
    assert.equal(res.status, 401, 'probe must surface the 401 status');
    assert.equal(s.__loggedOut, undefined, 'probe must NOT call Auth.logout (that cascades the whole page)');
  });

  it('a 401 lands in _authGated, NOT _fetchFailures; banner says sign-in, not DATA UNAVAILABLE', async () => {
    const s = makeSandbox({ token: '' });
    const out = await drive(s, '/cost/monthly', { status: 401, body: { detail: 'x' } });
    assert.equal(out, null);
    assert.deepEqual(failures(s), [], 'auth-gated 401 must NOT be counted unreachable');
    assert.deepEqual(authGated(s), ['/cost/monthly']);
    assert.ok(!bannerHtml(s).includes('DATA UNAVAILABLE'), 'must not scream DATA UNAVAILABLE on a 401');
    assert.ok(bannerHtml(s).includes('sign-in') || bannerHtml(s).includes('🔒'), 'should show a calm sign-in note');
  });

  it('REPRODUCES the operator symptom: 6 auth-gated endpoints → 0 unreachable', async () => {
    const s = makeSandbox({ token: '' });
    const gated = ['/autonomy/surface', '/learning/stats', '/cost/monthly',
                   '/cost/external', '/student/mastery', '/hallucination/stats'];
    for (const p of gated) await drive(s, p, { status: 401, body: { detail: 'x' } });
    assert.equal(failures(s).length, 0, 'ZERO genuine unreachable — the phantom count is gone');
    assert.equal(authGated(s).length, 6, 'all 6 correctly classified as auth-gated');
    assert.ok(!bannerHtml(s).includes('DATA UNAVAILABLE'));
  });

  it('a genuine timeout IS counted unreachable, with an honest reason', async () => {
    const s = makeSandbox({ token: 'tok' });
    await drive(s, '/adversarial/stats', { __abort: true });
    assert.deepEqual(failures(s), ['/adversarial/stats']);
    assert.ok(bannerHtml(s).includes('DATA UNAVAILABLE'));
    assert.ok(bannerHtml(s).includes('timeout'), 'timeout must be labelled honestly');
  });

  it('a 5xx and an error-envelope are both counted unreachable', async () => {
    const s = makeSandbox({ token: 'tok' });
    await drive(s, '/learning/stats', { status: 503, body: {} });
    await drive(s, '/cost/monthly', { status: 200, body: { error: 'ARIA service offline' } });
    assert.deepEqual(failures(s).sort(), ['/cost/monthly', '/learning/stats']);
  });

  it('a clean 200 clears prior state; recovery from 401→200 removes the auth note', async () => {
    const s = makeSandbox({ token: 'tok' });
    await drive(s, '/health', { status: 401, body: { detail: 'x' } });
    assert.deepEqual(authGated(s), ['/health']);
    const data = await drive(s, '/health', { status: 200, body: { status: 'healthy' } });
    assert.deepEqual(data, { status: 'healthy' });
    assert.deepEqual(authGated(s), [], 'recovery must clear the auth-gated note');
    assert.deepEqual(failures(s), []);
    assert.equal(bannerShown(s), false, 'banner hidden when nothing is wrong');
  });

  it('mixed load: 1 real failure + 3 auth-gated → banner shows "1 unreachable" only', async () => {
    const s = makeSandbox({ token: '' });
    await drive(s, '/adversarial/stats', { __abort: true });      // real
    await drive(s, '/cost/monthly', { status: 401, body: {} });   // auth
    await drive(s, '/learning/stats', { status: 401, body: {} }); // auth
    await drive(s, '/autonomy/surface', { status: 403, body: {} });// auth
    assert.equal(failures(s).length, 1, 'exactly ONE genuine unreachable');
    // R-F2876 — the two 401s stay auth-gated; the 403 is classified separately,
    // because no sign-in can ever unlock an operator-tier route. Total auth-ish
    // is still 3 — this contract is STRICTER than the old lumped count, not weaker.
    assert.equal(authGated(s).length, 2, 'the two 401s');
    assert.equal(forbidden(s).length, 1, 'the 403 — operator-tier, not a sign-in problem');
    const b = bannerHtml(s);
    assert.ok(b.includes('1 endpoint failed'), 'says 1, not 4');
    assert.ok(b.includes('🔒'), 'the sign-in note for the 401s');
    assert.ok(b.includes('🛡️'), 'the operator-tier note for the 403');
  });
});
