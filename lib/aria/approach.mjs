// lib/aria/approach.mjs
// Module 9: Approach Strategy Generator
//
// Transforms a raw lead into an actionable package:
// - Draft opening message (tailored to market/language/culture)
// - Named contacts from contact DB
// - Ranked OEM list with export compliance status
// - Compliance checklist
// - Competitive positioning
//
// A lead with this package can be acted on the SAME AFTERNOON.
// Without it, 3 days of manual research before anyone picks up the phone.

import { getContactsByCountry, searchContacts } from './contacts.mjs';
import { searchKnowledge } from './knowledge.mjs';
import { queryLedger } from './intel_ledger.mjs';

// ── Market profiles ──────────────────────────────────────────────────────────
const MARKET_PROFILES = {
  'Angola':         { language: 'Portuguese', formality: 'HIGH', greeting: 'Exmo. Senhor', timezone: 'WAT (UTC+1)', currency: 'AOA/USD', approach: 'Formal letter via Defence Attaché or direct MoD. Portuguese essential. Reference CPLP framework. Personal meeting critical before commercial discussion.' },
  'Mozambique':     { language: 'Portuguese', formality: 'HIGH', greeting: 'Exmo. Senhor', timezone: 'CAT (UTC+2)', currency: 'MZN/USD', approach: 'Reference SADC framework and EU/SAMIM cooperation. Cabo Delgado situation creates urgency. Portuguese essential.' },
  'Guinea-Bissau':  { language: 'Portuguese', formality: 'MEDIUM', greeting: 'Exmo. Senhor', timezone: 'GMT', currency: 'XOF', approach: 'Small military — personal relationships paramount. ECOWAS/CPLP channels. Portuguese Creole awareness helpful.' },
  'Cape Verde':     { language: 'Portuguese', formality: 'MEDIUM', greeting: 'Exmo. Senhor', timezone: 'CVT (UTC-1)', currency: 'CVE', approach: 'Very small armed forces. Maritime security focus. Coast guard more active than military.' },
  'Nigeria':        { language: 'English', formality: 'HIGH', greeting: 'Dear Sir/Madam', timezone: 'WAT (UTC+1)', currency: 'NGN/USD', approach: 'Formal tender process. Local content requirements (DICON partnership). Multiple competing services (Army, Navy, Air Force) — each procures independently.' },
  'Kenya':          { language: 'English', formality: 'MEDIUM', greeting: 'Dear Sir/Madam', timezone: 'EAT (UTC+3)', currency: 'KES/USD', approach: 'Professional, competitive tender process. Reference AMISOM/ATMIS contribution. UK defence relationship historically strong.' },
  'Brazil':         { language: 'Portuguese', formality: 'HIGH', greeting: 'Exmo. Senhor', timezone: 'BRT (UTC-3)', currency: 'BRL/USD', approach: 'Large domestic industry (Embraer, Taurus, CBC). Offset/local content essential. Long procurement cycles. Portuguese native.' },
  'Indonesia':      { language: 'English/Indonesian', formality: 'HIGH', greeting: 'Yang Terhormat', timezone: 'WIB (UTC+7)', currency: 'IDR/USD', approach: 'MEF programme active. Offset essential. Korean/Turkish competitors strong. Formal MoD engagement.' },
  'Philippines':    { language: 'English', formality: 'MEDIUM', greeting: 'Dear Sir/Madam', timezone: 'PHT (UTC+8)', currency: 'PHP/USD', approach: 'Horizon 3 modernisation active. US FMS dominant but diversifying. English-speaking, relatively open process.' },
  'UAE':            { language: 'English/Arabic', formality: 'HIGH', greeting: 'Your Excellency', timezone: 'GST (UTC+4)', currency: 'AED/USD', approach: 'Wealthy buyer, sophisticated procurement. EDGE Group is local champion. Agent/representative essential. IDEX is key networking event.' },
  'Saudi Arabia':   { language: 'English/Arabic', formality: 'VERY HIGH', greeting: 'Your Royal Highness / Your Excellency', timezone: 'AST (UTC+3)', currency: 'SAR/USD', approach: 'Vision 2030 localisation. GAMI is procurement authority. Agent mandatory. Very long cycles (2-5 years).' },
};

