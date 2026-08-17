/**
 * R-F4094 (C-138) — a legacy error counter must not render as a verdict.
 *
 * The `errors` counter was incremented from a boolean whose defining rule has
 * since changed (C-131: an `empty` search result is an answer, not a failure),
 * and the outcome that produced it was never stored. Those increments cannot be
 * re-derived. Live proof: brave rendered a red "42%" for a full day after C-131
 * shipped and was verified, against a ledger showing zero real failures.
 *
 * So the panel shows the number, marks it provisional, and lets the derived
 * rate take over as evidence accrues. It does NOT invent a clean zero — that
 * would be the absence-as-health failure — and it does not keep shouting a
 * figure nobody can stand behind.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const PAGE = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'aria-brain.html');
const html = readFileSync(PAGE, 'utf8');

function block() {
  const i = html.indexOf("const legacy = row.error_source === 'legacy_counter'");
  assert.ok(i > 0, 'the legacy-provenance branch is gone');
  return html.slice(i, i + 1400);
}

test('the row reads provenance from the API, not from a service name', () => {
  const b = block();
  assert.match(b, /row\.error_source === 'legacy_counter'/);
  // Hardcoding "brave" would fix today's symptom and rot the moment another
  // service carries a legacy counter.
  assert.doesNotMatch(b, /=== 'brave'/, 'provenance must not be keyed on a service name');
});

test('a legacy rate is never rendered in the alarm colour', () => {
  const b = block();
  const m = b.match(/const rateCls = ([^;]+);/);
  assert.ok(m, 'rateCls is gone');
  assert.match(m[1], /legacy/,
    'a rate that cannot be re-derived must not be presented as a red verdict');
});

test('a DERIVED rate can still go red', () => {
  // The whole point is that this reports a genuine failure. Downgrading every
  // rate to neutral would be a guard that cannot fire.
  const m = block().match(/const rateCls = ([^;]+);/)[1];
  assert.match(m, /'bad'/, 'a derived rate must still be able to read bad');
  assert.match(m, /0\.20/, 'the 20% threshold must survive');
});

test('the legacy figure is still shown, not hidden', () => {
  // Suppressing it would be the opposite error: we would be inventing silence
  // about spend that really was recorded.
  const b = block();
  assert.match(b, /rate \* 100/, 'the percentage must still render');
  assert.match(b, /row\.errors\|\|0/, 'the error count must still render');
});

test('both states explain themselves on hover', () => {
  const b = block();
  assert.match(b, /const why = legacy/);
  assert.match(b, /title="\$\{escapeHtml\(why\)\}"/, 'the explanation must reach the DOM');
  assert.match(b, /error_sample/, 'a derived rate must state the n it was derived from');
});
