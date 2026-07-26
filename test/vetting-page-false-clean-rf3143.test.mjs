// R-F3143 — the vetting page must never show a clean verdict it did not measure.
//
// ── Provenance ───────────────────────────────────────────────────────────
// This is R-F2819 carried forward. That guard was written for public/status.html
// after the page shipped `<div id="banner" class="st-banner operational">` in
// its STATIC markup, so an unreachable /api/status left the user looking at a
// green "All systems operational" banner next to a failure message. R-F3142
// retired that page; the property it protected is not retired with it.
//
// It matters MORE here. A status page claiming false health costs credibility.
// A screening page claiming a false clean on a named individual is the failure
// mode the whole module exists to prevent: the terminal good state is
// READY_FOR_CONTROLLER_REVIEW, and a human relies on it to decide whether
// someone gets a job.
//
// Runs the REAL inline loader from public/vetting.html against a minimal DOM
// stub (repo convention — no jsdom dependency) with fetch forced to fail, and
// asserts what the USER ends up seeing.
//
// Run: node --test test/vetting-page-false-clean-rf3143.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const HTML = readFileSync(path.join(ROOT, 'public', 'vetting.html'), 'utf8');

/** Every status the engine can return that is NOT a problem state. */
const CLEAN_STATES = ['ready_for_controller_review', 'evidence_complete'];

// ── static markup ─────────────────────────────────────────────────────────

test('R-F3143 — the verdict banner ships in the UNKNOWN state', () => {
  const cls = /<div id="verdict" class="([^"]+)"/.exec(HTML);
  assert.ok(cls, 'vetting.html must declare #verdict with a class');
  const banner = cls[1];
  assert.ok(banner.includes('unknown'),
    `the verdict banner ships as "${banner}" — it must ship "unknown"`);
  for (const state of CLEAN_STATES) {
    assert.ok(!banner.includes(state),
      `the verdict banner must not ship in the clean state "${state}"`);
  }
});

test('R-F3143 — no clean verdict wording is baked into static markup', () => {
  // The static HTML is what a user sees before (and if) JS ever resolves.
  const forbidden = [
    'Ready for controller review',
    'Evidence complete',
    'No findings',
    'No blockers',
  ];
  // Only inspect the markup ABOVE the loader script; the loader legitimately
  // contains these strings as data it may render AFTER a successful read.
  const markup = HTML.slice(0, HTML.indexOf('<script src="js/app.js'));
  for (const claim of forbidden) {
    assert.ok(!markup.includes(claim),
      `vetting.html ships the assertion "${claim}" as static markup — it must ` +
      'only appear after an assessment has actually been read back');
  }
});

test('R-F3143 — the shipped banner icon is not a green tick', () => {
  const head = HTML.slice(HTML.indexOf('<div id="verdict"'));
  const banner = head.slice(0, head.indexOf('</div>') + 6);
  assert.ok(!/bi-check-circle/.test(banner),
    'the verdict banner must not ship with a check-circle icon');
});

// ── the loader, run for real ──────────────────────────────────────────────

function extractLoaderScript() {
  const blocks = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const loader = blocks.find((b) => b.includes('/api/aria/vetting/'));
  assert.ok(loader, 'vetting.html must still load its state from /api/aria/vetting/*');
  return loader;
}

function makeDom() {
  const ids = [
    'verdict', 'verdict-icon', 'verdict-status', 'verdict-summary',
    'pack-info', 'pack-text', 'cases', 'findings', 'findings-count',
    'findings-wrap', 'controller-notes', 'new-case-btn', 'refresh-btn',
    'new-case-form', 'create-btn', 'f-pack', 'f-case-id', 'f-name',
    'f-dob', 'f-start',
  ];
  const nodes = {};
  const mkNode = (id) => ({
    id,
    className: '',
    style: {},
    value: '',
    disabled: false,
    _text: '',
    _html: '',
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this._html = String(v); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); this._text = String(v).replace(/<[^>]*>/g, ''); },
    addEventListener() {},
    reset() {},
    closest() { return null; },
  });
  for (const id of ids) nodes[id] = mkNode(id);
  // Seed the verdict banner with EXACTLY what the shipped HTML contains, so the
  // test starts from the real initial state rather than a convenient blank one.
  nodes.verdict.className = /<div id="verdict" class="([^"]+)"/.exec(HTML)[1];
  return {
    document: {
      getElementById: (id) => nodes[id] || mkNode(id),
      querySelectorAll: () => [],
      querySelector: () => null,
      createElement: () => ({ click() {}, style: {} }),
    },
    nodes,
  };
}

