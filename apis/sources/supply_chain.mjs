// Supply Chain Intelligence — Live data from commodity markets, shipping, and news
// Sources: Yahoo Finance (metals/freight futures), maritime RSS feeds, defense supply news
// No API keys required

import { safeFetch } from '../utils/fetch.mjs';

const YF_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart';

// Critical materials tracked via Yahoo Finance
const COMMODITY_SYMBOLS = {
  'HG=F':  { name: 'Copper',             unit: '/lb',  impact: 'Electronics, wiring, guidance systems',   threshold: 5  },
  'GC=F':  { name: 'Gold',               unit: '/oz',  impact: 'Reserve assets, electronics',             threshold: 3  },
  'PL=F':  { name: 'Platinum',           unit: '/oz',  impact: 'Catalysts, sensors, fuel cells',          threshold: 5  },
  'PA=F':  { name: 'Palladium',          unit: '/oz',  impact: 'Electronics, catalytic converters',       threshold: 8  },
  'LIT':   { name: 'Lithium (ETF)',      unit: 'USD',  impact: 'Batteries, drones, EVs, grid storage',    threshold: 8  },
  'REMX':  { name: 'Rare Earths (ETF)',  unit: 'USD',  impact: 'Magnets, guidance systems, radar, motors',threshold: 8  },
  'URA':   { name: 'Uranium (ETF)',      unit: 'USD',  impact: 'Nuclear energy, naval propulsion',        threshold: 10 },
  'ALB':   { name: 'Albemarle (Li proxy)',unit: 'USD', impact: 'Lithium production — EV/battery supply',  threshold: 8  },
  'MP':    { name: 'MP Materials (REE)', unit: 'USD',  impact: 'US rare earth supply chain',              threshold: 8  },
  '^BDI':  { name: 'Baltic Dry Index',   unit: 'pts',  impact: 'Global bulk shipping costs',              threshold: 5  },
};

// IMF Primary Commodity Prices — DataMapper API (free, no key required)
// These codes are IMF codes, not World Bank codes (WB uses dot-notation like SP.POP.TOTL)
const IMF_COMMODITIES = [
  { id: 'PNICK',  name: 'Nickel',           unit: '$/mt',    impact: 'EV batteries, stainless steel, armor plating',      threshold: 8  },
  { id: 'PCOBA',  name: 'Cobalt',            unit: '$/mt',    impact: 'Lithium-ion batteries, jet engines, superalloys',    threshold: 10 },
  { id: 'PTIN',   name: 'Tin',               unit: '$/mt',    impact: 'Electronics solder, aerospace components',           threshold: 8  },
  { id: 'PZINC',  name: 'Zinc',              unit: '$/mt',    impact: 'Brass casings, galvanizing, anti-corrosion',         threshold: 7  },
  { id: 'PALUM',  name: 'Aluminum',          unit: '$/mt',    impact: 'Aircraft, vehicles, missiles, packaging',            threshold: 6  },
  { id: 'PUREA',  name: 'Urea (AN proxy)',   unit: '$/mt',    impact: 'Ammonium nitrate precursor — ANFO, fertilizer bombs', threshold: 12 },
];

