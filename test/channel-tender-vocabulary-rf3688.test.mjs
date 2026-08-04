// test/channel-tender-vocabulary-rf3688.test.mjs
//
// R-F3688 — the channel allow-list never learned the brain's tender vocabulary.
//
// MEASURED LIVE 2026-08-04. The 07:00 Golden Intel slot had held for FOUR
// consecutive days (08-01 .. 08-04), each time logging
//     [ChannelCron] Morning: no Grade A — holding for corroboration
// and recording `skipped / held_for_corroboration` in the §25 outcome ledger.
//
// There was no shortage of Grade A. Probed against the live brain:
//     /api/aria/intel/signals/recent?grades=A  ->  12 signals, freshness publishable
// and selectTelegramGoldenIntel rejected EVERY ONE of them.
//
// The brain emits BOTH `active_tender` (8) and `contract_award` (6). Only the
// second was in `_GOLDEN_ALLOWED_TYPES`, so the procurement lane was split across
// two names and the open half — the tenders — was dropped silently. That is the
// lane the channel exists for: R-F2310 ranks "verified procurement tenders" as
// customer-acquisition priority #2, and R-F2893's own comment is about three
// official TED tenders that this cron failed to see.
//
// Measured effect of the fix, against the real feed:
//     Grade A passing the FULL gate   OLD: 0   NEW: 2   (both active_tender)
//     Grade B passing the FULL gate   OLD: 6   NEW: 11
//
// NOT WIDENED: `cyber_threat` (8 live signals) stays out — an infosec advisory
// lane is a positioning decision, and the operator kept the channel on
// defence/procurement/geopolitics. `security_operation` and `political_transition`
// are admitted; they contribute 0 today (no source_adapter signals of those types
// yet) and are additive for when those adapters produce per-item analysis.
//
// NOT TOUCHED: `_hasItemSpecificAnalysis` (R-F2899, `why_action_provenance ===
// 'source_adapter'`). It is doing exactly its job — `classifier_template` means the
// why/action are fixed per-pattern template strings, which is what once published a
// UN News multi-topic roundup as "decision-grade". Relaxing it to unblock the slot
// would trade the channel's whole quality claim for volume.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const hooks = await import('../lib/telegram/channelServerHooks.mjs');
const SRC = fs.readFileSync(
  new URL('../lib/telegram/channelServerHooks.mjs', import.meta.url), 'utf8');

function allowedTypes() {
  const m = SRC.match(/_GOLDEN_ALLOWED_TYPES\s*=\s*new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(m, '_GOLDEN_ALLOWED_TYPES must be a literal Set so drift is reviewable');
  return new Set([...m[1].matchAll(/'([a-z_]+)'/g)].map(x => x[1]));
}

// A signal shaped exactly like the live feed: every other gate predicate passes,
// so `signal_type` is the only thing under test.
function signal(type, over = {}) {
  return {
    id: `sig-${type}`,
    signal_type: type,
    intel_grade: 'A',
    why_action_provenance: 'source_adapter',   // R-F2899 — real per-item analysis
    title: `Germany – Military vehicles and associated parts – 600306761`,
    decision_summary: 'Open tender for military vehicle parts',
    why_it_matters: 'Directly addressable procurement opportunity in a core market.',
    recommended_action: 'Review the tender notice and assess bid eligibility.',
    url: 'https://ted.europa.eu/notice/600306761',
    source: 'TED',
    confidence: 'HIGH',
    detected_at: new Date().toISOString(),
    ...over,
  };
}

const feed = (signals) => ({
  ok: true,
  signals,
  freshness: { stale: false, stale_reasons: [], blocking_stale_reasons: [], publishable: true },
});

describe('R-F3688 — the tender lane reaches the channel', () => {
  it('active_tender is publishable — it is the same procurement lane as contract_award', () => {
    const types = allowedTypes();
    assert.ok(types.has('contract_award'), 'precondition: awards were already allowed');
    assert.ok(
      types.has('active_tender'),
      'the brain emits active_tender for OPEN procurement; excluding it drops the ' +
      'single largest Grade A category and the one the channel exists to carry',
    );
  });

  it('capability: a live-shaped TED tender is now SELECTED, not silently dropped', () => {
    // FAILS BEFORE: returns null, and the cron reports "no Grade A" — blaming the
    // supply for a gate decision.
    const picked = hooks.selectTelegramGoldenIntel(feed([signal('active_tender')]));
    assert.ok(picked, 'an official TED tender with full analysis must be publishable');
    assert.equal(picked.signal_type, 'active_tender');
  });

  it('the operator-chosen additions are present', () => {
    const types = allowedTypes();
    for (const t of ['security_operation', 'political_transition']) {
      assert.ok(types.has(t), `${t} was admitted by operator decision 2026-08-04`);
    }
  });

  it('cyber_threat stays OUT — scope is a decision, not an oversight', () => {
    assert.ok(
      !allowedTypes().has('cyber_threat'),
      'admitting infosec advisories re-positions the channel; that was declined',
    );
    assert.equal(
      hooks.selectTelegramGoldenIntel(feed([signal('cyber_threat')])), null,
    );
  });

  it('R-F2899 is NOT weakened — template analysis still cannot publish', () => {
    const templated = signal('active_tender', { why_action_provenance: 'classifier_template' });
    assert.equal(
      hooks.selectTelegramGoldenIntel(feed([templated])), null,
      'per-pattern template text must never reach the channel as decision-grade',
    );
  });

  it('the other gate predicates still bite', () => {
    for (const [label, over] of [
      ['no evidence url', { url: '', evidence: {} }],
      ['no why_it_matters', { why_it_matters: '' }],
      ['no recommended_action', { recommended_action: '' }],
      ['wrong grade', { intel_grade: 'B' }],
    ]) {
      assert.equal(
        hooks.selectTelegramGoldenIntel(feed([signal('active_tender', over)])), null,
        `${label} must still be rejected`,
      );
    }
  });
});

describe('R-F3688 — the allow-list must not silently outgrow the brain again', () => {
  // The root defect is not the missing entry, it is that a Node-side copy of a
  // Python-side taxonomy drifted with nothing binding them. This pins the known
  // vocabulary so a new brain signal_type is a REVIEWED decision — admitted or
  // deliberately excluded — rather than an item that vanishes without a trace.
  const BRAIN_TYPES_SEEN_LIVE = [
    'sanctions_change', 'contract_award', 'active_tender', 'competitor_activity',
    'conflict_escalation', 'security_operation', 'political_transition',
    'cyber_threat', 'natural_hazard',
  ];
  const DELIBERATELY_EXCLUDED = new Set(['cyber_threat', 'natural_hazard']);

  it('every live brain signal_type is either allowed or explicitly excluded', () => {
    const types = allowedTypes();
    const unaccounted = BRAIN_TYPES_SEEN_LIVE
      .filter(t => !types.has(t) && !DELIBERATELY_EXCLUDED.has(t));
    assert.deepEqual(
      unaccounted, [],
      `these brain types are neither allowed nor explicitly excluded: ${unaccounted.join(', ')} ` +
      '— add them to the allow-list or to DELIBERATELY_EXCLUDED with a reason',
    );
  });
});
