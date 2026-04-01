// lib/search/engine.mjs
// Multi-source web + social + intel search engine
// Sources: Brave Search (if key) → SearXNG meta-search → Google News RSS → Bing News RSS
//          Reddit public API · OpenCorporates · Wikipedia · in-sweep intel cache

// ── SearXNG public instances — tried in order ────────────────────────────────
const SEARXNG_INSTANCES = [
  'https://searx.be',
  'https://search.mdosch.de',
  'https://searxng.world',
  'https://paulgo.io',
];

// ── Lightweight RSS parser ────────────────────────────────────────────────────
function parseRSS(xml) {
  const items = [];
  const itemRegex = /<item[^>]*>([\s\S]*?)<\/item>/gi;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    const get = tag => {
      const m = block.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, 'i'))
               || block.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
      return m ? m[1].trim().replace(/<[^>]+>/g, '').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'") : '';
    };
    const title = get('title');
    if (!title) continue;
    const rawPub = get('pubDate') || get('dc:date');
    items.push({
      title,
      url:     get('link') || get('guid'),
      pubDate: rawPub ? new Date(rawPub).toISOString() : new Date().toISOString(),
      snippet: get('description').substring(0, 250),
    });
  }
  return items;
}

async function fetchText(url, headers = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 9000);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { 'User-Agent': 'Crucix/1.0', ...headers },
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.text();
  } catch (e) {
    clearTimeout(timer);
    throw e;
  }
}

async function fetchJSON(url, headers = {}) {
  const text = await fetchText(url, headers);
  return JSON.parse(text);
}

// ── Web search ────────────────────────────────────────────────────────────────
// Priority: Brave Search API (quality) → SearXNG (free meta-search)
async function searchWeb(query, limit = 10) {
  // Brave Search — best quality, free tier 2000 queries/month
  if (process.env.BRAVE_API_KEY) {
    try {
      const data = await fetchJSON(
        `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${limit}&safesearch=off`,
        { 'Accept': 'application/json', 'X-Subscription-Token': process.env.BRAVE_API_KEY }
      );
      if (data?.web?.results?.length > 0) {
        return data.web.results.map(r => ({
          type: 'web', source: 'Brave Search',
          title: r.title || '', url: r.url || '',
          snippet: r.description || '', age: r.age || null,
        }));
      }
    } catch (e) {
      console.warn('[Search] Brave error:', e.message);
    }
  }

  // SearXNG — aggregates Google, Bing, DDG, etc.
  for (const instance of SEARXNG_INSTANCES) {
    try {
      const data = await fetchJSON(
        `${instance}/search?q=${encodeURIComponent(query)}&format=json&language=en&categories=general`,
        { 'Accept': 'application/json' }
      );
      if (data?.results?.length > 0) {
        return data.results.slice(0, limit).map(r => ({
          type: 'web', source: `Web (${r.engine || 'search'})`,
          title: r.title || '', url: r.url || '',
          snippet: r.content || '', score: r.score || 0,
        }));
      }
    } catch {}
  }

  return [];
}

// ── News search ───────────────────────────────────────────────────────────────
// Google News RSS (free, no auth) + Bing News RSS as fallback
async function searchNews(query, limit = 12) {
  const results = [];

  // Google News RSS
  try {
    const xml = await fetchText(
      `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en-US&gl=US&ceid=US:en`
    );
    results.push(...parseRSS(xml).slice(0, limit).map(i => ({
      type: 'news', source: 'Google News',
      title: i.title, url: i.url, snippet: i.snippet, pubDate: i.pubDate,
    })));
  } catch (e) {
    console.warn('[Search] Google News error:', e.message);
  }

  // Bing News RSS — supplement if Google News thin
  if (results.length < 6) {
    try {
      const xml = await fetchText(
        `https://www.bing.com/news/search?q=${encodeURIComponent(query)}&format=RSS`
      );
      results.push(...parseRSS(xml).slice(0, 6).map(i => ({
        type: 'news', source: 'Bing News',
        title: i.title, url: i.url, snippet: i.snippet, pubDate: i.pubDate,
      })));
    } catch {}
  }

  // Al Jazeera RSS — strong for Middle East / Africa / defense topics
  try {
    const xml = await fetchText(
      `https://www.aljazeera.com/xml/rss/all.xml`
    );
    const items = parseRSS(xml).filter(i =>
      i.title.toLowerCase().includes(query.toLowerCase()) ||
      i.snippet.toLowerCase().includes(query.toLowerCase())
    );
    results.push(...items.slice(0, 4).map(i => ({
      type: 'news', source: 'Al Jazeera',
      title: i.title, url: i.url, snippet: i.snippet, pubDate: i.pubDate,
    })));
  } catch {}

  // Reuters RSS
  try {
    const xml = await fetchText(
      `https://feeds.reuters.com/reuters/topNews`
    );
    const items = parseRSS(xml).filter(i =>
      i.title.toLowerCase().includes(query.toLowerCase()) ||
      i.snippet.toLowerCase().includes(query.toLowerCase())
    );
    results.push(...items.slice(0, 4).map(i => ({
      type: 'news', source: 'Reuters',
      title: i.title, url: i.url, snippet: i.snippet, pubDate: i.pubDate,
    })));
  } catch {}

  return results.slice(0, limit);
}

