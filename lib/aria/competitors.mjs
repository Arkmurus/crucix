// lib/aria/competitors.mjs
// Module 6: Competitor Movement Tracker
//
// Tracks when competitors win contracts, enter markets, or make strategic moves.
// Feeds into ARIA context so she can position Arkmurus defensively.
// Auto-scans intel ledger + defence news for competitor activity.

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const COMP_FILE = join(process.cwd(), 'runs', 'aria_competitors.json');
const COMP_REDIS_KEY = 'crucix:aria:competitors';
const MAX_MOVES = 300;

let _cache = null;

// ── Known competitors by region ──────────────────────────────────────────────
const COMPETITORS = {
  // Turkish OEMs — most aggressive in Africa
  'baykar':      { name: 'Baykar', country: 'Turkey', products: 'UAVs (TB2, Akinci)', threat: 'HIGH', strategy: 'Aggressive pricing, political backing, proven combat record' },
  'tusas':       { name: 'Turkish Aerospace', country: 'Turkey', products: 'UAVs, helicopters, trainer aircraft', threat: 'HIGH', strategy: 'Government-to-government deals, offset packages' },
  'aselsan':     { name: 'Aselsan', country: 'Turkey', products: 'Electronics, radar, comms', threat: 'MEDIUM', strategy: 'Bundled with platform sales' },
  'otokar':      { name: 'Otokar', country: 'Turkey', products: 'Armoured vehicles', threat: 'HIGH', strategy: '20-30% below Western pricing' },
  'fnss':        { name: 'FNSS', country: 'Turkey', products: 'AFVs, amphibious vehicles', threat: 'MEDIUM', strategy: 'Technology transfer offers' },
  // Chinese OEMs
  'norinco':     { name: 'Norinco', country: 'China', products: 'Vehicles, small arms, ammunition', threat: 'HIGH', strategy: '40-50% below Western, bundled financing' },
  'avic':        { name: 'AVIC/CATIC', country: 'China', products: 'Aircraft, UAVs (Wing Loong)', threat: 'HIGH', strategy: 'State financing, infrastructure-for-arms deals' },
  'poly technologies': { name: 'Poly Technologies', country: 'China', products: 'Weapons systems, air defence', threat: 'MEDIUM', strategy: 'Chinese government backing, BRI leverage' },
  'poly defence':      { name: 'Poly Technologies', country: 'China', products: 'Weapons systems, air defence', threat: 'MEDIUM', strategy: 'Chinese government backing, BRI leverage' },
  // Russian — sanctioned but legacy presence
  'rostec':      { name: 'Rostec', country: 'Russia', products: 'Aircraft, helicopters, air defence', threat: 'LOW (sanctioned)', strategy: 'Replacement opportunity — cannot deliver spare parts' },
  'almaz':       { name: 'Almaz-Antey', country: 'Russia', products: 'Air defence (S-300/400)', threat: 'LOW (sanctioned)', strategy: 'Replacement window for existing operators' },
  // Israeli
  'elbit':       { name: 'Elbit Systems', country: 'Israel', products: 'UAVs, surveillance, training', threat: 'HIGH', strategy: 'Technology leader, proven systems, strong in Africa' },
  'rafael':      { name: 'Rafael', country: 'Israel', products: 'Missiles, air defence, precision weapons', threat: 'MEDIUM', strategy: 'Premium pricing, top-tier capability' },
  'iai':         { name: 'IAI', country: 'Israel', products: 'UAVs, satellites, radar', threat: 'MEDIUM', strategy: 'State-backed, intelligence cooperation packages' },
  // South African
  'paramount':   { name: 'Paramount Group', country: 'South Africa', products: 'Protected vehicles', threat: 'MEDIUM (potential partner)', strategy: 'Strong Africa presence — consider partnership vs competition' },
  'denel':       { name: 'Denel', country: 'South Africa', products: 'Ammunition, missiles, helicopters', threat: 'LOW', strategy: 'Financial difficulties — acquisition/partnership opportunity' },
  // South Korean — fast-growing global competitor
  'hanwha':      { name: 'Hanwha Defense', country: 'South Korea', products: 'K9 howitzer, K21 IFV, Redback', threat: 'HIGH', strategy: 'NATO-grade quality, 15-25% below Western pricing, rapid delivery, technology transfer' },
  'hyundai rotem': { name: 'Hyundai Rotem', country: 'South Korea', products: 'K2 Black Panther tank, railway', threat: 'MEDIUM', strategy: 'Poland mega-deal proved global credibility' },
  'kai':         { name: 'Korea Aerospace Industries', country: 'South Korea', products: 'FA-50 fighter, KUH Surion helicopter', threat: 'HIGH', strategy: 'FA-50 to Poland, Iraq, Philippines — proven export platform' },
  // Western majors
  'bae':         { name: 'BAE Systems', country: 'UK', products: 'All categories', threat: 'MEDIUM', strategy: 'Premium, established government relationships' },
  'leonardo':    { name: 'Leonardo', country: 'Italy', products: 'Helicopters, trainers, naval', threat: 'HIGH', strategy: 'Active in Nigeria (M-346), strong naval capability' },
  'rheinmetall': { name: 'Rheinmetall', country: 'Germany', products: 'Vehicles, ammunition, air defence', threat: 'MEDIUM', strategy: 'Quality premium, German export restrictions can limit reach' },
  'thales':      { name: 'Thales', country: 'France', products: 'Radar, communications, naval', threat: 'MEDIUM', strategy: 'Strong Francophone Africa presence' },
  'damen':       { name: 'Damen', country: 'Netherlands', products: 'Naval vessels', threat: 'MEDIUM (potential partner)', strategy: 'Best naval option for Africa — consider partnership' },
};