// ── OEM ranking by product category ──────────────────────────────────────────
const OEM_BY_PRODUCT = {
  'armoured_vehicles': [
    { oem: 'Paramount Group', country: 'ZA', itar: false, price: 'MEDIUM', africa: 'STRONG', products: 'Mbombe, Marauder, Matador' },
    { oem: 'Otokar', country: 'TR', itar: false, price: 'LOW-MEDIUM', africa: 'GROWING', products: 'Cobra II, Arma 8x8' },
    { oem: 'FNSS', country: 'TR', itar: false, price: 'MEDIUM', africa: 'GROWING', products: 'Pars, Kaplan' },
    { oem: 'Rheinmetall', country: 'DE', itar: false, price: 'HIGH', africa: 'MODERATE', products: 'Lynx, Fuchs, Boxer' },
    { oem: 'Norinco', country: 'CN', itar: false, price: 'LOW', africa: 'STRONG', products: 'VN-1, VP-11, WMA301' },
  ],
  'uav_systems': [
    { oem: 'Baykar', country: 'TR', itar: false, price: 'MEDIUM', africa: 'STRONG', products: 'TB2, TB3, Akinci' },
    { oem: 'Turkish Aerospace (TUSAS)', country: 'TR', itar: false, price: 'MEDIUM', africa: 'GROWING', products: 'Anka, Aksungur' },
    { oem: 'Elbit Systems', country: 'IL', itar: false, price: 'HIGH', africa: 'MODERATE', products: 'Hermes 450/900, Skylark' },
    { oem: 'AVIC/CASC', country: 'CN', itar: false, price: 'LOW', africa: 'STRONG', products: 'Wing Loong I/II, CH-4/5' },
    { oem: 'General Atomics', country: 'US', itar: true, price: 'VERY HIGH', africa: 'LIMITED', products: 'MQ-9 Reaper, Gray Eagle' },
  ],
  'patrol_vessels': [
    { oem: 'Damen', country: 'NL', itar: false, price: 'MEDIUM', africa: 'STRONG', products: 'Stan Patrol, OPV 1800/2400' },
    { oem: 'Fincantieri', country: 'IT', itar: false, price: 'HIGH', africa: 'MODERATE', products: 'Corvettes, patrol boats' },
    { oem: 'Dearsan', country: 'TR', itar: false, price: 'LOW-MEDIUM', africa: 'GROWING', products: 'Patrol vessels, landing craft' },
    { oem: 'OCEA', country: 'FR', itar: false, price: 'MEDIUM', africa: 'STRONG', products: 'FPB, OPV — strong Francophone Africa' },
  ],
  'ammunition': [
    { oem: 'Nammo', country: 'NO', itar: false, price: 'MEDIUM', africa: 'MODERATE', products: 'All NATO calibres, 30mm, 155mm' },
    { oem: 'Rheinmetall', country: 'DE', itar: false, price: 'MEDIUM-HIGH', africa: 'MODERATE', products: '30mm, 35mm, 120mm, 155mm' },
    { oem: 'General Dynamics OTS', country: 'US', itar: true, price: 'HIGH', africa: 'LIMITED', products: 'All US mil-spec ammunition' },
    { oem: 'PMP (Denel)', country: 'ZA', itar: false, price: 'LOW-MEDIUM', africa: 'STRONG', products: 'Small arms, 12.7mm, 20mm' },
    { oem: 'CBC', country: 'BR', itar: false, price: 'LOW', africa: 'MODERATE', products: 'Small arms ammunition, Lusophone advantage' },
  ],
  'helicopters': [
    { oem: 'Airbus Helicopters', country: 'FR', itar: false, price: 'HIGH', africa: 'STRONG', products: 'H145M, H225M, Tiger' },
    { oem: 'Leonardo', country: 'IT', itar: false, price: 'HIGH', africa: 'STRONG', products: 'AW109, AW139, AW149' },
    { oem: 'Turkish Aerospace', country: 'TR', itar: false, price: 'MEDIUM', africa: 'GROWING', products: 'T129 Atak, T625 Gökbey' },
    { oem: 'Russian Helicopters', country: 'RU', itar: false, price: 'LOW', africa: 'LEGACY', products: 'Mi-17, Mi-35 — sanctioned, replacement opportunity' },
  ],
  'radar_air_defence': [
    { oem: 'Thales', country: 'FR', itar: false, price: 'HIGH', africa: 'STRONG', products: 'Ground Master, SHORAD' },
    { oem: 'SAAB', country: 'SE', itar: false, price: 'HIGH', africa: 'MODERATE', products: 'Giraffe, RBS-70' },
    { oem: 'Aselsan', country: 'TR', itar: false, price: 'MEDIUM', africa: 'GROWING', products: 'Korkut, Hisar, radar systems' },
    { oem: 'Rafael', country: 'IL', itar: false, price: 'VERY HIGH', africa: 'LIMITED', products: 'Iron Dome, David\'s Sling, Spyder' },
  ],
  'training': [
    { oem: 'BAE Systems', country: 'UK', itar: false, price: 'HIGH', africa: 'STRONG', products: 'Training academies, simulation' },
    { oem: 'L3Harris', country: 'US', itar: true, price: 'HIGH', africa: 'MODERATE', products: 'Flight simulation, ISR training' },
    { oem: 'Saab', country: 'SE', itar: false, price: 'MEDIUM-HIGH', africa: 'MODERATE', products: 'Tactical training, MILES' },
  ],
};

