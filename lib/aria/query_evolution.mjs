// lib/aria/query_evolution.mjs
// Genetic Algorithm for Explorer Query Evolution
// Inspired by Infoblox Blue Helix — queries that produce leads survive,
// queries that produce nothing die. After 4 weeks, the query population
// naturally selects toward patterns that generate real procurement intelligence.
//
// Fitness = did this query produce insights/salesIdeas/tenders?
// Selection = top 60% survive, bottom 40% replaced by mutations of survivors
// Mutation = swap market names, add/remove keywords, combine successful patterns

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const EVOLUTION_FILE = join(process.cwd(), 'runs', 'query_evolution.json');
const EVOLUTION_REDIS_KEY = 'crucix:query_evolution';
const GENERATION_SIZE = 20; // evolved queries per generation
const SURVIVAL_RATE = 0.6;  // top 60% survive
const MUTATION_RATE = 0.3;  // 30% chance of mutation per offspring

let _cache = null;

function defaultState() {
  return {
    generation: 0,
    queries: [],     // { query, fitness, hits, misses, lastRun, born }
    graveyard: [],   // queries that died (for analysis)
    version: 1,
  };
}

function loadState() {
  if (_cache) return _cache;
  try {
    if (existsSync(EVOLUTION_FILE)) {
      _cache = JSON.parse(readFileSync(EVOLUTION_FILE, 'utf8'));
      return _cache;
    }
  } catch {}
  _cache = defaultState();
  return _cache;
}

