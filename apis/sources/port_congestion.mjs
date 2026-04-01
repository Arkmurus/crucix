// apis/sources/port_congestion.mjs
// Port congestion, maritime chokepoints, and undersea cable outage monitoring
// Sources: PortWatch (IMF, free), gCaptain RSS, TeleGeography cable map RSS,
//          NetBlocks (internet outages), UNCTAD maritime stats
// No API keys required for core functionality

const RSS2JSON = 'https://api.rss2json.com/v1/api.json?rss_url=';

// IMF PortWatch — ArcGIS Online hosted service (imf-dataviz.maps.arcgis.com)
// The public REST endpoint requires an ArcGIS Online token; disabled for now.
// Port congestion intelligence is covered by the maritime news feeds below.
const PORTWATCH_APIS = [];

// Key ports to monitor (UNLOCODE → display name)
const CRITICAL_PORTS = [
  { code: 'CNSHA', name: 'Shanghai',       country: 'China',         strategic: 'largest container port' },
  { code: 'SGSIN', name: 'Singapore',      country: 'Singapore',     strategic: 'Malacca choke' },
  { code: 'NLRTM', name: 'Rotterdam',      country: 'Netherlands',   strategic: 'Europe gateway' },
  { code: 'USNYC', name: 'New York/NJ',    country: 'United States', strategic: 'US East Coast' },
  { code: 'USLAX', name: 'Los Angeles',    country: 'United States', strategic: 'US West Coast' },
  { code: 'AEDXB', name: 'Dubai (Jebel Ali)', country: 'UAE',        strategic: 'Persian Gulf hub' },
  { code: 'EGHAK', name: 'Sokhna (Egypt)', country: 'Egypt',         strategic: 'Suez Canal entrance' },
  { code: 'DJJIB', name: 'Djibouti',       country: 'Djibouti',      strategic: 'Bab el-Mandeb' },
  { code: 'GRPIR', name: 'Piraeus',        country: 'Greece',        strategic: 'Mediterranean hub' },
  { code: 'BRDOS', name: 'Santos',         country: 'Brazil',        strategic: 'Brazil-Lusophone hub' },
  { code: 'AOLAD', name: 'Luanda',         country: 'Angola',        strategic: 'Lusophone oil hub' },
];

// Maritime and cable news sources
const NEWS_SOURCES = [
  { url: 'https://gcaptain.com/feed/',                                         label: 'gCaptain',              type: 'maritime' },
  { url: 'https://www.hellenicshippingnews.com/feed/',                         label: 'Hellenic Shipping',     type: 'maritime' },
  { url: 'https://splash247.com/feed/',                                         label: 'Splash247',             type: 'maritime' },
  { url: 'https://www.maritime-executive.com/feed',                            label: 'Maritime Executive',    type: 'maritime' },
  { url: 'https://www.tradewindsnews.com/rss',                                 label: 'TradeWinds',            type: 'maritime' },
  { url: 'https://www.seatrade-maritime.com/rss/xml',                          label: 'Seatrade Maritime',     type: 'maritime' },
  { url: 'https://www.porttechnology.org/feed/',                               label: 'Port Technology',       type: 'port'     },
  { url: 'https://www.submarinecablemap.com/blog/feed.xml',                    label: 'TeleGeography Cables',  type: 'cable'    },
  { url: 'https://netblocks.org/feed',                                          label: 'NetBlocks',             type: 'internet' },
  { url: 'https://news.un.org/feed/subscribe/en/news/topic/humanitarian-aid/feed/rss.xml', label: 'UN Humanitarian', type: 'maritime' },
];

// Keywords for congestion/disruption detection
const DISRUPTION_KEYWORDS = [
  'congestion', 'delays', 'backlog', 'waiting time', 'anchorage',
  'disruption', 'closure', 'blockage', 'grounded', 'collision',
  'houthi', 'attack', 'mine', 'piracy', 'seizure',
  'cable cut', 'cable fault', 'outage', 'internet disruption',
  'divert', 'reroute', 'capacity', 'port strike', 'labour',
];

// ── IMF PortWatch: vessel call stats at critical ports ───────────────────────
async function fetchPortWatch() {
  // ArcGIS token required — endpoints disabled until a free alternative is configured.
  if (PORTWATCH_APIS.length === 0) return [];

  const stats = [];
  const params = new URLSearchParams({
    where:          `portid IN ('${CRITICAL_PORTS.map(p => p.code).join("','")}')`,
    outFields:      'portid,portname,vessel_calls_7d,vessel_calls_7d_prev,pct_change',
    returnGeometry: 'false',
    f:              'json',
  });

  let res = null;
  for (const apiUrl of PORTWATCH_APIS) {
    try {
      const r = await fetch(`${apiUrl}?${params}`, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
        signal: AbortSignal.timeout(8000),
      });
      if (r.ok) { res = r; break; }
    } catch {}
  }

  try {
    if (!res) throw new Error('All PortWatch endpoints failed');
    const data     = await res.json();
    const features = data.features || [];

    for (const f of features) {
      const attr   = f.attributes || {};
      const port   = CRITICAL_PORTS.find(p => p.code === attr.portid) || {};
      const calls  = attr.vessel_calls_7d    || 0;
      const prev   = attr.vessel_calls_7d_prev || 0;
      const pct    = prev ? ((calls - prev) / prev) * 100 : 0;

      stats.push({
        port:      port.name || attr.portname || attr.portid,
        country:   port.country || '',
        strategic: port.strategic || '',
        calls7d:   calls,
        prev7d:    prev,
        pctChange: Math.round(pct * 10) / 10,
        congested: pct < -15, // >15% drop = likely congestion/disruption
      });
    }
  } catch (err) {
    console.warn('[PortWatch] Failed:', err.message);
  }
  return stats;
}

