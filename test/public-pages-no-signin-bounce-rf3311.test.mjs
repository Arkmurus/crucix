/**
 * R-F3308..R-F3311 — the public surface must work for a logged-OUT visitor.
 *
 * R-F3311 is the one that mattered. The landing page's "Read the model card"
 * button led to a login wall. Chain, all four links verified in the source:
 *
 *   model-card.html  -> Sidebar.init('model-card')      (page is public by design;
 *                                                        it says so in a comment)
 *   sidebar.js       -> Auth.me()
 *   app.js Auth.me   -> API.get('/api/auth/me')
 *   app.js API.get   -> 401 ? Auth.logout()             -> location = '/signin.html'
 *
 * and `GET /api/auth/me` returns 401 for an anonymous visitor. So the model
 * card, the one document written FOR people evaluating ARIA before signing up,
 * bounced every one of them to a sign-in form. Opting the page out of
 * Auth.requireAuth() achieved nothing, because the shell it loads reached auth
 * through a side door.
 *
 * This test EXECUTES app.js against a stubbed browser rather than grepping it,
 * because the defect is behavioural: every identifier involved was spelled
 * correctly and the page carried a comment asserting it was public.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { baseRules, declarationsFor } from './helpers/css_match.mjs';

const APP_JS = readFileSync(join('public', 'js', 'app.js'), 'utf8');
const SIDEBAR_JS = readFileSync(join('public', 'js', 'sidebar.js'), 'utf8');
const MODEL_CARD = readFileSync(join('public', 'model-card.html'), 'utf8');
const INDEX = readFileSync(join('public', 'index.html'), 'utf8');
const PARTNERS = readFileSync(join('public', 'partners.html'), 'utf8');
const AUTH_CSS = readFileSync(join('public', 'css', 'auth.css'), 'utf8');
const LANDING_CSS = readFileSync(join('public', 'pelican', 'assets', 'css', 'style.css'), 'utf8');

const AUTH_PAGES = ['signin', 'signup', 'partners', 'forgot-password', 'recovery'];

// Auth.logout() awaits a server call before it clears the token and navigates,
// and API.get does not await logout (it returns null to its caller straight
// away, exactly as it did before this fix). So the redirect lands a tick after
// the call resolves. Assert after a real tick rather than assuming ordering.
const flush = () => new Promise((r) => setTimeout(r, 5));

/**
 * Run app.js in a stubbed browser and report what it did.
 * `token` = what localStorage holds before the call; null = anonymous.
 */
function runApp({ token, status = 401 }) {
  const store = new Map();
  if (token) store.set('crucix_token', token);

  const nav = { assigned: [] };
  const fetches = [];

  const ctx = {
    console: { log() {}, warn() {}, error() {} },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    fetch: async (url) => {
      fetches.push(String(url));
      return { status, ok: status >= 200 && status < 300, json: async () => ({}) };
    },
    document: { addEventListener() {}, getElementById: () => null, querySelectorAll: () => [] },
    setTimeout, clearTimeout,
  };
  ctx.window = ctx;
  // A navigation is the whole point of the test, so record rather than perform.
  Object.defineProperty(ctx.window, 'location', {
    value: {
      _href: '/model-card.html',
      get href() { return this._href; },
      set href(v) { nav.assigned.push(v); this._href = v; },
    },
  });

  vm.createContext(ctx);
  // app.js declares `const API`/`const Auth`, and a top-level const is a lexical
  // binding rather than a property of the global object, so the context has no
  // .API until we publish it. The pages get these off `window` the same way a
  // classic <script> does.
  vm.runInContext(`${APP_JS}\n;globalThis.API = API; globalThis.Auth = Auth;`, ctx, { filename: 'app.js' });
  return { ctx, nav, fetches, store };
}

test('R-F3311 an anonymous visitor on a public page is NOT redirected to sign-in', async () => {
  const { ctx, nav, store } = runApp({ token: null, status: 401 });

  const result = await ctx.API.get('/api/auth/me');
  await flush();

  assert.equal(result, null, 'API.get should still report "no data" on a 401');
  assert.deepEqual(
    nav.assigned, [],
    `a logged-out visitor was navigated to ${nav.assigned.join(', ')} — the model card is public`,
  );
  assert.equal(store.has('crucix_token'), false, 'nothing to clear, and nothing was invented');
});

