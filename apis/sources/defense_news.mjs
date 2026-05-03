// apis/sources/defense_news.mjs
// Live defense industry news — arms contracts, market moves, African security
// Sources: DefenseWeb Africa, Breaking Defense, DefenseNews, ISS Africa, UN Peacekeeping
// All via RSS — direct → rss2json → allorigins fallback chain

import '../utils/env.mjs';
import { withConcurrency, shouldSkip, recordFailure, recordSuccess } from '../../lib/util/throttle.mjs';

const RSS2JSON   = 'https://api.rss2json.com/v1/api.json?rss_url=';
const ALLORIGINS = 'https://api.allorigins.win/get?url=';

const FEEDS = [
  {
    name: 'DefenseWeb',
    url:  'https://www.defenceweb.co.za/feed/',
    region: 'Africa',
    weight: 3, // Africa-focused — highest relevance for Arkmurus
  },
  {
    name: 'ISS Africa',
    // issafrica.org blocks cloud IPs — use Google News RSS
    url:  'https://news.google.com/rss/search?q=%22ISS+Africa%22+security+defence+peace&hl=en&gl=US&ceid=US:en',
    region: 'Africa',
    weight: 3,
  },
  {
    name: 'Breaking Defense',
    url:  'https://breakingdefense.com/feed/',
    region: 'Global',
    weight: 2,
  },
  {
    name: 'Defense News',
    url:  'https://www.defensenews.com/arc/outboundfeeds/rss/?hierarchy=',
    region: 'Global',
    weight: 2,
  },
  {
    name: 'Jane\'s / Shephard',
    // Google News: defence procurement — always accessible from cloud IPs
    url:  'https://news.google.com/rss/search?q=defence+procurement+contract+military+acquisition&hl=en&gl=GB&ceid=GB:en',
    region: 'Global',
    weight: 2,
  },
  {
    name: 'Africa Defence News',
    url:  'https://news.google.com/rss/search?q=africa+military+defence+security+procurement&hl=en&gl=US&ceid=US:en',
    region: 'Africa',
    weight: 3,
  },
  {
    name: 'UN News Africa',
    url:  'https://news.un.org/feed/subscribe/en/news/region/africa/feed/rss.xml',
    region: 'Africa',
    weight: 2,
  },
  // ── East/Central Africa (2026-04-12) ──────────────────────────────
  {
    name: 'Kenya Defence',
    url:  'https://news.google.com/rss/search?q=Kenya+military+KDF+defence+procurement+security&hl=en&gl=KE&ceid=KE:en',
    region: 'East Africa',
    weight: 3,
  },
  {
    name: 'Ethiopia Defence',
    url:  'https://news.google.com/rss/search?q=Ethiopia+ENDF+military+defence+Tigray+security+procurement&hl=en&gl=US&ceid=US:en',
    region: 'East Africa',
    weight: 3,
  },
  {
    name: 'East Africa Security',
    url:  'https://news.google.com/rss/search?q=%22East+Africa%22+OR+Uganda+OR+Tanzania+military+defence+security+AMISOM&hl=en&gl=KE&ceid=KE:en',
    region: 'East Africa',
    weight: 2,
  },
  {
    name: 'DRC Congo Defence',
    url:  'https://news.google.com/rss/search?q=DRC+Congo+FARDC+MONUSCO+military+security+arms&hl=en&gl=US&ceid=US:en',
    region: 'Central Africa',
    weight: 3,
  },
  {
    name: 'Great Lakes Security',
    url:  'https://news.google.com/rss/search?q=%22Great+Lakes%22+OR+Rwanda+OR+Burundi+military+security+peacekeeping&hl=en&gl=US&ceid=US:en',
    region: 'Central Africa',
    weight: 2,
  },
  {
    name: 'SIPRI Blog',
    // sipri.org blocks cloud IPs — use Google News RSS
    url:  'https://news.google.com/rss/search?q=SIPRI+arms+trade+military+expenditure&hl=en&gl=US&ceid=US:en',
    region: 'Global',
    weight: 2,
  },
];

