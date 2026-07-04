// test/sweep-real-sanctions-export-feeds-rf2416.test.mjs
//
// Capability test for R-F2416 — OSINT Market Sweep R3a: make the OFAC and
// export-control SWEEP feeds real.
//
// Before: ofac.mjs fetched the SDN URL then DISCARDED it and returned fake
// "12,000+ / Russia / Iran / DPRK" literals; export_controls.mjs returned
// static "Wassenaar 42 / MTCR 35" text with no fetch at all. Both fabricated
// their sweep output on every run.
//
// After: both pull REAL, dated actions from the Federal Register API (OFAC =
// Office of Foreign Assets Control; Export Controls = Bureau of Industry and
// Security, type=RULE). SHIP-GATE: honest-empty (status error, no content) on
// any fetch failure — never fabricated.
//
// This drives the ACTUAL briefing() functions with a mocked global fetch (no
// live network) to prove: (1) real parse, (2) correct agency/type targeting,
// (3) honest-empty on failure + non-200, and statically guards the old
// fabricated literals are gone.
//
// Run: node test/sweep-real-sanctions-export-feeds-rf2416.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { briefing as ofacBriefing } from '../apis/sources/ofac.mjs';
import { briefing as ecBriefing } from '../apis/sources/export_controls.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
let failures = 0;
function ok(cond, msg) {
  if (cond) console.log(`  ✓ ${msg}`);
  else { console.error(`  ✗ ${msg}`); failures++; }
}

const realFetch = globalThis.fetch;
let lastUrl = null;
const mockOk = (payload) => async (url) => { lastUrl = String(url); return { ok: true, status: 200, json: async () => payload }; };
const mockThrow = () => async () => { throw new Error('ECONNRESET'); };
const mock503 = () => async (url) => { lastUrl = String(url); return { ok: false, status: 503, json: async () => ({}) }; };

const SAMPLE = {
  count: 2589,
  results: [
    { title: 'Notice of OFAC Sanctions Actions', publication_date: '2026-07-06', html_url: 'https://www.federalregister.gov/documents/abc', document_number: '2026-0001', type: 'Notice' },
    { title: 'Notice of OFAC Sanctions Action', publication_date: '2026-07-02', html_url: 'https://www.federalregister.gov/documents/def', document_number: '2026-0002', type: 'Notice' },
  ],
};

try {
  // ── real parse (OFAC) ──
  console.log('OFAC — real parse:');
  globalThis.fetch = mockOk(SAMPLE);
  let r = await ofacBriefing();
  ok(r.status === 'active', 'status active when results present');
  ok(r.updates.length === 2 && r.counts.updates === 2, 'parses all results into updates');
  ok(r.updates[0].title.includes('Notice of OFAC Sanctions Actions'), 'update carries the REAL title');
  ok(r.updates[0].url === 'https://www.federalregister.gov/documents/abc', 'update carries the real source URL');
  ok(r.updates[0].content.includes('2026-07-06'), 'update carries the real date');
  ok(r.metrics.totalMatched === 2589 && r.metrics.latestAction === '2026-07-06', 'metrics reflect the real feed');
  ok(/foreign-assets-control-office/.test(lastUrl), 'targets the OFAC agency');
  ok(!r.updates.some((u) => /12,000\+|oligarchs/.test(u.content || u.title)), 'no old fabricated OFAC literal');

  // ── real parse (Export Controls) + correct targeting ──
  console.log('Export Controls — real parse + targeting:');
  globalThis.fetch = mockOk({ count: 739, results: [{ title: 'Streamlining Export Controls for Drone Exports', publication_date: '2026-01-21', html_url: 'https://www.federalregister.gov/documents/drone', document_number: '2026-9', type: 'Rule' }] });
  r = await ecBriefing();
  ok(r.status === 'active' && r.updates.length === 1, 'parses BIS rule into an update');
  ok(r.updates[0].title.includes('Drone Exports'), 'update carries the REAL rule title');
  ok(/industry-and-security-bureau/.test(lastUrl), 'targets the BIS agency');
  ok(/conditions%5Btype%5D%5B%5D=RULE|conditions\[type\]\[\]=RULE/.test(lastUrl), 'filters to type=RULE (substantive rules, not info-collection noise)');
  ok(!r.updates.some((u) => /Wassenaar|MTCR|NSG/.test(u.title)), 'no old fabricated export-control literal');

  // ── honest-empty on failure ──
  console.log('honest-empty on failure (ship-gate):');
  globalThis.fetch = mockThrow();
  r = await ofacBriefing();
  ok(r.status === 'error' && r.updates.length === 0 && r.signals.length === 0, 'fetch throw → status error, EMPTY updates/signals (no fabrication)');
  globalThis.fetch = mock503();
  r = await ecBriefing();
  ok(r.status === 'error' && r.updates.length === 0, 'non-200 → status error, empty (no fabrication)');
} finally {
  globalThis.fetch = realFetch;
}

// ── static guard: fabricated literals removed, shared client wired ──
console.log('static guard:');
const ofacSrc = readFileSync(join(__dirname, '..', 'apis', 'sources', 'ofac.mjs'), 'utf8');
const ecSrc = readFileSync(join(__dirname, '..', 'apis', 'sources', 'export_controls.mjs'), 'utf8');
ok(!ofacSrc.includes('12,000+') && !ofacSrc.includes('oligarchs'), 'ofac.mjs: fabricated literals removed');
ok(!ecSrc.includes('Wassenaar Arrangement') && !ecSrc.includes('participating states'), 'export_controls.mjs: fabricated literals removed');
ok(ofacSrc.includes('_federal_register.mjs') && ecSrc.includes('_federal_register.mjs'), 'both wired to the shared Federal Register client');

console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
