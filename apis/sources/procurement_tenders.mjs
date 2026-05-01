// apis/sources/procurement_tenders.mjs
// Live defense procurement tenders and arms sale notifications
// Sources:
//   - DSCA FMS via Google News RSS (dsca.mil blocks proxies — GNews is reliable)
//   - EU TED defense tenders API (POST, open)
//   - Africa defense procurement via Google News RSS
//   - UN peacekeeping procurement via Google News RSS
//   - World Bank procurement API (priority countries)
//
// Arkmurus focus: Lusophone Africa, African Union missions, export-control-compliant deals

import '../utils/env.mjs';

const RSS2JSON   = 'https://api.rss2json.com/v1/api.json?rss_url=';
const ALLORIGINS = 'https://api.allorigins.win/get?url=';

// ── Priority filters ─────────────────────────────────────────────────────────
const LUSOPHONE  = ['angola', 'mozambique', 'guinea-bissau', 'guinea bissau', 'cape verde', 'são tomé', 'sao tome', 'lusophone'];
const AFRICA_KW  = ['africa', 'sadc', 'ecowas', 'au mission', 'amisom', 'minusma', 'monusco', 'unmiss', 'african', 'mali', 'niger', 'burkina', 'senegal', 'nigeria', 'ethiopia', 'somalia', 'drc', 'congo', 'angola', 'mozambique'];
const DEFENSE_KW = ['defense', 'defence', 'military', 'security', 'arms', 'weapon', 'ammunition', 'munition', 'aircraft', 'helicopter', 'vessel', 'patrol', 'armored', 'armoured', 'vehicle', 'radar', 'drone', 'uav', 'missile', 'rifle', 'frigate', 'corvette', 'coast guard', 'gendarmerie'];
const PROC_KW    = ['tender', 'procurement', 'contract', 'rfp', 'rfq', 'invitation to bid', 'solicitation', 'award', 'sale notification', 'fms', 'arms sale', 'acquisition', 'purchase'];

function score(title, desc) {
  const text = `${title} ${desc}`.toLowerCase();
  let s = 0;
  for (const kw of LUSOPHONE)  if (text.includes(kw)) s += 15;
  for (const kw of AFRICA_KW)  if (text.includes(kw)) s += 6;
  for (const kw of DEFENSE_KW) if (text.includes(kw)) s += 3;
  for (const kw of PROC_KW)    if (text.includes(kw)) s += 2;
  return s;
}

function decodeEntities(str) {
  return str
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&apos;/g, "'")
    .replace(/&nbsp;/g, ' ');
}

function cleanText(raw) {
  return decodeEntities(raw)
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
    const b    = m[1];
    const title = cleanText(b.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i)?.[1] || '');
    const link  = b.match(/<link[^>]*href="([^"]+)"/i)?.[1]
               || b.match(/<link[^>]*>([\s\S]*?)<\/link>/i)?.[1]?.trim() || '';
    const date  = b.match(/<pubDate>([\s\S]*?)<\/pubDate>/i)?.[1]
               || b.match(/<published>([\s\S]*?)<\/published>/i)?.[1] || '';
    const rawDesc = b.match(/<description[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/description>/i)?.[1]
                 || b.match(/<summary[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/summary>/i)?.[1] || '';
    const desc = cleanText(rawDesc).slice(0, 300);
    if (title.length > 5) {
      items.push({ source: sourceName, title, description: desc, url: link.trim(), pubDate: date });
    }
    if (items.length >= 15) break;
  }
  return items;
}

