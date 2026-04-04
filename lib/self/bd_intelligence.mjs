// lib/self/bd_intelligence.mjs
// Self-aware Business Development & Strategy Intelligence Module
// Turns sweep data into actionable BD pipeline: real tenders, strategic ideas,
// market intelligence, and outcome-driven learning.
//
// Three output tiers:
//   ACTIVE_DEAL    — specific tender / RFP / contract identified, act now
//   STRATEGIC_IDEA — multi-signal intelligence convergence, worth pursuing
//   WATCHLIST      — monitor only, no current actionable signal

import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync } from 'fs';
import { join } from 'path';
import { logSelfUpdate, recordAlertOutcome, getAdaptiveScoringWeights } from './learning_store.mjs';
import { redisGet, redisSet } from '../persist/store.mjs';
import { TARGET_MARKETS as _DEFAULT_MARKETS } from './opportunity_engine.mjs';
import { getPromptModifiers, recordOutcomeForOptimization } from '../aria/prompt_optimizer.mjs';
import { recordOutcomeTraining, recordBrainAssessment } from '../aria/training_data.mjs';

const PIPELINE_FILE  = join(process.cwd(), 'runs', 'learning', 'bd_pipeline.json');
const STRATEGY_FILE  = join(process.cwd(), 'runs', 'learning', 'bd_strategy.json');
const LEARNING_FILE  = join(process.cwd(), 'runs', 'learning', 'bd_learning.json');

// Tolerant JSON parser — handles all common DeepSeek JSON quirks
function parseLlmJson(text) {
  let s = (text || '').trim();
  // Strip markdown code fences
  s = s.replace(/^```(?:json)?\s*\n?/i, '').replace(/\n?\s*```\s*$/i, '');
  // Strip any leading prose before the first {
  const firstBrace = s.indexOf('{');
  if (firstBrace > 0) s = s.substring(firstBrace);
  // Strip trailing text after the last }
  const lastBrace = s.lastIndexOf('}');
  if (lastBrace > 0 && lastBrace < s.length - 1) s = s.substring(0, lastBrace + 1);

  function clean(str) {
    let c = str;
    // Strip single-line JS/C++ comments
    c = c.replace(/\/\/[^\n]*/g, '');
    // Strip trailing commas before } or ]
    c = c.replace(/,(\s*[}\]])/g, '$1');
    // Fix common DeepSeek issue: unquoted number ranges like 0-100 → "0-100"
    c = c.replace(/:(\s*)(\d+-\d+)/g, ':$1"$2"');
    // Fix: bare values like HOT or WARM without quotes (common DeepSeek bug)
    c = c.replace(/:(\s*)(HOT|WARM|COLD|TENDER|STRATEGIC|PROACTIVE|high|medium|low)(\s*[,}\]])/g, ':$1"$2"$3');
    // Replace control characters inside strings
    c = c.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, ' ');
    return c;
  }

  try {
    return JSON.parse(clean(s));
  } catch {
    // Last resort: extract the outermost { ... } block and try again
    const m = s.match(/\{[\s\S]*\}/);
    if (!m) throw new Error('No JSON object found in LLM response');
    try { return JSON.parse(clean(m[0])); }
    catch (e) {
      // Final attempt: try fixing unescaped newlines inside string values
      let last = clean(m[0]).replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
      try { return JSON.parse(last); }
      catch (e2) { throw new Error('JSON parse failed after cleanup: ' + e2.message); }
    }
  }
}

function ensureDir() {
  const dir = join(process.cwd(), 'runs', 'learning');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}
const _BD_REDIS_KEYS = {
  [PIPELINE_FILE]: 'crucix:bd_pipeline',
  [STRATEGY_FILE]: 'crucix:bd_strategy',
  [LEARNING_FILE]: 'crucix:bd_learning',
};

function atomicWrite(path, data) {
  ensureDir();
  const tmp = path + '.tmp';
  writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
  renameSync(tmp, path);
  const key = _BD_REDIS_KEYS[path];
  if (key) redisSet(key, data).catch(() => {});
}

// ── Startup restore from Redis ────────────────────────────────────────────────
export async function initBDStore() {
  ensureDir();
  const files = [
    { path: PIPELINE_FILE, key: 'crucix:bd_pipeline', fallback: [] },
    { path: STRATEGY_FILE, key: 'crucix:bd_strategy', fallback: null },
    { path: LEARNING_FILE, key: 'crucix:bd_learning', fallback: {} },
  ];
  for (const f of files) {
    if (!existsSync(f.path)) {
      const remote = await redisGet(f.key);
      if (remote !== null) {
        const tmp = f.path + '.tmp';
        writeFileSync(tmp, JSON.stringify(remote, null, 2), 'utf8');
        renameSync(tmp, f.path);
        console.log(`[Persist] Restored ${f.key} from Redis`);
      }
    }
  }
}
function loadJson(path, fallback) {
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return fallback; }
}

// ── Procurement keyword sets (expanded for African + Lusophone markets) ───────
const TENDER_KW = [
  'tender', 'rfp', 'rfq', 'request for proposal', 'request for quotation',
  'itb', 'invitation to bid', 'solicitation', 'call for bids', 'call for proposals',
  // Regional / language variants
  "appel d'offres", 'licitação', 'aviso de licitação', 'concurso público',
  // Defence-specific
  'defence procurement', 'defense procurement', 'military procurement',
  'defence acquisition', 'defense acquisition', 'armed forces tender',
];
const CONTRACT_KW = [
  'contract award', 'contract awarded', 'signed contract', 'awarded to',
  'selected supplier', 'won contract', 'purchase agreement',
  // Expanded
  'mou signed', 'memorandum of understanding', 'letter of intent',
  'delivery contract', 'supply contract', 'service contract',
  'procurement completed', 'procurement finalised', 'procurement finalized',
  'sole source', 'direct award', 'emergency procurement',
  'oem selected', 'vendor selected', 'prime contractor', 'has procured',
];
const BUDGET_KW = [
  'defence budget', 'defense budget', 'military spending', 'allocated',
  'appropriation', 'procurement fund', 'capital expenditure',
  // Operational / capability
  'counterinsurgency funding', 'counter-terrorism funding',
  'modernisation fund', 'equipment fund', 'capital project',
  'peacekeeping deployment', 'capability gap', 'capability requirement',
  'foreign military aid', 'fms notification', 'foreign military sales',
  'end-user certificate', 'end user certificate',
  'defence cooperation agreement', 'defense cooperation agreement',
];
const STRATEGIC_KW = [
  'modernisation', 'modernization', 'upgrade programme', 'capability development',
  'bilateral defence', 'defense cooperation', 'military aid', 'equipment donation',
  // Urgency / crisis signals
  'conflict escalation', 'insurgency', 'terrorism', 'military incident',
  'maritime dispute', 'border incident', 'security threat',
  'rapid response', 'emergency deployment', 'force deployment',
  'peacekeeping', 'troop deployment', 'urgent procurement', 'accelerated procurement',
  'arms deal', 'defence pact', 'defense pact', 'military agreement',
];

// Defense relevance — at least one of these must match to accept a contract/tender
const DEFENSE_RELEVANCE_KW = [
  'defence', 'defense', 'military', 'army', 'navy', 'air force', 'armed forces',
  'weapon', 'arms', 'ammunition', 'missile', 'aircraft', 'vehicle', 'armour', 'armor',
  'security', 'surveillance', 'intelligence', 'police', 'border', 'coast guard',
  'fms', 'dsca', 'nato', 'peacekeep', 'counterterror', 'counter-terror',
  'sandf', 'fadm', 'faf', 'fds', 'faa ', ' fap ', 'paigc',
  'drone', 'uav', 'patrol', 'fighter', 'helicopter', 'radar', 'frigate',
  'counter-narcotics', 'mine clearance', 'special forces', 'commando',
];