// ── News: maritime disruption and cable outage headlines ─────────────────────
async function fetchDisruptionNews() {
  const items = [];
  await Promise.allSettled(
    NEWS_SOURCES.map(async src => {
      let articles = [];
      try {
        const res = await fetch(src.url, {
          headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
          signal: AbortSignal.timeout(7000),
        });
        if (res.ok) articles = parseRSS(await res.text());
      } catch {}

      if (articles.length === 0) {
        try {
          const res  = await fetch(RSS2JSON + encodeURIComponent(src.url), { signal: AbortSignal.timeout(9000) });
          const data = await res.json();
          if (data.status === 'ok' && data.items?.length) {
            articles = data.items.slice(0, 8).map(i => ({
              title:   i.title || '',
              url:     i.link  || '',
              summary: (i.description || '').replace(/<[^>]+>/g, '').substring(0, 200),
              pubDate: i.pubDate || '',
            }));
          }
        } catch {}
      }

      for (const a of articles) {
        const text = (a.title + ' ' + a.summary).toLowerCase();
        if (DISRUPTION_KEYWORDS.some(kw => text.includes(kw))) {
          items.push({ ...a, source: src.label, type: src.type });
        }
      }
    })
  );
  return items;
}

// ── Main briefing ─────────────────────────────────────────────────────────────
export async function briefing() {
  console.log('[PortCongestion] Fetching port and cable intelligence...');

  const [portStats, news] = await Promise.allSettled([
    fetchPortWatch(),
    fetchDisruptionNews(),
  ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : []));

  const congestedPorts = portStats.filter(p => p.congested);
  const cableNews      = news.filter(n => n.type === 'cable');
  const maritimeNews   = news.filter(n => n.type !== 'cable');

  const updates = [
    ...congestedPorts.map(p => ({
      title:    `Port Congestion: ${p.port} (${p.country}) — vessel calls ${p.pctChange > 0 ? '+' : ''}${p.pctChange}% WoW`,
      source:   'IMF PortWatch',
      type:     'port_congestion',
      priority: Math.abs(p.pctChange) > 25 ? 'critical' : 'high',
      strategic: p.strategic,
    })),
    ...news.slice(0, 15).map(n => ({
      title:    n.title,
      source:   n.source,
      url:      n.url,
      summary:  n.summary,
      type:     n.type === 'cable' ? 'cable_outage' : 'maritime_disruption',
      priority: n.type === 'cable' ? 'high' : 'medium',
    })),
  ];

  const signals = [
    ...congestedPorts.map(p => `Port disruption: ${p.port} vessel calls ${p.pctChange}% WoW (${p.strategic})`),
    ...cableNews.map(n => `Cable/internet: ${n.title}`),
  ];

  console.log(`[PortCongestion] ${portStats.length} ports · ${congestedPorts.length} congested · ${cableNews.length} cable events · ${maritimeNews.length} maritime news`);

  return {
    source:    'Port Congestion & Cable Intelligence',
    timestamp: new Date().toISOString(),
    updates,
    signals,
    portStats,
    congestedPorts,
    cableEvents: cableNews,
    counts: {
      portsMonitored: portStats.length,
      congestedPorts: congestedPorts.length,
      cableEvents:    cableNews.length,
      maritimeNews:   maritimeNews.length,
    },
  };
}

// ── RSS parser ────────────────────────────────────────────────────────────────
function parseRSS(xml) {
  const items = [];
  const re    = /<item[^>]*>([\s\S]*?)<\/item>/gi;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const b = m[1];
    const t = extractTag(b, 'title');
    if (!t) continue;
    items.push({
      title:   t,
      url:     extractTag(b, 'link') || extractTag(b, 'guid'),
      summary: extractTag(b, 'description').replace(/<[^>]+>/g, '').substring(0, 200),
      pubDate: extractTag(b, 'pubDate'),
    });
  }
  return items.slice(0, 10);
}

function extractTag(xml, tag) {
  const m = xml.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, 'i'))
          || xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  return m ? m[1].replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').trim() : '';
}

// CLI test
if (process.argv[1]?.endsWith('port_congestion.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
