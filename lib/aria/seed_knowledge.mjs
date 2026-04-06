// lib/aria/seed_knowledge.mjs
// Seeds ARIA's knowledge base with core defence industry facts
// Run once on first startup (checks if already seeded)

import { storeFact } from './knowledge.mjs';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

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

  // Module 3: Budget cycle intelligence by market
  ['Angola defence budget cycle', 'Fiscal year: January-December. OGE (Orçamento Geral do Estado) submitted Q3 previous year. Defence allocation typically 3-4% of GDP. Oil revenue drives budget — Brent above $70 = healthy defence spending. Key procurement windows: Q1-Q2 after budget approval. Supplementary budgets possible mid-year if oil revenue exceeds projections.', 'ASSESSED'],
  ['Mozambique defence budget cycle', 'Fiscal year: January-December. Budget submitted to parliament Q4. Defence spending constrained by Cabo Delgado insurgency costs. EU/SADC contributions supplement national budget. Key procurement: Q1-Q2. Emergency procurement for counter-insurgency can bypass normal cycle.', 'ASSESSED'],
  ['Nigeria defence budget cycle', 'Fiscal year: January-December. Budget typically delayed 2-3 months. Defence gets ~6-8% of total budget. Three service budgets (Army, Navy, Air Force) plus MoD central. Capital vs recurrent split important — equipment falls under capital. Key windows: Q2-Q3 after budget passage.', 'ASSESSED'],
  ['Kenya defence budget cycle', 'Fiscal year: July-June. Budget read June. KDF allocation steady at ~1.2% GDP. AMISOM/ATMIS reimbursements create separate equipment funding. Tender process relatively transparent. Key windows: Q1 (Jul-Sep) after budget approval.', 'ASSESSED'],
  ['Brazil defence budget cycle', 'Fiscal year: January-December. Largest military in Latin America. LBDN (Livro Branco) sets 20-year strategy. PAED (Strategic Programme) drives major procurement. Budget approved December. Key windows: Q1-Q2. Multi-year appropriations for major platforms (Gripen, PROSUB).', 'ASSESSED'],
  ['Indonesia defence budget cycle', 'Fiscal year: January-December. MEF (Minimum Essential Force) programme drives procurement through 2029. Budget approved October. Defence allocation ~0.7% GDP (target 1.5%). Key procurement: Q1-Q3. Offset requirements 35-85% depending on contract value.', 'ASSESSED'],
  ['Philippines defence budget cycle', 'Fiscal year: January-December. Horizon 3 modernisation programme (2023-2028). NDRRMC supplementary funding for disaster response. Budget approved December. Key procurement: Q1-Q2. US FMS significant channel alongside domestic procurement.', 'ASSESSED'],
  ['UAE defence budget cycle', 'Fiscal year: January-December. No public budget constraint — oil-funded sovereign wealth. EDGE Group is procurement/industrial champion. Procurement continuous year-round. IDEX (February) is primary deal-making venue. Offset programme (Tawazun) requires 60% local content.', 'ASSESSED'],

  // ── PLATFORM-TO-EQUIPMENT MAPPING (critical for "if they buy X, they need Y") ──
  ['AH-64 Apache equipment needs', 'Platform: AH-64 Apache attack helicopter. Ammunition: 30x113mm (M789 HEDP, M799 HEI via M230 chain gun), Hellfire missiles (AGM-114), Hydra 70mm rockets. Spare parts: T700-GE-701D engines, TADS/PNVS sensor heads, APU. Training: ~$15M for 12 pilots. Annual sustainment: ~$8-12M per squadron. ITAR-controlled — FMS only.', 'CONFIRMED'],
  ['Super Tucano A-29 equipment needs', 'Platform: Embraer A-29 Super Tucano. Ammunition: 12.7mm (.50 cal), Mk 81/82 bombs, 70mm rockets, AIM-9 Sidewinder optional. Low operating cost (~$1000/flight hour). Users: Nigeria (24), Angola, Mozambique, Afghanistan, Lebanon. Training: integrated in delivery. Lusophone advantage — Brazilian manufacturer, Portuguese documentation.', 'CONFIRMED'],
  ['M-346 Master equipment needs', 'Platform: Leonardo M-346 advanced jet trainer. Ammunition: training pods, optional 12.7mm gun pod, practice bombs. Nigeria ordering 24 units (2026, largest West Africa military aircraft buy). Can operate as light attack. Italian export — non-ITAR. Sustainment: ~$5M/year per squadron.', 'CONFIRMED'],
  ['Bayraktar TB2 equipment needs', 'Platform: Baykar TB2 UCAV. Weapons: MAM-L/MAM-C smart munitions (Roketsan). Ground control station + satellite link. Operational ceiling: 27,000ft, 27hr endurance. Export price: ~$5M per aircraft, full system (6 aircraft + GCS): ~$70M. Users: Ukraine, Nigeria, Ethiopia, Angola, Morocco, UAE. Non-ITAR. Training: 3-6 months.', 'CONFIRMED'],
  ['Lynx IFV equipment needs', 'Platform: Rheinmetall Lynx KF41 IFV. Ammunition: 30x173mm (Lance turret), 7.62mm coax. Crew: 3+8. Weight: 44t. German export — non-ITAR but German export restrictions can apply. Hungary ordered 218 units at ~$5.5M each. Active Iron Fist APS option.', 'CONFIRMED'],
  ['Patrol vessel equipment needs', 'Generic 30-50m patrol vessel. Weapons: 30mm naval cannon (Bushmaster), 12.7mm RWS, small arms. Systems: navigation radar, EO/IR sensor, communications suite. Crew: 15-30. Builders: Damen (NL), OCEA (FR), Dearsan (TR). Typical package: 4 vessels + training + 5yr spares = $100-200M.', 'ASSESSED'],

  // ── COUNTRY MILITARY STRUCTURES (expanded) ──
  ['Ghana Armed Forces', 'GAF: Army, Navy, Air Force. ~15,000 active. Participates in ECOWAS standby force and UN peacekeeping. Equipment ageing, modernisation needed. Key needs: APCs, patrol vessels, small arms. Procurement: Ministry of Defence, Burma Camp Accra. English-speaking, formal tender.', 'CONFIRMED'],
  ['Senegal Armed Forces', 'Forces armées sénégalaises. ~19,000 active. French-trained, strong ECOWAS contributor. Active in Casamance and Sahel. Key needs: vehicles, helicopters, ISR. Procurement: Ministère des Forces Armées, Dakar. FRENCH language essential. Former French colony — Nexter/Airbus historically strong.', 'CONFIRMED'],
  ['Ethiopia National Defense Force', 'ENDF: ~140,000 active (post-Tigray). Major reconstruction after 2020-2022 civil war. Previously Russian-equipped (Su-27, Mi-35). Diversifying to Turkish (Bayraktar) and Chinese. Key needs: everything — vehicles, aircraft, ammunition, training. Budget recovering. Addis Ababa procurement.', 'CONFIRMED'],
  ['Indonesia TNI structure', 'Tentara Nasional Indonesia: Army (TNI-AD), Navy (TNI-AL), Air Force (TNI-AU). ~400,000 active. MEF (Minimum Essential Force) programme through 2029. Major buys: Rafale (France), Scorpène submarines, K2 tanks (South Korea). Offset required: 35-85%. Ministry of Defence Jakarta. Indonesian + English.', 'CONFIRMED'],
  ['Philippines AFP structure', 'Armed Forces of Philippines: Army (PA), Navy (PN), Air Force (PAF). ~150,000 active. Horizon 3 modernisation (2023-2028, $5B+). Needs: frigates, fighters, helicopters, ISR. US FMS dominant but diversifying (South Korea, Israel). Camp Aguinaldo, Quezon City. English-speaking.', 'CONFIRMED'],
  ['Saudi Arabia military structure', 'RSLF + SANG (Saudi Arabian National Guard) + Royal Saudi Air Force. Combined ~250,000. Vision 2030 localisation through GAMI (General Authority for Military Industries). Biggest defence spender in Middle East (~$55B/year). Agent mandatory. Very formal, very slow (2-5 year cycles). Arabic + English. GAMI is procurement gatekeeper.', 'CONFIRMED'],

  // ── END-USER CERTIFICATE WORKFLOWS ──
  ['EUC Angola process', 'End-User Certificate for Angola: (1) Arkmurus drafts EUC template, (2) Submit to Ministério da Defesa Nacional via Defence Attaché or direct MoD contact, (3) MoD reviews and signs (typically signed by Vice-Minister or Director of Equipment), (4) Original posted to Arkmurus UK, (5) Submit with UK SIEL application to ECJU. Timeline: 4-8 weeks for MoD signature. Portuguese language. Include: item description, quantity, end-use statement, non-re-export clause.', 'ASSESSED'],
  ['EUC Nigeria process', 'End-User Certificate for Nigeria: (1) Prepare certificate per UK ECJU template, (2) Submit to Federal Ministry of Defence, Ship House Abuja, (3) Requires Permanent Secretary or Minister signature, (4) Can take 6-12 weeks due to bureaucracy, (5) Expedite via Defence Attaché (British High Commission Abuja). English language. Nigeria requires National Security Adviser (NSA) clearance for major deals.', 'ASSESSED'],
  ['UK SIEL application process', 'Standard Individual Export Licence (SIEL): (1) Register on SPIRE system (spire.trade.gov.uk), (2) Complete F680 pre-application for sensitive destinations, (3) Submit SIEL with: goods description, ECCN classification, end-user details, EUC, (4) ECJU assessment (20-60 working days), (5) May request additional information or conditions. Refusal grounds: Consolidated Criteria (human rights, regional stability, diversion risk). Appeal possible to Export Control Organisation.', 'CONFIRMED'],

  // ── FINANCIAL MODELLING ──
  ['Defence deal economics', 'Typical Arkmurus deal structure: (1) Base equipment cost (OEM price), (2) Training package (5-15% of equipment cost), (3) Spares & maintenance (10-20% initial, then annual), (4) Logistics/shipping (3-8% depending on destination), (5) Offset obligation (0-85% depending on country), (6) Arkmurus commission (5-12% of total deal value). Example: 50 APCs at $800K = $40M base + $4M training + $6M spares + $2M logistics = $52M total. Commission at 8% = $4.16M.', 'ASSESSED'],
  ['FMS financing terms', 'US Foreign Military Sales financing: (1) Cash sales (immediate payment), (2) FMF (Foreign Military Financing — US government grant, mainly for allies like Israel, Egypt, Jordan), (3) Commercial financing via US EXIM Bank (loan guarantee, 7-10 year terms, LIBOR+2-3%), (4) Direct commercial credit from OEM (rare, typically 2-3 year terms). For non-FMF recipients: budget constraints mean concessional financing critical. AfDB/EU Peace Facility can supplement.', 'ASSESSED'],
  ['Offset obligation guide', 'Offset requirements by market: UAE (Tawazun): 60% of contract value. Saudi Arabia (GAMI): 50-60% by 2030. Indonesia: 35-85% depending on contract value (>$10M = 85%). South Africa: 30-50% via DIPA. Brazil: varies, typically 100% for Gripen. Most African markets: 0-20% formal offset, but informal local content expectations (training, assembly, tech transfer). Offset types: direct (related to contract), indirect (investment in other sectors), technology transfer.', 'ASSESSED'],

  // ── SUPPLY CHAIN INTELLIGENCE ──
  ['Defence supply chain lead times', 'Typical lead times from order to delivery: Armoured vehicles: 12-24 months. UAV systems: 8-18 months. Ammunition (bulk): 6-12 months (longer for specialty calibres). Patrol vessels: 18-36 months (custom build). Helicopters: 18-30 months. Radar/air defence: 12-24 months. Training programmes: 3-6 months setup. Small arms: 3-6 months. Critical: ammunition supply chains globally strained since 2022 (Ukraine conflict). 155mm artillery shell backlog: 2-3 years.', 'ASSESSED'],
  ['Key component suppliers', 'Critical defence supply chain nodes: Engines — GE Aviation (US, ITAR), Rolls-Royce (UK), Safran (FR), Honeywell (US, ITAR). Fire control — Elbit (IL), Thales (FR), Leonardo (IT). Ammunition propellant — Nammo (NO), Rheinmetall (DE), General Dynamics (US, ITAR). Optics/EO — FLIR/Teledyne (US, ITAR), Thales (FR), Hensoldt (DE). Communications — L3Harris (US, ITAR), Thales (FR), Elbit (IL). ITAR components create cascading export restrictions on entire platforms.', 'CONFIRMED'],

  // ── MULTI-JURISDICTION COMPLIANCE (global scope) ──
  ['EU Common Position on arms exports', 'EU Council Common Position 2008/944/CFSP defines 8 criteria for arms export decisions. Each EU member state assesses independently. Key criteria: (1) International obligations/embargoes, (2) Human rights, (3) Internal tensions/armed conflict, (4) Regional stability, (5) National security of buyer + allies, (6) Buyer government behaviour, (7) Diversion risk, (8) Economic capacity. German exports most restrictive (Bundessicherheitsrat). French/Italian more permissive. Relevant for any European OEM product.', 'CONFIRMED'],
  ['US State Dept DSP-5 licence', 'Direct Commercial Sales licence for USML items. Required for ANY item on the US Munitions List exported from the US or containing US-origin components. Process: (1) Register with DDTC, (2) Submit DSP-5 application via D-Trade, (3) Congressional notification if >$25M (major equipment), >$7M (defence articles), or >$1M (firearms/ammunition), (4) Processing: 30-60 days typical, can extend to 6+ months for sensitive destinations. De minimis rule: >$0 of US-origin content = ITAR applies.', 'CONFIRMED'],
  ['Brazil offset requirements', 'Brazilian Industrial Compensation Policy (PCI): contracts >R$50M (~$10M) require offset. Typically 100% of contract value for major platforms. Types: co-production, technology transfer, direct procurement from Brazilian industry. Managed by CMID (Comissão Mista da Indústria de Defesa). Recent example: Gripen programme — Saab agreed to full technology transfer to Embraer. Arkmurus angle: leverage Lusophone advantage to navigate Brazilian offset bureaucracy.', 'CONFIRMED'],
  // ── BROKER/JV/PARTNERSHIP STRATEGIES ──
  ['Broker value proposition', 'Arkmurus adds value as broker when: (1) Multi-OEM package needed — no single manufacturer has everything, (2) Offset/local content navigation — complex bureaucracy requires specialist, (3) Language/cultural bridge — Portuguese/French-speaking markets need interlocutor, (4) Compliance complexity — ITAR/dual-use requires export control expertise, (5) Russian replacement — client has Soviet-era fleet, needs migration path to Western alternatives, (6) First-time buyer — new procurement authority needs guidance through process. Commission: 5-12% depending on complexity and risk.', 'CONFIRMED'],
  ['JV and partnership models', 'Partnership strategies for market entry: (1) OEM Representative — represent a specific OEM exclusively in target market (lowest risk, 3-5% commission), (2) Multi-OEM Broker — assemble package from multiple OEMs (medium risk, 5-8%), (3) JV with Local Industry — co-invest in local assembly/maintenance facility (highest commitment, 8-15% of ongoing revenue), (4) Teaming Agreement — partner with established firm for specific bid (shared commission), (5) Offset Partner — manage offset obligations on behalf of OEM (fee-based). Best entry to cold markets: approach established OEM that needs Africa/Lusophone access and offer our relationship network in exchange for their product.', 'CONFIRMED'],
  ['Partnership targeting by region', 'Best partnership angles by region: LUSOPHONE AFRICA: Arkmurus leads, OEMs follow (we are the bridge). WEST AFRICA: Partner with Turkish OEM (Baykar, Otokar) — they have product, we have cultural access. EAST AFRICA: Partner with Israeli firm (Elbit, IAI) — they have surveillance tech, we navigate procurement. SOUTHEAST ASIA: Partner with Korean OEM (Hanwha, KAI) — they win on price/quality, we add compliance expertise. MIDDLE EAST: Partner with European OEM (Leonardo, Airbus) — they have premium product, we manage offset through local JV. EASTERN EUROPE: Partner with South African firm (Paramount) — they have non-ITAR vehicles, we handle EU Common Position compliance.', 'CONFIRMED'],

  ['South Korea defence export growth', 'South Korea emerged as top-5 global arms exporter since 2022. Products: K2 Black Panther tank (Poland, Egypt), K9 Thunder howitzer (Poland, Estonia, Norway, Australia), FA-50 fighter (Poland, Iraq, Philippines), KSS-III submarine (Indonesia). Pricing: 15-25% below Western competitors, proven NATO-grade quality. Strategy: aggressive financing, rapid delivery, technology transfer. Key competitor for Arkmurus in Southeast Asia and Eastern Europe.', 'CONFIRMED'],

  // Competitive positioning insights
  ['Turkish defence export strategy', 'Turkey has become Africa\'s fastest-growing defence supplier since 2019. Strategy: (1) Government-to-government deals backed by presidential visits, (2) 20-30% below Western pricing, (3) Combat-proven systems (TB2 in Libya, Ethiopia), (4) No political conditions attached, (5) Fast delivery timelines. Counter: Emphasise lifecycle cost (Turkish spares expensive long-term), NATO interoperability issues, and Arkmurus advisory value-add.', 'CONFIRMED'],
  ['Chinese defence export strategy', 'China offers 40-50% below Western pricing with state-backed financing (often tied to BRI infrastructure). Products: VN-series APCs, Wing Loong UAVs, JF-17 fighters (with Pakistan). Weakness: After-sales support poor, interoperability limited, quality perception issues. Counter: Emphasise quality, training packages, lifecycle support, and Western interoperability.', 'CONFIRMED'],
  ['Russian equipment replacement opportunity', 'Russia\'s invasion of Ukraine (2022) triggered Western sanctions making spares/ammunition supply impossible for Russian-equipped African militaries. Angola (Su-30, Mi-17), Mozambique (Mi-24), Nigeria (Mi-35), Uganda (Su-30) all affected. This creates replacement windows for Western/Turkish/Israeli alternatives. Key approach: offer trade-in/phase-out programmes with guaranteed support contracts.', 'CONFIRMED'],
];