// Non-defense sources — exclude only if text lacks defense relevance
const NON_DEFENSE_SOURCES = ['world bank', 'worldbank', 'iadb', 'imf ',
  'unicef', 'unhcr', 'wfp', 'who ', 'undp ', 'usaid',
  'isdb', 'ebrd', 'european investment bank',
  'sanitation', 'health program', 'water supply'];

// Political party / pure opinion domains — NEVER a real procurement source
const HARD_BLOCK_DOMAINS = [
  'da.org.za', 'anc.org.za', 'eff.org.za', 'acdp.org.za', 'cope.org.za',
  'dailymaverick.co.za', 'timeslive.co.za', 'dailymail.co.uk',
  'huffpost.com', 'politico.com', 'theintercept.com',
];

// Phrases indicating coverage ABOUT a tender, not the tender itself
const NEGATIVE_COVERAGE_KW = [
  'slams', 'slammed', 'criticis', 'criticized', 'reckless', 'unfair', 'irregular',
  'scandal', 'corruption', 'probe', 'investigat', 'parliament questions', 'opposition',
  'halted', 'cancelled', 'suspended', 'scrapped', 'overpriced', 'inflated',
  'audit finding', 'whistleblow', 'leaked document',
];

function kwScore(text, kwList) {
  const lower = text.toLowerCase();
  return kwList.filter(k => lower.includes(k)).length;
}

function hasDefenseRelevance(text) {
  const lower = (text || '').toLowerCase();
  return DEFENSE_RELEVANCE_KW.some(k => lower.includes(k));
}

function isNegativeCoverage(text, url) {
  const lower  = (text || '').toLowerCase();
  const domain = (() => { try { return new URL(url || '').hostname.replace(/^www\./, ''); } catch { return ''; } })();
  // Hard block: political parties, pure opinion sites — never a procurement source
  if (HARD_BLOCK_DOMAINS.includes(domain)) return true;
  // Negative coverage keywords: article is about a tender problem, not a real opportunity
  if (NEGATIVE_COVERAGE_KW.some(k => lower.includes(k))) return true;
  return false;
}

// ── Extract real tenders from procurementTenders source ──────────────────────
function extractRealTenders(procurementTenders, targetMarkets) {
  const tenders = [];
  if (!procurementTenders) return tenders;

  const allUpdates = [
    ...(procurementTenders.updates   || []),
    ...(procurementTenders.lusophone || []),
    ...(procurementTenders.africa    || []),
  ];

  for (const item of allUpdates) {
    const text    = ((item.title || '') + ' ' + (item.content || item.text || item.summary || '')).toLowerCase();

    // Skip non-defense sources ONLY if text lacks defense relevance
    // (AfDB/World Bank sometimes fund security-sector projects — don't blindly reject)
    const srcLower = (item.source || '').toLowerCase();
    const isNonDefSrc = NON_DEFENSE_SOURCES.some(s => srcLower.includes(s));
    if (isNonDefSrc && !hasDefenseRelevance(text)) continue;

    // Skip news coverage ABOUT tenders (political criticism, investigations, opinion)
    if (isNegativeCoverage(text, item.url || item.link)) continue;

    const tScore  = kwScore(text, TENDER_KW);
    const cScore  = kwScore(text, CONTRACT_KW);
    const bScore  = kwScore(text, BUDGET_KW);
    const total   = tScore + cScore + bScore;
    if (total === 0) continue;

    // Require defense relevance — prevents development bank projects slipping in
    if (!hasDefenseRelevance(text)) continue;

    // Match to a target market
    const market  = targetMarkets.find(m => text.includes(m.name.toLowerCase()) || text.includes((m.iso2 || '').toLowerCase()));

    const type = tScore > 0 ? 'TENDER' : cScore > 0 ? 'CONTRACT' : 'BUDGET';

    // Lead quality classification — determines how actionable this signal is
    const hasUrl = !!(item.url || item.link);
    const isStrategicMarket = market?.priority === 'HIGH' || market?.lusophone;
    let leadQuality;
    if (hasUrl && tScore >= 2 && isStrategicMarket) leadQuality = 'HOT';
    else if (hasUrl && total >= 2) leadQuality = 'WARM';
    else leadQuality = 'WATCH';

    tenders.push({
      type,
      market:      market?.name || 'Global',
      iso2:        market?.iso2 || null,
      lusophone:   market?.lusophone || false,
      title:       (item.title || '').substring(0, 150),
      summary:     (item.content || item.text || item.summary || '').substring(0, 400),
      url:         item.url || item.link || '',
      source:      item.source || procurementTenders.source || 'Procurement Feed',
      date:        item.timestamp ? new Date(item.timestamp).toISOString().substring(0, 10) : null,
      score:       total * 10 + (market?.lusophone ? 15 : 0) + (market?.priority === 'HIGH' ? 10 : 0),
      priority:    market?.priority || 'MEDIUM',
      leadQuality,
      procurementPortal: market?.procurementPortal || null,
    });
  }

  return tenders.sort((a, b) => b.score - a.score);
}