// ── IMF Primary Commodity Prices fetch ───────────────────────────────────────
async function fetchIMFCommodity(indicator) {
  try {
    // IMF DataMapper API — returns annual average prices in USD
    const url = `https://www.imf.org/external/datamapper/api/v1/${indicator.id}`;
    const res = await fetch(url, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0', 'Accept': 'application/json' },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) throw new Error(`IMF API ${res.status}`);
    const data = await res.json();

    // Response: { values: { PNICK: { WLD: { "2022": 25626 } } } }
    // Country key varies — try WLD first, then W00, then first available key
    const byIndicator = data?.values?.[indicator.id];
    if (!byIndicator) {
      console.warn(`[IMF] No data for ${indicator.id} — keys: ${JSON.stringify(Object.keys(data || {}))}`);
      return null;
    }
    const series = byIndicator.WLD || byIndicator.W00 || byIndicator[Object.keys(byIndicator)[0]];
    if (!series) return null;

    const years = Object.keys(series).filter(y => series[y] != null).sort();
    if (years.length < 1) return null;

    const latestYear = years[years.length - 1];
    const prevYear   = years[years.length - 2] || null;
    const latest     = series[latestYear];
    const prev       = prevYear ? series[prevYear] : null;
    const pct        = prev ? ((latest - prev) / prev) * 100 : 0;

    return {
      symbol:    indicator.id,
      name:      indicator.name,
      price:     Math.round(latest * 100) / 100,
      prevValue: prev ? Math.round(prev * 100) / 100 : null,
      changePct: Math.round(pct * 100) / 100,
      unit:      indicator.unit,
      impact:    indicator.impact,
      period:    latestYear,
      alert:     Math.abs(pct) >= indicator.threshold,
      source:    'IMF Commodities',
    };
  } catch (e) {
    console.warn(`[IMF] ${indicator.id} failed: ${e.message}`);
    return null;
  }
}

// ── Explosive precursor & defense munitions news keywords ─────────────────────
const EXPLOSIVE_KEYWORDS = [
  'ammonium nitrate', 'ammonium perchlorate', 'nitrocellulose', 'rdx', 'hmx', 'tnt',
  'propellant', 'explosive', 'warhead', 'detonator', 'nitric acid',
  'ammunition shortage', 'ammo production', 'shell production', 'artillery round',
  'solid rocket', 'rocket motor', 'energetic material',
  'c-4', 'semtex', 'octogen', 'hexogen',
];

// Maritime chokepoints — cross-referenced against shipping news keywords
const CHOKEPOINTS = [
  { name: 'Strait of Hormuz',   keywords: ['hormuz', 'persian gulf', 'iran tanker'],          impact: 'Middle East oil & LNG — 20% of global supply' },
  { name: 'Bab el-Mandeb',      keywords: ['bab el-mandeb', 'houthi', 'red sea', 'yemen'],    impact: 'Red Sea — Europe/Asia route' },
  { name: 'Suez Canal',         keywords: ['suez', 'suez canal'],                             impact: 'Europe-Asia shortcut — 12% of global trade' },
  { name: 'Strait of Malacca',  keywords: ['malacca', 'singapore strait'],                    impact: 'Asia-Pacific — China/India/Middle East route' },
  { name: 'South China Sea',    keywords: ['south china sea', 'spratly', 'taiwan strait'],    impact: 'Asia-Pacific — $3.4T annual trade' },
  { name: 'Panama Canal',       keywords: ['panama canal', 'panama drought'],                 impact: 'Pacific-Atlantic link — US/Latin America trade' },
];

// Maritime and supply chain news sources — all free RSS
const NEWS_SOURCES = [
  { url: 'https://gcaptain.com/feed/',                                 label: 'gCaptain',           type: 'maritime' },
  { url: 'https://www.maritime-executive.com/feed',                    label: 'Maritime Executive', type: 'maritime' },
  { url: 'https://splash247.com/feed/',                                label: 'Splash247',          type: 'maritime' },
  { url: 'https://breakingdefense.com/feed/',                          label: 'Breaking Defense',   type: 'defense'  },
  { url: 'https://www.defensenews.com/rss/',                           label: 'Defense News',       type: 'defense'  },
  { url: 'https://www.reuters.com/rssFeed/technologyNews',             label: 'Reuters Tech',       type: 'tech'     },
];

// Supply chain alert keywords
const SUPPLY_KEYWORDS = [
  'shortage', 'shortage', 'supply chain', 'disruption', 'delay', 'backlog',
  'sanctions', 'export ban', 'export restriction', 'tariff', 'embargo',
  'semiconductor', 'chip', 'rare earth', 'lithium', 'cobalt', 'titanium',
  'ammunition', 'propellant', 'explosives', 'missile component',
  'port', 'shipping', 'freight', 'container', 'logistics',
];

// ── Fetch a Yahoo Finance quote ───────────────────────────────────────────────
async function fetchQuote(symbol) {
  try {
    const url = `${YF_BASE}/${encodeURIComponent(symbol)}?range=5d&interval=1d&includePrePost=false`;
    const data = await safeFetch(url, {
      timeout: 8000,
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' },
    });

    const result = data?.chart?.result?.[0];
    if (!result) return null;

    const meta   = result.meta || {};
    const closes = result.indicators?.quote?.[0]?.close || [];
    const price  = meta.regularMarketPrice ?? closes[closes.length - 1];
    const prev   = meta.chartPreviousClose ?? closes[closes.length - 2];
    const pct    = prev ? ((price - prev) / prev) * 100 : 0;
    const info   = COMMODITY_SYMBOLS[symbol];

    return {
      symbol,
      name:      info?.name || meta.shortName || symbol,
      price:     Math.round(price * 100) / 100,
      prevClose: Math.round((prev || 0) * 100) / 100,
      changePct: Math.round(pct * 100) / 100,
      unit:      info?.unit || 'USD',
      impact:    info?.impact || '',
      alert:     Math.abs(pct) >= (info?.threshold || 5),
    };
  } catch {
    return null;
  }
}

// ── Fetch RSS with rss2json proxy fallback ────────────────────────────────────
async function fetchFeed(src) {
  try {
    const res = await fetch(src.url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
      signal: AbortSignal.timeout(8000),
    });
    if (res.ok) {
      const items = parseRSS(await res.text());
      if (items.length > 0) return items;
    }
  } catch {}

  try {
    const proxy = 'https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(src.url);
    const res   = await fetch(proxy, { signal: AbortSignal.timeout(10000) });
    if (res.ok) {
      const data = await res.json();
      if (data.status === 'ok' && data.items?.length > 0) {
        return data.items.slice(0, 10).map(i => ({
          title:   i.title || '',
          url:     i.link  || '',
          summary: (i.description || i.content || '').replace(/<[^>]+>/g, '').substring(0, 200),
          pubDate: i.pubDate || new Date().toISOString(),
        }));
      }
    }
  } catch {}

  return [];
}

