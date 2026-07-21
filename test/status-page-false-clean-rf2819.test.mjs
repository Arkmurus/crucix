// test/status-page-false-clean-rf2819.test.mjs
//
// R-F2819 — CAPABILITY test: the public status page must never assert a clean
// system state it has not measured.
//
// THE DEFECT (verified in the shipped file before this fix):
//   public/status.html shipped `<div id="banner" class="st-banner operational">`
//   with a `bi-check-circle-fill` icon, and shipped the literal strings
//   "No active incidents." / "No resolved incidents in the last 30 days." as
//   static markup. `banner.className` and `renderIncidents()` were reassigned ONLY
//   inside the `try`. The `catch` set banner-status text and nothing else.
//   So when /api/status was unreachable the user saw:
//       [GREEN TICK]  Status check failed
//       Active incidents:  No active incidents.
//   i.e. the page ASSERTED zero incidents while unable to read the incident feed.
//   A false clean on the status page of a never-false-clean product.
//
//   Third, separate false clean in the same file: `statusMap[data.overall] ||
//   statusMap.operational` rendered "All systems operational" for ANY status
//   string the page did not recognise, including a malformed payload.
//
// This runs the REAL inline script from public/status.html against a minimal DOM
// stub (repo convention — see test/aria-undo-toast-rf1690.test.mjs; no jsdom
// dependency) with fetch forced to fail, and asserts what the USER ends up seeing.
//
// Run: node --test test/status-page-false-clean-rf2819.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const HTML = readFileSync(path.join(ROOT, 'public', 'status.html'), 'utf8');

/** The page's own loader IIFE — the last <script> block, run verbatim. */
function extractLoaderScript() {
  const blocks = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const loader = blocks.find((b) => b.includes("fetch('/api/status')"));
  assert.ok(loader, 'status.html must still load its state from /api/status');
  return loader;
}

/** Minimal DOM stub: only what the real script touches. */
function makeDom() {
  const ids = [
    'banner', 'banner-status', 'banner-summary', 'banner-scope',
    'uptime-pct', 'bridge-row', 'bridge-text',
    'open-incidents', 'recent-incidents', 'generated-at',
  ];
  const nodes = {};
  const mkNode = (id) => {
    const node = {
      id,
      className: '',
      style: {},
      _text: '',
      _html: '',
      get textContent() { return this._text; },
      set textContent(v) { this._text = String(v); this._html = String(v); },
      get innerHTML() { return this._html; },
      set innerHTML(v) {
        this._html = String(v);
        // Good enough for assertions: strip tags for the text view.
        this._text = String(v).replace(/<[^>]*>/g, '');
        this.firstChild = { set textContent(t) { node._text = String(t); node._html = String(t); },
          get textContent() { return node._text; } };
      },
      firstChild: null,
      querySelector() { return mkNode(id + ':icon-stub'); },
    };
    return node;
  };
  for (const id of ids) nodes[id] = mkNode(id);
  // Seed the banner + incident lists with EXACTLY what the shipped HTML contains,
  // so the test observes the real starting state, not an invented one.
  const bannerCls = /<div id="banner" class="([^"]+)"/.exec(HTML);
  assert.ok(bannerCls, 'status.html must still declare #banner with a class');
  nodes.banner.className = bannerCls[1];
  const openStatic = /<div id="open-incidents">([\s\S]*?)<\/div>\s*<\/div>|<div id="open-incidents">([\s\S]*?)<\/div>/.exec(HTML);
  nodes['open-incidents'].innerHTML = openStatic ? (openStatic[1] || openStatic[2] || '') : '';
  const recentStatic = /<div id="recent-incidents">([\s\S]*?)<\/div>/.exec(HTML);
  nodes['recent-incidents'].innerHTML = recentStatic ? recentStatic[1] : '';

  return {
    nodes,
    document: {
      getElementById: (id) => nodes[id] || mkNode(id),
      querySelectorAll: () => [],
    },
  };
}