// ── Build strategic ideas from intelligence convergence ───────────────────────
function buildStrategicIdeas(currentData, targetMarkets, existingTenderMarkets) {
  const ideas = [];

  // Aggregate ALL available signal sources into one unified list
  const allItems = [];

  // RSS news
  for (const n of (currentData.news || [])) {
    allItems.push({ title: n.title || '', text: n.summary || n.description || '', url: n.url || '', src: 'news' });
  }
  // Unified news feed
  for (const n of (currentData.newsFeed || [])) {
    allItems.push({ title: n.title || '', text: n.description || n.text || '', url: n.url || '', src: 'newsFeed' });
  }
  // OSINT Telegram signals (urgent + top) — these are the primary OSINT source
  for (const s of (currentData.tg?.urgent || [])) {
    allItems.push({ title: (s.text || '').substring(0, 80), text: s.text || '', url: s.url || '', src: s.channel || 'OSINT' });
  }
  for (const s of (currentData.tg?.top || [])) {
    allItems.push({ title: (s.text || '').substring(0, 80), text: s.text || '', url: s.url || '', src: s.channel || 'OSINT' });
  }
  // Defence news (defenseNews is an object with .updates array, NOT an array itself)
  var defNewsArr = Array.isArray(currentData.defenseNews) ? currentData.defenseNews : (currentData.defenseNews?.updates || []);
  for (const d of defNewsArr) {
    allItems.push({ title: d.title || '', text: d.content || d.description || d.summary || '', url: d.url || d.link || '', src: d.source || 'DefenseNews' });
  }
  // Defence events (same pattern — might be object with .updates or direct array)
  var defEventsArr = Array.isArray(currentData.defenseEvents) ? currentData.defenseEvents : (currentData.defenseEvents?.updates || []);
  for (const e of defEventsArr) {
    allItems.push({ title: e.title || '', text: e.content || e.description || '', url: e.url || '', src: 'DefenseEvent' });
  }
  // Procurement portal monitoring results
  for (const p of (currentData.procurementPortals?.items || [])) {
    allItems.push({ title: p.title || '', text: p.description || p.text || '', url: p.url || p.link || '', src: p.source || 'ProcurementPortal' });
  }
  // Regional correlations (high-value intelligence convergence signals)
  for (const c of (currentData.correlations || [])) {
    const cText = (c.topSignals || []).map(s => s.text || '').join(' ');
    allItems.push({ title: c.region + ' — ' + (c.severity || 'signal'), text: cText, url: '', src: 'correlation' });
  }

  // Index all items by target market
  const marketSignals = {};
  for (const item of allItems) {
    const combined = (item.title + ' ' + item.text).toLowerCase();
    for (const market of targetMarkets) {
      if (!combined.includes(market.name.toLowerCase())) continue;
      const key = market.name;
      if (!marketSignals[key]) marketSignals[key] = { market, items: [], stratScore: 0, budgetScore: 0 };
      marketSignals[key].items.push({ title: item.title, url: item.url, text: item.text, src: item.src });
      marketSignals[key].stratScore  += kwScore(combined, STRATEGIC_KW);
      marketSignals[key].budgetScore += kwScore(combined, BUDGET_KW);
    }
  }

  // Explorer insights
  const explorerInsights = currentData.explorerFindings?.findings?.insights
    || currentData.explorerFindings?.insights || [];
  for (const ins of explorerInsights) {
    const combined = ((ins.title || '') + ' ' + (ins.summary || '')).toLowerCase();
    for (const market of targetMarkets) {
      if (!combined.includes(market.name.toLowerCase())) continue;
      const key = market.name;
      if (!marketSignals[key]) marketSignals[key] = { market, items: [], stratScore: 0, budgetScore: 0 };
      marketSignals[key].items.push({ title: ins.title, url: ins.sourceUrl || '', text: ins.summary || '', src: 'explorer' });
      marketSignals[key].stratScore += ins.relevance === 'HIGH' ? 3 : ins.relevance === 'MEDIUM' ? 2 : 1;
    }
  }

  // Explorer salesIdeas — pre-qualified by LLM, highest value signal (Unit 1)
  const explorerSalesIdeas = currentData.explorerFindings?.findings?.salesIdeas
    || currentData.explorerFindings?.salesIdeas || [];
  for (const idea of explorerSalesIdeas) {
    const marketName = (idea.market || '').trim();
    const market = targetMarkets.find(m => m.name.toLowerCase() === marketName.toLowerCase());
    if (!market) continue;
    const key = market.name;
    if (!marketSignals[key]) marketSignals[key] = { market, items: [], stratScore: 0, budgetScore: 0 };
    marketSignals[key].items.push({
      title: idea.title || idea.lead || marketName, url: idea.portalUrl || '',
      text: idea.rationale || idea.summary || '', src: 'explorerSalesIdea',
    });
    marketSignals[key].stratScore += 5; // higher than insights — pre-qualified
    if (idea.estimatedValue) marketSignals[key].budgetScore += 3;
  }

  // AfDB/dev finance signals
  const afdbItems = (currentData.afdb?.projects || currentData.afdb?.updates || []);
  for (const proj of afdbItems) {
    const text = ((proj.country || '') + ' ' + (proj.title || '') + ' ' + (proj.sector || '')).toLowerCase();
    if (!text.match(/security|defence|defense|military|police|border|navy|air force/)) continue;
    for (const market of targetMarkets) {
      if (!text.includes(market.name.toLowerCase())) continue;
      const key = market.name;
      if (!marketSignals[key]) marketSignals[key] = { market, items: [], stratScore: 0, budgetScore: 0 };
      marketSignals[key].budgetScore += 3;
      marketSignals[key].items.push({ title: proj.title || 'AfDB security-sector project', url: proj.url || '', text: proj.sector || '', src: 'AfDB' });
    }
  }

  // Build strategic ideas from markets that have signals but no real tender yet
  for (const [marketName, intel] of Object.entries(marketSignals)) {
    if (existingTenderMarkets.has(marketName)) continue; // Already in active deals
    // Weight budget signals highest (most concrete), then strategy, then sourced items only
    const sourcedItems = intel.items.filter(i => i.url).length;
    const totalScore = intel.budgetScore * 4 + intel.stratScore * 3 + sourcedItems * 3 + Math.min(6, intel.items.length);
    if (totalScore < 8) continue;  // Higher threshold — fewer but better ideas

    const market = intel.market;
    const sources = intel.items.filter(i => i.url).slice(0, 4);
    const hasFinance = intel.budgetScore > 0;

    // Determine what the intelligence is saying
    const stratType = intel.budgetScore > intel.stratScore
      ? 'Budget/Finance Signal'
      : intel.stratScore > 0
        ? 'Modernisation/Cooperation Signal'
        : 'Intelligence Convergence';

    ideas.push({
      market:     marketName,
      iso2:       market.iso2,
      lusophone:  market.lusophone,
      type:       stratType,
      rationale:  _buildRationale(intel, market),
      signals:    intel.items.length,
      score:      Math.min(95, totalScore + (market.lusophone ? 12 : 0) + (market.priority === 'HIGH' ? 8 : 0)),
      priority:   market.priority,
      sources,
      devFinance: hasFinance,
      actionStep: _suggestAction(market, intel),
    });
  }

  return ideas.sort((a, b) => b.score - a.score);
}

function _buildRationale(intel, market) {
  const parts = [];
  if (intel.budgetScore > 0) parts.push(`budget/finance activity detected`);
  if (intel.stratScore > 0)  parts.push(`modernisation or cooperation signals present`);
  if (intel.items.length > 2) parts.push(`${intel.items.length} intelligence items mention this market`);
  if (market.lusophone) parts.push(`Lusophone linguistic advantage applies`);
  return parts.length > 0
    ? parts.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('. ') + '.'
    : `${intel.items.length} intelligence signal(s) detected — worth qualifying.`;
}

function _suggestAction(market, intel) {
  if (intel.budgetScore > 2) return `Identify defence budget cycle and contact MoD budget office in ${market.name}`;
  if (intel.stratScore > 1)  return `Review modernisation programme scope and identify partner OEMs for ${market.name}`;
  if (market.lusophone)      return `Leverage Lusophone network for introductory meeting with ${market.name} armed forces`;
  return `Conduct targeted intelligence sweep on ${market.name} procurement pipeline`;
}

// ── LLM-enhanced strategy layer ───────────────────────────────────────────────
async function llmStrategicAnalysis(llmProvider, tenders, ideas, currentData) {
  if (!llmProvider?.isConfigured) return null;

  const context = [
    '=== ACTIVE TENDERS ===',
    tenders.slice(0, 5).map(t =>
      `[${t.type}] ${t.market}: ${t.title} — ${t.summary?.substring(0, 200)}`
    ).join('\n'),
    '\n=== STRATEGIC IDEAS ===',
    ideas.slice(0, 6).map(i =>
      `${i.market} (score ${i.score}): ${i.rationale}`
    ).join('\n'),
    '\n=== MARKET CONTEXT ===',
    `Global military spending: $2.44T (+7% YoY). Top importers: Saudi Arabia (8.4%), India (7.3%), Qatar (6.7%).`,
    `VIX: ${currentData.fred?.find(f => f.id === 'VIXCLS')?.value || 'N/A'}`,
    `Energy: Brent $${currentData.energy?.brent || 'N/A'}, WTI $${currentData.energy?.wti || 'N/A'}`,
  ].join('\n');

  const systemPrompt = `You are the Chief Strategy Officer of Arkmurus, a defense brokering firm.
Core competency: Lusophone Africa (Angola, Mozambique, Guinea-Bissau, Cape Verde, Brazil).
Global mandate: any non-embargoed, export-control permissible market.
Strictly excluded: Russia, Belarus, Iran, North Korea, Syria, Myanmar, Sudan.

Analyze the current intelligence and generate:
1. TOP PRIORITY (1 specific deal to pursue this week — named buyer, product, OEM, first action)
2. STRATEGIC MOVES (2-3 medium-term positioning recommendations based on market trends)
3. RISK WATCH (1-2 things that could close current windows — sanctions, political risk, competitor moves)

Be specific. Name companies, ministries, people where visible. No generic advice.
Output as JSON: { "topPriority": {...}, "strategicMoves": [...], "riskWatch": [...] }`;

  try {
    const result = await llmProvider.complete(systemPrompt, context, { maxTokens: 1500, timeout: 60000 });
    return parseLlmJson(result.text || '');
  } catch (err) {
    console.warn('[BD] LLM strategy failed (non-fatal):', err.message);
    return null;
  }
}

// ── Learning Store ────────────────────────────────────────────────────────────
// Self-updating memory: market productivity, win rates, signal patterns, source quality

