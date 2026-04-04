// lib/aria/prompt_optimizer.mjs
// Outcome-Driven Prompt Optimization (DSPy pattern in Node.js)
//
// Every deal outcome becomes training signal. The system tracks which
// brain recommendations led to wins vs losses, then adjusts emphasis
// in future prompts. Not rewriting prompts — adjusting EMPHASIS.
//
// Example: If brain's Angola leads have 60% win rate but Nigeria leads
// have 10%, the optimizer adds "Prioritise Lusophone markets where
// relationship advantage is proven" to the brain's context.

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const OPT_FILE = join(process.cwd(), 'runs', 'prompt_optimizer.json');
const OPT_REDIS_KEY = 'crucix:prompt_optimizer';

let _cache = null;

function defaultState() {
  return {
    observations: [],  // { market, product, outcome, brainRecommended, winProb, ts }
    adjustments: [],   // { rule, reason, confidence, createdAt }
    promptModifiers: '', // accumulated text to inject into brain prompt
    version: 1,
  };
}

function loadState() {
  if (_cache) return _cache;
  try {
    if (existsSync(OPT_FILE)) {
      _cache = JSON.parse(readFileSync(OPT_FILE, 'utf8'));
      return _cache;
    }
  } catch {}
  _cache = defaultState();
  return _cache;
}

function saveState(state) {
  _cache = state;
  try {
    const dir = dirname(OPT_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(OPT_FILE, JSON.stringify(state, null, 2), 'utf8');
  } catch {}
  redisSet(OPT_REDIS_KEY, state).catch(() => {});
}

export async function initOptimizer() {
  try {
    const remote = await redisGet(OPT_REDIS_KEY);
    if (remote && remote.observations) { _cache = remote; return; }
  } catch {}
  loadState();
  console.log(`[PromptOptimizer] ${_cache.observations.length} observations, ${_cache.adjustments.length} rules`);
}

/**
 * Record a deal outcome for learning.
 */
export function recordOutcomeForOptimization(market, product, outcome, brainRecommended, winProb) {
  const state = loadState();
  state.observations.unshift({
    market, product, outcome, brainRecommended, winProb,
    ts: new Date().toISOString(),
  });
  if (state.observations.length > 200) state.observations.splice(200);

  // Recompute adjustments every 5 observations
  if (state.observations.length % 5 === 0) recomputeAdjustments(state);
  saveState(state);
}

/**
 * Analyze observations and generate prompt adjustment rules.
 */
function recomputeAdjustments(state) {
  const obs = state.observations;
  if (obs.length < 5) return;

  const adjustments = [];

  // Market-level analysis
  const byMarket = {};
  for (const o of obs) {
    if (!byMarket[o.market]) byMarket[o.market] = { wins: 0, losses: 0, total: 0 };
    byMarket[o.market].total++;
    if (o.outcome === 'WON') byMarket[o.market].wins++;
    else if (o.outcome === 'LOST') byMarket[o.market].losses++;
  }

  // Find strong and weak markets
  for (const [market, stats] of Object.entries(byMarket)) {
    if (stats.total < 2) continue;
    const winRate = stats.wins / stats.total;

    if (winRate >= 0.6) {
      adjustments.push({
        rule: `INCREASE priority for ${market} — proven ${Math.round(winRate * 100)}% win rate (${stats.wins}/${stats.total} deals).`,
        reason: 'Historical outcome data shows strong conversion',
        confidence: Math.min(0.9, 0.5 + stats.total * 0.05),
        createdAt: new Date().toISOString(),
      });
    } else if (winRate <= 0.2 && stats.total >= 3) {
      adjustments.push({
        rule: `CAUTION on ${market} — only ${Math.round(winRate * 100)}% win rate (${stats.wins}/${stats.total}). Investigate: wrong OEM? Wrong approach? Competitor advantage?`,
        reason: 'Repeated losses suggest structural disadvantage',
        confidence: Math.min(0.9, 0.5 + stats.total * 0.05),
        createdAt: new Date().toISOString(),
      });
    }
  }

  // Brain recommendation accuracy
  const brainRecommended = obs.filter(o => o.brainRecommended);
  if (brainRecommended.length >= 3) {
    const brainWins = brainRecommended.filter(o => o.outcome === 'WON').length;
    const brainRate = brainWins / brainRecommended.length;
    if (brainRate < 0.3) {
      adjustments.push({
        rule: 'Brain recommendations have low conversion — be more conservative with HOT ratings. Only rate HOT if relationship tier is INCUMBENT or ESTABLISHED.',
        reason: `Brain-recommended deals: ${Math.round(brainRate * 100)}% win rate`,
        confidence: 0.7,
        createdAt: new Date().toISOString(),
      });
    } else if (brainRate > 0.5) {
      adjustments.push({
        rule: 'Brain recommendations converting well — maintain current qualification standards.',
        reason: `Brain-recommended deals: ${Math.round(brainRate * 100)}% win rate`,
        confidence: 0.8,
        createdAt: new Date().toISOString(),
      });
    }
  }

  // Win probability calibration
  const withProb = obs.filter(o => o.winProb > 0);
  if (withProb.length >= 5) {
    const highProb = withProb.filter(o => o.winProb >= 50);
    const highProbWins = highProb.filter(o => o.outcome === 'WON').length;
    if (highProb.length >= 3 && highProbWins / highProb.length < 0.4) {
      adjustments.push({
        rule: 'Win probability scores are OVER-OPTIMISTIC — deals scored >50% only win ' + Math.round(highProbWins / highProb.length * 100) + '% of the time. Reduce base score by 10 points.',
        reason: 'Calibration mismatch between predicted and actual win rates',
        confidence: 0.75,
        createdAt: new Date().toISOString(),
      });
    }
  }

  state.adjustments = adjustments;

  // Build prompt modifier text
  if (adjustments.length > 0) {
    state.promptModifiers = '\nOUTCOME-LEARNED RULES (from ' + obs.length + ' deal outcomes):\n' +
      adjustments.map(a => '- ' + a.rule).join('\n');
  } else {
    state.promptModifiers = '';
  }
}

/**
 * Get prompt modifier text to inject into brain/ARIA context.
 */
export function getPromptModifiers() {
  const state = loadState();
  return state.promptModifiers || '';
}

export function getOptimizerStats() {
  const state = loadState();
  return {
    totalObservations: state.observations.length,
    adjustmentCount: state.adjustments.length,
    adjustments: state.adjustments,
    recentOutcomes: state.observations.slice(0, 5),
  };
}