async function fetchRSS(rssUrl, sourceName) {
  const attempts = [
    () => fetch(rssUrl, { headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' }, signal: AbortSignal.timeout(10000) }),
    () => fetch(RSS2JSON + encodeURIComponent(rssUrl), { signal: AbortSignal.timeout(12000) }),
    () => fetch(ALLORIGINS + encodeURIComponent(rssUrl), { signal: AbortSignal.timeout(14000) }),
  ];
  for (const attempt of attempts) {
    try {
      const res = await attempt();
      if (!res.ok) continue;
      const text = await res.text();
      if (!text || text.length < 50) continue;
      if (text.trim().startsWith('{')) {
        let data;
        try { data = JSON.parse(text); } catch { continue; }
        if (data.contents) return parseXML(data.contents, sourceName);
        if (data.status === 'ok' && data.items?.length) {
          return data.items.map(i => ({
            source:      sourceName,
            title:       cleanText(i.title || ''),
            description: cleanText(i.description || i.content || '').slice(0, 300),
            url:         i.link || '',
            pubDate:     i.pubDate || '',
          }));
        }
        continue;
      }
      if (text.includes('<item>') || text.includes('<entry>')) return parseXML(text, sourceName);
    } catch {}
  }
  return [];
}

// ── DSCA: US Foreign Military Sales — via Google News RSS ────────────────────
// dsca.mil blocks all proxy/CDN IPs. Google News search RSS is reliable.
async function fetchDSCA() {
  const queries = [
    'DSCA "foreign military sale" OR "arms sale approval"',
    'DSCA Africa military sale',
  ];
  const items = [];
  for (const q of queries) {
    const url = `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=en-US&gl=US&ceid=US:en`;
    try {
      const fetched = await fetchRSS(url, 'DSCA/FMS');
      items.push(...fetched);
      if (items.length >= 10) break;
    } catch {}
  }
  console.log(`[Procurement] DSCA/FMS: ${items.length} items via Google News`);
  return items.slice(0, 10);
}

// ── EU TED: Tenders Electronic Daily ─────────────────────────────────────────
// CPV 35* = defense/security equipment.
// TED API v3 POST → TED OData GET → Google News fallback
async function fetchEUTED() {
  // Attempt 1: TED API v3 POST (corrected query syntax)
  try {
    const body = {
      query:    'cpvCode=35*',
      fields:   ['title', 'publicationDate', 'contractingAuthorityName', 'buyerCountry', 'totalValue', 'cpv', 'noticeType'],
      page:     1,
      pageSize: 15,
      scope:    'ACTIVE',
    };
    const res = await fetch('https://api.ted.europa.eu/v3/notices/search', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'Crucix/1.0' },
      body:    JSON.stringify(body),
      signal:  AbortSignal.timeout(15000),
    });
    if (res.ok) {
      const data = await res.json();
      const notices = data.notices || data.results || data.items || [];
      if (notices.length > 0) {
        const items = notices.map(n => {
          const title   = (Array.isArray(n.title) ? n.title.find(t => t.language === 'EN')?.value : (n.title?.EN || n.title || '')).toString().slice(0, 200);
          const country = n.buyerCountry || '';
          const authority = (Array.isArray(n.contractingAuthorityName)
            ? n.contractingAuthorityName.find(a => a.language === 'EN')?.value
            : n.contractingAuthorityName) || '';
          const value   = n.totalValue?.amount ? `€${(n.totalValue.amount / 1e6).toFixed(1)}M` : '';
          return {
            source:      'EU TED',
            title:       title || `Defense Tender (${country})`,
            description: `${authority} · ${country}${value ? ' · ' + value : ''}`.replace(/^·\s*|·\s*$/, '').trim(),
            url:         n.permalink || `https://ted.europa.eu`,
            pubDate:     n.publicationDate || '',
          };
        }).filter(i => i.title && i.title.length > 5);
        console.log(`[Procurement] EU TED: ${items.length} defense notices`);
        return items;
      }
    }
  } catch {}

  // Fallback: Google News for EU defense tenders
  const gnUrl = `https://news.google.com/rss/search?q=${encodeURIComponent('Europe defense security procurement tender contract CPV 35')}&hl=en-US&gl=GB&ceid=GB:en`;
  try {
    const items = await fetchRSS(gnUrl, 'EU TED');
    console.log(`[Procurement] EU TED (Google News fallback): ${items.length} items`);
    return items;
  } catch (e) {
    console.warn(`[Procurement] EU TED failed: ${e.message}`);
    return [];
  }
}