function getlearning() {
  return loadJson(LEARNING_FILE, {
    version:           1,
    marketScores:      {},   // market → { opportunities, signals, lastSeen, avgScore, winRate }
    sourceQuality:     {},   // source → { hits, leads, actionable }
    signalPatterns:    [],   // [{ pattern, leadTimeDays, confidence }]
    outcomes:          [],   // [{ dealId, market, type, outcome, reason, ts }]
    totalSearches:     0,
    lastThought:       null, // last LLM autonomous reasoning output
  });
}

function saveLearning(data) {
  atomicWrite(LEARNING_FILE, data);
}

function updateMarketScore(market, score, hasUrl) {
  const L = getlearning();
  if (!L.marketScores[market]) L.marketScores[market] = { opportunities: 0, leads: 0, signals: 0, totalScore: 0, lastSeen: null, winRate: 0 };
  const m = L.marketScores[market];
  m.opportunities++;
  if (hasUrl) m.leads++;
  m.totalScore += score;
  m.avgScore    = Math.round(m.totalScore / m.opportunities);
  m.lastSeen    = new Date().toISOString();
  L.totalSearches++;
  saveLearning(L);
}

function recordWinProbTrend(market, winProb) {
  const L = getlearning();
  if (!L.winProbHistory) L.winProbHistory = {};
  if (!L.winProbHistory[market]) L.winProbHistory[market] = [];
  const today = new Date().toISOString().substring(0, 10);
  // Keep one entry per day per market — replace if same day
  const hist = L.winProbHistory[market];
  const lastIdx = hist.length - 1;
  if (lastIdx >= 0 && hist[lastIdx].date === today) {
    // Average with today's existing value
    hist[lastIdx].prob = Math.round((hist[lastIdx].prob + winProb) / 2);
  } else {
    hist.push({ date: today, prob: winProb });
    // Keep 90 days max
    if (hist.length > 90) hist.splice(0, hist.length - 90);
  }
  saveLearning(L);
}

export function recordOutcome(dealId, market, type, outcome, reason = '') {
  const L = getlearning();
  L.outcomes.unshift({ dealId, market, type, outcome, reason, ts: new Date().toISOString() });
  if (L.outcomes.length > 200) L.outcomes.splice(200);
  // Update market win rate
  const marketOutcomes = L.outcomes.filter(o => o.market === market);
  const wins           = marketOutcomes.filter(o => o.outcome === 'WON').length;
  if (!L.marketScores[market]) L.marketScores[market] = { opportunities: 0, leads: 0, signals: 0, totalScore: 0, lastSeen: null, winRate: 0 };
  L.marketScores[market].winRate = marketOutcomes.length > 0 ? Math.round((wins / marketOutcomes.length) * 100) : 0;
  saveLearning(L);

  // FEEDBACK LOOP: also record into alert learning system so adaptive weights update
  const alertOutcome = outcome === 'WON' ? 'confirmed' : 'dismissed';
  try {
    recordAlertOutcome(dealId, `${type}: ${market} — ${reason || dealId}`, alertOutcome, {
      source: 'bd_pipeline', region: market, tier: 'bd',
    });
  } catch (e) { /* non-fatal */ }

  // PROMPT OPTIMIZER: every outcome trains the system to improve recommendations
  try {
    recordOutcomeForOptimization(market, type, outcome, true, L.marketScores[market]?.winRate || 0);
  } catch (e) { /* non-fatal */ }

  // TRAINING DATA: record for future proprietary LLM fine-tuning
  try {
    recordOutcomeTraining(market, type, outcome, true, reason, L.marketScores[market]?.winRate || 0);
  } catch (e) { /* non-fatal */ }
}

export function getLearningStats() {
  const L = getlearning();
  // Summarise win prob trends — last 30 days per market, top 5 markets by recency
  const trendSummary = Object.entries(L.winProbHistory || {})
    .filter(([, hist]) => hist.length > 0)
    .sort((a, b) => (b[1].at(-1)?.date || '') > (a[1].at(-1)?.date || '') ? 1 : -1)
    .slice(0, 5)
    .map(([market, hist]) => ({
      market,
      trend: hist.slice(-30),
      latest: hist.at(-1)?.prob,
      direction: hist.length >= 2
        ? (hist.at(-1).prob > hist.at(-2).prob ? 'rising' : hist.at(-1).prob < hist.at(-2).prob ? 'falling' : 'stable')
        : 'stable',
    }));
  return { ...L, trendSummary };
}

// ── Win Probability Engine ────────────────────────────────────────────────────
// Scores each opportunity 0-100 based on Arkmurus strengths, market fit, timing

// ── Separated scoring: Market Opportunity vs Arkmurus Relationship Edge ──────
// This separation is what makes the model honest about where Arkmurus is
// the incumbent vs where it's entering cold.

// ── BROKER MODEL SCORING ─────────────────────────────────────────────────────
// Arkmurus is a BROKER — a vehicle to support transactions, not an OEM.
// Win probability = can we find the right supplier × can we connect the parties × do we add value?
//
// Three independent score dimensions:
// 1. RELATIONSHIP: How well do we know this market's decision-makers?
// 2. BROKER_VALUE: Can we add value here? (supplier matching, JV creation, compliance nav)
// 3. PARTNERSHIP: Can we create synergies with OEMs/local partners to fill gaps?

const ARKMURUS_RELATIONSHIP = {
  // TIER 1: INCUMBENT — Lusophone core (genuine moat)
  lusophone: 25, angola: 22, mozambique: 20, portugal: 18,
  'guinea-bissau': 16, 'cape verde': 14, 'sao tome': 14, brazil: 12,
  // TIER 2: ESTABLISHED — regular engagement
  'south africa': 10, kenya: 8, nigeria: 8, sadc: 10,
  // TIER 3: DEVELOPING — building presence
  senegal: 6, ghana: 5, rwanda: 5, uganda: 5, tanzania: 5,
  'cote d\'ivoire': 5, cameroon: 4, ethiopia: 4, libya: 3,
  // TIER 4: COLD ENTRY — need right partner to compete
  indonesia: 2, philippines: 2, vietnam: 2, bangladesh: 2,
  uae: 2, 'saudi arabia': 1, jordan: 2, colombia: 2, peru: 2,
  poland: 1, romania: 1, greece: 1, bulgaria: 1,
};

// BROKER_VALUE: Where does a broker/intermediary add the most value?
// High value = complex multi-party deals, offset navigation, compliance expertise
// Low value = simple direct sales where buyer goes direct to OEM
const BROKER_VALUE_FACTORS = {
  'offset required':     12,  // Complex offset → broker navigates bureaucracy
  'multi-oem':           10,  // Package deal from multiple OEMs → broker assembles
  'compliance complex':   8,  // ITAR/EAR/dual-use → broker manages licences
  'language barrier':     8,  // Portuguese/French market + non-local OEM → broker translates
  'jv potential':        10,  // Local assembly/JV opportunity → broker connects parties
  'financing needed':     8,  // Concessional financing required → broker arranges
  'first time buyer':     6,  // New customer for this product → broker qualifies
  'russian replacement':  8,  // Replacing sanctioned Russian kit → broker finds alternatives
};

// PARTNERSHIP_POTENTIAL: Score for JV/partnership opportunities per market
// These are markets where the RIGHT PARTNER transforms cold entry into warm opportunity
const PARTNERSHIP_MULTIPLIERS = {
  // Lusophone → Portuguese OEMs/local partners amplify our advantage
  'Angola': 1.3, 'Mozambique': 1.3,
  // Markets where a JV with local industry is the ONLY way to win
  'Brazil': 1.4, 'Indonesia': 1.5, 'Saudi Arabia': 1.5, 'UAE': 1.4,
  // Markets where Turkish/South African partner opens doors
  'Nigeria': 1.2, 'Kenya': 1.2, 'Ghana': 1.1, 'Senegal': 1.1,
  // Markets where Korean partner is strongest angle
  'Philippines': 1.3, 'Poland': 1.3, 'Romania': 1.2,
};

