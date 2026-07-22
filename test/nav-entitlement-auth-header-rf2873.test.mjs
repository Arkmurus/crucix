// test/nav-entitlement-auth-header-rf2873.test.mjs
//
// R-F2873 — the nav entitlement fetch sent NO Authorization header, so every
// gated link hid from every user, including the admin.
//
// R-F2822 reveals gated nav links from GET /api/auth/nav-pages. It built the
// request as:
//
//     headers: (window.API && API.headers) ? API.headers() : {},
//
// `API` is declared `const API = {...}` in public/js/app.js, loaded as a CLASSIC
// script (<script src="js/app.js">, no type="module"). Top-level const/let bind
// to the global LEXICAL environment — reachable as bare `API`, but NEVER as a
// property of `window`. Only `var` and function declarations become window
// properties.
//
// So `window.API` was always undefined, the guard always chose `{}`, the fetch
// went out UNAUTHENTICATED, requireAuth returned 401, `allowed` stayed [], and
// every [data-gated] link stayed hidden — silently, because the handler
// deliberately fails closed and never throws.
//
// Proven empirically before fixing: in a context with `window = globalThis` and
// a classic-script `const API`, `typeof window.API` is "undefined" and the guard
// evaluates to the empty-headers branch.
//
// This is a KNOWN class in this codebase. public/js/network.js:7-10 documents the
// identical defect being fixed there (R-F2354: "`Auth` is a const global (NOT
// window.Auth) ... silently short-circuited to {} → boot() aborted"). The pattern
// survived in sidebar.js and aria-brain.html.
//
// Live evidence 2026-07-22: [Auth] login OK ... role=admin, and the server
// returned all 11 routes for that account — yet the tabs stayed hidden, because
// the browser never sent the token it had.
//
// Run: node --test test/nav-entitlement-auth-header-rf2873.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SIDEBAR = readFileSync(new URL('../public/js/sidebar.js', import.meta.url), 'utf8');
const BRAIN = readFileSync(new URL('../public/aria-brain.html', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/js/app.js', import.meta.url), 'utf8');

/** The _applyNavEntitlement method body. Anchored on DEFINITIONS: an earlier
 *  `_bindEvents()` CALL sits above the method, so slicing to the bare name
 *  produced a backwards (empty) window and the assertions silently passed on
 *  nothing. Anchor on `_bindEvents() {` — the definition — which follows it. */
function navBlock() {
  const start = SIDEBAR.indexOf('async _applyNavEntitlement()');
  if (start < 0) throw new Error('_applyNavEntitlement definition not found');
  const end = SIDEBAR.indexOf('_bindEvents() {', start);
  if (end <= start) throw new Error('block window is invalid');
  return SIDEBAR.slice(start, end);
}

test('R-F2873: the premise — API really is a const global, not a window property', () => {
  assert.match(APP, /^const API = \{/m,
    'app.js declares API with const; if this ever becomes var/window.API, revisit');
  const ctx = vm.createContext({});
  ctx.window = ctx;
  vm.runInContext('const API = { headers() { return { Authorization: "Bearer x" }; } };', ctx);
  assert.equal(vm.runInContext('typeof API', ctx), 'object', 'bare API resolves');
  assert.equal(vm.runInContext('typeof window.API', ctx), 'undefined',
    'window.API does NOT resolve — this is the whole bug');
});

test('R-F2873: the entitlement fetch no longer guards on window.API', () => {
  // Strip comment lines first: this block's own comment cites the OLD expression
  // to explain the bug, and matching that would make the guard fire on its own
  // documentation (the same trap as the deploy.sh check in R-F2868).
  const code = navBlock()
    .split(/\r?\n/)
    .filter((l) => !l.trim().startsWith('//'))
    .join('\n');
  assert.ok(!/window\.API/.test(code),
    'THE BUG: window.API is always undefined, so the fetch sent no auth header');
});

test('R-F2873: it resolves API via typeof, which DOES see a lexical global', () => {
  const block = navBlock();
  assert.match(block, /typeof API !== 'undefined'/,
    'typeof is the correct existence check for a const-declared global');
  assert.match(block, /API\.headers\(\)/, 'the auth header must still be attached');
});

test('R-F2873: NEGATIVE CONTROL — the fixed guard actually yields headers', () => {
  // Run the real guard expression in a browser-like context and assert it
  // chooses the header branch. The old expression must choose {}.
  const ctx = vm.createContext({});
  ctx.window = ctx;
  vm.runInContext('const API = { headers() { return { Authorization: "Bearer t" }; } };', ctx);
  const fixed = vm.runInContext(
    "(typeof API !== 'undefined' && API.headers) ? API.headers() : {}", ctx);
  const broken = vm.runInContext(
    "(window.API && API.headers) ? API.headers() : {}", ctx);
  assert.equal(fixed.Authorization, 'Bearer t', 'the fix must send the token');
  // NB compare by shape, not deepStrictEqual: `broken` is created in the vm
  // realm, so its prototype differs from this realm's Object.prototype.
  assert.equal(Object.keys(broken).length, 0,
    'the old guard must be shown to send nothing');
});

test('R-F2873: aria-brain hasToken no longer uses window.API', () => {
  // Same class: hasToken was always false, so a signed-in operator was told to
  // "sign in" rather than "re-authenticate" — a misleading message, not a lockout.
  assert.ok(!/window\.API && API\.token/.test(BRAIN),
    'hasToken must not depend on window.API');
  assert.match(BRAIN, /typeof API !== 'undefined' && API\.token/,
    'hasToken must resolve API lexically');
});

test('R-F2873: the fail-closed behaviour is preserved', () => {
  // R-F2822 hides links on any failure rather than showing ones that bounce.
  // Fixing the header must not turn that into a permissive default.
  const block = navBlock();
  assert.match(block, /let allowed = \[\]/, 'the default must remain empty');
  assert.match(block, /if \(r\.ok\)/, 'only a successful response may populate it');
});
