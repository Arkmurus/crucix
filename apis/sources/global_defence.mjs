// apis/sources/global_defence.mjs
// 2026-04-12: Global defence intelligence sources — covers Asia, MENA, LatAm gaps.
// 10 new sources targeting regions underrepresented in the existing 53-source sweep.
// All via RSS (direct → rss2json → allorigins fallback chain).

import '../utils/env.mjs';

const RSS2JSON   = 'https://api.rss2json.com/v1/api.json?rss_url=';
const ALLORIGINS = 'https://api.allorigins.win/get?url=';

const FEEDS = [
  // ── ASIA ──────────────────────────────────────────────────────────────
  {
    name: 'Asia Military Review',
    url:  'https://news.google.com/rss/search?q=%22asia+military%22+OR+%22asian+defence%22+procurement+contract&hl=en&gl=SG&ceid=SG:en',
    region: 'Asia-Pacific',
    weight: 3,
  },
  {
    name: 'India Defence News',
    url:  'https://news.google.com/rss/search?q=india+defence+procurement+military+Make+in+India+DPP&hl=en&gl=IN&ceid=IN:en',
    region: 'Asia-Pacific',
    weight: 3,
  },
  {
    name: 'ASEAN Defence',
    url:  'https://news.google.com/rss/search?q=ASEAN+military+defence+Indonesia+Philippines+Vietnam+procurement&hl=en&gl=SG&ceid=SG:en',
    region: 'Asia-Pacific',
    weight: 2,
  },
  // ── AFRICA ────────────────────────────────────────────────────────────
  {
    name: 'ISS Africa Direct',
    url:  'https://news.google.com/rss/search?q=%22Institute+for+Security+Studies%22+OR+%22ISS+Africa%22+security+conflict&hl=en&gl=ZA&ceid=ZA:en',
    region: 'Africa',
    weight: 3,
  },
  {
    name: 'Sahel Intelligence',
    url:  'https://news.google.com/rss/search?q=sahel+security+military+Mali+Burkina+Niger+Wagner+Africa+Corps&hl=en&gl=US&ceid=US:en',
    region: 'Africa',
    weight: 3,
  },
  // ── MENA ──────────────────────────────────────────────────────────────
  {
    name: 'Al Jazeera Defence',
    url:  'https://www.aljazeera.com/xml/rss/all.xml',
    region: 'MENA',
    weight: 2,
  },
  {
    name: 'Gulf Defence Procurement',
    url:  'https://news.google.com/rss/search?q=Gulf+defence+procurement+Saudi+UAE+IDEX+SAMI+EDGE+military+contract&hl=en&gl=AE&ceid=AE:en',
    region: 'MENA',
    weight: 3,
  },
  // ── LATIN AMERICA ─────────────────────────────────────────────────────
  {
    name: 'Infodefensa LatAm',
    url:  'https://news.google.com/rss/search?q=infodefensa+OR+%22defensa+latinoamerica%22+OR+%22LAAD%22+militar+adquisicion&hl=es&gl=MX&ceid=MX:es',
    region: 'Latin-America',
    weight: 3,
  },
  {
    name: 'Zona Militar',
    url:  'https://news.google.com/rss/search?q=%22zona+militar%22+OR+%22defensa+sudamericana%22+ejercito+armada+compra&hl=es&gl=AR&ceid=AR:es',
    region: 'Latin-America',
    weight: 2,
  },
  // ── GLOBAL TRADE ──────────────────────────────────────────────────────
  {
    name: 'Global Arms Trade',
    url:  'https://news.google.com/rss/search?q=arms+trade+export+military+transfer+SIPRI+defence+deal+billion&hl=en&gl=GB&ceid=GB:en',
    region: 'Global',
    weight: 2,
  },
];

// Relevance scoring — GLOBAL lens: all regions weighted equally
const KW_ASIA      = ['india', 'indonesia', 'singapore', 'vietnam', 'philippines', 'south korea', 'korea', 'japan', 'asean', 'aukus', 'australia', 'taiwan'];
const KW_MENA      = ['saudi', 'uae', 'qatar', 'bahrain', 'oman', 'kuwait', 'jordan', 'egypt', 'morocco', 'algeria', 'israel', 'gulf', 'gcc', 'idex'];
const KW_LATAM     = ['brazil', 'colombia', 'peru', 'chile', 'argentina', 'mexico', 'laad', 'embraer', 'fidae'];
const KW_AFRICA    = ['angola', 'mozambique', 'nigeria', 'kenya', 'south africa', 'ghana', 'senegal', 'cote d\'ivoire', 'ethiopia', 'sahel', 'mali', 'niger', 'burkina'];
const KW_PROCUREMENT = ['contract', 'tender', 'procure', 'order', 'sale', 'awarded', 'acquisition', 'deal', 'signed', 'billion', 'million'];
const KW_EQUIPMENT = ['helicopter', 'aircraft', 'vessel', 'patrol', 'armoured', 'vehicle', 'ammunition', 'radar', 'drone', 'uav', 'missile', 'frigate', 'corvette', 'submarine', 'fighter', 'howitzer'];

function scoreItem(title, desc) {
  const text = `${title} ${desc}`.toLowerCase();
  let score = 0;
  for (const kw of KW_ASIA)         if (text.includes(kw)) score += 5;
  for (const kw of KW_MENA)         if (text.includes(kw)) score += 5;
  for (const kw of KW_LATAM)        if (text.includes(kw)) score += 5;
  for (const kw of KW_AFRICA)       if (text.includes(kw)) score += 5;
  for (const kw of KW_PROCUREMENT)  if (text.includes(kw)) score += 3;
  for (const kw of KW_EQUIPMENT)    if (text.includes(kw)) score += 2;
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
    if (items.length >= 15) break;
  }
  return items;
}

async function fetchFeed(feed) {
  const attempts = [
    () => fetch(feed.url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
      signal: AbortSignal.timeout(10000),
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
      const ct = res.headers.get('content-type') || '';
      let body = await res.text();
      // rss2json returns JSON wrapper
      if (ct.includes('json')) {
        try {
          const json = JSON.parse(body);
          if (json.contents) body = json.contents; // allorigins wrapper
          if (json.items) {
            return json.items.map(i => ({
              source: feed.name,
              title: i.title || '',
              description: (i.description || '').replace(/<[^>]*>/g, ' ').slice(0, 500).trim(),
              url: i.link || '',
              pubDate: i.pubDate || '',
            }));
          }
        } catch { /* fall through to XML parse */ }
      }
      return parseXML(body, feed.name);
    } catch { continue; }
  }
  return [];
}

export async function briefing() {
  return globalDefence();
}

export default async function globalDefence() {
  const results = await Promise.allSettled(FEEDS.map(f => fetchFeed(f)));
  const items = [];

  for (const r of results) {
    if (r.status === 'fulfilled' && r.value) items.push(...r.value);
  }

  // Score, sort, deduplicate
  const scored = items.map(item => ({
    ...item,
    _score: scoreItem(item.title, item.description),
  }));
  scored.sort((a, b) => b._score - a._score);

  const seen = new Set();
  const deduped = [];
  for (const item of scored) {
    const key = item.title.toLowerCase().slice(0, 60);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }

  return {
    source: 'global_defence',
    items: deduped.slice(0, 50),
    count: deduped.length,
    feeds: FEEDS.length,
    regions: ['Asia-Pacific', 'Africa', 'MENA', 'Latin-America', 'Global'],
  };
}
