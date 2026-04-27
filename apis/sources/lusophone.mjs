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
    // RFI restructured their RSS paths — old /pt/feeds/rss returns 404.
    // The africa-specific path /pt/áfrica/rss (url-encoded) matches the
    // source's intent and returns a live Portuguese-language Africa feed.
    name:   'RFI Portuguese Africa',
    url:    'https://www.rfi.fr/pt/%C3%A1frica/rss',
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
  // ANGOP's public RSS (rss.rss / /rss / /rss/) returns 404 or 503 under all
  // UAs and proxies — the site actively blocks non-browser clients. Route the
  // source through Google News PT Angola search, which indexes ANGOP + other
  // Angolan outlets and serves RSS reliably from a Portuguese-language locale.
  {
    name:   'Agência Angola Press',
    url:    'https://news.google.com/rss/search?q=Angola+Angop&hl=pt-PT&gl=PT&ceid=PT:pt-150',
    type:   'rss',
    region: 'Angola',
    weight: 'high',
  },
  // ── Lusophone Africa expansion (2026-04-12) ──────────────────────
  {
    name:   'Mozambique Defence',
    url:    'https://news.google.com/rss/search?q=Mo%C3%A7ambique+FADM+militar+defesa+seguran%C3%A7a+Cabo+Delgado&hl=pt&gl=MZ&ceid=MZ:pt',
    type:   'rss',
    region: 'Mozambique',
    weight: 'critical',
  },
  {
    name:   'Angola Military',
    url:    'https://news.google.com/rss/search?q=Angola+FAA+militar+defesa+seguran%C3%A7a+for%C3%A7as+armadas&hl=pt&gl=AO&ceid=AO:pt',
    type:   'rss',
    region: 'Angola',
    weight: 'critical',
  },
  {
    name:   'Guinea-Bissau Security',
    url:    'https://news.google.com/rss/search?q=Guin%C3%A9-Bissau+militar+seguran%C3%A7a+for%C3%A7as+armadas+ECOWAS&hl=pt&gl=BR&ceid=BR:pt',
    type:   'rss',
    region: 'Guinea-Bissau',
    weight: 'critical',
  },
  {
    name:   'Lusa Africa',
    url:    'https://news.google.com/rss/search?q=Lusa+%C3%81frica+defesa+militar+CPLP+portugu%C3%AAs&hl=pt&gl=PT&ceid=PT:pt',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },
  {
    name:   'CPLP Defence Cooperation',
    url:    'https://news.google.com/rss/search?q=CPLP+coopera%C3%A7%C3%A3o+defesa+militar+exerc%C3%ADcio+seguran%C3%A7a&hl=pt&gl=PT&ceid=PT:pt',
    type:   'rss',
    region: 'CPLP',
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

// Cache-keyed by source URL. TTL chosen so the cache covers ~6 sweeps
// (sweeps fire every 5 min; 30 min cache means we re-fetch only every
// 6th sweep instead of every sweep — cuts proxy requests by 6×).
// On all-proxy-fail, we serve the cache so the dashboard panel keeps
// rendering content from the most recent successful pull instead of
// going blank when the proxies are all rate-limited (429/503 cascade
// observed live 2026-04-27 16:59 — entire CPLP layer was failing).
const FEED_CACHE_TTL_S = 30 * 60;
let _persistMod = null;
async function _getPersist() {
  if (_persistMod) return _persistMod;
  try {
    _persistMod = await import('../../lib/persist/store.mjs');
  } catch { _persistMod = { redisGet: async () => null, redisSet: async () => null }; }
  return _persistMod;
}
function _cacheKey(src) {
  // Hash the URL so very long URLs don't blow Redis key limits
  // (some Google News RSS URLs are 200+ chars).
  let h = 0;
  for (let i = 0; i < src.url.length; i++) {
    h = ((h << 5) - h + src.url.charCodeAt(i)) | 0;
  }
  return `crucix:rss_cache:${(src.name || 'unnamed').replace(/\s+/g, '_').toLowerCase()}:${(h >>> 0).toString(36)}`;
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

  // Rotate User-Agents to avoid blocks — sites reject known bot UAs
  const _UAS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
  ];
  const _ua = _UAS[Math.floor(Math.random() * _UAS.length)];

  const persist = await _getPersist();
  const cacheKey = _cacheKey(src);

  // Cache check: if we have a fresh cached payload, return it without
  // hitting the network at all. Saves rate-limited proxy requests.
  try {
    const cached = await persist.redisGet(cacheKey);
    if (cached && Array.isArray(cached.items) && cached.items.length > 0) {
      const ageS = Math.floor((Date.now() - (cached.at || 0)) / 1000);
      if (ageS < FEED_CACHE_TTL_S) {
        return cached.items;
      }
    }
  } catch {}

  // Track last upstream status so a "blocked" warning can distinguish
  // 404 (feed moved) from 403/503 (real blocking) from network errors.
  const attempts = [];

  // Direct fetch — bumped timeout 10s -> 15s. Lusophone PT/AO/MZ
  // government sites can be slow without being unreachable; the previous
  // 10s cap was timing out on perfectly working endpoints.
  try {
    const res = await fetch(src.url, {
      headers: {
        'User-Agent': _ua,
        'Accept':     'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'pt-PT,pt;q=0.9,en;q=0.5',
      },
      signal: AbortSignal.timeout(15000),
      redirect: 'follow',
    });
    attempts.push(`direct=${res.status}`);
    if (res.ok) {
      const xml = await res.text();
      const items = parseRSS(xml);
      if (items.length > 0) {
        try { await persist.redisSet(cacheKey, { items, at: Date.now() }, FEED_CACHE_TTL_S); } catch {}
        return items;
      }
    }
  } catch (e) { attempts.push(`direct=err(${(e && e.message || 'unknown').slice(0, 40)})`); }

  // Fallback chain. Order matters -- previous chain always tried rss2json
  // first which is the most rate-limited free service. New ordering puts
  // r.jina.ai first (more generous quota, content-extracts cleanly) then
  // the older proxies.
  // r.jina.ai is a reader proxy that returns the raw page contents --
  // for RSS URLs it returns the RSS XML which we parse normally.
  try {
    const res = await fetch(`https://r.jina.ai/${src.url}`, {
      headers: { 'User-Agent': _ua, 'Accept': 'application/xml, text/xml, */*' },
      signal: AbortSignal.timeout(15000),
    });
    attempts.push(`jina=${res.status}`);
    if (res.ok) {
      const xml = await res.text();
      const items = parseRSS(xml);
      if (items.length > 0) {
        try { await persist.redisSet(cacheKey, { items, at: Date.now() }, FEED_CACHE_TTL_S); } catch {}
        return items;
      }
    }
  } catch { attempts.push(`jina=err`); }

  // rss2json proxy (bypasses Render IP blocks; rate-limited free quota)
  try {
    const proxyUrl = 'https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(src.url);
    const res = await fetch(proxyUrl, { signal: AbortSignal.timeout(12000) });
    attempts.push(`rss2json=${res.status}`);
    if (res.ok) {
      const data = await res.json();
      if (data.status === 'ok' && data.items?.length > 0) {
        const items = data.items.slice(0, 20).map(item => ({
          title:       item.title || '',
          link:        item.link || '',
          description: item.description || item.content || '',
          pubDate:     item.pubDate || '',
        }));
        try { await persist.redisSet(cacheKey, { items, at: Date.now() }, FEED_CACHE_TTL_S); } catch {}
        return items;
      }
    }
  } catch (e) { attempts.push(`rss2json=err`); }

  // allorigins.win
  try {
    const res = await fetch(`https://api.allorigins.win/get?url=${encodeURIComponent(src.url)}`, {
      signal: AbortSignal.timeout(12000),
    });
    attempts.push(`allorigins=${res.status}`);
    if (res.ok) {
      const data = await res.json();
      if (data.contents) {
        const items = parseRSS(data.contents);
        if (items.length > 0) {
          try { await persist.redisSet(cacheKey, { items, at: Date.now() }, FEED_CACHE_TTL_S); } catch {}
          return items;
        }
      }
    }
  } catch { attempts.push(`allorigins=err`); }

  // corsproxy.io
  try {
    const res = await fetch(`https://corsproxy.io/?${encodeURIComponent(src.url)}`, {
      headers: { 'User-Agent': _ua },
      signal: AbortSignal.timeout(12000),
    });
    attempts.push(`corsproxy=${res.status}`);
    if (res.ok) {
      const xml = await res.text();
      const items = parseRSS(xml);
      if (items.length > 0) {
        try { await persist.redisSet(cacheKey, { items, at: Date.now() }, FEED_CACHE_TTL_S); } catch {}
        return items;
      }
    }
  } catch { attempts.push(`corsproxy=err`); }

  // codetabs.com proxy — additional fallback. Free quota is small but
  // independent of the others, so works when rss2json/allorigins are
  // both rate-limited.
  try {
    const res = await fetch(`https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(src.url)}`, {
      headers: { 'User-Agent': _ua },
      signal: AbortSignal.timeout(12000),
    });
    attempts.push(`codetabs=${res.status}`);
    if (res.ok) {
      const xml = await res.text();
      const items = parseRSS(xml);
      if (items.length > 0) {
        try { await persist.redisSet(cacheKey, { items, at: Date.now() }, FEED_CACHE_TTL_S); } catch {}
        return items;
      }
    }
  } catch { attempts.push(`codetabs=err`); }

  // Last resort: serve stale cache if we have it. Better than empty
  // dashboard. The age is logged so the operator knows it's stale.
  try {
    const cached = await persist.redisGet(cacheKey);
    if (cached && Array.isArray(cached.items) && cached.items.length > 0) {
      const ageMin = Math.floor((Date.now() - (cached.at || 0)) / 60000);
      console.warn(`[Lusophone] ${src.name} all proxies failed (${attempts.join(' ')}); serving stale cache age=${ageMin}min`);
      return cached.items;
    }
  } catch {}

  console.warn(`[Lusophone] ${src.name} failed: ${attempts.join(' ')}`);
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
