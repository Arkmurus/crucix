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
    { url: 'https://www.rand.org/news/press.xml',                             label: 'RAND' },
    { url: 'https://www.chathamhouse.org/path/news-releases.xml',            label: 'Chatham House' },
    { url: 'https://www.iiss.org/rss-feeds/iiss-analysis.xml',              label: 'IISS' },
    { url: 'https://www.brookings.edu/feed/',                                label: 'Brookings' },
    { url: 'https://carnegieendowment.org/rss/solr/articles?q=&lang=en',    label: 'Carnegie' },
    { url: 'https://www.wilsoncenter.org/rss.xml',                           label: 'Wilson Center' },
    { url: 'https://www.crisisgroup.org/rss.xml',                            label: 'Crisis Group' },
    { url: 'https://www.sipri.org/rss/news',                                 label: 'SIPRI News' },
    { url: 'https://www.atlanticcouncil.org/feed/',                          label: 'Atlantic Council' },
    { url: 'https://www.csis.org/feed',                                      label: 'CSIS' },
    { url: 'https://rusi.org/feed',                                          label: 'RUSI' },
    { url: 'https://ecfr.eu/feed/',                                          label: 'ECFR' },
    { url: 'https://www.bellingcat.com/feed/',                               label: 'Bellingcat' },
    { url: 'https://www.cfr.org/rss/publications',                           label: 'CFR' },
  ];

  // Fetch all think tanks in parallel (was sequential — up to 7 min if all blocked)
  const fetchFeed = async (feed) => {
    let items = [];

    // Try direct fetch first
    try {
      const res = await fetch(feed.url, {
        headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' },
        signal: AbortSignal.timeout(6000),
      });
      if (res.ok) items = parseRSS(await res.text());
    } catch {}

    // Fallback 1: rss2json proxy
    if (items.length === 0) {
      try {
        const proxy = 'https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(feed.url);
        const res   = await fetch(proxy, { signal: AbortSignal.timeout(8000) });
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'ok' && data.items?.length > 0) {
            items = data.items.slice(0, 4).map(i => ({
              title:   i.title || '',
              url:     i.link  || '',
              pubDate: i.pubDate || new Date().toISOString(),
              summary: (i.description || i.content || '').replace(/<[^>]+>/g,'').substring(0, 200),
            }));
          }
        }
      } catch {}
    }

    // Fallback 2: allorigins.win proxy
    if (items.length === 0) {
      try {
        const proxy = 'https://api.allorigins.win/get?url=' + encodeURIComponent(feed.url);
        const res   = await fetch(proxy, { signal: AbortSignal.timeout(8000) });
        if (res.ok) {
          const data = await res.json();
          if (data.contents) items = parseRSS(data.contents).slice(0, 4);
        }
      } catch {}
    }

    return { feed, items };
  };

  const feedResults = await Promise.allSettled(feeds.map(fetchFeed));
  for (const r of feedResults) {
    if (r.status !== 'fulfilled') continue;
    const { feed, items } = r.value;
    if (items.length === 0) {
      console.warn(`[ThinkTanks] ${feed.label} failed: all attempts blocked`);
      continue;
    }
    for (const item of items.slice(0, 4)) {
      results.updates.push({ ...item, source: feed.label, type: 'think_tank' });
    }
  }

  console.log(`[Think Tanks] ${results.updates.length} items`);
  return results;
}

// ── IMF DOTS — bilateral trade flow monitoring ────────────────────────────────
// IMF Direction of Trade Statistics — free, no API key required
// Replaces UN Comtrade (now requires paid subscription)
// Monitors trade between key geopolitical country pairs for anomaly detection
export async function fetchTradeFLows() {
  const results = { flows: [], anomalies: [], error: null };

  // Country pairs to monitor (ISO2 codes for IMF DOTS)
  // Focus: sanctioned/monitored bilateral flows
  const COUNTRY_PAIRS = [
    { reporter: 'CN', partner: 'RU', label: 'China → Russia' },
    { reporter: 'RU', partner: 'CN', label: 'Russia → China' },
    { reporter: 'CN', partner: 'IR', label: 'China → Iran' },
    { reporter: 'US', partner: 'CN', label: 'US → China' },
    { reporter: 'CN', partner: 'US', label: 'China → US' },
    { reporter: 'IN', partner: 'RU', label: 'India → Russia' },
  ];

  // IMF DOTS annual data lags 12-18 months. In early 2026, 2025 data doesn't exist yet.
  // Always request a 3-year window to guarantee 2+ observations for YoY comparison.
  const year = new Date().getFullYear() - 1;

  try {
    for (const pair of COUNTRY_PAIRS.slice(0, 4)) { // limit calls
      try {
        // IMF DOTS compact data: exports (TXG_FOB_USD) between pair
        const url = `https://dataservices.imf.org/REST/SDMX_JSON.svc/CompactData/DOT/A.${pair.reporter}.${pair.partner}.TXG_FOB_USD.?startPeriod=${year - 2}&endPeriod=${year}`;
        const res = await fetch(url, {
          headers: { 'User-Agent': 'CrucixIntelligence/1.0', 'Accept': 'application/json' },
          signal: AbortSignal.timeout(12000),
        });
        if (!res.ok) continue;

        const data = await res.json();
        const series = data?.CompactData?.DataSet?.Series;
        if (!series) continue;

        const obs = Array.isArray(series.Obs) ? series.Obs : [series.Obs].filter(Boolean);
        const vals = obs.map(o => ({ period: o['@TIME_PERIOD'], value: parseFloat(o['@OBS_VALUE']) }))
                       .filter(o => !isNaN(o.value));

        if (vals.length >= 2) {
          const latest = vals[vals.length - 1];
          const prior  = vals[vals.length - 2];
          const pctChg = prior.value ? ((latest.value - prior.value) / prior.value) * 100 : 0;

          results.flows.push({
            label:     pair.label,
            reporter:  pair.reporter,
            partner:   pair.partner,
            value_usd: latest.value * 1e6, // IMF reports in millions USD
            period:    latest.period,
            pctChange: Math.round(pctChg * 10) / 10,
            type:      'trade_flow',
          });

          // Flag anomalies: >20% change YoY in monitored corridors
          if (Math.abs(pctChg) > 20) {
            results.anomalies.push({
              text:      `${pair.label}: trade ${pctChg > 0 ? '+' : ''}${pctChg.toFixed(1)}% YoY ($${(latest.value / 1000).toFixed(1)}B)`,
              severity:  Math.abs(pctChg) > 40 ? 'high' : 'medium',
              type:      'trade_anomaly',
            });
          }
        }
      } catch {}
    }

    console.log(`[Comtrade] ${results.flows.length} IMF DOTS trade flows · ${results.anomalies.length} anomalies`);
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
