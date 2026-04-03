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
import { logSelfUpdate } from './learning_store.mjs';
import { redisGet, redisSet } from '../persist/store.mjs';
import { TARGET_MARKETS as _DEFAULT_MARKETS } from './opportunity_engine.mjs';

const PIPELINE_FILE  = join(process.cwd(), 'runs', 'learning', 'bd_pipeline.json');
const STRATEGY_FILE  = join(process.cwd(), 'runs', 'learning', 'bd_strategy.json');
const LEARNING_FILE  = join(process.cwd(), 'runs', 'learning', 'bd_learning.json');

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

// ── Procurement keyword sets ──────────────────────────────────────────────────
const TENDER_KW   = ['tender', 'rfp', 'rfq', 'request for proposal', 'request for quotation',
                     'itb', 'invitation to bid', 'solicitation', 'notice of intent'];
const CONTRACT_KW = ['contract award', 'contract awarded', 'signed contract', 'awarded to',
                     'selected supplier', 'won contract', 'purchase agreement'];
const BUDGET_KW   = ['defence budget', 'defense budget', 'military spending', 'allocated',
                     'appropriation', 'procurement fund', 'capital expenditure'];
const STRATEGIC_KW = ['modernisation', 'modernization', 'upgrade programme', 'capability development',
                      'bilateral defence', 'defense cooperation', 'military aid', 'equipment donation'];

// Defense relevance — at least one of these must match to accept a contract/tender
const DEFENSE_RELEVANCE_KW = [
  'defence', 'defense', 'military', 'army', 'navy', 'air force', 'armed forces',
  'weapon', 'arms', 'ammunition', 'missile', 'aircraft', 'vehicle', 'armour', 'armor',
  'security', 'surveillance', 'intelligence', 'police', 'border', 'coast guard',
  'fms', 'dsca', 'nato', 'peacekeep', 'counterterror', 'counter-terror',
  'sandf', 'fadm', 'faf', 'fds', 'faa ', ' fap ', 'paigc',
];