async function runLoader({ fetchImpl }) {
  const dom = makeDom();
  const src = extractLoaderScript();
  const fn = new Function('document', 'fetch', 'console', 'window',
    `return (async () => { ${src} })();`);
  await fn(dom.document, fetchImpl, { error() {}, warn() {}, log() {} }, {});
  // The script's own IIFE is async; give its microtasks a turn to settle.
  await new Promise((r) => setTimeout(r, 5));
  return dom.nodes;
}

const CLEAN_ASSERTIONS = [
  'No active incidents',
  'No resolved incidents',
  'All systems operational',
];

test('R-F2819 — the page does NOT ship a clean assertion in its static markup', () => {
  // The static HTML is what a user sees before (and if) JS ever resolves.
  const openBlock = /<div id="open-incidents">([\s\S]*?)<\/div>/.exec(HTML)[1];
  const recentBlock = /<div id="recent-incidents">([\s\S]*?)<\/div>/.exec(HTML)[1];
  for (const claim of ['No active incidents', 'No resolved incidents']) {
    assert.ok(!openBlock.includes(claim) && !recentBlock.includes(claim),
      `status.html ships the assertion "${claim}" as static markup — it must only ` +
      'appear after the incident feed has actually been read');
  }
  const bannerCls = /<div id="banner" class="([^"]+)"/.exec(HTML)[1];
  assert.ok(!bannerCls.includes('operational'),
    'the banner must not ship in the "operational" (green) state');
  assert.ok(bannerCls.includes('unknown'), 'the banner must ship as "unknown"');
  assert.ok(!/<div id="banner"[\s\S]{0,220}?bi-check-circle-fill/.test(HTML),
    'the banner must not ship with a green check-circle icon');
});

test('R-F2819 CAPABILITY — /api/status unreachable ⇒ no clean claim survives', async () => {
  const nodes = await runLoader({
    fetchImpl: async () => { throw new Error('network down'); },
  });

  // 1. The banner must not still be green.
  assert.ok(!nodes.banner.className.includes('operational'),
    `banner stayed "${nodes.banner.className}" after a failed fetch — green + ` +
    '"Status check failed" is the exact false clean this fix removes');
  assert.ok(nodes.banner.className.includes('unknown'),
    'a failed status read must render the UNKNOWN state');

  // 2. No surface on the page may still assert cleanliness.
  const visible = Object.values(nodes).map((n) => n.textContent).join(' | ');
  for (const claim of CLEAN_ASSERTIONS) {
    assert.ok(!visible.includes(claim),
      `after a failed status read the page still displays "${claim}" — ` +
      `full visible text was: ${visible}`);
  }

  // 3. It must say WHY, and say the sections are unread rather than clear.
  assert.match(nodes['banner-status'].textContent, /unknown/i,
    'the status line must state that status is unknown');
  assert.match(nodes['open-incidents'].textContent, /NOT a statement that there are none/i,
    'the incident list must explicitly disclaim that empty means none');
  assert.match(nodes['recent-incidents'].textContent, /NOT a statement that there are none/i);

  // 4. Stale/optimistic numerics must be retracted too.
  assert.ok(!/^\s*\d/.test(nodes['uptime-pct'].textContent),
    'uptime must not display a number the page could not measure');
});

test('R-F2819 CAPABILITY — an UNRECOGNISED status does not render as operational', async () => {
  const nodes = await runLoader({
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        overall: 'some_future_state_this_page_has_never_heard_of',
        summary: 'x', open: [], recent: [], uptime30dPct: 99.9, generatedAt: null,
      }),
    }),
  });
  assert.ok(!nodes.banner.className.includes('operational'),
    'an unknown status string must NOT fall back to the green operational banner');
  assert.ok(!nodes['banner-status'].textContent.includes('All systems operational'),
    'an unknown status string must not be labelled "All systems operational"');
});

test('R-F2819 — a genuine operational reading still renders green (fix is not over-broad)', async () => {
  const nodes = await runLoader({
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        overall: 'operational', summary: 'all good', measuresNote: 'availability only',
        open: [], recent: [], uptime30dPct: 99.95, generatedAt: null,
      }),
    }),
  });
  assert.ok(nodes.banner.className.includes('operational'),
    'a MEASURED operational state must still render as operational');
  assert.equal(nodes['banner-status'].textContent, 'All systems operational');
});
