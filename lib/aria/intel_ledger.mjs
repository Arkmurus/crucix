// lib/aria/intel_ledger.mjs
// Intelligence Ledger — persistent, queryable store of all significant signals
//
// Every sweep, important signals are logged here indexed by country/entity/product.
// ARIA queries this to answer "What's happening in Angola?" or "Why is there demand for 30mm?"
//
// Storage: Redis (primary, 90-day TTL) + local file (fallback)
// Structure: flat array of signal entries, max 2000, pruned by age

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const LEDGER_FILE = join(process.cwd(), 'runs', 'intel_ledger.json');
const LEDGER_REDIS_KEY = 'crucix:intel_ledger';
const MAX_ENTRIES = 2000;
const MAX_AGE_DAYS = 30;

let _cache = null;

function loadLedger() {
  if (_cache) return _cache;
  try {
    if (existsSync(LEDGER_FILE)) {
      _cache = JSON.parse(readFileSync(LEDGER_FILE, 'utf8'));
      return _cache;
    }
  } catch {}
  _cache = { signals: [], version: 1 };
  return _cache;
}

function saveLedger(ledger) {
  _cache = ledger;
  try {
    const dir = dirname(LEDGER_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(LEDGER_FILE, JSON.stringify(ledger), 'utf8');
  } catch {}
  redisSet(LEDGER_REDIS_KEY, ledger).catch(() => {});
}

export async function initLedger() {
  try {
    const remote = await redisGet(LEDGER_REDIS_KEY);
    if (remote && remote.signals) {
      _cache = remote;
      console.log(`[Intel Ledger] Loaded ${remote.signals.length} signals from Redis`);
      try { writeFileSync(LEDGER_FILE, JSON.stringify(remote), 'utf8'); } catch {}
      return;
    }
  } catch {}
  loadLedger();
  console.log(`[Intel Ledger] Initialized (${_cache.signals.length} signals)`);
}

// ── Country/entity extraction ────────────────────────────────────────────────
const COUNTRIES = [
  'Angola', 'Mozambique', 'Nigeria', 'Kenya', 'Brazil', 'Indonesia', 'Philippines',
  'Vietnam', 'UAE', 'Saudi Arabia', 'Ghana', 'Senegal', 'Ethiopia', 'Uganda',
  'Tanzania', 'Rwanda', 'Cameroon', "Côte d'Ivoire", 'Guinea-Bissau', 'Cape Verde',
  'South Africa', 'Libya', 'Jordan', 'Bangladesh', 'Bulgaria', 'Romania', 'Poland',
  'Greece', 'Colombia', 'Peru', 'Iran', 'Israel', 'Ukraine', 'Russia', 'China',
  'Turkey', 'India', 'Pakistan', 'Egypt', 'Morocco', 'Algeria', 'Tunisia',
  'Lebanon', 'Syria', 'Iraq', 'Yemen', 'Somalia', 'Sudan', 'Madagascar',
];

const PRODUCT_KEYWORDS = {
  'ammunition': ['ammunition', 'ammo', 'round', 'cartridge', 'calibre', 'caliber', 'mortar', 'shell', 'projectile'],
  'vehicles': ['vehicle', 'armoured', 'armored', 'apc', 'ifv', 'mrap', 'tank'],
  'aircraft': ['aircraft', 'fighter', 'helicopter', 'drone', 'uav', 'f-15', 'f-16', 'f-35', 'su-', 'mig-', 'apache', 'black hawk'],
  'naval': ['vessel', 'frigate', 'corvette', 'patrol boat', 'submarine', 'destroyer', 'coast guard', 'naval'],
  'missiles': ['missile', 'rocket', 'sam', 'manpad', 'patriot', 'iron dome', 'javelin', 'stinger', 'himars'],
  'radar': ['radar', 'air defense', 'air defence', 'shorad', 'c-ram', 'ewi', 'electronic warfare'],
  'small_arms': ['rifle', 'pistol', 'machine gun', 'small arms', 'carbine', 'sniper'],
  'surveillance': ['surveillance', 'isr', 'reconnaissance', 'sigint', 'elint', 'comint', 'border security'],
  'training': ['training', 'exercise', 'drill', 'simulation', 'capacity building'],
};

const OEM_KEYWORDS = [
  'lockheed', 'boeing', 'raytheon', 'northrop', 'general dynamics', 'bae systems',
  'leonardo', 'airbus', 'dassault', 'saab', 'rheinmetall', 'thales',
  'elbit', 'rafael', 'iai', 'turkish aerospace', 'baykar', 'aselsan',
  'norinco', 'avic', 'csgc', 'rostec', 'almaz-antey',
  'paramount', 'denel', 'armscor', 'embraer', 'nammo', 'nexter',
  'hanwha', 'hyundai rotem', 'damen', 'fincantieri', 'navantia',
  'mbda', 'kongsberg', 'fnss', 'otokar', 'nurol',
];

function extractEntities(text) {
  const lower = (text || '').toLowerCase();
  const countries = COUNTRIES.filter(c => lower.includes(c.toLowerCase()));
  const products = [];
  for (const [cat, kws] of Object.entries(PRODUCT_KEYWORDS)) {
    if (kws.some(kw => lower.includes(kw))) products.push(cat);
  }
  const oems = OEM_KEYWORDS.filter(oem => lower.includes(oem));
  return { countries, products, oems };
}

// ── Ingest signals from a sweep ──────────────────────────────────────────────
export function ingestSweepSignals(currentData) {
  if (!currentData) return 0;
  const ledger = loadLedger();
  const now = new Date();
  let added = 0;

  // Collect all significant signals from this sweep
  const rawSignals = [];

  // OSINT Telegram signals
  for (const s of (currentData.tg?.urgent || [])) {
    rawSignals.push({ text: s.text || '', source: s.channel || 'OSINT', type: 'osint', url: s.url || '', date: s.date });
  }

  // Correlations (multi-source convergence)
  for (const c of (currentData.correlations || [])) {
    if (c.severity !== 'critical' && c.severity !== 'high') continue;
    for (const sig of (c.topSignals || []).slice(0, 3)) {
      rawSignals.push({ text: `[${c.region}] ${sig.text || ''}`, source: sig.source || 'correlation', type: 'correlation', url: sig.url || '', severity: c.severity });
    }
  }

  // Defence news
  const defNews = Array.isArray(currentData.defenseNews) ? currentData.defenseNews : (currentData.defenseNews?.updates || []);
  for (const d of defNews.slice(0, 10)) {
    rawSignals.push({ text: d.title || '', source: d.source || 'DefenseNews', type: 'defense_news', url: d.url || d.link || '' });
  }

  // Procurement tenders
  for (const t of (currentData.procurementTenders?.updates || []).slice(0, 10)) {
    rawSignals.push({ text: (t.title || '') + ' ' + (t.content || t.summary || ''), source: t.source || 'Procurement', type: 'tender', url: t.url || t.link || '' });
  }

  // BD intelligence tenders
  if (currentData.bdIntelligence?.tenders) {
    for (const t of currentData.bdIntelligence.tenders.slice(0, 10)) {
      rawSignals.push({ text: t.title + ' — ' + t.market + ' ' + (t.summary || ''), source: 'BD', type: 'bd_tender', url: t.url || '' });
    }
  }

  // BD brain sales leads
  if (currentData.bdIntelligence?.brain?.salesLeads) {
    for (const l of currentData.bdIntelligence.brain.salesLeads) {
      rawSignals.push({ text: `${l.market}: ${l.lead || ''} — ${l.estimatedValue || ''} — ${l.nextStep || ''}`, source: 'Brain', type: 'brain_lead' });
    }
  }

  // Dedup against existing ledger entries (by text similarity)
  const existingTexts = new Set(ledger.signals.map(s => s.text.substring(0, 80).toLowerCase()));

  for (const sig of rawSignals) {
    if (!sig.text || sig.text.length < 20) continue;
    const textKey = sig.text.substring(0, 80).toLowerCase();
    if (existingTexts.has(textKey)) continue;
    existingTexts.add(textKey);

    const entities = extractEntities(sig.text);
    if (entities.countries.length === 0 && entities.products.length === 0 && entities.oems.length === 0) continue;

    ledger.signals.unshift({
      text: sig.text.substring(0, 400),
      source: sig.source,
      type: sig.type,
      url: sig.url || '',
      countries: entities.countries,
      products: entities.products,
      oems: entities.oems,
      severity: sig.severity || 'medium',
      ts: now.toISOString(),
    });
    added++;
  }

  // Prune old entries and cap size
  const cutoff = Date.now() - MAX_AGE_DAYS * 86400000;
  ledger.signals = ledger.signals
    .filter(s => new Date(s.ts).getTime() > cutoff)
    .slice(0, MAX_ENTRIES);

  if (added > 0) {
    saveLedger(ledger);
    console.log(`[Intel Ledger] +${added} signals stored (${ledger.signals.length} total)`);
  }
  return added;
}

// ── Query the ledger ─────────────────────────────────────────────────────────

/**
 * Search the intel ledger by country, product, OEM, or free text.
 * Returns formatted string for ARIA prompt injection.
 */
export function queryLedger(query) {
  const ledger = loadLedger();
  if (!ledger.signals.length) return '';

  const q = (query || '').toLowerCase();
  const words = q.split(/\s+/).filter(w => w.length > 3);
  if (!words.length) return '';

  // Find matching signals
  const matches = [];
  for (const sig of ledger.signals) {
    let score = 0;
    const combined = (sig.text + ' ' + sig.countries.join(' ') + ' ' + sig.products.join(' ') + ' ' + sig.oems.join(' ')).toLowerCase();
    for (const w of words) {
      if (combined.includes(w)) score += 2;
    }
    // Boost for country/product/OEM exact matches
    for (const c of sig.countries) { if (q.includes(c.toLowerCase())) score += 5; }
    for (const p of sig.products) { if (q.includes(p)) score += 4; }
    for (const o of sig.oems) { if (q.includes(o)) score += 4; }
    if (score > 0) matches.push({ ...sig, _score: score });
  }

  if (!matches.length) return '';

  matches.sort((a, b) => b._score - a._score);
  const top = matches.slice(0, 12);

  return '\n\n[INTELLIGENCE LEDGER — recent signals matching this query]\n' +
    top.map(s => {
      const age = Math.floor((Date.now() - new Date(s.ts).getTime()) / 86400000);
      return `- [${s.source}] ${age}d ago: ${s.text.substring(0, 200)}` +
        (s.countries.length ? ` | Markets: ${s.countries.join(', ')}` : '') +
        (s.oems.length ? ` | OEMs: ${s.oems.join(', ')}` : '');
    }).join('\n');
}

/**
 * Get situation summary for a specific country.
 */
export function getCountrySituation(country) {
  const ledger = loadLedger();
  const lower = country.toLowerCase();
  const signals = ledger.signals.filter(s => s.countries.some(c => c.toLowerCase() === lower));
  if (!signals.length) return null;

  return {
    country,
    signalCount: signals.length,
    latestSignal: signals[0]?.ts,
    byType: signals.reduce((acc, s) => { acc[s.type] = (acc[s.type] || 0) + 1; return acc; }, {}),
    products: [...new Set(signals.flatMap(s => s.products))],
    oems: [...new Set(signals.flatMap(s => s.oems))],
    recentSignals: signals.slice(0, 5).map(s => ({ text: s.text.substring(0, 150), source: s.source, ts: s.ts })),
  };
}

export function getLedgerStats() {
  const ledger = loadLedger();
  const byCountry = {};
  const byType = {};
  for (const s of ledger.signals) {
    for (const c of s.countries) byCountry[c] = (byCountry[c] || 0) + 1;
    byType[s.type] = (byType[s.type] || 0) + 1;
  }
  return {
    totalSignals: ledger.signals.length,
    byCountry: Object.entries(byCountry).sort((a, b) => b[1] - a[1]).slice(0, 15),
    byType,
    oldestSignal: ledger.signals.at(-1)?.ts,
    newestSignal: ledger.signals[0]?.ts,
  };
}