// Relevance scoring — Arkmurus focus: Lusophone Africa, procurement, brokering
const KW_LUSOPHONE  = ['angola', 'mozambique', 'guinea-bissau', 'cape verde', 'são tomé', 'sao tome', 'lusophone'];
const KW_AFRICA     = ['africa', 'sadc', 'ecowas', 'au mission', 'amisom', 'minusma', 'monusco', 'unmiss', 'african union'];
const KW_PROCUREMENT = ['contract', 'tender', 'procure', 'order', 'sale', 'agreement', 'awarded', 'acquisition', 'purchase', 'bid', 'deal', 'signed'];
const KW_EQUIPMENT  = ['helicopter', 'aircraft', 'vessel', 'patrol', 'armored', 'armoured', 'vehicle', 'ammunition', 'munition', 'radar', 'drone', 'uav', 'missile', 'rifle', 'weapon', 'frigate', 'corvette'];

function scoreItem(title, desc) {
  const text = `${title} ${desc}`.toLowerCase();
  let score = 0;
  for (const kw of KW_LUSOPHONE)   if (text.includes(kw)) score += 10;
  for (const kw of KW_AFRICA)      if (text.includes(kw)) score += 5;
  for (const kw of KW_PROCUREMENT) if (text.includes(kw)) score += 3;
  for (const kw of KW_EQUIPMENT)   if (text.includes(kw)) score += 2;
  return score;
}

function parseXML(xml, feedName) {
  const items = [];
  const isAtom = xml.includes('<entry>');
  const tag    = isAtom ? 'entry' : 'item';
  const re     = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'gi');
  let m;

  while ((m = re.exec(xml)) !== null) {
    const b     = m[1];
    const title = (b.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i)?.[1] || '').trim();
    const link  = b.match(/<link[^>]*href="([^"]+)"/i)?.[1]
               || b.match(/<link[^>]*>([\s\S]*?)<\/link>/i)?.[1]?.trim() || '';
    const date  = b.match(/<pubDate>([\s\S]*?)<\/pubDate>/i)?.[1]
               || b.match(/<published>([\s\S]*?)<\/published>/i)?.[1] || '';
    const rawDesc = (b.match(/<description[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/description>/i)?.[1]
               || b.match(/<summary[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/summary>/i)?.[1] || '');
    const desc = rawDesc
      .replace(/<[^>]*>/g, ' ')
      .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ').replace(/&apos;/g, "'")
      .replace(/\s+/g, ' ')
      .slice(0, 500)
      .trim();

    if (title) {
      items.push({ source: feedName, title, description: desc, url: link, pubDate: date });
    }
    if (items.length >= 20) break;
  }
  return items;
}

