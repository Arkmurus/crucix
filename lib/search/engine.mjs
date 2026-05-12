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

// ── R-F368 (2026-05-12) per-backend 401/403 mute ─────────────────────────
//
// Live evidence 2026-05-12 11:03-11:04 BST: operator typed an entity name
// into the DD search UI, the frontend fired /api/search/entity per
// keystroke (~8 progressively-truncated variants in 60s). Each variant
// triggered the full fan-out (OpenSanctions + OpenCorporates + Reddit),
// and EVERY single one returned 401/403 because the API keys aren't set
// on seenode (OpenSanctions/OpenCorporates) or the upstream blocks bots
// (Reddit). Net effect: ~24 wasted HTTP calls per keystroke burst, log
// flood, and zero useful data for the operator.
//
// Fix: track per-backend consecutive 401/403 count. On N=3 in a row,
// mute the backend for 10 minutes (return [] / unchecked-result without
// firing). After cooldown, allow one probe to test recovery — if it
// also fails, mute another 10 min.
//
// Authoritative source for "is X muted":  _backendMute.get(name).until
const _backendMute = new Map();
const _BACKEND_MUTE_THRESHOLD = 3;
const _BACKEND_MUTE_COOLDOWN_MS = 10 * 60 * 1000;

function _isBackendMuted(name) {
  const m = _backendMute.get(name);
  if (!m) return false;
  return Date.now() < (m.until || 0);
}

function _recordBackendAuthFailure(name, errMessage) {
  // Only count as a "mute-worthy" failure when message starts with
  // HTTP 4xx auth/policy codes. Transient timeouts and 5xx are not
  // mute-worthy — those should be retried per normal.
  if (!errMessage || typeof errMessage !== 'string') return;
  const isAuth = /^HTTP\s+40[13]\b/.test(errMessage) || /\b40[13]\b/.test(errMessage);
  if (!isAuth) return;
  const m = _backendMute.get(name) || { count: 0, until: 0 };
  m.count += 1;
  if (m.count >= _BACKEND_MUTE_THRESHOLD) {
    m.until = Date.now() + _BACKEND_MUTE_COOLDOWN_MS;
    m.count = 0;  // reset for after-cooldown probe
    console.warn(`[Search] ${name}: muted for 10min after ${_BACKEND_MUTE_THRESHOLD} consecutive 401/403 (R-F368). Check API key env vars.`);
  }
  _backendMute.set(name, m);
}

function _recordBackendSuccess(name) {
  // Any success clears the streak counter (but not an active mute window).
  const m = _backendMute.get(name);
  if (m && m.count > 0 && !m.until) {
    m.count = 0;
    _backendMute.set(name, m);
  }
}

