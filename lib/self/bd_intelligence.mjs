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

const PIPELINE_FILE = join(process.cwd(), 'runs', 'learning', 'bd_pipeline.json');
const STRATEGY_FILE = join(process.cwd(), 'runs', 'learning', 'bd_strategy.json');

function ensureDir() {
  const dir = join(process.cwd(), 'runs', 'learning');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}
function atomicWrite(path, data) {
  ensureDir();
  const tmp = path + '.tmp';
  writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
  renameSync(tmp, path);
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

function kwScore(text, kwList) {
  const lower = text.toLowerCase();
  return kwList.filter(k => lower.includes(k)).length;
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
    const tScore  = kwScore(text, TENDER_KW);
    const cScore  = kwScore(text, CONTRACT_KW);
    const bScore  = kwScore(text, BUDGET_KW);
    const total   = tScore + cScore + bScore;
    if (total === 0) continue;

    // Match to a target market
    const market  = targetMarkets.find(m => text.includes(m.name.toLowerCase()) || text.includes((m.iso2 || '').toLowerCase()));

    const type = tScore > 0 ? 'TENDER' : cScore > 0 ? 'CONTRACT' : 'BUDGET';

    tenders.push({
      type,
      market:     market?.name || 'Global',
      iso2:       market?.iso2 || null,
      lusophone:  market?.lusophone || false,
      title:      (item.title || '').substring(0, 150),
      summary:    (item.content || item.text || item.summary || '').substring(0, 400),
      url:        item.url || item.link || '',
      source:     item.source || procurementTenders.source || 'Procurement Feed',
      date:       item.timestamp ? new Date(item.timestamp).toISOString().substring(0, 10) : null,
      score:      total * 10 + (market?.lusophone ? 15 : 0) + (market?.priority === 'HIGH' ? 10 : 0),
      priority:   market?.priority || 'MEDIUM',
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
  const procurementTenders = currentData.procurementTenders
    || currentData.procurement
    || {};

  // 1. Extract real tenders from procurement feed
  const tenders = extractRealTenders(procurementTenders, targetMarkets);

  // Auto-track new tenders in deal pipeline
  for (const t of tenders.filter(t => t.type === 'TENDER' && t.url)) {
    _upsertPipelineDeal(t);
  }

  // 2. Build strategic ideas from market intelligence
  const tenderMarkets = new Set(tenders.map(t => t.market));
  const ideas = buildStrategicIdeas(currentData, targetMarkets, tenderMarkets);

  // 3. LLM strategic layer (if configured)
  const strategy = await llmStrategicAnalysis(llmProvider, tenders, ideas, currentData);

  const result = {
    tenders,
    ideas,
    strategy,
    pipeline: getDealPipeline(),
    computedAt: new Date().toISOString(),
    counts: {
      activeTenders: tenders.filter(t => t.type === 'TENDER').length,
      contractAwards: tenders.filter(t => t.type === 'CONTRACT').length,
      strategicIdeas: ideas.length,
      pipelineDeals: getDealPipeline().length,
    },
  };

  // Persist
  atomicWrite(STRATEGY_FILE, result);
  logSelfUpdate('bd_intelligence_run', result.counts);

  return result;
}

export function getBDIntelligence() {
  return loadJson(STRATEGY_FILE, null);
}

// ── Telegram formatter ────────────────────────────────────────────────────────
export function formatBDSummaryForTelegram(bd) {
  if (!bd) return '📊 *BD INTELLIGENCE*\n\nNo data yet — run a sweep first.';

  let msg = `📊 *BD INTELLIGENCE UPDATE*\n`;
  msg += `_${bd.computedAt?.substring(0, 16).replace('T', ' ')} London_\n`;
  msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  if (bd.counts.activeTenders > 0) {
    msg += `🎯 *${bd.counts.activeTenders} ACTIVE TENDER(S)*\n`;
    for (const t of bd.tenders.filter(t => t.type === 'TENDER').slice(0, 3)) {
      msg += `▸ *${t.market}* — ${t.title.substring(0, 80)}\n`;
      if (t.url) msg += `  ${t.url.substring(0, 80)}\n`;
    }
    msg += '\n';
  }

  if (bd.counts.contractAwards > 0) {
    msg += `✅ *${bd.counts.contractAwards} CONTRACT AWARD(S)*\n`;
    for (const t of bd.tenders.filter(t => t.type === 'CONTRACT').slice(0, 2)) {
      msg += `▸ *${t.market}* — ${t.title.substring(0, 80)}\n`;
    }
    msg += '\n';
  }

  if (bd.strategy?.topPriority) {
    const p = bd.strategy.topPriority;
    msg += `⚡ *THIS WEEK'S TOP PRIORITY*\n`;
    msg += `${JSON.stringify(p).replace(/[{}"]/g, '').substring(0, 300)}\n\n`;
  }

  if (bd.ideas?.length > 0) {
    msg += `💡 *${bd.ideas.length} STRATEGIC IDEA(S)*\n`;
    for (const i of bd.ideas.slice(0, 3)) {
      msg += `▸ *${i.market}* (${i.type}): ${i.actionStep?.substring(0, 100)}\n`;
    }
    msg += '\n';
  }

  msg += `_/opportunities for full dashboard · Pipeline: ${bd.counts.pipelineDeals} tracked_`;
  return msg;
}
