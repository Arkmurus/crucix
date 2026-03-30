// apis/sources/opensanctions.mjs
// OpenSanctions — consolidated global sanctions database
// Covers: OFAC, EU, UN, UK FCDO, SECO, and 40+ other authorities
// Reference: https://www.opensanctions.org/docs/api/

const BASE_URL = 'https://api.opensanctions.org';

const DATASETS = ['us_ofac_sdn', 'eu_fsf', 'un_sc_sanctions', 'gb_hmt_sanctions'];

const DATASET_LABELS = {
  us_ofac_sdn:      'OFAC SDN (US)',
  eu_fsf:           'EU Financial Sanctions',
  un_sc_sanctions:  'UN Security Council',
  gb_hmt_sanctions: 'UK FCDO (HMT)',
  ch_seco_sanctions:'SECO (Switzerland)',
  ca_osfi_sanctions:'OSFI (Canada)',
  au_dfat_sanctions:'DFAT (Australia)',
};

export async function fetchOpenSanctions() {
  const results = { updates: [], recent: [], stats: {}, error: null };
  try {
    const params = new URLSearchParams({
      limit:   '50',
      sort:    'last_change:desc',
      dataset: DATASETS.join(','),
      schema:  'LegalEntity,Person,Organization',
    });
    const headers = {
      'Accept':     'application/json',
      'User-Agent': 'CrucixIntelligence/1.0',
    };
    if (process.env.OPENSANCTIONS_API_KEY) {
      headers['Authorization'] = `ApiKey ${process.env.OPENSANCTIONS_API_KEY}`;
    }
    const res = await fetch(`${BASE_URL}/entities?${params}`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });
    if (res.status === 402 || res.status === 401 || res.status === 403) {
      results.error = 'OpenSanctions requires a free API key — register at https://www.opensanctions.org/api/ and set OPENSANCTIONS_API_KEY env var';
      console.warn('[OpenSanctions] API key required (402/401/403). Register free at opensanctions.org');
      return results;
    }
    if (!res.ok) throw new Error(`OpenSanctions API ${res.status}`);
    const data = await res.json();
    const entities = data.results || [];
    for (const e of entities) {
      const lists = (e.datasets || []).map(d => DATASET_LABELS[d] || d).filter(Boolean);
      results.updates.push({
        name:       e.caption || 'Unknown',
        id:         e.id,
        schema:     e.schema,
        datasets:   lists,
        lastChange: e.last_change || '',
        topics:     e.properties?.topics || [],
        country:    e.properties?.country?.[0] || '',
        type:       'sanctions_entity',
      });
    }
    results.stats = {
      total:     data.total || 0,
      returned:  entities.length,
      datasets:  DATASETS,
      fetchedAt: new Date().toISOString(),
    };
    results.recent = results.updates.filter(e => e.datasets.length >= 2);

    // ── Sanctions lead-time detection ──────────────────────────────────────
    // Entity appearing on 2+ lists within 48h = early designation signal,
    // often days before the official press release.
    const cutoff48h = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString();
    results.preDesignation = results.updates.filter(e =>
      e.datasets.length >= 2 &&
      e.lastChange &&
      e.lastChange >= cutoff48h
    ).map(e => ({
      ...e,
      leadTimeSignal: true,
      text: `PRE-DESIGNATION: ${e.name} added to ${e.datasets.length} lists (${e.datasets.join(', ')}) within 48h — potential imminent official designation`,
      priority: 'critical',
    }));

    if (results.preDesignation.length > 0) {
      console.log(`[OpenSanctions] ⚠️  ${results.preDesignation.length} pre-designation signals (multi-list within 48h)`);
    }

    console.log(`[OpenSanctions] ${entities.length} entities, ${results.recent.length} multi-list`);
  } catch (err) {
    results.error = err.message;
    console.error('[OpenSanctions] Error:', err.message);
  }
  return results;
}

export async function searchSanctions(query) {
  try {
    const params = new URLSearchParams({ q: query, limit: '10', dataset: DATASETS.join(',') });
    const res = await fetch(`${BASE_URL}/match?${params}`, {
      headers: { 'Accept': 'application/json', 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return { results: [], error: `API ${res.status}` };
    const data = await res.json();
    return {
      query,
      results: (data.results || []).map(e => ({
        name:    e.caption,
        score:   e.score || 0,
        lists:   (e.datasets || []).map(d => DATASET_LABELS[d] || d),
        schema:  e.schema,
        country: e.properties?.country?.[0] || '',
        id:      e.id,
        url:     `https://www.opensanctions.org/entities/${e.id}/`,
      })),
    };
  } catch (err) {
    return { query, results: [], error: err.message };
  }
}
