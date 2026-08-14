// test/intel-value-chain-rf3536.test.mjs
//
// R-F3536 — the surface half of the intel value-chain fix. Lifts the pure
// functions out of dashboard.html and RUNS them, so this guards behaviour
// rather than the presence of a string.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PAGE = readFileSync(join(__dirname, '..', 'public/dashboard.html'), 'utf8');
const HOOKS = readFileSync(join(__dirname, '..', 'lib/telegram/channelServerHooks.mjs'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}
function lift(name) {
  const start = PAGE.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`${name} not found`);
  const open = PAGE.indexOf('{', start);
  let depth = 0, i = open;
  for (; i < PAGE.length; i++) {
    if (PAGE[i] === '{') depth++;
    else if (PAGE[i] === '}') { depth--; if (depth === 0) break; }
  }
  return PAGE.slice(start, i + 1);
}

console.log('R-F3536 — intel value chain (surface)\n');

// ── evidenceLabel: the grade badge and the evidence line must agree ──────────
const evidenceLabel = new Function(lift('evidenceLabel') + '; return evidenceLabel;')();

check('an official primary source is not called "single-source"',
  evidenceLabel({ source_tier: 'tier_1a', corroboration: 'single-source' }) === 'official primary source',
  'this is the exact contradiction on every Grade A card');
check('a corroborated signal states how many sources',
  evidenceLabel({ source_tier: 'tier_2', evidence_count: 3 }) === '3 independent sources');
check('an official screen is labelled as such',
  evidenceLabel({ source_tier: 'tier_1b' }) === 'official screen against primary lists');
check('a genuine single-source item still says corroboration pending',
  /corroboration pending/.test(evidenceLabel({ source_tier: 'tier_2' })));
check('a stated corroboration on a weak tier is preserved',
  evidenceLabel({ source_tier: 'tier_3', corroboration: 'two outlets' }) === 'two outlets');

// ── channel policy ───────────────────────────────────────────────────────────
const allowed = HOOKS.split('_GOLDEN_ALLOWED_TYPES = new Set([')[1].split(']);')[0];

// R-F4012 (C-89) — this asserted `!allowed.includes("'active_tender'")`, i.e. that
// open tenders are never published as intelligence. R-F3688 DELIBERATELY reversed
// that on 2026-08-04, admitting active_tender as "the OPEN half of the procurement
// lane" alongside security_operation and political_transition, all three recorded
// as operator-admitted.
//
// So the guard sat permanently red while pinning a policy the operator had
// replaced. Greening it by deleting 'active_tender' from the allowed set would
// have REVERSED an operator decision to satisfy a test — the same trap as C-81's
// unavailable-source checkbox, and the reason a red test must be diagnosed rather
// than silenced.
//
// The surviving intent is that the golden feed publishes a REVIEWED set, not
// whatever accumulates. So the set is pinned exactly: a new type has to be added
// here too, which forces the review the original assertion was really protecting.
const ADMITTED_TYPES = [
  'sanctions_change',
  'contract_award',
  'active_tender',        // R-F3688
  'budget_movement',
  'programme_signal',
  'competitor_activity',
  'conflict_escalation',
  'security_operation',   // R-F3688
  'political_transition', // R-F3688
];
const declaredTypes = [...allowed.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]);
check('the golden feed publishes exactly the reviewed set of signal types',
  JSON.stringify(declaredTypes) === JSON.stringify(ADMITTED_TYPES));
check('the alarming classes are still publishable',
  ["'sanctions_change'", "'conflict_escalation'", "'competitor_activity'", "'contract_award'"]
    .every(t => allowed.includes(t)));

// ── raw channel text ─────────────────────────────────────────────────────────
check('verbatim channel posts are no longer rendered',
  !PAGE.includes("escHtml(truncate(p.text||'',150))"));
check('collection is still shown as alive (observability kept)',
  PAGE.includes('items collected from') && PAGE.includes('Channel collection is an INPUT'));
check('the panel no longer calls itself Raw Telegram Collection',
  !PAGE.includes('Raw Telegram Collection') && PAGE.includes('Channel Collection'));

// ── counters derive from the rendered feed ───────────────────────────────────
check('correlation KPI counts high AND critical, matching the list below',
  PAGE.includes("c.severity === 'critical' || c.severity === 'high'"));
check('tender KPI counts the rendered feed, not a different window',
  PAGE.includes('window._lastFeedSignals') && PAGE.includes("signal_type) === 'active_tender'"));
check('the feed publishes what it rendered', PAGE.includes('window._lastFeedSignals = signals;'));

// ── empty watchlist ──────────────────────────────────────────────────────────
check('an empty watchlist prompts the user instead of reporting a failed match',
  PAGE.includes('add entities to your watchlist'));
check('a populated watchlist reports the real count',
  PAGE.includes('watched entit'));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
