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

function runtime({ bindingResponse } = {}) {
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
      getElementById(id) { return elements.get(id); },
      createElement() { return { textContent: '', innerHTML: '' }; },
      querySelectorAll() { return checkboxes; },
    },
    localStorage: { getItem: () => 'test-user-jwt' },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      const body = String(url).includes('/api/wa/binding/code')
        ? { ok: true, code: '445566', expiresAt: '2026-07-31T23:00:00Z' }
        : String(url).includes('/api/wa/binding')
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

  const post = requests.find((r) => String(r.url).includes('/api/wa/binding/code'));
  assert.ok(post, 'no request was made — the button is decorative');
  assert.equal(post.options.method, 'POST');
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
    apiFetch: async () => { throw new Error('network down'); },
    formatTime: () => 'x',
  });
  vm.runInContext(src + '\nrefreshBinding();', ctx, { filename: 'refreshBinding' });
  await new Promise((r) => setImmediate(r));
  const text = elements.get('bindStatus').textContent;
  assert.match(text, /Could not check/, `status read "${text}" — an unreachable check must not render as a verdict`);
  assert.doesNotMatch(text, /Not verified/);
});
