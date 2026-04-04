// lib/aria/knowledge.mjs
// ARIA Long-Term Knowledge Base — persistent memory that survives sessions
//
// Three knowledge types:
//   FACTS     — verified product/OEM/market data (corrected by user or confirmed by research)
//   QUERIES   — past questions and topics (builds institutional knowledge)
//   LEARNINGS — corrections, preferences, domain-specific insights from conversations
//
// Storage: Upstash Redis (primary) + local file (fallback)
// Capacity: ~500 knowledge entries, auto-pruned by age + relevance

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';
import { recordKnowledgeFact } from './training_data.mjs';

const KB_FILE = join(process.cwd(), 'runs', 'aria_knowledge.json');
const KB_REDIS_KEY = 'crucix:aria:knowledge';
const MAX_ENTRIES = 500;
const MAX_CONTEXT_ENTRIES = 15; // max entries injected into prompt

// ── In-memory cache ──────────────────────────────────────────────────────────
let _cache = null;

function defaultKB() {
  return {
    facts: [],      // { id, topic, content, source, confidence, createdAt, accessCount }
    queries: [],    // { id, query, summary, market, category, createdAt }
    learnings: [],  // { id, correction, context, createdAt }
    version: 1,
  };
}

// ── Load / Save ──────────────────────────────────────────────────────────────
function loadKB() {
  if (_cache) return _cache;
  try {
    if (existsSync(KB_FILE)) {
      _cache = JSON.parse(readFileSync(KB_FILE, 'utf8'));
      return _cache;
    }
  } catch (e) { console.warn('[ARIA KB] File corrupted, creating new KB:', e.message); }
  _cache = defaultKB();
  return _cache;
}

