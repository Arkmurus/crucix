// test/amendments-delegation-rf527.test.mjs
//
// Capability test for R-F527 — Pending Amendments event delegation.
//
// Operator reported 2026-05-14: "Approve / Reject buttons not working,
// no engagement, nothing happens" — even after R-F471 shipped a
// defensive wrap + DOMContentLoaded handler-presence self-check. The
// remaining failure scenario was inline `onclick="approveAmendment(...)"`
// attributes being silently dropped (some browser-extension rule sets
// strip inline event handlers as an anti-XSS measure, leaving the
// button looking dead).
//
// R-F527 switches the queue to data-action delegation:
//   - Each row renders `<button data-action="approve" data-attack-id="X">`
//   - A single delegated listener on `#amendments-queue` dispatches by
//     data-action, calling the existing approveAmendment / rejectAmendment
//     / bulkRejectStale handlers unchanged.
//   - Entry trace via console.info('[R-F527] amendments click', …) so the
//     operator can confirm in DevTools that the click actually reached
//     the handler even when the modal path fails silently.
//
// Static-source guardrail (no browser). Verifies:
//   1. The button-rendering uses data-action / data-attack-id (not onclick).
//   2. The bulk-reject button uses data-action="bulk-reject-stale".
//   3. The delegated handler _amendmentsDelegatedClick exists, dispatches
//      by data-action, and falls back to a toast on unknown action.
//   4. loadAmendments wires the delegated listener idempotently via the
//      _rf527Bound marker (so refreshAll doesn't stack handlers).
//   5. R-F471 / R-F433 / R-F441 regression markers still present.
//
// Run: node test/amendments-delegation-rf527.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(
  join(__dirname, '..', 'public', 'aria-brain.html'),
  'utf8'
);

let failures = 0;
function check(label, cond, extra) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${extra ? ' — ' + extra : ''}`); failures += 1; }
}

console.log('R-F527 capability tests\n');

// ── 1. Button rendering uses data-action / data-attack-id ───────────────
console.log('1. Per-amendment buttons render with data-action attributes');
{
  // The Approve button
  check('Approve button has data-action="approve"',
        /data-action="approve"\s+data-attack-id=/.test(HTML));
  check('Reject button has data-action="reject"',
        /data-action="reject"\s+data-attack-id=/.test(HTML));
  // Regression: inline onclick on per-row buttons must be GONE so a
  // browser that strips inline handlers can no longer kill the queue.
  check('No inline onclick="approveAmendment(...)" left',
        !/onclick="approveAmendment\(/.test(HTML));
  check('No inline onclick="rejectAmendment(...)" left',
        !/onclick="rejectAmendment\(/.test(HTML));
}

// ── 2. Bulk-reject button uses data-action delegation ───────────────────
console.log('\n2. Bulk-reject button uses data-action');
{
  check('Bulk button has data-action="bulk-reject-stale"',
        HTML.includes('data-action="bulk-reject-stale"'));
  check('No inline onclick="bulkRejectStale()" left',
        !/onclick="bulkRejectStale\(/.test(HTML));
}

// ── 3. Delegated handler dispatches correctly ───────────────────────────
console.log('\n3. _amendmentsDelegatedClick dispatcher exists and routes');
{
  const fnStart = HTML.indexOf('function _amendmentsDelegatedClick');
  check('_amendmentsDelegatedClick defined', fnStart > -1);
  const fnEnd = HTML.indexOf('\n}', fnStart);
  const body = HTML.slice(fnStart, fnEnd);
  check('reads closest button[data-action]',
        /closest\(['"]button\[data-action\]['"]\)/.test(body));
  check('dispatches approve  → approveAmendment',
        /['"]approve['"].*approveAmendment/.test(body)
        || /approve.*approveAmendment/.test(body));
  check('dispatches reject   → rejectAmendment',
        /['"]reject['"].*rejectAmendment/.test(body)
        || /reject.*rejectAmendment/.test(body));
  check('dispatches bulk-reject-stale → bulkRejectStale',
        /bulk-reject-stale/.test(body) && /bulkRejectStale/.test(body));
  check('entry trace via console.info',
        /console\.info\(['"]\[R-F527\]/.test(body));
  check('falls back to error toast on unknown action',
        /Unknown amendments action/.test(body));
}

// ── 4. loadAmendments wires the listener idempotently ───────────────────
console.log('\n4. loadAmendments binds the delegated listener once');
{
  const fnStart = HTML.indexOf('async function loadAmendments');
  check('loadAmendments defined', fnStart > -1);
  // End at the next top-level function definition so the slice covers
  // the whole body, including the post-innerHTML listener binding.
  const nextFn = HTML.indexOf('\nfunction ', fnStart + 30);
  const fnEnd = nextFn > -1 ? nextFn : HTML.length;
  const body = HTML.slice(fnStart, fnEnd);
  check('addEventListener for click attached',
        /addEventListener\(['"]click['"],\s*_amendmentsDelegatedClick\)/.test(body));
  check('idempotency guard via _rf527Bound flag',
        /_rf527Bound/.test(body));
}

// ── 5. Regression guards — R-F471 / R-F433 / R-F441 still present ───────
console.log('\n5. Prior fixes still intact (regression guard)');
{
  check('R-F471 self-check still listed', HTML.includes("typeof approveAmendment !== 'function'"));
  check('R-F433 #op-modal element still present', HTML.includes('id="op-modal"'));
  check('R-F441 #js-error-banner still present', HTML.includes('id="js-error-banner"'));
  check('showOperatorModal still defined', HTML.includes('function showOperatorModal'));
}

console.log(`\nR-F527 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
