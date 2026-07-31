// test/wa-binding-ui-rf3593.test.mjs
//
// R-F3593 — the binding API (R-F3587) had no surface, so nobody could actually
// verify a handset, so verified-only mode could not be switched on without
// locking the operator out. This is that surface.
//
// The page script is EXECUTED here, not grepped. A previous session's lesson,
// recorded in ui_unverified_claim_defect_class: "a source assertion proves
// SHAPE, not BEHAVIOUR — RUN THE PATH." Markup being present says nothing about
// whether the button is wired or the fetch goes anywhere.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../public/wa-connections.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];

function runtime({ bindingResponse, mintExtra } = {}) {
  const listeners = new Map();
  const elements = new Map();
  const ids = [
    'accounts', 'createBar', 'accountName', 'createBtn', 'refreshBtn', 'qrCloseBtn',
    'qrModal', 'qrModalInner', 'loading', 'empty', 'limitNote', 'error', 'toast',
    'qrTitle', 'qrContent', 'qrStatus',
    'acceptRiskBtn', 'governanceStatus', 'officialLink', 'pauseBtn', 'revokeBtn', 'totpCode',
    // R-F3593
    'bindPanel', 'bindStatus', 'bindCodeBtn', 'bindRefreshBtn', 'unbindBtn',
    'bindCodeBox', 'bindCode', 'bindExpiry',
  ];
  for (const id of ids) {
    elements.set(id, {
      id, value: '', textContent: '', innerHTML: '', disabled: false,
      style: { display: 'none' }, classList: { add() {}, remove() {} },
      addEventListener(type, fn) { listeners.set(`${id}:${type}`, fn); },
      closest() { return null; },
    });
  }
  const checkboxes = [];
  const requests = [];
  const context = vm.createContext({
    console,
    confirm: () => true,
    document: {
      // R-F3599 — MINT ON DEMAND. This file enumerated ids and rotted the moment
      // R-F3599 added `bindTarget`: three tests failed on the stub being
      // incomplete, not on the behaviour they guard. Same fix already applied to
      // wa-model-card-add-live-rf3562 — a fixed id list cannot survive a page
      // that grows.
      getElementById(id) {
        if (!elements.has(id)) {
          elements.set(id, {
            id, value: '', textContent: '', innerHTML: '', disabled: false,
            style: { display: 'none' }, classList: { add() {}, remove() {} },
            addEventListener(type, fn) { listeners.set(`${id}:${type}`, fn); },
            closest() { return null; },
          });
        }
        return elements.get(id);
      },
      createElement() { return { textContent: '', innerHTML: '' }; },
      querySelectorAll() { return checkboxes; },
    },
    localStorage: { getItem: () => 'test-user-jwt' },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      // Exact-match the stub too: a prefixed URL must NOT be served a happy
      // response, or the test keeps passing while the page 404s.
      const body = String(url) === '/api/wa/binding/code'
        ? { ok: true, code: '445566', expiresAt: '2026-07-31T23:00:00Z', ...(mintExtra || {}) }
        : String(url) === '/api/wa/binding'
          ? (bindingResponse ?? { bound: false, pairingPending: false })
          : { accounts: [] };
      return new Response(JSON.stringify(body), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    },
    Response,
    setInterval: () => 1,
    setTimeout: (fn) => { fn(); return 1; },
    clearInterval() {},
  });
  vm.runInContext(script, context, { filename: 'wa-connections.html' });
  return { elements, listeners, requests };
}

test('R-F3593 the verify panel exists and explains what proves what', () => {
  assert.match(html, /id="bindPanel"/);
  assert.match(html, /from the handset you want to verify/);
  assert.match(html, /Being signed in proves the account; sending the code proves the phone/,
    'the two-factor nature is the whole point — the user has to understand why '
    + 'both halves are needed, or they will not complete the second one');
});

test('R-F3593 the buttons are actually wired', () => {
  const { listeners } = runtime();
  for (const id of ['bindCodeBtn', 'bindRefreshBtn', 'unbindBtn']) {
    assert.equal(typeof listeners.get(`${id}:click`), 'function', `${id} has no handler`);
  }
});

