// Sanctions actions — US State Department (R-F2419: real Federal Register feed).
//
// Was a pure fabricated stub: 0 fetches, hardcoded "12,000+ / Russian defense
// sector / Iran missile" literals returned on every sweep, still wired in as
// runSource('Sanctions', ...). OFAC (Treasury) recent actions are already
// covered for real by ofac.mjs (R-F2416) and the UN consolidated list by
// un_sc_sanctions.mjs, so this source now surfaces the COMPLEMENTARY US STATE
// DEPARTMENT sanctions actions (nonproliferation / ISN designations) from the
// Federal Register API — real, dated, free, no API key. Honest-empty (status
// 'error', empty updates) on failure — never fabricated.
//
// Full on-demand entity screening remains the Python brain (sanctions.py +
// the OFAC/EU/UK/UN ingests); this feed answers "what did State do recently".

import '../utils/env.mjs';
import { fetchFederalRegister } from './_federal_register.mjs';

export async function briefing() {
  console.log('[Sanctions] Fetching real State Dept sanctions actions (Federal Register)...');
  return fetchFederalRegister({
    sourceName: 'Sanctions',
    emoji: '⚖️',
    agencies: ['state-department'],
    types: ['NOTICE'],   // sanctions actions are Notices — filters out unrelated rules
    term: 'sanctions',
    perPage: 12,
    searchUrl: 'https://www.federalregister.gov/agencies/state-department',
  });
}

if (process.argv[1]?.endsWith('sanctions.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
