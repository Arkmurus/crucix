// apis/sources/sec_edgar.mjs
// SEC EDGAR — Material event filings for defense, energy, and finance sectors
// Free JSON API, no API key required
// Reference: https://efts.sec.gov / https://data.sec.gov

const EFTS_BASE = 'https://efts.sec.gov/LATEST/search-index';
const DATA_BASE = 'https://data.sec.gov/submissions';

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

// ── Fetch recent 8-K filings via EFTS full-text search ───────────────────────
async function fetchMaterial8Ks() {
  const updates = [];
  const { start, end } = dateRange();

  // Search for material events at watched companies + defense keywords
  const queries = [
    'defense contract "material definitive"',
    '"cybersecurity incident" OR "data breach"',
    '"executive officer" departure resignation',
  ];

  for (const q of queries) {
    try {
      const params = new URLSearchParams({
        q:          q,
        forms:      '8-K',
        dateRange:  'custom',
        startdt:    start,
        enddt:      end,
      });
      const res = await fetch(`${EFTS_BASE}?${params}`, {
        headers: { 'User-Agent': 'CrucixIntelligence/1.0 research@crucix.live' },
        signal: AbortSignal.timeout(12000),
      });
      if (!res.ok) continue;
      const data = await res.json();
      const hits  = data.hits?.hits || [];

      for (const hit of hits.slice(0, 6)) {
        const src     = hit._source || {};
        const company = src.display_names?.[0] || src.entity_name || 'Unknown';
        const form    = src.form_type || '8-K';
        const filed   = src.file_date || src.period_of_report || '';
        const desc    = src.file_description || '';
        const accNum  = hit._id?.replace(/:/g, '-') || '';

        // Check if it's a watched company or contains material keywords
        const isWatched = Object.values(WATCH_COMPANIES).some(name =>
          company.toLowerCase().includes(name.toLowerCase().split(' ')[0])
        );
        const isMaterial = MATERIAL_KEYWORDS.some(kw =>
          (company + ' ' + desc).toLowerCase().includes(kw)
        );
        if (!isWatched && !isMaterial) continue;

        updates.push({
          title:    `${form}: ${company} — ${desc || 'Material Event'}`,
          company,
          form,
          filed,
          url:      accNum ? `https://www.sec.gov/Archives/edgar/data/${accNum.split('-')[0]}/${accNum.replace(/-/g,'')}/` : 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K',
          source:   'SEC EDGAR',
          type:     'sec_filing',
          priority: desc.toLowerCase().includes('cyber') ? 'high' :
                    isWatched ? 'high' : 'medium',
        });
      }
    } catch (e) {
      console.warn('[EDGAR] Query failed:', e.message);
    }
  }

  return updates;
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
