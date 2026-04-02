// apis/sources/lusophone.mjs
// Lusophone & West Africa Intelligence — unique Arkmurus edge
// Covers: Guinea-Bissau, Angola, Mozambique, Cape Verde, São Tomé,
//         Timor-Leste, Brazil, Portugal + broader ECOWAS/AU region
// Free — no API keys required

// ReliefWeb JSON API — more reliable than RSS on cloud IPs
const RELIEFWEB_API = 'https://api.reliefweb.int/v1/updates?appname=crucix&limit=15&fields[include][]=title&fields[include][]=url&fields[include][]=date&fields[include][]=primary_country&filter[field]=primary_country.iso3&filter[value]=';

const SOURCES = [
  {
    name:   'ECOWAS Peace & Security',
    url:    'https://news.google.com/rss/search?q=ECOWAS+peace+security+west+africa+military&hl=en&gl=US&ceid=US:en',
    type:   'rss',
    region: 'West Africa',
    weight: 'high',
  },
  {
    name:   'African Union PSC',
    url:    'https://au.int/en/rss.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'high',
  },
  {
    name:   'RFI Portuguese Africa',
    url:    'https://www.rfi.fr/pt/feeds/rss',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },
  {
    name:   'Al Jazeera Africa',
    url:    'https://www.aljazeera.com/xml/rss/all.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },
  {
    name:   'AllAfrica West Africa',
    url:    'https://allafrica.com/tools/headlines/rdf/westafrica/headlines.rdf',
    type:   'rss',
    region: 'West Africa',
    weight: 'medium',
  },
  {
    name:   'BBC Africa',
    url:    'https://feeds.bbci.co.uk/news/world/africa/rss.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },
  {
    name:   'Observador (Portugal)',
    url:    'https://observador.pt/feed/',
    type:   'rss',
    region: 'Portugal/CPLP',
    weight: 'medium',
  },
  {
    name:   'DW África (Portuguese)',
    url:    'https://news.google.com/rss/search?q=DW+africa+angola+mo%C3%A7ambique+portugu%C3%AAs&hl=pt&gl=BR&ceid=BR:pt',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },
  // ReliefWeb via JSON API (reliable, no Render blocks)
  {
    name:   'ReliefWeb Guinea-Bissau',
    url:    RELIEFWEB_API + 'GNB',
    type:   'reliefweb_api',
    region: 'Guinea-Bissau',
    weight: 'critical',
  },
  {
    name:   'ReliefWeb Angola',
    url:    RELIEFWEB_API + 'AGO',
    type:   'reliefweb_api',
    region: 'Angola',
    weight: 'high',
  },
  {
    name:   'ReliefWeb Mozambique',
    url:    RELIEFWEB_API + 'MOZ',
    type:   'reliefweb_api',
    region: 'Mozambique',
    weight: 'high',
  },
  {
    name:   'ReliefWeb Timor-Leste',
    url:    RELIEFWEB_API + 'TLS',
    type:   'reliefweb_api',
    region: 'Timor-Leste',
    weight: 'medium',
  },
  {
    name:   'ReliefWeb Guinea',
    url:    RELIEFWEB_API + 'GIN',
    type:   'reliefweb_api',
    region: 'West Africa',
    weight: 'medium',
  },
  // VOA Portuguese (Africa service) — correct feed URL
  {
    name:   'VOA Portuguese Africa',
    url:    'https://news.google.com/rss/search?q=VOA+africa+angola+mo%C3%A7ambique+portugu%C3%AAs&hl=pt&gl=AO&ceid=AO:pt',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },
  // UN News Africa (Portuguese)
  {
    name:   'UN News Africa PT',
    url:    'https://news.un.org/feed/subscribe/pt/news/region/africa/feed/rss.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },
  // Angola Agência Angola Press
  {
    name:   'Agência Angola Press',
    url:    'https://www.angop.ao/rss.rss',
    type:   'rss',
    region: 'Angola',
    weight: 'high',
  },
];

const ARKMURUS_KEYWORDS = [
  'coup', 'junta', 'military', 'armed', 'conflict', 'attack', 'violence',
  'instability', 'unrest', 'protest', 'election', 'crisis', 'sanction',
  'guinea-bissau', 'guinea bissau', 'bissau', 'angola', 'mozambique',
  'cabo verde', 'cape verde', 'são tomé', 'sao tome', 'timor', 'macau',
  'defence', 'defense', 'weapons', 'arms', 'procurement',
  'contract', 'tender', 'security forces', 'police', 'army', 'navy',
  'oil', 'gas', 'mineral', 'mining', 'infrastructure', 'port', 'airport',
  'investment', 'china', 'russian', 'wagner', 'mercenary',
  'ecowas', 'african union', 'au ', 'afdb', 'imf', 'world bank', 'un ',
  'cplp', 'palop', 'lusophone',
];

const CRITICAL_KEYWORDS = [
  'coup', 'junta', 'overthrow', 'assassination', 'civil war', 'invaded',
  'wagner', 'mercenary', 'nuclear', 'embargo', 'sanctions imposed',
];

export async function briefing() {
  const results = {
    updates:  [],
    signals:  [],
    alerts:   [],
    regions:  {},
    stats:    {},
    error:    null,
  };

  const fetchPromises = SOURCES.map(src => fetchSource(src));
  const settled = await Promise.allSettled(fetchPromises);

  for (let i = 0; i < settled.length; i++) {
    const res = settled[i];
    const src = SOURCES[i];
    if (res.status !== 'fulfilled' || !res.value) continue;

    const items = res.value;
    for (const item of items) {
      const text = `${item.title} ${item.description || ''}`.toLowerCase();
      const isRelevant = ARKMURUS_KEYWORDS.some(k => text.includes(k));
      if (!isRelevant) continue;

      const isCritical = CRITICAL_KEYWORDS.some(k => text.includes(k));
      const priority = isCritical ? 'critical' : src.weight === 'critical' ? 'high' : src.weight;

      const update = {
        title:    item.title,
        source:   src.name,
        region:   src.region,
        url:      item.link || '',
        date:     item.pubDate || new Date().toISOString(),
        priority,
        type:     'lusophone_intel',
      };

      results.updates.push(update);

      if (!results.regions[src.region]) results.regions[src.region] = 0;
      results.regions[src.region]++;

      if (priority === 'critical' || priority === 'high') {
        results.signals.push({
          text:     `[${src.region.toUpperCase()}] ${item.title}`,
          source:   src.name,
          url:      item.link || '',
          priority,
          type:     'lusophone_signal',
        });
      }

      if (priority === 'critical') {
        results.alerts.push({
          text:     `LUSOPHONE ALERT [${src.region}]: ${item.title}`,
          source:   src.name,
          priority: 'critical',
        });
      }
    }
  }

  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  results.updates.sort((a, b) => (order[a.priority] || 3) - (order[b.priority] || 3));
  results.signals.sort((a, b) => (order[a.priority] || 3) - (order[b.priority] || 3));

  results.stats = {
    totalUpdates:   results.updates.length,
    criticalAlerts: results.alerts.length,
    regions:        Object.keys(results.regions).length,
    fetchedAt:      new Date().toISOString(),
  };

  console.log(`[Lusophone] ${results.updates.length} updates · ${results.signals.length} signals · ${results.alerts.length} critical alerts`);
  return results;
}

async function fetchSource(src) {
  // ReliefWeb JSON API — always works, no proxy needed
  if (src.type === 'reliefweb_api') {
    try {
      const res = await fetch(src.url, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0', 'Accept': 'application/json' },
        signal: AbortSignal.timeout(12000),
      });
      if (res.ok) {
        const data = await res.json();
        return (data.data || []).map(d => ({
          title:       d.fields?.title || '',
          link:        d.fields?.url?.url || `https://reliefweb.int/updates/${d.id}`,
          description: d.fields?.title || '',
          pubDate:     d.fields?.date?.created || new Date().toISOString(),
        })).filter(i => i.title);
      }
    } catch {}
    return [];
  }

  // Try direct fetch first
  try {
    const res = await fetch(src.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'Accept':     'application/rss+xml, application/xml, text/xml, */*',
      },
      signal: AbortSignal.timeout(10000),
    });
    if (res.ok) {
      const xml = await res.text();
      const items = parseRSS(xml);
      if (items.length > 0) return items;
    }
  } catch (e) {}

  // Fallback: rss2json proxy (bypasses Render IP blocks)
  try {
    const proxyUrl = 'https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(src.url);
    const res = await fetch(proxyUrl, { signal: AbortSignal.timeout(12000) });
    if (res.ok) {
      const data = await res.json();
      if (data.status === 'ok' && data.items?.length > 0) {
        return data.items.slice(0, 20).map(item => ({
          title:       item.title || '',
          link:        item.link || '',
          description: item.description || item.content || '',
          pubDate:     item.pubDate || '',
        }));
      }
    }
  } catch (e) {}

  // Third proxy: allorigins.win
  try {
    const res = await fetch(`https://api.allorigins.win/get?url=${encodeURIComponent(src.url)}`, {
      signal: AbortSignal.timeout(12000),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.contents) {
        const items = parseRSS(data.contents);
        if (items.length > 0) return items;
      }
    }
  } catch {}

  console.warn(`[Lusophone] ${src.name} failed: all attempts blocked`);
  return [];
}

function parseRSS(xml) {
  const items = [];
  const itemRegex = /<item>([\s\S]*?)<\/item>/gi;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    items.push({
      title:       extractTag(block, 'title'),
      link:        extractTag(block, 'link'),
      description: extractTag(block, 'description'),
      pubDate:     extractTag(block, 'pubDate'),
    });
  }
  return items.slice(0, 20);
}

function extractTag(xml, tag) {
  const re = new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]></${tag}>|<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, 'i');
  const m = xml.match(re);
  if (!m) return '';
  return (m[1] || m[2] || '').replace(/<[^>]+>/g, '').trim();
}