// ── Offset / local content requirements by country ──
const OFFSET_FACTS = [
  ['Indonesia offset requirements', 'Indonesia requires 35-85% offset depending on contract value. Contracts >$10M require 85% offset. Managed under MEF (Minimum Essential Force) programme. Types: co-production, technology transfer, direct procurement from Indonesian industry.', 'CONFIRMED'],
  ['UAE offset requirements', 'UAE requires 60% offset under the Tawazun Economic Council programme. Applies to all major defence contracts. Offset can be direct (defence-related) or indirect (investment in other UAE sectors). Tawazun manages compliance and verification.', 'CONFIRMED'],
  ['Saudi Arabia offset requirements', 'Saudi Arabia requires 50-60% offset under GAMI (General Authority for Military Industries) localisation targets by 2030. SAMI (Saudi Arabian Military Industries) is the industrial champion. Vision 2030 drives increasing local content mandates.', 'CONFIRMED'],
  ['India offset requirements', 'India requires 30-50% offset under Make in India defence policy. Defence Acquisition Procedure (DAP) mandates local content for contracts above threshold. Categories: Buy (Indian), Buy & Make (Indian), Buy & Make, Buy (Global). DPSUs and private sector both eligible offset partners.', 'CONFIRMED'],
  ['South Korea offset requirements', 'South Korea requires 30-50% offset on major defence imports. DAPA (Defense Acquisition Program Administration) manages offset compliance. Types include technology transfer, co-production, and Korean industry subcontracting. Strong domestic defence industrial base.', 'CONFIRMED'],
  ['Brazil offset requirements', 'Brazil offset managed via PCI (Industrial Compensation Policy). Contracts >R$50M (~$10M) require offset, typically 100% of contract value for major platforms. Managed by CMID (Comissao Mista da Industria de Defesa). EMBRAER partnership model is primary vehicle for aerospace offset.', 'CONFIRMED'],
  ['Turkey offset requirements', 'Turkey requires 25-50% offset under SSB (Presidency of Defence Industries) requirements. Strong push for indigenous production via SSIK (Defence Industry Support Fund). Types: direct industrial participation, technology transfer, co-production. Turkish defence industry increasingly self-sufficient.', 'CONFIRMED'],
];

