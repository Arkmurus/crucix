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
// defenceweb.co.za is the primary Africa defence news source; fetch directly first
async function fetchDefenceWeb() {
  const feeds = [
    'https://www.defenceweb.co.za/feed/',
    'https://www.defenceweb.co.za/category/industry/tenders/feed/',
    'https://www.defenceweb.co.za/category/joint/joint-procurement/feed/',
  ];
  const items = [];
  for (const feedUrl of feeds) {
    const fetched = await fetchRSS(feedUrl, 'DefenceWeb Africa');
    items.push(...fetched);
    if (items.length >= 20) break;
  }
  // Also try Google News if direct feeds fail
  if (items.length === 0) {
    const gnUrl = `https://news.google.com/rss/search?q=site:defenceweb.co.za+contract+OR+tender+OR+procurement&hl=en-US&gl=ZA&ceid=ZA:en`;
    const gnItems = await fetchRSS(gnUrl, 'DefenceWeb Africa');
    items.push(...gnItems);
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
  // WB search API for procurement notices in Africa
  const url = 'https://search.worldbank.org/api/v2/procnotices?format=json&rows=25&regioncode_exact=AFR&fq=notice_type%3A%22REQUEST+FOR+BIDS%22+OR+notice_type%3A%22REQUEST+FOR+PROPOSALS%22+OR+notice_type%3A%22INVITATION+FOR+BIDS%22';
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Crucix/1.0', 'Accept': 'application/json' },
      signal: AbortSignal.timeout(12000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // WB API returns nested: data.procnotices.rows[]
    const container = data.procnotices || data;
    const rows = Array.isArray(container.rows) ? container.rows
               : Array.isArray(container)      ? container
               : Object.values(container).find(Array.isArray) || [];

    const PRIORITY = ['angola', 'mozambique', 'guinea-bissau', 'cape verde', 'guinea', 'senegal', 'mali', 'niger', 'burkina faso', 'nigeria', 'ethiopia', 'somalia', 'congo', 'ghana', 'côte d\'ivoire', 'cote d\'ivoire'];

    const items = rows
      .filter(n => {
        const country = (n.countryshortname || n.country_name || '').toLowerCase();
        const desc    = (n.project_name || n.notice_title || '').toLowerCase();
        return PRIORITY.some(p => country.includes(p)) || DEFENSE_KW.some(kw => desc.includes(kw));
      })
      .slice(0, 10)
      .map(n => ({
        source:      'World Bank',
        title:       `[${n.countryshortname || n.country_name || 'Africa'}] ${n.project_name || n.notice_title || 'Procurement Notice'}`.slice(0, 200),
        description: `${n.notice_type || 'Notice'} · Deadline: ${n.contact_deadline || n.deadline || 'TBD'}`,
        url:         n.project_url || 'https://projects.worldbank.org/en/projects-operations/procurement',
        pubDate:     n.contact_deadline || '',
      }));

    console.log(`[Procurement] World Bank: ${items.length} Africa notices`);
    return items;
  } catch (e) {
    console.warn(`[Procurement] World Bank failed: ${e.message}`);
    return [];
  }
}

// ── Main briefing export ─────────────────────────────────────────────────────
export async function briefing() {
  console.log('[Procurement] Fetching live procurement tenders and FMS notifications...');

  const [dsca, ted, dw, africa, un, wb] = await Promise.allSettled([
    fetchDSCA(),
    fetchEUTED(),
    fetchDefenceWeb(),
    fetchAfricaDefenseProcurement(),
    fetchUNProcurement(),
    fetchWorldBankProcurement(),
  ]);

  const allItems = [
    ...(dsca.status   === 'fulfilled' ? dsca.value   : []),
    ...(ted.status    === 'fulfilled' ? ted.value    : []),
    ...(dw.status     === 'fulfilled' ? dw.value     : []),
    ...(africa.status === 'fulfilled' ? africa.value : []),
    ...(un.status     === 'fulfilled' ? un.value     : []),
    ...(wb.status     === 'fulfilled' ? wb.value     : []),
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