const COMPETITOR_KEYWORDS = Object.keys(COMPETITORS);

function loadDB() {
  if (_cache) return _cache;
  try {
    if (existsSync(COMP_FILE)) {
      _cache = JSON.parse(readFileSync(COMP_FILE, 'utf8'));
      return _cache;
    }
  } catch {}
  _cache = { moves: [], version: 1 };
  return _cache;
}

function saveDB(db) {
  _cache = db;
  try {
    const dir = dirname(COMP_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(COMP_FILE, JSON.stringify(db, null, 2), 'utf8');
  } catch {}
  redisSet(COMP_REDIS_KEY, db).catch(() => {});
}

export async function initCompetitors() {
  try {
    const remote = await redisGet(COMP_REDIS_KEY);
    if (remote && remote.moves) { _cache = remote; return; }
  } catch {}
  loadDB();
  console.log(`[Competitors] Initialized (${_cache.moves.length} tracked moves)`);
}

/**
 * Scan current sweep data for competitor activity.
 * Called after each sweep, stores detected moves.
 */
export function scanForCompetitorMoves(currentData) {
  if (!currentData) return 0;
  const db = loadDB();
  let added = 0;

  // Collect signal texts from all sources
  const signals = [];
  const defNews = Array.isArray(currentData.defenseNews) ? currentData.defenseNews : (currentData.defenseNews?.updates || []);
  for (const d of defNews) signals.push({ text: (d.title || '') + ' ' + (d.content || d.description || ''), source: d.source || 'DefenseNews', url: d.url || '' });
  for (const s of (currentData.tg?.urgent || [])) signals.push({ text: s.text || '', source: s.channel || 'OSINT', url: s.url || '' });
  for (const t of (currentData.bdIntelligence?.tenders || [])) signals.push({ text: t.title + ' ' + (t.summary || ''), source: 'BD', url: t.url || '' });

  // Check each signal for competitor mentions
  const existingKeys = new Set(db.moves.map(m => m.text?.substring(0, 60)));

  for (const sig of signals) {
    const lower = (sig.text || '').toLowerCase();
    if (lower.length < 30) continue;
    const sigKey = sig.text.substring(0, 60);
    if (existingKeys.has(sigKey)) continue;

    const matchedCompetitors = [];
    for (const [kw, comp] of Object.entries(COMPETITORS)) {
      if (lower.includes(kw) || lower.includes(comp.name.toLowerCase())) {
        matchedCompetitors.push(comp);
      }
    }
    if (!matchedCompetitors.length) continue;

    // Determine move type
    const isWin = lower.match(/awarded|won|signed|delivered|selected|contract|deal|order|procured/);
    const isEntry = lower.match(/partnership|cooperation|agreement|mou|exhibition|demonstrated/);
    const type = isWin ? 'CONTRACT_WIN' : isEntry ? 'MARKET_ENTRY' : 'ACTIVITY';

    db.moves.unshift({
      competitor: matchedCompetitors[0].name,
      country: matchedCompetitors[0].country,
      type,
      text: sig.text.substring(0, 300),
      source: sig.source,
      url: sig.url,
      ts: new Date().toISOString(),
    });
    existingKeys.add(sigKey);
    added++;
  }

  // Prune old moves
  if (db.moves.length > MAX_MOVES) db.moves.splice(MAX_MOVES);
  if (added > 0) {
    saveDB(db);
    console.log(`[Competitors] +${added} competitor moves detected`);
  }
  return added;
}

/**
 * Get competitor context for ARIA prompt injection.
 */
export function getCompetitorContext(query) {
  const q = (query || '').toLowerCase();
  const words = q.split(/\s+/).filter(w => w.length > 3);
  if (!words.length) return '';

  const db = loadDB();
  // Find relevant competitor moves
  const relevant = db.moves.filter(m => {
    const text = (m.text + ' ' + m.competitor).toLowerCase();
    return words.some(w => text.includes(w));
  }).slice(0, 8);

  // Find relevant competitor profiles
  const profiles = [];
  for (const [kw, comp] of Object.entries(COMPETITORS)) {
    if (words.some(w => kw.includes(w) || comp.name.toLowerCase().includes(w) || comp.products.toLowerCase().includes(w))) {
      profiles.push(comp);
    }
  }

  if (!relevant.length && !profiles.length) return '';

  const parts = [];
  if (profiles.length) {
    parts.push('COMPETITOR PROFILES:\n' +
      profiles.slice(0, 5).map(c => `- ${c.name} (${c.country}) | ${c.products} | Threat: ${c.threat} | Strategy: ${c.strategy}`).join('\n'));
  }
  if (relevant.length) {
    parts.push('RECENT COMPETITOR MOVES:\n' +
      relevant.map(m => {
        const age = Math.floor((Date.now() - new Date(m.ts).getTime()) / 86400000);
        return `- [${m.type}] ${m.competitor} — ${age}d ago: ${m.text.substring(0, 150)}`;
      }).join('\n'));
  }
  return '\n\n[COMPETITIVE INTELLIGENCE]\n' + parts.join('\n\n');
}

export function getCompetitorStats() {
  const db = loadDB();
  const byCompetitor = {};
  for (const m of db.moves) {
    byCompetitor[m.competitor] = (byCompetitor[m.competitor] || 0) + 1;
  }
  return {
    totalMoves: db.moves.length,
    byCompetitor: Object.entries(byCompetitor).sort((a, b) => b[1] - a[1]),
    recentMoves: db.moves.slice(0, 5),
  };
}
