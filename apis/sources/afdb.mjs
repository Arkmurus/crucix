// apis/sources/afdb.mjs
// African Development Bank — Project Pipeline Intelligence
// Covers infrastructure investment across Lusophone Africa and broader ECOWAS region
// Free API: https://projectsapi.afdb.org
// No API key required

import { shouldSkip, recordFailure, recordSuccess } from '../../lib/util/throttle.mjs';
import { enrichFetchError } from '../utils/fetch_error.mjs';

// ORDS API (projectsapi.afdb.org) and OpenData portal consistently fail on cloud IPs — removed.
// IATI Datastore requires authentication (401) — removed.
const AFDB_NEWS_RSS     = 'https://www.afdb.org/en/rss/news-and-events';
const AFDB_NEWS_RSS_ALT = 'https://www.afdb.org/en/rss.xml';
const RSS2JSON          = 'https://api.rss2json.com/v1/api.json?rss_url=';

// R-F23 2026-05-01: AfDB has a ~30% failure rate today (5/13 sweeps)
// where ALL 5 fallback endpoints fail simultaneously. Shared root
// cause: rss2json free quota gets burned by DefenseNews/Lusophone
// earlier in the same sweep, plus AfDB direct site rate-limits the
// seenode IP under load. After 3 consecutive total-failures, skip
// AfDB for 5 minutes so we stop wasting 5×10s = 50s of timeout each
// sweep on a known-broken source.
const _CIRCUIT_KEY = 'afdb:all_endpoints';

// Countries of primary interest (ISO2 codes)
const PRIORITY_COUNTRIES = {
  'GW': 'Guinea-Bissau',
  'AO': 'Angola',
  'MZ': 'Mozambique',
  'CV': 'Cape Verde',
  'ST': 'São Tomé and Príncipe',
  'GN': 'Guinea',
  'SN': 'Senegal',
  'ML': 'Mali',
  'BF': 'Burkina Faso',
  'NE': 'Niger',
  'CI': "Côte d'Ivoire",
  'GH': 'Ghana',
  'NG': 'Nigeria',
  'SD': 'Sudan',
  'ET': 'Ethiopia',
  'SO': 'Somalia',
  'CD': 'Congo (DRC)',
};

const CRITICAL_SECTORS = [
  'transport', 'energy', 'water', 'infrastructure', 'governance',
  'security', 'agriculture', 'finance', 'social', 'health',
];

// Infer a PRIORITY_COUNTRIES key from a free-text title. The RSS-news
// fallback path returns per-item titles with no country field, so without
// this every item was tagged 'AF' → filtered out → the whole source
// silently returned 0 projects.
// Longest name first so "São Tomé and Príncipe" matches before "São Tomé",
// "Côte d'Ivoire" before "Ivoire", and "Congo (DRC)" before "Congo".
const _COUNTRY_NAME_LOOKUP = Object.entries(PRIORITY_COUNTRIES)
  .map(([iso, name]) => ({ iso, name }))
  .sort((a, b) => b.name.length - a.name.length);

function _inferCountry(title) {
  if (!title) return '';
  const t = title.toLowerCase();
  for (const { iso, name } of _COUNTRY_NAME_LOOKUP) {
    // Match country name OR the iso code in word-boundary context
    // (prevents "GH" inside "ghana" from firing on unrelated text).
    const nameMatch = t.includes(name.toLowerCase());
    const isoRe = new RegExp(`\\b${iso}\\b`, 'i');
    if (nameMatch || isoRe.test(title)) return iso;
  }
  return '';
}