// ── DefenceWeb Africa: direct RSS + Google News ───────────────────────────────
// defenceweb.co.za is the primary Africa defence news source; fetch directly first.
// Previously these 3 feeds were fetched serially — any one slow feed (with 3
// internal fallback attempts per fetch) could blow the 30s hard cap, and the
// outer withTimeout then discarded everything already accumulated. Parallel
// allSettled bounds the wall time to max(per-feed timeout) instead of sum.
async function fetchDefenceWeb() {
  const feeds = [
    'https://www.defenceweb.co.za/feed/',
    'https://www.defenceweb.co.za/category/industry/tenders/feed/',
    'https://www.defenceweb.co.za/category/joint/joint-procurement/feed/',
  ];
  const results = await Promise.allSettled(
    feeds.map(url => fetchRSS(url, 'DefenceWeb Africa'))
  );
  const items = [];
  for (const r of results) {
    if (r.status === 'fulfilled' && Array.isArray(r.value)) items.push(...r.value);
  }
  // Fall back to Google News only when every direct feed returned empty.
  if (items.length === 0) {
    const gnUrl = `https://news.google.com/rss/search?q=site:defenceweb.co.za+contract+OR+tender+OR+procurement&hl=en-US&gl=ZA&ceid=ZA:en`;
    try {
      const gnItems = await fetchRSS(gnUrl, 'DefenceWeb Africa');
      if (Array.isArray(gnItems)) items.push(...gnItems);
    } catch {}
  }
  console.log(`[Procurement] DefenceWeb: ${items.length} items`);
  return items.slice(0, 15);
}

// ── Africa defense procurement — Google News RSS ─────────────────────────────
async function fetchAfricaDefenseProcurement() {
  const queries = [
    '"Africa" defense procurement contract 2026',
    'Angola OR Mozambique military procurement contract',
    'SADC OR "African Union" defense acquisition 2026',
    'breakingdefense.com Africa OR Angola OR Mozambique',
  ];
  const items = [];
  for (const q of queries) {
    const url = `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=en-US&gl=US&ceid=US:en`;
    try {
      const fetched = await fetchRSS(url, 'Africa Defense Procurement');
      items.push(...fetched);
    } catch {}
  }
  // Deduplicate
  const seen = new Set();
  const unique = [];
  for (const i of items) {
    const k = i.title.toLowerCase().slice(0, 50);
    if (!seen.has(k)) { seen.add(k); unique.push(i); }
  }
  console.log(`[Procurement] Africa procurement: ${unique.length} items`);
  return unique.slice(0, 15);
}

// ── UN peacekeeping procurement — Google News RSS ─────────────────────────────
async function fetchUNProcurement() {
  const url = `https://news.google.com/rss/search?q=${encodeURIComponent('UN peacekeeping procurement contract equipment Africa')}&hl=en-US&gl=US&ceid=US:en`;
  try {
    const items = await fetchRSS(url, 'UN Procurement');
    console.log(`[Procurement] UN: ${items.length} items`);
    return items.slice(0, 8);
  } catch (e) {
    console.warn(`[Procurement] UN procurement failed: ${e.message}`);
    return [];
  }
}

