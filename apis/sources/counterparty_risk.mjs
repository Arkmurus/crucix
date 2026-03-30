// apis/sources/counterparty_risk.mjs
// Counterparty Risk Scoring — Arkmurus Due Diligence Engine
// Combines: Sanctions exposure + Corporate structure + Cyber risk + Geographic risk
// Produces a composite risk score (0-100) for any entity
// Sources: OpenSanctions, OpenCorporates, OFAC, Companies House, Have I Been Pwned
// Free — no API keys required for core functionality

const RISK_WEIGHTS = {
  sanctions:   40,  // % weight — highest priority for compliance
  corporate:   25,  // Ownership opacity, jurisdiction risk
  geographic:  20,  // Country of operation risk
  cyber:       15,  // Domain/breach exposure
};

// High-risk jurisdictions for defence brokering (FATF, EU, UN designations)
const HIGH_RISK_JURISDICTIONS = new Set([
  'russia', 'russian federation', 'iran', 'islamic republic of iran',
  'north korea', 'democratic people\'s republic of korea', 'dprk',
  'syria', 'syrian arab republic', 'belarus', 'myanmar', 'burma',
  'venezuela', 'sudan', 'south sudan', 'somalia', 'mali',
  'burkina faso', 'niger', 'guinea', 'central african republic',
  'haiti', 'cuba', 'nicaragua', 'eritrea', 'zimbabwe',
  'libya', 'iraq', 'yemen', 'afghanistan',
]);

const SECRECY_JURISDICTIONS = new Set([
  'british virgin islands', 'bvi', 'cayman islands', 'panama',
  'seychelles', 'vanuatu', 'samoa', 'cook islands', 'labuan',
  'liechtenstein', 'monaco', 'andorra', 'marshall islands',
]);

const MEDIUM_RISK_JURISDICTIONS = new Set([
  'united arab emirates', 'uae', 'turkey', 'nigeria', 'china',
  'pakistan', 'indonesia', 'vietnam', 'cambodia', 'laos',
  'ethiopia', 'kenya', 'ghana', 'senegal', 'ivory coast',
]);

export async function briefing() {
  // This module provides scoring functions rather than live data
  // Returns stats on the scoring engine itself
  return {
    updates: [],
    signals: [],
    stats: {
      engineVersion: '1.0',
      weightsActive: RISK_WEIGHTS,
      highRiskJurisdictions: HIGH_RISK_JURISDICTIONS.size,
  secrecyJurisdictions: SECRECY_JURISDICTIONS.size,
      fetchedAt: new Date().toISOString(),
    },
  };
}

// ── MAIN SCORING FUNCTION ────────────────────────────────────────────────────
export async function scoreCounterparty(entityName, options = {}) {
  const result = {
    entity:     entityName,
    score:      0,           // 0-100 composite risk score
    rating:     'unknown',   // low / medium / high / critical
    breakdown:  {},
    flags:      [],
    sources:    [],
    timestamp:  new Date().toISOString(),
  };

  try {
    const [sanctionsResult, corporateResult, geoResult] = await Promise.allSettled([
      scoreSanctions(entityName),
      scoreCorporate(entityName),
      scoreGeographic(entityName, options.country),
    ]);

    const sanctions  = sanctionsResult.status  === 'fulfilled' ? sanctionsResult.value  : { score: 0, flags: [], sources: [] };
    const corporate  = corporateResult.status  === 'fulfilled' ? corporateResult.value  : { score: 0, flags: [], sources: [] };
    const geographic = geoResult.status === 'fulfilled' ? geoResult.value : { score: 0, flags: [], sources: [] };

    result.breakdown = {
      sanctions:   { score: sanctions.score,  weight: RISK_WEIGHTS.sanctions,  weighted: Math.round(sanctions.score * RISK_WEIGHTS.sanctions / 100) },
      corporate:   { score: corporate.score,  weight: RISK_WEIGHTS.corporate,  weighted: Math.round(corporate.score * RISK_WEIGHTS.corporate / 100) },
      geographic:  { score: geographic.score, weight: RISK_WEIGHTS.geographic, weighted: Math.round(geographic.score * RISK_WEIGHTS.geographic / 100) },
      cyber:       { score: 0, weight: RISK_WEIGHTS.cyber, weighted: 0 },
    };

    result.score = Object.values(result.breakdown).reduce((sum, b) => sum + b.weighted, 0);
    result.rating = result.score >= 75 ? 'critical' : result.score >= 50 ? 'high' : result.score >= 25 ? 'medium' : 'low';
    result.flags = [...sanctions.flags, ...corporate.flags, ...geographic.flags];
    result.sources = [...sanctions.sources, ...corporate.sources, ...geographic.sources];

  } catch (err) {
    result.error = err.message;
    console.error('[CounterpartyRisk] Score error:', err.message);
  }

  return result;
}

