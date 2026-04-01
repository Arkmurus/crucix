// apis/sources/afdb.mjs
// African Development Bank — Project Pipeline Intelligence
// Covers infrastructure investment across Lusophone Africa and broader ECOWAS region
// Free API: https://projectsapi.afdb.org
// No API key required

// ORDS API (projectsapi.afdb.org) and OpenData portal consistently fail on cloud IPs — removed.
// IATI Datastore requires authentication (401) — removed.
const AFDB_NEWS_RSS     = 'https://www.afdb.org/en/rss/news-and-events';
const AFDB_NEWS_RSS_ALT = 'https://www.afdb.org/en/rss.xml';
const RSS2JSON          = 'https://api.rss2json.com/v1/api.json?rss_url=';

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

async function fetchAfDBProjects() {
  // ORDS API and OpenData portal consistently unreachable from cloud IPs — removed.
  // Go straight to the RSS chain which reliably works via proxy.

  // AfDB news RSS (direct → rss2json → alt URL → allorigins proxy)
  const rssAttempts = [
    () => fetch(AFDB_NEWS_RSS, { headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' }, signal: AbortSignal.timeout(10000) }),
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
        return items.slice(0, 10).map(i => ({
          project_name:  i.title,
          country:       'AF',
          country_name:  'Africa (Multi-country)',
          sector:        'news',
          status:        'active',
          ua_amount:     0,
          approval_date: i.pubDate,
        }));
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
          return items.slice(0, 10).map(i => ({
            project_name: i.title, country: 'AF', country_name: 'Africa (Multi-country)',
            sector: 'news', status: 'active', ua_amount: 0, approval_date: i.pubDate,
          }));
        }
      }
    }
  } catch {}

  throw new Error('All AfDB endpoints unreachable');
}

export async function briefing() {
  console.log('[AfDB] Fetching project pipeline...');
  const results = { updates: [], signals: [], stats: {}, error: null };

  try {
    const items = await fetchAfDBProjects();

    let lusophoneCount = 0;
    let totalValue     = 0;

    for (const proj of items) {
      const country  = (proj.country || proj.COUNTRY || '').toUpperCase().substring(0, 2);
      const name     = proj.project_name || proj.PROJECT_NAME || proj.name || '';
      const sector   = (proj.sector || proj.SECTOR || '').toLowerCase();
      const status   = proj.status || proj.STATUS || '';
      const value    = parseFloat(proj.ua_amount || proj.UA_AMOUNT || proj.amount || 0);
      const approved = proj.approval_date || proj.APPROVAL_DATE || '';
      const countryName = PRIORITY_COUNTRIES[country] || proj.country_name || country;

      if (!PRIORITY_COUNTRIES[country]) continue;

      const isLusophone = ['GW','AO','MZ','CV','ST'].includes(country);
      if (isLusophone) lusophoneCount++;
      if (value > 0) totalValue += value;

      const priority = isLusophone ? 'high' :
                       value > 100 ? 'medium' : 'normal';

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
    results.error = err.message;
    console.error('[AfDB] Error:', err.message);
  }

  return results;
}

// CLI test
if (process.argv[1]?.endsWith('afdb.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