async function fetchAfDBProjects() {
  // ORDS API and OpenData portal consistently unreachable from cloud IPs — removed.
  // Primary: Google News RSS (reliably works from cloud IPs)
  // Fallback: AfDB RSS chain (direct → rss2json → allorigins)

  const GNEWS_URL = 'https://news.google.com/rss/search?q=%22African+Development+Bank%22+project+africa+infrastructure&hl=en&gl=US&ceid=US:en';
  const rssAttempts = [
    () => fetch(GNEWS_URL, { headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36' }, signal: AbortSignal.timeout(10000) }),
    () => fetch(AFDB_NEWS_RSS, { headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36' }, signal: AbortSignal.timeout(10000) }),
    () => fetch(RSS2JSON + encodeURIComponent(AFDB_NEWS_RSS), { signal: AbortSignal.timeout(10000) }),
    () => fetch(RSS2JSON + encodeURIComponent(AFDB_NEWS_RSS_ALT), { signal: AbortSignal.timeout(10000) }),
  ];
  for (const attempt of rssAttempts) {
    try {
      const res = await attempt();
      if (!res.ok) continue;
      const text = await res.text();
      // Handle both direct XML and rss2json JSON
      let items = [];
      if (text.trim().startsWith('{')) {
        const data = JSON.parse(text);
        if (data.status === 'ok' && data.items?.length) {
          items = data.items.map(i => ({ title: i.title || '', pubDate: i.pubDate || '' }));
        }
      } else if (text.includes('<item>')) {
        const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
        let m;
        while ((m = itemRegex.exec(text)) !== null) {
          const block = m[1];
          const title = block.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
          const date  = block.match(/<pubDate>([\s\S]*?)<\/pubDate>/i);
          items.push({ title: (title?.[1] || '').replace(/<!\[CDATA\[|\]\]>/g, '').trim(), pubDate: date?.[1] || '' });
        }
      }
      if (items.length > 0) {
        return items.slice(0, 10).map(i => {
          const iso = _inferCountry(i.title);
          return {
            project_name:  i.title,
            country:       iso || 'AF',
            country_name:  iso ? PRIORITY_COUNTRIES[iso] : 'Africa (Multi-country)',
            sector:        'news',
            status:        'active',
            ua_amount:     0,
            approval_date: i.pubDate,
          };
        });
      }
    } catch {}
  }

  // Final fallback: allorigins proxy (handles CORS/IP blocks)
  try {
    const res = await fetch(`https://api.allorigins.win/get?url=${encodeURIComponent(AFDB_NEWS_RSS)}`, {
      signal: AbortSignal.timeout(12000),
    });
    if (res.ok) {
      const wrapper = await res.json();
      const xml = wrapper.contents || '';
      if (xml.includes('<item>')) {
        const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
        let m;
        const items = [];
        while ((m = itemRegex.exec(xml)) !== null) {
          const block = m[1];
          const title = block.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
          const date  = block.match(/<pubDate>([\s\S]*?)<\/pubDate>/i);
          items.push({ title: (title?.[1] || '').replace(/<!\[CDATA\[|\]\]>/g, '').trim(), pubDate: date?.[1] || '' });
        }
        if (items.length > 0) {
          console.log(`[AfDB] allorigins proxy: ${items.length} items`);
          return items.slice(0, 10).map(i => {
            const iso = _inferCountry(i.title);
            return {
              project_name:  i.title,
              country:       iso || 'AF',
              country_name:  iso ? PRIORITY_COUNTRIES[iso] : 'Africa (Multi-country)',
              sector:        'news',
              status:        'active',
              ua_amount:     0,
              approval_date: i.pubDate,
            };
          });
        }
      }
    }
  } catch {}

  throw new Error('All AfDB endpoints unreachable');
}

export async function briefing() {
  console.log('[AfDB] Fetching project pipeline...');
  const results = { updates: [], signals: [], stats: {}, error: null };

  // R-F23: short-circuit when all AfDB fallbacks have been failing
  // recently. Saves 5×10s of timeouts per sweep during outage windows.
  if (shouldSkip(_CIRCUIT_KEY)) {
    results.error = 'AfDB circuit open — recent consecutive failures';
    return results;
  }

  try {
    const items = await fetchAfDBProjects();
    recordSuccess(_CIRCUIT_KEY);

    let lusophoneCount = 0;
    let totalValue     = 0;

    for (const proj of items) {
      const country  = (proj.country || proj.COUNTRY || '').toUpperCase().substring(0, 2);
      const name     = proj.project_name || proj.PROJECT_NAME || proj.name || '';
      const sector   = (proj.sector || proj.SECTOR || '').toLowerCase();
      const status   = proj.status || proj.STATUS || '';
      const value    = parseFloat(proj.ua_amount || proj.UA_AMOUNT || proj.amount || 0);
      const approved = proj.approval_date || proj.APPROVAL_DATE || '';

      // R-F275 (2026-05-11) — silent-drop fix. Previously this loop did:
      //   if (!PRIORITY_COUNTRIES[country]) continue;
      // which dropped ~9 of every 10 AfDB items because most news titles
      // don't name a specific country, _inferCountry() returns "", and
      // the item gets tagged country='AF' (Africa multi-country) which
      // isn't in the 17-country PRIORITY_COUNTRIES dict. Operator saw
      // "[AfDB] 1 projects · 0 Lusophone · $0M UA" every sweep as a result.
      //
      // Now: keep ALL items (the upstream fetch already caps at 10).
      // Priority is set per-item so Lusophone gets 'high', other priority
      // countries get 'medium' when value>100, multi-country/regional
      // items get 'normal'. The 10-item cap from fetchAfDBProjects() means
      // there's no clutter risk — at most 10 updates per sweep, vs. the
      // previous ~1 per sweep.
      const isPriorityCountry = !!PRIORITY_COUNTRIES[country];
      const countryName = isPriorityCountry
        ? PRIORITY_COUNTRIES[country]
        : (proj.country_name || (country === 'AF' ? 'Africa (Multi-country)' : (country || 'Africa')));

      const isLusophone = ['GW','AO','MZ','CV','ST'].includes(country);
      if (isLusophone) lusophoneCount++;
      if (value > 0) totalValue += value;

      const priority = isLusophone ? 'high' :
                       (isPriorityCountry && value > 100) ? 'medium' :
                       value > 200 ? 'medium' :
                       'normal';

      results.updates.push({
        title:   `[AfDB] ${countryName}: ${name}`,
        source:  'AfDB',
        country: countryName,
        sector,
        status,
        value_mua: value, // Millions of Units of Account
        approved,
        url:     `https://www.afdb.org/en/projects-and-operations`,
        type:    'afdb_project',
        priority,
      });

      if (isLusophone || value > 200) {
        results.signals.push({
          text:     `AfDB ${countryName}: ${name} — $${value.toFixed(0)}M UA (${sector})`,
          source:   'AfDB',
          priority,
        });
      }
    }

    results.stats = {
      totalProjects:    results.updates.length,
      lusophoneProjects: lusophoneCount,
      totalValueMUA:    Math.round(totalValue),
      fetchedAt:        new Date().toISOString(),
    };

    console.log(`[AfDB] ${results.updates.length} projects · ${lusophoneCount} Lusophone · $${Math.round(totalValue)}M UA`);
  } catch (err) {
    // R-F23: bump circuit-breaker counter so repeated total-failures
    // start short-circuiting next call.
    recordFailure(_CIRCUIT_KEY);
    // R-F369: enrich error so '[AfDB] Error: All AfDB endpoints unreachable'
    // (string message) and undici TypeErrors both surface meaningful detail.
    const detail = enrichFetchError(err);
    results.error = err.message;
    console.error('[AfDB] Error:', detail);
  }

  return results;
}

// CLI test
if (process.argv[1]?.endsWith('afdb.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
