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

// Fallback: OFAC recent actions RSS (no API key needed)
async function fetchSanctionsFallback() {
  const sources = [
    'https://home.treasury.gov/rss.xml',
    'https://ofac.treasury.gov/rss.xml',
  ];
  for (const url of sources) {
    try {
      const res = await fetch(url, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0', 'Accept': 'application/rss+xml, application/xml, text/xml' },
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) continue;
      const xml = await res.text();
      const items = [];
      const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
      let match;
      while ((match = itemRegex.exec(xml)) !== null) {
        const block = match[1];
        const title = block.match(/<title[^>]*><!\[CDATA\[([\s\S]*?)\]\]><\/title>|<title[^>]*>([\s\S]*?)<\/title>/i);
        const link  = block.match(/<link>([\s\S]*?)<\/link>/i);
        const date  = block.match(/<pubDate>([\s\S]*?)<\/pubDate>/i);
        const t = (title?.[1] || title?.[2] || '').trim();
        if (!t) continue;
        const lower = t.toLowerCase();
        if (!lower.includes('sanction') && !lower.includes('ofac') && !lower.includes('designat') && !lower.includes('blocked')) continue;
        items.push({
          name:       t,
          id:         (link?.[1] || '').trim(),
          schema:     'LegalEntity',
          datasets:   ['us_ofac_sdn'],
          lastChange: (date?.[1] || '').trim(),
          topics:     [],
          country:    '',
          type:       'sanctions_entity',
        });
      }
      if (items.length > 0) {
        console.log(`[OpenSanctions] Fallback: ${items.length} OFAC items from Treasury RSS`);
        return items;
      }
    } catch {}
  }
  return [];
}

export async function fetchOpenSanctions() {
  const results = { updates: [], recent: [], stats: {}, error: null };
  try {
    // 2026-04-12: OpenSanctions deprecated /entities endpoint (returns 404).
    // New endpoint: /search/default?q=... for entity search, or
    // /collections/default/entities for listing. Using /search/default
    // with a broad query to get recent sanctions changes.
    const params = new URLSearchParams({
      limit:   '50',
      schema:  'LegalEntity,Person,Organization',
    });
    const headers = {
      'Accept':     'application/json',
      'User-Agent': 'CrucixIntelligence/1.0',
    };
    if (process.env.OPENSANCTIONS_API_KEY) {
      headers['Authorization'] = `ApiKey ${process.env.OPENSANCTIONS_API_KEY}`;
    }
    // Try the new /search endpoint. Use 'sanctions' as the query term
    // (not wildcard '*' which returns 400). This returns recently-changed
    // sanctioned entities — the sweep's purpose is to detect new listings.
    let res = await fetch(`${BASE_URL}/search/default?q=sanctions&${params}`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });
    if (res.status === 404) {
      // Try collections endpoint as fallback
      res = await fetch(`${BASE_URL}/collections/default/entities?${params}`, {
        headers,
        signal: AbortSignal.timeout(15000),
      });
    }
    if (res.status === 402 || res.status === 401 || res.status === 403) {
      throw new Error(`OpenSanctions API ${res.status} — check OPENSANCTIONS_API_KEY`);
    }
    if (res.status === 404) {
      throw new Error(`OpenSanctions API 404 — endpoint not found, API may have changed`);
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
    // Fallback to OFAC Treasury RSS
    try {
      const fallback = await fetchSanctionsFallback();
      if (fallback.length > 0) {
        results.updates = fallback;
        results.error = null;
      }
    } catch {}
  }
  return results;
}

export async function searchSanctions(query) {
  try {
    const params = new URLSearchParams({ q: query, limit: '10', dataset: DATASETS.join(',') });
    const headers = { 'Accept': 'application/json', 'User-Agent': 'CrucixIntelligence/1.0' };
    if (process.env.OPENSANCTIONS_API_KEY) {
      headers['Authorization'] = `ApiKey ${process.env.OPENSANCTIONS_API_KEY}`;
    }
    const res = await fetch(`${BASE_URL}/search/default?${params}`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });
    if (res.status === 401 || res.status === 403) {
      return { results: [], error: 'OpenSanctions auth failed — check OPENSANCTIONS_API_KEY' };
    }
    if (res.status === 429) {
      return { results: [], error: 'OpenSanctions rate-limited — set OPENSANCTIONS_API_KEY for higher quota' };
    }
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
