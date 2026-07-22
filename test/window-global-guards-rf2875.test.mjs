// test/window-global-guards-rf2875.test.mjs
//
// R-F2875 — the last `window.X` guards on const-declared globals, plus a
// repo-wide guard so the CLASS cannot come back.
//
// public/js/app.js declares `const API`, `const Auth`, `const Nav`; sidebar.js
// declares `const Sidebar`. In a CLASSIC script, top-level const/let bind to the
// global LEXICAL environment — reachable as bare identifiers, but NEVER as
// properties of `window` (only var and function declarations are). Every
// `window.API` / `window.Auth` test is therefore permanently false, and each one
// silently takes its fallback branch.
//
// This has now bitten the codebase FOUR times:
//   R-F2354 network.js  — `window.Auth && Auth.user()` → boot() aborted, no buttons wired
//   R-F2873 sidebar.js  — `window.API && API.headers` → nav fetch sent NO auth header,
//                          so every gated tab hid from every user including the admin
//   R-F2873 aria-brain  — `window.API && API.token` → hasToken always false, told a
//                          signed-in operator to "sign in" instead of "re-authenticate"
//   R-F2875 (this one)  — news.html + account.html, below
//
// Fixing the two remaining sites is easy; stopping the fifth occurrence is the
// point. The last test here is a repo-wide ratchet.
//
// NOTE on news.html: it declared `const API = window.API || {fallback}` INSIDE an
// IIFE — a local binding that shadows the global. `typeof API` in its own
// initializer would hit the temporal dead zone (ReferenceError), so the fix must
// RENAME the local rather than swap the guard. That trap is why this is a
// separate ticket and not a blind find-and-replace.
//
// Run: node --test test/window-global-guards-rf2875.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const PUBLIC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
const read = (p) => readFileSync(join(PUBLIC_DIR, p), 'utf8');

/** Source with // and /* *\/ comments stripped, so a guard never fires on its own
 *  documentation (the trap that broke my own checks in R-F2868 and R-F2873). */
function codeOnly(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split(/\r?\n/)
    .filter((l) => !l.trim().startsWith('//'))
    .join('\n');
}

// ── the two remaining sites ──────────────────────────────────────────────────

test('R-F2875: news.html no longer resolves API through window', () => {
  const code = codeOnly(read('news.html'));
  assert.ok(!/window\.API/.test(code),
    'window.API is permanently undefined — the fallback was always taken');
});

test('R-F2875: news.html does not shadow API with a TDZ self-reference', () => {
  const code = codeOnly(read('news.html'));
  // `const API = typeof API !== 'undefined' ? API : {...}` would throw at runtime:
  // the initializer references the binding being declared. The local must be renamed.
  assert.ok(!/const\s+API\s*=\s*[^;]*typeof\s+API/.test(code),
    'a const cannot reference itself in its own initializer (temporal dead zone)');
});

test('R-F2875: news.html still has a fallback if app.js failed to load', () => {
  const code = codeOnly(read('news.html'));
  assert.match(code, /typeof API !== 'undefined'/,
    'the real API must be preferred when present');
  assert.match(code, /localStorage\.getItem\('crucix_token'\)/,
    'the defensive fallback must survive — this ticket fixes detection, not policy');
});

test('R-F2875: account.html no longer resolves Auth through window', () => {
  const code = codeOnly(read('account.html'));
  assert.ok(!/window\.Auth/.test(code),
    'window.Auth is permanently undefined — cacheUser never updated Auth.user and '
    + 'the profile load always used the stale localStorage snapshot');
  assert.match(code, /typeof Auth !== 'undefined'/,
    'Auth must be resolved lexically');
});

test('R-F2875: account.html still falls back to the cached user', () => {
  const code = codeOnly(read('account.html'));
  assert.match(code, /Auth\.me\(\)/, 'the fresh fetch must be preferred');
  assert.match(code, /crucix_user/, 'the cached fallback must survive');
});

// ── the ratchet: stop the fifth occurrence ───────────────────────────────────

test('R-F2875: RATCHET — no window.<const-global> guards anywhere in public/', () => {
  // These four are declared with const in app.js / sidebar.js, so none of them is
  // ever a window property. Any new `window.X` reference to them is the same bug.
  const CONST_GLOBALS = ['API', 'Auth', 'Nav', 'Sidebar'];
  const offenders = [];

  const walk = (dir, rel = '') => {
    for (const e of readdirSync(join(PUBLIC_DIR, dir), { withFileTypes: true })) {
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) { walk(join(dir, e.name), r); continue; }
      if (!/\.(html|js)$/.test(e.name)) continue;
      const code = codeOnly(readFileSync(join(PUBLIC_DIR, dir, e.name), 'utf8'));
      for (const g of CONST_GLOBALS) {
        if (new RegExp(`window\\.${g}\\b`).test(code)) offenders.push(`${r} → window.${g}`);
      }
    }
  };
  walk('.');

  assert.deepEqual(offenders, [],
    'window.<const global> is ALWAYS undefined — use `typeof X !== "undefined"`.\n'
    + 'Offenders:\n  ' + offenders.join('\n  '));
});
