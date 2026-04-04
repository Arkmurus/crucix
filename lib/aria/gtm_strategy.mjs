// lib/aria/gtm_strategy.mjs
// Go-To-Market Strategy Engine for Arkmurus
//
// Generates market entry strategies based on relationship tier,
// product-market fit, competitive landscape, and partnership angles.
// Injected into ARIA context when market entry discussions arise.

import { searchKnowledge } from './knowledge.mjs';
import { getContactsByCountry } from './contacts.mjs';

// ── Regional GTM Playbooks ───────────────────────────────────────────────────
const GTM_PLAYBOOKS = {
  // TIER 1: INCUMBENT — Leverage existing relationships
  INCUMBENT: {
    timeToFirstDeal: '1-3 months',
    strategy: 'Direct engagement — leverage existing MoD relationships',
    steps: [
      'Identify specific requirement from latest intel sweep',
      'Match requirement to 2-3 qualified OEMs',
      'Contact MoD procurement directorate directly (Portuguese)',
      'Present capability brief with OEM technical data',
      'Arrange OEM demonstration visit',
      'Submit formal proposal with pricing + training + spares package',
      'Navigate end-user certificate and export licence',
      'Close — typical commission 8-12%',
    ],
    partnerNeeded: false,
    localAgentNeeded: false,
    keyRisk: 'Complacency — competitors are entering our markets. Stay proactive.',
  },
  // TIER 2: ESTABLISHED — Competitive positioning
  ESTABLISHED: {
    timeToFirstDeal: '3-6 months',
    strategy: 'Competitive bid — differentiate on broker value-add',
    steps: [
      'Monitor procurement portals and defence news for tender announcements',
      'Pre-qualify with MoD through existing contacts',
      'Identify which OEMs are already bidding (competitive intelligence)',
      'Position Arkmurus as multi-OEM assembler (package what no single OEM offers)',
      'Submit proposal with compliance + offset management as differentiator',
      'Offer training/sustainment package that OEM direct sales skip',
      'Follow up within 48 hours of every interaction',
    ],
    partnerNeeded: false,
    localAgentNeeded: 'Recommended for sustained presence',
    keyRisk: 'Established competitors have deeper relationships. Win on service quality.',
  },
  // TIER 3: DEVELOPING — Build relationships first
  DEVELOPING: {
    timeToFirstDeal: '6-12 months',
    strategy: 'Relationship-first — invest before expecting returns',
    steps: [
      'Attend regional defence exhibition (DSEI, ShieldAfrica, AAD)',
      'Identify and approach Defence Attaché at UK embassy/high commission',
      'Request introductory meeting with MoD procurement directorate',
      'Present Arkmurus credentials and OEM partnerships',
      'Offer free intelligence briefing on relevant market/product trends',
      'Identify specific near-term procurement opportunity',
      'Partner with an established OEM that needs local representation',
      'Build relationship over 2-3 meetings before commercial discussion',
      'First deal likely small (training, spares) — build trust before major equipment',
    ],
    partnerNeeded: 'OEM partnership essential for credibility',
    localAgentNeeded: 'Essential — cannot operate without local presence',
    keyRisk: 'Investment without guaranteed return. Pick battles carefully.',
  },
  // TIER 4: COLD ENTRY — Partnership is the only way in
  COLD_ENTRY: {
    timeToFirstDeal: '12-24 months',
    strategy: 'Partnership-led — find the right OEM or local firm first',
    steps: [
      'Research: which OEMs are already active in this market?',
      'Approach OEMs that need Africa/Lusophone expertise in exchange for market access',
      'Propose teaming agreement: Arkmurus brings compliance + Africa relationships, OEM brings product',
      'Identify local agent/representative (mandatory in most markets)',
      'Attend the major regional exhibition (IDEX for ME, DSA for SE Asia, EUROSATORY for Europe)',
      'Request meeting with local procurement authority via Defence Attaché',
      'Start with advisory/consultancy role — build credibility before brokering',
      'Target offset/local content opportunity as entry point (high broker value)',
      'First deal may take 18+ months — plan budget accordingly',
    ],
    partnerNeeded: 'Mandatory — without a partner, you cannot compete',
    localAgentNeeded: 'Mandatory',
    keyRisk: 'High investment, uncertain return. Only enter if market opportunity score > 60.',
  },
};

