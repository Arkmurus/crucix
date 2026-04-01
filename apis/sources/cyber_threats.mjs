// apis/sources/cyber_threats.mjs
// Cyber threat intelligence — CVE, ransomware groups, and active exploits
// Sources: NVD (NIST), ransomware.live, CISA (supplementary)
// Free — no API keys required

const NVD_API              = 'https://services.nvd.nist.gov/rest/json/cves/2.0';
const RANSOMWARE_ENDPOINTS = [
  'https://api.ransomware.live/v2/recentvictims',
  'https://api.ransomware.live/recentvictims',
  'https://api.ransomware.live/v1/recentvictims',
];

const RANSOMWATCH_URL = 'https://raw.githubusercontent.com/joshhighet/ransomwatch/main/posts.json';

// Sectors where a ransomware hit is intelligence-relevant
const PRIORITY_SECTORS = [
  'defense', 'government', 'energy', 'utilities', 'critical infrastructure',
  'finance', 'banking', 'healthcare', 'transport', 'logistics',
  'aerospace', 'manufacturing', 'oil', 'gas', 'nuclear',
  'military', 'intelligence', 'ministry', 'ministry of', 'department of',
];

// ── NVD: Critical CVEs (CVSS >= 9.0) in the last 3 days ─────────────────────
async function fetchCriticalCVEs() {
  const updates = [];
  try {
    const now   = new Date();
    const start = new Date(now.getTime() - 3 * 24 * 60 * 60 * 1000);
    const params = new URLSearchParams({
      pubStartDate:  start.toISOString().replace('.000Z', '.000'),
      pubEndDate:    now.toISOString().replace('.000Z', '.000'),
      cvssV3Severity: 'CRITICAL',
      resultsPerPage: '20',
    });

    const res = await fetch(`${NVD_API}?${params}`, {
      headers: { 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error(`NVD API ${res.status}`);
    const data = await res.json();
    const cves  = data.vulnerabilities || [];

    for (const entry of cves) {
      const cve     = entry.cve || {};
      const id      = cve.id || '';
      const desc    = cve.descriptions?.find(d => d.lang === 'en')?.value || '';
      const metrics = cve.metrics?.cvssMetricV31?.[0] || cve.metrics?.cvssMetricV30?.[0];
      const score   = metrics?.cvssData?.baseScore || 0;
      const vector  = metrics?.cvssData?.attackVector || '';
      const refs    = cve.references?.slice(0, 2).map(r => r.url) || [];

      // Focus on network-exploitable, low-complexity CVEs
      const isNetwork  = vector === 'NETWORK';
      const isICS      = desc.toLowerCase().includes('scada') ||
                         desc.toLowerCase().includes('industrial') ||
                         desc.toLowerCase().includes('ics');

      updates.push({
        title:    `${id}: CVSS ${score} — ${desc.substring(0, 120)}`,
        cveId:    id,
        score,
        vector,
        isICS,
        isNetwork,
        description: desc.substring(0, 300),
        refs,
        source:   'NVD / NIST',
        type:     'cve',
        priority: score >= 9.5 ? 'critical' : 'high',
        url:      `https://nvd.nist.gov/vuln/detail/${id}`,
      });
    }

    console.log(`[CVE] ${updates.length} critical CVEs (CVSS >= 9.0)`);
  } catch (err) {
    console.warn('[CVE] NVD fetch failed:', err.message);
  }
  return updates;
}

// ── ransomware.live: Recent victims in priority sectors ──────────────────────
async function fetchRansomwareVictims() {
  const updates = [];
  let victims = null;

  for (const endpoint of RANSOMWARE_ENDPOINTS) {
    try {
      const res = await fetch(endpoint, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; CrucixIntelligence/1.0)',
          'Accept':     'application/json',
        },
        signal: AbortSignal.timeout(10000),
      });
      if (res.ok) { victims = await res.json(); break; }
    } catch {}
  }

  try {
    if (!victims) throw new Error('ransomware.live unreachable');

    for (const v of victims.slice(0, 50)) {
      const victim  = v.victim || v.company || v.name || '';
      const group   = v.group || v.ransomware_group || '';
      const sector  = (v.activity || v.sector || v.industry || '').toLowerCase();
      const country = v.country || v.nationality || '';
      const date    = v.published || v.date || '';
      const url     = v.website || '';

      const isPriority = PRIORITY_SECTORS.some(s => sector.includes(s) || victim.toLowerCase().includes(s));
      if (!isPriority) continue;

      updates.push({
        title:    `Ransomware: ${victim} (${group}) — ${sector}`,
        victim,
        group,
        sector,
        country,
        date,
        url:      url || 'https://ransomware.live',
        source:   'ransomware.live',
        type:     'ransomware_victim',
        priority: PRIORITY_SECTORS.slice(0, 8).some(s => sector.includes(s)) ? 'critical' : 'high',
      });
    }

    console.log(`[Ransomware] ${updates.length} priority-sector victims`);
  } catch (err) {
    console.warn('[Ransomware] Fetch failed:', err.message);
  }

  // Fallback: ransomwatch GitHub (more reliable than ransomware.live)
  if (updates.length === 0) {
    try {
      const res = await fetch(RANSOMWATCH_URL, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; CrucixIntelligence/1.0)',
          'Accept':     'application/json',
        },
        signal: AbortSignal.timeout(10000),
      });
      if (res.ok) {
        const posts = await res.json();
        for (const item of posts.slice(0, 200)) {
          const postTitle = item.post_title || '';
          const lower = postTitle.toLowerCase();
          const isPriority = PRIORITY_SECTORS.some(s => lower.includes(s));
          if (!isPriority) continue;
          updates.push({
            title:    `Ransomware: ${postTitle} (${item.group_name || 'unknown'})`,
            victim:   postTitle,
            group:    item.group_name || '',
            sector:   '',
            country:  '',
            date:     item.discovered || '',
            url:      'https://github.com/joshhighet/ransomwatch',
            source:   'ransomwatch',
            type:     'ransomware_victim',
            priority: PRIORITY_SECTORS.slice(0, 8).some(s => lower.includes(s)) ? 'critical' : 'high',
          });
        }
        if (updates.length > 0) {
          console.log(`[Ransomware] ransomwatch fallback: ${updates.length} priority-sector victims`);
        }
      }
    } catch (rwErr) {
      console.warn('[Ransomware] ransomwatch fallback failed:', rwErr.message);
    }
  }

  return updates;
}

