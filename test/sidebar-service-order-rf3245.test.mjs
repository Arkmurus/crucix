// R-F3245 — capability guard for the customer-service-first menu sequence.
//
// The shared sidebar is injected on every application page. Its opening
// offering must lead from ARIA Chat and the Intelligence Brief into the three
// core services, without unrelated monitoring or administration tabs
// interrupting the group.
//
// R-F3246 — THE ORIGINAL GUARD COULD NOT FAIL ON THE THING ITS COMMENT FORBADE.
// It asserted only that five positions were ascending, so injecting an admin
// tab between Vetting and Watchlist left it green — measured, not theorised.
// Three gaps, all the same kind: the comment described a contract the
// assertions did not encode.
//   1. ascending != contiguous       -> an interrupting tab passed
//   2. the stated first item (ARIA Chat) was never checked at all
//   3. exactly-once was asserted for three of six, so a second occurrence
//      elsewhere could silently become the one `indexOf` measured
//
// This file now encodes the contract it states. It is a SOURCE-ORDER guard by
// design, and that is only sufficient because: `sidebar.js` holds the only
// `rail-nav` in the tree, all six items are ungated, and one template literal
// emits them in order — so source order is DOM order here. If a second nav
// writer or conditional rendering is introduced, this guard stops being
// sufficient and must be replaced by one that renders.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const sidebar = readFileSync(
  new URL('../public/js/sidebar.js', import.meta.url),
  'utf8',
);

// The contract, in the order the customer should meet it.
const CORE_SEQUENCE = [
  ['aria', 'ARIA Chat'],
  ['brief', 'Intelligence Brief'],
  ['dd-reports', 'DD Reports'],
  ['vetting', 'Vetting'],
  ['watchlist', 'Watchlist'],
  ['news', 'News Monitor'],
];

// Quote-tolerant: a formatter switching to double quotes must not read as
// "the item vanished from the sidebar" — a false alarm shaped exactly like a
// real regression.
function occurrences(page) {
  const re = new RegExp(`\\$\\{\\s*link\\(\\s*['"]${page}['"]`, 'g');
  return [...sidebar.matchAll(re)].map((m) => m.index);
}

function soleIndex(page) {
  const found = occurrences(page);
  assert.equal(found.length, 1,
    `${page} must appear exactly once in the shared sidebar (found ${found.length})`
    + ' — a second occurrence makes the ordering assertion measure the wrong one');
  return found[0];
}

test('R-F3245 every core service appears exactly once', () => {
  for (const [page] of CORE_SEQUENCE) soleIndex(page);
});

test('R-F3245 core services appear in the contracted order', () => {
  const positions = CORE_SEQUENCE.map(([page]) => soleIndex(page));
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b),
    'the core menu sequence is out of order');
});

test('R-F3246 ARIA Chat leads the menu', () => {
  // Stated as item 1 of the contract and previously unasserted: the old
  // sequence started at 'brief', so ARIA Chat could be moved anywhere.
  const first = soleIndex('aria');
  const others = CORE_SEQUENCE.slice(1).map(([page]) => soleIndex(page));
  assert.ok(others.every((p) => p > first), 'ARIA Chat must come first');
});

test('R-F3246 nothing interrupts the core-service group', () => {
  // THE ASSERTION THE COMMENT ALWAYS CLAIMED. Every `link(` emitted between the
  // first and last core item must itself be a core item.
  const positions = CORE_SEQUENCE.map(([page]) => soleIndex(page));
  const start = Math.min(...positions);
  const end = Math.max(...positions);
  const core = new Set(CORE_SEQUENCE.map(([page]) => page));

  const anyLink = /\$\{\s*link\(\s*['"]([^'"]+)['"]/g;
  const intruders = [...sidebar.matchAll(anyLink)]
    .filter((m) => m.index > start && m.index < end)
    .map((m) => m[1])
    .filter((page) => !core.has(page));

  assert.deepEqual(intruders, [],
    `these tabs interrupt the core-service group: ${intruders.join(', ')}`);
});