// ── Market-specific entry intelligence ───────────────────────────────────────
const MARKET_ENTRY_INTEL = {
  'Angola': { tier: 'INCUMBENT', exhibition: 'FILDA (Luanda, Jul)', language: 'Portuguese', keyRelationship: 'Defence Attaché + MoD direct', bestOEM: 'Embraer (Lusophone), Paramount (vehicles), Baykar (UAVs)', offset: 'Informal — local content appreciated but not mandated' },
  'Mozambique': { tier: 'INCUMBENT', exhibition: 'FACIM (Maputo, Aug)', language: 'Portuguese', keyRelationship: 'SADC framework + EU SAMIM mission', bestOEM: 'Paramount (vehicles), Damen (naval), Embraer (air)', offset: 'None formal' },
  'Nigeria': { tier: 'ESTABLISHED', exhibition: 'None major — DSEI/ShieldAfrica', language: 'English', keyRelationship: 'MoD + individual service chiefs', bestOEM: 'Leonardo (trainers), Damen (naval), Turkish OEMs (competitive pricing)', offset: 'Local content via DICON partnership' },
  'Kenya': { tier: 'ESTABLISHED', exhibition: 'None major — DSEI', language: 'English', keyRelationship: 'MoD + AMISOM/ATMIS framework', bestOEM: 'Elbit (ISR), Paramount (vehicles), Airbus (helicopters)', offset: 'None formal' },
  'Indonesia': { tier: 'COLD_ENTRY', exhibition: 'Indo Defence (Jakarta, Nov, biennial)', language: 'English/Indonesian', keyRelationship: 'Need Korean OEM partner (Hanwha/KAI already established)', bestOEM: 'Hanwha (vehicles/artillery), KAI (aircraft) — partner, not compete', offset: '35-85% mandatory — broker navigates' },
  'Philippines': { tier: 'COLD_ENTRY', exhibition: 'ADAS (Manila, Sep)', language: 'English', keyRelationship: 'US FMS dominant — position as alternative channel', bestOEM: 'KAI (FA-50 already sold), Elbit (ISR), Damen (naval)', offset: 'None formal' },
  'Saudi Arabia': { tier: 'COLD_ENTRY', exhibition: 'IDEX (Abu Dhabi, Feb) + WDS (Riyadh, Mar)', language: 'Arabic/English', keyRelationship: 'GAMI is gatekeeper — agent mandatory', bestOEM: 'European primes (Leonardo, Airbus) — need premium product for premium market', offset: '50-60% via GAMI — Arkmurus manages offset compliance' },
  'UAE': { tier: 'COLD_ENTRY', exhibition: 'IDEX (Abu Dhabi, Feb)', language: 'Arabic/English', keyRelationship: 'EDGE Group partnership essential', bestOEM: 'Partner with EDGE subsidiary + European OEM', offset: '60% Tawazun — broker value highest here' },
  'Senegal': { tier: 'DEVELOPING', exhibition: 'ShieldAfrica (Abidjan, Jan, biennial)', language: 'French', keyRelationship: 'French Defence Attaché + Ministère des Forces Armées', bestOEM: 'Nexter (French heritage), Turkish OEMs (price competitive)', offset: 'None formal' },
  'Ghana': { tier: 'DEVELOPING', exhibition: 'None major — DSEI', language: 'English', keyRelationship: 'MoD Burma Camp + UK defence relationship', bestOEM: 'Paramount (vehicles), Damen (naval)', offset: 'None formal' },
  'Ethiopia': { tier: 'DEVELOPING', exhibition: 'None — bilateral engagement', language: 'English/Amharic', keyRelationship: 'Post-Tigray reconstruction window — MoD receptive to new suppliers', bestOEM: 'Baykar (already sold TB2), Turkish/Chinese alternatives to Russian legacy fleet', offset: 'None formal' },
  'Poland': { tier: 'COLD_ENTRY', exhibition: 'MSPO (Kielce, Sep)', language: 'Polish/English', keyRelationship: 'Need Korean partner (K2/K9 mega-deals established relationship)', bestOEM: 'Hanwha, Hyundai Rotem — already in market, partner not compete', offset: 'EU/NATO procurement rules' },
  'Brazil': { tier: 'INCUMBENT', exhibition: 'LAAD (Rio, Apr, biennial)', language: 'Portuguese', keyRelationship: 'Deep Lusophone ties + Embraer relationship', bestOEM: 'Embraer (domestic champion), Iveco (Brazilian factory)', offset: '100% for deals >R$50M — broker essential for offset navigation' },
};

/**
 * Generate full GTM strategy for a market.
 */
export function generateGTMStrategy(market) {
  const intel = MARKET_ENTRY_INTEL[market];
  if (!intel) return null;

  const playbook = GTM_PLAYBOOKS[intel.tier];
  const contacts = getContactsByCountry(market);

  return {
    market,
    tier: intel.tier,
    playbook,
    exhibition: intel.exhibition,
    language: intel.language,
    keyRelationship: intel.keyRelationship,
    bestOEM: intel.bestOEM,
    offset: intel.offset,
    contacts: contacts.slice(0, 5),
    timeToFirstDeal: playbook.timeToFirstDeal,
  };
}

/**
 * Context injection for ARIA when GTM discussions arise.
 */
export function getGTMContext(query) {
  const q = (query || '').toLowerCase();
  const triggers = ['go to market', 'market entry', 'enter market', 'how do we get into',
    'strategy for', 'break into', 'expand to', 'start in', 'enter the', 'grow into'];
  if (!triggers.some(t => q.includes(t))) return '';

  const markets = Object.keys(MARKET_ENTRY_INTEL);
  const market = markets.find(m => q.includes(m.toLowerCase()));
  if (!market) return '';

  const strategy = generateGTMStrategy(market);
  if (!strategy) return '';

  return '\n\n[GO-TO-MARKET STRATEGY — ' + market + ' (' + strategy.tier + ')]\n' +
    'Time to first deal: ' + strategy.timeToFirstDeal + '\n' +
    'Strategy: ' + strategy.playbook.strategy + '\n' +
    'Exhibition: ' + strategy.exhibition + '\n' +
    'Language: ' + strategy.language + '\n' +
    'Key relationship: ' + strategy.keyRelationship + '\n' +
    'Best OEMs: ' + strategy.bestOEM + '\n' +
    'Offset: ' + strategy.offset + '\n' +
    'Partner needed: ' + strategy.playbook.partnerNeeded + '\n' +
    'Local agent: ' + strategy.playbook.localAgentNeeded + '\n' +
    'Key risk: ' + strategy.playbook.keyRisk + '\n' +
    'Steps:\n' + strategy.playbook.steps.map((s, i) => (i + 1) + '. ' + s).join('\n');
}
