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
    (c.name || '').toLowerCase() === (contact.name || '').toLowerCase() && c.country === contact.country
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
    const text = [c.name, c.role, c.title, c.country, c.organisation, c.notes].filter(Boolean).join(' ').toLowerCase();
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
  const matches = searchContacts(query).slice(0, 4); // max 4 contacts to save context budget
  if (!matches.length) return '';

  return '\n\n[CONTACT INTELLIGENCE]\n' +
    matches.map(c => {
      const tenure = c.appointedDate ? Math.floor((Date.now() - new Date(c.appointedDate).getTime()) / 86400000) : null;
      return `- ${c.name} | ${c.title || c.role} | ${c.country}` +
        (c.influence ? ` [${c.influence}]` : '') +
        (tenure !== null ? ` ${tenure}d in role` : '');
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

    // ── Angola — Expanded ──────────────────────────────────────────────────────
    { name: 'Gen. João Ernesto dos Santos', country: 'Angola', organisation: 'Ministério da Defesa Nacional', role: 'minister', title: 'Minister of National Defence', influence: 'CRITICAL', appointedDate: '2024-09-15', notes: 'Political appointee, close to presidency. All major defence decisions require ministerial sign-off. Approach via formal diplomatic channel or Defence Attaché. Portuguese only.' },
    { name: 'Gen. António José Maria', country: 'Angola', organisation: 'Estado-Maior General das FAA', role: 'chief_of_staff', title: 'Chief of General Staff (CEMGFA)', influence: 'CRITICAL', appointedDate: '2025-02-01', notes: 'Top military officer. Controls operational requirements and force planning. Recently appointed — 90-day relationship window active. Portuguese essential.' },
    { name: 'Gen. Carlos Alberto Fernandes', country: 'Angola', organisation: 'Exército (FAA)', role: 'army_commander', title: 'Commander of FAA Ground Forces', influence: 'HIGH', appointedDate: '2024-06-20', notes: 'Drives vehicle, artillery, and infantry equipment requirements. Key decision-maker for Marauder/Mbombe-type procurements. Approach via CEMGFA introduction.' },
    { name: 'Gen. Hélder da Silva Pitta', country: 'Angola', organisation: 'Força Aérea Nacional (FAN)', role: 'air_force_commander', title: 'Commander of Angolan Air Force', influence: 'HIGH', appointedDate: '2025-01-10', notes: 'Air force modernisation is priority. Interested in UAVs (Bayraktar), light attack (Super Tucano), transport. Recently appointed — window open.' },
    { name: 'Vice-Adm. Manuel Augusto Neto', country: 'Angola', organisation: 'Marinha de Guerra Angolana', role: 'navy_commander', title: 'Commander of Angolan Navy', influence: 'HIGH', appointedDate: '2024-03-15', notes: 'Maritime security of oil infrastructure is top priority. Patrol vessel and coastal radar requirements. Damen and Israel Shipyards competing.' },
    { name: 'Col. Ricardo Mendes de Almeida', country: 'Angola', organisation: 'SIMPORTEX', role: 'procurement_director', title: 'Director of SIMPORTEX — State Arms Import Agency', influence: 'CRITICAL', appointedDate: '2024-11-01', notes: 'ALL defence imports must route through SIMPORTEX. Gatekeeper for end-user certificates. Relationship here is prerequisite for any deal. Very formal, Portuguese only.' },
    { name: 'Brig. Paulo Vieira Lopes', country: 'Angola', organisation: 'FAA Logistics Directorate', role: 'procurement_official', title: 'Deputy Director — Equipment Procurement', influence: 'HIGH', notes: 'Technical evaluator for ground force equipment. Manages requirement specifications. More accessible than senior leadership. Portuguese preferred.' },
    { name: 'Col. Tomás Gomes Pereira', country: 'Angola', organisation: 'Embassy of Angola, Brussels', role: 'defence_attache', title: 'Defence Attaché — Belgium/EU', influence: 'MEDIUM', appointedDate: '2025-03-01', notes: 'Key intermediary for European OEM introductions. Attends Eurosatory, DSEI. Newly posted — relationship window open. Portuguese and French.' },
    { name: 'Col. Francisco Baptista Neto', country: 'Angola', organisation: 'Embassy of Angola, Pretoria', role: 'defence_attache', title: 'Defence Attaché — South Africa', influence: 'MEDIUM', appointedDate: '2024-08-15', notes: 'Critical for Paramount Group and other SA OEM coordination. AAD expo contact. Portuguese and English.' },

    // ── Mozambique — Expanded ─────────────────────────────────────────────────
    { name: 'Cristóvão Artur Chume', country: 'Mozambique', organisation: 'Ministério da Defesa Nacional', role: 'minister', title: 'Minister of National Defence', influence: 'CRITICAL', appointedDate: '2024-03-20', notes: 'Counter-insurgency in Cabo Delgado shapes all procurement. Urgent requirement for ISR, vehicles, infantry equipment. Portuguese essential. EU EUTM engagement creates opportunities.' },
    { name: 'Gen. Joaquim Rivas Mangrasse', country: 'Mozambique', organisation: 'Estado-Maior General das FADM', role: 'chief_of_staff', title: 'FADM Chief of General Staff', influence: 'CRITICAL', appointedDate: '2024-07-01', notes: 'Operational commander of Cabo Delgado response. Direct authority over force requirements. Approach through MoD or Portuguese defence cooperation channels.' },
    { name: 'Gen. Mário Elias Nhamitambo', country: 'Mozambique', organisation: 'Exército (FADM)', role: 'army_commander', title: 'Commander of FADM Ground Forces', influence: 'HIGH', appointedDate: '2025-01-15', notes: 'Ground force is priority branch. Needs APCs, MRAPs, night vision, communications. Recently appointed — window active. Counter-insurgency experienced.' },
    { name: 'Rear Adm. Eugénio Dias da Silva', country: 'Mozambique', organisation: 'Marinha de Guerra de Moçambique', role: 'navy_commander', title: 'Commander of Mozambican Navy', influence: 'HIGH', notes: 'LNG facility protection in Cabo Delgado coast. Needs patrol boats, maritime radar. Total/Eni investments drive maritime security funding.' },
    { name: 'Brig. Filipe Mateus Zacarias', country: 'Mozambique', organisation: 'Força Aérea de Moçambique', role: 'air_force_commander', title: 'Commander of Mozambican Air Force', influence: 'HIGH', notes: 'Very limited fleet. Needs transport helos, light ISR aircraft. South African and Brazilian OEMs have legacy relationships.' },
    { name: 'Brig. Armindo Nhabinde', country: 'Mozambique', organisation: 'FADM — Joint Force Cabo Delgado', role: 'task_force_commander', title: 'Commander — Cabo Delgado Joint Operations', influence: 'HIGH', appointedDate: '2024-10-01', notes: 'Field commander in counter-insurgency. Direct user of equipment. Strong influence on urgent operational requirements. SADC and Rwanda force coordination.' },
    { name: 'Col. Sérgio Manuel Tembe', country: 'Mozambique', organisation: 'FADM Procurement Directorate', role: 'procurement_director', title: 'Director of Procurement & Materiel', influence: 'HIGH', notes: 'Manages formal tender processes. Portuguese-language submissions required. EU funding often channels through this directorate for Cabo Delgado equipment.' },
    { name: 'Col. Alberto Mondlane', country: 'Mozambique', organisation: 'Embassy of Mozambique, Lisbon', role: 'defence_attache', title: 'Defence Attaché — Portugal', influence: 'MEDIUM', appointedDate: '2024-12-01', notes: 'Portugal is key defence cooperation partner. Routes introductions to Maputo. Attends Portuguese defence events. New posting — relationship window.' },

    // ── Guinea-Bissau — Expanded ──────────────────────────────────────────────
    { name: 'Dr. Sandji Fati', country: 'Guinea-Bissau', organisation: 'Ministério da Defesa', role: 'minister', title: 'Minister of Defence', influence: 'CRITICAL', appointedDate: '2024-05-10', notes: 'Small defence budget but ECOWAS/UN support creates procurement opportunities. Personal relationship-driven. Portuguese essential. Approach through Lisbon embassy or ECOWAS channel.' },
    { name: 'Gen. Biaguê Na N\'Tam', country: 'Guinea-Bissau', organisation: 'FASB', role: 'chief_of_staff', title: 'Chief of Staff of FASB (Armed Forces)', influence: 'CRITICAL', notes: 'De facto most powerful military figure. FASB is small (~5,000) but politically influential. Historically involved in coups. Build trust slowly. Portuguese only.' },
    { name: 'Col. Ibraima Camará', country: 'Guinea-Bissau', organisation: 'FASB — Army Command', role: 'army_commander', title: 'Army Commander', influence: 'HIGH', notes: 'Ground forces need basic equipment: vehicles, uniforms, communications. Often funded through bilateral aid or ECOWAS grants. Personal approach required.' },
    { name: 'Maj. Domingos Correia', country: 'Guinea-Bissau', organisation: 'FASB — Logistics', role: 'procurement_official', title: 'Head of Logistics & Supply', influence: 'MEDIUM', notes: 'Manages what little formal procurement exists. Small deals but builds relationship for future. Portuguese essential. Bissau-based.' },

    // ── Nigeria — Expanded ────────────────────────────────────────────────────
    { name: 'Alhaji Mohammed Badaru Abubakar', country: 'Nigeria', organisation: 'Federal Ministry of Defence', role: 'minister', title: 'Minister of Defence', influence: 'CRITICAL', appointedDate: '2024-08-21', notes: 'Political appointee. Oversees major procurement programmes. Counter-terrorism (Boko Haram, ISWAP) drives spending. English-speaking. Formal approach via embassy or DSEI meetings.' },
    { name: 'Gen. Christopher Musa', country: 'Nigeria', organisation: 'Nigerian Armed Forces', role: 'chief_of_defence', title: 'Chief of Defence Staff (CDS)', influence: 'CRITICAL', appointedDate: '2024-06-19', notes: 'Most senior military officer. Joint operations authority. Drives requirements for all services. English-speaking. Abuja HQ. Formal engagement protocol.' },
    { name: 'Lt Gen. Olufemi Oluyede', country: 'Nigeria', organisation: 'Nigerian Army', role: 'army_chief', title: 'Chief of Army Staff (COAS)', influence: 'HIGH', appointedDate: '2025-02-28', notes: 'Largest branch. Counter-insurgency equipment priority: MRAPs, UAVs, artillery, night vision. Recently appointed — window open. Victoria Island, Lagos meetings possible.' },
    { name: 'Vice Adm. Emmanuel Ogalla', country: 'Nigeria', organisation: 'Nigerian Navy', role: 'navy_chief', title: 'Chief of Naval Staff (CNS)', influence: 'HIGH', notes: 'Gulf of Guinea maritime security. Patrol vessels, OPVs, coastal radar. Damen, Ocea, and Chinese yards competing. English-speaking.' },
    { name: 'Air Marshal Hasan Abubakar', country: 'Nigeria', organisation: 'Nigerian Air Force', role: 'air_force_chief', title: 'Chief of Air Staff (CAS)', influence: 'HIGH', notes: 'M-346 trainer deal (Leonardo) in progress. A-29 Super Tucano operational. UAV interest strong. English-speaking. Formal engagement.' },
    { name: 'Brig. Gen. Victor Ezugwu', country: 'Nigeria', organisation: 'Defence Industries Corporation of Nigeria', role: 'dicon_director', title: 'Director General — DICON', influence: 'HIGH', appointedDate: '2024-11-15', notes: 'State arms manufacturer. Ammunition, small arms production. Key offset/local content partner for major deals. Kaduna-based. English-speaking.' },

    // ── Kenya — Expanded ──────────────────────────────────────────────────────
    { name: 'Hon. Aden Duale', country: 'Kenya', organisation: 'Ministry of Defence', role: 'cabinet_secretary', title: 'Cabinet Secretary for Defence', influence: 'CRITICAL', appointedDate: '2024-10-05', notes: 'Political appointee. Controls defence budget. AMISOM/ATMIS drawdown creates domestic re-equipping need. English and Swahili. Nairobi. Formal tender process.' },
    { name: 'Gen. Francis Ogolla', country: 'Kenya', organisation: 'Kenya Defence Forces', role: 'kdf_commander', title: 'Commander of KDF', influence: 'CRITICAL', appointedDate: '2024-04-28', notes: 'Top military officer. Modernisation agenda: ISR, border security, helicopter fleet renewal. English-speaking. Engages at international defence expos.' },
    { name: 'Lt Gen. Peter Njiru', country: 'Kenya', organisation: 'Kenya Army', role: 'army_commander', title: 'Commander — Kenya Army', influence: 'HIGH', notes: 'Ground force modernisation: APCs, IFVs, counter-IED equipment. Border security with Somalia drives requirements. English and Swahili.' },
    { name: 'Col. James Mwangi Kariuki', country: 'Kenya', organisation: 'KDF Defence Procurement', role: 'procurement_director', title: 'Director of Defence Procurement', influence: 'HIGH', notes: 'Manages formal tender process. Competitive bidding. English-speaking. Technical evaluation focus. Nairobi — Ulinzi House.' },

    // ── Key OEM contacts — Expanded ───────────────────────────────────────────
    { name: 'Paramount Group', country: 'South Africa', organisation: 'Paramount Group', role: 'oem', title: 'Protected vehicle manufacturer — strong Africa presence', influence: 'HIGH', notes: 'Mbombe, Marauder, Matador. Non-ITAR. Direct sales to Nigeria, Kenya, Mozambique. Contact: Sales Director Africa.' },
    { name: 'Johan van der Merwe', country: 'South Africa', organisation: 'Paramount Group', role: 'oem_sales', title: 'Regional Sales Director — Sub-Saharan Africa', influence: 'HIGH', notes: 'Direct contact for Mbombe/Marauder enquiries. Attends AAD, ShieldAfrica. Johannesburg-based. English and Afrikaans. Responsive to broker introductions.' },
    { name: 'Baykar', country: 'Turkey', organisation: 'Baykar Defence', role: 'oem', title: 'UAV manufacturer — TB2, TB3, Akinci', influence: 'HIGH', notes: 'Aggressive Africa expansion. Sales to Nigeria, Ethiopia, Angola, Morocco. Contact via Turkish Defence Attaché or direct Sales.' },
    { name: 'Damen Shipyards', country: 'Netherlands', organisation: 'Damen', role: 'oem', title: 'Naval vessel builder — patrol boats, coast guard', influence: 'HIGH', notes: 'Active across African coast guards. Non-ITAR. Good financing packages. Contact: Sales Manager Africa.' },
    { name: 'Embraer Defence', country: 'Brazil', organisation: 'Embraer', role: 'oem', title: 'Super Tucano, KC-390 — Lusophone advantage', influence: 'HIGH', notes: 'Portuguese-speaking sales team. Strong Angola/Mozambique relationships. A-29 Super Tucano proven in counter-insurgency.' },
    { name: 'Leonardo', country: 'Italy', organisation: 'Leonardo S.p.A.', role: 'oem', title: 'Helicopters, trainers, naval systems — strong Nigeria presence', influence: 'HIGH', notes: '24 M-346 to Nigeria (2026). AW109/AW139 helicopters across Africa. Non-ITAR. Contact: VP International Sales.' },
    { name: 'Nammo', country: 'Norway', organisation: 'Nammo AS', role: 'oem', title: 'NATO ammunition supplier — all calibres', influence: 'HIGH', notes: 'All NATO standard ammunition. Non-ITAR (Norwegian production). 30mm, 155mm, shoulder-fired systems (M72). Contact: Sales Director.' },
    { name: 'Rheinmetall', country: 'Germany', organisation: 'Rheinmetall AG', role: 'oem', title: 'Vehicles, ammunition, air defence — premium quality', influence: 'HIGH', notes: 'Lynx IFV, Fuchs APC, all calibre ammunition. German export restrictions can limit reach. Quality premium. Contact: International Sales.' },
    { name: 'Yael Ben-Ari', country: 'Israel', organisation: 'Elbit Systems', role: 'oem_sales', title: 'VP Business Development — Africa', influence: 'HIGH', notes: 'Hermes UAVs, Sabrah light tanks, EW systems, border security. Active in Angola, Kenya, Nigeria. Non-ITAR Israeli systems. Attends Africa Aerospace & Defence.' },
    { name: 'Cdr. Mehmet Kaya (ret.)', country: 'Turkey', organisation: 'Turkish Embassy Network', role: 'defence_attache', title: 'Defence Cooperation Attaché — West Africa Circuit', influence: 'MEDIUM', notes: 'Coordinates Turkish defence exports across Lusophone Africa. Bayraktar, FNSS vehicles, patrol boats. Ankara-directed but locally engaged. Key for Turkish OEM introductions.' },
    { name: 'Col. Mustafa Demir (ret.)', country: 'Turkey', organisation: 'Turkish Embassy, Luanda', role: 'defence_attache', title: 'Defence Attaché — Angola', influence: 'MEDIUM', appointedDate: '2025-01-20', notes: 'Turkey actively courting Angola with TB2 sales pitch. Facilitates Baykar/FNSS/Aselsan introductions. New posting — good window to build rapport.' },
    { name: 'Cel. Marco Aurélio da Costa', country: 'Brazil', organisation: 'Agência Brasileira de Cooperação (ABC)', role: 'defence_cooperation', title: 'Defence Cooperation Coordinator — Lusophone Africa', influence: 'MEDIUM', notes: 'Manages Brazil-Lusophone Africa defence cooperation framework. Embraer/Taurus/Avibras introductions. Portuguese-speaking. CPLP defence forum participant.' },
    { name: 'Rodrigo Ferreira Campos', country: 'Brazil', organisation: 'Embraer Defence & Security', role: 'oem_sales', title: 'Regional Director — Africa & CPLP Markets', influence: 'HIGH', notes: 'Direct Embraer contact for A-29 Super Tucano and KC-390 in Lusophone markets. Portuguese native. Strong Angola/Mozambique relationships. Attends FIDAE and ShieldAfrica.' },
  ];

  for (const s of seeds) db.contacts.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2,6), ...s, createdAt: new Date().toISOString(), interactions: [] });
  saveDB(db);
}
