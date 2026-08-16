// R-F4065 (C-117) — the rendering half: a reading with no age cannot be told
// apart from a fresh one.
//
// Live 2026-08-16 the brain page showed, all without qualification:
//   "Memory: Redis: up"        state store is SQLite; Upstash gone 2026-05-12
//   "Tasks Fired 29 · Ticks 50"  per-process; became 5/7 after the 17:11 restart
//   "Latest run 2026-08-13"      three days stale, styled as current
//   "Training examples staged 1882"  last exported 2026-08-03, thirteen days
//   Operating Mode history newest entry 2026-08-07, nine days, with no way to
//     tell "evaluated, nothing to change" from "the evaluator died"

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pageHtml } from './helpers/aria_brain_page.mjs';

const html = pageHtml();

test('R-F4065 the memory row names the state store, not Redis', () => {
  assert.match(html, /state_store_reachable/,
    'the row read "Redis: up" while the backend is SQLite on the fly volume');
  assert.doesNotMatch(html, /memBits\.push\(`Redis:/,
    'the hardcoded Redis label is back');
});

test('R-F4065 the engine counters name their window', () => {
  assert.match(html, /_sinceBoot\(e\.started_at\)/,
    'Tasks Fired / Ticks are per-process and reset on every restart; '
    + 'unlabelled they read as lifetime work');
  assert.match(html, /function _sinceBoot/, 'the helper is gone');
});

test('R-F4065 operating mode shows when it was last EVALUATED', () => {
  assert.match(html, /last_evaluated_at/,
    'the panel showed transitions only, so a nine-day-old history could not '
    + 'be told apart from a dead evaluator — and this is the only route out '
    + 'of DEGRADED, which suppresses all external delivery');
  assert.match(html, /not in the last 72h/,
    'an absent stamp must read as a statement, not as a blank');
});

test('R-F4065 stale timestamps carry an age', () => {
  assert.match(html, /_ageLabel\(d\.latest_run_at\)/,
    'Layer 5c printed a bare date that was three days old');
  assert.match(html, /last export \$\{escapeHtml\(exportAge\)\}/,
    'the training corpus count had an honest "no model consumes these" note '
    + 'but no staleness, and the last export was thirteen days old');
});

test('R-F4065 the age helper degrades rather than inventing a value', () => {
  const start = html.indexOf('function _ageHours');
  const end = html.indexOf('function _sinceBoot');
  const body = html.slice(start, end);
  assert.match(body, /return null/,
    'an unparseable or absent timestamp must yield no age, not 0 — "0h ago" '
    + 'would read as the freshest possible reading');
  assert.match(body, /isFinite/, 'Date.parse returns NaN on junk');
});