test('R-F3311 a genuinely EXPIRED session still logs out (the guard is not a blanket opt-out)', async () => {
  const { ctx, nav, store } = runApp({ token: 'expired-jwt', status: 401 });

  await ctx.API.get('/api/auth/some-protected-thing');
  await flush();

  assert.deepEqual(nav.assigned, ['/signin.html'],
    'a rejected token must still end the session — otherwise this "fix" is a security regression');
  assert.equal(store.has('crucix_token'), false, 'the dead token must be cleared');
});

test('R-F3311 mutating verbs get the same treatment, not just GET', async () => {
  for (const verb of ['put', 'del']) {
    const anon = runApp({ token: null, status: 401 });
    await anon.ctx.API[verb]('/api/whatever', {});
    await flush();
    assert.deepEqual(anon.nav.assigned, [], `API.${verb} bounced an anonymous caller`);

    const expired = runApp({ token: 'expired-jwt', status: 401 });
    await expired.ctx.API[verb]('/api/whatever', {});
    await flush();
    assert.deepEqual(expired.nav.assigned, ['/signin.html'], `API.${verb} ignored a dead token`);
  }
});

test('R-F3311 Auth.me() does not even ask the server when there is no session', async () => {
  const { ctx, fetches } = runApp({ token: null, status: 401 });

  const user = await ctx.Auth.me();

  assert.equal(user, null, 'no token means no user');
  assert.deepEqual(fetches, [],
    `Auth.me() called ${fetches.join(', ')} while logged out — that 401 is what triggered the bounce`);
});

