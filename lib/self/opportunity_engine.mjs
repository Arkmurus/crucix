// lib/self/opportunity_engine.mjs
// Sales opportunity detector — purpose-built for Arkmurus defense brokering
// Cross-references: ACLED conflict intensity + AfDB finance + OEM directory + export controls
// Output: ranked procurement windows per target market

import { OEM_DATABASE } from '../intel/oem_db.mjs';
import { saveOpportunities, getAdaptiveScoringWeights } from './learning_store.mjs';

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
// strategicNeeds: known standing procurement programmes / capability gaps (used when ACLED data unavailable)
// procurementPortal: authoritative public tender search URL for this market
const TARGET_MARKETS = [
  // Lusophone Africa — core / highest advantage
  { name: 'Angola',         iso2: 'AO', lusophone: true,  priority: 'HIGH',   riskLevel: 2,
    strategicNeeds: ['patrol vessels', 'border surveillance', 'small arms', 'ammunition', 'communications', 'armoured vehicles'],
    procurementPortal: 'https://www.minfin.gov.ao/PortalMF/',
    stratContext: 'FAA modernisation programme ongoing; oil-funded defence budget; Lusophone direct access' },
  { name: 'Mozambique',     iso2: 'MZ', lusophone: true,  priority: 'HIGH',   riskLevel: 3,
    strategicNeeds: ['counter-insurgency equipment', 'small arms', 'patrol vessels', 'surveillance UAV', 'personal armor'],
    procurementPortal: 'https://www.fazenda.gov.mz/licitacoes/',
    stratContext: 'Active Cabo Delgado insurgency; SADC and SAMIM support operations; EU and US funding flows' },
  { name: 'Guinea-Bissau',  iso2: 'GW', lusophone: true,  priority: 'MEDIUM', riskLevel: 4,
    strategicNeeds: ['light weapons', 'maritime patrol', 'communications', 'vehicles'],
    procurementPortal: 'https://www.minfin.gw/',
    stratContext: 'Fragile state; ECOWAS stabilisation; narco-trafficking interdiction equipment demand' },
  { name: 'Cape Verde',     iso2: 'CV', lusophone: true,  priority: 'LOW',    riskLevel: 1,
    strategicNeeds: ['coast guard vessels', 'maritime surveillance', 'communications'],
    procurementPortal: 'https://www.base.gov.cv/',
    stratContext: 'Stable Lusophone Atlantic hub; coast guard modernisation; EU border cooperation' },
  // West & Central Africa
  { name: 'Nigeria',        iso2: 'NG', lusophone: false, priority: 'HIGH',   riskLevel: 3,
    strategicNeeds: ['counter-terrorism', 'armoured vehicles', 'patrol aircraft', 'small arms', 'ammunition', 'surveillance systems'],
    procurementPortal: 'https://www.publicprocurement.gov.ng/tenders',
    stratContext: 'Boko Haram / ISWAP operations in NE; NE counterinsurgency; NAF air assets; NN maritime' },
  { name: 'Ghana',          iso2: 'GH', lusophone: false, priority: 'MEDIUM', riskLevel: 1,
    strategicNeeds: ['border security', 'patrol vehicles', 'communications', 'personal protection'],
    procurementPortal: 'https://www.ppa.gov.gh/public-procurement/tender-notices',
    stratContext: 'Stable democracy; Sahel spillover risk; GAF modernisation budget; UN peacekeeping contributor' },
  { name: 'Senegal',        iso2: 'SN', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['maritime patrol', 'counter-terrorism', 'light armour', 'communications'],
    procurementPortal: 'https://www.marchespublics.sn/',
    stratContext: 'New hydrocarbon wealth; Casamance security; Sahel counterterrorism operations' },
  { name: 'Côte d\'Ivoire', iso2: 'CI', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['border surveillance', 'armoured vehicles', 'special forces equipment', 'communications'],
    procurementPortal: 'https://www.dmp.ci/appels-doffres/',
    stratContext: 'Post-crisis rearmament; Sahel border pressure; ECOWAS standby force contributor' },
  { name: 'Cameroon',       iso2: 'CM', lusophone: false, priority: 'MEDIUM', riskLevel: 3,
    strategicNeeds: ['counter-insurgency', 'surveillance', 'small arms', 'armoured vehicles'],
    procurementPortal: 'https://www.armp.cm/index.php/marches-publics',
    stratContext: 'BIR operations vs Boko Haram; Anglophone crisis; Lake Chad Basin coalition' },
  // East Africa
  { name: 'Kenya',          iso2: 'KE', lusophone: false, priority: 'HIGH',   riskLevel: 2,
    strategicNeeds: ['counter-terrorism', 'armoured vehicles', 'communications', 'surveillance UAV', 'personal protection'],
    procurementPortal: 'https://tenders.go.ke/website/tenders/index',
    stratContext: 'Active AMISOM/AUSSOM contributor; Al-Shabaab threat; Haiti peacekeeping deployment; growing defence budget' },
  { name: 'Tanzania',       iso2: 'TZ', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['maritime patrol', 'border security', 'communications', 'armoured vehicles'],
    procurementPortal: 'https://www.ppra.go.tz/index.php/tender-notices',
    stratContext: 'SADC military contributor; Mozambique SAMIM deployment; Indian Ocean maritime security' },
  { name: 'Rwanda',         iso2: 'RW', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['peacekeeping equipment', 'personal protection', 'light vehicles', 'communications'],
    procurementPortal: 'https://www.rppa.gov.rw/index.php/tenders',
    stratContext: 'Premier UN peacekeeping contributor; RDF deployed Mozambique/CAR; modernising force' },
  { name: 'Uganda',         iso2: 'UG', lusophone: false, priority: 'MEDIUM', riskLevel: 3,
    strategicNeeds: ['counter-terrorism', 'armoured vehicles', 'surveillance', 'small arms', 'ammunition'],
    procurementPortal: 'https://www.ppda.go.ug/procurement-notices/',
    stratContext: 'AMISOM/AUSSOM force; ADF threat DRC border; UPDF active deployment' },
  { name: 'Ethiopia',       iso2: 'ET', lusophone: false, priority: 'MEDIUM', riskLevel: 4,
    strategicNeeds: ['communications', 'surveillance', 'logistics vehicles', 'personal protection'],
    procurementPortal: 'https://www.ppesa.gov.et/index.php/procurement-news',
    stratContext: 'Post-Tigray rearmament; ENDF reconstruction; large standing army; IGAD security contributor' },
  // Southeast Asia
  { name: 'Philippines',    iso2: 'PH', lusophone: false, priority: 'HIGH',   riskLevel: 2,
    strategicNeeds: ['naval vessels', 'patrol craft', 'counter-terrorism', 'surveillance UAV', 'small arms'],
    procurementPortal: 'https://www.philgeps.gov.ph/GEPSNONPILOT/Tender/SplashOpportunitiesSearchUI.aspx',
    stratContext: 'Horizon 3 modernisation programme; South China Sea territorial dispute; AFP/PN/PA active procurement' },
  { name: 'Indonesia',      iso2: 'ID', lusophone: false, priority: 'HIGH',   riskLevel: 2,
    strategicNeeds: ['patrol vessels', 'armoured vehicles', 'fighter support', 'surveillance', 'small arms'],
    procurementPortal: 'https://lpse.kemhan.go.id/eproc2/',
    stratContext: 'MEF III (2020-2024) major rearmament; archipelago maritime security; TNI procurement active' },
  { name: 'Vietnam',        iso2: 'VN', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['coast guard vessels', 'maritime surveillance', 'radar', 'communications'],
    procurementPortal: 'https://muasamcong.mpi.gov.vn/',
    stratContext: 'South China Sea tensions; PAVN modernisation; diversifying away from Russian supply' },
  { name: 'Bangladesh',     iso2: 'BD', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['patrol vessels', 'border surveillance', 'peacekeeping equipment', 'light armour'],
    procurementPortal: 'https://cptu.gov.bd/Procurement_Notice.html',
    stratContext: 'Largest UN peacekeeping contributor; BN/BA modernisation; Bay of Bengal maritime security' },
  // Latin America
  { name: 'Brazil',         iso2: 'BR', lusophone: true,  priority: 'HIGH',   riskLevel: 2,
    strategicNeeds: ['armoured vehicles', 'patrol aircraft', 'ammunition', 'surveillance', 'naval systems'],
    procurementPortal: 'https://www.comprasnet.gov.br/seguro/loginPortal.asp',
    stratContext: 'Amazon/border security programme; Brazilian Army VBTP-MR; Lusophone direct channel; major arms importer' },
  { name: 'Colombia',       iso2: 'CO', lusophone: false, priority: 'MEDIUM', riskLevel: 3,
    strategicNeeds: ['counter-narcotics', 'counter-insurgency', 'surveillance UAV', 'small arms', 'helicopters'],
    procurementPortal: 'https://www.colombiacompra.gov.co/',
    stratContext: 'FARC dissidents / ELN active; Peace Process implementation; US FMF funding; MDN modernisation' },
  { name: 'Peru',           iso2: 'PE', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['armoured vehicles', 'border surveillance', 'patrol aircraft', 'communications'],
    procurementPortal: 'https://www.seace.gob.pe/',
    stratContext: 'Sendero Luminoso remnants; VRAEM counter-narcotics; MINDEF procurement active' },
  // Middle East (export-permissible)
  { name: 'Saudi Arabia',   iso2: 'SA', lusophone: false, priority: 'HIGH',   riskLevel: 2,
    strategicNeeds: ['air defense', 'armoured vehicles', 'ammunition', 'surveillance', 'communications', 'special forces'],
    procurementPortal: 'https://tenders.etimad.sa/Tender/AllTendersForVisitor',
    stratContext: 'Vision 2030 localisation push; GAMI 50% domestic target; Yemen conflict consumption; SAMI partnerships' },
  { name: 'UAE',            iso2: 'AE', lusophone: false, priority: 'HIGH',   riskLevel: 1,
    strategicNeeds: ['air defense', 'surveillance UAV', 'counter-drone', 'special forces equipment', 'cyber'],
    procurementPortal: 'https://www.hadef.gov.ae/',
    stratContext: 'IDEX/NAVDEX procurement hub; EDGE Group partnerships; regional hub for defence distribution' },
  { name: 'Jordan',         iso2: 'JO', lusophone: false, priority: 'MEDIUM', riskLevel: 2,
    strategicNeeds: ['border security', 'armoured vehicles', 'surveillance', 'special forces', 'communications'],
    procurementPortal: 'https://www.gpp.gov.jo/',
    stratContext: 'Syria/Iraq border security; JAF modernisation; US FMF recipient; active regional security actor' },
  // Europe (NATO/EU members — massive post-2022 modernisation budgets)
  { name: 'Poland',         iso2: 'PL', lusophone: false, priority: 'HIGH',   riskLevel: 1,
    strategicNeeds: ['armoured vehicles', 'artillery', 'ammunition', 'air defense', 'communications', 'logistics'],
    procurementPortal: 'https://ezamawiajacy.pl/pzp/pzp.nsf/index.xsp',
    stratContext: 'Largest NATO European buyer; K2/K9 programmes; 4% GDP defence spend; eastern flank priority' },
  { name: 'Romania',        iso2: 'RO', lusophone: false, priority: 'HIGH',   riskLevel: 1,
    strategicNeeds: ['armoured vehicles', 'ammunition', 'surveillance', 'air defense', 'communications'],
    procurementPortal: 'https://www.e-licitatie.ro/pub/notices/contract-notices/list/1/0',
    stratContext: 'Eastern flank NATO; Patriot + F-35 operator; major rearmament programme 2024-2030; $10B+ planned' },
  { name: 'Greece',         iso2: 'GR', lusophone: false, priority: 'MEDIUM', riskLevel: 1,
    strategicNeeds: ['naval systems', 'armoured vehicles', 'air defense', 'surveillance'],
    procurementPortal: 'https://www.promitheus.gov.gr/',
    stratContext: 'Aegean tensions; Greek-Turkish deterrence; HAF/HN modernisation; Rafale operator' },
  { name: 'Bulgaria',       iso2: 'BG', lusophone: false, priority: 'MEDIUM', riskLevel: 1,
    strategicNeeds: ['armoured vehicles', 'ammunition', 'communications', 'surveillance', 'personal protection'],
    procurementPortal: 'https://www.appalti.eu/bg/',
    stratContext: 'NATO eastern flank; F-16 transition; BG Armed Forces legacy Russian equipment replacement' },
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

function getProcurementNeeds(conflictData, market) {
  const needs = new Set();

  // Conflict-derived needs
  for (const type of conflictData.topTypes) {
    for (const [pattern, products] of Object.entries(CONFLICT_TO_PROCUREMENT)) {
      if (type.includes(pattern.split('/')[0]) || pattern.includes(type.split(' ')[0])) {
        products.forEach(p => needs.add(p));
      }
    }
  }
  if (conflictData.total >= 5) {
    needs.add('ammunition');
    needs.add('155mm');
  }

  // Always supplement with market's known strategic programme needs
  // This ensures meaningful OEM matching even without live ACLED data
  if (market?.strategicNeeds) {
    for (const need of market.strategicNeeds) {
      needs.add(need);
      if (needs.size >= 8) break; // cap at 8 items
    }
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

  // Base score from market priority — ensures high-value markets always appear
  score += market.priority === 'HIGH' ? 18 : market.priority === 'MEDIUM' ? 10 : 5;

  // Conflict intensity (max 40pts on top of base)
  score += Math.min(40, conflictData.total * 3);

  // Fatalities indicate severity (max 15pts)
  score += Math.min(15, Math.floor(conflictData.fatalities / 10));

  // Procurement type diversity (max 15pts)
  score += Math.min(15, Object.keys(conflictData.byType).length * 4);

  // Development finance signals = budget available (10pts)
  if (devFinanceCount > 0) score += 10;

  // Lusophone advantage: language + relationship edge (12pts)
  if (market.lusophone) score += 12;

  // Risk penalty (higher risk = harder to close)
  score -= market.riskLevel * 3;

  // Sanctions/blocker penalty (not full embargo — those are pre-filtered)
  score -= blockers.length * 15;

  return Math.min(100, Math.max(0, score));
}

// Procurement signal keywords — ranked highest to lowest specificity
const PROCUREMENT_KW = [
  'tender', 'rfp', 'rfq', 'request for proposal', 'request for quotation',
  'contract award', 'contract awarded', 'procurement', 'bid', 'bidding',
  'purchase order', 'framework agreement', 'defence budget', 'defense budget',
  'military spending', 'acquisition', 'modernisation', 'modernization',
  'arms deal', 'weapons deal', 'equipment order', 'supply contract',
];

// Extract per-market signal counts AND source links from all available data
function _extractMarketIntel(currentData) {
  const byMarket = {}; // { marketNameLower: { count, sources: [{title, url, type, procScore}] } }

  // Candidate items from news, signals, explorer insights
  const candidates = [
    ...(currentData.news || []).map(n => ({
      title:   n.title || n.headline || '',
      summary: n.summary || n.description || '',
      url:     n.url || n.link || '',
      type:    'news',
    })),
    ...(currentData.newsFeed || []).map(n => ({
      title:   n.title || '',
      summary: n.description || '',
      url:     n.url || n.link || '',
      type:    'news',
    })),
    ...(currentData.signals || []).map(s => ({
      title:   (s.text || '').substring(0, 120),
      summary: s.text || '',
      url:     s.url || s.link || '',
      type:    'signal',
    })),
  ];

  // Also include explorer insights if present
  const explorerInsights = currentData.explorerFindings?.findings?.insights
    || currentData.explorerFindings?.insights || [];
  for (const ins of explorerInsights) {
    candidates.push({
      title:   ins.title || '',
      summary: ins.summary || '',
      url:     ins.sourceUrl || '',
      type:    'explorer',
    });
  }

  for (const item of candidates) {
    const combined = ((item.title || '') + ' ' + (item.summary || '')).toLowerCase();

    for (const market of TARGET_MARKETS) {
      if (!combined.includes(market.name.toLowerCase())) continue;

      const key = market.name.toLowerCase();
      if (!byMarket[key]) byMarket[key] = { count: 0, sources: [] };
      byMarket[key].count++;

      if (!item.url || byMarket[key].sources.length >= 6) continue;

      // Score by procurement keyword presence
      const procScore = PROCUREMENT_KW.filter(kw => combined.includes(kw)).length;

      byMarket[key].sources.push({
        title:     (item.title || '').substring(0, 110),
        url:       item.url,
        type:      item.type,
        procScore,
        isProcurement: procScore > 0,
      });
    }
  }

  // Sort each market's sources: procurement-specific first, then by type
  for (const key of Object.keys(byMarket)) {
    byMarket[key].sources.sort((a, b) => b.procScore - a.procScore);
  }

  return byMarket;
}

export function detectOpportunities(currentData) {
  if (!currentData) return [];

  const acled = currentData.acled || {};
  const exportControlData = currentData.exportControlIntel || currentData.exportControls || {};
  const opensanctions = currentData.opensanctions || {};
  const afdb = currentData.afdb || {};

  // Load outcome-driven adaptive weights (null = not enough history yet)
  const adaptiveWeights = getAdaptiveScoringWeights();

  const opportunities = [];

  // Build per-market intel: signal counts + source links
  const marketIntel = _extractMarketIntel(currentData);
  // backward-compat alias for signal count lookup
  const explorerSignals = Object.fromEntries(
    Object.entries(marketIntel).map(([k, v]) => [k, v.count])
  );

  for (const market of TARGET_MARKETS) {
    const conflictData  = extractMarketConflicts(market.name, acled);
    const blockers      = checkExportBlockers(market.name, exportControlData, opensanctions);

    // Hard stop: market under active arms embargo — never show
    if (blockers.some(b => b.type === 'embargo')) continue;

    const procurementNeeds = getProcurementNeeds(conflictData, market);
    const matchedOEMs = matchOEMs(
      procurementNeeds.length > 0 ? procurementNeeds : (market.strategicNeeds || ['small arms', 'ammunition', 'communications'])
    );

    // Development finance signals for this market (budget available indicator)
    const devFinanceCount = (afdb.projects || afdb.updates || []).filter(p => {
      const text = ((p.country || '') + ' ' + (p.title || '') + ' ' + (p.sector || '')).toLowerCase();
      return text.includes(market.name.toLowerCase()) &&
        text.match(/security|defence|military|police|border|capacity/);
    }).length;

    let score = scoreOpportunity(market, conflictData, blockers, devFinanceCount);

    // Boost score if explorer/OSINT signals mention this market
    const marketSignals = explorerSignals[market.name.toLowerCase()] || 0;
    if (marketSignals > 0) score += Math.min(20, marketSignals * 5);

    // Apply outcome-learned multiplier for this market if available
    const multiplier = adaptiveWeights?.regionMultipliers?.[market.name];
    if (multiplier != null) {
      score = Math.min(100, Math.max(0, Math.round(score * multiplier)));
    }

    score = Math.min(100, Math.max(0, score));

    // Always include HIGH priority markets as WATCH minimum (pre-positioning value)
    // Drop MEDIUM/LOW markets only if score too low AND no explorer signals
    if (score < 10 && market.priority !== 'HIGH') continue;
    if (score < 5) continue;

    const hasConflict = conflictData.total > 0;
    opportunities.push({
      id: `${market.iso2}-${Date.now()}`,
      market: market.name,
      iso2: market.iso2,
      lusophone: market.lusophone,
      score,
      tier: score >= 65 ? 'HIGH' : score >= 35 ? 'MEDIUM' : 'WATCH',
      conflict: {
        events: conflictData.total,
        fatalities: conflictData.fatalities,
        types: conflictData.topTypes.slice(0, 3),
      },
      procurementNeeds: (procurementNeeds.length > 0 ? procurementNeeds : ['monitoring']).slice(0, 5),
      matchedOEMs,
      blockers,
      devFinanceActivity: devFinanceCount > 0,
      explorerSignals: marketSignals,
      complianceStatus: blockers.length === 0 ? 'CLEAR' : 'REVIEW_REQUIRED',
      detectedAt: new Date().toISOString(),
      notes: blockers.length > 0
          ? `⚠️ Export control review required before engagement`
          : hasConflict
            ? `${conflictData.total} active conflict events — procurement window open`
            : market.stratContext || `Pre-positioning: ${market.priority} priority market${marketSignals > 0 ? ` — ${marketSignals} intel signal(s) detected` : ''}`,
      adaptiveMultiplier: multiplier ?? null,
      procurementPortal: market.procurementPortal || null,
      // Source links: procurement-specific first, max 5
      sources: (marketIntel[market.name.toLowerCase()]?.sources || [])
        .slice(0, 5)
        .map(s => ({ title: s.title, url: s.url, type: s.type, isProcurement: s.isProcurement })),
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