// ── Derive chokepoint risk from news headlines ────────────────────────────────
function deriveChokepoints(newsItems) {
  const allText = newsItems.map(n => (n.title + ' ' + n.summary).toLowerCase()).join(' ');
  return CHOKEPOINTS.map(cp => {
    const hits    = cp.keywords.filter(kw => allText.includes(kw));
    const mention = newsItems.filter(n =>
      cp.keywords.some(kw => (n.title + ' ' + n.summary).toLowerCase().includes(kw))
    ).slice(0, 2);
    return {
      name:     cp.name,
      impact:   cp.impact,
      severity: hits.length >= 3 ? 'critical' : hits.length >= 2 ? 'high' : hits.length >= 1 ? 'elevated' : 'normal',
      mentions: mention.map(m => m.title),
    };
  });
}

// ── Main briefing ─────────────────────────────────────────────────────────────
export async function briefing() {
  console.log('[Supply Chain] Fetching intelligence...');

  // 1. Commodity prices (parallel) — Yahoo Finance + IMF Primary Commodity Prices
  const [yQuotes, imfQuotes] = await Promise.all([
    Promise.allSettled(Object.keys(COMMODITY_SYMBOLS).map(s => fetchQuote(s)))
      .then(r => r.map(x => x.status === 'fulfilled' ? x.value : null).filter(Boolean)),
    Promise.allSettled(IMF_COMMODITIES.map(c => fetchIMFCommodity(c)))
      .then(r => r.map(x => x.status === 'fulfilled' ? x.value : null).filter(Boolean)),
  ]);
  const wbQuotes = imfQuotes; // keep variable name for downstream compatibility
  const quotes = [...yQuotes, ...imfQuotes];

  // 2. Maritime + defense news (parallel)
  const feedResults = await Promise.allSettled(
    NEWS_SOURCES.map(s => fetchFeed(s).then(items => items.map(i => ({ ...i, source: s.label, type: s.type }))))
  );
  const allNews = feedResults
    .filter(r => r.status === 'fulfilled')
    .flatMap(r => r.value)
    .filter(n => SUPPLY_KEYWORDS.some(kw => (n.title + ' ' + n.summary).toLowerCase().includes(kw)));

  // 2b. Explosive/munitions news
  const explosiveNews = feedResults
    .filter(r => r.status === 'fulfilled')
    .flatMap(r => r.value)
    .filter(n => EXPLOSIVE_KEYWORDS.some(kw => (n.title + ' ' + n.summary).toLowerCase().includes(kw)));

  // 3. Derive chokepoint risk from live news
  const chokepoints = deriveChokepoints(allNews);

  // 4. Build commodity alerts
  const commodityAlerts = quotes
    .filter(q => q.alert)
    .map(q => ({
      type:    Math.abs(q.changePct) >= 10 ? 'critical' : 'high',
      message: `${q.name} ${q.changePct > 0 ? '+' : ''}${q.changePct}% — ${q.impact}`,
      source:  'Yahoo Finance',
    }));

  const chokepointAlerts = chokepoints
    .filter(c => c.severity === 'critical' || c.severity === 'high')
    .map(c => ({
      type:    c.severity,
      message: `${c.name}: ${c.severity.toUpperCase()} — ${c.impact}`,
      source:  'Maritime Intelligence',
    }));

  const alerts = [...commodityAlerts, ...chokepointAlerts];

  // 5. Build updates from live news
  const updates = allNews.slice(0, 15).map(n => ({
    source:    n.source,
    title:     n.title,
    content:   n.summary,
    url:       n.url,
    timestamp: new Date(n.pubDate || Date.now()).getTime(),
    priority:  SUPPLY_KEYWORDS.slice(0, 5).some(kw => n.title.toLowerCase().includes(kw)) ? 'high' : 'normal',
  }));

  // 6. Build signals
  const signals = [
    ...commodityAlerts.map(a => a.message),
    ...chokepointAlerts.map(a => `${a.type === 'critical' ? '🚨' : '⚠️'} ${a.message}`),
  ];

  // 6b. Explosive precursor alerts
  const explosiveAlerts = quotes
    .filter(q => q.alert && (q.name.includes('Urea') || q.name.includes('Nitro') || q.name.includes('Ammonium')))
    .map(q => ({
      type: 'high',
      message: `${q.name} ${q.changePct > 0 ? '+' : ''}${q.changePct}% — explosive precursor price movement`,
      source: q.source || 'World Bank',
    }));

  if (explosiveNews.length > 0) {
    explosiveAlerts.push(...explosiveNews.slice(0, 5).map(n => ({
      type: 'medium',
      message: `Munitions: ${n.title}`,
      source: n.source,
      url: n.url,
    })));
  }

  alerts.push(...explosiveAlerts);

  const liveCount = quotes.filter(q => q.price > 0).length;
  console.log(`[Supply Chain] ${liveCount} live commodity prices (${imfQuotes.length} IMF) · ${allNews.length} supply news · ${explosiveNews.length} munitions · ${alerts.length} alerts`);

  return {
    source:    'Supply Chain Intelligence',
    timestamp: new Date().toISOString(),
    status:    'active',
    updates,
    signals,
    metrics: {
      rawMaterials: quotes.map(q => ({
        name:    q.name,
        price:   `${q.price}${q.unit}`,
        trend:   q.changePct > 1 ? 'up' : q.changePct < -1 ? 'down' : 'stable',
        change:  `${q.changePct > 0 ? '+' : ''}${q.changePct}%`,
        impact:  q.impact,
        risk:    q.alert ? (Math.abs(q.changePct) >= 10 ? 'critical' : 'high') : 'normal',
        live:    true,
        source:  q.source || 'Yahoo Finance',
        period:  q.period || null, // World Bank monthly period label
      })),
      munitions: {
        explosiveNews: explosiveNews.slice(0, 8).map(n => ({ title: n.title, source: n.source, url: n.url })),
        precursorPrices: wbQuotes.filter(q => q?.name?.includes('Urea') || q?.impact?.includes('nitrate')),
        note: 'Direct TNT/RDX/C4/HMX prices are not publicly traded — tracked via precursor materials (urea/ammonium nitrate) and defense procurement news',
      },
      logistics:    chokepoints,
      alerts,
      lastUpdated:  new Date().toISOString(),
      criticalRisks: alerts.filter(a => a.type === 'critical').length,
    },
    counts: {
      updates:       updates.length,
      signals:       signals.length,
      criticalAlerts: alerts.filter(a => a.type === 'critical').length,
      liveQuotes:    liveCount,
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
    const title = extractTag(b, 'title');
    if (!title) continue;
    items.push({
      title,
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
if (process.argv[1]?.endsWith('supply_chain.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
