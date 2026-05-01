// apis/sources/procurement_portals.mjs
// Direct monitoring of government procurement portals from TARGET_MARKETS
// Strategy per portal:
//   1. Try direct RSS feed (portal/rss, /feed, /atom)
//   2. Try site:domain Google News search (finds indexed portal content)
//   3. Fallback: country-specific Google News procurement query
//
// Arkmurus focus: surface live tenders before they appear in secondary news

import '../utils/env.mjs';
import { TARGET_MARKETS } from '../../lib/self/opportunity_engine.mjs';
import { withConcurrency, shouldSkip, recordFailure, recordSuccess } from '../../lib/util/throttle.mjs';

const RSS2JSON   = 'https://api.rss2json.com/v1/api.json?rss_url=';
const ALLORIGINS = 'https://api.allorigins.win/get?url=';

// Markets worth portal-monitoring (skip very-low-priority + no portal)
const PRIORITY_MARKETS = TARGET_MARKETS.filter(m =>
  m.procurementPortal && m.priority !== 'LOW'
);

// Defense-specific keywords for scoring
const DEFENSE_KW = [
  'defense', 'defence', 'military', 'security', 'arms', 'weapon', 'ammunition',
  'munition', 'aircraft', 'helicopter', 'vessel', 'patrol', 'armor', 'armour',
  'armoured', 'armored', 'vehicle', 'radar', 'drone', 'uav', 'missile', 'frigate',
  'corvette', 'coast guard', 'gendarmerie', 'police', 'infantry', 'special forces',
  'surveillance', 'intelligence', 'communications', 'c4i', 'c4isr', 'isr',
];
const PROC_KW = [
  'tender', 'procurement', 'contract', 'rfp', 'rfq', 'bid', 'solicitation',
  'acquisition', 'purchase', 'supply', 'services', 'licitação', 'licitacion',
  'adjudicação', 'concurso', 'appel d\'offres', 'marché', 'appel offre',
];
const LUSOPHONE_KW = ['angola', 'mozambique', 'guinea-bissau', 'cape verde', 'brazil', 'lusophone', 'cplp'];

function scoreItem(title, desc, market) {
  const text = `${title} ${desc}`.toLowerCase();
  let s = 0;
  if (market.lusophone)                                         s += 10;
  if (market.priority === 'HIGH')                               s += 5;
  for (const kw of DEFENSE_KW) if (text.includes(kw))          s += 4;
  for (const kw of PROC_KW)    if (text.includes(kw))          s += 3;
  for (const kw of LUSOPHONE_KW) if (text.includes(kw))        s += 8;
  return s;
}

function decodeEntities(str) {
  return str
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&apos;/g, "'")
    .replace(/&nbsp;/g, ' ');
}

function cleanText(raw) {
  return decodeEntities(raw || '')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function parseXML(xml, sourceName) {
  const items = [];
  const isAtom = xml.includes('<entry>');
  const tag    = isAtom ? 'entry' : 'item';
  const re     = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'gi');
  let m;
  while ((m = re.exec(xml)) !== null) {
    const b     = m[1];
    const title = cleanText(b.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i)?.[1] || '');
    const link  = b.match(/<link[^>]*href="([^"]+)"/i)?.[1]
               || b.match(/<link[^>]*>([\s\S]*?)<\/link>/i)?.[1]?.trim() || '';
    const date  = b.match(/<pubDate>([\s\S]*?)<\/pubDate>/i)?.[1]
               || b.match(/<published>([\s\S]*?)<\/published>/i)?.[1] || '';
    const rawDesc = b.match(/<description[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/description>/i)?.[1]
                 || b.match(/<summary[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/summary>/i)?.[1] || '';
    const desc  = cleanText(rawDesc).slice(0, 300);
    if (title.length > 5) {
      items.push({ title, description: desc, url: link.trim(), pubDate: date, source: sourceName });
    }
    if (items.length >= 12) break;
  }
  return items;
}

async function tryFetch(url, timeout = 10000) {
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36' },
      signal: AbortSignal.timeout(timeout),
    });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

// Try RSS-like paths on a portal domain
async function tryPortalRSS(portalUrl, sourceName) {
  let base;
  try { base = new URL(portalUrl).origin; } catch { return []; }

  const rssAttempts = [
    `${base}/feed`,
    `${base}/rss`,
    `${base}/rss.xml`,
    `${base}/feed.xml`,
    `${base}/atom.xml`,
    `${base}/tenders/rss`,
    `${base}/procurement/rss`,
    `${base}/licitacoes/feed`,
    `${base}/appels-doffres/feed`,
  ];

  for (const rssUrl of rssAttempts) {
    // Try directly
    let text = await tryFetch(rssUrl, 8000);
    // Try through allorigins if direct fails
    if (!text) {
      text = await tryFetch(`${ALLORIGINS}${encodeURIComponent(rssUrl)}`, 12000);
      if (text) {
        try { const j = JSON.parse(text); text = j.contents || null; } catch { text = null; }
      }
    }
    if (text && (text.includes('<item>') || text.includes('<entry>'))) {
      const items = parseXML(text, sourceName);
      if (items.length > 0) return items;
    }
  }
  return [];
}