// ── Generate approach strategy ───────────────────────────────────────────────

/**
 * Generate a complete approach strategy for a lead.
 * Returns structured data that ARIA can present as actionable intelligence.
 */
export function generateApproach(market, product, context) {
  const profile = MARKET_PROFILES[market] || MARKET_PROFILES['Nigeria']; // default
  const contacts = getContactsByCountry(market);
  const productKey = mapProductToCategory(product);
  const oems = OEM_BY_PRODUCT[productKey] || [];

  // Filter OEMs by export feasibility
  const rankedOEMs = oems.map(o => ({
    ...o,
    exportRisk: o.itar ? 'HIGH — requires US DSP-5 licence' : 'LOW — non-ITAR, standard export licence',
    lusophoneAdvantage: ['BR', 'PT'].includes(o.country) && ['Portuguese'].includes(profile.language),
    africaFit: o.africa === 'STRONG' ? 3 : o.africa === 'GROWING' ? 2 : o.africa === 'MODERATE' ? 1 : 0,
  })).sort((a, b) => {
    // Prefer: non-ITAR > strong Africa presence > Lusophone advantage > lower price
    if (a.itar !== b.itar) return a.itar ? 1 : -1;
    if (a.africaFit !== b.africaFit) return b.africaFit - a.africaFit;
    return 0;
  });

  // Compliance checklist
  const compliance = [];
  compliance.push(profile.language === 'Portuguese' ? 'Lusophone market — Portuguese documentation required' : 'English documentation acceptable');
  if (oems.some(o => o.itar)) compliance.push('WARNING: Some OEM options are ITAR-controlled — verify US content before proceeding');
  compliance.push('End-user certificate required from ' + market + ' MoD');
  compliance.push('UK SIEL export licence application (20-60 working days)');
  compliance.push('Check UN Security Council embargo status for ' + market);
  compliance.push('Verify entity not on OFAC SDN, EU consolidated, or UK sanctions list');

  // Draft approach message
  const draftMessage = generateDraftMessage(market, product, profile, contacts);

  return {
    market,
    product: productKey,
    profile,
    contacts: contacts.slice(0, 5),
    rankedOEMs: rankedOEMs.slice(0, 4),
    compliance,
    draftMessage,
    timing: profile.approach,
    estimatedCycle: market === 'Saudi Arabia' ? '2-5 years' : market === 'UAE' ? '6-18 months' : '3-12 months',
  };
}

