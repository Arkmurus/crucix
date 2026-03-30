// apis/sources/lusophone.mjs
// Lusophone & West Africa Intelligence — unique Arkmurus edge
// Covers: Guinea-Bissau, Angola, Mozambique, Cape Verde, São Tomé,
//         Timor-Leste, Brazil, Portugal + broader ECOWAS/AU region
// Sources: ECOWAS, African Union, ReliefWeb (filtered), ACLED (filtered),
//          RSS feeds from key Lusophone/African news outlets
// Free — no API keys required

const SOURCES = [
  // ECOWAS Peace & Security alerts
  {
    name:   'ECOWAS Peace & Security',
    url:    'https://www.ecowas.int/feed/',
    type:   'rss',
    region: 'West Africa',
    weight: 'high',
  },
  // African Union Peace & Security Council
  {
    name:   'African Union PSC',
    url:    'https://au.int/en/rss.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'high',
  },
  // ReliefWeb — filtered to Lusophone countries
  {
    name:   'ReliefWeb Guinea-Bissau',
    url:    'https://reliefweb.int/country/gnb/updates.rss',
    type:   'rss',
    region: 'Guinea-Bissau',
    weight: 'critical',
  },
  {
    name:   'ReliefWeb Angola',
    url:    'https://reliefweb.int/country/ago/updates.rss',
    type:   'rss',
    region: 'Angola',
    weight: 'high',
  },
  {
    name:   'ReliefWeb Mozambique',
    url:    'https://reliefweb.int/country/moz/updates.rss',
    type:   'rss',
    region: 'Mozambique',
    weight: 'high',
  },
  // DW Africa (Portuguese service)
  {
    name:   'RFI Portuguese Africa',
    url:    'https://www.rfi.fr/pt/rss',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },
  // VOA Africa
  {
    name:   'Al Jazeera Africa',
    url:    'https://www.aljazeera.com/xml/rss/all.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },
  // AllAfrica — West Africa
  {
    name:   'AllAfrica West Africa',
    url:    'https://allafrica.com/tools/headlines/rdf/westafrica/headlines.rdf',
    type:   'rss',
    region: 'West Africa',
    weight: 'medium',
  },
  // African Development Bank — project pipeline
  {
    name:   'BBC Africa',
    url:    'https://feeds.bbci.co.uk/news/world/africa/rss.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },
];

// Keywords that elevate priority for Arkmurus operations
const ARKMURUS_KEYWORDS = [
  // Security & conflict
  'coup', 'junta', 'military', 'armed', 'conflict', 'attack', 'violence',
  'instability', 'unrest', 'protest', 'election', 'crisis', 'sanction',
  // Lusophone countries
  'guinea-bissau', 'guinea bissau', 'bissau', 'angola', 'mozambique',
  'cabo verde', 'cape verde', 'são tomé', 'sao tome', 'timor', 'macau',
  // Defence & procurement
  'defence', 'defense', 'military', 'weapons', 'arms', 'procurement',
  'contract', 'tender', 'security forces', 'police', 'army', 'navy',
  // Economic
  'oil', 'gas', 'mineral', 'mining', 'infrastructure', 'port', 'airport',
  'investment', 'china', 'russian', 'wagner', 'mercenary',
  // Organisations
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

      // Track by region
      if (!results.regions[src.region]) results.regions[src.region] = 0;
      results.regions[src.region]++;

      // Elevate to signal if critical or high weight
      if (priority === 'critical' || priority === 'high') {
        results.signals.push({
          text:     `[${src.region.toUpperCase()}] ${item.title}`,
          source:   src.name,
          url:      item.link || '',
          priority,
          type:     'lusophone_signal',
        });
      }

      // Alert for critical items
      if (priority === 'critical') {
        results.alerts.push({
          text:     `LUSOPHONE ALERT [${src.region}]: ${item.title}`,
          source:   src.name,
          priority: 'critical',
        });
      }
    }
  }

  // Sort by priority
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
  // Try direct fetch first
  try {
    const res = await fetch(src.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
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