// Backward-compatible ARKMURUS_STRENGTHS for scoreWinProbability()
const ARKMURUS_STRENGTHS = {
  lusophone: 25, angola: 22, mozambique: 20, portugal: 18,
  'guinea-bissau': 16, 'cape verde': 14, 'sao tome': 14, brazil: 12,
  'south africa': 10, kenya: 8, nigeria: 8, sadc: 10,
  senegal: 6, ghana: 5, rwanda: 5, uganda: 5, tanzania: 5,
  'cote d\'ivoire': 5, cameroon: 4, ethiopia: 4, libya: 3,
  indonesia: 2, philippines: 2, vietnam: 2, bangladesh: 2,
  uae: 2, 'saudi arabia': 1, jordan: 2,
  'counter-terrorism': 8, maritime: 7, peacekeeping: 8,
};

const ARKMURUS_PRODUCTS = [
  // Mobility
  'vehicle', 'armoured', 'armored', 'protected mobility', 'apc', 'ifv', 'mrap',
  'helicopter', 'rotorcraft', 'transport aircraft',
  // ISR + air
  'uav', 'drone', 'surveillance', 'radar', 'air defense', 'air defence',
  'counter-drone', 'anti-drone', 'counter-uas', 'fighter', 'aircraft',
  // Maritime
  'patrol boat', 'patrol vessel', 'coast guard', 'naval', 'maritime', 'frigate',
  // C4ISR
  'border security', 'c2', 'communications', 'tactical radio', 'secure comms',
  // Firepower
  'ammunition', 'small arms', 'mortar', 'artillery', 'grenade',
  // Protection
  'personal protection', 'ppe', 'body armor', 'body armour', 'helmet', 'ballistic',
  // Medical
  'medical', 'field hospital',
  // Support
  'training', 'logistics', 'sustainment', 'spare parts', 'maintenance',
  'engineering equipment', 'mine clearance', 'counter-ied',
];

function scoreWinProbability(tender, markets) {
  const L = getlearning();
  const text = (tender.title + ' ' + (tender.summary || '')).toLowerCase();
  const marketName = tender.market || '';
  const marketLower = marketName.toLowerCase();
  let score = 20; // base — broker starts lower than OEM, must earn score

  // ── 1. RELATIONSHIP SCORE (do we know the decision-makers?) ──
  for (const [key, bonus] of Object.entries(ARKMURUS_RELATIONSHIP)) {
    if (marketLower.includes(key) || text.includes(key)) { score += bonus; break; }
  }

  // ── 2. BROKER VALUE SCORE (can we add value as intermediary?) ──
  let brokerValue = 0;
  // Offset required? Broker navigates
  const marketDef = markets.find(m => m.name === marketName);
  if (marketDef && ['Indonesia', 'Saudi Arabia', 'UAE', 'Brazil', 'South Africa'].includes(marketName)) brokerValue += BROKER_VALUE_FACTORS['offset required'];
  // Multi-product need? Broker assembles package
  const productMatch = ARKMURUS_PRODUCTS.filter(p => text.includes(p)).length;
  if (productMatch >= 2) brokerValue += BROKER_VALUE_FACTORS['multi-oem'];
  else if (productMatch >= 1) brokerValue += 5;
  // Compliance complexity? (ITAR mention, export control, dual-use)
  if (text.match(/itar|export control|dual.use|licence|license|fms|dsca/)) brokerValue += BROKER_VALUE_FACTORS['compliance complex'];
  // Language barrier? (Lusophone market + non-Portuguese OEM likely)
  if (ARKMURUS_RELATIONSHIP[marketLower] >= 12) brokerValue += BROKER_VALUE_FACTORS['language barrier'];
  // Russian replacement? (sanctions created opportunity)
  if (text.match(/replac|russian|soviet|su-|mi-|mig-/)) brokerValue += BROKER_VALUE_FACTORS['russian replacement'];
  // JV/local content mentioned?
  if (text.match(/joint venture|local content|offset|technology transfer|co-production/)) brokerValue += BROKER_VALUE_FACTORS['jv potential'];
  score += Math.min(25, brokerValue);

  // ── 3. PARTNERSHIP MULTIPLIER (right partner transforms cold into warm) ──
  const partnerMultiplier = PARTNERSHIP_MULTIPLIERS[marketName] || 1.0;
  score = Math.round(score * partnerMultiplier);

  // ── 4. DEAL QUALITY SIGNALS ──
  if (tender.url) score += 5;                           // Verifiable source
  if (tender.leadQuality === 'HOT') score += 8;         // Pre-qualified
  else if (tender.leadQuality === 'WARM') score += 4;

  // Historical win rate for this market (learning)
  const mScore = L.marketScores[marketName];
  if (mScore?.winRate > 0) score += Math.min(10, mScore.winRate / 10);

  // Recency — recent signals are hotter
  if (tender.date) {
    const ageDays = (Date.now() - new Date(tender.date).getTime()) / 86400000;
    if (ageDays < 7) score += 8;
    else if (ageDays < 30) score += 4;
    else if (ageDays > 90) score -= 8;
  }

  // Priority market boost
  if (marketDef?.priority === 'HIGH') score += 6;

  return Math.max(5, Math.min(92, Math.round(score)));
}

// ── Autonomous LLM Brain ──────────────────────────────────────────────────────
// The "thinking" layer: receives all intelligence, reasons about what to do,
// identifies what it doesn't know, plans next steps, and learns from patterns.

