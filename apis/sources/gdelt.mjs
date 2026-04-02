// apis/sources/gdelt.mjs
// GDELT v2 — Global Event Database
// Fixed: uses correct v2 API endpoint and GKG (Global Knowledge Graph) feed
// Reference: https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/
// Free — no API key required

const GDELT_DOC_API   = 'https://api.gdeltproject.org/api/v2/doc/doc';
const GDELT_GEO_API   = 'https://api.gdeltproject.org/api/v2/geo/geo';
const GDELT_GKG_V1    = 'https://api.gdeltproject.org/api/v1/gkg_geojson';

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

  // Primary: try RSS format (more resilient than JSON on cloud IPs)
  const query = '(conflict OR military OR sanctions OR strike OR crisis)';
  let primaryOk = false;

  try {
    const rssParams = new URLSearchParams({
      query,
      mode:       'artlist',
      maxrecords: '25',
      format:     'rss',
      timespan:   '4h',
      sort:       'DateDesc',
    });
    const res = await fetch(`${GDELT_DOC_API}?${rssParams}`, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(20000),
    });
    if (res.ok) {
      const xml = await res.text();
      const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
      let m;
      while ((m = itemRegex.exec(xml)) !== null && results.updates.length < 15) {
        const block = m[1];
        const getTag = (tag) => {
          const match = block.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\/${tag}>|<${tag}[^>]*>([\\s\\S]*?)<\/${tag}>`, 'i'));
          return (match?.[1] || match?.[2] || '').trim();
        };
        const title = getTag('title');
        if (!title) continue;
        results.updates.push({
          title,
          url:     getTag('link'),
          source:  getTag('source') || 'GDELT',
          seenAt:  getTag('pubDate') || new Date().toISOString(),
          tone:    0,
          country: '',
          lang:    'English',
          type:    'gdelt_article',
        });
      }
      if (results.updates.length > 0) {
        primaryOk = true;
        console.log(`[GDELT] ${results.updates.length} articles (RSS)`);
        _cache = results;
        _cacheTime = Date.now();
      }
    }
  } catch (rssErr) {
    console.warn('[GDELT] RSS attempt failed:', rssErr.message);
  }

  // Fallback: JSON API
  if (!primaryOk) {
    try {
      const params = new URLSearchParams({
        query,
        mode:       'artlist',
        maxrecords: '25',
        format:     'json',
        timespan:   '4h',
        sort:       'ToneDesc',
      });
      const res = await fetch(`${GDELT_DOC_API}?${params}`, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
        signal: AbortSignal.timeout(20000),
      });
      if (!res.ok) throw new Error(`GDELT Doc API ${res.status}`);
      const rawText = await res.text();
      if (!rawText.trim().startsWith('{') && !rawText.trim().startsWith('[')) throw new Error('GDELT non-JSON: ' + rawText.substring(0, 40));
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
          type:    'gdelt_article',
        });
      }
      if (results.updates.length > 0) {
        console.log(`[GDELT] ${results.updates.length} articles (JSON)`);
        _cache = results;
        _cacheTime = Date.now();
      }
    } catch (jsonErr) {
      // v1 GKG fallback
      try {
        const v1Params = new URLSearchParams({
          QUERY:    '(conflict OR military OR sanctions OR coup OR crisis)',
          TIMESPAN: '1440',
          MAXROWS:  '50',
        });
        const v1Res = await fetch(`${GDELT_GKG_V1}?${v1Params}`, {
          headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
          signal: AbortSignal.timeout(20000),
        });
        if (v1Res.ok) {
          const v1Data = await v1Res.json();
          const features = v1Data.features || [];
          for (const f of features.slice(0, 20)) {
            const props = f.properties || {};
            const themes = (props.themes || '').split(';').filter(Boolean);
            results.updates.push({
              title:   props.names || props.locations || 'GDELT GKG Event',
              url:     props.url || '',
              source:  'GDELT GKG',
              seenAt:  props.date || new Date().toISOString(),
              tone:    parseFloat(props.tone) || 0,
              country: props.locations || '',
              themes,
              type:    'gdelt_article',
            });
          }
          results.signals = features.slice(0, 5).map(f => ({
            name:  f.properties?.names || f.properties?.locations || 'Unknown',
            count: 1,
            lat:   f.geometry?.coordinates?.[1] || 0,
            lon:   f.geometry?.coordinates?.[0] || 0,
            type:  'gdelt_hotspot',
          }));
          if (results.updates.length > 0) {
            console.log(`[GDELT] v1 fallback: ${results.updates.length} GKG events`);
            _cache = results;
            _cacheTime = Date.now();
          }
        } else {
          throw new Error(`GDELT v1 ${v1Res.status}`);
        }
      } catch (v1Err) {
        // Final fallback: allorigins proxy (bypasses cloud IP blocks on GDELT)
        try {
          const proxyQuery = new URLSearchParams({
            query, mode: 'artlist', maxrecords: '25', format: 'rss', timespan: '4h', sort: 'DateDesc',
          });
          const proxyRes = await fetch(
            `https://api.allorigins.win/get?url=${encodeURIComponent(`${GDELT_DOC_API}?${proxyQuery}`)}`,
            { signal: AbortSignal.timeout(25000) }
          );
          if (proxyRes.ok) {
            const wrapper = await proxyRes.json();
            const xml = wrapper.contents || '';
            const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
            let m2;
            while ((m2 = itemRegex.exec(xml)) !== null && results.updates.length < 15) {
              const block = m2[1];
              const getTag = (tag) => {
                const match = block.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>|<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
                return (match?.[1] || match?.[2] || '').trim();
              };
              const title = getTag('title');
              if (!title) continue;
              results.updates.push({
                title, url: getTag('link'), source: getTag('source') || 'GDELT',
                seenAt: getTag('pubDate') || new Date().toISOString(),
                tone: 0, country: '', lang: 'English', type: 'gdelt_article',
              });
            }
            if (results.updates.length > 0) {
              console.log(`[GDELT] ${results.updates.length} articles (allorigins proxy)`);
              _cache = results;
              _cacheTime = Date.now();
              return results;
            }
          }
        } catch (proxyErr) {
          console.warn('[GDELT] allorigins proxy failed:', proxyErr.message);
        }

        // Ultimate fallback: Google News RSS (works from any IP, broad conflict/defence coverage)
        try {
          const gnTopics = [
            'https://news.google.com/rss/search?q=military+procurement+defence+contract&hl=en&gl=US&ceid=US:en',
            'https://news.google.com/rss/search?q=africa+military+security+conflict&hl=en&gl=US&ceid=US:en',
          ];
          for (const gnUrl of gnTopics) {
            if (results.updates.length >= 10) break;
            try {
              const gnRes = await fetch(gnUrl, {
                headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
                signal: AbortSignal.timeout(15000),
              });
              if (!gnRes.ok) continue;
              const xml = await gnRes.text();
              const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
              let m3;
              while ((m3 = itemRegex.exec(xml)) !== null && results.updates.length < 20) {
                const block = m3[1];
                const getTag = (tag) => {
                  const match = block.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>|<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
                  return (match?.[1] || match?.[2] || '').trim();
                };
                const title = getTag('title');
                if (!title) continue;
                const srcBlock = block.match(/<source[^>]*>([^<]*)<\/source>/i)?.[1] || 'News';
                results.updates.push({
                  title, url: getTag('link'), source: srcBlock,
                  seenAt: getTag('pubDate') || new Date().toISOString(),
                  tone: 0, country: '', lang: 'English', type: 'gdelt_article',
                });
              }
            } catch {}
          }
          if (results.updates.length > 0) {
            console.log(`[GDELT] ${results.updates.length} articles (Google News fallback)`);
            _cache = results;
            _cacheTime = Date.now();
            return results;
          }
        } catch (gnErr) {
          console.warn('[GDELT] Google News fallback failed:', gnErr.message);
        }

        results.error = jsonErr.message;
        console.error('[GDELT] Error:', jsonErr.message);
        if (_cache) {
          console.log('[GDELT] Using stale cache after error');
          return _cache;
        }
      }
    }
  }

  return results;
}
