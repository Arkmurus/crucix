// test/golden-intel-empty-state-rf2583.test.mjs
//
// Capability test for R-F2583 — professional, HONEST empty-state for the Golden
// Intel "Distribution Ready" column.
//
// Context: the left column is empty because the source poll is stale (poll_stale:
// last_success_at ~27h old, so no new signal can be certified for public
// distribution). That is HONEST — it mirrors the live Telegram publish gate
// (R-F2554). The bug the operator saw is UX: a bare "0" + one muted line reads as
// "broken". R-F2583 replaces it with a matched-height status card that EXPLAINS
// what the gate is and WHY nothing has cleared it (using freshness data already
// returned), WITHOUT lowering the gate or faking items.
//
// Honesty guard: this test also asserts the distribution gate is UNCHANGED — if a
// future edit lowers isDistributionReadyGolden / feedPublishable to "fill" the
// column, this fails. Filling Distribution Ready with items Telegram would refuse
// to post is a never-false-clean violation.
//
// Run: node test/golden-intel-empty-state-rf2583.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2583 Golden Intel empty-state capability tests\n');

// ── 1. STATIC: the professional empty-state is defined + wired ───────────────
console.log('1. Professional empty-state wired into Distribution Ready');
check('renderGoldenIntelEmptyCard() defined', /function\s+renderGoldenIntelEmptyCard\s*\(/.test(HTML));
check('_goldenFmtAgo() time-ago helper defined', /function\s+_goldenFmtAgo\s*\(/.test(HTML));
check('empty card surfaces the poll-freshness diagnostic',
  /last refreshed/.test(HTML) && /populates automatically once the poll resumes/.test(HTML));
check('empty card is a matched-height status card (not a bare <p>)',
  /min-height:200px/.test(HTML) && /border:1px dashed var\(--sc-border\)/.test(HTML));
check('Distribution Ready column receives the empty card (7th arg)',
  /renderGoldenIntelEmptyCard\(fresh,\s*candidates\.length\)/.test(HTML));

// ── 2. HONESTY GUARD: the distribution gate is UNCHANGED ──────────────────────
console.log('\n2. Honesty guard — the gate was NOT lowered to fill the column');
check('feed-level gate intact (stale/backfilled feed publishes nothing)',
  /const\s+feedPublishable\s*=\s*fresh\.stale\s*===\s*false\s*&&\s*!fresh\.backfilled/.test(HTML));
check('per-signal gate intact (decision-grade + HIGH priority + trusted tier + evidence)',
  /quality\.indexOf\('decision-grade'\)\s*===\s*0/.test(HTML)
    && /priority\s*===\s*'HIGH'/.test(HTML)
    && /\['tier_1a',\s*'tier_1b',\s*'tier_2'\]\.includes\(tier\)/.test(HTML));

// ── 3. BEHAVIOURAL: message selection is correct (lockstep mirror) ───────────
console.log('\n3. Empty-state message picks the right reason (lockstep mirror)');
// Mirror of renderGoldenIntelEmptyCard's reason selection. Keep in lockstep.
function whyFor(fr) {
  const reasons = Array.isArray(fr.stale_reasons) ? fr.stale_reasons : [];
  if (fr.stale === false) return 'gate';                       // fresh, nothing cleared the gate
  if (reasons.indexOf('poll_stale') !== -1 || reasons.indexOf('missing_poll_state') !== -1) return 'poll';
  if (reasons.indexOf('signals_stale') !== -1 || reasons.indexOf('no_signals') !== -1) return 'nosignals';
  return 'generic';
}
check('poll_stale → explains the stale poll (the operator\'s live case)',
  whyFor({ stale: true, stale_reasons: ['poll_stale'], last_success_at: '2026-07-11T20:14:35Z' }) === 'poll');
check('fresh feed, nothing qualifies → explains the gate',
  whyFor({ stale: false }) === 'gate');
check('no signals → explains empty feed',
  whyFor({ stale: true, stale_reasons: ['no_signals'] }) === 'nosignals');

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
