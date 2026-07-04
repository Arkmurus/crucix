// test/sweep-sanctions-stub-retired-rf2419.test.mjs
//
// Capability test for R-F2419 — retire the fabricated sanctions.mjs stub.
//
// sanctions.mjs was a pure stub (0 fetches; hardcoded "12,000+ / Russian
// defense sector / Iran missile" literals) still wired into the sweep as
// runSource('Sanctions', ...). It now returns REAL US State Department
// sanctions actions from the Federal Register API (agency=state-department,
// term=sanctions) — complementary to ofac.mjs (Treasury) + un_sc_sanctions.mjs
// (UN). Honest-empty on failure. No API key, no paid membership.
//
// Run: node test/sweep-sanctions-stub-retired-rf2419.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { briefing as sanctionsBriefing } from '../apis/sources/sanctions.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
let failures = 0;
const ok = (c, m) => { console.log(`${c ? '  ✓' : '  ✗'} ${m}`); if (!c) failures++; };

const realFetch = globalThis.fetch;
let lastUrl = null;
const mockOk = (p) => async (url) => { lastUrl = String(url); return { ok: true, status: 200, json: async () => p }; };
const mockThrow = () => async () => { throw new Error('ECONNRESET'); };

const SAMPLE = { count: 486, results: [
  { title: 'Notice of Department of State Sanctions Action', publication_date: '2026-05-15', html_url: 'https://www.federalregister.gov/documents/sd1', document_number: '2026-1', type: 'Notice' },
] };

try {
  console.log('real parse + targeting:');
  globalThis.fetch = mockOk(SAMPLE);
  let r = await sanctionsBriefing();
  ok(r.status === 'active' && r.updates.length === 1, 'parses State Dept action into an update');
  ok(r.updates[0].title.includes('Department of State Sanctions Action'), 'carries the REAL State Dept title');
  ok(r.updates[0].url === 'https://www.federalregister.gov/documents/sd1', 'carries the real source URL');
  ok(r.updates[0].content.includes('2026-05-15'), 'carries the real date');
  ok(/state-department/.test(lastUrl), 'targets the State Department agency');
  ok(/conditions%5Bterm%5D=sanctions|conditions\[term\]=sanctions/.test(lastUrl), 'filters by term=sanctions');
  ok(/conditions%5Btype%5D%5B%5D=NOTICE|conditions\[type\]\[\]=NOTICE/.test(lastUrl), 'filters to type=NOTICE (real sanctions actions, not unrelated rules)');
  ok(!r.updates.some((u) => /12,000\+|Russian defense sector|Iran missile/.test((u.title || '') + (u.content || ''))), 'NO fabricated literal in output');

  console.log('honest-empty on failure:');
  globalThis.fetch = mockThrow();
  r = await sanctionsBriefing();
  ok(r.status === 'error' && r.updates.length === 0 && r.signals.length === 0, 'fetch throw → status error, empty (no fabrication)');
} finally {
  globalThis.fetch = realFetch;
}

console.log('static guard:');
const src = readFileSync(join(__dirname, '..', 'apis', 'sources', 'sanctions.mjs'), 'utf8');
ok(!/'🛡️ OFAC Sanctions List Active'|Russian defense sector|Iran missile program|ofacEntities/.test(src), 'fabricated literals removed from source');
ok(src.includes('_federal_register.mjs'), 'wired to the shared Federal Register client');

console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