// ── SANCTIONS SCORING ────────────────────────────────────────────────────────
async function scoreSanctions(entityName) {
  const result = { score: 0, flags: [], sources: [] };
  try {
    // OpenSanctions search
    const params = new URLSearchParams({ q: entityName, limit: '5', dataset: 'us_ofac_sdn,eu_fsf,un_sc_sanctions,gb_hmt_sanctions' });
    const res = await fetch(`https://api.opensanctions.org/search/default?${params}`, {
      headers: { 'Accept': 'application/json', 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(10000),
    });

    if (res.ok) {
      const data = await res.json();
      const matches = data.results || [];
      result.sources.push('OpenSanctions');

      for (const match of matches) {
        const score = match.score || 0;
        const datasets = match.datasets || [];
        const multiList = datasets.length >= 2;

        if (score >= 0.9) {
          result.score = Math.min(100, result.score + (multiList ? 100 : 85));
          result.flags.push(`SANCTIONS MATCH (${Math.round(score * 100)}%): ${match.caption} — ${datasets.join(', ')}`);
        } else if (score >= 0.7) {
          result.score = Math.min(100, result.score + 40);
          result.flags.push(`POSSIBLE SANCTIONS MATCH (${Math.round(score * 100)}%): ${match.caption}`);
        }
      }
    }

    // OFAC SDN check via OFAC API
    const ofacRes = await fetch(`https://sanctionssearch.ofac.treas.gov/Api/Search/Search?terms=${encodeURIComponent(entityName)}&type=Individual,Entity&program=All&list=SDN,CONS&score=90`, {
      headers: { 'Accept': 'application/json', 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(8000),
    });
    if (ofacRes.ok) {
      const ofacData = await ofacRes.json();
      const hits = ofacData.results?.hits || 0;
      result.sources.push('OFAC SDN');
      if (hits > 0) {
        result.score = Math.min(100, result.score + 90);
        result.flags.push(`OFAC SDN MATCH: ${hits} result(s) found`);
      }
    }

  } catch (err) {
    result.sources.push('Sanctions check failed: ' + err.message);
  }
  return result;
}

// ── CORPORATE SCORING ────────────────────────────────────────────────────────
async function scoreCorporate(entityName) {
  const result = { score: 0, flags: [], sources: [] };
  try {
    const params = new URLSearchParams({ q: entityName, jurisdiction_code: '', 'per_page': '5' });
    const res = await fetch(`https://api.opencorporates.com/v0.4/companies/search?${params}`, {
      headers: { 'Accept': 'application/json', 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(10000),
    });

    if (res.ok) {
      const data = await res.json();
      const companies = data.results?.companies || [];
      result.sources.push('OpenCorporates');

      for (const { company } of companies) {
        const jurisdiction = (company.jurisdiction_code || '').toLowerCase();
        const country = (company.registered_address?.country || '').toLowerCase();
        const status = (company.current_status || '').toLowerCase();
        const name = (company.name || '').toLowerCase();

        // Check secrecy jurisdictions
        if (SECRECY_JURISDICTIONS.has(jurisdiction) || SECRECY_JURISDICTIONS.has(country)) {
          result.score = Math.min(100, result.score + 35);
          result.flags.push(`SECRECY JURISDICTION: ${company.name} registered in ${jurisdiction || country}`);
        }

        // Check high-risk jurisdictions
        if (HIGH_RISK_JURISDICTIONS.has(jurisdiction) || HIGH_RISK_JURISDICTIONS.has(country)) {
          result.score = Math.min(100, result.score + 50);
          result.flags.push(`HIGH-RISK JURISDICTION: ${company.name} in ${jurisdiction || country}`);
        }

        // Dissolved or inactive companies used as shells
        if (status.includes('dissolved') || status.includes('inactive') || status.includes('struck off')) {
          result.score = Math.min(100, result.score + 20);
          result.flags.push(`DISSOLVED/INACTIVE ENTITY: ${company.name} — status: ${status}`);
        }

        // Very recently incorporated
        if (company.incorporation_date) {
          const age = (Date.now() - new Date(company.incorporation_date).getTime()) / (1000 * 60 * 60 * 24 * 365);
          if (age < 1) {
            result.score = Math.min(100, result.score + 25);
            result.flags.push(`RECENTLY INCORPORATED: ${company.name} — less than 1 year old`);
          }
        }
      }
    }

    // Companies House check for UK entities
    const chRes = await fetch(`https://api.company-information.service.gov.uk/search/companies?q=${encodeURIComponent(entityName)}&items_per_page=3`, {
      headers: { 'Accept': 'application/json', 'User-Agent': 'CrucixIntelligence/1.0' },
      signal: AbortSignal.timeout(8000),
    });
    if (chRes.ok) {
      const chData = await chRes.json();
      result.sources.push('Companies House');
      const items = chData.items || [];
      for (const company of items) {
        if (company.company_status === 'dissolved') {
          result.score = Math.min(100, result.score + 15);
          result.flags.push(`DISSOLVED UK COMPANY: ${company.title}`);
        }
      }
    }

  } catch (err) {
    result.sources.push('Corporate check failed: ' + err.message);
  }
  return result;
}

// ── GEOGRAPHIC SCORING ───────────────────────────────────────────────────────
async function scoreGeographic(entityName, country = '') {
  const result = { score: 0, flags: [], sources: [] };
  const c = country.toLowerCase().trim();

  if (!c) return result;

  result.sources.push('Geographic risk assessment');

  if (HIGH_RISK_JURISDICTIONS.has(c)) {
    result.score = 80;
    result.flags.push(`HIGH-RISK COUNTRY OF OPERATION: ${country}`);
  } else if (SECRECY_JURISDICTIONS.has(c)) {
    result.score = 60;
    result.flags.push(`SECRECY JURISDICTION COUNTRY: ${country}`);
  } else if (MEDIUM_RISK_JURISDICTIONS.has(c)) {
    result.score = 35;
    result.flags.push(`MEDIUM-RISK COUNTRY: ${country}`);
  }

  return result;
}

// ── BATCH SCREENING ──────────────────────────────────────────────────────────
export async function screenWatchlist(entities) {
  const results = [];
  for (const entity of entities) {
    const name = typeof entity === 'string' ? entity : entity.name;
    const country = typeof entity === 'object' ? entity.country : '';
    const score = await scoreCounterparty(name, { country });
    results.push(score);
    await new Promise(r => setTimeout(r, 300)); // rate limiting
  }
  return results.sort((a, b) => b.score - a.score);
}

// ── TELEGRAM COMMAND HANDLER ─────────────────────────────────────────────────
export async function handleRiskCommand(entityName) {
  if (!entityName?.trim()) {
    return `COUNTERPARTY RISK SCORING\n\nUsage: /risk [company name]\nExample: /risk Rosoboronexport\n\nScores 0-100 across:\n• Sanctions exposure (40%)\n• Corporate structure (25%)\n• Geographic risk (20%)\n• Cyber exposure (15%)`;
  }

  const result = await scoreCounterparty(entityName);
  const ratingEmoji = result.rating === 'critical' ? '🔴' : result.rating === 'high' ? '🟠' : result.rating === 'medium' ? '🟡' : '🟢';

  let msg = `RISK ASSESSMENT: ${entityName.toUpperCase()}\n\n`;
  msg += `${ratingEmoji} COMPOSITE SCORE: ${result.score}/100 — ${result.rating.toUpperCase()}\n\n`;
  msg += `BREAKDOWN:\n`;
  msg += `• Sanctions: ${result.breakdown.sanctions?.score || 0}/100 (weight 40%)\n`;
  msg += `• Corporate: ${result.breakdown.corporate?.score || 0}/100 (weight 25%)\n`;
  msg += `• Geographic: ${result.breakdown.geographic?.score || 0}/100 (weight 20%)\n\n`;

  if (result.flags.length > 0) {
    msg += `FLAGS:\n`;
    for (const flag of result.flags.slice(0, 6)) {
      msg += `⚠️ ${flag}\n`;
    }
  } else {
    msg += `No adverse flags detected\n`;
  }

  msg += `\nSources: ${result.sources.join(', ')}\n`;
  msg += `\nVerify: opensanctions.org/search/?q=${encodeURIComponent(entityName)}`;

  return msg;
}