// ── Main briefing ─────────────────────────────────────────────────────────────
export async function briefing() {
  console.log('[CyberThreats] Fetching CVE and ransomware intelligence...');

  const [cves, ransomware] = await Promise.allSettled([
    fetchCriticalCVEs(),
    fetchRansomwareVictims(),
  ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : []));

  const updates = [...cves, ...ransomware];
  const signals = updates
    .filter(u => u.priority === 'critical')
    .map(u => u.type === 'cve'
      ? `CRITICAL CVE ${u.cveId} (${u.score}): ${u.description?.substring(0, 100)}`
      : `RANSOMWARE: ${u.victim} hit by ${u.group} (${u.sector})`
    );

  console.log(`[CyberThreats] ${cves.length} CVEs · ${ransomware.length} ransomware hits · ${signals.length} critical`);

  return {
    source:    'Cyber Threat Intelligence',
    timestamp: new Date().toISOString(),
    updates,
    signals,
    cves,
    ransomware,
    counts: {
      criticalCVEs:      cves.filter(c => c.score >= 9.5).length,
      totalCVEs:         cves.length,
      ransomwareVictims: ransomware.length,
      criticalSignals:   signals.length,
    },
  };
}

// CLI test
if (process.argv[1]?.endsWith('cyber_threats.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}
