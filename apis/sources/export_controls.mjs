// Export Controls — US BIS export-control rule changes (R-F2416: real feed).
//
// Was 100% static (no fetch at all): it returned the same hardcoded
// "Wassenaar 42 / MTCR 35 / NSG 48" reference text on every sweep. It now
// returns REAL, dated export-control RULES from the Federal Register API
// (agency = Bureau of Industry and Security, type = RULE) — Entity List
// changes, EAR amendments, license-review-policy revisions, Commerce Control
// List updates. Honest-empty (status 'error'/'degraded') on failure.
//
// Dual-use classification of a specific item is a separate on-demand engine
// (aria_service/intel/dual_use_classifier.py + global_export_control.py); this
// feed answers "what export-control rules changed recently".

import '../utils/env.mjs';
import { fetchFederalRegister } from './_federal_register.mjs';

export async function briefing() {
  console.log('[Export Controls] Fetching real BIS export-control rules (Federal Register)...');
  return fetchFederalRegister({
    sourceName: 'Export Controls',
    emoji: '🚦',
    agencies: ['industry-and-security-bureau'],
    types: ['RULE'],
    perPage: 12,
    searchUrl: 'https://www.federalregister.gov/agencies/industry-and-security-bureau',
  });
}

if (process.argv[1]?.endsWith('export_controls.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
