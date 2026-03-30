// apis/sources/sec_edgar.mjs
// SEC EDGAR — Material event filings for defense, energy, and finance sectors
// Free JSON API, no API key required
// Reference: https://efts.sec.gov / https://data.sec.gov

// EFTS full-text search is unreliable on cloud IPs — dropped in favour of stable endpoints
const DATA_BASE  = 'https://data.sec.gov/submissions';
const EDGAR_RSS  = 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=20&search_text=&output=atom';
const RSS2JSON   = 'https://api.rss2json.com/v1/api.json?rss_url=';

// Defense, aerospace, and critical infrastructure companies to monitor (CIK → name)
const WATCH_COMPANIES = {
  '0000040533':  'General Dynamics',
  '0000936468':  'Lockheed Martin',
  '0000101829':  'RTX (Raytheon)',
  '0001133421':  'Northrop Grumman',
  '0000202058':  'L3Harris Technologies',
  '0000012927':  'Boeing',
  '0000217346':  'Leidos',
  '0001336920':  'Booz Allen Hamilton',
  '0001590895':  'CACI International',
  '0000093676':  'ExxonMobil',
  '0000023632':  'Chevron',
  '0000019617':  'JPMorgan Chase',
  '0000070858':  'Goldman Sachs',
};

// Keywords that indicate material intelligence value in 8-K filings
const MATERIAL_KEYWORDS = [
  'defense contract', 'government contract', 'department of defense', 'dod',
  'material agreement', 'executive departure', 'resignation', 'termination',
  'sanctions', 'export control', 'debarment', 'investigation',
  'cybersecurity incident', 'data breach', 'ransomware',
  'acquisition', 'merger', 'joint venture', 'strategic',
  'force majeure', 'supply disruption', 'operational disruption',
];

// Get today and 3 days ago formatted for EDGAR
function dateRange() {
  const end   = new Date();
  const start = new Date(end.getTime() - 3 * 24 * 60 * 60 * 1000);
  return {
    start: start.toISOString().split('T')[0],
    end:   end.toISOString().split('T')[0],
  };
}

// ── Fetch recent 8-K filings via SEC EDGAR RSS (stable, works on cloud IPs) ───
async function fetchMaterial8Ks() {
  const updates = [];
  const watchNames = Object.values(WATCH_COMPANIES).map(n => n.toLowerCase().split(' ')[0]);

  // Try direct SEC RSS first, then rss2json proxy
  let items = [];
  try {
    const res = await fetch(EDGAR_RSS, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0 research@crucix.live' },
      signal: AbortSignal.timeout(12000),
    });
    if (res.ok) items = parseAtomFeed(await res.text());
  } catch {}

  if (items.length === 0) {
    try {
      const res = await fetch(RSS2JSON + encodeURIComponent(EDGAR_RSS), { signal: AbortSignal.timeout(12000) });
      if (res.ok) {
        const d = await res.json();
        if (d.status === 'ok') items = (d.items || []).map(i => ({ title: i.title || '', link: i.link || '', company: i.author || '', date: i.pubDate || '' }));
      }
    } catch {}
  }

  for (const item of items.slice(0, 30)) {
    const company = item.company || item.title?.split(' - ')?.[0] || '';
    const desc    = item.title || '';
    const isWatched  = watchNames.some(n => company.toLowerCase().includes(n));
    const isMaterial = MATERIAL_KEYWORDS.some(kw => (company + ' ' + desc).toLowerCase().includes(kw));
    if (!isWatched && !isMaterial) continue;
    updates.push({
      title:    `8-K: ${company} — ${desc.replace(/^.*?8-K\s*[-–]\s*/i,'').substring(0,120)}`,
      company,
      form:     '8-K',
      filed:    item.date || '',
      url:      item.link || 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K',
      source:   'SEC EDGAR',
      type:     'sec_filing',
      priority: desc.toLowerCase().includes('cyber') ? 'high' : isWatched ? 'high' : 'medium',
    });
  }

  console.log(`[EDGAR] RSS: ${updates.length} material 8-Ks`);
  return updates;
}

function parseAtomFeed(xml) {
  const items = [];
  const re = /<entry>([\s\S]*?)<\/entry>/gi;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const b = m[1];
    const link = b.match(/href="([^"]+)"/)?.[1] || '';
    const title = extractTag(b,'title');
    const date  = extractTag(b,'updated') || extractTag(b,'published');
    const company = title.split(' - ')?.[0] || '';
    items.push({ title, link, company, date });
  }
  return items;
}

function extractTag(xml, tag) {
  const m = xml.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>|<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  return m ? (m[1]||m[2]||'').replace(/<[^>]+>/g,'').trim() : '';
}

// ── Fetch recent filings for specific watched companies ───────────────────────
async function fetchWatchedCompanies() {
  const updates = [];
  const { start } = dateRange();
  const ciks = Object.keys(WATCH_COMPANIES).slice(0, 6); // limit to avoid rate limits

  for (const cik of ciks) {
    try {
      const res = await fetch(`${DATA_BASE}/CIK${cik}.json`, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0 research@crucix.live' },
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) continue;
      const data = await res.json();

      const filings = data.filings?.recent;
      if (!filings) continue;

      const forms  = filings.form        || [];
      const dates  = filings.filingDate  || [];
      const accNums = filings.accessionNumber || [];
      const descs  = filings.primaryDocument || [];
      const name   = WATCH_COMPANIES[cik];

      for (let i = 0; i < forms.length; i++) {
        if (forms[i] !== '8-K') continue;
        if (dates[i] < start) break; // filings are sorted newest-first

        updates.push({
          title:    `8-K: ${name} — Material Event (${dates[i]})`,
          company:  name,
          form:     '8-K',
          filed:    dates[i],
          url:      `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${cik}&type=8-K&dateb=&owner=include&count=5`,
          source:   'SEC EDGAR',
          type:     'sec_filing',
          priority: 'high',
        });
      }
    } catch {}
  }

  return updates;
}

// ── Main briefing ─────────────────────────────────────────────────────────────
export async function briefing() {
  console.log('[SEC EDGAR] Fetching material filings...');

  const [material, watched] = await Promise.allSettled([
    fetchMaterial8Ks(),
    fetchWatchedCompanies(),
  ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : []));

  // Merge and deduplicate by title
  const seen   = new Set();
  const updates = [];
  for (const u of [...material, ...watched]) {
    if (!seen.has(u.title)) {
      seen.add(u.title);
      updates.push(u);
    }
  }

  const signals = updates
    .filter(u => u.priority === 'high')
    .map(u => `SEC 8-K: ${u.company} — ${u.title.replace(/^8-K: [^—]+ — /, '')}`);

  console.log(`[SEC EDGAR] ${updates.length} material filings · ${signals.length} high-priority`);

  return {
    source:    'SEC EDGAR',
    timestamp: new Date().toISOString(),
    updates,
    signals,
    counts: {
      updates:  updates.length,
      signals:  signals.length,
      watched:  watched.length,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('sec_edgar.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