// Non-defense sources — exclude these from BD contract/tender classification
const NON_DEFENSE_SOURCES = ['world bank', 'worldbank', 'afdb', 'adb ', 'iadb', 'imf ',
  'unicef', 'unhcr', 'wfp', 'who ', 'undp ', 'usaid water', 'sanitation', 'health program'];

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
  return DEFENSE_RELEVANCE_KW.some(k => text.includes(k));
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

    // Skip non-defense sources (development banks, humanitarian orgs)
    const srcLower = (item.source || '').toLowerCase();
    if (NON_DEFENSE_SOURCES.some(s => srcLower.includes(s) || text.includes(s))) continue;

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

  // Signals + news indexed by market
  const marketSignals = {};
  for (const item of [
    ...(currentData.news || []).map(n => ({ title: n.title || '', text: n.summary || '', url: n.url || '' })),
    ...(currentData.newsFeed || []).map(n => ({ title: n.title || '', text: n.description || '', url: n.url || '' })),
    ...(currentData.signals || []).map(s => ({ title: (s.text || '').substring(0, 80), text: s.text || '', url: s.url || '' })),
  ]) {
    const combined = (item.title + ' ' + item.text).toLowerCase();
    for (const market of targetMarkets) {
      if (!combined.includes(market.name.toLowerCase())) continue;
      const key = market.name;
      if (!marketSignals[key]) marketSignals[key] = { market, items: [], stratScore: 0, budgetScore: 0 };
      marketSignals[key].items.push({ title: item.title, url: item.url, text: item.text });
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
      marketSignals[key].items.push({ title: ins.title, url: ins.sourceUrl || '', text: ins.summary || '' });
      marketSignals[key].stratScore += ins.relevance === 'HIGH' ? 3 : ins.relevance === 'MEDIUM' ? 2 : 1;
    }
  }

  // AfDB/dev finance signals
  const afdbItems = (currentData.afdb?.projects || currentData.afdb?.updates || []);
  for (const proj of afdbItems) {
    const text = ((proj.country || '') + ' ' + (proj.title || '') + ' ' + (proj.sector || '')).toLowerCase();
    if (!text.match(/security|defence|military|police|border/)) continue;
    for (const market of targetMarkets) {
      if (!text.includes(market.name.toLowerCase())) continue;
      const key = market.name;
      if (!marketSignals[key]) marketSignals[key] = { market, items: [], stratScore: 0, budgetScore: 0 };
      marketSignals[key].budgetScore += 3;
      marketSignals[key].items.push({ title: proj.title || 'AfDB security-sector project', url: proj.url || '', text: proj.sector || '' });
    }
  }

  // Build strategic ideas from markets that have signals but no real tender yet
  for (const [marketName, intel] of Object.entries(marketSignals)) {
    if (existingTenderMarkets.has(marketName)) continue; // Already in active deals
    const totalScore = intel.stratScore * 4 + intel.budgetScore * 3 + Math.min(10, intel.items.length * 2);
    if (totalScore < 5) continue;

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
    let text = (result.text || '').trim();
    if (text.startsWith('```')) text = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');
    return JSON.parse(text);
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

const ARKMURUS_STRENGTHS = {
  lusophone:     25, // deep language + cultural advantage
  angola:        20, portugal:   15, mozambique:  18, brazil:     12,
  'cape verde':  12, 'guinea-bissau': 14, 'sao tome': 12,
  sadc:          10, 'south africa': 8, kenya: 6, nigeria: 6,
};

const ARKMURUS_PRODUCTS = [
  'vehicle', 'armoured', 'protected mobility', 'apc', 'ifv',
  'uav', 'drone', 'surveillance', 'border security', 'c2',
  'ammunition', 'small arms', 'personal protection', 'ppe',
  'training', 'logistics', 'sustainment', 'spare parts',
];

function scoreWinProbability(tender, markets) {
  const L = getlearning();
  const text = (tender.title + ' ' + (tender.summary || '')).toLowerCase();
  const market = tender.market?.toLowerCase() || '';
  let score = 30; // base probability

  // Arkmurus geographic advantage
  for (const [key, bonus] of Object.entries(ARKMURUS_STRENGTHS)) {
    if (market.includes(key) || text.includes(key)) { score += bonus; break; }
  }

  // Product fit
  const productMatch = ARKMURUS_PRODUCTS.filter(p => text.includes(p)).length;
  score += Math.min(20, productMatch * 6);

  // URL available = verifiable = more real
  if (tender.url) score += 5;

  // Historical win rate for this market
  const mScore = L.marketScores[tender.market];
  if (mScore?.winRate > 0) score += Math.min(10, mScore.winRate / 10);

  // Recency bonus — recent signals are hotter
  if (tender.date) {
    const ageDays = (Date.now() - new Date(tender.date).getTime()) / 86400000;
    if (ageDays < 7)  score += 10;
    else if (ageDays < 30) score += 5;
    else if (ageDays > 90) score -= 10;
  }

  // High-priority market multiplier
  const marketDef = markets.find(m => m.name === tender.market);
  if (marketDef?.priority === 'HIGH') score += 8;

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
- Products: armoured vehicles, UAVs, border security systems, ammunition, training, logistics
- Approach: act as broker/intermediary between OEMs and end-user armed forces/MoDs
- Strictly excluded markets: Russia, Belarus, Iran, North Korea, Syria, Myanmar, Sudan
- Export control compliant — all UK/EU/US regulations apply

YOUR JOB: Think like a seasoned BD director with 20 years in defence. Be commercially ruthless but legally compliant.
Produce REAL, SPECIFIC, ACTIONABLE sales intelligence — not generic advice. Name actual MoDs, directorates,
OEM companies, estimated contract values in USD. Qualify leads as HOT/WARM/COLD based on commercial evidence.

CURRENT INTELLIGENCE:
Active tenders (all): ${JSON.stringify(topTenders)}
Qualified leads (HOT/WARM): ${JSON.stringify(hotWarmTenders)}
Strategic ideas: ${JSON.stringify(topIdeas)}
Active pipeline: ${JSON.stringify(pipelineActive)}
Market learning (historical): ${JSON.stringify(topMarkets)}
Recent outcomes: ${JSON.stringify(recentOutcomes)}
Global context: VIX ${currentData.fred?.find(f=>f.id==='VIXCLS')?.value||'N/A'}, Brent $${currentData.energy?.brent||'N/A'}${trendContext}

OFFICIAL PROCUREMENT PORTALS (where to find live tenders):
${portalLines}

TASK: Produce a complete autonomous assessment including qualified sales leads. Think step by step.

Respond ONLY with valid JSON:
{
  "weeklyPriority": {
    "action": "specific action to take TODAY — who to call/email/visit",
    "market": "market name",
    "deal": "which tender or opportunity",
    "whyNow": "time-sensitive reason",
    "firstStep": "very specific first step — name of ministry/contact/OEM if known"
  },
  "salesLeads": [
    {
      "market": "market name",
      "lead": "specific opportunity description (not generic — name the programme/requirement)",
      "type": "TENDER|STRATEGIC|PROACTIVE",
      "estimatedValue": "$XM-$YM (use comparable contract benchmarks)",
      "procurementAuthority": "specific govt body — directorate/dept name if known",
      "oemRecommendation": "specific OEM name and why they fit (export track record, product match)",
      "urgency": "HOT|WARM|COLD",
      "portalUrl": "official portal URL for this market",
      "nextStep": "one specific action in next 48h — include language/contact method"
    }
  ],
  "actionPlan": [
    { "deal": "title", "market": "...", "steps": ["step 1","step 2","step 3"], "deadline": "when", "winProb": 0-100 }
  ],
  "knowledgeGaps": [
    { "question": "What don't we know that would change our strategy?", "howToFind": "how to get this info", "urgency": "high|medium|low" }
  ],
  "marketIntelligence": [
    { "market": "...", "signal": "what the data is telling us", "recommendation": "what to do about it" }
  ],
  "competitiveThreats": [
    { "market": "...", "threat": "who might be competing or what risk", "counter": "how to counter" }
  ],
  "selfLearning": {
    "patternsObserved": ["pattern 1", "pattern 2"],
    "strategyAdjustment": "how should we change our approach based on recent data",
    "nextSweepFocus": "what to look for in the next intelligence sweep"
  },
  "confidence": 0.0-1.0
}`;

  try {
    const raw = await llmProvider.complete(
      systemPrompt,
      'Analyze the current intelligence and produce your autonomous assessment as valid JSON.',
      { maxTokens: 2500, temperature: 0.15 }
    );
    let text = (typeof raw === 'string' ? raw : raw?.text || '').trim();
    if (text.startsWith('```')) text = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      const m = text.match(/\{[\s\S]*\}/);
      if (!m) throw new Error('No JSON object found in LLM response');
      parsed = JSON.parse(m[0]);
    }
    // Stamp and store the brain's reasoning for learning
    const result = { ...parsed, generatedAt: new Date().toISOString() };
    const L = getlearning();
    L.lastThought = { ts: result.generatedAt, result };
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
