// R-F2715 (#4, #7) — per-candidate freshness + grade-before-paginate.
//
// #4: a feed marked stale ONLY because >15% of UNRELATED news feeds failed
//     ('source_failure_degraded') must NOT suppress a fresh Grade-A candidate.
//     Other staleness reasons (signals_stale, no_signals, poll_stale) still block.
// #7: the morning cron grades over a LARGER bounded window (60), not the newest 20,
//     so low-value 'context' signals can't crowd out a high-value Grade-A signal.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aria-freshness-'));
process.env.CHANNEL_POST_DEDUP_PATH = path.join(tempDir, 'posted.json');
fs.writeFileSync(process.env.CHANNEL_POST_DEDUP_PATH, '{}');

const { selectTelegramGoldenIntel } = await import('../lib/telegram/channelServerHooks.mjs');

function gradeASignal(overrides = {}) {
  return {
    id: 'sig-A', signal_type: 'active_tender', priority: 'HIGH', confidence: 'HIGH',
    intel_grade: 'A', source_tier: 'tier_1a', score: 90,
    decision_summary: 'UK MoD opens frigate tender', why_it_matters: 'Near-term commercial window.',
    recommended_action: 'Qualify opportunity', target: 'UK',
    source: 'gov.uk', url: 'https://gov.uk/tender/1', detected_at: '2026-07-18T09:00:00Z',
    ...overrides,
  };
}

describe('R-F2715 per-candidate freshness (#4)', () => {
  it('source_failure_degraded alone does NOT suppress a fresh Grade-A candidate', () => {
    const feed = {
      ok: true,
      freshness: { stale: true, stale_reasons: ['source_failure_degraded'], backfilled: false },
      signals: [gradeASignal()],
    };
    assert.equal(selectTelegramGoldenIntel(feed)?.id, 'sig-A',
      'a live official tender must publish even when unrelated feeds failed');
  });

  it('a real staleness reason (signals_stale) still blocks the whole feed', () => {
    const feed = {
      ok: true,
      freshness: { stale: true, stale_reasons: ['signals_stale'], backfilled: false },
      signals: [gradeASignal()],
    };
    assert.equal(selectTelegramGoldenIntel(feed), null);
  });

  it('degraded + a real reason still blocks (any blocking reason wins)', () => {
    const feed = {
      ok: true,
      freshness: { stale: true, stale_reasons: ['source_failure_degraded', 'no_signals'], backfilled: false },
      signals: [gradeASignal()],
    };
    assert.equal(selectTelegramGoldenIntel(feed), null);
  });

  it('stale with no itemised reason blocks conservatively', () => {
    const feed = { ok: true, freshness: { stale: true }, signals: [gradeASignal()] };
    assert.equal(selectTelegramGoldenIntel(feed), null);
  });
});

describe('R-F2715 grade-before-paginate (#7) — superseded by R-F2893', () => {
  // R-F2893: widening the window was a treadmill. R-F2715 raised it 20 -> 60 and
  // within days the volume outgrew that too: live 2026-07-23 the only three Grade A
  // signals sat at feed positions 66-68, so the cron fetched 60 and honestly
  // reported "no Grade A" while three official TED tenders sat in the store.
  // The window is now grade-SCOPED AT THE SOURCE, which removes the race instead of
  // outrunning it. This asserts the stronger contract: a bounded window AND an
  // explicit grade request.
  it('the morning cron requests a bounded window scoped to publishable grades', () => {
    const src = fs.readFileSync(
      path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')),
        '..', 'lib', 'telegram', 'channelServerHooks.mjs'), 'utf8');
    assert.match(src, /fetchGoldenIntelSignals\(\{\s*limit:\s*60\s*,\s*grades:/,
      'cron must bound the window AND scope it by grade at the source');
    assert.match(src, /grades:\s*allowGradeB\s*\?\s*'A,B'\s*:\s*'A'/,
      'morning publishes Grade A only; only the evening slot may fall back to Grade B');
  });
});
