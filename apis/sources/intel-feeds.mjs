// apis/sources/intel-feeds.mjs
// New intelligence sources: UN Security Council, BIS, think tanks, UN Comtrade
// All free, no API keys required

// ── UN Security Council RSS ──────────────────────────────────────────────────
export async function fetchUNSecurityCouncil() {
  const results = { updates: [], error: null };
  const feeds = [
    { url: 'https://www.un.org/press/en/rss.xml',              label: 'UN Press' },
    { url: 'https://www.un.org/securitycouncil/content/rss',   label: 'UN SC' },
    { url: 'https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml', label: 'UN Peace & Security' },
  ];

  for (const feed of feeds) {
    try {
      const res  = await fetch(feed.url, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
        signal: AbortSignal.timeout(10000)
      });
      if (!res.ok) continue;
      const xml  = await res.text();
      const items = parseRSS(xml);
      for (const item of items.slice(0, 8)) {
        results.updates.push({ ...item, source: feed.label, type: 'un_sc' });
      }
    } catch (e) {
      console.warn(`[UN SC] ${feed.label} failed:`, e.message);
    }
  }

  console.log(`[UN SC] ${results.updates.length} items`);
  return results;
}

// ── BIS / Central Bank feeds ─────────────────────────────────────────────────
export async function fetchCentralBanks() {
  const results = { updates: [], error: null };
  const feeds = [
    { url: 'https://www.bis.org/rss/mktc.rss',                             label: 'BIS Markets' },
    { url: 'https://www.bis.org/rss/work.rss',                             label: 'BIS Research' },
    { url: 'https://www.federalreserve.gov/feeds/press_all.xml',           label: 'Federal Reserve' },
    { url: 'https://www.ecb.europa.eu/rss/press.html',                     label: 'ECB' },
    { url: 'https://www.bankofengland.co.uk/rss/news',                     label: 'Bank of England' },
    { url: 'https://www.imf.org/en/News/rss?language=eng',                 label: 'IMF' },
    { url: 'https://www.worldbank.org/en/news/all?format=rss',             label: 'World Bank' },
  ];

  for (const feed of feeds) {
    try {
      const res  = await fetch(feed.url, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
        signal: AbortSignal.timeout(10000)
      });
      if (!res.ok) continue;
      const xml  = await res.text();
      const items = parseRSS(xml);
      for (const item of items.slice(0, 5)) {
        results.updates.push({ ...item, source: feed.label, type: 'central_bank' });
      }
    } catch (e) {
      console.warn(`[CentralBanks] ${feed.label} failed:`, e.message);
    }
  }

  console.log(`[Central Banks] ${results.updates.length} items`);
  return results;
}

// ── Think tank / analytical feeds ────────────────────────────────────────────
export async function fetchThinkTanks() {
  const results = { updates: [], error: null };
  const feeds = [
    { url: 'https://www.rand.org/pubs/rss/recent.xml',                      label: 'RAND' },
    { url: 'https://www.chathamhouse.org/rss.xml',                          label: 'Chatham House' },
    { url: 'https://www.iiss.org/rss-feeds/the-military-balance.xml',       label: 'IISS Military Balance' },
    { url: 'https://www.brookings.edu/topic/foreign-policy/feed/',          label: 'Brookings FP' },
    { url: 'https://carnegieendowment.org/rss/solr/articles?q=&lang=en',   label: 'Carnegie' },
    { url: 'https://www.wilsoncenter.org/rss.xml',                          label: 'Wilson Center' },
    { url: 'https://www.crisisgroup.org/rss.xml',                           label: 'Crisis Group' },
    { url: 'https://www.sipri.org/rss/news',                                label: 'SIPRI News' },
  ];

  for (const feed of feeds) {
    try {
      const res  = await fetch(feed.url, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
        signal: AbortSignal.timeout(10000)
      });
      if (!res.ok) continue;
      const xml  = await res.text();
      const items = parseRSS(xml);
      for (const item of items.slice(0, 4)) {
        results.updates.push({ ...item, source: feed.label, type: 'think_tank' });
      }
    } catch (e) {
      console.warn(`[ThinkTanks] ${feed.label} failed:`, e.message);
    }
  }

  console.log(`[Think Tanks] ${results.updates.length} items`);
  return results;
}

// ── UN Comtrade — trade flow anomaly detection ────────────────────────────────
// Monitors dual-use commodity flows between key country pairs
// Free API: https://comtradeapi.un.org/
export async function fetchTradeFLows() {
  const results = { flows: [], anomalies: [], error: null };

  // Dual-use commodity codes to monitor
  const WATCH_COMMODITIES = [
    { code: '854231', label: 'Semiconductors' },
    { code: '280469', label: 'Radioactive materials' },
    { code: '381800', label: 'Chemical precursors' },
    { code: '720299', label: 'Ferro-alloys (rare earths)' },
    { code: '854140', label: 'Photosensitive devices / lasers' },
  ];

  // Key reporter countries
  const REPORTERS = ['156', '840', '643', '364']; // China, US, Russia, Iran

  try {
    for (const commodity of WATCH_COMMODITIES.slice(0, 2)) { // limit API calls
      const params = new URLSearchParams({
        reporterCode: REPORTERS.join(','),
        period:       getPreviousMonth(),
        cmdCode:      commodity.code,
        flowCode:     'X,M',
        fmt:          'json',
        max:          '20',
      });

      const res = await fetch(
        `https://comtradeapi.un.org/public/v1/preview/C/A/HS?${params}`,
        {
          headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
          signal: AbortSignal.timeout(15000)
        }
      );

      if (!res.ok) continue;
      const data = await res.json();
      const rows = data.data || [];

      for (const row of rows) {
        results.flows.push({
          commodity:    commodity.label,
          code:         commodity.code,
          reporter:     row.reporterDesc || '',
          partner:      row.partnerDesc  || '',
          flow:         row.flowDesc     || '',
          value_usd:    row.primaryValue || 0,
          period:       row.period       || '',
          type:         'trade_flow'
        });
      }
    }

    console.log(`[Comtrade] ${results.flows.length} trade flow records`);
  } catch (err) {
    results.error = err.message;
    console.error('[Comtrade] Error:', err.message);
  }

  return results;
}

// ── RSS parser (lightweight, no dependencies) ─────────────────────────────────
function parseRSS(xml) {
  const items = [];
  const itemRegex = /<item[^>]*>([\s\S]*?)<\/item>/gi;
  let match;

  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    const title   = extractTag(block, 'title');
    const link    = extractTag(block, 'link') || extractTag(block, 'guid');
    const pubDate = extractTag(block, 'pubDate') || extractTag(block, 'dc:date');
    const desc    = extractTag(block, 'description');

    if (title) {
      items.push({
        title:     cleanText(title),
        url:       cleanText(link),
        pubDate:   pubDate ? new Date(pubDate).toISOString() : new Date().toISOString(),
        summary:   cleanText(desc).substring(0, 200),
      });
    }
  }

  return items;
}

function extractTag(xml, tag) {
  const match = xml.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, 'i'))
             || xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  return match ? match[1].trim() : '';
}

function cleanText(str) {
  return str.replace(/<[^>]+>/g, '').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").trim();
}

function getPreviousMonth() {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}`;
}
