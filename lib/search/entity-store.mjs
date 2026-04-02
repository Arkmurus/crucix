// lib/search/entity-store.mjs
// Persistent entity graph — learns from every search, cross-references across sessions
// Stores: companies, people, relationships, search history, source hit rates

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dir  = dirname(fileURLToPath(import.meta.url));
const STORE_PATH = join(__dir, '../../runs/entity-graph.json');

const ENTITY_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days before re-querying

function now() { return Date.now(); }

function emptyStore() {
  return {
    version: 2,
    entities:      {},   // id → EntityRecord
    relationships: [],   // { from, to, type, confidence, source, ts }
    searches:      [],   // { query, ts, entityIds, durationMs }
    sourceStats:   {},   // sourceName → { hits, misses, avgMs }
    lastSaved:     now(),
  };
}

let _store = null;

function load() {
  if (_store) return _store;
  try {
    if (existsSync(STORE_PATH)) {
      _store = JSON.parse(readFileSync(STORE_PATH, 'utf8'));
      if (_store.version !== 2) _store = emptyStore();
    } else {
      _store = emptyStore();
    }
  } catch {
    _store = emptyStore();
  }
  return _store;
}

function save() {
  try {
    mkdirSync(dirname(STORE_PATH), { recursive: true });
    _store.lastSaved = now();
    writeFileSync(STORE_PATH, JSON.stringify(_store, null, 2));
  } catch (e) {
    console.warn('[EntityStore] Save failed:', e.message);
  }
}

// ── Entity management ─────────────────────────────────────────────────────────

export function entityId(type, identifier) {
  return `${type}:${identifier.toLowerCase().replace(/\s+/g,'-').replace(/[^a-z0-9:-]/g,'')}`;
}

export function upsertEntity(id, data) {
  const store = load();
  const existing = store.entities[id] || { id, createdAt: now(), searchCount: 0 };
  store.entities[id] = {
    ...existing,
    ...data,
    id,
    updatedAt: now(),
    searchCount: (existing.searchCount || 0) + 1,
  };
  return store.entities[id];
}

export function getEntity(id) {
  return load().entities[id] || null;
}

export function isStale(id) {
  const e = getEntity(id);
  if (!e || !e.updatedAt) return true;
  return (now() - e.updatedAt) > ENTITY_TTL_MS;
}

export function addRelationship(from, to, type, confidence = 0.9, source = '') {
  const store = load();
  const exists = store.relationships.find(r =>
    r.from === from && r.to === to && r.type === type
  );
  if (!exists) {
    store.relationships.push({ from, to, type, confidence, source, ts: now() });
  }
}

// ── Network traversal ─────────────────────────────────────────────────────────

export function getRelated(entityId, maxHops = 2) {
  const store  = load();
  const visited = new Set([entityId]);
  const queue  = [{ id: entityId, hop: 0 }];
  const results = [];

  while (queue.length > 0) {
    const { id, hop } = queue.shift();
    if (hop >= maxHops) continue;

    const links = store.relationships.filter(r => r.from === id || r.to === id);
    for (const link of links) {
      const other = link.from === id ? link.to : link.from;
      if (!visited.has(other)) {
        visited.add(other);
        const entity = store.entities[other];
        if (entity) results.push({ entity, relationship: link, hop: hop + 1 });
        queue.push({ id: other, hop: hop + 1 });
      }
    }
  }
  return results;
}

// ── Source performance tracking ───────────────────────────────────────────────

export function recordSourceStat(source, hit, ms) {
  const store = load();
  if (!store.sourceStats[source]) store.sourceStats[source] = { hits: 0, misses: 0, totalMs: 0, calls: 0 };
  const s = store.sourceStats[source];
  if (hit) s.hits++; else s.misses++;
  s.totalMs += ms;
  s.calls++;
  s.avgMs = Math.round(s.totalMs / s.calls);
  s.hitRate = s.hits / (s.hits + s.misses);
}

export function getSourceStats() {
  return load().sourceStats;
}

// ── Search history ────────────────────────────────────────────────────────────

export function recordSearch(query, entityIds, durationMs) {
  const store = load();
  store.searches.unshift({ query, entityIds, durationMs, ts: now() });
  if (store.searches.length > 500) store.searches.splice(500);
  save();
}

export function getSearchHistory(limit = 20) {
  return load().searches.slice(0, limit);
}

// ── Graph stats ───────────────────────────────────────────────────────────────

export function getStats() {
  const store = load();
  return {
    entities:      Object.keys(store.entities).length,
    relationships: store.relationships.length,
    searches:      store.searches.length,
    lastSaved:     store.lastSaved,
    topEntities:   Object.values(store.entities)
      .sort((a, b) => (b.searchCount || 0) - (a.searchCount || 0))
      .slice(0, 10)
      .map(e => ({ id: e.id, name: e.name, type: e.type, count: e.searchCount })),
  };
}

export { save as saveStore, load as loadStore };