test('R-F3593 generating a code calls the API and displays what came back', async () => {
  const { listeners, elements, requests } = runtime();
  await listeners.get('bindCodeBtn:click')();

  // R-F3598 — assert the EXACT url, not includes().
  //
  // This originally used .includes('/api/wa/binding/code') and PASSED against a
  // live 404: apiFetch() prepends '/api/wa-listener', so the real request was
  // POST /api/wa-listener/api/wa/binding/code — which still CONTAINS the
  // substring. A containment check cannot detect a wrong prefix, which is the
  // most likely way a URL is wrong.
  const post = requests.find((r) => String(r.options?.method) === 'POST');
  assert.ok(post, 'no request was made — the button is decorative');
  assert.equal(post.url, '/api/wa/binding/code',
    `posted to ${post.url} — the binding routes are on aria-web, NOT under the `
    + `/api/wa-listener proxy prefix`);
  assert.match(post.options.headers?.Authorization || '', /^Bearer /,
    'the mint route is Bearer-gated; without the header every real user 401s');

  // The displayed code must be the SERVER's, never generated in the browser —
  // a client-side code would not exist in the listener and could never be honoured.
  assert.equal(elements.get('bindCode').textContent, '445566');
  assert.equal(elements.get('bindCodeBox').style.display, '');
  assert.match(elements.get('bindExpiry').textContent, /single use/);
});

test('R-F3593 an already-verified account shows verified and offers unlink', async () => {
  const { elements } = runtime({
    bindingResponse: { bound: true, identityCount: 3, boundAt: '2026-07-31T20:00:00Z' },
  });
  await new Promise((r) => setImmediate(r));
  assert.match(elements.get('bindStatus').textContent, /Verified/);
  assert.equal(elements.get('unbindBtn').style.display, '', 'unlink must be reachable');
});

test('R-F3593 a failed status check does NOT report "not verified"', async () => {
  // Claiming a security state you did not observe is the false-clean shape this
  // repo keeps returning to. An error must read as an error.
  const listeners = new Map();
  const elements = new Map();
  for (const id of ['bindStatus', 'bindCodeBtn', 'bindRefreshBtn', 'unbindBtn', 'bindCodeBox',
                    'bindCode', 'bindExpiry', 'error', 'toast']) {
    elements.set(id, { id, textContent: '', style: { display: 'none' },
                       classList: { add() {}, remove() {} },
                       addEventListener(t, f) { listeners.set(`${id}:${t}`, f); } });
  }
  const src = script.slice(script.indexOf('async function refreshBinding'),
                           script.indexOf('async function generateBindCode'));
  const ctx = vm.createContext({
    console,
    document: { getElementById: (id) => elements.get(id) || { style: {}, textContent: '' } },
    // R-F3598 — inject the helper the code ACTUALLY calls. Injecting the old
    // name left bindingFetch undefined, so this passed on a ReferenceError
    // instead of on the simulated network failure — green for the wrong reason.
    bindingFetch: async () => { throw new Error('network down'); },
    formatTime: () => 'x',
  });
  vm.runInContext(src + '\nrefreshBinding();', ctx, { filename: 'refreshBinding' });
  await new Promise((r) => setImmediate(r));
  const text = elements.get('bindStatus').textContent;
  assert.match(text, /Could not check/, `status read "${text}" — an unreachable check must not render as a verdict`);
  assert.doesNotMatch(text, /Not verified/);
});


