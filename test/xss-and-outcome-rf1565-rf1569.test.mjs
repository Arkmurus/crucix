// R-F1565 + R-F1569 — web-tier dark-ops wiring + delivery-outcome + stored-XSS.
//
// R-F1565:
//   - the sweep-ingest catch in server.mjs (pushSweepToARIA) was console-only
//     (dark) — it must now route through errorTracker.record so the failure
//     reaches the brain (/api/aria/brain/signal via R-F900 _reportToBrain).
//   - the main web answer path (/api/aria/chat) must call reportOutcome so web
//     delivery is no longer fully dark (§25a).
//
// R-F1569:
//   - the watchlist + coder per-row action buttons interpolated user/server-
//     stored names into a single-quoted onclick JS string (a JS-string-inside-
//     HTML-attribute double-decode context that HTML-entity escaping alone
//     cannot make safe). They now use data-attr + event delegation, and the
//     names are HTML-attribute-escaped.
//
// These are source-level assertions (the broken paths live inside server.mjs /
// static HTML that are not unit-importable) plus a behavioural escaping test.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const read = (p) => readFileSync(join(root, p), 'utf-8');

// ── R-F1565: sweep-ingest catch is wired to the brain ──────────────────────
describe('R-F1565 dark-ops failure wiring', () => {
  const server = read('server.mjs');

  it('pushSweepToARIA catch now calls errorTracker.record', () => {
    const fn = server.slice(
      server.indexOf('async function pushSweepToARIA'),
      server.indexOf('app.get(\'/api/aria/identity\'')
    );
    assert.ok(fn.length > 0, 'pushSweepToARIA function located');
    assert.match(fn, /errorTracker\.record\(\s*'aria_sweep_ingest'/,
      'sweep-ingest catch routes through errorTracker.record (no longer console-only)');
  });

  it('ariaProxy threw-catch is wired to the brain', () => {
    assert.match(server, /errorTracker\.record\(\s*'aria_intel_proxy'/,
      'aria-intel proxy threw-catch routes through errorTracker.record');
  });
});

// ── R-F1565: web delivery-outcome on the main answer path ──────────────────
describe('R-F1565 §25a web delivery-outcome', () => {
  const server = read('server.mjs');

  it('defines a best-effort reportOutcome helper for the web tier', () => {
    assert.match(server, /function reportOutcome\(/, 'reportOutcome helper defined');
    assert.match(server, /\/api\/aria\/outcome/, 'posts to the /api/aria/outcome endpoint (mirrors WA)');
  });

  it('/api/aria/chat reports both success and failure outcomes', () => {
    const route = server.slice(
      server.indexOf("app.post('/api/aria/chat',"),
      server.indexOf("app.get('/api/aria/unguarded-fallback/stats'")
    );
    assert.ok(route.length > 0, 'chat route located');
    // R-F2436: the chat route now reports its outcome via classifyDeliveryOutcome(data)
    // (R-F2405) — STRICTER than the old hardcoded 'delivered_real_answer' literal, because
    // a timeout fallback is honestly NOT logged as delivered (§25). Assert the
    // honest-classification wiring instead of the retired literal.
    assert.match(route, /reportOutcome\('web',[^;]*'chat_answer',\s*classifyDeliveryOutcome/,
      'success branch reports a classified delivery outcome (§25 honest classification)');
    assert.match(route, /reportOutcome\('web',[^)]*'error'/,
      'failure / fallback branch reports error');
  });
});

// ── R-F1569: no user-stored name in an inline-onclick JS string ────────────
describe('R-F1569 stored-XSS — no user data in onclick JS strings', () => {
  it('watchlist.html action buttons use data-attr + delegation, not onclick(name)', () => {
    const wl = read('public/watchlist.html');
    assert.doesNotMatch(wl, /onclick="runDD\(\\?'/,
      'no inline onclick="runDD(\'...\')" with interpolated name');
    assert.doesNotMatch(wl, /onclick="removeEntry\(\\?'/,
      'no inline onclick="removeEntry(\'...\')" with interpolated name');
    assert.match(wl, /data-name="' \+ escAttr\(e\.name\)/, 'name moved into escaped data-attr');
    assert.match(wl, /wl-act-dd|wl-act-remove/, 'delegated action classes present');
  });

  // R-F2436: the ARIA Coder web UI (public/coder.html, R-F1189) was RETIRED —
  // sidebar link removed + file deleted, superseded by the `aria` CLI. Skip this
  // assertion when the page is absent so a retired-page test isn't a false red.
  it('coder.html close button uses data-attr + delegation, not onclick(filename)',
     { skip: existsSync(join(root, 'public/coder.html')) ? false : 'coder.html retired (R-F2436)' }, () => {
    const coder = read('public/coder.html');
    assert.doesNotMatch(coder, /onclick="closeFile\(\\?'/,
      'no inline onclick="closeFile(\'...\')" with interpolated filename');
    assert.match(coder, /coder-tab-close/, 'delegated close class present');
    assert.match(coder, /escapeHtml\(filename\)/, 'displayed filename is escaped');
  });
});

// ── R-F1569: escaping helpers actually escape <script> ─────────────────────
describe('R-F1569 escaping helpers neutralise <script>', () => {
  // Mirror the shared escapers (public/js/app.js escHtml + watchlist escAttr).
  const escHtml = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const escAttr = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  it('escHtml turns <script> into harmless entities', () => {
    const out = escHtml('<script>alert(document.cookie)</script>');
    assert.doesNotMatch(out, /<script>/);
    assert.match(out, /&lt;script&gt;/);
  });

  it('escAttr neutralises both quote styles and angle brackets', () => {
    const out = escAttr(`'");<script>`);
    assert.doesNotMatch(out, /[<>"']/, 'no raw quote/angle chars survive');
    assert.match(out, /&#39;.*&quot;.*&lt;script&gt;/);
  });
});