async function runAutonomousBrain(llmProvider, tenders, ideas, pipeline, currentData, learningData) {
  if (!llmProvider?.isConfigured) return null;

  // Build rich context
  const topTenders = tenders.slice(0, 6).map(t => ({
    type:     t.type,
    market:   t.market,
    title:    t.title.substring(0, 120),
    url:      t.url ? '✓ verified link' : '✗ no source link',
    score:    t.score,
    winProb:  t.winProbability,
    priority: t.priority,
  }));

  const topIdeas = ideas.slice(0, 5).map(i => ({
    market:   i.market,
    type:     i.type,
    signals:  i.signals,
    score:    i.score,
    action:   i.actionStep,
  }));

  const pipelineActive = pipeline.filter(d => !['CLOSED_WON','CLOSED_LOST','NO_BID'].includes(d.stage)).slice(0, 8);

  const topMarkets = Object.entries(learningData.marketScores || {})
    .sort((a,b) => (b[1].avgScore || 0) - (a[1].avgScore || 0))
    .slice(0, 6)
    .map(([m, s]) => ({ market: m, opportunities: s.opportunities, winRate: s.winRate, lastSeen: s.lastSeen?.substring(0, 10) }));

  const recentOutcomes = (learningData.outcomes || []).slice(0, 5).map(o => ({
    market: o.market, outcome: o.outcome, reason: o.reason, type: o.type,
  }));

  // Win probability trend context
  const trendLines = (learningData.trendSummary || [])
    .map(t => `  ${t.market}: ${t.direction} (latest ${t.latest ?? '?'}%, ${t.trend?.length || 0} days tracked)`)
    .join('\n');
  const trendContext = trendLines
    ? `\nWin Probability Trends (last 30 days — your own learning data):\n${trendLines}`
    : '';

  // Procurement portal URLs for top markets — where to find official tenders
  const portalLines = _DEFAULT_MARKETS
    .filter(m => m.priority === 'HIGH' || m.lusophone)
    .map(m => `  ${m.name}: ${m.procurementPortal || 'no portal recorded'}`)
    .join('\n');

  // OEM and contract benchmarks for the brain to size deals accurately
  const benchmarks = `
CONTRACT BENCHMARKS (recent comparable deals — use for value estimation):
  Small arms (5M rounds): $15-25M
  Protected vehicles (50-100 unit): $80-120M
  UAV programme (full system): $40-80M
  Radar/air defense system: $60-150M
  Patrol vessels (2-4 unit): $50-150M
  Ammunition (annual supply): $20-50M
  Training programmes: $5-15M
  Border security system: $30-80M
  Helicopter fleet (4-8 unit): $100-250M

OEM EXPORT TRACK RECORD (verified sales to Africa):
  Paramount Group (ZA): protected vehicles → Nigeria, Kenya, Mozambique
  Turkish Aerospace/Baykar: UAVs → Nigeria, Ethiopia, Angola, Morocco
  Damen (NL): patrol vessels → multiple African coast guards
  Norinco (CN): armoured vehicles, small arms → African Union members
  Embraer (BR): Super Tucano → Nigeria, Angola, Mozambique
  Leonardo (IT): M-346 → Nigeria (2026 deal), helicopters → various
  Elbit (IL): surveillance, UAVs → various African markets
  Rheinmetall (DE): Fuchs APC → various, training → SADC

KNOWN COMPETITORS IN REGION:
  West Africa: Turkish OEMs (aggressive pricing), DynaCorp (US), Norinco
  Southern Africa: Armscor/Denel (ZA), Paramount, BAE Systems
  East Africa: Elbit (IL), Turkish drones, Chinese OEMs
  Lusophone: Brazilian OEMs have language advantage; compete on relationships`;


  // Explorer pre-qualified sales ideas (Unit 1) — highest value intelligence
  const explorerSalesIdeas = currentData.explorerFindings?.findings?.salesIdeas
    || currentData.explorerFindings?.salesIdeas || [];
  const explorerLeadsCtx = explorerSalesIdeas.length > 0
    ? `\nEXPLORER PRE-QUALIFIED LEADS (from web intelligence — already market-matched by LLM):\n${JSON.stringify(explorerSalesIdeas.slice(0, 5))}`
    : '';

  // Trend-driven context (Unit 5 + 6)
  let trendCtx = '';
  try {
    const { analyzeTrends } = await import('../intel/archive.mjs');
    const trends = analyzeTrends();
    if (trends) {
      const parts = [];
      if (trends.signals?.trend) parts.push(`Signal trend: ${trends.signals.trend.direction} (${trends.signals.trend.pct || 0}% 7d)`);
      if (trends.criticalChanges?.trend) parts.push(`Critical changes: ${trends.criticalChanges.trend.direction}`);
      if (trends.wti?.trend) parts.push(`Oil: ${trends.wti.trend.direction} (${trends.wti.trend.pct || 0}% 7d)`);
      if (trends.vix?.trend) parts.push(`VIX: ${trends.vix.trend.direction}`);
      if (parts.length) trendCtx = `\nMARKET TRENDS (7-day momentum): ${parts.join(' | ')}`;
    }
  } catch {}

  // Previous brain thought — so the brain can build on its own reasoning
  const L = getlearning();
  const prevThought = L.lastThought;
  const prevContext = prevThought?.result
    ? `\nPREVIOUS BRAIN ASSESSMENT (${prevThought.ts || 'unknown date'}):
  Weekly priority: ${prevThought.result.weeklyPriority?.action || 'none'}
  Strategy adjustment: ${prevThought.result.selfLearning?.strategyAdjustment || 'none'}
  Next sweep focus: ${prevThought.result.selfLearning?.nextSweepFocus || 'none'}
  Patterns observed: ${(prevThought.result.selfLearning?.patternsObserved || []).join('; ') || 'none'}
  Confidence: ${prevThought.result.confidence ?? 'unknown'}`
    : '';

  // Adaptive scoring weights — show the brain what the learning system has computed
  const adaptiveWeights = getAdaptiveScoringWeights();
  const weightContext = adaptiveWeights?.regionMultipliers
    ? `\nADAPTIVE WEIGHTS (outcome-learned, ${adaptiveWeights.sampleSize} outcomes):
${Object.entries(adaptiveWeights.regionMultipliers).map(([r, m]) => `  ${r}: ${m}x multiplier`).join('\n')}`
    : '';

  // HOT/WARM leads in context so brain knows what's already qualified
  const hotWarmTenders = tenders
    .filter(t => t.leadQuality === 'HOT' || t.leadQuality === 'WARM')
    .slice(0, 6)
    .map(t => ({
      quality: t.leadQuality,
      type:    t.type,
      market:  t.market,
      title:   t.title.substring(0, 120),
      url:     t.url || null,
      winProb: t.winProbability,
    }));

  const systemPrompt = `You are CRUCIX — the autonomous BD intelligence brain of Arkmurus, a specialist defence brokering firm.

ARKMURUS PROFILE:
- Core competency: Lusophone Africa (Angola, Mozambique, Guinea-Bissau, Cape Verde, São Tomé, Brazil)
- Products: armoured vehicles, UAVs, border security systems, ammunition, training, logistics, helicopters, naval vessels
- Approach: act as broker/intermediary between OEMs and end-user armed forces/MoDs
- Global scope: Lusophone Africa is core, but ALSO compete in West/East Africa, Southeast Asia, Middle East, Eastern Europe, Latin America
- Strictly excluded markets: Russia, Belarus, Iran, North Korea, Syria, Myanmar, Sudan
- Export control compliant — all UK/EU/US/EU Common Position regulations apply
- Multi-jurisdiction compliance: OFAC SDN, OFSI, State Dept DSP-5, EU dual-use, Brazil 100% offset (>$5M)

ARKMURUS RELATIONSHIP TIERS (be honest about where we have edge vs entering cold):
- INCUMBENT (Lusophone core — we are the go-to): Angola, Mozambique, Guinea-Bissau, Cape Verde, São Tomé, Brazil, Portugal
- ESTABLISHED (regular engagement): South Africa, Kenya, Nigeria, SADC members
- DEVELOPING (building presence): Senegal, Ghana, Rwanda, Uganda, Cameroon, Ethiopia, Libya
- COLD ENTRY (intelligence-driven, no relationships yet): Indonesia, Philippines, Vietnam, UAE, Saudi Arabia, Jordan, Poland, Romania, Greece

CRITICAL RULE: When recommending leads, ALWAYS state the relationship tier. For COLD ENTRY markets, explain what specific advantage Arkmurus brings that overcomes the relationship gap (product fit, pricing, OEM partnership, compliance expertise). Do NOT recommend cold-entry leads without a clear angle.

ARKMURUS IS A BROKER — not an OEM. We are a vehicle to support transactions:
- We FIND the right suppliers to fulfil a specific need
- We CREATE synergies between OEMs and end-users that wouldn't happen without us
- We NAVIGATE compliance, offset obligations, and multi-jurisdiction export controls
- We ASSEMBLE multi-OEM packages that no single manufacturer offers
- We BRIDGE language and cultural gaps (Portuguese, French, Arabic markets)
- We FORM JVs and partnerships with established firms to enter new markets together

YOUR JOB: Think like a seasoned BD director who builds deals through connections. Be commercially ruthless but legally compliant.
Produce REAL, SPECIFIC, ACTIONABLE sales intelligence. For every lead, identify:
- The BUYER (who specifically needs this)
- The SUPPLIER (which OEM fits best — name the company)
- The ARKMURUS VALUE-ADD (why does this deal need a broker? What do we bring?)
- The PARTNERSHIP ANGLE (is there a JV, local partner, or OEM collaboration that multiplies our chance?)
- The DEAL ECONOMICS (estimated value, commission potential, financing needs)

For Lusophone markets — leverage deep relationships and Portuguese language (our moat).
For cold-entry markets — identify which PARTNER (OEM, local agent, or JV) gets us into the room.
For complex deals — show how Arkmurus assembles the package no single OEM can offer alone.

LEAD QUALIFICATION RULES — a lead is only "actionable" if you answer ALL of these:
1. SPECIFIC BUYER — name the ministry/directorate (e.g., "Angola FAA Equipment Directorate", not just "Angola")
2. ESTIMATED VALUE — use the contract benchmarks below; estimate deal size ($XM-$YM)
3. PRODUCT MATCH — name 1-2 specific products Arkmurus can source
4. OEM RECOMMENDATION — name a real company with export track record to this region
5. FIRST CONTACT — specific 48-hour action (email/LinkedIn target, not "contact ministry")
6. TIMING — explain why NOW (deadline approaching, conflict driver, funding released)
If you cannot answer all 6, classify as WARM or COLD, not HOT.
${benchmarks}

CURRENT INTELLIGENCE:
Active tenders (all): ${JSON.stringify(topTenders)}
Qualified leads (HOT/WARM): ${JSON.stringify(hotWarmTenders)}
Strategic ideas: ${JSON.stringify(topIdeas)}
Active pipeline: ${JSON.stringify(pipelineActive)}
Market learning (historical): ${JSON.stringify(topMarkets)}
Recent outcomes: ${JSON.stringify(recentOutcomes)}
Global context: VIX ${currentData.fred?.find(f=>f.id==='VIXCLS')?.value||currentData.markets?.vix?.value||'N/A'}, Brent $${currentData.energy?.brent||'N/A'}${trendContext}${trendCtx}${prevContext}${weightContext}${explorerLeadsCtx}

OFFICIAL PROCUREMENT PORTALS (where to find live tenders):
${portalLines}

LEARNING RULES:
- Compare your previous assessment to current data — note what changed, what you got right/wrong
- If a market's adaptive weight is high (>1.5x), prioritise it — the system has proven wins there
- If a market's win rate is 0%, explain what needs to change to start winning
- Reference SPECIFIC data points from the intelligence above — don't generalise
${getPromptModifiers()}

TASK: Produce your autonomous BD assessment. Think step by step. Output ONLY valid JSON — no comments, no trailing commas.

JSON FORMAT (follow exactly):
{"weeklyPriority":{"action":"string","market":"string","whyNow":"string","firstStep":"string"},"salesLeads":[{"market":"string","lead":"string","type":"TENDER or STRATEGIC or PROACTIVE","estimatedValue":"string","procurementAuthority":"string","oemRecommendation":"string","urgency":"HOT or WARM or COLD","nextStep":"string"}],"actionPlan":[{"deal":"string","market":"string","steps":["string"],"deadline":"string","winProb":50}],"knowledgeGaps":[{"question":"string","howToFind":"string","urgency":"high or medium or low"}],"marketIntelligence":[{"market":"string","signal":"string","recommendation":"string"}],"competitiveThreats":[{"market":"string","threat":"string","counter":"string"}],"selfLearning":{"patternsObserved":["string"],"strategyAdjustment":"string","nextSweepFocus":"string"},"confidence":0.7}`;

  try {
    const raw = await llmProvider.complete(
      systemPrompt,
      'Analyze the current intelligence and produce your autonomous assessment as valid JSON.',
      { maxTokens: 4000, temperature: 0.12 }
    );
    const text = (typeof raw === 'string' ? raw : raw?.text || '');
    const parsed = parseLlmJson(text);
    // Stamp and store the brain's reasoning for learning
    const result = { ...parsed, generatedAt: new Date().toISOString() };
    const L = getlearning();
    L.lastThought = { ts: result.generatedAt, result };
    // Record brain assessment for future LLM training
    try { recordBrainAssessment({ tenderCount: tenders.length, ideaCount: ideas.length }, result); } catch {}
    // Save explorer directives so web_explorer can use brain's recommendations (Unit 2)
    L.explorerDirectives = {
      nextSweepFocus: result.selfLearning?.nextSweepFocus || '',
      knowledgeGaps: result.knowledgeGaps || [],
      updatedAt: new Date().toISOString(),
    };
    saveLearning(L);
    return result;
  } catch (err) {
    console.warn('[BD Brain] LLM reasoning failed (non-fatal):', err.message);
    return null;
  }
}