function saveKB(kb) {
  _cache = kb;
  try {
    const dir = dirname(KB_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(KB_FILE, JSON.stringify(kb, null, 2), 'utf8');
  } catch (e) { console.warn('[ARIA KB] File save failed:', e.message); }
  // Async Redis backup
  redisSet(KB_REDIS_KEY, kb).catch(() => {});
}

export async function initKnowledgeBase() {
  if (_cache) return;
  try {
    const remote = await redisGet(KB_REDIS_KEY);
    if (remote && remote.facts) {
      _cache = remote;
      console.log(`[ARIA KB] Loaded ${remote.facts.length} facts, ${remote.queries.length} queries, ${remote.learnings.length} learnings from Redis`);
      // Restore to file
      try { writeFileSync(KB_FILE, JSON.stringify(remote, null, 2), 'utf8'); } catch {}
      return;
    }
  } catch {}
  loadKB();
  console.log(`[ARIA KB] Initialized (${_cache.facts.length} facts)`);
}

// ── Knowledge Operations ─────────────────────────────────────────────────────

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

/**
 * Store a verified fact (product spec, OEM capability, market detail).
 */
export function storeFact(topic, content, source = 'aria', confidence = 'CONFIRMED') {
  const kb = loadKB();
  // Check for existing fact on same topic — update instead of duplicate
  const existing = kb.facts.findIndex(f => f.topic.toLowerCase() === topic.toLowerCase());
  if (existing >= 0) {
    kb.facts[existing].content = content;
    kb.facts[existing].source = source;
    kb.facts[existing].confidence = confidence;
    kb.facts[existing].updatedAt = new Date().toISOString();
    kb.facts[existing].accessCount = (kb.facts[existing].accessCount || 0) + 1;
  } else {
    kb.facts.unshift({
      id: genId(), topic, content, source, confidence,
      createdAt: new Date().toISOString(), accessCount: 0,
    });
  }
  // Prune oldest if over limit
  if (kb.facts.length > MAX_ENTRIES) kb.facts.splice(MAX_ENTRIES);
  saveKB(kb);
  // Record for future LLM training
  try { recordKnowledgeFact(topic, content, confidence); } catch {}
}

/**
 * Record a user query topic for institutional memory.
 */
export function recordQuery(query, summary = '', market = '', category = '') {
  const kb = loadKB();
  kb.queries.unshift({
    id: genId(), query: query.substring(0, 200), summary: summary.substring(0, 300),
    market, category, createdAt: new Date().toISOString(),
  });
  if (kb.queries.length > MAX_ENTRIES) kb.queries.splice(MAX_ENTRIES);
  saveKB(kb);
}

/**
 * Store a learning/correction from the user.
 */
export function storeLearning(correction, context = '') {
  const kb = loadKB();
  kb.learnings.unshift({
    id: genId(), correction: correction.substring(0, 500),
    context: context.substring(0, 200), createdAt: new Date().toISOString(),
  });
  if (kb.learnings.length > 200) kb.learnings.splice(200);
  saveKB(kb);
}

/**
 * Search knowledge base for relevant entries given a query.
 * Returns a formatted string for injection into the ARIA prompt.
 */
export function searchKnowledge(query) {
  const kb = loadKB();
  const q = (query || '').toLowerCase();
  const words = q.split(/\s+/).filter(w => w.length > 3);
  if (!words.length) return '';

  // Score each fact by relevance
  const scored = kb.facts.map(f => {
    const text = (f.topic + ' ' + f.content).toLowerCase();
    let score = 0;
    for (const w of words) {
      if (text.includes(w)) score += 3;
    }
    // Boost recently accessed
    score += Math.min(3, f.accessCount || 0);
    return { ...f, score };
  }).filter(f => f.score > 0).sort((a, b) => b.score - a.score);

  // Score learnings
  const relevantLearnings = kb.learnings.filter(l => {
    const text = (l.correction + ' ' + l.context).toLowerCase();
    return words.some(w => text.includes(w));
  }).slice(0, 3);

  // Recent queries on similar topics
  const similarQueries = kb.queries.filter(q2 => {
    const text = (q2.query + ' ' + q2.summary).toLowerCase();
    return words.some(w => text.includes(w));
  }).slice(0, 3);

  const parts = [];

  if (scored.length > 0) {
    parts.push('KNOWLEDGE BASE (verified facts from past research):\n' +
      scored.slice(0, MAX_CONTEXT_ENTRIES).map(f =>
        `- [${f.confidence}] ${f.topic}: ${f.content}`
      ).join('\n'));
  }

  if (relevantLearnings.length > 0) {
    parts.push('CORRECTIONS & LEARNINGS:\n' +
      relevantLearnings.map(l => `- ${l.correction}`).join('\n'));
  }

  if (similarQueries.length > 0) {
    parts.push('PREVIOUS QUERIES ON THIS TOPIC:\n' +
      similarQueries.map(q2 => `- "${q2.query}" → ${q2.summary || 'no summary'}`).join('\n'));
  }

  return parts.length > 0 ? '\n\n[ARIA KNOWLEDGE BASE]\n' + parts.join('\n\n') : '';
}

/**
 * Extract and auto-store facts from an ARIA response.
 * Looks for [CONFIRMED] and [PROBABLE] tagged statements.
 */
export function autoExtractFacts(userQuery, ariaResponse) {
  if (!ariaResponse || ariaResponse.length < 50) return;
  try {
    // Extract topic from query
    const topic = userQuery.substring(0, 80).replace(/[?!.]/g, '').trim();

    // Find confirmed/probable statements
    const lines = ariaResponse.split('\n');
    for (const line of lines) {
      const match = line.match(/\[(CONFIRMED|PROBABLE)\]\s*(.+)/i);
      if (match) {
        const confidence = match[1].toUpperCase();
        const content = match[2].trim().substring(0, 300);
        if (content.length > 20) {
          storeFact(topic + ' — ' + content.substring(0, 40), content, 'aria-auto', confidence);
        }
      }
    }

    // Record the query itself
    const summary = ariaResponse.substring(0, 200).replace(/\n/g, ' ').trim();
    const market = extractMarket(userQuery);
    recordQuery(userQuery, summary, market);
  } catch {}
}

// Simple market extraction from query text
function extractMarket(text) {
  const markets = ['Angola', 'Mozambique', 'Nigeria', 'Kenya', 'Brazil', 'Indonesia',
    'Philippines', 'Vietnam', 'UAE', 'Saudi Arabia', 'Ghana', 'Senegal', 'Ethiopia'];
  const lower = (text || '').toLowerCase();
  for (const m of markets) {
    if (lower.includes(m.toLowerCase())) return m;
  }
  return '';
}

/**
 * Get knowledge base stats for the panel.
 */
export function getKBStats() {
  const kb = loadKB();
  return {
    totalFacts: kb.facts.length,
    totalQueries: kb.queries.length,
    totalLearnings: kb.learnings.length,
    topTopics: kb.facts.slice(0, 5).map(f => f.topic),
    recentQueries: kb.queries.slice(0, 5).map(q => q.query),
  };
}
