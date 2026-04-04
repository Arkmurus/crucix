// lib/aria/seed_knowledge.mjs
// Seeds ARIA's knowledge base with core defence industry facts
// Run once on first startup (checks if already seeded)

import { storeFact } from './knowledge.mjs';

const SEED_FACTS = [
  // Ammunition calibres & platforms
  ['30x113mm ammunition', 'Used in M230 Chain Gun (AH-64 Apache) and GAU-8/A Avenger (A-10). Primary manufacturers: General Dynamics OTS (USA), Northrop Grumman (USA), Nammo (Norway). ITAR-controlled. Variants: M789 HEDP, M799 HEI, PGU-13/B API.', 'CONFIRMED'],
  ['30x173mm ammunition', 'NATO standard for Mk44 Bushmaster II and GAU-22/A (F-35). Manufacturers: Nammo, Rheinmetall, General Dynamics, Northrop Grumman. Used in CV90, Stryker Dragoon, VBCI.', 'CONFIRMED'],
  ['12.7x99mm (.50 BMG)', 'NATO standard heavy machine gun round. Used in M2 Browning, M85. Manufacturers: FN Herstal, General Dynamics, Nammo, PMP (South Africa), CBC (Brazil).', 'CONFIRMED'],
  ['5.56x45mm NATO', 'Standard NATO rifle cartridge. Used in M4, HK416, FN SCAR, Galil. Produced by virtually all NATO ammunition manufacturers. Export generally unrestricted for allied nations.', 'CONFIRMED'],
  ['7.62x51mm NATO', 'Standard NATO medium machine gun/marksman cartridge. Used in FN MAG, M240, HK G3. Widely produced and exported.', 'CONFIRMED'],
  ['155mm artillery', 'NATO standard howitzer calibre. Key platforms: M777, PzH 2000, CAESAR, K9 Thunder, AS90. Manufacturers: Nammo, Rheinmetall, Nexter, General Dynamics, Elbit. Critical supply shortage globally since 2022.', 'CONFIRMED'],

  // Key OEMs and their African export track record
  ['Paramount Group', 'South African defence company. Products: Mbombe APC, Marauder, Matador. Export markets: Nigeria, Kenya, Mozambique, UAE, Jordan, Kazakhstan. Strong Africa presence, non-ITAR.', 'CONFIRMED'],
  ['Turkish Aerospace (TUSAS)', 'Turkish state defence company. Products: Hürjet trainer, Anka UAV, Atak helicopter. Active exports to: Nigeria, Pakistan, UAE. Aggressive pricing vs Western competitors.', 'CONFIRMED'],
  ['Baykar', 'Turkish drone manufacturer. Products: Bayraktar TB2, TB3, Akinci. Export markets: Ukraine, Nigeria, Ethiopia, Angola, Morocco, Pakistan, UAE. Game-changer in African drone procurement.', 'CONFIRMED'],
  ['Leonardo', 'Italian defence conglomerate. Products: M-346 trainer, AW109/AW139 helicopters, naval systems. 2026 deal: 24 M-346 to Nigeria (largest West African military aircraft acquisition).', 'CONFIRMED'],
  ['Embraer', 'Brazilian aerospace. Products: Super Tucano (A-29), KC-390. Export markets: Nigeria (24 Super Tucano), Angola, Mozambique, Portugal. Lusophone advantage.', 'CONFIRMED'],
  ['Damen', 'Dutch shipbuilder. Products: patrol vessels, corvettes, coast guard vessels. Active across African coast guards (Nigeria, Senegal, Mozambique). Non-ITAR.', 'CONFIRMED'],
  ['Elbit Systems', 'Israeli defence. Products: Hermes UAVs, surveillance systems, Sabrah light tanks. Active in Africa: Cameroon, Ethiopia, various. Export compliance varies by destination.', 'CONFIRMED'],
  ['Rheinmetall', 'German defence. Products: Lynx IFV, Fuchs APC, ammunition (30mm, 35mm, 120mm, 155mm), air defence. Active exports to SADC nations.', 'CONFIRMED'],
  ['Denel', 'South African state defence. Products: Rooivalk helicopter, ammunition (all calibres), Umkhonto SAM. Financial difficulties but deep African market knowledge.', 'CONFIRMED'],
  ['Nammo', 'Norwegian-Finnish ammunition manufacturer. Products: all NATO calibres including 30x113mm, 30x173mm, 155mm, shoulder-fired weapons (M72). Key NATO supplier.', 'CONFIRMED'],

  // Key markets — military structure
  ['Angola FAA', 'Forças Armadas Angolanas (Angolan Armed Forces). Branches: Army, Navy (Marinha de Guerra), Air Force (Força Aérea Nacional). Equipment: mix of Soviet-era and modern Western. Modernisation programme active. Key procurement authority: Ministério da Defesa Nacional.', 'CONFIRMED'],
  ['Mozambique FADM', 'Forças Armadas de Defesa de Moçambique. Active counter-insurgency in Cabo Delgado province (since 2017). Equipment needs: patrol vessels, ISR, infantry equipment. SADC and EU training missions active.', 'CONFIRMED'],
  ['Nigeria Armed Forces', 'Largest military in West Africa. Active counter-terrorism (Boko Haram/ISWAP in NE), counter-banditry (NW). Major procurement: Super Tucano (Brazil/USA), M-346 (Italy), patrol vessels (Damen). Key authority: Ministry of Defence, Abuja.', 'CONFIRMED'],
  ['Kenya Defence Forces', 'East African regional hub. Active in AMISOM/ATMIS (Somalia). Equipment: F-5 (retiring), helicopters (MD530, AH-1), armoured vehicles. Modernisation focus on ISR and border security.', 'CONFIRMED'],

  // Export control frameworks
  ['UK export controls', 'Managed by ECJU (Export Control Joint Unit). Licence types: SIEL (Standard Individual), OIEL (Open Individual), OGEL (Open General). Assessment criteria: Consolidated Criteria (human rights, regional stability, diversion risk). Processing time: 20-60 working days.', 'CONFIRMED'],
  ['ITAR overview', 'International Traffic in Arms Regulations (US). Covers USML (United States Munitions List) items. Requires DSP-5 license for export. Key risk: any product with >de minimis US content triggers ITAR. Managed by DDTC (Directorate of Defense Trade Controls).', 'CONFIRMED'],
  ['EU dual-use regulation', 'Regulation 2021/821. Covers items in Annex I (based on Wassenaar, MTCR, NSG, Australia Group). Catch-all provision (Art. 4) for non-listed items. Member states issue licences independently.', 'CONFIRMED'],

  // Arkmurus positioning
  ['Arkmurus business model', 'Defence brokering and advisory firm. Core competency: Lusophone Africa (Angola, Mozambique, Guinea-Bissau, Cape Verde, São Tomé, Brazil). Acts as intermediary between OEMs and end-user armed forces/MoDs. UK-based, export control compliant (UK/EU/US regulations). Products: armoured vehicles, UAVs, border security, ammunition, training, logistics.', 'CONFIRMED'],

  // Pricing benchmarks
  ['Armoured vehicles pricing', 'Protected vehicles (Paramount Mbombe/Marauder class): $500K-$1.2M per unit. 50-100 unit order: $80M-$120M total. Turkish alternatives (Otokar Cobra/FNSS Pars): 20-30% cheaper. Chinese (Norinco VN-1): 40-50% cheaper but less capable. Always factor 15-20% for spares package and training.', 'ASSESSED'],
  ['UAV system pricing', 'Bayraktar TB2 system (6 aircraft + GCS + logistics): ~$70M. Full programme with training: $80-120M. Chinese Wing Loong II: ~$1-2M per unit (significantly cheaper). Israeli Hermes 900: ~$10M per system. Training programme typically $5-15M additional.', 'ASSESSED'],
  ['Ammunition pricing', 'Small arms ammunition (5.56mm NATO): $0.30-0.50/round bulk. 12.7mm: $3-5/round. 30x113mm: $40-80/round depending on variant. 155mm: $3,000-8,000/round (precision guided up to $100K). Annual supply contracts typically $20-50M for medium military.', 'ASSESSED'],
  ['Patrol vessel pricing', '30-40m patrol vessel: $15-25M. 50-60m OPV: $40-80M. Fleet of 4 vessels + training + logistics: $150-250M. Damen builds in 18-24 months. Turkish alternatives (Dearsan, ADIK): 20-30% cheaper.', 'ASSESSED'],
  ['Training programme pricing', 'Infantry training (battalion, 6 months): $5-10M. Pilot training (12 students, fixed-wing): $15-25M. Counter-terrorism/special forces: $3-8M per package. Includes instructors, curriculum, equipment, consumables.', 'ASSESSED'],

  // Approach strategies by region
  ['Africa procurement approach', 'Key principles: (1) Relationships before contracts — invest in MoD engagement 12-18 months before tender. (2) Local partners are essential — identify a reputable local agent/representative. (3) Language matters — Lusophone markets strongly prefer Portuguese-speaking interlocutors. (4) Offset obligations are common — be prepared to propose technology transfer, local assembly, or training. (5) Payment terms — African procurement often uses letter of credit or deferred payment; factor financing into pricing.', 'CONFIRMED'],
  ['FMS process', 'US Foreign Military Sales: (1) Buyer submits Letter of Request (LOR) to US DSCA. (2) DSCA prepares Letter of Offer and Acceptance (LOA). (3) Congressional notification required for deals >$25M (major defence equipment). (4) Timeline: 6-18 months from LOR to contract. (5) Arkmurus role: advisory on LOR preparation, platform selection, integration planning. Cannot broker ITAR items directly without US export partner.', 'CONFIRMED'],
  ['Direct commercial sales approach', 'For non-ITAR equipment via DCS: (1) Identify requirement through MoD engagement or tender notice. (2) Match OEM product to requirement. (3) Obtain end-user certificate from buyer government. (4) Apply for export licence (UK SIEL, 20-60 working days). (5) Negotiate commercial terms (price, delivery, training, spares). (6) Arkmurus fee: typically 5-12% commission depending on deal size and complexity.', 'CONFIRMED'],

  // Geopolitical drivers
  ['Sahel instability', 'The Sahel region (Mali, Burkina Faso, Niger, Chad) has seen military coups since 2020, expulsion of French forces, and growing Russian Wagner/Africa Corps presence. Creates procurement opportunities: (1) Neighbouring countries (Nigeria, Ghana, Senegal, Côte d\'Ivoire) are bolstering border defences. (2) ECOWAS standby force needs equipment. (3) Russian replacement of French equipment opens new supplier windows.', 'CONFIRMED'],
  ['Indo-Pacific rearmament', 'Philippines, Indonesia, Vietnam, India are all in major military modernisation programmes driven by South China Sea tensions. Philippines Horizon 3 programme: $5B+ through 2028. Indonesia MEF (Minimum Essential Force): frigates, fighters, submarines. Vietnam: diversifying away from Russian equipment (opportunity for Western/Israeli alternatives).', 'CONFIRMED'],
  ['Post-Ukraine equipment replacement', 'European NATO countries are replenishing stocks donated to Ukraine. Poland: $30B+ programme (K2 tanks, FA-50 fighters, HIMARS). Romania, Bulgaria, Greece upgrading Soviet-era equipment to NATO standard. Creates secondary market for replaced equipment (older but functional) suitable for African buyers.', 'CONFIRMED'],

  // Defence exhibitions
  ['Key defence exhibitions', 'DSEI (London, Sep): Largest European defence show, Arkmurus home base. IDEX (Abu Dhabi, Feb): Middle East focus, major African buyer attendance. AAD (Pretoria, Sep, biennial): Africa\'s premier defence show. ShieldAfrica (Abidjan, Jan, biennial): West Africa focus. EUROSATORY (Paris, Jun, biennial): Land and airland defence. FIDAE (Santiago, Apr, biennial): Latin America. DSA (Kuala Lumpur, Apr, biennial): Southeast Asia.', 'CONFIRMED'],
];

let _seeded = false;

export function seedKnowledgeBase() {
  if (_seeded) return;
  _seeded = true;
  let count = 0;
  for (const [topic, content, confidence] of SEED_FACTS) {
    storeFact(topic, content, 'seed', confidence);
    count++;
  }
  console.log(`[ARIA KB] Seeded ${count} core defence industry facts`);
}