test('R-F3598 the binding calls do NOT go through the listener-proxy helper', () => {
  // apiFetch() prepends `const API = '/api/wa-listener'`. The binding routes are
  // aria-web's own, so any binding call routed through it 404s.
  const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const start = src.indexOf('async function refreshBinding');
  const block = src.slice(start);
  assert.doesNotMatch(block.slice(0, 3000), /apiFetch\('\/api\/wa\/binding/,
    'a binding call is using apiFetch again — it will be prefixed and 404'
  );
  assert.match(block.slice(0, 3000), /bindingFetch\('\/api\/wa\/binding/);
});

test('R-F3598 every URL the page requests for binding is absolute and correct', async () => {
  const { listeners, requests } = runtime();
  await listeners.get('bindCodeBtn:click')();
  const bindingCalls = requests.filter((r) => String(r.url).includes('binding'));
  assert.ok(bindingCalls.length >= 2, 'expected a status read and a mint');
  for (const c of bindingCalls) {
    assert.ok(['/api/wa/binding', '/api/wa/binding/code'].includes(String(c.url)),
      `unexpected binding URL: ${c.url}`);
  }
});


// ── R-F3599 — a code with no destination is not a flow ──────────────────────

test('R-F3599 the code box tells the user WHICH number to text', async () => {
  const { listeners, elements } = runtime({ mintExtra: { ariaNumber: '351912345678' } });
  await listeners.get('bindCodeBtn:click')();
  const target = elements.get('bindTarget').textContent;
  assert.match(target, /\+351912345678/,
    'the code was shown without a destination — the operator asked exactly this: '
    + '"how would users know which number to text once they receive the code?"');
});

test('R-F3599 an offline ARIA is stated, never papered over with a stale number', async () => {
  const { listeners, elements } = runtime({ mintExtra: { ariaNumber: '' } });
  await listeners.get('bindCodeBtn:click')();
  const target = elements.get('bindTarget').textContent;
  assert.match(target, /not connected/i);
  assert.doesNotMatch(target, /\+\d/, 'no number may be printed when there is none');
});

test('R-F3599 the number is DERIVED from the live session, not an env var', () => {
  const listener = fs.readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
  const code = listener.split(/\r?\n/).filter((l) => !l.trim().startsWith('//')).join('\n');
  const i = code.indexOf('function _waOwnNumber');
  const body = code.slice(i, i + 500);
  assert.match(body, /sock\?\.user\?\.id/, 'must read the live session identity');
  assert.match(body, /isConnected/, 'a disconnected session must yield no number');
  assert.doesNotMatch(body, /process\.env/,
    'a hand-set variable drifts from what ARIA is actually reachable on — that is '
    + 'the declared-capability-flag-drift class');
});

test('R-F3599 the mint response carries the destination with the code', () => {
  const listener = fs.readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');
  const i = listener.indexOf("return res.json({ ok: true, expiresAt: issued.pairing.expiresAt");
  assert.ok(i > 0, 'mint response not found');
  assert.match(listener.slice(i, i + 160), /ariaNumber/,
    'the code and its destination must arrive together — a second round trip to '
    + 'learn where to send it is how a user ends up holding a code and guessing');
});


// ── R-F3600 — the confirm dialog belongs to the page, not to the browser ────

test('R-F3600 no raw window.confirm remains in the page script', () => {
  const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const code = src.split(/\r?\n/).filter((l) => !l.trim().startsWith('//')).join('\n');
  assert.doesNotMatch(code, /(^|[^.\w])confirm\s*\(/,
    'a raw window.confirm() is back — it renders as OS chrome with the site URL '
    + 'printed above it, next to a page that already ships a modal system');
  assert.match(code, /confirmDialog\(/);
});

test('R-F3600 the dialog reuses the page design system, not bespoke styling', () => {
  const dlg = html.slice(html.indexOf('id="confirmModal"'), html.indexOf('id="qrModal"'));
  assert.match(dlg, /class="modal-overlay"/, 'must reuse the existing overlay');
  assert.match(dlg, /class="modal"/);
  assert.match(dlg, /class="btn btn-danger"/, 'destructive action needs the danger button style');
});

test('R-F3600 it is a real dialog for assistive tech', () => {
  const dlg = html.slice(html.indexOf('id="confirmModal"'), html.indexOf('id="qrModal"'));
  assert.match(dlg, /role="dialog"/);
  assert.match(dlg, /aria-modal="true"/);
  assert.match(dlg, /aria-labelledby="confirmTitle"/);
});

test('R-F3600 escape and the overlay cancel, and focus starts on Cancel', () => {
  const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const fn = src.slice(src.indexOf('function confirmDialog'), src.indexOf('function confirmDialog') + 1800);
  assert.match(fn, /e\.key === 'Escape'/, 'Escape must cancel');
  assert.match(fn, /e\.target === modal/, 'clicking the overlay must cancel');
  assert.match(fn, /cancel\.focus\(\)/,
    'focus must start on Cancel — a destructive action one stray Enter away is '
    + 'exactly what the native dialog allowed');
});

test('R-F3600 the dialog resolves false by default and cleans up its listeners', () => {
  const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const fn = src.slice(src.indexOf('function confirmDialog'), src.indexOf('function confirmDialog') + 1800);
  assert.match(fn, /removeEventListener\('keydown'/,
    'a keydown listener left attached accumulates on every open');
  assert.match(fn, /close\(false\)/);
});

test('R-F3600 unlink actually awaits the dialog before calling the API', async () => {
  // The failure mode of swapping a synchronous confirm() for a Promise is
  // forgetting the await — the API call then fires regardless of the answer.
  const src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const fn = src.slice(src.indexOf('async function unbindHandset'), src.indexOf('async function unbindHandset') + 800);
  assert.match(fn, /const yes = await confirmDialog\(/);
  assert.match(fn, /if \(!yes\) return;/, 'the DELETE must not run when the user cancels');
  assert.ok(fn.indexOf('await confirmDialog') < fn.indexOf("method: 'DELETE'"),
    'the confirmation must precede the request');
});


// ── R-F3601 — the official-number card ──────────────────────────────────────

function governanceRuntime(official) {
  const listeners = new Map();
  const elements = new Map();
  const mk = (id) => ({
    id, textContent: '', innerHTML: '', href: '', value: '', disabled: false,
    style: { display: 'none' }, classList: { add() {}, remove() {} },
    _attrs: {},
    setAttribute(k, v) { this._attrs[k] = v; },
    removeAttribute(k) { delete this._attrs[k]; },
    addEventListener(t, f) { listeners.set(`${id}:${t}`, f); },
    closest() { return null; },
  });
  const doc = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, mk(id)); return elements.get(id); },
    createElement: () => mk('tmp'),
    querySelectorAll: () => [],
  };
  const src = script.slice(script.indexOf('function renderGovernance'), script.indexOf('function updateRiskButton'));
  const ctx = vm.createContext({
    console, document: doc, formatTime: () => 'x',
    governance: { official, linked: { active: false, status: 'not_configured', scopes: [] } },
    accounts: [],
  });
  vm.runInContext(src + '\nrenderGovernance();', ctx, { filename: 'renderGovernance' });
  return elements;
}

test('R-F3601 a configured number is SHOWN, not just linked', () => {
  const els = governanceRuntime({ enabled: true, number: '+351932015591' });
  assert.equal(els.get('officialNumber').textContent, '+351932015591',
    'the number never appeared on the page — a user wanting to save the contact '
    + 'or message from another device had no way to find it');
  assert.equal(els.get('officialNumber').style.display, '');
});

test('R-F3601 the button is a real wa.me click-to-chat deep link', () => {
  const els = governanceRuntime({ enabled: true, number: '+351 932 015 591' });
  assert.equal(els.get('officialLink').href, 'https://wa.me/351932015591',
    'wa.me requires digits only — spaces or a leading + break the link');
});

test('R-F3601 an unconfigured number leaves an INERT control, not a live dead link', () => {
  const els = governanceRuntime({ enabled: false, number: null });
  const link = els.get('officialLink');
  assert.equal(link._attrs['aria-disabled'], 'true');
  assert.equal(link.style.pointerEvents, 'none',
    'aria-disabled tells a screen reader the control is unavailable and does '
    + 'NOTHING to a mouse — the anchor stayed clickable and navigated to "#"');
  assert.equal(els.get('officialNumber').style.display, 'none');
});

test('R-F3601 enabled-but-empty is treated as unconfigured', () => {
  // Trusting `enabled` alone would render "+" and a wa.me link to nowhere.
  const els = governanceRuntime({ enabled: true, number: '' });
  assert.equal(els.get('officialLink').style.pointerEvents, 'none');
  assert.equal(els.get('officialNumber').style.display, 'none');
});