function generateDraftMessage(market, product, profile, contacts) {
  const authority = contacts.find(c => c.role === 'procurement_authority');
  const authorityName = authority ? authority.organisation : 'Ministry of Defence';

  if (profile.language === 'Portuguese') {
    return `Exmo. Senhor,\n\nTenho a honra de me dirigir a V. Exa. em nome da Arkmurus, empresa britânica especializada em assessoria de defesa e intermediação de equipamentos militares.\n\nTomamos conhecimento do interesse do ${authorityName} em ${product} e gostaríamos de apresentar soluções de fabricantes europeus e sul-africanos de referência, em total conformidade com as regulamentações internacionais de controlo de exportação.\n\nSolicito a oportunidade de apresentar as nossas credenciais e discutir como podemos apoiar os requisitos operacionais das Forças Armadas.\n\nAguardo a resposta de V. Exa.\nCom os melhores cumprimentos,\n[Nome]\nArkmurus Defence Advisory`;
  }

  return `Dear Sir/Madam,\n\nI write on behalf of Arkmurus, a UK-based defence advisory and brokering firm specialising in equipment procurement for armed forces across Africa and emerging markets.\n\nWe understand that the ${authorityName} of ${market} has requirements for ${product} and would welcome the opportunity to present solutions from leading European, South African, and Asian manufacturers, fully compliant with international export control regulations.\n\nWe would be grateful for the opportunity to present our credentials and discuss how we can support your operational requirements.\n\nYours faithfully,\n[Name]\nArkmurus Defence Advisory`;
}

function mapProductToCategory(product) {
  const p = (product || '').toLowerCase();
  if (p.match(/vehicle|armour|apc|ifv|mrap/)) return 'armoured_vehicles';
  if (p.match(/uav|drone|unmanned/)) return 'uav_systems';
  if (p.match(/patrol|vessel|coast|naval|ship|boat/)) return 'patrol_vessels';
  if (p.match(/ammun|round|calibr|bullet|shell|155|30mm|12\.7/)) return 'ammunition';
  if (p.match(/helicopter|rotorcraft|helo/)) return 'helicopters';
  if (p.match(/radar|air def|sam |shorad|manpad/)) return 'radar_air_defence';
  if (p.match(/train|simul|academy/)) return 'training';
  return 'armoured_vehicles'; // default
}

/**
 * Build approach context for ARIA prompt injection.
 */
export function getApproachContext(query) {
  const q = (query || '').toLowerCase();

  // Only inject if query relates to approaching/contacting/selling
  const triggers = ['approach', 'contact', 'how to', 'sell to', 'pitch', 'proposal', 'offer', 'engage', 'reach out', 'opening', 'introduce'];
  if (!triggers.some(t => q.includes(t))) return '';

  // Try to extract market and product from query
  const markets = Object.keys(MARKET_PROFILES);
  const market = markets.find(m => q.includes(m.toLowerCase()));
  if (!market) return '';

  const products = Object.keys(OEM_BY_PRODUCT);
  const product = products.find(p => q.includes(p.replace(/_/g, ' '))) || '';

  const strategy = generateApproach(market, product, q);

  return '\n\n[APPROACH STRATEGY — auto-generated for ' + market + ']\n' +
    'Language: ' + strategy.profile.language + ' | Formality: ' + strategy.profile.formality + '\n' +
    'Greeting: ' + strategy.profile.greeting + '\n' +
    'Timezone: ' + strategy.profile.timezone + '\n' +
    'Approach: ' + strategy.profile.approach + '\n' +
    'Estimated cycle: ' + strategy.estimatedCycle + '\n' +
    (strategy.contacts.length ? 'Key contacts: ' + strategy.contacts.map(c => c.name + ' (' + c.title + ')').join('; ') + '\n' : '') +
    (strategy.rankedOEMs.length ? 'Ranked OEMs: ' + strategy.rankedOEMs.map(o => o.oem + ' [' + o.country + '] ' + o.price + ' ' + (o.itar ? 'ITAR' : 'non-ITAR')).join('; ') + '\n' : '') +
    'Compliance: ' + strategy.compliance.join(' | ');
}
