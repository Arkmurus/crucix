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
import { withConcurrency, shouldSkip, recordFailure, recordSuccess } from '../../lib/util/throttle.mjs';
import { enrichFetchError } from '../utils/fetch_error.mjs';

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
  // R-F19 2026-05-01: same UA fix as R-F17 (Arkmurus) and R-F18
  // (DefenseNews). The Googlebot pretender UA gets throttled by Google
  // News from non-Google cloud IPs. Live evidence 09:19:34: Lusophone
  // procurement returned 0 items vs 107 in earlier sweeps — every
  // Portuguese Google News RSS fetch in fetchLusophoneProcurement uses
  // this shared fetchRSS helper. Switch to a stock Chrome desktop UA
  // and bump the direct-fetch timeout 10s → 15s to match.
  const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36';
  const attempts = [
    () => fetch(rssUrl, { headers: { 'User-Agent': USER_AGENT }, signal: AbortSignal.timeout(15000) }),
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
  let attempts = 0, failures = 0;
  for (const q of queries) {
    const url = `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=en-US&gl=US&ceid=US:en`;
    attempts++;
    try {
      const fetched = await fetchRSS(url, 'DSCA/FMS');
      items.push(...fetched);
      if (items.length >= 10) break;
    } catch { failures++; }
  }
  // R-F2722 — if EVERY attempted query ERRORED (not merely returned empty), this is a real
  // failure, not a legitimately-empty result — throw so withTimeout tags 'error', not 'empty'.
  if (items.length === 0 && failures > 0 && failures === attempts) throw new Error(`DSCA: all ${attempts} queries failed`);
  console.log(`[Procurement] DSCA/FMS: ${items.length} items via Google News`);
  return items.slice(0, 10);
}