// Google News site: search for a specific portal domain
async function tryGoogleNewsSite(portalUrl, market) {
  let domain;
  try { domain = new URL(portalUrl).hostname.replace(/^www\./, ''); } catch { return []; }

  const sourceName = `${market.name} Gov Portal`;
  const query = `site:${domain} procurement OR tender OR contract OR defence OR military`;
  const gnUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en&gl=${market.iso2}&ceid=${market.iso2}:en`;

  const text = await tryFetch(gnUrl, 12000);
  if (text && (text.includes('<item>') || text.includes('<entry>'))) {
    return parseXML(text, sourceName).slice(0, 8);
  }

  // Fallback through rss2json
  try {
    const r2j = await tryFetch(`${RSS2JSON}${encodeURIComponent(gnUrl)}`, 14000);
    if (r2j) {
      const data = JSON.parse(r2j);
      if (data.status === 'ok' && data.items?.length) {
        return data.items.slice(0, 8).map(i => ({
          source:      sourceName,
          title:       cleanText(i.title || ''),
          description: cleanText(i.description || i.content || '').slice(0, 300),
          url:         i.link || '',
          pubDate:     i.pubDate || '',
        }));
      }
    }
  } catch {}
  return [];
}

// Google News country-level procurement query (broadest fallback)
async function tryGoogleNewsCountry(market) {
  const sourceName = `${market.name} Procurement News`;
  const query = `"${market.name}" defence OR defense procurement tender contract 2026`;
  const gnUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en&gl=${market.iso2}&ceid=${market.iso2}:en`;

  const text = await tryFetch(gnUrl, 12000);
  if (text && (text.includes('<item>') || text.includes('<entry>'))) {
    return parseXML(text, sourceName).slice(0, 6);
  }
  return [];
}

// Monitor a single market — try portal RSS → site: search → country news
async function monitorMarket(market) {
  const sourceName = `${market.name} Portal`;
  let items = [];

  // Step 1: direct portal RSS
  try {
    items = await tryPortalRSS(market.procurementPortal, sourceName);
    if (items.length > 0) {
      console.log(`[Portals] ${market.name}: ${items.length} items via portal RSS`);
      return { market, items, method: 'portal_rss' };
    }
  } catch {}

  // Step 2: Google News site: search
  try {
    items = await tryGoogleNewsSite(market.procurementPortal, market);
    if (items.length > 0) {
      console.log(`[Portals] ${market.name}: ${items.length} items via site: search`);
      return { market, items, method: 'google_site' };
    }
  } catch {}

  // Step 3: Google News country query
  try {
    items = await tryGoogleNewsCountry(market);
    if (items.length > 0) {
      console.log(`[Portals] ${market.name}: ${items.length} items via country news`);
      return { market, items, method: 'google_country' };
    }
  } catch {}

  console.log(`[Portals] ${market.name}: no items found`);
  return { market, items: [], method: 'none' };
}