async function runLoader({ fetchImpl }) {
  const dom = makeDom();
  const src = extractLoaderScript();
  // R-F3168 — the loader now calls Sidebar.init, so the harness must supply it.
  // Stubbed rather than skipped: the point of this harness is that it runs the
  // REAL shipped script, and a stub that omitted Sidebar would just be testing
  // a different script.
  const fn = new Function(
    'document', 'fetch', 'console', 'window', 'authed', 'API', 'alert',
    'Sidebar', 'CSS', 'localStorage', 'Modal', 'Toast', 'escHtml',
    `return (async () => { ${src} })();`);
  await fn(
    dom.document, fetchImpl,
    { error() {}, warn() {}, log() {} }, {},
    (p, o) => fetchImpl(p, o),
    { BASE: '', headers: () => ({}) },
    () => {},
    { init() {} },
    { escape: (v) => String(v) },
    { getItem: () => null, setItem() {} },
    // R-F3170 — the loader now uses the SHARED Modal/Toast (app.js) instead of
    // window.prompt/alert, so the harness supplies them. Stubs, not skips: this
    // harness exists to run the real shipped script.
    { form: async () => null, confirm: async () => false, info() {} },
    { show() {} },
    (v) => String(v == null ? '' : v),
  );
  await new Promise((r) => setTimeout(r, 5));
  return dom.nodes;
}

test('R-F3143 CAPABILITY — an unreachable vetting API leaves no clean verdict', async () => {
  const nodes = await runLoader({
    fetchImpl: async () => { throw new Error('network down'); },
  });
  assert.ok(nodes.verdict.className.includes('unknown'),
    `verdict stayed "${nodes.verdict.className}" after a failed fetch — a screening ` +
    'page must fall back to UNKNOWN, never to a clean state');
  for (const state of CLEAN_STATES) {
    assert.ok(!nodes.verdict.className.includes(state),
      `verdict rendered the clean state "${state}" despite the API being unreachable`);
  }
});

test('R-F3143 CAPABILITY — an HTTP error on assess does not read as clear', async () => {
  const nodes = await runLoader({
    fetchImpl: async () => ({
      ok: false, status: 503,
      json: async () => ({ error: 'upstream down' }),
    }),
  });
  assert.ok(nodes.verdict.className.includes('unknown'),
    'a non-OK assess response must render UNKNOWN, not a clean verdict');
  assert.ok(!/ready for controller review/i.test(nodes['verdict-status'].textContent),
    'a failed assessment must not display readiness');
});

test('R-F3143 CAPABILITY — an unrecognised status is UNKNOWN, not clean', async () => {
  // A server that returns a status this page has never heard of must not be
  // optimistically rendered. Unrecognised is not the same as fine.
  const nodes = await runLoader({
    fetchImpl: async () => ({
      ok: true, status: 200,
      json: async () => ({ status: 'SOMETHING_NEW', counts: {}, findings: [] }),
    }),
  });
  assert.ok(nodes.verdict.className.includes('unknown'),
    'an unrecognised status must render UNKNOWN');
});

// ── R-F3168: the page shell and card view ────────────────────────────────