// ── Global export control regimes ──
const GLOBAL_EXPORT_CONTROLS = [
  ['EU Dual-Use Regulation 2021/821', 'Replaces 428/2009. Controls dual-use including cyber-surveillance. Annex I = EU Control List aligned with Wassenaar/MTCR/NSG/AG. Article 4 catch-all. National authorities can add controls.', 'CONFIRMED'],
  ['EU Common Position 2008/944/CFSP', 'Legally binding on EU states. 8 criteria for arms exports. Denial notifications shared — members consult before approving transfer denied by another. Annual EU Arms Export Report.', 'CONFIRMED'],
  ['France CIEEMG export controls', 'CIEEMG approves all arms exports. 2nd largest exporter. Rafale, CAESAR, Scorpene flagships. Key clients: India, UAE, Saudi, Qatar, Egypt, Greece, Indonesia.', 'CONFIRMED'],
  ['Germany export controls', 'War Weapons Control Act + AWG. Bundessicherheitsrat for sensitive cases. Restrictive policy. Leopard 2 re-export requires German approval. Rheinmetall, ThyssenKrupp, Hensoldt.', 'CONFIRMED'],
  ['Italy Law 185/1990', 'UAMA issues licences. 6th largest exporter. Leonardo (helicopters, electronics), Beretta, Fincantieri. Active Middle East, SE Asia, Latin America.', 'CONFIRMED'],
  ['Spain JIMDDU', 'Law 53/2007. 8th largest exporter. Navantia (naval), Airbus Spain, Indra, Santa Barbara. Key markets: Saudi, Australia, NATO.', 'CONFIRMED'],
  ['Turkey SSB export controls', 'Law 5201. Fastest-growing exporter. TB2/Akinci, KAAN, Aselsan, Roketsan. 25-50% offset. 70+ countries. No ITAR constraints on indigenous — key selling point.', 'CONFIRMED'],
  ['Israel DECA', 'Defence Export Control Law 5766-2007. ~$12.5B annual. IAI, Rafael, Elbit. 20% global UAV market. No published reports. Strong cyber, C4ISR, precision munitions.', 'CONFIRMED'],
  ['South Korea DAPA exports', '9th largest exporter. K2, FA-50, K9, Chunmoo. 30-50% offset. Strategic Trade Act. Markets: Poland, Australia, Norway, Malaysia, Philippines, Egypt.', 'CONFIRMED'],
  ['Japan defence exports', 'Three Principles 2014 relaxed ban. OSP 2023 for grants. ATLA manages. Type 12 SSM to Philippines. Mitsubishi, Kawasaki platforms. Emerging exporter.', 'CONFIRMED'],
  ['Australia defence trade', 'Defence Trade Controls Act 2012. AUKUS trilateral. ITAR waiver. Thales Australia, BAE Australia, Hanwha Defence Australia.', 'CONFIRMED'],
  ['India SCOMET list', 'Controls dual-use. Aims $5B exports under Atmanirbhar. BrahMos, Dhruv, Tejas, Pinaka. DAP 2020 categories. 30-50% offset.', 'CONFIRMED'],
  ['China Export Control Law 2020', 'Comprehensive. MOFCOM licences. No Wassenaar member. Norinco, AVIC, CSSC, CASC. No human rights conditionality. Active Africa, ME, Asia.', 'CONFIRMED'],
  ['South Africa NCACC', 'National Conventional Arms Control Act 2002. Monthly approvals. End-user cert + non-re-export mandatory. Denel, Paramount, Reutech. ARMSCOR procurement.', 'CONFIRMED'],
  ['Brazil CMID exports', 'PCI offset >R$50M = 100%. EMBRAER (C-390, Super Tucano), Iveco Guarani, Avibras ASTROS, Taurus. ~$28B budget.', 'CONFIRMED'],
  ['Canada EIPA', 'ATT Implementation Act 2019. Substantial Risk Test. LAV Saudi controversy led reforms. Global Affairs assesses.', 'CONFIRMED'],
  ['Norway ISP exports', 'Kongsberg (NSM, NASAMS, RWS), Nammo. Strict humanitarian law. Denied Saudi post-Yemen. Naval/precision munitions.', 'CONFIRMED'],
  ['Sweden ISP exports', 'Military Equipment Act. Strict democracy criteria. Saab (Gripen, Carl-Gustaf, AT4). Gripen needs Swedish approval per customer.', 'CONFIRMED'],
  ['Netherlands CDIU', 'Under MFA. Very strict. Thales Netherlands, Damen Shipyards, DAF. Parliament scrutinises. Frequent conflict zone denials.', 'CONFIRMED'],
  ['MTCR', '35 members. Missiles 500kg/300km (Cat I strongest denial). Cat II components. Not legally binding. Key for UAV exports.', 'CONFIRMED'],
  ['Nuclear Suppliers Group', '48 members. Nuclear/dual-use controls. India not member. Requires comprehensive IAEA safeguards.', 'CONFIRMED'],
  ['Australia Group', '43 members. Chemical/biological controls. No-undercut policy: consult before approving denied transfer.', 'CONFIRMED'],
  ['Russia post-sanctions arms trade', 'Post-Ukraine sanctions crippled exports. Lost key clients. SWIFT exclusion, component shortages. Exports dropped 50%+ from $15B peak. Africa clients seeking alternatives.', 'CONFIRMED'],
  ['Poland defence modernisation', '4%+ GDP. Massive K2/Abrams/HIMARS/FA-50/K9/Patriot/Apache surge. PGZ national champion. Largest European spender.', 'CONFIRMED'],
  ['UAE EDGE Group', '25 entities. Tawazun 60% offset. ADASI, NIMR, Halcon. Aims top 25 exporter by 2030. IDEX major show.', 'CONFIRMED'],
  ['Saudi GAMI Vision 2030', 'SAMI national champion. 50-60% localisation by 2030. JVs with Lockheed, Raytheon, BAE, Navantia. ~$75B annual spend.', 'CONFIRMED'],
  ['Nigeria defence market', '~$3B budget. Turkey TB2, China vehicles, Russia Mi-35. Boko Haram drives armoured/ISR demand. DICON state manufacturer.', 'CONFIRMED'],
  ['Kenya defence', '~$1.2B budget. AMISOM/ATMIS. AH-1Z (US), Cougar (Turkey). UK/US partner. East Africa defence hub.', 'CONFIRMED'],
  ['Mozambique FADM', '~$200M budget. Cabo Delgado insurgency. LNG projects security. Portugal training bilateral.', 'CONFIRMED'],
  ['Philippines AFP Modernization', 'Horizon 1-3, ~$5B. BrahMos, Black Hawk. South China Sea drives naval needs. US Mutual Defense Treaty.', 'CONFIRMED'],
  ['Pakistan defence exports', 'DEPO manages. JF-17 (with China), Al-Khalid. Myanmar, Nigeria, Sri Lanka. POF largest manufacturer.', 'CONFIRMED'],
  ['UN arms embargoes current', 'CAR, DRC, Libya, Mali, DPRK, Somalia, South Sudan, Sudan (Darfur), Yemen (Houthis). Panel of Experts monitors.', 'CONFIRMED'],
  ['EU sanctions overview', '~40 regimes. Arms embargoes: Russia, Belarus, China (1989), Myanmar, Zimbabwe, Syria. Each state enforces.', 'CONFIRMED'],
];

