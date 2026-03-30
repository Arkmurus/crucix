// apis/sources/gdelt.mjs
// GDELT v2 — Global Event Database
// Fixed: uses correct v2 API endpoint and GKG (Global Knowledge Graph) feed
// Reference: https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/
// Free — no API key required

const GDELT_DOC_API   = 'https://api.gdeltproject.org/api/v2/doc/doc';
const GDELT_GEO_API   = 'https://api.gdeltproject.org/api/v2/geo/geo';

// Cache: GDELT rate-limits at ~1 req/15min per IP on shared cloud. Cache 12 min.
let _cache = null;
let _cacheTime = 0;
const CACHE_MS = 12 * 60 * 1000;

// Themes to monitor — GDELT theme codes for conflict/crisis intelligence
const MONITOR_THEMES = [
  'CONFLICT', 'MILITARY', 'SANCTION', 'PROTEST', 'TERROR',
  'WMD', 'NUCLEAR', 'CYBERSECURITY', 'ECONOMIC_CRISIS', 'MARITIME'
];

export async function fetchGDELT() {
  // Return cached result if fresh enough (avoids 429 on 5-min sweep cycle)
  if (_cache && (Date.now() - _cacheTime) < CACHE_MS) {
    console.log('[GDELT] Returning cached result');
    return _cache;
  }

  const results = { updates: [], signals: [], error: null };

  try {
    // Doc API: search recent articles matching conflict/crisis themes
    const query = '(conflict OR military OR sanctions OR strike OR crisis)';
    const params = new URLSearchParams({
      query,
      mode:       'artlist',
      maxrecords: '25',
      format:     'json',
      timespan:   '1h',        // last 1 hour only — keeps it fresh
      sort:       'ToneDesc',  // most negative tone first (crisis coverage)
    });

    const res = await fetch(`${GDELT_DOC_API}?${params}`, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(15000)
    });

    if (!res.ok) throw new Error(`GDELT Doc API ${res.status}`);
    const rawText = await res.text();
    if (!rawText.trim().startsWith("{") && !rawText.trim().startsWith("[")) throw new Error("GDELT non-JSON: "+rawText.substring(0,40));
    const data = JSON.parse(rawText);

    const articles = data.articles || [];
    for (const a of articles.slice(0, 15)) {
      results.updates.push({
        title:   a.title   || 'Untitled',
        url:     a.url     || '',
        source:  a.domain  || 'GDELT',
        seenAt:  a.seendate || new Date().toISOString(),
        tone:    a.tone    || 0,
        country: a.sourcecountry || '',
        lang:    a.language || 'English',
        type:    'gdelt_article'
      });
    }

    // GEO API: geographic event clusters (conflict hotspots)
    const geoParams = new URLSearchParams({
      query,
      mode:     'pointdata',
      format:   'json',
      timespan: '24h',
    });

    try {
      const geoRes = await fetch(`${GDELT_GEO_API}?${geoParams}`, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
        signal: AbortSignal.timeout(12000)
      });
      if (geoRes.ok) {
        const geoData = await geoRes.json();
        const points  = geoData.features || [];
        // Top 5 geographic hotspots by article count
        const hotspots = points
          .sort((a, b) => (b.properties?.count || 0) - (a.properties?.count || 0))
          .slice(0, 5)
          .map(f => ({
            name:    f.properties?.name || 'Unknown',
            count:   f.properties?.count || 0,
            lat:     f.geometry?.coordinates?.[1] || 0,
            lon:     f.geometry?.coordinates?.[0] || 0,
            type:    'gdelt_hotspot'
          }));
        results.signals = hotspots;
      }
    } catch (geoErr) {
      console.warn('[GDELT] GEO API failed (non-fatal):', geoErr.message);
    }

    console.log(`[GDELT] ${results.updates.length} articles, ${results.signals.length} hotspots`);
    _cache = results;
    _cacheTime = Date.now();
  } catch (err) {
    results.error = err.message;
    console.error('[GDELT] Error:', err.message);
    if (_cache) {
      console.log('[GDELT] Using stale cache after error');
      return _cache;
    }
  }

  return results;
}