async function fetchFeed(feed) {
  // R-F18 2026-05-01: same root cause as R-F17 (Arkmurus). The
  // Googlebot UA pretender works against most servers but Google News
  // itself detects non-Google-IP requests claiming to be Googlebot and
  // throttles aggressively. Live evidence (3 sweeps): Ethiopia /
  // ISS Africa / Africa Defence News / Defense News / Breaking
  // Defense / Kenya Defence / Great Lakes Security / DRC Congo
  // Defence / DefenseWeb all "all attempts failed". 09:19:23 was 1/13
  // sources OK. Switch to a stock Chrome desktop UA and bump the
  // direct timeout to 15s.
  const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36';
  const attempts = [
    () => fetch(feed.url, {
      headers: { 'User-Agent': USER_AGENT },
      signal: AbortSignal.timeout(15000),
    }),
    () => fetch(RSS2JSON + encodeURIComponent(feed.url), {
      signal: AbortSignal.timeout(12000),
    }),
    () => fetch(ALLORIGINS + encodeURIComponent(feed.url), {
      signal: AbortSignal.timeout(14000),
    }),
  ];

  for (const attempt of attempts) {
    try {
      const res = await attempt();
      if (!res.ok) continue;
      const text = await res.text();
      if (!text || text.length < 50) continue;

      // rss2json JSON response
      if (text.trim().startsWith('{')) {
        let data;
        try { data = JSON.parse(text); } catch { continue; }

        // allorigins wrapper
        if (data.contents) {
          const inner = data.contents;
          if (inner.includes('<item>') || inner.includes('<entry>')) return parseXML(inner, feed.name);
        }

        // rss2json direct
        if (data.status === 'ok' && data.items?.length) {
          return data.items.map(i => ({
            source:      feed.name,
            title:       (i.title || '').trim(),
            description: (i.description || i.content || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').slice(0, 300).trim(),
            url:         i.link || '',
            pubDate:     i.pubDate || '',
          }));
        }
        continue;
      }

      // Raw XML
      if (text.includes('<item>') || text.includes('<entry>')) return parseXML(text, feed.name);
    } catch {}
  }

  console.warn(`[DefenseNews] ${feed.name}: all attempts failed`);
  return [];
}

export async function briefing() {
  console.log('[DefenseNews] Fetching live defense industry feeds...');

  // R-F21 2026-05-01: cap concurrency at 4 to avoid the 13-feed burst
  // pattern that triggered seenode connection-pool / proxy quota
  // exhaustion on 2026-05-01 09:39 (0/13 sources OK across 13×3 = 39
  // simultaneous outbound calls). Per-feed circuit breaker skips a
  // source after 3 consecutive empty returns for 5 minutes.
  const results = await withConcurrency(FEEDS, async (feed) => {
    const ckey = `defense_news:${feed.name}`;
    if (shouldSkip(ckey)) return [];  // circuit open — skip wasted call
    const items = await fetchFeed(feed);
    if (Array.isArray(items) && items.length > 0) {
      recordSuccess(ckey);
    } else {
      recordFailure(ckey);
    }
    return items;
  }, { concurrency: 4 });

  const allItems = [];
  const sourceStatus = {};

  for (let i = 0; i < FEEDS.length; i++) {
    const feed = FEEDS[i];
    const r    = results[i];
    const items = r.status === 'fulfilled' ? r.value : [];
    sourceStatus[feed.name] = items.length > 0 ? 'ok' : 'failed';

    for (const item of items) {
      allItems.push({
        ...item,
        relevanceScore: scoreItem(item.title, item.description) * feed.weight,
      });
    }
  }

  // Sort by relevance, deduplicate by title similarity
  allItems.sort((a, b) => b.relevanceScore - a.relevanceScore);

  const seen   = new Set();
  const unique = [];
  for (const item of allItems) {
    const key = item.title.toLowerCase().slice(0, 60);
    if (!seen.has(key)) {
      seen.add(key);
      unique.push(item);
    }
  }

  const top = unique.slice(0, 30);
  const highRelevance = top.filter(i => i.relevanceScore >= 10);
  const lusophone     = top.filter(i => KW_LUSOPHONE.some(kw => `${i.title} ${i.description}`.toLowerCase().includes(kw)));

  const updates = top.map(i => ({
    title:    `[${i.source}] ${i.title}`,
    source:   i.source,
    content:  i.description,
    url:      i.url,
    timestamp: i.pubDate ? new Date(i.pubDate).getTime() : Date.now(),
    priority: i.relevanceScore >= 15 ? 'high' : i.relevanceScore >= 5 ? 'medium' : 'normal',
    type:     'defense_news',
  }));

  const signals = [
    ...lusophone.slice(0, 3).map(i => ({
      text:     `[Lusophone] ${i.source}: ${i.title.slice(0, 120)}`,
      source:   i.source,
      priority: 'high',
    })),
    ...highRelevance.filter(i => !lusophone.includes(i)).slice(0, 4).map(i => ({
      text:     `[${i.source}] ${i.title.slice(0, 120)}`,
      source:   i.source,
      priority: 'medium',
    })),
  ];

  const okSources = Object.values(sourceStatus).filter(s => s === 'ok').length;
  const failedSubs = Object.entries(sourceStatus)
    .filter(([, s]) => s !== 'ok')
    .map(([n]) => n);
  console.log(`[DefenseNews] ${top.length} items · ${lusophone.length} Lusophone · ${okSources}/${FEEDS.length} sources OK`);

  return {
    source:       'Defense News',
    timestamp:    new Date().toISOString(),
    updates,
    signals,
    sourceStatus,
    counts: {
      total:      top.length,
      lusophone:  lusophone.length,
      highRelevance: highRelevance.length,
      sourcesOk:  okSources,
    },
    // R-F34: surface sub-source health to the runner so the top-level
    // sweep tally is honest. See apis/briefing.mjs `runSource`.
    _subStatus: {
      ok:     okSources,
      total:  FEEDS.length,
      failed: failedSubs,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('defense_news.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