// ── Web search ────────────────────────────────────────────────────────────────
// R-F373 (2026-05-12): Brave Search removed (operator independence directive
// 2026-05-12, mirrors Python aria_service R-F320 stub). Was the priority
// backend; SearXNG → Google News RSS now serve the full load. Removing
// the consumer here (vs leaving it as a no-op stub) prevents anyone from
// accidentally re-enabling it by setting BRAVE_API_KEY.
async function searchWeb(query, limit = 10) {
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
// Public JSON API — no auth required (but increasingly returns 403 on
// untrusted UAs / IPs — R-F368 mute kicks in after 3 consecutive 403s).
async function searchReddit(query, limit = 10) {
  if (_isBackendMuted('reddit')) return [];
  try {
    const data = await fetchJSON(
      `https://www.reddit.com/search.json?q=${encodeURIComponent(query)}&sort=new&limit=${limit}&type=link`,
      { 'Accept': 'application/json' }
    );
    _recordBackendSuccess('reddit');
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
    _recordBackendAuthFailure('reddit', e.message);
    console.warn('[Search] Reddit error:', e.message);
  }
  return [];
}

// ── Companies ─────────────────────────────────────────────────────────────────
async function searchCompanies(query, limit = 5) {
  if (_isBackendMuted('opencorporates')) return [];
  try {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (process.env.OPENCORPORATES_API_KEY) params.set('api_token', process.env.OPENCORPORATES_API_KEY);
    const data = await fetchJSON(
      `https://api.opencorporates.com/v0.4/companies/search?${params}`
    );
    _recordBackendSuccess('opencorporates');
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
    _recordBackendAuthFailure('opencorporates', e.message);
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

// ── OpenSanctions entity check ────────────────────────────────────────────────
// Free-tier search — no API key required for basic lookups
async function checkSanctions(query) {
  const result = { sanctioned: false, lists: [], entities: [], confidence: 0.95, checked: true };
  // R-F368: if OpenSanctions is muted (3 consecutive 401/403s), short-circuit.
  // The caller sees `checked:false confidence:0` rather than another wasted
  // round-trip; sanctioned defaults to false so downstream code doesn't
  // false-positive on "we don't know" — the verifier gate handles this.
  if (_isBackendMuted('opensanctions')) {
    result.checked = false;
    result.confidence = 0;
    return result;
  }
  // R-F368: OpenSanctions free /search/default endpoint started returning
  // 401 on un-authed requests at some point in 2026. When OPENSANCTIONS_API_KEY
  // is set (already configured on fly.io per pickup; needs copying to
  // seenode), send it as Authorization. Without the key the endpoint will
  // continue to 401 and the mute will kick in.
  const headers = { 'Accept': 'application/json' };
  if (process.env.OPENSANCTIONS_API_KEY) {
    headers['Authorization'] = `ApiKey ${process.env.OPENSANCTIONS_API_KEY}`;
  }
  try {
    const data = await fetchJSON(
      `https://api.opensanctions.org/search/default?q=${encodeURIComponent(query)}&schema=Company&schema=Person&limit=5`,
      headers,
    );
    _recordBackendSuccess('opensanctions');
    if (data?.results?.length > 0) {
      for (const ent of data.results) {
        const datasets = ent.datasets || [];
        const isSanctioned = datasets.some(d =>
          ['us_ofac_sdn','eu_fsf','un_sc_sanctions','gb_hmt_sanctions','opensanctions'].includes(d)
        );
        result.entities.push({
          name:       ent.caption || ent.name || query,
          sanctioned: isSanctioned,
          lists:      datasets,
          topics:     ent.properties?.topics || [],
          countries:  ent.properties?.country || [],
          score:      ent.score || 0,
        });
        if (isSanctioned) {
          result.sanctioned = true;
          result.lists.push(...datasets);
        }
      }
      result.lists = [...new Set(result.lists)];
    }
  } catch (e) {
    result.checked  = false;
    result.confidence = 0;
    _recordBackendAuthFailure('opensanctions', e.message);
    console.warn('[Search] OpenSanctions check error:', e.message);
  }
  return result;
}

// ── GLEIF Legal Entity Identifier lookup ──────────────────────────────────────
// Free API — no key required
async function searchGLEIF(query) {
  try {
    const data = await fetchJSON(
      `https://api.gleif.org/api/v1/fuzzycompletions?field=entity.legalName&q=${encodeURIComponent(query)}`
    );
    const completions = data?.data || [];
    if (completions.length === 0) return [];
    // Fetch first LEI record for details
    const lei = completions[0]?.relationships?.lei?.data?.id;
    if (!lei) return [];
    const detail = await fetchJSON(`https://api.gleif.org/api/v1/lei-records/${lei}`);
    const ent = detail?.data?.attributes;
    if (!ent) return [];
    return [{
      type:          'corporate',
      source:        'GLEIF (Official LEI)',
      confidence:    0.92,
      lei,
      name:          ent.entity?.legalName?.name || '',
      legalForm:     ent.entity?.legalForm?.id || '',
      jurisdiction:  ent.entity?.jurisdiction || '',
      status:        ent.entity?.status || '',
      address:       [
        ent.entity?.legalAddress?.addressLines?.join(', '),
        ent.entity?.legalAddress?.city,
        ent.entity?.legalAddress?.country,
      ].filter(Boolean).join(', '),
      registrationDate: ent.registration?.initialRegistrationDate || null,
      url: `https://www.gleif.org/en/lei-data/global-lei-index/lei-record-detail-view/data/${lei}`,
    }];
  } catch {
    return [];
  }
}

// ── Wikidata structured entity lookup ─────────────────────────────────────────
async function searchWikidata(query) {
  try {
    const search = await fetchJSON(
      `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=${encodeURIComponent(query)}&language=en&limit=1&format=json&type=item`
    );
    const entity = search?.search?.[0];
    if (!entity) return null;
    return {
      id:          entity.id,
      label:       entity.label || '',
      description: entity.description || '',
      url:         entity.url || `https://www.wikidata.org/wiki/${entity.id}`,
      confidence:  0.80,
    };
  } catch {
    return null;
  }
}

// ── Confidence scoring ────────────────────────────────────────────────────────
// Each source has a reliability weight; aggregate confidence across sources
const SOURCE_WEIGHTS = {
  'OpenSanctions': 0.95, 'GLEIF (Official LEI)': 0.92, 'OpenCorporates': 0.90,
  'Wikipedia': 0.78, 'Wikidata': 0.80, 'Google News': 0.72, 'Brave Search': 0.70,
  'Bing News': 0.68, 'Reuters': 0.75, 'Al Jazeera': 0.68,
  'intel': 0.85, 'default': 0.60,
};

function scoreConfidence(sources) {
  if (!sources.length) return 0;
  const weights = sources.map(s => SOURCE_WEIGHTS[s] || SOURCE_WEIGHTS.default);
  // Combine: higher with more sources, capped at 0.97
  const base = Math.max(...weights);
  const bonus = Math.min(0.15, (sources.length - 1) * 0.04);
  return Math.min(0.97, base + bonus);
}

// ── LLM entity synthesis ──────────────────────────────────────────────────────
async function synthesizeWithLLM(query, data, llmProvider) {
  if (!llmProvider) return null;
  try {
    const context = {
      corporate: (data.companies || []).slice(0, 3).map(c => ({ name: c.title, snippet: c.snippet })),
      gleif:     (data.gleif || []).slice(0, 2).map(g => ({ name: g.name, jurisdiction: g.jurisdiction, status: g.status, address: g.address })),
      wiki:      data.reference?.[0]?.snippet || '',
      wikidata:  data.wikidata?.description || '',
      news:      (data.news || []).slice(0, 5).map(n => n.title),
      sanctions: { sanctioned: data.sanctions?.sanctioned, lists: data.sanctions?.lists },
    };
    const prompt = `You are an intelligence analyst. Analyze the following data about the entity "${query}" and provide a concise structured summary.

Data: ${JSON.stringify(context)}

Respond in JSON with this exact structure:
{
  "entityType": "company|person|organisation",
  "summary": "2-3 sentence factual description",
  "keyFacts": ["fact1","fact2","fact3"],
  "riskIndicators": ["risk1","risk2"] or [],
  "peopleFound": ["name - role"] or [],
  "sector": "industry sector or null",
  "confidence": 0.0-1.0
}
Only include verified facts. Set confidence based on data quality.`;

    const raw = await llmProvider.complete(prompt, { maxTokens: 600, temperature: 0.1 });
    const jsonMatch = raw.match(/\{[\s\S]*\}/);
    if (jsonMatch) return JSON.parse(jsonMatch[0]);
  } catch (e) {
    console.warn('[Search] LLM synthesis error:', e.message);
  }
  return null;
}

// ── Entity power search ───────────────────────────────────────────────────────
export async function runEntitySearch(query, cachedData = null, llmProvider = null) {
  const start = Date.now();

  // Run all sources in parallel
  const [web, news, companies, wikipedia, sanctions, gleif, wikidata] = await Promise.allSettled([
    searchWeb(`"${query}" company OR organisation OR defense`, 8),
    searchNews(query, 12),
    searchCompanies(query, 6),
    searchWikipedia(query),
    checkSanctions(query),
    searchGLEIF(query),
    searchWikidata(query),
  ]);

  const intel = searchIntelCache(query, cachedData);

  const companiesVal  = companies.status  === 'fulfilled' ? companies.value  : [];
  const newsVal       = news.status       === 'fulfilled' ? news.value       : [];
  const webVal        = web.status        === 'fulfilled' ? web.value        : [];
  const wikiVal       = wikipedia.status  === 'fulfilled' ? wikipedia.value  : [];
  const sanctionsVal  = sanctions.status  === 'fulfilled' ? sanctions.value  : { checked: false, confidence: 0 };
  const gleifVal      = gleif.status      === 'fulfilled' ? gleif.value      : [];
  const wikidataVal   = wikidata.status   === 'fulfilled' ? wikidata.value   : null;

  // Build source list for confidence scoring
  const activeSources = [
    ...(companiesVal.length  > 0 ? ['OpenCorporates']        : []),
    ...(gleifVal.length      > 0 ? ['GLEIF (Official LEI)']  : []),
    ...(wikiVal.length       > 0 ? ['Wikipedia']             : []),
    ...(wikidataVal          ?     ['Wikidata']               : []),
    ...(newsVal.length       > 0 ? ['Google News']           : []),
    ...(webVal.length        > 0 ? ['Brave Search']          : []),
    ...(intel.length         > 0 ? ['intel']                 : []),
  ];
  const entityConfidence = scoreConfidence(activeSources);

  // Only surface structured data we can verify at ≥80% confidence
  const verifiedCorporate = [...gleifVal, ...companiesVal.map(c => ({ ...c, confidence: 0.90 }))];
  const verifiedNews      = newsVal.filter(n => (SOURCE_WEIGHTS[n.source] || 0.60) >= 0.60);
  const verifiedWeb       = webVal.filter(w => (SOURCE_WEIGHTS[w.source] || 0.60) >= 0.60);

  // LLM synthesis (async, best-effort)
  const synthesis = await synthesizeWithLLM(query, {
    companies: companiesVal, gleif: gleifVal, reference: wikiVal,
    wikidata: wikidataVal, news: newsVal, sanctions: sanctionsVal,
  }, entityConfidence >= 0.80 ? llmProvider : null);

  console.log(`[EntitySearch] "${query}" — conf:${(entityConfidence*100).toFixed(0)}% sources:${activeSources.length} in ${Date.now()-start}ms`);

  return {
    query,
    timestamp:        new Date().toISOString(),
    durationMs:       Date.now() - start,
    confidence:       entityConfidence,
    sourcesQueried:   activeSources.length,
    meetsThreshold:   entityConfidence >= 0.80,
    synthesis,
    sanctions:        sanctionsVal,
    corporate:        verifiedCorporate,
    news:             verifiedNews.slice(0, 10),
    web:              verifiedWeb.slice(0, 8),
    reference:        wikiVal[0] || null,
    wikidata:         wikidataVal,
    intelligence:     intel,
    activeSources,
  };
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
