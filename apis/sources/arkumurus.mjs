// Arkmurus Intelligence Feed
// Aggregates live defence/security news from BICC, GRIP, ISS Africa, and
// the African Union Peace and Security Council — all directly relevant to
// Lusophone Africa arms brokering and export-control intelligence.

export async function briefing() {
  console.log('[Arkmurus] Fetching live Lusophone/Africa defence intelligence...');

  const FEEDS = [
    { name: 'ISS Africa',  url: 'https://issafrica.org/rss.xml' },
    { name: 'BICC',        url: 'https://www.bicc.de/publications/rss.xml' },
    { name: 'GRIP',        url: 'https://www.grip.org/en/rss.xml' },
    { name: 'AU PSC',      url: 'https://www.peaceau.org/en/rss.xml' },
  ];

  // Lusophone-relevant keywords for signal extraction
  const LUSO_KW = [
    'angola', 'mozambique', 'guinea-bissau', 'cape verde', 'são tomé',
    'sao tome', 'lusophone', 'portuguese', 'africa', 'sahel',
    'arms', 'weapons', 'defence', 'defense', 'military', 'security',
    'export', 'procurement', 'contract', 'sanctions', 'embargo',
  ];

  function parseRssItems(xml) {
    const items = [];
    const itemBlocks = xml.match(/<item[\s\S]*?<\/item>/gi) || [];
    for (const block of itemBlocks.slice(0, 8)) {
      const title   = (block.match(/<title[^>]*><!\[CDATA\[([\s\S]*?)\]\]><\/title>/) ||
                       block.match(/<title[^>]*>([\s\S]*?)<\/title>/))?.[1]?.trim() || '';
      const desc    = (block.match(/<description[^>]*><!\[CDATA\[([\s\S]*?)\]\]><\/description>/) ||
                       block.match(/<description[^>]*>([\s\S]*?)<\/description>/))?.[1]
                       ?.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300) || '';
      const link    = (block.match(/<link>([\s\S]*?)<\/link>/) ||
                       block.match(/<link[^>]*href="([^"]+)"/))?.[1]?.trim() || '';
      const pubDate = block.match(/<pubDate>([\s\S]*?)<\/pubDate>/)?.[1]?.trim() || '';
      if (title) items.push({ title, desc, link, pubDate });
    }
    return items;
  }

  const updates  = [];
  const signals  = [];
  const errors   = [];

  await Promise.allSettled(
    FEEDS.map(async ({ name, url }) => {
      try {
        const res = await fetch(url, {
          signal: AbortSignal.timeout(10000),
          headers: { 'User-Agent': 'ArkmurusintelBot/1.0' }
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const xml   = await res.text();
        const items = parseRssItems(xml);

        for (const item of items) {
          const combined = (item.title + ' ' + item.desc).toLowerCase();
          const relevant = LUSO_KW.some(kw => combined.includes(kw));
          if (!relevant) continue;

          updates.push({
            source:    name,
            title:     item.title,
            content:   item.desc || item.title,
            url:       item.link,
            timestamp: item.pubDate ? new Date(item.pubDate).getTime() : Date.now(),
            priority:  combined.includes('critical') || combined.includes('alert') ? 'high' : 'normal',
          });

          signals.push(`[${name}] ${item.title.slice(0, 120)}`);
        }
        console.log(`[Arkmurus] ${name}: ${items.length} items, ${signals.length} relevant so far`);
      } catch (err) {
        errors.push(`${name}: ${err.message}`);
        console.warn(`[Arkmurus] ${name} fetch failed: ${err.message}`);
      }
    })
  );

  // Deduplicate by title
  const seen  = new Set();
  const dedup = updates.filter(u => {
    const key = u.title.toLowerCase().slice(0, 80);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Sort newest first
  dedup.sort((a, b) => b.timestamp - a.timestamp);

  return {
    source:    'Arkmurus Intelligence',
    timestamp: new Date().toISOString(),
    status:    dedup.length > 0 ? 'active' : (errors.length === FEEDS.length ? 'error' : 'partial'),
    updates:   dedup,
    signals:   signals.slice(0, 10),
    alerts:    dedup.filter(u => u.priority === 'high').map(u => ({ text: u.title, priority: 'high' })),
    errors:    errors.length > 0 ? errors : undefined,
    counts: {
      updates: dedup.length,
      signals: signals.length,
      feeds:   FEEDS.length,
      errors:  errors.length,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('arkumurus.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
