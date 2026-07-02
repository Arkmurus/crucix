// R-F2302 — SCREEN reply-keyword must return REAL, VERIFIABLE, NEVER-FALSE-CLEAN
// sanctions results. handleScreen calls /api/aria/sanctions/fuzzy and renders via
// formatScreenResult. The load-bearing rule: an UNPERFORMED/errored screen must
// NEVER read as "clear".
import { test } from 'node:test';
import assert from 'node:assert';
import { handleScreen } from '../lib/telegram/replyKeywordRouter.mjs';

const _origFetch = global.fetch;
function mockJson(body, ok = true) {
  global.fetch = async () => ({ ok, status: ok ? 200 : 503, json: async () => body });
}

test.afterEach(() => { global.fetch = _origFetch; });

test('never-false-clean: an unperformed/errored screen is NOT a clearance', async () => {
  mockJson({ name: 'Acme', screened: false, error: 'sanctions_source_unavailable', matches: [], match_count: 0 });
  const r = await handleScreen('Acme');
  assert.match(r.text, /could not be completed|NOT.*clearance/i);
  assert.doesNotMatch(r.text, /No matches/i);  // must never claim clear
});

test('clear: a performed screen with no matches → honest no-matches', async () => {
  mockJson({ name: 'Acme', screened: true, blocking_matches: [], matches: [], match_count: 0, variants_tried: ['Acme', 'Acme Ltd'] });
  const r = await handleScreen('Acme');
  assert.match(r.text, /No matches/i);
  assert.match(r.text, /variants tested/i);
});

test('blocking hit → do not proceed + names the list', async () => {
  mockJson({ name: 'Acme', screened: true, blocking_matches: [{ name: 'Acme', list: 'OFAC SDN', score: 0.95 }], matches: [{ name: 'Acme' }], match_count: 1 });
  const r = await handleScreen('Acme');
  assert.match(r.text, /BLOCKING|Do not proceed/i);
  assert.match(r.text, /OFAC SDN/);
});

test('possible (below-threshold) match → enhanced DD required', async () => {
  mockJson({ name: 'Acme', screened: true, blocking_matches: [], matches: [{ name: 'Acme Similar', score: 0.6 }], match_count: 1 });
  const r = await handleScreen('Acme');
  assert.match(r.text, /possible match|enhanced DD/i);
});

test('API failure → honest fallback, never a fake clearance', async () => {
  mockJson({}, false);
  const r = await handleScreen('Acme');
  assert.match(r.text, /could not be completed|NOT.*clearance/i);
  assert.doesNotMatch(r.text, /No matches/i);
});

test('every success path carries the PRO conversion CTA', async () => {
  mockJson({ name: 'Acme', screened: true, matches: [], match_count: 0 });
  const r = await handleScreen('Acme');
  assert.match(r.text, /PRO/);
});