// ── Deal Pipeline Tracker ─────────────────────────────────────────────────────
export function getDealPipeline() {
  return loadJson(PIPELINE_FILE, []);
}

export function createDeal(market, opportunity, value = null) {
  ensureDir();
  const pipeline = loadJson(PIPELINE_FILE, []);
  const id = `${(market || 'XX').substring(0, 3).toUpperCase()}-${Date.now().toString(36)}`;
  const deal = {
    id,
    market: market || 'Unknown',
    title: (opportunity || '').substring(0, 200),
    sourceTitle: opportunity,
    stage: 'IDENTIFIED',
    type: 'MANUAL',
    score: 50,
    value: value || null,
    detectedAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    notes: [],
  };
  pipeline.unshift(deal);
  atomicWrite(PIPELINE_FILE, pipeline.slice(0, 200));
  logSelfUpdate('deal_created', { id, market, opportunity: (opportunity || '').substring(0, 60) });
  return { ok: true, id, deal };
}

export function updateDealStage(dealId, stage, notes = '') {
  ensureDir();
  const pipeline = loadJson(PIPELINE_FILE, []);
  const idx = pipeline.findIndex(d => d.id === dealId);
  const entry = idx >= 0 ? pipeline[idx] : null;
  if (!entry) return { ok: false, error: 'Deal not found' };
  entry.stage = stage;
  entry.updatedAt = new Date().toISOString();
  if (notes) entry.notes = (entry.notes || []).concat({ ts: new Date().toISOString(), note: notes });
  pipeline[idx] = entry;
  atomicWrite(PIPELINE_FILE, pipeline);
  logSelfUpdate('deal_stage_update', { dealId, stage });
  return { ok: true };
}

function _upsertPipelineDeal(tender) {
  ensureDir();
  const pipeline = loadJson(PIPELINE_FILE, []);
  const existing = pipeline.find(d => d.sourceTitle === tender.title && d.market === tender.market);
  if (existing) return; // already tracked
  pipeline.unshift({
    id:          `${tender.iso2 || 'XX'}-${Date.now()}`,
    market:      tender.market,
    iso2:        tender.iso2,
    type:        tender.type,
    stage:       'IDENTIFIED',
    title:       tender.title,
    sourceTitle: tender.title,
    url:         tender.url,
    source:      tender.source,
    score:       tender.score,
    detectedAt:  new Date().toISOString(),
    updatedAt:   new Date().toISOString(),
    notes:       [],
  });
  // Keep max 200 pipeline entries
  atomicWrite(PIPELINE_FILE, pipeline.slice(0, 200));
}

// ── Main entry point ──────────────────────────────────────────────────────────
export async function runBDIntelligence(currentData, targetMarkets, llmProvider = null) {
  const markets = targetMarkets || _DEFAULT_MARKETS;

  const procurementTenders = currentData.procurementTenders || currentData.procurement || {};

  // 1. Extract real tenders from procurement feed
  const tenders = extractRealTenders(procurementTenders, markets);

  // 1a. Score win probability for each tender & update market learning
  for (const t of tenders) {
    t.winProbability = scoreWinProbability(t, markets);
    updateMarketScore(t.market, t.score, !!t.url);
    recordWinProbTrend(t.market, t.winProbability);
  }

  // Auto-track new tenders in deal pipeline
  for (const t of tenders.filter(t => t.type === 'TENDER' && t.url)) {
    _upsertPipelineDeal(t);
  }

  // 2. Build strategic ideas from market intelligence
  const tenderMarkets = new Set(tenders.map(t => t.market));
  const ideas = buildStrategicIdeas(currentData, markets, tenderMarkets);

  // 3. Get current pipeline
  const pipeline = getDealPipeline();

  // 4. LLM strategy + autonomous brain (single combined call for efficiency)
  // Use getLearningStats() (not getlearning()) so trendSummary is included in brain context
  const learningData = getLearningStats();
  const [strategy, brain] = await Promise.allSettled([
    llmStrategicAnalysis(llmProvider, tenders, ideas, currentData),
    runAutonomousBrain(llmProvider, tenders, ideas, pipeline, currentData, learningData),
  ]);

  // Derive topMarkets summary for frontend display
  const topMarkets = Object.entries(learningData.marketScores || {})
    .sort((a, b) => (b[1].avgScore || 0) - (a[1].avgScore || 0))
    .slice(0, 5)
    .map(([m]) => m);

  const fullLearning = getLearningStats();

  const result = {
    tenders,
    ideas,
    strategy:   strategy.status  === 'fulfilled' ? strategy.value  : null,
    brain:      brain.status     === 'fulfilled' ? brain.value     : null,
    pipeline,
    learning:   { ...learningData, topMarkets, totalOutcomes: (learningData.outcomes || []).length, trendSummary: fullLearning.trendSummary },
    computedAt: new Date().toISOString(),
    counts: {
      activeTenders:  tenders.filter(t => t.type === 'TENDER').length,
      contractAwards: tenders.filter(t => t.type === 'CONTRACT').length,
      strategicIdeas: ideas.length,
      pipelineDeals:  pipeline.length,
    },
  };

  atomicWrite(STRATEGY_FILE, result);
  logSelfUpdate('bd_intelligence_run', { ...result.counts, brainActive: !!result.brain });

  return result;
}