// ── EU TED: Tenders Electronic Daily ─────────────────────────────────────────
// CPV 35* = defense/security equipment.
// TED API v3 POST → Google News fallback
//
// R-F49 (2026-05-09): API field migration. TED v3 rejected the prior shape
// with HTTP 400 every cycle, silently falling through to Google News and
// reporting "EU TED 0 items" forever. Three corrections:
//   1. query operator: `cpvCode=35*` → `classification-cpv=35*`
//      (verified live: cpvCode now returns "Unknown search field" 400)
//   2. body parameter rename: `pageSize` → `limit` (mirrors R-F7 on the
//      Python-side tender_monitor; field was never propagated here)
//   3. fields list: legacy camelCase names dropped from API; eForms
//      hyphenated names (`notice-title`, `publication-date`,
//      `buyer-country`, `notice-type`) are the supported v3 surface.
//
// Response shape changed correspondingly: top-level kebab-case keys, no
// embedded contracting-authority block (the rich-content fields are
// behind separate fetches now). We display what's directly available
// (title + publication-date + buyer-country + notice-type) and link to
// the canonical ENG XML via `links.xml.MUL` so the human can dig in.
async function fetchEUTED() {
  try {
    const body = {
      query: 'classification-cpv=35*',
      fields: ['notice-title', 'publication-date', 'buyer-country', 'notice-type'],
      page:  1,
      limit: 15,
      scope: 'ACTIVE',
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
          // notice-title can be (a) a string, (b) an object keyed by
          // ISO-639-3 language code (e.g. {ENG: '...', FRA: '...'}),
          // or (c) absent for older notices.
          const rawTitle = n['notice-title'] ?? n.noticeTitle ?? n.title ?? '';
          const title = (typeof rawTitle === 'string'
            ? rawTitle
            : (rawTitle?.ENG || rawTitle?.EN || Object.values(rawTitle || {})[0] || '')
          ).toString().slice(0, 200);
          const country = n['buyer-country'] || n.buyerCountry || '';
          const noticeType = n['notice-type'] || n.noticeType || '';
          const pubNumber = n['publication-number'] || '';
          const pubDate = (n['publication-date'] || n.publicationDate || '').toString().slice(0, 10);
          const xmlLink = n.links?.xml?.MUL || n.permalink || `https://ted.europa.eu/en/notice/${pubNumber}`;
          return {
            source:      'EU TED',
            title:       title || `Defense Tender ${pubNumber || ''} (${country})`.trim(),
            description: [country, noticeType, pubDate].filter(Boolean).join(' · '),
            url:         xmlLink,
            pubDate,
          };
        }).filter(i => (i.title && i.title.length > 5) || i.url);
        console.log(`[Procurement] EU TED: ${items.length} defense notices`);
        return items;
      }
    } else {
      // Surface the actual API response so future schema drift is loud.
      const errBody = await res.text().catch(() => '');
      console.warn(`[Procurement] EU TED v3 HTTP ${res.status} — ${errBody.slice(0, 160)}`);
    }
  } catch (e) {
    console.warn(`[Procurement] EU TED v3 fetch failed: ${enrichFetchError(e)}`);
  }

  // Fallback: Google News for EU defense tenders
  const gnUrl = `https://news.google.com/rss/search?q=${encodeURIComponent('Europe defense security procurement tender contract CPV 35')}&hl=en-US&gl=GB&ceid=GB:en`;
  try {
    const items = await fetchRSS(gnUrl, 'EU TED');
    console.log(`[Procurement] EU TED (Google News fallback): ${items.length} items`);
    return items;
  } catch (e) {
    console.warn(`[Procurement] EU TED failed: ${enrichFetchError(e)}`);
    throw e;  // R-F2722 — TED API failed AND the Google News fallback failed = a real error, not empty
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
  const allFeedsErrored = results.length > 0 && results.every(r => r.status === 'rejected');
  let gnErrored = false;
  // Fall back to Google News only when every direct feed returned empty.
  if (items.length === 0) {
    const gnUrl = `https://news.google.com/rss/search?q=site:defenceweb.co.za+contract+OR+tender+OR+procurement&hl=en-US&gl=ZA&ceid=ZA:en`;
    try {
      const gnItems = await fetchRSS(gnUrl, 'DefenceWeb Africa');
      if (Array.isArray(gnItems)) items.push(...gnItems);
    } catch { gnErrored = true; }
  }
  // R-F2722 — every direct feed ERRORED and the fallback errored → real failure, not empty.
  if (items.length === 0 && allFeedsErrored && gnErrored) throw new Error('DefenceWeb: all feeds + fallback failed');
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
  let attempts = 0, failures = 0;
  for (const q of queries) {
    const url = `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=en-US&gl=US&ceid=US:en`;
    attempts++;
    try {
      const fetched = await fetchRSS(url, 'Africa Defense Procurement');
      items.push(...fetched);
    } catch { failures++; }
  }
  // R-F2722 — every attempted query ERRORED (not merely empty) → real failure, not empty.
  if (items.length === 0 && failures > 0 && failures === attempts) throw new Error(`Africa: all ${attempts} queries failed`);
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
    console.warn(`[Procurement] UN procurement failed: ${enrichFetchError(e)}`);
    throw e;  // R-F2722 — a real fetch error, not a legitimately-empty result
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
    console.warn(`[Procurement] World Bank failed: ${enrichFetchError(e)}`);
    throw e;  // R-F2722 — real error (incl. the !res.ok throw above), not a legitimately-empty result
  }
}

