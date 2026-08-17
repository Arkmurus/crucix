/**
 * R-F4092 (C-137) — `unscoped:*` spend must be flagged, and must survive the
 * top-10 slice.
 *
 * Found by reviewing my OWN work, not by a failure. R-F4090 renamed the
 * majority of month-to-date LLM spend from `uncategorized` to
 * `unscoped:<module>`. Both mean the same thing: a caller that declared no
 * `feature()` scope. But the panel flagged only the literal string:
 *
 *     const cls = name === 'uncategorized' ? 'warn' : '';
 *
 * so the rename moved 53% of spend off the one label the panel highlights.
 * The panel would have looked HEALTHIER with the underlying gap unchanged —
 * an absence rendered as health, self-inflicted by the fix meant to end it.
 *
 * The second, quieter way it could hide: the table slices to the top 10. One
 * big `uncategorized` row sat at #1; split across N modules, every piece can
 * fall below the cut and vanish from the panel while the total is unchanged.
 * So the summary sums over ALL features, never the sliced rows.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const PAGE = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'aria-brain.html');
const html = readFileSync(PAGE, 'utf8');

test('the warn class keys on the CONDITION, not the spelling', () => {
  const m = html.match(/const unattributed = ([^;]+);/);
  assert.ok(m, 'the unattributed predicate is gone — did someone revert to === uncategorized?');
  const expr = m[1];
  assert.match(expr, /uncategorized/);
  assert.match(expr, /startsWith\('unscoped:'\)/,
    'unscoped:<module> means the same thing as uncategorized and must flag too');
});

test('the unattributed total is summed over ALL features, not the sliced rows', () => {
  const m = html.match(/const unattrib = Object\.entries\((\w+)\)([\s\S]{0,260}?)reduce\(/);
  assert.ok(m, 'the unattributed total is missing');
  assert.equal(m[1], 'byFeat',
    'summing over the sliced rows would let the top-10 cut hide the figure');
  assert.doesNotMatch(m[2], /slice\(/, 'the total must not be computed from a sliced list');
});

test('the summary is emitted OUTSIDE the table element', () => {
  // A <div> is not a valid child of <table>; browsers hoist it out and the
  // layout breaks. The first draft made exactly this mistake.
  //
  // Asserted STRUCTURALLY, between the two landmarks, rather than by scanning
  // a fixed-size window backwards. The first version of this test used a
  // 400-char lookbehind and failed on correct code because the explanatory
  // comment above the summary is ~414 chars — the line/offset fragility
  // R-F3597 records, reproduced in the guard written to prevent a different
  // fragility.
  const open = html.indexOf('<table style="font-size:0.78em"><tr><th>Feature');
  const summary = html.indexOf('const unattrib = Object.entries');
  assert.ok(open > 0, 'the By Feature table is gone');
  assert.ok(summary > open, 'the summary must come after the table it summarises');
  const between = html.slice(open, summary);
  assert.match(between, /html \+= '<\/table>';/,
    'the table must be closed before the summary div is appended');
});

test('the predicate and the total agree on what counts as unattributed', () => {
  // Two places encode the same rule; if they drift, the flagged rows and the
  // headline number describe different sets and the panel contradicts itself.
  const rowRule = html.match(/const unattributed = ([^;]+);/)[1];
  const sumRule = html.match(/\.filter\(\(\[n\]\) => ([^)]+\)?)\)/);
  assert.ok(sumRule, 'the total has no filter');
  for (const token of ['uncategorized', 'unscoped:']) {
    assert.ok(rowRule.includes(token), `row rule lost ${token}`);
    assert.ok(sumRule[1].includes(token), `total rule lost ${token}`);
  }
});

test('the reduce cannot produce NaN on a malformed row', () => {
  // The first draft read `r[1] && 0` on the destructured VALUE, which is
  // undefined, and `undefined + n` is NaN — the headline would have rendered
  // "$NaN". Guard the accessor, and prove the guard by evaluating it.
  const m = html.match(/\.reduce\(\((.*?)\) => (.*?), 0\)/);
  assert.ok(m, 'the reduce is gone');
  const body = m[2];
  assert.doesNotMatch(body, /r\[\d\]/, 'indexing the value object yields undefined -> NaN');
  const fn = new Function('a', 'r', `return a + ${body.replace(/^a \+ /, '')};`);
  for (const row of [{}, null, undefined, { cost_usd: 2.5 }]) {
    const out = fn(0, row);
    assert.ok(Number.isFinite(out), `row ${JSON.stringify(row)} produced ${out}`);
  }
  assert.equal(fn(0, { cost_usd: 2.5 }), 2.5);
});