// ── World Bank procurement notices — Africa region ────────────────────────────
async function fetchWorldBankProcurement() {
  // WB search API for procurement notices in Africa. Two drifts the old
  // query did not survive:
  //   1. Schema rename — `countryshortname` → `project_ctry_name`;
  //      `notice_title` no longer present; use `project_name` +
  //      `bid_description` as descriptors.
  //   2. Notice-type filter excluded "Contract Award" which is the
  //      most common (and load-bearing for BD signal). Dropped the `fq`
  //      and let the Africa + keyword filter do the work client-side —
  //      robust against future WB notice-type renames.
  const url = 'https://search.worldbank.org/api/v2/procnotices?format=json&rows=50&regioncode_exact=AFR';
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Crucix/1.0', 'Accept': 'application/json' },
      signal: AbortSignal.timeout(12000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // WB API returns { rows: <count>, procnotices: [...] } at the top
    // level (NOT `data.procnotices.rows`). Defensive fall-through covers
    // both layouts in case they revert.
    const rows = Array.isArray(data.procnotices) ? data.procnotices
               : Array.isArray(data.procnotices?.rows) ? data.procnotices.rows
               : Array.isArray(data.rows) ? data.rows
               : Object.values(data).find(Array.isArray) || [];

    const PRIORITY = [
      'angola', 'mozambique', 'guinea-bissau', 'cape verde', 'guinea',
      'senegal', 'mali', 'niger', 'burkina faso', 'nigeria', 'ethiopia',
      'somalia', 'congo', 'ghana', "côte d'ivoire", "cote d'ivoire",
    ];

    const items = rows
      .filter(n => {
        const country = (n.project_ctry_name || n.countryshortname || n.country_name || '').toLowerCase();
        const descriptors = `${n.project_name || ''} ${n.bid_description || ''} ${n.notice_title || ''}`.toLowerCase();
        return PRIORITY.some(p => country.includes(p)) || DEFENSE_KW.some(kw => descriptors.includes(kw));
      })
      .slice(0, 10)
      .map(n => {
        const ctry = n.project_ctry_name || n.countryshortname || n.country_name || 'Africa';
        const name = n.project_name || n.bid_description || n.notice_title || 'Procurement Notice';
        const deadline = n.contact_deadline || n.bid_deadline_date || n.deadline || '';
        return {
          source:      'World Bank',
          title:       `[${ctry}] ${name}`.slice(0, 200),
          description: `${n.notice_type || 'Notice'}${deadline ? ` · Deadline: ${deadline}` : ''}`,
          url:         n.project_url || n.bid_reference_url || 'https://projects.worldbank.org/en/projects-operations/procurement',
          pubDate:     n.noticedate || deadline || '',
        };
      });

    console.log(`[Procurement] World Bank: ${items.length} Africa notices (from ${rows.length} scanned)`);
    return items;
  } catch (e) {
    console.warn(`[Procurement] World Bank failed: ${e.message}`);
    return [];
  }
}

// Per-fetcher hard cap so one slow source can't strangle the whole sweep.
// Each fetcher already has internal per-attempt timeouts, but multiple
// fallback attempts (3 per RSS) compound to ~45s worst-case. This wraps
// each source in a 30s ceiling — the slowest still finishes, the slowest
// failures fail fast, and the sweep completes inside its 90s budget.
function withTimeout(label, promiseFactory, ms = 30000) {
  return new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      console.warn(`[Procurement] ${label} hit ${ms}ms hard cap — returning empty`);
      resolve([]);
    }, ms);
    Promise.resolve()
      .then(promiseFactory)
      .then((val) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(Array.isArray(val) ? val : []);
      })
      .catch((err) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        console.warn(`[Procurement] ${label} failed: ${err.message}`);
        resolve([]);
      });
  });
}