// ── Compliance, legal, and regulatory knowledge ──
const COMPLIANCE_LAW_FACTS = [
  // UK Export Control Framework
  ['UK Export Control Act 2002', 'The Export Control Act 2002 is the primary UK legislation governing arms exports. It gives the Secretary of State power to make orders controlling the export of goods, transfer of technology, and provision of technical assistance. Enforced by HMRC and investigated by HMRC and NCA. Maximum penalties: unlimited fine and/or up to 10 years imprisonment.', 'CONFIRMED'],

  ['UK Export Control Order 2008', 'The Export Control Order 2008 (ECO 2008) implements the Export Control Act 2002. It defines the UK Strategic Export Control Lists (Schedule 2 = Military List ML1-ML22, Schedule 3 = Dual-Use). Requires export licences for controlled goods. Amended regularly to reflect Wassenaar updates.', 'CONFIRMED'],

  ['UK SIEL licence process', 'Standard Individual Export Licence (SIEL): required for each specific export of controlled goods to a specific consignee/end-user. Application via SPIRE online system. Processing time: 20 working days target (60 working days for complex cases). Requires end-user undertaking (EUU). Valid for 2 years from date of issue.', 'CONFIRMED'],

  ['UK OIEL licence process', 'Open Individual Export Licence (OIEL): permits multiple shipments of specified items to specified destinations over the licence period (typically 5 years). Requires compliance audit capability. More efficient for repeat exports but higher compliance burden. Reporting requirements every 6 months.', 'CONFIRMED'],

  ['UK OGEL licences', 'Open General Export Licences (OGELs): pre-published licences for lower-risk exports. No application needed — register and comply with conditions. Key OGELs: Military Goods (X), Dual-Use Items (Technology for Government & EU destinations). Cannot be used for embargoed destinations. Annual compliance returns required.', 'CONFIRMED'],

  ['UK Consolidated Criteria', 'UK Consolidated EU and National Arms Export Licensing Criteria (8 criteria): 1) International obligations, 2) Human rights, 3) Internal situation in destination, 4) Regional stability, 5) National security of UK and allies, 6) Behaviour of buyer country, 7) Diversion risk, 8) Sustainable development. ALL criteria must be assessed for every licence application.', 'CONFIRMED'],

  ['UK End-User Undertaking', 'End-User Undertaking (EUU) requirements: Must state end-user identity, intended use, confirmation goods will not be re-exported without UK Government permission. For military goods: must be signed by authorised government official of end-user country. ECJU may request additional documentation including delivery verification certificates.', 'CONFIRMED'],

  ['ECJU role and structure', 'Export Control Joint Unit (ECJU): the UK Government body that assesses export licence applications. Joint unit of DIT, MOD, and FCO. Located in London. Headed by a Director-level civil servant. Consults with intelligence agencies on sensitive applications. Publishes quarterly and annual statistics. Appeals go to Export Control Organisation.', 'CONFIRMED'],

  // US Export Control Framework
  ['ITAR overview (detailed)', 'International Traffic in Arms Regulations (ITAR): controls export of defence articles and services on the US Munitions List (USML, 21 categories). Administered by State Department DDTC. Key features: registration required for manufacturers/exporters ($2,250/year), no de minimis exception (unlike EAR), citizenship-based restrictions. Violations: up to $1M per violation and/or 20 years imprisonment.', 'CONFIRMED'],

  ['EAR overview', 'Export Administration Regulations (EAR): controls dual-use and commercial items on Commerce Control List (CCL). Administered by BIS. Uses ECCN classification (5-character alphanumeric). De minimis rule: items with <25% controlled US content may not need licence (10% for embargoed countries). License exceptions available. Entity List restricts exports to specific foreign parties.', 'CONFIRMED'],

  ['FMS process (detailed)', 'Foreign Military Sales (FMS): US Government-to-Government arms sales programme. Process: Letter of Request (LOR) → Price & Availability (P&A) → Letter of Offer and Acceptance (LOA) → Congressional notification (30-day hold for NATO/major allies, 15 days others, $14M threshold for major defence equipment) → Contract execution. Managed by DSCA. ~$80B/year in active cases.', 'CONFIRMED'],

  ['CAATSA impact on defence', 'Countering Americas Adversaries Through Sanctions Act (CAATSA) Section 231: imposes sanctions on persons engaging in significant transactions with Russian defence/intelligence sector. Key impact: countries purchasing Russian S-400 (Turkey), Su-35, or other major Russian platforms risk US sanctions including exclusion from F-35 programme, denial of US export licences, and financial sanctions. Waiver possible under Section 231(d).', 'CONFIRMED'],

  // Wassenaar Arrangement
  ['Wassenaar Arrangement', 'The Wassenaar Arrangement on Export Controls for Conventional Arms and Dual-Use Goods and Technologies: 42 participating states. Established 1996 (successor to COCOM). Maintains Munitions List and Dual-Use List. Not legally binding — members implement through national legislation. Plenary meets December annually in Vienna. Best Practice documents guide member state implementation.', 'CONFIRMED'],

  // Arms Trade Treaty
  ['Arms Trade Treaty obligations', 'Arms Trade Treaty (ATT): entered into force 2014. 113 states parties. Key obligations: Article 6 — prohibit transfers that violate UN arms embargoes or would be used for genocide/war crimes. Article 7 — risk assessment before export (peace, human rights, terrorism, organised crime, gender-based violence, diversion). Article 8 — importing state to provide EUC. Article 11 — prevent diversion. Article 13 — annual reporting.', 'CONFIRMED'],

  // Key Case Law
  ['BAE Systems Tanzania case', 'R v BAE Systems plc (2010): BAE pleaded guilty to breach of duty to keep accounting records (s.221 Companies Act 1985). Related to Tanzania military radar deal (JAS 39 Gripen associated). SFO investigation 2004-2010. Fine: £500,000 + £30M ex gratia payment to Tanzania. Established precedent that defence companies face prosecution for corrupt overseas dealings. Led to reforms in UK anti-bribery enforcement (preceded Bribery Act 2010).', 'CONFIRMED'],

  ['Al Yamamah precedent', 'Al Yamamah arms deal (1985-present): UK-Saudi Arabia defence package worth ~£43B. Includes Tornado, Typhoon aircraft, support services. SFO investigation into BAE corruption halted 2006 by Attorney General on national security grounds. Landmark R (Corner House Research) v Director of SFO [2008] UKHL 60: House of Lords upheld SFO decision to drop investigation. Precedent: national security can override anti-corruption investigations in arms deals.', 'CONFIRMED'],

  ['ITAR violation penalties precedent', 'Key ITAR enforcement actions: Raytheon — $20M civil penalty (2023) for unauthorised exports of classified missile defence technology. ITT Corporation — $100M combined penalties (2007) for night vision technology transfers to China/Singapore. L3 Technologies — $30M (2016) for satellite/EO technology. Trend: penalties increasing, DOJ pursuing criminal prosecution alongside civil penalties. Voluntary self-disclosure reduces penalties by ~50%.', 'CONFIRMED'],

  // Defence Market Cycles
  ['Angola defence procurement cycle', 'Angola defence procurement: fiscal year October-September. Budget approved by National Assembly ~Q4. Primary procurement agency: SIMPORTEX (subordinate to MINFAR). Major equipment purchases require presidential approval. Payment typically via oil-backed credit lines or sovereign wealth fund (FSDEA). Key partner: Russia (legacy), expanding to Turkey, China, Brazil. Annual defence budget ~$3B (2024 estimate). Offset requirements managed by SIMPORTEX, typically 15-30%.', 'CONFIRMED'],

  ['Saudi Arabia GAMI process', 'Saudi Arabia defence procurement via GAMI (General Authority for Military Industries) established 2017 under Vision 2030. Localisation target: 50% by 2030. SAMI (Saudi Arabian Military Industries) is the national champion. Procurement categories: direct military sales, offset obligations (50-60%), joint ventures, technology transfer. Budget cycle: January fiscal year. Royal Saudi Air Force, Royal Saudi Land Forces, Royal Saudi Navy each have procurement authority under MOD umbrella.', 'CONFIRMED'],

  ['Indonesia MEF phases', 'Indonesia Minimum Essential Force (MEF) programme: 3 phases. Phase 1 (2010-2014): basic force readiness. Phase 2 (2015-2019): enhanced capability. Phase 3 (2020-2024): ideal force strength. Beyond 2024: maintained force level. Offset requirements: 35% for contracts $5-10M, escalating to 85% for >$10M. Managed by Kemhan (MOD). Key recent acquisitions: Rafale (France), F-15EX (US consideration), Leopard 2 (Germany). Annual defence budget ~$9B.', 'CONFIRMED'],

  // Diversion Risk Framework
  ['Diversion risk assessment', 'Diversion risk assessment framework for arms exports: 1) Track record of recipient (previous diversions?), 2) Transit routes (through high-risk countries?), 3) End-user credibility (legitimate military/police?), 4) Intermediaries involved (sanctioned/known diverters?), 5) Stated purpose vs capability (excessive for stated use?), 6) Regional conflict dynamics, 7) Re-export controls in destination, 8) Post-delivery verification capability. UK/EU require this under Criterion 7/Article 11 ATT.', 'CONFIRMED'],
];

