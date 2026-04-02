// SIPRI Arms Transfers Database
// Stockholm International Peace Research Institute
// Static annual market-share data (updated from March report) + live SIPRI news feed

import '../utils/env.mjs';

// ── Baseline data from SIPRI March 2025 Annual Report ────────────────────────
const BASELINE = {
  topExporters: [
    { country: 'United States', share: '40%', trend: 'stable',   rank: 1 },
    { country: 'France',        share: '11%', trend: 'rising',   rank: 2, change: '+44%' },
    { country: 'China',         share: '5.5%', trend: 'stable',  rank: 3 },
    { country: 'Russia',        share: '4.8%', trend: 'declining', rank: 4, change: '-53%' },
    { country: 'Germany',       share: '4.2%', trend: 'stable',  rank: 5 },
  ],
  topImporters: [
    { country: 'Saudi Arabia', share: '8.4%', rank: 1 },
    { country: 'India',        share: '7.3%', rank: 2 },
    { country: 'Qatar',        share: '6.7%', rank: 3 },
    { country: 'Ukraine',      share: '5.2%', rank: 4, note: 'war-driven demand' },
    { country: 'Pakistan',     share: '4.9%', rank: 5 },
  ],
  globalTrends: {
    europeanImportsChange:  '+94%',
    russianExportsChange:   '-53%',
    frenchExportsChange:    '+44%',
    globalMilitarySpending: '$2.44T',
    globalSpendingGrowth:   '+7%',
    topExporter: 'United States (40%)',
    topImporter: 'Saudi Arabia (8.4%)',
  },
};

const STATIC_SIGNALS = [
  '🚨 France overtakes Russia as #2 arms exporter (+44% vs -53%)',
  '📈 European arms imports up 94% — NATO rearmament underway',
  '📉 Russian arms exports collapsed 53% since 2019',
  '🇺🇸 US maintains 40% global market share',
  '🇺🇦 Ukraine enters top 5 arms importers (war-driven)',
  '💰 Global military spending hits $2.44 trillion record',
  '✈️ F-35 and Rafale dominate fighter jet market',
];

// ── Live SIPRI news fetch ─────────────────────────────────────────────────────

function parseRssItems(xml) {
  const items = [];
  const blocks = xml.match(/<item[\s\S]*?<\/item>/gi) || [];
  for (const b of blocks.slice(0, 10)) {
    const title   = (b.match(/<title[^>]*><!\[CDATA\[([\s\S]*?)\]\]><\/title>/) ||
                     b.match(/<title[^>]*>([\s\S]*?)<\/title>/))?.[1]?.trim() || '';
    const desc    = (b.match(/<description[^>]*><!\[CDATA\[([\s\S]*?)\]\]><\/description>/) ||
                     b.match(/<description[^>]*>([\s\S]*?)<\/description>/))?.[1]
                     ?.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 250) || '';
    const link    = (b.match(/<link>([\s\S]*?)<\/link>/))?.[1]?.trim() || '';
    const pubDate = b.match(/<pubDate>([\s\S]*?)<\/pubDate>/)?.[1]?.trim() || '';
    if (title) items.push({ title, desc, link, pubDate });
  }
  return items;
}

async function fetchLiveNews() {
  // SIPRI publishes news at sipri.org — try their news feed
  const SIPRI_FEEDS = [
    'https://sipri.org/rss.xml',
    'https://www.sipri.org/news-and-events/news',
  ];

  for (const url of SIPRI_FEEDS) {
    try {
      const res = await fetch(url, {
        signal: AbortSignal.timeout(10000),
        headers: { 'User-Agent': 'ArkmurusintelBot/1.0' },
      });
      if (!res.ok) continue;
      const text = await res.text();
      if (!text.includes('<item') && !text.includes('<article')) continue;
      const items = parseRssItems(text);
      if (items.length > 0) return items;
    } catch {}
  }
  return [];
}

// ── Main export ───────────────────────────────────────────────────────────────

export async function briefing() {
  console.log('[SIPRI] Fetching arms trade data...');

  // Static baseline updates (always included)
  const baseUpdates = [
    { source: 'SIPRI', title: '📊 Top Weapons Exporters 2025',
      content: 'US (40%) | France (11% +44%) | China (5.5%) | Russia (4.8% −53%) | Germany (4.2%)',
      url: 'https://sipri.org/databases/armstransfers', timestamp: Date.now(), priority: 'high' },
    { source: 'SIPRI', title: '🎯 Top Weapons Importers 2025',
      content: 'Saudi Arabia (8.4%), India (7.3%), Qatar (6.7%), Ukraine (5.2%), Pakistan (4.9%)',
      url: 'https://sipri.org/databases/armstransfers', timestamp: Date.now(), priority: 'high' },
    { source: 'SIPRI', title: '📈 European Arms Imports Surge 94%',
      content: 'European weapons imports up 94% due to Ukraine war. Poland, Germany, Netherlands lead increases.',
      timestamp: Date.now(), priority: 'high' },
    { source: 'SIPRI', title: '📉 Russian Arms Exports Collapse',
      content: "Russian arms exports declined 53% since 2019. France now 2nd largest exporter.",
      timestamp: Date.now(), priority: 'high' },
  ];

  // Attempt live news
  let liveUpdates = [];
  let liveSignals = [];
  try {
    const items = await fetchLiveNews();
    for (const item of items) {
      liveUpdates.push({
        source:    'SIPRI News',
        title:     item.title,
        content:   item.desc || item.title,
        url:       item.link,
        timestamp: item.pubDate ? new Date(item.pubDate).getTime() : Date.now(),
        priority:  'normal',
      });
      liveSignals.push(`[SIPRI] ${item.title.slice(0, 120)}`);
    }
    if (liveUpdates.length > 0) {
      console.log(`[SIPRI] ${liveUpdates.length} live news items fetched`);
    }
  } catch (err) {
    console.warn('[SIPRI] Live news fetch failed (non-fatal):', err.message);
  }

  const allUpdates = [...liveUpdates, ...baseUpdates];
  const allSignals = [...liveSignals, ...STATIC_SIGNALS];

  return {
    source:    'SIPRI Arms Transfers',
    timestamp: new Date().toISOString(),
    status:    'active',
    updates:   allUpdates,
    signals:   allSignals,
    metrics: {
      ...BASELINE,
      lastUpdated: new Date().toISOString(),
      dataSource:  'SIPRI Annual Report 2025 + live news',
    },
    counts: {
      updates:   allUpdates.length,
      signals:   allSignals.length,
      live:      liveUpdates.length,
      exporters: BASELINE.topExporters.length,
      importers: BASELINE.topImporters.length,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('sipri_arms.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