// ── Lusophone procurement — direct Portuguese sources for incumbent advantage ─
// Hits Portuguese-language news + government portals that English Google News
// systematically misses. This is the source set that protects Arkmurus's
// Lusophone Africa moat — without it the BD Brain has no visibility into
// Angolan/Mozambican procurement until 7+ days after announcement.
async function fetchLusophoneProcurement() {
  // 14 serial fetches (8 PT + 2 BR + 4 direct feeds) were blowing the
  // 30s outer cap on most sweeps because each fetchRSS has up to 3
  // internal fallback attempts. Wall-clock was sum-of-all-latencies.
  // Now parallel allSettled: wall-clock is max(per-fetch), and any
  // slow individual fetch no longer prevents accumulation of the
  // faster ones.

  // 1. Direct Portuguese-language Google News (PT-BR + PT-PT + Lusophone Africa)
  const ptQueries = [
    // Angola
    'Angola contrato defesa OR aquisição militar 2026',
    'Angola FAA "Forças Armadas" equipamento',
    'Angola SIMPORTEX importação defesa',
    // Mozambique
    'Moçambique FADM contrato defesa OR aquisição',
    'Moçambique Cabo Delgado equipamento militar',
    // Cape Verde / Guinea-Bissau
    'Cabo Verde defesa equipamento aquisição',
    'Guiné-Bissau FARP defesa contrato',
    // CPLP defence cooperation
    'CPLP cooperação defesa militar',
  ];
  const brQueries = [
    'Brasil exportação defesa Angola OR Moçambique',
    'Brasil cooperação militar CPLP',
  ];
  const directFeeds = [
    { url: 'https://clubofmozambique.com/feed/', name: 'Club of Mozambique' },
    { url: 'https://www.angop.ao/rss', name: 'ANGOP (Angola)' },
    { url: 'https://noticias.sapo.cv/feed', name: 'Cabo Verde / Sapo' },
    { url: 'https://www.dn.pt/rss', name: 'Diário de Notícias (PT)' },
  ];

  // Build all 14 fetch promises. Direct feeds carry a `filter` flag so
  // we can skip non-defence news in the merge step.
  const tasks = [
    ...ptQueries.map(q => ({
      kind: 'rss',
      label: 'Lusophone (PT)',
      url: `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=pt-PT&gl=PT&ceid=PT:pt`,
      filter: false,
    })),
    ...brQueries.map(q => ({
      kind: 'rss',
      label: 'Lusophone (BR)',
      url: `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=pt-BR&gl=BR&ceid=BR:pt`,
      filter: false,
    })),
    ...directFeeds.map(f => ({
      kind: 'rss',
      label: f.name,
      url: f.url,
      filter: true,
    })),
  ];

  const DEFENCE_FILTER = /(defesa|defense|militar|military|forças armadas|exército|marinha|armas|"contrato"|aquisição|tender|licitação)/i;
  const results = await Promise.allSettled(
    tasks.map(t => fetchRSS(t.url, t.label))
  );

  const items = [];
  results.forEach((r, i) => {
    if (r.status !== 'fulfilled' || !Array.isArray(r.value)) return;
    const source = tasks[i];
    const fetched = source.filter
      ? r.value.filter(it => DEFENCE_FILTER.test(`${it.title || ''} ${it.description || ''}`))
      : r.value;
    items.push(...fetched);
  });

  // Dedup
  const seen = new Set();
  const unique = [];
  for (const i of items) {
    const k = (i.title || '').toLowerCase().slice(0, 60);
    if (k && !seen.has(k)) { seen.add(k); unique.push(i); }
  }
  console.log(`[Procurement] Lusophone: ${unique.length} items (incumbent advantage feeds)`);
  return unique.slice(0, 25);
}