// ── Recall enhancement for compliance cross-referencing ──
const RECALL_ENHANCEMENT = [
  ['ARIA recall protocol', 'When answering compliance questions, ALWAYS check: 1) Your knowledge base facts on the specific regulation, 2) Country risk profile of the destination, 3) Product classification (ML category), 4) Any applicable sanctions or embargoes, 5) Relevant case law or precedent. Structure your answer using the compliance framework: Classification → Licensing Route → Risk Factors → Recommendation.', 'CONFIRMED'],

  ['ARIA knowledge verification', 'When sharing facts from your knowledge base, state your confidence level and source. If you are not sure about a specific regulation or requirement, say so clearly and recommend the user consult ECJU/DDTC/legal counsel. Never present uncertain information as confirmed fact in compliance contexts — the consequences of wrong compliance advice can include criminal prosecution.', 'CONFIRMED'],
];

let _seeded = false;

/**
 * Seed the export control knowledge base from the JSON data file.
 * Loads ML categories, ITAR categories, country designations,
 * manufacturers, HS codes, and compliance rules as individual facts.
 */
export function seedExportControlKB() {
  let count = 0;
  try {
    const __dirname = dirname(fileURLToPath(import.meta.url));
    const kbPath = join(__dirname, '..', '..', 'data', 'aria_export_control_kb.json');
    const kb = JSON.parse(readFileSync(kbPath, 'utf-8'));

    // ML categories
    for (const ml of (kb.ml_categories || [])) {
      storeFact(
        `ML category ${ml.code}`,
        `${ml.code}: ${ml.title}. Examples: ${ml.examples}`,
        'export_control_kb', 'CONFIRMED'
      );
      count++;
    }

    // ITAR categories
    for (const cat of (kb.itar_categories || [])) {
      storeFact(
        `USML Category ${cat.category}`,
        `ITAR USML Category ${cat.category}: ${cat.title}`,
        'export_control_kb', 'CONFIRMED'
      );
      count++;
    }

    // Country designations
    for (const cd of (kb.country_designations || [])) {
      storeFact(
        `${cd.country} export control status`,
        `${cd.country} — Embargoes: ${cd.embargoes}. Risk level: ${cd.risk_level}. ${cd.notes}`,
        'export_control_kb', 'CONFIRMED'
      );
      count++;
    }

    // Manufacturers
    for (const mfr of (kb.manufacturers || [])) {
      storeFact(
        `${mfr.name} ITAR status`,
        `${mfr.name} (${mfr.country}): ITAR-controlled: ${mfr.itar ? 'YES' : 'NO'}. Products: ${mfr.products}`,
        'export_control_kb', 'CONFIRMED'
      );
      count++;
    }

    // HS codes
    for (const hs of (kb.hs_codes || [])) {
      storeFact(
        `HS code ${hs.code}`,
        `HS ${hs.code}: ${hs.description}. Maps to UK SECL ${hs.ml_category}.`,
        'export_control_kb', 'CONFIRMED'
      );
      count++;
    }

    // Compliance rules
    for (const rule of (kb.compliance_rules || [])) {
      storeFact(
        `Compliance rule: ${rule.rule}`,
        rule.description,
        'export_control_kb', 'CONFIRMED'
      );
      count++;
    }

    console.log(`[ARIA KB] Seeded ${count} export control facts from KB`);
  } catch (err) {
    console.warn(`[ARIA KB] Export control KB seed failed: ${err.message}`);
  }
  return count;
}

export function seedKnowledgeBase() {
  if (_seeded) return;
  _seeded = true;
  let count = 0;
  for (const [topic, content, confidence] of SEED_FACTS) {
    storeFact(topic, content, 'seed', confidence);
    count++;
  }
  // Seed offset requirements
  for (const [topic, content, confidence] of OFFSET_FACTS) {
    storeFact(topic, content, 'seed', confidence);
    count++;
  }
  // Seed global export control regimes
  for (const [topic, content, confidence] of GLOBAL_EXPORT_CONTROLS) {
    storeFact(topic, content, 'global_export_controls', confidence);
    count++;
  }
  // Seed compliance, legal, and regulatory knowledge
  for (const [topic, content, confidence] of COMPLIANCE_LAW_FACTS) {
    storeFact(topic, content, 'compliance_law', confidence);
    count++;
  }
  // Seed recall enhancement facts
  for (const [topic, content, confidence] of RECALL_ENHANCEMENT) {
    storeFact(topic, content, 'compliance_law', confidence);
    count++;
  }
  console.log(`[ARIA KB] Seeded ${count} core defence industry facts`);

  // Seed export control KB from JSON data file
  seedExportControlKB();
}
