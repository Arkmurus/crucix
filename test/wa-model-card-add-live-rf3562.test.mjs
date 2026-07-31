import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const html = fs.readFileSync(new URL('../public/wa-connections.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];

test('R-F3562 empty state uses the canonical WhatsApp icon, not a generic device emoji', () => {
  assert.match(html, /class="bi bi-whatsapp"/);
  assert.doesNotMatch(html, /<div class="icon">📱<\/div>/);
  assert.match(html, /fonts\/bootstrap-icons\/font-css\.css/);
});

function managerRuntime() {
  const listeners = new Map();
  const elements = new Map();
  // Derived from every getElementById() in wa-connections.html. R-F3578 added the
  // governance controls (acceptRiskBtn, governanceStatus, officialLink, pauseBtn,
  // revokeBtn, totpCode); a missing id returns undefined and the page's
  // addEventListener wiring throws at LOAD, taking every assertion with it.
  const ids = [
    'accounts', 'createBar', 'accountName', 'createBtn', 'refreshBtn', 'qrCloseBtn',
    'qrModal', 'qrModalInner', 'loading', 'empty', 'limitNote', 'error', 'toast',
    'qrTitle', 'qrContent', 'qrStatus',
    'acceptRiskBtn', 'governanceStatus', 'officialLink', 'pauseBtn', 'revokeBtn', 'totpCode',
  ];
  for (const id of ids) {
    elements.set(id, {
      id, value: '', textContent: '', innerHTML: '', disabled: false,
      style: { display: 'none' }, classList: { add() {}, remove() {} },
      addEventListener(type, fn) { listeners.set(`${id}:${type}`, fn); },
      closest() { return null; },
    });
  }

  // Eight risk acceptances + one scope, mirroring REQUIRED_RISK_ACCEPTANCES and
  // LINKED_SCOPES in lib/whatsapp/waGovernance.mjs. Left UNCHECKED by default so
  // this stays a test about the BLANK form, which is what R-F3562 guards.
  const checkboxes = [
    ...['optional_mode', 'official_channel_available', 'unsupported_integration',
        'account_enforcement_risk', 'continuity_not_guaranteed', 'authorised_account',
        'no_unlawful_monitoring', 'privacy_retention_reviewed']
      .map((value) => ({ name: 'risk', value, checked: false, addEventListener() {} })),
    { name: 'scope', value: 'forwarded_or_tagged', checked: false, addEventListener() {} },
  ];

  const requests = [];
  const context = vm.createContext({
    console,
    document: {
      getElementById(id) { return elements.get(id); },
      createElement() { return { textContent: '', innerHTML: '' }; },
      // R-F3578 review — the governance UI reads the risk/scope checkboxes at
      // load time (wa-connections.html:554). Without this the whole script
      // throws during vm.runInContext and every assertion below is unreachable,
      // which is how the R-F3578 change broke this test at SCRIPT LOAD rather
      // than at the behaviour it guards.
      querySelectorAll(selector) {
        const boxes = String(selector).includes('name="scope"') || String(selector).includes('name="risk"')
          ? checkboxes.filter((b) => {
              if (selector.includes(':checked') && !b.checked) return false;
              if (selector.includes('name="scope"') && selector.includes('name="risk"')) return true;
              return selector.includes(`name="${b.name}"`);
            })
          : [];
        return boxes;
      },
    },
    localStorage: { getItem() { return 'test-user-jwt'; } },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (options.method === 'POST') {
        return new Response(JSON.stringify({ account: { id: 'wa_test' } }), {
          status: 200, headers: { 'content-type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({ accounts: [] }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    },
    Response,
    setInterval() { return 1; },
    setTimeout(fn) { fn(); return 1; },
    clearInterval() {},
  });
  vm.runInContext(script, context, { filename: 'wa-connections.html' });
  return { elements, listeners, requests };
}

test('R-F3562 capability: blank Add submits a real owner-authenticated creation request', async () => {
  const runtime = managerRuntime();
  const submit = runtime.listeners.get('createBar:submit');
  assert.equal(typeof submit, 'function', 'the visible create form must be wired');

  let prevented = false;
  submit({ preventDefault() { prevented = true; } });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(prevented, true, 'form submission must remain in the embedded manager');
  const post = runtime.requests.find((request) => request.options.method === 'POST');
  assert.ok(post, 'clicking Add with the operator-visible blank field must call account creation');
  assert.equal(post.url, '/api/wa-listener/accounts');
  assert.equal(post.options.headers.Authorization, 'Bearer test-user-jwt');
  assert.deepEqual(JSON.parse(post.options.body), { name: 'My WhatsApp' });
  assert.equal(runtime.elements.get('createBtn').disabled, false);
  assert.equal(runtime.elements.get('createBtn').textContent, '+ Add');
});
