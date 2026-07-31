// R-F2908 — the /brief digest must use the SAME Golden Intel gate as the channel.
//
// Found by the 2026-07-23 intel review: there were THREE gates in front of Golden
// Intel — the channel (intel_grade + provenance + publishable), the dashboard
// (intel_grade + publishable), and server.mjs's /brief, which gated on
// `customer_value.score >= 80` and `freshness.stale !== false`.
//
// The /brief gate predated R-F2896 and R-F2899, so it:
//   * admitted classifier-template signals as though ARIA had analysed them,
//   * would blank the section whenever `source_failure_degraded` was the only stale
//     reason (the divergence that emptied the customer dashboard for days),
//   * admitted Grade B with no corroboration-pending labelling — live at review time
//     EVERY signal clearing >=80 was Grade B scoring 96.
//
// selectPublishableGoldenIntel is now the one gate; selectTelegramGoldenIntel is its
// head. These tests pin that they cannot drift apart again.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const hooks = await import('../lib/telegram/channelServerHooks.mjs');
const SERVER = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

const sig = (over = {}) => ({
  id: 'x', intel_grade: 'A', signal_type: 'sanctions_change', confidence: 'HIGH', score: 90,
  title: 'Germany - Fighter aircraft procurement notice',
  why_it_matters: 'Bundesamt (Germany) - deadline 2026-08-11. Matched products: ammunition.',
  recommended_action: 'Assess bid/no-bid - review scope, eligibility and deadline.',
  url: 'https://ted.europa.eu/en/notice/1',
  detected_at: '2026-07-23T06:02:55Z',
  why_action_provenance: 'source_adapter',
  ...over,
});

const fresh = { stale: false, stale_reasons: [], blocking_stale_reasons: [], publishable: true };

// ── the two lanes share one gate ───────────────────────────────────────────

test('R-F2908: selectTelegramGoldenIntel is the head of selectPublishableGoldenIntel', () => {
  const feed = { ok: true, freshness: fresh, signals: [sig({ id: 'a' }), sig({ id: 'b', score: 70 })] };
  const all = hooks.selectPublishableGoldenIntel(feed, { grade: 'A' });
  const one = hooks.selectTelegramGoldenIntel(feed, { grade: 'A' });
  assert.equal(all.length, 2);
  assert.equal(one.id, all[0].id, 'the channel and the brief must agree on the best candidate');
});

test('R-F2908: the shared gate rejects classifier templates (R-F2899 applies to /brief)', () => {
  const feed = {
    ok: true, freshness: fresh,
    signals: [sig({ id: 'tmpl', why_action_provenance: 'classifier_template' })],
  };
  assert.deepEqual(hooks.selectPublishableGoldenIntel(feed, { grade: 'A' }), [],
    'a canned classifier string reached the brief');
});

test('R-F2908: the shared gate uses the canonical publishable verdict (R-F2896)', () => {
  // The ONLY stale reason is ambient source-health noise — the old /brief rule
  // (`freshness.stale !== false`) would have returned nothing here.
  const feed = {
    ok: true,
    freshness: { stale: true, stale_reasons: ['source_failure_degraded'], blocking_stale_reasons: [], publishable: true },
    signals: [sig()],
  };
  assert.equal(hooks.selectPublishableGoldenIntel(feed, { grade: 'A' }).length, 1,
    'ambient feed noise blanked the brief again');
});

test('R-F2908: genuine staleness still yields nothing', () => {
  const feed = {
    ok: true,
    freshness: { stale: true, stale_reasons: ['signals_stale'], blocking_stale_reasons: ['signals_stale'], publishable: false },
    signals: [sig()],
  };
  assert.deepEqual(hooks.selectPublishableGoldenIntel(feed, { grade: 'A' }), []);
});

test('R-F2908: limit is honoured', () => {
  const feed = { ok: true, freshness: fresh, signals: [sig({ id: '1' }), sig({ id: '2' }), sig({ id: '3' })] };
  assert.equal(hooks.selectPublishableGoldenIntel(feed, { grade: 'A', limit: 2 }).length, 2);
});

// ── the brief labels Grade B honestly ──────────────────────────────────────

test('R-F2908: /brief prefers Grade A and falls back to Grade B', () => {
  assert.match(SERVER, /selectPublishableGoldenIntel\(feed, \{ grade: 'A', limit \}\)/);
  assert.match(SERVER, /selectPublishableGoldenIntel\(feed, \{ grade: 'B'/);
});

test('R-F2908: a Grade B item in the brief is labelled corroboration-pending', () => {
  assert.match(SERVER, /GRADE B — single source, corroboration pending/,
    'Grade B must never render in the brief as though it were confirmed');
  // The label is applied to the rendered line, not merely defined.
  assert.match(SERVER, /\$\{goldenBriefGradeLabel\(s\)\}\$\{title\}/);
});

test('R-F2908: Grade A carries no qualifier — the badge is the claim', () => {
  const start = SERVER.indexOf('function goldenBriefGradeLabel');
  assert.ok(start > -1);
  const body = SERVER.slice(start, start + 400);
  assert.match(body, /grade === 'B'/);
  assert.match(body, /return '';/);
});

test('R-F2908: the superseded customer_value gate is GONE, not just bypassed', () => {
  assert.doesNotMatch(SERVER, /goldenBriefCustomerScore\s*\(/,
    'the stale gate helper still exists and could be re-wired');
  assert.doesNotMatch(SERVER, /goldenBriefHardRejections\s*\(/);
  assert.doesNotMatch(SERVER, /freshness\.stale !== false/,
    'the pre-R-F2896 staleness rule is still present in the brief lane');
});