export function getBDIntelligence() {
  return loadJson(STRATEGY_FILE, null);
}

// Returns qualified sales leads from the last BD run
// HOT = verified URL + strong tender signal + strategic market
// WARM = verified URL + any procurement match
// brainLeads = LLM-enriched leads from autonomous brain
export function getQualifiedLeads() {
  const bd = getBDIntelligence();
  if (!bd) return { hot: [], warm: [], brainLeads: [], computedAt: null };
  const hot  = (bd.tenders || []).filter(t => t.leadQuality === 'HOT');
  const warm = (bd.tenders || []).filter(t => t.leadQuality === 'WARM');
  return {
    hot,
    warm,
    brainLeads: bd.brain?.salesLeads || [],
    computedAt: bd.computedAt,
  };
}

// ── Telegram formatter ────────────────────────────────────────────────────────
export function formatBDSummaryForTelegram(bd) {
  if (!bd) return '📊 *BD INTELLIGENCE*\n\nNo data yet — run a sweep first.';

  let msg = `📊 *BD & STRATEGY INTELLIGENCE*\n`;
  msg += `_${(bd.computedAt || '').substring(0, 16).replace('T', ' ')} UTC_\n`;
  msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  // Brain priority — the most actionable single thing
  const brain = bd.brain;
  if (brain?.weeklyPriority?.action) {
    msg += `🧠 *BRAIN — TOP PRIORITY*\n`;
    msg += `${brain.weeklyPriority.action.substring(0, 200)}\n`;
    if (brain.weeklyPriority.whyNow) msg += `_Why now: ${brain.weeklyPriority.whyNow.substring(0, 120)}_\n`;
    if (brain.weeklyPriority.firstStep) msg += `→ *First step:* ${brain.weeklyPriority.firstStep.substring(0, 120)}\n`;
    msg += '\n';
  }

  // Qualified sales leads from brain
  const hotLeads = (brain?.salesLeads || []).filter(l => l.urgency === 'HOT');
  const warmLeads = (brain?.salesLeads || []).filter(l => l.urgency === 'WARM');
  if (hotLeads.length > 0 || warmLeads.length > 0) {
    msg += `🔥 *QUALIFIED SALES LEADS*\n`;
    for (const l of hotLeads.slice(0, 2)) {
      msg += `🔴 *HOT* [${l.market}] ${(l.lead || '').substring(0, 80)}\n`;
      if (l.estimatedValue) msg += `  💰 ${l.estimatedValue}`;
      if (l.procurementAuthority) msg += ` · ${l.procurementAuthority.substring(0, 60)}`;
      msg += `\n`;
      if (l.nextStep) msg += `  → ${l.nextStep.substring(0, 100)}\n`;
    }
    for (const l of warmLeads.slice(0, 2)) {
      msg += `🟡 *WARM* [${l.market}] ${(l.lead || '').substring(0, 80)}\n`;
      if (l.estimatedValue) msg += `  💰 ${l.estimatedValue}\n`;
    }
    msg += '\n';
  }

  // Active tenders with win probability (HOT first)
  if ((bd.counts?.activeTenders || 0) > 0) {
    msg += `🎯 *${bd.counts.activeTenders} ACTIVE TENDER(S)*\n`;
    for (const t of (bd.tenders || []).filter(t => t.type === 'TENDER').sort((a,b) => (a.leadQuality === 'HOT' ? -1 : b.leadQuality === 'HOT' ? 1 : 0)).slice(0, 4)) {
      const prob = t.winProbability != null ? ` [${t.winProbability}%]` : '';
      const flag = t.lusophone ? '🇵🇹 ' : '';
      const hot  = t.leadQuality === 'HOT' ? '🔥 ' : '';
      msg += `${hot}${flag}▸ *${t.market}*${prob} — ${t.title.substring(0, 90)}\n`;
      if (t.url) msg += `  ${t.url.substring(0, 80)}\n`;
    }
    msg += '\n';
  }

  // Contract awards
  if ((bd.counts?.contractAwards || 0) > 0) {
    msg += `✅ *${bd.counts.contractAwards} CONTRACT AWARD(S)*\n`;
    for (const t of (bd.tenders || []).filter(t => t.type === 'CONTRACT').slice(0, 2)) {
      msg += `▸ *${t.market}* — ${t.title.substring(0, 90)}\n`;
      if (t.url) msg += `  ${t.url.substring(0, 80)}\n`;
    }
    msg += '\n';
  }

  // Action plan top deals
  if (brain?.actionPlan?.length > 0) {
    msg += `📋 *ACTION PLAN (${brain.actionPlan.length} deals)*\n`;
    for (const ap of brain.actionPlan.slice(0, 2)) {
      const winStr = ap.winProb != null ? ` [${ap.winProb}% win]` : '';
      msg += `▸ *${ap.market}*${winStr} — ${(ap.deal || '').substring(0, 80)}\n`;
      if (ap.steps?.[0]) msg += `  Step 1: ${ap.steps[0].substring(0, 80)}\n`;
    }
    msg += '\n';
  }

  // Strategic ideas
  if (bd.ideas?.length > 0) {
    msg += `💡 *${bd.ideas.length} STRATEGIC IDEA(S)*\n`;
    for (const i of bd.ideas.slice(0, 3)) {
      const flag = i.lusophone ? '🇵🇹 ' : '';
      msg += `${flag}▸ *${i.market}* (${i.type}): ${(i.actionStep || i.rationale || '').substring(0, 100)}\n`;
    }
    msg += '\n';
  }

  // Market intelligence from brain
  if (brain?.marketIntelligence?.length > 0) {
    msg += `📡 *MARKET INTELLIGENCE*\n`;
    for (const mi of brain.marketIntelligence.slice(0, 2)) {
      msg += `▸ *${mi.market}:* ${(mi.signal || '').substring(0, 100)}\n`;
      if (mi.recommendation) msg += `  → ${mi.recommendation.substring(0, 80)}\n`;
    }
    msg += '\n';
  }

  // High-urgency knowledge gaps
  const urgentGaps = (brain?.knowledgeGaps || []).filter(g => (g.urgency || '').toUpperCase() === 'HIGH');
  if (urgentGaps.length > 0) {
    msg += `❓ *KNOWLEDGE GAPS (HIGH)*\n`;
    for (const g of urgentGaps.slice(0, 2)) {
      msg += `▸ ${g.question.substring(0, 120)}\n`;
    }
    msg += '\n';
  }

  msg += `_Pipeline: ${bd.counts?.pipelineDeals || 0} deals tracked · Dashboard → BD & Strategy_`;
  return msg;
}