// ── Main export ───────────────────────────────────────────────────────────────
export async function briefing() {
  console.log(`[Portals] Monitoring ${PRIORITY_MARKETS.length} government procurement portals...`);

  // R-F21 2026-05-01: cap market-fetch concurrency at 6 instead of
  // firing all 28 in parallel. Previous burst (28 markets × up to 3
  // sequential RSS fallbacks each = 84 outbound calls in flight)
  // triggered seenode connection-pool exhaustion on 09:39 — only
  // 4/28 markets covered before the 25s cap. With concurrency=6 each
  // market still gets its own 3-attempt chain but the wave is smaller.
  // Per-market circuit breaker skips a country after 3 consecutive
  // "no items" returns for 5 minutes.
  //
  // Construction: build N deferred slots (one per market) up front so
  // the outer Promise.race / Promise.allSettled call below sees a fixed
  // promise array with one entry per market. Worker pool of 6 pulls
  // the next index, runs monitorMarket, resolves that slot.
  const marketPromises = PRIORITY_MARKETS.map(() => {
    let resolve;
    const p = new Promise((r) => { resolve = r; });
    p._resolve = resolve;
    return p;
  });
  let nextIdx = 0;
  const concurrency = 6;
  async function portalWorker() {
    while (true) {
      const i = nextIdx++;
      if (i >= PRIORITY_MARKETS.length) return;
      const market = PRIORITY_MARKETS[i];
      const ckey = `portals:${market.name}`;
      let result;
      if (shouldSkip(ckey)) {
        result = { market, items: [], method: 'circuit_open' };
      } else {
        try {
          result = await monitorMarket(market);
          if (result && Array.isArray(result.items) && result.items.length > 0) {
            recordSuccess(ckey);
          } else {
            recordFailure(ckey);
          }
        } catch (err) {
          recordFailure(ckey);
          result = { market, items: [], method: 'error', error: err?.message };
        }
      }
      marketPromises[i]._resolve(result);
    }
  }
  for (let w = 0; w < concurrency; w++) portalWorker();

  const timeoutPromise = new Promise(resolve =>
    setTimeout(() => resolve('TIMEOUT'), 25000)
  );

  const raceResult = await Promise.race([
    Promise.allSettled(marketPromises),
    timeoutPromise,
  ]);

  // If timeout hit, collect whatever has resolved so far
  let results;
  if (raceResult === 'TIMEOUT') {
    console.log('[Portals] 25s internal timeout — collecting partial results');
    results = await Promise.allSettled(
      marketPromises.map(p => Promise.race([p, new Promise(resolve => setTimeout(() => resolve({ market: { name: 'timeout' }, items: [], method: 'none' }), 100))]))
    );
  } else {
    results = raceResult;
  }

  const allItems = [];
  const sourceStatus = {};
  const marketCoverage = {};

  for (const result of results) {
    if (result.status !== 'fulfilled') continue;
    const { market, items, method } = result.value;

    sourceStatus[market.name] = method !== 'none' ? 'ok' : 'failed';
    marketCoverage[market.name] = { count: items.length, method };

    for (const item of items) {
      const rel = scoreItem(item.title, item.description, market);
      allItems.push({
        ...item,
        market:    market.name,
        iso2:      market.iso2,
        lusophone: market.lusophone,
        priority:  market.priority,
        portalUrl: market.procurementPortal,
        relevanceScore: rel,
      });
    }
  }

  // Deduplicate and sort
  const seen   = new Set();
  const unique = [];
  for (const item of allItems.sort((a, b) => b.relevanceScore - a.relevanceScore)) {
    const key = item.title.toLowerCase().slice(0, 60);
    if (!seen.has(key)) { seen.add(key); unique.push(item); }
  }

  const top      = unique.slice(0, 40);
  const lusophone = top.filter(i => i.lusophone);
  const highPri  = top.filter(i => i.priority === 'HIGH' && !i.lusophone);
  const okCount  = Object.values(sourceStatus).filter(s => s === 'ok').length;

  console.log(`[Portals] ${top.length} total items · ${lusophone.length} Lusophone · ${okCount}/${PRIORITY_MARKETS.length} markets covered`);

  // Format updates for briefing pipeline
  const updates = top.map(i => ({
    title:     `[${i.market}] ${i.title}`,
    source:    i.source,
    content:   i.description,
    url:       i.url,
    portalUrl: i.portalUrl,
    market:    i.market,
    timestamp: i.pubDate ? new Date(i.pubDate).getTime() || Date.now() : Date.now(),
    priority:  i.relevanceScore >= 20 ? 'high' : i.relevanceScore >= 10 ? 'medium' : 'normal',
    type:      'portal_tender',
  }));

  // Signals for correlation engine
  const signals = [
    ...lusophone.slice(0, 3).map(i => ({
      text:     `[Portal/Lusophone/${i.market}] ${i.title.slice(0, 120)}`,
      source:   i.source,
      priority: 'high',
    })),
    ...highPri.slice(0, 3).map(i => ({
      text:     `[Portal/HighPri/${i.market}] ${i.title.slice(0, 120)}`,
      source:   i.source,
      priority: 'medium',
    })),
  ];

  // Full item records for BD intelligence
  const lusiItems = lusophone.map(i => ({
    title:     i.title,
    content:   i.description,
    text:      i.description,
    source:    i.source,
    url:       i.url || i.portalUrl,
    link:      i.url || i.portalUrl,
    market:    i.market,
    timestamp: i.pubDate ? new Date(i.pubDate).getTime() || Date.now() : Date.now(),
  }));

  return {
    source:    'Government Procurement Portals',
    timestamp: new Date().toISOString(),
    updates,
    signals,
    lusophone: lusiItems,
    sourceStatus,
    marketCoverage,
    counts: {
      total:     top.length,
      lusophone: lusophone.length,
      highPriority: highPri.length,
      marketsOk: okCount,
      marketsTotal: PRIORITY_MARKETS.length,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('procurement_portals.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify({ counts: data.counts, marketCoverage: data.marketCoverage }, null, 2));
}
