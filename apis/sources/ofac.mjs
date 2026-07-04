// OFAC Sanctions actions — US Treasury (R-F2416: real Federal Register feed).
//
// Was a hardcoded-literal stub: it fetched the SDN download URL then DISCARDED
// the response and returned fake, static counts and country lines on every
// sweep (a fabricated feed). It now returns REAL, dated OFAC sanctions
// actions from the Federal Register API (agency = Office of Foreign Assets
// Control) — "Notice of OFAC Sanctions Actions" and the like, with real dates
// and links. Honest-empty (status 'error'/'degraded', no content) on failure.
//
// The on-demand entity screening path is separate (aria_service/intel/
// sanctions.py + OpenSanctions); this feed answers "what did OFAC do recently".

import '../utils/env.mjs';
import { fetchFederalRegister } from './_federal_register.mjs';

export async function briefing() {
  console.log('[OFAC] Fetching real sanctions actions (Federal Register)...');
  return fetchFederalRegister({
    sourceName: 'OFAC',
    emoji: '🛡️',
    agencies: ['foreign-assets-control-office'],
    perPage: 15,
    searchUrl: 'https://ofac.treasury.gov/recent-actions',
  });
}

if (process.argv[1]?.endsWith('ofac.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