test('R-F3425 the public model card has no authenticated account shell', () => {
  const mcCode = MODEL_CARD.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(mcCode, /Auth\.requireAuth\(\)/, 'the model card must stay public');
  assert.doesNotMatch(MODEL_CARD, /Sidebar\.init\(|js\/sidebar\.js|js\/app\.js/,
    'a public assurance document must not render an account menu');
  assert.doesNotMatch(MODEL_CARD, /btn-logout|Sign Out|nav-avatar|nav-role/,
    'the model card invented an anonymous Account/analyst identity');
  assert.match(MODEL_CARD, /href="\/"[^>]*>[\s\S]*?Back to imaria\.io/,
    'the public document needs an ordinary route back to the landing page');
  const directLinks = [...INDEX.matchAll(/href="\/model-card\.html(?:#[^"]*)?"/g)];
  assert.ok(directLinks.length >= 2,
    'both the landing CTA and footer must link directly to the public model card');
});

test('R-F3425 the public-document layout does not reserve space for the removed account rail', () => {
  assert.match(MODEL_CARD, /body\.public-document #app-main\s*\{[^}]*margin-left:\s*0/,
    'removing the sidebar without removing its reserved margin leaves a broken page');
});

test('R-F3309 the landing offers a design-partner route, and it reaches the public application', () => {
  assert.match(INDEX, /href="\/partners\.html"/,
    'the landing has no design-partner entry point');
  // partners.html is the PUBLIC application. design-partners.html is the
  // admin review queue and would 302 a prospect to sign-in.
  assert.doesNotMatch(INDEX, /href="\/design-partners\.html"/,
    'the landing links the admin queue instead of the public application form');
  assert.match(PARTNERS, /\/api\/design-partners\/apply/,
    'partners.html must post to the public apply endpoint');
  assert.doesNotMatch(PARTNERS, /Auth\.requireAuth\(\)|Auth\.requireAdmin\(\)/,
    'the application form must not require an account');
});

test('R-F3308/R-F3313 the footer is one line, and nothing in it can wrap', () => {
  const rules = baseRules(LANDING_CSS);
  // The real chain, read off public/index.html. R-F3313 removed the col-md-4
  // grid; if this list stops matching the markup the guard silently measures
  // an element that no longer exists.
  assert.match(INDEX, /<div class="footer-bar">/, 'the footer bar markup changed');
  assert.doesNotMatch(INDEX, /<footer[\s\S]{0,400}col-md-4/,
    'the footer is back on the rigid third-width grid that forced the links to wrap');
  const ancestors = [
    { tag: 'body', classes: [], id: null },
    { tag: 'div', classes: ['wrapper'], id: null },
    { tag: 'div', classes: ['main'], id: 'main' },
    { tag: 'footer', classes: ['footer-sm'], id: null },
    { tag: 'div', classes: ['container-m'], id: null },
    { tag: 'div', classes: ['footer-bar'], id: null },
  ];
  const bar = declarationsFor(rules, { tag: 'div', classes: ['footer-bar'], id: null }, ancestors.slice(0, -1));
  const list = declarationsFor(rules, { tag: 'ul', classes: [], id: null }, ancestors);
  const item = declarationsFor(rules, { tag: 'li', classes: [], id: null }, [...ancestors, { tag: 'ul', classes: [], id: null }]);
  const copyright = declarationsFor(rules, { tag: 'h6', classes: [], id: null }, ancestors);

  // R-F3313: one line is a structural property, not a hope about text width.
  assert.equal(bar.get('display'), 'flex', 'the footer bar is not a flex row');
  assert.equal(bar.get('flex-wrap'), 'nowrap', 'the footer bar may wrap onto a second line');
  assert.equal(list.get('display'), 'flex', 'the link list is not laid out as a row');
  assert.equal(list.get('flex-wrap'), 'nowrap', 'the link list may break mid-list');
  assert.equal(list.get('white-space'), 'nowrap', 'a link label may break across lines');

  // R-F3308: and no inherited spacing to sit off-centre on.
  assert.equal(list.get('padding-left'), '0',
    'the footer link list keeps the UA list indent, so it renders off-centre');
  assert.equal(list.get('margin'), '0',
    "the footer link list keeps Bootstrap's bottom margin, so it sits above the bar centre");
  assert.equal(item.get('margin'), '0',
    'the list items keep their side margins, which double up with the flex gap');
  assert.equal(copyright.get('margin-bottom'), '0',
    "the copyright keeps Bootstrap's 0.5rem bottom margin");
});

test('R-F3310 auth colour has ONE source, and it is the landing palette', () => {
  // The palette was copy-pasted into five pages and had already drifted.
  assert.match(AUTH_CSS, /--sc-prime:\s*#4285f4/, 'auth.css must rebind the accent to the landing blue');
  assert.match(AUTH_CSS, /--sc-heading:\s*#364655/, 'auth.css must rebind headings to the landing slate');

  const RETIRED = /#913BFF|#0066FF|145,\s*59,\s*255|0,\s*102,\s*255|124,\s*58,\s*237|#f1ecfb|#faf7f1/i;
  for (const page of AUTH_PAGES) {
    const html = readFileSync(join('public', `${page}.html`), 'utf8');
    assert.match(html, /href="css\/auth\.css/, `${page}.html does not load the shared palette`);
    const styles = [...html.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
    const rendered = styles.replace(/\/\*[\s\S]*?\*\//g, '');
    assert.doesNotMatch(rendered, RETIRED,
      `${page}.html still hardcodes the retired violet palette, which overrides auth.css`);
  }
});

test('R-F3310 rebinding the token actually repaints the shared components', () => {
  // The mechanism is token re-binding, not per-component overrides: aria.css
  // builds .sc-btn-primary from var(--sc-prime). If that ever stops being true,
  // the auth buttons silently revert to violet while this file still says blue.
  const ARIA_CSS = readFileSync(join('public', 'css', 'aria.css'), 'utf8');
  assert.match(ARIA_CSS, /\.sc-btn-primary\s*\{[^}]*background:\s*var\(--sc-prime\)/,
    'aria.css no longer reads the accent from a token, so .auth-page rebinding cannot reach it');
  // and the hardcoded violet shadows in aria.css are overridden inside the shell
  assert.match(AUTH_CSS, /\.auth-page \.sc-btn-primary\s*\{[^}]*rgba\(66,\s*133,\s*244/);
  assert.match(AUTH_CSS, /\.auth-page \.sc-field input:focus[\s\S]{0,160}rgba\(66,\s*133,\s*244/);
});