function saveState(state) {
  _cache = state;
  try {
    const dir = dirname(EVOLUTION_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(EVOLUTION_FILE, JSON.stringify(state, null, 2), 'utf8');
  } catch {}
  redisSet(EVOLUTION_REDIS_KEY, state).catch(() => {});
}

export async function initEvolution() {
  try {
    const remote = await redisGet(EVOLUTION_REDIS_KEY);
    if (remote && remote.queries) { _cache = remote; return; }
  } catch {}
  loadState();
  // Seed initial population if empty
  if (!_cache.queries.length) seedInitialPopulation();
  console.log(`[QueryEvolution] Gen ${_cache.generation} — ${_cache.queries.length} evolved queries`);
}

// ── Keyword pools for mutation ───────────────────────────────────────────────
const MARKETS = [
  'Angola', 'Mozambique', 'Nigeria', 'Kenya', 'Ghana', 'Senegal', 'Ethiopia',
  'Indonesia', 'Philippines', 'Vietnam', 'Brazil', 'Colombia', 'UAE', 'Saudi Arabia',
  'Poland', 'Romania', 'Cameroon', 'Tanzania', 'Rwanda', 'Uganda',
];

const PRODUCTS = [
  'armoured vehicle', 'UAV drone', 'patrol vessel', 'ammunition', 'helicopter',
  'radar air defense', 'border security', 'training programme', 'small arms',
  'counter-terrorism equipment', 'surveillance ISR', 'naval frigate',
];

const ACTIONS = [
  'tender RFP', 'contract awarded', 'procurement announcement', 'defence budget',
  'military modernisation', 'arms deal signed', 'FMS notification', 'offset agreement',
  'equipment delivery', 'MoU signed', 'defence cooperation',
];

// ── Seed initial population ──────────────────────────────────────────────────
function seedInitialPopulation() {
  const state = loadState();
  const now = new Date().toISOString();
  // Generate diverse initial queries
  for (let i = 0; i < GENERATION_SIZE; i++) {
    const market = MARKETS[Math.floor(Math.random() * MARKETS.length)];
    const product = PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
    const action = ACTIONS[Math.floor(Math.random() * ACTIONS.length)];
    state.queries.push({
      query: `${market} ${product} ${action} 2026`,
      fitness: 0,
      hits: 0,
      misses: 0,
      lastRun: null,
      born: now,
    });
  }
  state.generation = 1;
  saveState(state);
}

// ── Record outcome of a query ────────────────────────────────────────────────
/**
 * After explorer runs a query, record whether it produced results.
 * @param {string} query - The query that was run
 * @param {boolean} hit - Did it produce insights or sales ideas?
 * @param {number} resultCount - How many useful results
 */
export function recordQueryOutcome(query, hit, resultCount = 0) {
  const state = loadState();
  const q = state.queries.find(e => e.query === query);
  if (!q) return; // not an evolved query, skip

  q.lastRun = new Date().toISOString();
  if (hit) {
    q.hits++;
    q.fitness += resultCount * 2 + 1; // reward based on result count
  } else {
    q.misses++;
    q.fitness -= 1; // small penalty for miss
  }
  saveState(state);
}

// ── Evolve to next generation ────────────────────────────────────────────────
/**
 * Run selection + mutation. Call weekly or after enough data accumulates.
 * Returns the new generation of queries.
 */
export function evolveGeneration() {
  const state = loadState();
  if (state.queries.length < 5) return state.queries;

  // Sort by fitness (highest first)
  state.queries.sort((a, b) => b.fitness - a.fitness);

  // Selection: top SURVIVAL_RATE survive
  const surviveCount = Math.max(3, Math.ceil(state.queries.length * SURVIVAL_RATE));
  const survivors = state.queries.slice(0, surviveCount);
  const dead = state.queries.slice(surviveCount);

  // Archive dead queries
  for (const d of dead) {
    state.graveyard.unshift({ ...d, diedAt: new Date().toISOString() });
  }
  if (state.graveyard.length > 50) state.graveyard.splice(50);

  // Generate offspring from survivors via mutation
  const offspring = [];
  while (survivors.length + offspring.length < GENERATION_SIZE) {
    const parent = survivors[Math.floor(Math.random() * survivors.length)];
    const child = mutateQuery(parent.query);
    offspring.push({
      query: child,
      fitness: 0,
      hits: 0,
      misses: 0,
      lastRun: null,
      born: new Date().toISOString(),
      parentQuery: parent.query.substring(0, 60),
    });
  }

  state.queries = [...survivors, ...offspring];
  state.generation++;
  saveState(state);

  console.log(`[QueryEvolution] Evolved to Gen ${state.generation} — ${survivors.length} survived, ${offspring.length} new, ${dead.length} died`);
  return state.queries;
}

// ── Mutation operators ───────────────────────────────────────────────────────
function mutateQuery(parentQuery) {
  const parts = parentQuery.split(' ');
  const r = Math.random();

  if (r < 0.25) {
    // Swap market
    const newMarket = MARKETS[Math.floor(Math.random() * MARKETS.length)];
    // Replace first recognized market in query
    for (let i = 0; i < parts.length; i++) {
      if (MARKETS.some(m => m.toLowerCase() === parts[i].toLowerCase())) {
        parts[i] = newMarket;
        break;
      }
    }
  } else if (r < 0.5) {
    // Swap product
    const newProduct = PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
    // Append product term
    parts.push(...newProduct.split(' '));
  } else if (r < 0.75) {
    // Swap action
    const newAction = ACTIONS[Math.floor(Math.random() * ACTIONS.length)];
    parts.push(...newAction.split(' '));
  } else {
    // Crossover: combine two random elements
    const market = MARKETS[Math.floor(Math.random() * MARKETS.length)];
    const action = ACTIONS[Math.floor(Math.random() * ACTIONS.length)];
    return `${market} ${action} 2026`;
  }

  // Trim to reasonable length
  return parts.slice(0, 8).join(' ');
}

// ── Get evolved queries for explorer ─────────────────────────────────────────
/**
 * Returns the current generation of evolved queries.
 * Explorer should append these to its static query list.
 */
export function getEvolvedQueries() {
  const state = loadState();
  return state.queries.map(q => q.query);
}

export function getEvolutionStats() {
  const state = loadState();
  return {
    generation: state.generation,
    populationSize: state.queries.length,
    topQueries: state.queries.sort((a, b) => b.fitness - a.fitness).slice(0, 5).map(q => ({
      query: q.query, fitness: q.fitness, hits: q.hits, misses: q.misses,
    })),
    graveyardSize: state.graveyard.length,
    totalHits: state.queries.reduce((s, q) => s + q.hits, 0),
    totalMisses: state.queries.reduce((s, q) => s + q.misses, 0),
  };
}