test('R-F3168 the page initialises the shared nav', () => {
  // The live symptom: vetting.html rendered with NO menu at all, because it
  // never called Sidebar.init. Every other app page does; nothing checked it.
  assert.match(HTML, /Sidebar\.init\(\s*['"]vetting['"]\s*\)/,
    'vetting.html must call Sidebar.init or the rail never renders');
  assert.match(HTML, /id="sidebar-placeholder"/,
    'the rail needs its placeholder element');
});

test('R-F3168 every badge class used actually exists in the shared stylesheet', () => {
  // A class that does not exist renders as unstyled text, which reads as a
  // missing feature rather than a typo.
  const css = readFileSync(path.join(ROOT, 'public', 'css', 'aria.css'), 'utf8');
  const used = [...HTML.matchAll(/sc-badge-([a-z]+)/g)].map((m) => m[0]);
  assert.ok(used.length, 'the card view should use the shared badge classes');
  for (const cls of new Set(used)) {
    assert.ok(css.includes(`.${cls}`), `${cls} is used but not defined in aria.css`);
  }
});

test('R-F3168 an unassessed case is grouped as unknown, never as clear', () => {
  // Same rule as the verdict banner: absence of an assessment is not a pass.
  assert.match(HTML, /Not yet assessed/,
    'there must be a section for cases with no assessment');
  assert.match(HTML, /status is unknown, not clear/,
    'the unassessed section must say what its absence means');
  assert.ok(!/last_status\s*\|\|\s*['"]READY/.test(HTML),
    'a missing cached status must never default to a ready state');
});

test('R-F3168 no case can silently vanish from the queue', () => {
  assert.match(HTML, /orphans/,
    'cases matching no section must still be rendered — a case that '
    + 'disappears from a screening queue is the worst possible bug here');
});

test('R-F3168 applicant photographs are deliberately not rendered', () => {
  // Extracting a face from a held passport would be biometric processing
  // (Art. 9) and would contradict our own AI Act assessment.
  assert.ok(!/avatarUrl|photo_url|applicant_photo|<img/i.test(HTML),
    'the card view must not render applicant photographs');
  assert.match(HTML, /vt-avatar/, 'initials avatars provide the affordance instead');
});

// ── R-F3170: no primitive browser dialogs ────────────────────────────────

test('R-F3170 the page uses no window.prompt / alert / confirm', () => {
  // R-F2293 established Modal/Toast precisely to replace these. Shipping a
  // window.prompt chain for a four-field decision was worse than ugly: the
  // officer answered from memory with no sight of the file, and a typo in
  // question two could not be fixed without abandoning the sequence.
  // Strip line comments so the prose ABOUT the old popups is not mistaken for
  // the popups themselves.
  const code = HTML.split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''))
    .join('\n');
  for (const bad of ['window.prompt', 'window.alert', 'window.confirm']) {
    assert.ok(!code.includes(bad), `${bad} must not be used`);
  }
  assert.ok(!/(^|[^.\w])prompt\s*\(/.test(code), 'bare prompt() must not be used');
  assert.ok(!/(^|[^.\w])alert\s*\(/.test(code), 'bare alert() must not be used');
});

test('R-F3170 it uses the SHARED modal + toast system', () => {
  assert.match(HTML, /Modal\.form\(/, 'forms must use the shared Modal.form');
  assert.match(HTML, /Toast\.show\(/, 'feedback must use the shared Toast');
});

test('R-F3170 the decision dialog shows the engine state as context', () => {
  // The officer must decide WITH the file in front of them, not from memory.
  assert.match(HTML, /Current assessment/,
    'the decision dialog must show the current assessment');
  assert.match(HTML, /type: 'static'/,
    'read-only context is rendered as a static field');
});

test('R-F3170 adverse-decision rules are enforced client-side too', () => {
  // The same rules the server enforces, surfaced BEFORE the round-trip, so a
  // user is never bounced by a rule they could not see.
  assert.match(HTML, /A rejection requires a stated reason/);
  assert.match(HTML, /A rejection requires a second reviewer/);
  assert.match(HTML, /cannot be the sole decision-maker/);
});

test('R-F3170 case creation validates dates against each other', () => {
  assert.match(HTML, /must precede the employment start date/);
  assert.match(HTML, /at least 16/);
});

// ── R-F3183: the applicant's full name is never clipped ──────────────────

test('R-F3183 the applicant name is not truncated', () => {
  // Reported live: "Antonio Magalhaes Cande Correa" rendered clipped. On a
  // screening file the name IS the identity being verified — against a
  // passport, a reference, a register entry — and it is the one field an
  // officer must not have to guess at.
  const rule = /\.vt-name\s*\{([^}]*)\}/.exec(HTML);
  assert.ok(rule, '.vt-name must be styled');
  const body = rule[1];
  assert.ok(!/text-overflow\s*:\s*ellipsis/.test(body),
    '.vt-name must not ellipsis-truncate the applicant name');
  assert.ok(!/white-space\s*:\s*nowrap/.test(body),
    '.vt-name must be allowed to wrap');
  assert.ok(/overflow-wrap|word-break/.test(body),
    'a very long single-token name must still wrap rather than overflow');
});

test('R-F3183 the card renders the full name, unabbreviated', () => {
  // The initials avatar is an ADDITION, not a replacement for the name.
  assert.match(HTML, /class="vt-name">\$\{esc\(c\.applicant_name/,
    'the card must render applicant_name in full');
});