// ── Main briefing export ─────────────────────────────────────────────────────
export async function briefing() {
  console.log('[Procurement] Fetching live procurement tenders and FMS notifications...');

  const [dsca, ted, dw, africa, un, wb, luso] = await Promise.allSettled([
    withTimeout('DSCA',          fetchDSCA,                       25000),
    withTimeout('EU TED',        fetchEUTED,                      30000),
    withTimeout('DefenceWeb',    fetchDefenceWeb,                 30000),
    withTimeout('Africa procurement', fetchAfricaDefenseProcurement, 30000),
    withTimeout('UN procurement',     fetchUNProcurement,             20000),
    withTimeout('World Bank',    fetchWorldBankProcurement,       25000),
    // R-F14 2026-05-01: bump 30s → 45s. Live evidence 08:45:27: Lusophone
    // timed out at 30s but the data actually arrived at 08:45:39 (~42s).
    // 14 parallel feeds, slowest fallback chain can hit the high-30s.
    // Budget is plenty (outer sweep cap is 90s); losing 182 updates/sweep
    // for the Lusophone moat is worse than the extra 15s of sweep time.
    withTimeout('Lusophone',     fetchLusophoneProcurement,       45000),
  ]);

  const allItems = [
    ...(dsca.status   === 'fulfilled' ? dsca.value   : []),
    ...(ted.status    === 'fulfilled' ? ted.value    : []),
    ...(dw.status     === 'fulfilled' ? dw.value     : []),
    ...(africa.status === 'fulfilled' ? africa.value : []),
    ...(un.status     === 'fulfilled' ? un.value     : []),
    ...(wb.status     === 'fulfilled' ? wb.value     : []),
    ...(luso.status   === 'fulfilled' ? luso.value   : []),
  ];

  const scored = allItems.map(i => ({
    ...i,
    relevanceScore: score(i.title, i.description),
  })).sort((a, b) => b.relevanceScore - a.relevanceScore);

  const seen   = new Set();
  const unique = [];
  for (const item of scored) {
    const key = item.title.toLowerCase().slice(0, 60);
    if (!seen.has(key)) { seen.add(key); unique.push(item); }
  }

  const top       = unique.slice(0, 30);
  const lusophone = top.filter(i => LUSOPHONE.some(kw  => `${i.title} ${i.description}`.toLowerCase().includes(kw)));
  const african   = top.filter(i => AFRICA_KW.some(kw  => `${i.title} ${i.description}`.toLowerCase().includes(kw)));

  const updates = top.map(i => ({
    title:    `[${i.source}] ${i.title}`,
    source:   i.source,
    content:  i.description,
    url:      i.url,
    timestamp: i.pubDate ? new Date(i.pubDate).getTime() || Date.now() : Date.now(),
    priority: i.relevanceScore >= 15 ? 'high' : i.relevanceScore >= 6 ? 'medium' : 'normal',
    type:     'procurement_tender',
  }));

  const signals = [
    ...lusophone.slice(0, 3).map(i => ({
      text:     `[Tender/Lusophone] ${i.source}: ${i.title.slice(0, 120)}`,
      source:   i.source,
      priority: 'high',
    })),
    ...african.filter(i => !lusophone.includes(i)).slice(0, 3).map(i => ({
      text:     `[Tender/Africa] ${i.source}: ${i.title.slice(0, 120)}`,
      source:   i.source,
      priority: 'medium',
    })),
  ];

  // Build full item records for lusophone/africa sub-arrays (used by BD intelligence)
  const lusiItems = lusophone.map(i => ({
    title:     i.title,
    content:   i.description,
    text:      i.description,
    source:    i.source,
    url:       i.url,
    link:      i.url,
    timestamp: i.pubDate ? new Date(i.pubDate).getTime() || Date.now() : Date.now(),
  }));
  const africaItems = african.filter(i => !lusophone.includes(i)).map(i => ({
    title:     i.title,
    content:   i.description,
    text:      i.description,
    source:    i.source,
    url:       i.url,
    link:      i.url,
    timestamp: i.pubDate ? new Date(i.pubDate).getTime() || Date.now() : Date.now(),
  }));

  const sourceStatus = {
    'DSCA/FMS':    dsca.status    === 'fulfilled' && dsca.value.length    > 0 ? 'ok' : 'failed',
    'EU TED':      ted.status     === 'fulfilled' && ted.value.length     > 0 ? 'ok' : 'failed',
    'DefenceWeb':  dw.status      === 'fulfilled' && dw.value.length      > 0 ? 'ok' : 'failed',
    'Africa News': africa.status  === 'fulfilled' && africa.value.length  > 0 ? 'ok' : 'failed',
    'UN':          un.status      === 'fulfilled' && un.value.length      > 0 ? 'ok' : 'failed',
    'World Bank':  wb.status      === 'fulfilled' && wb.value.length      > 0 ? 'ok' : 'failed',
  };

  const okCount = Object.values(sourceStatus).filter(s => s === 'ok').length;
  console.log(`[Procurement] ${top.length} tenders · ${lusophone.length} Lusophone · ${african.length} African · ${okCount}/6 sources OK`);

  return {
    source:    'Procurement Tenders',
    timestamp: new Date().toISOString(),
    updates,
    signals,
    lusophone: lusiItems,   // full item records for BD intelligence
    africa:    africaItems, // full item records for BD intelligence
    sourceStatus,
    counts: {
      total:     top.length,
      lusophone: lusophone.length,
      african:   african.length,
      bySource: {
        dsca:        dsca.status    === 'fulfilled' ? dsca.value.length    : 0,
        ted:         ted.status     === 'fulfilled' ? ted.value.length     : 0,
        defenceweb:  dw.status      === 'fulfilled' ? dw.value.length      : 0,
        africa:      africa.status  === 'fulfilled' ? africa.value.length  : 0,
        un:          un.status      === 'fulfilled' ? un.value.length      : 0,
        wb:          wb.status      === 'fulfilled' ? wb.value.length      : 0,
      },
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('procurement_tenders.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
