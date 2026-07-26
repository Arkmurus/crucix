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
    },
    nodes,
  };
}

async function runLoader({ fetchImpl }) {
  const dom = makeDom();
  const src = extractLoaderScript();
  const fn = new Function('document', 'fetch', 'console', 'window', 'authed', 'API', 'alert',
    `return (async () => { ${src} })();`);
  await fn(
    dom.document, fetchImpl,
    { error() {}, warn() {}, log() {} }, {},
    (p, o) => fetchImpl(p, o),
    { BASE: '', headers: () => ({}) },
    () => {},
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
