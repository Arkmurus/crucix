// lib/aria/contacts.mjs
// Module 5: Human Intelligence Layer — tracks key decision-makers
//
// Defence procurement in Lusophone Africa is decided by <30 people.
// This module tracks: appointments, roles, tenure windows, public appearances.
// When General X gets the FAA Logistics job → 90-day relationship window alert.

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const CONTACTS_FILE = join(process.cwd(), 'runs', 'aria_contacts.json');
const CONTACTS_REDIS_KEY = 'crucix:aria:contacts';

let _cache = null;

function defaultDB() {
  return { contacts: [], version: 1 };
}

function loadDB() {
  if (_cache) return _cache;
  try {
    if (existsSync(CONTACTS_FILE)) {
      _cache = JSON.parse(readFileSync(CONTACTS_FILE, 'utf8'));
      return _cache;
    }
  } catch {}
  _cache = defaultDB();
  return _cache;
}

function saveDB(db) {
  _cache = db;
  try {
    const dir = dirname(CONTACTS_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(CONTACTS_FILE, JSON.stringify(db, null, 2), 'utf8');
  } catch {}
  redisSet(CONTACTS_REDIS_KEY, db).catch(() => {});
}

export async function initContacts() {
  try {
    const remote = await redisGet(CONTACTS_REDIS_KEY);
    if (remote && remote.contacts) {
      _cache = remote;
      console.log(`[Contacts] Loaded ${remote.contacts.length} contacts from Redis`);
      return;
    }
  } catch {}
  loadDB();
  // Only seed if truly empty — check for a known seed entry to prevent duplicates
  if (!_cache.contacts.length || !_cache.contacts.some(c => c.role === 'procurement_authority')) {
    _cache = defaultDB();
    seedContacts();
  }
  console.log(`[Contacts] Initialized (${_cache.contacts.length} contacts)`);
}

// ── Contact Operations ────────────────────────────────────────────────────────

export function addContact(contact) {
  const db = loadDB();
  const existing = db.contacts.findIndex(c =>
    c.name.toLowerCase() === (contact.name || '').toLowerCase() && c.country === contact.country
  );
  if (existing >= 0) {
    db.contacts[existing] = { ...db.contacts[existing], ...contact, updatedAt: new Date().toISOString() };
  } else {
    db.contacts.push({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      ...contact,
      createdAt: new Date().toISOString(),
      interactions: [],
    });
  }
  saveDB(db);
}

export function getContactsByCountry(country) {
  const db = loadDB();
  return db.contacts.filter(c => c.country?.toLowerCase() === country.toLowerCase());
}

export function getContactsByRole(role) {
  const db = loadDB();
  const r = role.toLowerCase();
  return db.contacts.filter(c => (c.role || '').toLowerCase().includes(r) || (c.title || '').toLowerCase().includes(r));
}

export function searchContacts(query) {
  const db = loadDB();
  const q = (query || '').toLowerCase();
  const words = q.split(/\s+/).filter(w => w.length > 2);
  return db.contacts.filter(c => {
    const text = [c.name, c.role, c.title, c.country, c.organisation, c.notes].join(' ').toLowerCase();
    return words.some(w => text.includes(w));
  }).slice(0, 10);
}

export function getAllContacts() {
  return loadDB().contacts;
}

/**
 * Build contact context for ARIA prompt injection.
 */
export function getContactContext(query) {
  const matches = searchContacts(query);
  if (!matches.length) return '';

  return '\n\n[CONTACT INTELLIGENCE — key decision-makers]\n' +
    matches.map(c => {
      const tenure = c.appointedDate ? Math.floor((Date.now() - new Date(c.appointedDate).getTime()) / 86400000) : null;
      return `- ${c.name} | ${c.title || c.role} | ${c.organisation || ''} | ${c.country}` +
        (tenure !== null ? ` | ${tenure}d in role` : '') +
        (c.influence ? ` | Influence: ${c.influence}` : '') +
        (c.notes ? ` | ${c.notes}` : '');
    }).join('\n');
}

// ── Seed with known Lusophone defence leadership ──────────────────────────────
function seedContacts() {
  const db = loadDB();
  const seeds = [
    // Angola
    { name: 'Ministry of National Defence', country: 'Angola', organisation: 'Ministério da Defesa Nacional', role: 'procurement_authority', title: 'MoD — Primary procurement authority for FAA', influence: 'CRITICAL', notes: 'Controls defence budget allocation. Portuguese language essential. Formal approach through Defence Attaché or direct MoD engagement.' },
    { name: 'FAA Equipment Directorate', country: 'Angola', organisation: 'Forças Armadas Angolanas', role: 'equipment', title: 'Equipment & Logistics Directorate', influence: 'HIGH', notes: 'Technical evaluation body for all major equipment procurements. Key for vehicle, UAV, and ammunition requirements.' },
    { name: 'Angolan Navy Command', country: 'Angola', organisation: 'Marinha de Guerra Angolana', role: 'naval_procurement', title: 'Naval Command — Patrol vessel and maritime security', influence: 'HIGH', notes: 'Separate procurement line for naval assets. Contact via Naval Attaché. Portuguese-speaking interlocutors preferred.' },

    // Mozambique
    { name: 'Ministry of National Defence', country: 'Mozambique', organisation: 'Ministério da Defesa Nacional', role: 'procurement_authority', title: 'MoD — Primary authority for FADM procurement', influence: 'CRITICAL', notes: 'Active counter-insurgency in Cabo Delgado drives urgent procurement. EU and SADC training missions create equipment requirements.' },
    { name: 'FADM Logistics Command', country: 'Mozambique', organisation: 'Forças Armadas de Defesa de Moçambique', role: 'logistics', title: 'Logistics & Equipment Command', influence: 'HIGH', notes: 'Manages equipment lifecycle for ground forces. Key contact for vehicles, small arms, ammunition.' },

    // Guinea-Bissau
    { name: 'Ministry of Defence', country: 'Guinea-Bissau', organisation: 'Ministério da Defesa', role: 'procurement_authority', title: 'MoD — Small military, limited budget', influence: 'CRITICAL', notes: 'Very small armed forces (FASB). Procurement often via ECOWAS grants or bilateral aid. Portuguese language essential. Personal relationships drive decisions.' },

    // Nigeria
    { name: 'Ministry of Defence', country: 'Nigeria', organisation: 'Federal Ministry of Defence', role: 'procurement_authority', title: 'MoD — Largest military in West Africa', influence: 'CRITICAL', notes: 'Major procurements: Super Tucano (USA/Brazil), M-346 (Italy), patrol vessels. Active counter-terrorism procurement. English-speaking.' },
    { name: 'Defence Industries Corporation of Nigeria', country: 'Nigeria', organisation: 'DICON', role: 'local_industry', title: 'State defence manufacturer — offset/local content partner', influence: 'MEDIUM', notes: 'Local production capability for ammunition, small arms. Offset obligations often routed through DICON. Key for meeting local content requirements.' },

    // Kenya
    { name: 'Ministry of Defence', country: 'Kenya', organisation: 'Ministry of Defence', role: 'procurement_authority', title: 'MoD — East African regional hub', influence: 'CRITICAL', notes: 'Active in AMISOM/ATMIS. Modernisation focus: ISR, border security, helicopters. English-speaking. Formal tender process.' },

    // Ghana
    { name: 'Ministry of Defence', country: 'Ghana', organisation: 'Ministry of Defence', role: 'procurement_authority', title: 'MoD — GAF procurement authority', influence: 'CRITICAL', notes: 'Burma Camp, Accra. English-speaking. UN peacekeeping contributor. Equipment ageing — modernisation needed for vehicles, patrol vessels.' },

    // Senegal
    { name: 'Ministère des Forces Armées', country: 'Senegal', organisation: 'Ministère des Forces Armées', role: 'procurement_authority', title: 'MoD — Senegalese Armed Forces procurement', influence: 'CRITICAL', notes: 'French language essential. Strong ECOWAS contributor. Active counter-insurgency (Casamance). Nexter/Airbus historically preferred — opening to alternatives.' },

    // Ethiopia
    { name: 'Ministry of Defence', country: 'Ethiopia', organisation: 'Ministry of Defence', role: 'procurement_authority', title: 'MoD — ENDF procurement (post-Tigray reconstruction)', influence: 'CRITICAL', notes: 'Massive reconstruction after civil war. Previously Russian-equipped, now diversifying. Buying Bayraktar TB2 (Turkey). Budget recovering. English + Amharic.' },

    // Indonesia
    { name: 'Ministry of Defence', country: 'Indonesia', organisation: 'Kementerian Pertahanan', role: 'procurement_authority', title: 'MoD — MEF programme procurement authority', influence: 'CRITICAL', notes: 'MEF programme through 2029. Buying Rafale (France), K2 tanks (Korea), submarines. Offset 35-85% required. Jakarta. English + Indonesian.' },

    // Philippines
    { name: 'Department of National Defense', country: 'Philippines', organisation: 'DND', role: 'procurement_authority', title: 'DND — Horizon 3 modernisation authority', influence: 'CRITICAL', notes: 'Horizon 3 programme (2023-2028). US FMS dominant but diversifying to Korea, Israel. Camp Aguinaldo, Quezon City. English-speaking.' },

    // UAE
    { name: 'EDGE Group', country: 'UAE', organisation: 'EDGE Group', role: 'local_industry', title: 'UAE defence industrial champion — Tawazun offset partner', influence: 'HIGH', notes: 'State-backed defence conglomerate. All major deals route through EDGE. 60% local content (Tawazun) required. IDEX (February) is key meeting point. Agent mandatory.' },

    // Saudi Arabia
    { name: 'GAMI', country: 'Saudi Arabia', organisation: 'General Authority for Military Industries', role: 'procurement_authority', title: 'GAMI — centralised defence procurement authority', influence: 'CRITICAL', notes: 'Vision 2030 localisation gatekeeper. All defence procurement routes through GAMI. 50-60% offset target. Agent mandatory. Very formal, very slow (2-5 years). Arabic + English.' },

    // Key OEM contacts
    { name: 'Paramount Group', country: 'South Africa', organisation: 'Paramount Group', role: 'oem', title: 'Protected vehicle manufacturer — strong Africa presence', influence: 'HIGH', notes: 'Mbombe, Marauder, Matador. Non-ITAR. Direct sales to Nigeria, Kenya, Mozambique. Contact: Sales Director Africa.' },
    { name: 'Baykar', country: 'Turkey', organisation: 'Baykar Defence', role: 'oem', title: 'UAV manufacturer — TB2, TB3, Akinci', influence: 'HIGH', notes: 'Aggressive Africa expansion. Sales to Nigeria, Ethiopia, Angola, Morocco. Contact via Turkish Defence Attaché or direct Sales.' },
    { name: 'Damen Shipyards', country: 'Netherlands', organisation: 'Damen', role: 'oem', title: 'Naval vessel builder — patrol boats, coast guard', influence: 'HIGH', notes: 'Active across African coast guards. Non-ITAR. Good financing packages. Contact: Sales Manager Africa.' },
    { name: 'Embraer Defence', country: 'Brazil', organisation: 'Embraer', role: 'oem', title: 'Super Tucano, KC-390 — Lusophone advantage', influence: 'HIGH', notes: 'Portuguese-speaking sales team. Strong Angola/Mozambique relationships. A-29 Super Tucano proven in counter-insurgency.' },
    { name: 'Leonardo', country: 'Italy', organisation: 'Leonardo S.p.A.', role: 'oem', title: 'Helicopters, trainers, naval systems — strong Nigeria presence', influence: 'HIGH', notes: '24 M-346 to Nigeria (2026). AW109/AW139 helicopters across Africa. Non-ITAR. Contact: VP International Sales.' },
    { name: 'Nammo', country: 'Norway', organisation: 'Nammo AS', role: 'oem', title: 'NATO ammunition supplier — all calibres', influence: 'HIGH', notes: 'All NATO standard ammunition. Non-ITAR (Norwegian production). 30mm, 155mm, shoulder-fired systems (M72). Contact: Sales Director.' },
    { name: 'Rheinmetall', country: 'Germany', organisation: 'Rheinmetall AG', role: 'oem', title: 'Vehicles, ammunition, air defence — premium quality', influence: 'HIGH', notes: 'Lynx IFV, Fuchs APC, all calibre ammunition. German export restrictions can limit reach. Quality premium. Contact: International Sales.' },
  ];

  for (const s of seeds) db.contacts.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2,6), ...s, createdAt: new Date().toISOString(), interactions: [] });
  saveDB(db);
}