// ── Reddit ────────────────────────────────────────────────────────────────────
// Public JSON API — no auth required
async function searchReddit(query, limit = 10) {
  try {
    const data = await fetchJSON(
      `https://www.reddit.com/search.json?q=${encodeURIComponent(query)}&sort=new&limit=${limit}&type=link`,
      { 'Accept': 'application/json' }
    );
    if (data?.data?.children?.length > 0) {
      return data.data.children.map(c => c.data).map(p => ({
        type:     'social',
        source:   `Reddit · r/${p.subreddit}`,
        title:    p.title || '',
        url:      `https://reddit.com${p.permalink}`,
        snippet:  (p.selftext || '').substring(0, 200) || p.url || '',
        score:    p.score || 0,
        comments: p.num_comments || 0,
        pubDate:  new Date((p.created_utc || 0) * 1000).toISOString(),
      }));
    }
  } catch (e) {
    console.warn('[Search] Reddit error:', e.message);
  }
  return [];
}

// ── Companies ─────────────────────────────────────────────────────────────────
async function searchCompanies(query, limit = 5) {
  try {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (process.env.OPENCORPORATES_API_KEY) params.set('api_token', process.env.OPENCORPORATES_API_KEY);
    const data = await fetchJSON(
      `https://api.opencorporates.com/v0.4/companies/search?${params}`
    );
    if (data?.results?.companies?.length > 0) {
      return data.results.companies.map(c => c.company).map(c => ({
        type:   'company',
        source: 'OpenCorporates',
        title:  c.name || '',
        url:    c.opencorporates_url || `https://opencorporates.com`,
        snippet:`${(c.jurisdiction_code || '').toUpperCase()} · Reg: ${c.registration_number || 'N/A'} · Status: ${c.current_status || 'unknown'} · Inc: ${c.incorporation_date || 'N/A'}`,
        jurisdiction: c.jurisdiction_code,
        status:       c.current_status,
      }));
    }
  } catch (e) {
    console.warn('[Search] OpenCorporates error:', e.message);
  }
  return [];
}

// ── Wikipedia ─────────────────────────────────────────────────────────────────
async function searchWikipedia(query) {
  try {
    const data = await fetchJSON(
      `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(query.replace(/ /g, '_'))}`
    );
    if (data?.title && !data.error) {
      return [{
        type:    'reference',
        source:  'Wikipedia',
        title:   data.title,
        url:     data.content_urls?.desktop?.page || `https://en.wikipedia.org/wiki/${encodeURIComponent(query)}`,
        snippet: (data.extract || data.description || '').substring(0, 350),
      }];
    }
  } catch {}
  return [];
}

// ── In-sweep intel cache ──────────────────────────────────────────────────────
// Searches the last sweep's data — zero latency, no external calls
function searchIntelCache(query, cachedData) {
  if (!cachedData) return [];
  const q = query.toLowerCase();
  const hits = [];

  const check = (text, source, url = null, priority = 'medium') => {
    const t = String(text || '');
    if (t.toLowerCase().includes(q)) {
      hits.push({ type: 'intel', source, title: t.substring(0, 130), snippet: t.substring(0, 300), url, priority, pubDate: new Date().toISOString() });
    }
  };

  for (const s of (cachedData.tg?.urgent || []))              check(s.text, s.channel || 'OSINT', null, 'high');
  for (const u of (cachedData.lusophone?.updates || []))       check(u.title || u.text, 'Lusophone Intel', u.url);
  for (const e of (cachedData.exportControl?.updates || []))   check(e.title || e.text, 'Export Control', e.url, 'high');
  for (const a of (cachedData.defense?.updates || []))         check((a.title || '') + ' ' + (a.content || ''), 'Defense News', a.url);
  for (const s of (cachedData.opensanctions?.updates || []))   check(s.name, 'Sanctions', null, 'critical');
  for (const c of (cachedData.supplyChain?.metrics?.alerts || [])) check(c.message, 'Supply Chain', null, c.type || 'medium');
  for (const g of (cachedData.gdelt?.updates || []))           check(g.title, 'GDELT', g.url);
  for (const a of (cachedData.acled?.deadliestEvents || []))   check(`${a.location}: ${a.fatalities} fatalities`, 'ACLED', null, 'critical');
  for (const u of (cachedData.unsc?.updates || []))            check(u.title, 'UN Security Council', u.url, 'high');
  for (const t of (cachedData.thinkTanks?.updates || []))      check(t.title, t.source || 'Think Tank', t.url);

  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  return hits
    .sort((a, b) => (order[a.priority] ?? 2) - (order[b.priority] ?? 2))
    .slice(0, 12);
}

// ── Main orchestrator ─────────────────────────────────────────────────────────
export async function runSearch(query, cachedData = null) {
  const start = Date.now();

  const [web, news, reddit, companies, wikipedia] = await Promise.allSettled([
    searchWeb(query),
    searchNews(query),
    searchReddit(query),
    searchCompanies(query),
    searchWikipedia(query),
  ]);

  const intel = searchIntelCache(query, cachedData);

  console.log(`[Search] "${query}" completed in ${Date.now() - start}ms`);

  return {
    query,
    timestamp:  new Date().toISOString(),
    durationMs: Date.now() - start,
    results: {
      web:       web.status       === 'fulfilled' ? web.value       : [],
      news:      news.status      === 'fulfilled' ? news.value      : [],
      social:    reddit.status    === 'fulfilled' ? reddit.value    : [],
      companies: companies.status === 'fulfilled' ? companies.value : [],
      reference: wikipedia.status === 'fulfilled' ? wikipedia.value : [],
      intel,
    },
    totals: {
      web:       web.status       === 'fulfilled' ? web.value.length       : 0,
      news:      news.status      === 'fulfilled' ? news.value.length      : 0,
      social:    reddit.status    === 'fulfilled' ? reddit.value.length    : 0,
      companies: companies.status === 'fulfilled' ? companies.value.length : 0,
      reference: wikipedia.status === 'fulfilled' ? wikipedia.value.length : 0,
      intel:     intel.length,
    },
  };
}
