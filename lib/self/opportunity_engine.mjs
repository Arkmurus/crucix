// lib/self/opportunity_engine.mjs
// Sales opportunity detector — purpose-built for Arkmurus defense brokering
// Cross-references: ACLED conflict intensity + AfDB finance + OEM directory + export controls
// Output: ranked procurement windows per target market

import { OEM_DATABASE } from '../intel/oem_db.mjs';
import { saveOpportunities } from './learning_store.mjs';

// Conflict event type → likely procurement need (maps to OEM product keywords)
const CONFLICT_TO_PROCUREMENT = {
  'explosions/remote violence': ['155mm', 'ammunition', 'fuzes', 'propellant', 'mortar'],
  'battles': ['small arms', 'vehicles', 'personal armor', 'communications'],
  'air/drone': ['air defense', 'radar', 'SHORAD', 'counter-UAS'],
  'violence against civilians': ['border security', 'surveillance', 'C2 systems'],
  'protests/riots': ['riot control', 'vehicles', 'communications'],
  'strategic developments': ['logistics', 'C2 systems', 'intelligence'],
};

// Global target markets — export-control permissible, no UN/EU/US embargoed states
const TARGET_MARKETS = [
  // Lusophone Africa — core / highest advantage
  { name: 'Angola',         iso2: 'AO', lusophone: true,  priority: 'HIGH',   riskLevel: 2 },
  { name: 'Mozambique',     iso2: 'MZ', lusophone: true,  priority: 'HIGH',   riskLevel: 3 },
  { name: 'Guinea-Bissau',  iso2: 'GW', lusophone: true,  priority: 'MEDIUM', riskLevel: 4 },
  { name: 'Cape Verde',     iso2: 'CV', lusophone: true,  priority: 'LOW',    riskLevel: 1 },
  // West & Central Africa
  { name: 'Nigeria',        iso2: 'NG', lusophone: false, priority: 'HIGH',   riskLevel: 3 },
  { name: 'Ghana',          iso2: 'GH', lusophone: false, priority: 'MEDIUM', riskLevel: 1 },
  { name: 'Senegal',        iso2: 'SN', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  { name: 'Côte d\'Ivoire', iso2: 'CI', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  { name: 'Cameroon',       iso2: 'CM', lusophone: false, priority: 'MEDIUM', riskLevel: 3 },
  // East Africa
  { name: 'Kenya',          iso2: 'KE', lusophone: false, priority: 'HIGH',   riskLevel: 2 },
  { name: 'Tanzania',       iso2: 'TZ', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  { name: 'Rwanda',         iso2: 'RW', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  { name: 'Uganda',         iso2: 'UG', lusophone: false, priority: 'MEDIUM', riskLevel: 3 },
  { name: 'Ethiopia',       iso2: 'ET', lusophone: false, priority: 'MEDIUM', riskLevel: 4 },
  // Southeast Asia
  { name: 'Philippines',    iso2: 'PH', lusophone: false, priority: 'HIGH',   riskLevel: 2 },
  { name: 'Indonesia',      iso2: 'ID', lusophone: false, priority: 'HIGH',   riskLevel: 2 },
  { name: 'Vietnam',        iso2: 'VN', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  { name: 'Bangladesh',     iso2: 'BD', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  // Latin America
  { name: 'Brazil',         iso2: 'BR', lusophone: true,  priority: 'HIGH',   riskLevel: 2 },
  { name: 'Colombia',       iso2: 'CO', lusophone: false, priority: 'MEDIUM', riskLevel: 3 },
  { name: 'Peru',           iso2: 'PE', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
  // Middle East (export-permissible)
  { name: 'Saudi Arabia',   iso2: 'SA', lusophone: false, priority: 'HIGH',   riskLevel: 2 },
  { name: 'UAE',            iso2: 'AE', lusophone: false, priority: 'HIGH',   riskLevel: 1 },
  { name: 'Jordan',         iso2: 'JO', lusophone: false, priority: 'MEDIUM', riskLevel: 2 },
];

// Hard stop: patterns in export control text that kill a deal
const EMBARGO_PATTERNS = [
  /arms embargo/i,
  /weapons embargo/i,
  /military.*embargo/i,
  /UNSC.*resolution.*\d+.*arms/i,
  /EU.*restrictive measures.*arms/i,
];

function extractMarketConflicts(marketName, acledData) {
  const events = (acledData?.events || []).filter(e => {
    const loc = ((e.country || '') + ' ' + (e.region || '') + ' ' + (e.admin1 || '')).toLowerCase();
    return loc.includes(marketName.toLowerCase());
  });

  const byType = {};
  for (const ev of events) {
    const t = (ev.event_type || ev.type || 'unknown').toLowerCase();
    byType[t] = (byType[t] || 0) + 1;
  }

  return {
    total: events.length,
    fatalities: events.reduce((sum, e) => sum + (e.fatalities || 0), 0),
    byType,
    topTypes: Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([t]) => t),
  };
}

function checkExportBlockers(marketName, exportControlData, opensanctionsData) {
  const blockers = [];

  // Check for active embargoes in export control intel
  const alerts = [
    ...(exportControlData?.alerts || []),
    ...(exportControlData?.updates || []),
    ...(exportControlData?.changes || []),
  ];
  for (const alert of alerts) {
    const text = (alert.title || '') + ' ' + (alert.text || alert.summary || '');
    if (text.toLowerCase().includes(marketName.toLowerCase())) {
      for (const pattern of EMBARGO_PATTERNS) {
        if (pattern.test(text)) {
          blockers.push({ type: 'embargo', message: (alert.title || text).substring(0, 100) });
          break;
        }
      }
    }
  }

  // Check sanctions for country-level designations
  const sanctioned = (opensanctionsData?.recent || []).filter(e => {
    const ctx = ((e.name || '') + ' ' + (e.datasets?.join(' ') || '')).toLowerCase();
    return ctx.includes(marketName.toLowerCase()) && ctx.includes('arms');
  });
  if (sanctioned.length > 0) {
    blockers.push({ type: 'sanctions', message: `${sanctioned.length} sanctions entry/entries linked to ${marketName}` });
  }

  return blockers;
}

function getProcurementNeeds(conflictData) {
  const needs = new Set();
  for (const type of conflictData.topTypes) {
    for (const [pattern, products] of Object.entries(CONFLICT_TO_PROCUREMENT)) {
      if (type.includes(pattern.split('/')[0]) || pattern.includes(type.split(' ')[0])) {
        products.forEach(p => needs.add(p));
      }
    }
  }
  // Fallback: high-intensity conflict always needs ammunition
  if (conflictData.total >= 5) {
    needs.add('ammunition');
    needs.add('155mm');
  }
  return Array.from(needs);
}

function matchOEMs(procurementNeeds) {
  return OEM_DATABASE.filter(oem => {
    const productStr = (oem.products || []).join(' ').toLowerCase();
    const tagStr = (oem.tags || []).join(' ').toLowerCase();
    return procurementNeeds.some(need =>
      productStr.includes(need.toLowerCase()) || tagStr.includes(need.toLowerCase())
    );
  }).slice(0, 4).map(oem => ({
    name: oem.name,
    country: oem.country,
    scale: oem.scale,
    products: (oem.products || []).slice(0, 3).join(', '),
    exportContact: oem.exportContact || oem.contact?.general || null,
  }));
}

function scoreOpportunity(market, conflictData, blockers, devFinanceCount) {
  let score = 0;

  // Conflict intensity is the primary driver (max 40pts)
  score += Math.min(40, conflictData.total * 3);

  // Fatalities indicate severity (max 15pts)
  score += Math.min(15, Math.floor(conflictData.fatalities / 10));

  // Procurement type diversity: more types = more procurement channels (max 15pts)
  score += Math.min(15, Object.keys(conflictData.byType).length * 4);

  // Development finance signals = budget available (10pts)
  if (devFinanceCount > 0) score += 10;

  // Lusophone advantage: language + relationship edge (15pts)
  if (market.lusophone) score += 15;

  // Market priority bonus
  if (market.priority === 'HIGH') score += 5;

  // Risk penalty (higher risk = harder to close)
  score -= market.riskLevel * 3;

  // Hard blockers severely reduce score
  score -= blockers.length * 25;

  return Math.min(100, Math.max(0, score));
}

export function detectOpportunities(currentData) {
  if (!currentData) return [];

  const acled = currentData.acled || {};
  const exportControlData = currentData.exportControlIntel || currentData.exportControls || {};
  const opensanctions = currentData.opensanctions || {};
  const afdb = currentData.afdb || {};

  const opportunities = [];

  for (const market of TARGET_MARKETS) {
    const conflictData = extractMarketConflicts(market.name, acled);
    if (conflictData.total === 0) continue; // No conflict = no immediate procurement driver

    const blockers = checkExportBlockers(market.name, exportControlData, opensanctions);
    const procurementNeeds = getProcurementNeeds(conflictData);
    const matchedOEMs = matchOEMs(procurementNeeds);

    // Development finance signals for this market (budget available indicator)
    const devFinanceCount = (afdb.projects || afdb.updates || []).filter(p => {
      const text = ((p.country || '') + ' ' + (p.title || '') + ' ' + (p.sector || '')).toLowerCase();
      return text.includes(market.name.toLowerCase()) &&
        text.match(/security|defence|military|police|border|capacity/);
    }).length;

    const score = scoreOpportunity(market, conflictData, blockers, devFinanceCount);
    if (score < 15) continue; // Below minimum threshold

    opportunities.push({
      id: `${market.iso2}-${Date.now()}`,
      market: market.name,
      iso2: market.iso2,
      lusophone: market.lusophone,
      score,
      tier: score >= 65 ? 'HIGH' : score >= 40 ? 'MEDIUM' : 'WATCH',
      conflict: {
        events: conflictData.total,
        fatalities: conflictData.fatalities,
        types: conflictData.topTypes.slice(0, 3),
      },
      procurementNeeds: procurementNeeds.slice(0, 5),
      matchedOEMs,
      blockers,
      devFinanceActivity: devFinanceCount > 0,
      complianceStatus: blockers.length === 0 ? 'CLEAR' : 'REVIEW_REQUIRED',
      detectedAt: new Date().toISOString(),
      notes: blockers.length > 0
        ? `⚠️ Export control review required before engagement`
        : `${conflictData.total} active conflict events — procurement window open`,
    });
  }

  const sorted = opportunities.sort((a, b) => b.score - a.score);
  saveOpportunities(sorted);
  return sorted;
}

export function formatOpportunitiesForTelegram(opportunities) {
  if (!opportunities || opportunities.length === 0) {
    return '📊 *OPPORTUNITY PIPELINE*\n\nNo active procurement windows detected.\nMonitoring 9 target markets — will alert on conflict escalation.';
  }

  const ts = new Date().toISOString().slice(0, 10);
  let msg = `📊 *ARKMURUS OPPORTUNITY PIPELINE*\n_${ts}_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  const high   = opportunities.filter(o => o.tier === 'HIGH');
  const medium = opportunities.filter(o => o.tier === 'MEDIUM');
  const watch  = opportunities.filter(o => o.tier === 'WATCH');

  if (high.length > 0) {
    msg += `*🔴 HIGH PRIORITY (${high.length})*\n`;
    for (const opp of high) {
      msg += `\n▸ *${opp.market}* — Score ${opp.score}/100`;
      if (opp.lusophone) msg += ' 🇵🇹';
      msg += `\n  ${opp.conflict.events} conflict events`;
      if (opp.conflict.fatalities > 0) msg += ` · ${opp.conflict.fatalities} fatalities`;
      msg += `\n  Needs: ${opp.procurementNeeds.slice(0, 3).join(', ')}\n`;
      if (opp.matchedOEMs.length > 0) {
        msg += `  OEMs: ${opp.matchedOEMs.slice(0, 2).map(o => o.name.split(' ')[0]).join(', ')}\n`;
      }
      if (opp.blockers.length > 0) {
        msg += `  ⚠️ ${opp.blockers[0].message.substring(0, 80)}\n`;
      }
    }
    msg += '\n';
  }

  if (medium.length > 0) {
    msg += `*🟠 MEDIUM PRIORITY (${medium.length})*\n`;
    for (const opp of medium) {
      const flag = opp.lusophone ? ' 🇵🇹' : '';
      msg += `▸ *${opp.market}*${flag} (${opp.score}/100) — ${opp.conflict.events} events · ${opp.procurementNeeds[0] || '?'}\n`;
    }
    msg += '\n';
  }

  if (watch.length > 0) {
    msg += `*🟡 WATCHING (${watch.length})*\n`;
    msg += watch.map(o => `▸ ${o.market} (${o.score}/100)`).join('\n') + '\n';
  }

  msg += `\n━━━━━━━━━━━━━━━━━━━━━━━━\n`;
  msg += `_/risk <entity> · /oem <product> · Refreshed each sweep_`;
  return msg;
}