// Per-fetcher hard cap so one slow source can't strangle the whole sweep.
// Each fetcher already has internal per-attempt timeouts, but multiple
// fallback attempts (3 per RSS) compound to ~45s worst-case. This wraps
// each source in a 30s ceiling — the slowest still finishes, the slowest
// failures fail fast, and the sweep completes inside its 90s budget.
// R-F2722 (Codex #11) — tag the resolved ARRAY with its outcome so the classifier can tell a
// real failure (the fetch THREW) or a TIMEOUT apart from a genuinely-empty result. The value
// stays a real Array (every `.value.length` / spread / `.map` / `Array.isArray` consumer keeps
// working) and just carries a non-enumerable `_fetchStatus` ∈ {ok, timeout, error}. This is
// what makes it SAFE to treat a 0-item 'ok' as successful-empty: a 500 now surfaces as 'error',
// not as a hidden empty (avoids the Codex #5 band-aid).
const _tagStatus = (arr, status) => {
  try { Object.defineProperty(arr, '_fetchStatus', { value: status, enumerable: false, configurable: true }); } catch { /* frozen array — ignore */ }
  return arr;
};
export function fetchStatusOf(value) {
  return (value && value._fetchStatus) || 'ok';
}
// R-F2722 — pure, testable per-source classifier (Codex #11). A rejected settle or a fetch that
// THREW is 'failed'; a hard-cap 'timeout' is distinct; a fulfilled fetch is 'ok' (data) or
// 'empty' (genuine successful-empty — NOT a failure, and NOT a hidden error since errors are 'failed').
export function classifyTenderSource(settled) {
  if (!settled || settled.status !== 'fulfilled') return 'failed';
  const st = fetchStatusOf(settled.value);
  if (st === 'error') return 'failed';
  if (st === 'timeout') return 'timeout';
  return (Array.isArray(settled.value) && settled.value.length > 0) ? 'ok' : 'empty';
}
function withTimeout(label, promiseFactory, ms = 30000) {
  return new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      console.warn(`[Procurement] ${label} hit ${ms}ms hard cap — returning empty`);
      resolve(_tagStatus([], 'timeout'));
    }, ms);
    Promise.resolve()
      .then(promiseFactory)
      .then((val) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(_tagStatus(Array.isArray(val) ? val : [], 'ok'));
      })
      .catch((err) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        console.warn(`[Procurement] ${label} failed: ${enrichFetchError(err)}`);
        resolve(_tagStatus([], 'error'));
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
  // R-F21 2026-05-01: cap concurrency at 4 for the 14-feed Lusophone
  // burst. Combined with DefenseNews (13×) and Portals (28×) running
  // in parallel, the previous all-at-once Promise.allSettled produced
  // 60+ simultaneous outbound calls and triggered upstream rate-limit
  // cascades (rss2json 429, corsproxy 503). Per-feed circuit breaker
  // skips a label after 3 consecutive empty returns for 5 minutes.
  const results = await withConcurrency(tasks, async (t) => {
    const ckey = `procurement_lusophone:${t.label}:${t.url.slice(0, 40)}`;
    if (shouldSkip(ckey)) return [];
    const out = await fetchRSS(t.url, t.label);
    if (Array.isArray(out) && out.length > 0) {
      recordSuccess(ckey);
    } else {
      recordFailure(ckey);
    }
    return out;
  }, { concurrency: 4 });

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

function samDate(d) {
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${mm}/${dd}/${d.getUTCFullYear()}`;
}

export async function fetchSAMGovOpportunities(now = new Date()) {
  const apiKey = process.env.SAM_GOV_API_KEY || process.env.SAM_API_KEY || '';
  if (!apiKey) {
    console.log('[Procurement] SAM.gov: disabled_no_key');
    return [];
  }
  const postedTo = new Date(now);
  const postedFrom = new Date(postedTo.getTime() - 14 * 86400 * 1000);
  const params = new URLSearchParams({
    api_key: apiKey,
    postedFrom: samDate(postedFrom),
    postedTo: samDate(postedTo),
    limit: '100',
    offset: '0',
  });
  const res = await fetch(`https://api.sam.gov/opportunities/v2/search?${params.toString()}`, {
    headers: { Accept: 'application/json', 'User-Agent': 'Crucix/1.0' },
    signal: AbortSignal.timeout(20000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    console.warn(`[Procurement] SAM.gov HTTP ${res.status} — ${body.slice(0, 160)}`);
    throw new Error(`SAM.gov HTTP ${res.status}`);  // R-F2722 — a rejected request is a real error, not empty
  }
  const data = await res.json();
  const records = Array.isArray(data?.opportunitiesData) ? data.opportunitiesData : [];
  const items = records.map(r => {
    const title = cleanText(r.title || r.solicitationTitle || '');
    const org = cleanText(r.fullParentPathName || r.organizationName || r.officeAddress?.city || '');
    const type = cleanText(r.type || r.noticeType || '');
    const deadline = cleanText(r.responseDeadLine || r.responseDeadline || '');
    const naics = cleanText(r.naicsCode || '');
    const psc = cleanText(r.classificationCode || '');
    const url = r.uiLink || r.link || (r.noticeId ? `https://sam.gov/opp/${encodeURIComponent(r.noticeId)}/view` : 'https://sam.gov/content/opportunities');
    return {
      source: 'SAM.gov',
      title: title || `SAM.gov opportunity ${r.noticeId || r.solicitationNumber || ''}`.trim(),
      description: [org, type, deadline ? `deadline ${deadline}` : '', naics ? `NAICS ${naics}` : '', psc ? `PSC ${psc}` : '']
        .filter(Boolean)
        .join(' · '),
      url,
      pubDate: r.postedDate || r.archiveDate || '',
    };
  }).filter(i => i.title && i.url);
  console.log(`[Procurement] SAM.gov: ${items.length} official opportunities`);
  return items.slice(0, 30);
}


// ── Main briefing export ─────────────────────────────────────────────────────
export async function briefing() {
  console.log('[Procurement] Fetching live procurement tenders and FMS notifications...');

  const [sam, dsca, ted, dw, africa, un, wb, luso] = await Promise.allSettled([
    withTimeout('SAM.gov',       fetchSAMGovOpportunities,        25000),
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
    ...(sam.status    === 'fulfilled' ? sam.value    : []),
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

  // R-F2722 (Codex #11) — honest per-source status from the fetch-status contract:
  //   error   → 'failed'   (the fetch THREW — a real problem)
  //   timeout → 'timeout'  (hit the hard cap — distinct from a failure or an empty)
  //   ok + data     → 'ok'
  //   ok + 0 items  → 'empty' (genuine successful-empty — a legitimate "no current tenders"
  //                   result). Safe to treat as healthy: a 500 is now 'failed', so 'empty' can
  //                   no longer HIDE an error (the Codex #5 band-aid this avoids).
  const tenderStatus = classifyTenderSource;
  const sourceStatus = {
    'SAM.gov':      sam.status !== 'fulfilled' ? 'failed'
      : (!(process.env.SAM_GOV_API_KEY || process.env.SAM_API_KEY) ? 'disabled_no_key' : tenderStatus(sam)),
    'DSCA/FMS':    tenderStatus(dsca),
    'EU TED':      tenderStatus(ted),
    'DefenceWeb':  tenderStatus(dw),
    'Africa News': tenderStatus(africa),
    'UN':          tenderStatus(un),
    'World Bank':  tenderStatus(wb),
  };

  const okCount = Object.values(sourceStatus).filter(s => s === 'ok').length;
  // R-F2722 — operationally healthy = the source WORKED: data ('ok'), a genuine empty ('empty'),
  // or unconfigured ('disabled_no_key'). Only 'failed' / 'timeout' are real problems.
  const HEALTHY_STATES = new Set(['ok', 'empty', 'disabled_no_key']);
  const healthyCount = Object.values(sourceStatus).filter(s => HEALTHY_STATES.has(s)).length;
  const failedSubs = Object.entries(sourceStatus)
    .filter(([, s]) => !HEALTHY_STATES.has(s))
    .map(([n]) => n);
  console.log(`[Procurement] ${top.length} tenders · ${lusophone.length} Lusophone · ${african.length} African · ${okCount}/${Object.keys(sourceStatus).length} sources live`);

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
        sam:         sam.status     === 'fulfilled' ? sam.value.length     : 0,
        dsca:        dsca.status    === 'fulfilled' ? dsca.value.length    : 0,
        ted:         ted.status     === 'fulfilled' ? ted.value.length     : 0,
        defenceweb:  dw.status      === 'fulfilled' ? dw.value.length      : 0,
        africa:      africa.status  === 'fulfilled' ? africa.value.length  : 0,
        un:          un.status      === 'fulfilled' ? un.value.length      : 0,
        wb:          wb.status      === 'fulfilled' ? wb.value.length      : 0,
      },
    },
    // R-F34: surface sub-source health to the runner. Sub-sources that
    // returned 0 items count as failed — World Bank 500s and Breaking
    // Defense blocks now show up in the top-level "X partial" tally.
    _subStatus: {
      ok:     healthyCount,
      total:  Object.keys(sourceStatus).length,
      failed: failedSubs,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('procurement_tenders.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
