# =============================================================================
# ARIA v3.1 — Regional Navigation Intelligence
# aria_service/intel/regional_navigation.py
#
# PURPOSE:
#   ARIA must not approach every market the same way.
#   Each region has a distinct political architecture, procurement culture,
#   relationship timeline, communication register, and BD entry strategy.
#   Getting this wrong costs relationships and deals.
#
# PRINCIPLE:
#   Facts about a country are not regional intelligence.
#   Regional intelligence is knowing HOW a market operates —
#   not just WHAT it contains.
#
# COVERAGE:
#   Region 1:  Latin America (deep build — highest priority gap)
#   Region 2:  Sub-Saharan Africa — Anglophone and Francophone
#   Region 3:  North Africa and Sahel
#   Region 4:  Middle East and Gulf
#   Region 5:  Asia-Pacific (ASEAN, South Asia, NE Asia, Oceania)
#   Region 6:  Europe (NATO, Eastern, Balkans)
#   Region 7:  Central Asia and Caucasus
#   Region 8:  North America
#   Region 9:  Regional Intelligence Cross-Reference Matrix
# =============================================================================

import json
import hashlib
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger("ARIA.RegionalNavigation")


# =============================================================================
# REGION 1 — LATIN AMERICA
# Deepest build. ARIA currently has almost nothing here.
# =============================================================================

LATIN_AMERICA = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — LATIN AMERICA REGIONAL NAVIGATION INTELLIGENCE                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARIA SELF-ASSESSMENT: Latin America is a critical gap. The region contains
Brazil (one of the world's top 15 defence spenders), Colombia (active COIN
procurement), Peru, Chile, Argentina, Ecuador, and Mexico — all with active
procurement programmes. Arkmurus has no incumbent relationships. Building this
regional intelligence is the prerequisite to any engagement.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.1 HOW LATIN AMERICA IS DIFFERENT — THE OPERATING SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RELATIONSHIP PRIMACY:
Latin American defence BD is built on personalismo — the personal relationship
between individuals, not companies. An institution signs a contract; a person
decides. Without a prior personal relationship with the decision-maker, you
do not exist as a supplier. This is not a process culture — it is a trust
culture. The trust is with Antonio specifically, not with Arkmurus as an entity.

COLD APPROACH FAILURE RATE:
Cold approaches to Latin American MoDs without a local partner, a personal
introduction, or an OEM backing fail at an extremely high rate. The decision-
maker will take the meeting out of courtesy but will not advance it without a
trusted third-party endorsement. The endorsement must come from within their
network, not from the supplier's marketing.

TIME HORIZON:
First meaningful relationship contact to first signed contract in Latin America:
  Minimum 18–24 months in most markets.
  Brazil: 24–36 months for major programmes.
  Colombia: 12–18 months (more transactional culture, US FMS familiarity).
  Peru: 18–24 months.
  Chile: 12–18 months (most process-driven market in the region).
Patience is not a commercial weakness. It is the price of admission.

POLITICAL VOLATILITY — THE MOST IMPORTANT REGIONAL VARIABLE:
Political change is endemic. Brazil has had four presidents in five years
(Bolsonaro → Lula). Peru has impeached six presidents since 2016. Colombia
oscillates between security-focused and dialogue-focused administrations.
ARIA APPLICATION: Never tie a BD relationship exclusively to one administration.
Build institutional relationships at the military/procurement officer level —
these survive government changes. Ministerial relationships are high-value but
transient.

PROCUREMENT CULTURE TYPE: Hybrid — formal process with informal power
Process exists (Brazil: LICITAÇÃO; Peru: SEACE; Chile: ChileCompra).
But the contract goes to whoever has the relationship with the decision-maker
unless the process is highly formalised and overseen. The formal process can
be navigated around through direct procurement routes, exceptions, and
presidential discretion for strategic acquisitions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.2 BRAZIL — THE PRIORITY MARKET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHY BRAZIL IS DIFFERENT FROM THE REST OF LATIN AMERICA:
Brazil is not a Latin American defence market. It is a major global defence
market that happens to be in Latin America. It is the only country in the
Western Hemisphere with an indigenous combat aircraft programme (KC-390),
a submarine programme (Álvaro Alberto nuclear-powered class under development),
a domestic OPV production capability, and a defence industrial policy that
requires technology transfer for major acquisitions.

KEY FACTS:
  Defence budget: ~$20B total (includes pensions) / ~$4–5B actual procurement
  Military structure: FAB (Air Force), Exército (Army), Marinha (Navy)
  Defence ministry: Ministério da Defesa (MD) — civilian since 1999
  Procurement law: Lei de Licitações (14.133/2021) — extensive process
  Strategic defence: PAED (Strategic Programme for Defence Artefacts)
  Key acquisitions body: ASTROS (Army), EMGEPRON (Navy), FAB COPAC (Air)

BRAZIL — PROCUREMENT CULTURE:
  Technology transfer (Transferência de Tecnologia / ToT) is mandatory for
  major acquisitions. Without a clear ToT commitment, Brazilian procurement
  committees will not advance foreign suppliers.
  
  EMBRAER factor: Brazil's aerospace industry is world-class. Any aviation
  procurement that bypasses EMBRAER creates political resistance. Structure
  proposals to include EMBRAER as a maintenance, integration, or offset partner.
  
  ITAR sensitivity: Brazil is deeply ITAR-aware and frequently prefers
  non-US-origin platforms specifically to avoid ITAR restrictions on future
  modification, export, or technology reuse.
  Examples: KC-390 (Boeing C-390 collaboration but Brazilian-designed),
  F-39 Gripen selection (over F-18 and Rafale — explicitly ITAR-free factor).

BRAZIL — KEY DECISION-MAKER TYPES:
  EMFA (Estado-Maior das Forças Armadas): Joint Chiefs level — strategic direction.
  SMGE (Secretaria de Modernização): Modernisation secretariat under Ministério.
  Each service has its own procurement secretariat.
  Key: Brazilian military has significant institutional autonomy.
  Political interference exists but technical evaluations carry weight.

BRAZIL — CPLP ANGLE (Arkmurus specific):
Brazil is a CPLP member and has Portuguese as its language.
This creates a natural Arkmurus bridge. Brazil maintains active military
cooperation with Angola, Mozambique, Cape Verde, and Guinea-Bissau.
Opportunity: Arkmurus as the entity that understands both the Brazilian
industrial ecosystem AND the CPLP African procurement context could position
itself as the bridge between Brazilian OEMs and African MoD clients.
EMBRAER, Avibras (ASTROS II MLRS), Mectron (Piranha AAM), Forjas Taurus:
all seeking African market access without established relationships.

BRAZIL — BD ENTRY STRATEGY:
  Step 1: Attend LAAD (Latin American Aero & Defence) — Rio de Janeiro, biennial.
    This is the single highest-value exhibition for Brazil BD. Every major
    Brazilian MoD official and OEM is present. No alternative.
  Step 2: Identify a local parceiro estratégico (strategic partner) in Brasília.
    A Brazilian defence consultancy or law firm with MoD relationships is essential.
    Without a local anchor, cold outreach to Ministério da Defesa fails.
  Step 3: Position through the CPLP lens.
    Brazilian military has real affinity for CPLP African states.
    Introducing Arkmurus as the CPLP-specialist broker creates differentiation.
  Step 4: Focus on technology transfer opportunities.
    Brazilian OEMs want African market access but need compliance support.
    Arkmurus provides: SITCL compliance, CPLP market access, EUC support.

BRAZIL — COMMUNICATION STYLE:
  Language: Brazilian Portuguese — not European Portuguese. Different vocabulary,
    rhythm, and register. Never use European Portuguese forms with Brazilians.
  Formality: Professional but warm. First names relatively quickly once rapport
    established. Formal titles (Dr., Eng., Cel.) are important in first contact.
  Meeting culture: Meetings start late (10–20 minutes is normal, not rude).
    Small talk is mandatory before business. Rushing to the agenda signals
    disrespect. Allow time.
  Email: Responded to. WhatsApp: Even more so. WhatsApp is the primary
    professional communication channel in Brazil — use it.
  Hierarchy: The most senior person in the room is deferred to. Do not address
    junior officials in front of seniors without acknowledgment from the senior.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.3 COLOMBIA — THE ACTIVE COIN MARKET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHY COLOMBIA MATTERS:
Colombia has been in active internal armed conflict since the 1960s. Even with
the FARC peace agreement (2016), the ELN (Ejército de Liberación Nacional)
remains active, and dissident FARC factions continue operations. This creates
a persistent, well-funded COIN procurement requirement across: light aviation,
armoured vehicles, riverine craft, special operations equipment, comms, and ISR.

DEFENCE BUDGET: ~$4.8B annually. High execution rate (~85%) compared to broader
region. US FMF (Foreign Military Financing) supplements domestic procurement.

PROCUREMENT CULTURE:
  More transactional than Brazil or Peru. US FMS familiarity means processes
  are better understood. US-origin equipment dominates: Black Hawk helicopters,
  OV-10 Bronco (recently retired), A-29 Super Tucano.
  ITAR presence: Very high. Colombia is deeply integrated into US security
  assistance architecture. Non-ITAR alternatives are welcomed but must prove
  logistics sustainability.

CURRENT PETRO GOVERNMENT RISK:
  Gustavo Petro (elected 2022) has pursued dialogue with ELN and reduced some
  military operations tempo. This creates uncertainty in procurement pipeline.
  However: Colombia's military is institutionally US-aligned and procurement
  decisions have significant military autonomy.
  ARIA: monitor Colombian political trajectory — defence procurement restarts
  with every operational escalation.

KEY PROGRAMMES:
  Light attack aircraft replacement (A-29 Tucano successor evaluation)
  Helicopter fleet sustainment (Black Hawk, Bell)
  Riverine interdiction vessels (narco-trafficking and insurgency)
  C4ISR modernisation
  Special Forces equipment (Fuerzas Especiales have significant autonomy)

ENTRY STRATEGY:
  US relationships are the fastest entry point. Colombia buys US and trusts US.
  For non-US OEMs: demonstrate that your platform will not be ITAR-constrained
  when Colombia needs to maintain or modify it in 10 years.
  Local partner: essential. Colombian defence contractors (INDUMIL is the
  state arsenal) or defence consultancy firms in Bogotá.
  Exhibition: ExpoDefensa (Bogotá, biennial) — the Colombian equivalent of DSEI.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.4 CHILE — THE MOST PROCESS-DRIVEN MARKET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Chile is the most institutionally stable and process-oriented defence market
in Latin America. This is both an advantage (lower corruption risk, process
integrity) and a challenge (no shortcuts — you must compete on merit and
specification compliance).

COPPER LAW (Ley del Cobre): 10% of CODELCO (state copper mining) revenues
are constitutionally allocated to military procurement. Creates a stable,
predictable procurement budget largely insulated from political fluctuation.
Approximately $800M–$1.2B annually for new acquisitions.
This is exceptional in the region. Chile's procurement pipeline is the
most reliable in Latin America as a result.

KEY PROGRAMMES:
  FAch (Air Force) fighter modernisation — F-16 upgrade + potential successor
  Army armoured vehicle replacement
  Armada de Chile frigate and OPV programme
  Air defence modernisation

PROCUREMENT APPROACH:
  International competitive tender is the norm. Genuine competition, genuine
  evaluation. Technical compliance is scored seriously.
  Prior relationships matter but less than in Brazil or Peru.
  European suppliers (France, Germany, Netherlands, Spain) compete strongly.
  Israel (SIBAT) has significant presence.

ENTRY STRATEGY:
  Respond to tenders properly — spec compliance is evaluated seriously.
  FIDAE (Feria Internacional del Aire y del Espacio) — Santiago, biennial.
    Chile's premier aerospace and defence exhibition. Worth attending.
  Government-to-government: UK has defence cooperation with Chile.
    Leverage for introductions where possible.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.5 PERU — SEE ALSO ARK-INTEL-PERU-20260412
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Key additional regional context beyond the Peru intelligence report:
Peru is heavily influenced by its Andean neighbours. Ecuador border tensions
have historically driven procurement. Argentina to the south influences
prestige procurement. The Pacific Alliance (Chile, Colombia, Peru, Mexico)
creates some alignment in procurement approach and interoperability.

SEACE portal: https://seace.gob.pe — the primary procurement transparency portal.
Peru-Brazil CPLP angle: Peru has no CPLP connection but Brazil is the dominant
regional power and Brazilian OEMs (EMBRAER, Avibras) see Peru as a market.
Arkmurus as a Brazilian-OEM bridge creates opportunity if the Brazil angle is developed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.6 ARGENTINA — THE DISTRESSED MARKET WITH LATENT POTENTIAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Argentina has chronic economic crisis, currency controls, and limited hard
currency for procurement. Defence budget is large in pesos, tiny in USD.
BUT: Argentina has a proud military tradition, a domestic defence industry
(INVAP for nuclear/radar, FADEA for aircraft), and significant aspiration.

Current situation (Milei government 2023+): Severe austerity, defence budget
slashed, major procurement effectively frozen. Not a near-term market.
Monitor for recovery — Argentina historically oscillates.

Latent opportunity: Argentina desperately needs fighter aircraft replacement
(Fuerza Aérea Argentina operates 1970s A-4AR Skyhawks and Mirage III).
F-16s from Denmark and Netherlands have been discussed. When economic
stabilisation occurs, this will be a significant procurement.

ARIA flag: Monitor Argentine economic stabilisation. When fiscal space returns,
the FAA fighter programme becomes a live opportunity.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.7 MEXICO — LARGE BUT DIFFICULT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Mexico has a large military ($15B+ budget) focused primarily on internal
security and narco-interdiction. However, Mexico is complex for foreign
defence suppliers for three reasons:

1. US dominance: NORTHCOM relationship means most procurement is US-origin
   through FMS or DCS. Alternative suppliers face high trust barriers.
2. Political sensitivities: AMLO/Sheinbaum governments view foreign military
   equipment procurement as politically sensitive domestically.
3. Corruption risk: Mexico scores very low on TI CPI for defence procurement.
   AML and Bribery Act exposure is significant for UK-based brokers.

Arkmurus recommendation: Not a priority market currently. Monitor but do not
invest BD resource without a specific named opportunity and a trusted local partner.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1.8 LATIN AMERICA — REGIONAL BD PRINCIPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEVER make these mistakes in Latin America:
  → Cold approach a Ministry of Defence without introduction
  → Assume European Portuguese works in Brazil (it does not — register is different)
  → Skip the small talk and go straight to business (cultural breach)
  → Promise technology transfer you cannot deliver (permanent relationship damage)
  → Use the same intermediary across multiple competing countries
    (Brazil and Argentina are rivals; Colombia and Venezuela are hostile)
  → Ignore ITAR implications — LATAM clients are acutely aware of them
  → Offer a price without understanding currency controls and payment structure

ALWAYS do this in Latin America:
  → Find the local parceiro/socio before approaching the institution
  → Attend the regional exhibition before making contact
  → Research the specific general or admiral who will make the decision
  → Frame technology transfer as a benefit in every proposal
  → Offer financing options (payment over time is often as important as price)
  → Learn the name of the client's country correctly (never call Brazil "LatAm")
  → Follow up with WhatsApp, not email — WhatsApp is the professional channel

KEY EXHIBITIONS FOR LATIN AMERICA:
  LAAD (Rio de Janeiro, biennial — April, odd years): Brazil/region primary
  ExpoDefensa (Bogotá, biennial — December, odd years): Colombia primary
  FIDAE (Santiago, biennial — April, even years): Chile/aviation primary
  ADE (Ecuador, biennial): Andean region
  SITDEF (Lima, biennial — May, odd years): Peru
  EXPO SegDef (São Paulo, annual): Brazilian domestic market

KEY PROCUREMENT PORTALS:
  Brazil:    https://comprasnet.gov.br + https://www.defesa.gov.br
  Peru:      https://seace.gob.pe
  Chile:     https://www.chilecompra.cl
  Colombia:  https://www.secop.gov.co
  Argentina: https://www.argentina.gob.ar/contrataciones

SIPRI LATIN AMERICA DATA:
  Source: https://armstransfers.sipri.org (filter by recipient region: Americas)
  Key insight: USA dominates Latin American imports. Russia declining.
  China growing (Venezuela, Bolivia, Ecuador). European OEMs present in Brazil/Chile.
  Gap: No dominant non-US supplier in COIN equipment — opportunity for UK brokers.
"""


# =============================================================================
# REGION 2 — SUB-SAHARAN AFRICA: ANGLOPHONE AND FRANCOPHONE
# (Beyond CPLP — ARIA already has depth there)
# =============================================================================

SUB_SAHARAN_AFRICA_BEYOND_CPLP = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — SUB-SAHARAN AFRICA: ANGLOPHONE AND FRANCOPHONE                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARIA NOTE: ARIA has deep knowledge of CPLP/Lusophone Africa (Angola, Mozambique,
Guinea-Bissau, Cape Verde, São Tomé). This section covers the rest of Sub-Saharan
Africa — the broader Anglophone and Francophone markets that collectively represent
a large procurement opportunity Arkmurus does not yet systematically address.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2.1 HOW ANGLOPHONE AFRICA OPERATES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROCUREMENT CULTURE TYPE: Relationship-heavy with formal process overlay.
More similar to Lusophone Africa than European markets, but with British
procedural inheritance in some states (Kenya, Ghana, South Africa, Nigeria).

Key commonality across Anglophone Africa: Personal relationships with
Chiefs of Defence Staff, Adjutants General, and procurement directors are
the decisive factor. The public tender is often the confirmation of a decision
already taken, not the decision itself.

UK ADVANTAGE: Post-colonial defence relationships mean UK military attachés
have access, UK training programmes (Kenya BATUK, Sierra Leone, Nigeria) create
procurement preference, and UK suppliers are known quantities. Arkmurus as a
UK-registered advisory firm has legitimate entry angles in Anglophone Africa
that other European competitors do not have.

NIGERIA — Largest African economy, highest defence budget outside North Africa:
  Budget: ~$4.8B (highly variable, heavily stolen in some periods)
  Threats: Boko Haram (NE), ISWAP, Bandits (NW), Niger Delta (militant groups)
  Equipment needs: Light aviation (ISR + COIN), mine-resistant vehicles,
    riverine craft, small arms, communications, UAVs
  ITAR exposure: US FMF recipient. US-origin equipment dominant.
  Chinese presence: Growing. VT-4 MBT, Type 056 corvette for Nigerian Navy.
  Procurement pathways: Predominantly direct/sole source at senior level.
    Process is present but regularly bypassed by presidential/ministerial decree.
  Corruption risk: VERY HIGH. TI CPI rank consistently among lowest globally.
    Arkmurus must apply enhanced due diligence to every Nigerian engagement.
  UK angle: BATUK training, UK MoD engagement, shared Commonwealth military ties.
  Entry: Through a trusted Lagos or Abuja-based defence partner. No cold approaches.

KENYA — Eastern Africa's most stable BD environment:
  Budget: ~$1B. Stable execution.
  Threats: Al-Shabaab (cross-border from Somalia), Northern border tension.
  Equipment needs: Light infantry, ISR, riverine/maritime patrol, COIN aviation.
  US presence: AFRICOM engagement, some FMF. UK presence: BATUK long-standing.
  Israeli presence: Significant. Hermes UAVs, Galil rifles, SIGINT.
  Procurement: More process-oriented than Nigeria. ChileCompra equivalent exists.
  Entry: UK military attaché introduction is effective here. BATUK relationships.

SOUTH AFRICA — The continental industrial power:
  Budget: ~$2.8B (severely constrained by economic challenges).
  Unique position: South Africa is simultaneously a buyer AND a regional supplier.
  Denel (state OEM): In crisis — opportunity for alternative suppliers.
  ARMSCOR: The procurement agency. Manages acquisitions + exports.
  BEE (Black Economic Empowerment) requirements: All major procurement requires
    BBBEE-compliant partnership. Cannot win without South African majority partner.
  NCACC (National Conventional Arms Control Committee): Governs exports.
    South African export controls are sophisticated — one of the best systems
    on the continent. Arkmurus must understand them for any SA-origin goods.
  Entry: Through a South African BBBEE-compliant defence partner. Mandatory.
  AAD (Africa Aerospace and Defence, Pretoria): Primary exhibition.

GHANA — Stable, smaller, growing:
  Budget: ~$250M. British Commonwealth military tradition.
  COIN focus: Northern border with Burkina Faso security deterioration.
  Growing maritime security requirement (Gulf of Guinea, EEZ).
  Entry: Ghana Armed Forces have active UK training relationships.
    UK entry relatively accessible compared to rest of West Africa.

ETHIOPIA AND HORN OF AFRICA:
  Currently in post-Tigray War reconstruction. Defence budget severely stressed.
  Not a near-term market but watch for reconstruction procurement.
  Eritrea: Sanctioned (UN/EU). No engagement.
  Somalia: Active conflict. AMISOM/ATMIS supply through UN framework only.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2.2 HOW FRANCOPHONE AFRICA OPERATES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CRITICAL CONTEXT — FRENCH WITHDRAWAL:
The most important recent development in Francophone Africa for Arkmurus is
France's expulsion from Mali, Burkina Faso, Niger, and rebalancing in Chad
and Senegal (2022–2024). France was THE dominant external defence partner
in Francophone Africa for 60 years. This dominance is collapsing.

WHO FILLS THE VACUUM:
  Russia/Africa Corps: Moving in rapidly — Mali, Burkina Faso, Niger.
  Turkey: Expanding. Somalia, Senegal, Guinea, Togo, Côte d'Ivoire discussions.
  China: Infrastructure + security package deals across the region.
  USA: Limited — not trusted after perceived abandonment post-coup tolerance.
  European alternatives (non-French): Not yet positioned. OPPORTUNITY.

FRANCOPHONE AFRICA PROCUREMENT CULTURE:
  Previously: French equipment, French support, French training — one ecosystem.
  Now: Transition market. New suppliers being evaluated.
  Language: French is mandatory for all formal communication.
    Senior military officers in Francophone Africa were often trained in France.
    Switching to English signals lack of engagement with their world.
  Relationship: Highly personal. Who do you know? Who sent you?
  Corruption risk: Variable. Côte d'Ivoire improving. Mali/Burkina: significant.

CÔTE D'IVOIRE (Ivory Coast):
  Budget: ~$700M. Growing. UEMOA's most stable large economy.
  French presence: Declining but still significant. French base in Abidjan.
  Procurement: Relatively structured. Presidents Ouattara era brought reform.
  Opportunity: Ouattara-era professionalization = merit in procurement.
  Entry: French-speaking intermediary mandatory. Abidjan-based partner.

SENEGAL:
  Budget: ~$450M. Stable. New government (Sonko/Faye 2024) recalibrating.
  French rebalancing: Senegal hosting French base under renegotiation.
  Opportunity: Space created by French repositioning.
  OPV/maritime: Atlantic EEZ enforcement requirement growing with offshore gas.

CAMEROON:
  Budget: ~$700M. Anglophone/Francophone split — internal political tension.
  COIN: Boko Haram in Far North. Anglophone separatist crisis.
  Complex: Two languages, two political communities, persistent conflict.
  FATF grey list: Compliance risk — enhanced due diligence mandatory.

SAHEL STATES (Mali, Burkina Faso, Niger):
  All three under military junta governments as of 2024.
  All three have expelled French forces.
  Russia/Africa Corps present.
  UK arms embargo: None currently, but reputational risk significant.
  Arkmurus approach: Intelligence monitoring only. No active BD. Too high risk.

DEMOCRATIC REPUBLIC OF CONGO (DRC):
  Budget: ~$600M (chaotic execution). UN arms embargo (partial) in force.
  Conflict: M23/FDLR in east. FARDC (national army) weak and fragmented.
  Any supply to DRC requires CAREFUL UN embargo navigation.
    Embargo applies to non-governmental entities and some state forces in specific
    provinces. MONUSCO-supplied equipment exempt with notification.
  Arkmurus: Extreme caution. Legal advice before any engagement.

REGIONAL EXHIBITION: FORUMILAC (Forum of Lake Chad Basin) — irregular.
  EUROSATORY covers Francophone Africa delegations well.
  No dedicated Francophone Africa defence exhibition — gap opportunity.
"""


# =============================================================================
# REGION 3 — NORTH AFRICA AND SAHEL
# =============================================================================

NORTH_AFRICA_SAHEL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — NORTH AFRICA AND SAHEL NAVIGATION INTELLIGENCE                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3.1 NORTH AFRICA — THE LARGEST AFRICAN DEFENCE SPENDERS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALGERIA — Africa's largest defence budget:
  Budget: ~$9.1B — largest in Africa, driven by hydrocarbons revenue.
  Equipment: Predominantly Russian — Su-30, T-90, BMP-3, Kilo submarines.
  Post-2022: Russian supply disruption + sanctions creating space for alternatives.
  Procurement culture: Opaque. State-controlled. Presidential/military elite decide.
  Key dynamic: Algeria-Morocco rivalry drives procurement competition.
    Morocco buys Western (F-16, Patriot, Lynx IFV). Algeria mirrors or exceeds.
  ITAR sensitivity: Algeria refuses ITAR-constrained equipment on principle.
    This makes European and Turkish suppliers preferred over US alternatives.
  Entry: Extremely difficult for unsolicited foreign approaches.
    Algerian procurement is essentially closed to cold entry.
    Intelligence monitoring value: Very high (signals supply chain gaps).

MOROCCO — The most Western-aligned North African market:
  Budget: ~$5.4B. Growing rapidly.
  Equipment: F-16 (USAF training), Abrams MBT, Patriot air defence,
    Lynx IFV (Rheinmetall), Israeli SIGINT, UAVs.
  Abraham Accords (2020): Normalisation with Israel opened Israeli defence
    supply — significant shift in procurement landscape.
  Procurement culture: More structured than Algeria. MoD procurement
    directorate has process. Relationships still important but spec matters.
  Entry: US FMS for major platforms. European OEMs for specific categories.
    Israeli suppliers now have direct access. UK has limited presence —
    opportunity in categories where US-origin creates future ITAR concerns.
  Language: Arabic (official) + French (business and military).
    Do not attempt English as primary language with military officials.

EGYPT — Large, complex, strategically important:
  Budget: ~$4.6B. Heavily subsidised by Gulf states (UAE, Saudi Arabia).
  Equipment: Mix of US (F-16, M1A1, M2 Bradley, AH-64) + French (Mistral,
    Rafale, FREMM frigate) + Russian legacy + increasingly domestic production.
  EDGE Group model: Egypt is building domestic defence industry modelled
    on UAE's EDGE — seek technology transfer, not just equipment.
  Procurement: US FMS for legacy US platforms. French DGA-to-DGA for French.
    Direct commercial competition rare for major programmes.
  Compliance note: Egypt is not on UN embargo but has significant human rights
    concerns (UK Criterion 2 risk). Enhanced DD required for any engagement.
  Entry: Government-to-government channel primary. No cold commercial approaches.

LIBYA — Embargoed but monitored:
  UN arms embargo: In force (UNSC Res 1970, 2011). Multiple documented violations.
  NO engagement. Intelligence monitoring only.
  ARIA tracks: Which actors are receiving arms in violation — competitor intel.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3.2 REGIONAL NAVIGATION — NORTH AFRICA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Communication approach: Arabic primary, French secondary.
  Never approach North African MoD officials in English as primary language.
  Use professional translator/interpreter for formal meetings.

Religious and cultural sensitivities:
  Ramadan: Business pace slows significantly. Do not schedule critical meetings.
  Prayer times: Meetings pause. Build this into agendas.
  Hospitality: Coffee and dates are offered. Accepting shows respect.
  Hierarchy: Extremely important. Never bypass seniority in the room.

EUROSATORY covers North Africa delegations well.
No dedicated North Africa defence exhibition — engage at MILIPOL and DSEI.
"""


# =============================================================================
# REGION 4 — MIDDLE EAST AND GULF
# =============================================================================

MIDDLE_EAST_GULF = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — MIDDLE EAST AND GULF NAVIGATION INTELLIGENCE                       ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4.1 GULF COOPERATION COUNCIL (GCC) — THE WEALTHIEST DEFENCE MARKET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GCC MEMBERS: Saudi Arabia, UAE, Qatar, Kuwait, Bahrain, Oman.
Combined defence spending: ~$100B annually.
This is the most financially potent defence market in the developing world.

PROCUREMENT CULTURE — CORE PRINCIPLE:
Gulf procurement is relationship-driven at the highest levels.
The decision is made by a prince, a minister, or a senior royal family member.
Process exists but is secondary to who has access to the decision-maker.

Without a royal or senior government-level endorsement or a trusted
introduction from an existing relationship, no foreign supplier wins a
major contract in the Gulf. This is not cynicism — it is the operating reality
of every Gulf market.

SAUDI ARABIA (SAMI and Vision 2030):
  Budget: ~$75B (2nd largest in the world).
  Vision 2030: 50% domestic content target in defence by 2030.
  SAMI (Saudi Arabian Military Industries): The mandatory local partner.
    SAMI is the vehicle through which foreign OEMs access Saudi procurement.
    Any major acquisition requires a SAMI joint venture or offset arrangement.
  Procurement culture: Royal family decision-making at the top level.
    GAMI (General Authority for Military Industries) manages process.
    Crown Prince MBS has consolidated defence procurement decision-making.
  Key platforms: F-15SA, Typhoon, Patriot, LAV, M1A2 Abrams, Black Hawk.
  UK special relationship: Typhoon (BAE Systems), Saudi National Guard training,
    longstanding defence cooperation treaty. UK has privileged access.
  CAATSA note: Saudi interest in Chinese/Russian systems creates secondary
    sanctions exposure for Saudi counterparties. Monitor.
  Compliance note: Yemen conflict — UK Criterion 2 risk documented (2019
    Court of Appeal). ECJU applies enhanced scrutiny. Always risk-assess.

UAE (EDGE Group):
  Budget: ~$22B. High execution.
  EDGE Group: UAE's sovereign defence company. Manages domestic + imports.
    EDGE expects partnership/ToT for significant programmes.
    72+ companies under EDGE umbrella — complex ecosystem.
  Abraham Accords: Israeli suppliers now have direct UAE access.
    Significant shift — Israeli SIGINT, drones, cyber all entering UAE supply chain.
  Procurement: More commercial than Saudi Arabia. More process.
    IDEX (Abu Dhabi, biennial) is the primary BD opportunity.
    Meetings at IDEX carry real commercial weight in UAE.

QATAR:
  Budget: ~$8B. Small but enormously wealthy per capita.
  Typhoon, Rafale, F-15QA: Multiple major combat aircraft purchases simultaneously.
  Procurement: Royal Amiri Air Force makes decisions with Al Thani family oversight.
  UK relationship: Typhoon purchase creates long-term UK relationship.

KUWAIT, BAHRAIN, OMAN: Smaller markets. US-aligned. Process more transparent.
  UK has historical defence cooperation with Oman (Jebel Akhdar veterans).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4.2 GULF — CULTURAL NAVIGATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Relationship building timeline: 6–24 months before a procurement conversation.
Relationship building activities: Majlis attendance, hospitality reciprocation,
  conference and exhibition attendance, intermediary introductions.

The wasta principle: Who you know determines what you can access.
  Wasta = influence/connections. Every Gulf procurement is navigated through wasta.
  Your local partner's wasta is more important than your product's specification.

Meeting culture:
  Meetings start when the senior person decides. Not when the calendar says.
  Multiple visitors may come and go. This is not disrespect.
  Coffee (qahwa) is offered. Accept. Refusing is poor form.
  Business is rarely discussed in the first meeting. The first meeting is assessment.
  Do not rush. Do not show impatience. This is fatal in Gulf BD.

Language: Arabic is always the primary professional language.
  Many Gulf officials speak excellent English but appreciate Arabic engagement.
  "Ahlan wa sahlan" and basic Arabic greetings are always appreciated.
  Formal communications to ministers: Arabic.

Religious observance:
  Halal food and alcohol-free settings for any hosted meal.
  Ramadan: Very different pace. Iftar dinners are excellent relationship-building.
  Prayer times: Build into all meeting schedules.

Gender: Male-dominated military environment. Female team members from Arkmurus
  should be senior and confident — Gulf military culture has evolved but
  still requires careful navigation. Senior female advisors are respected.

IDEX (Abu Dhabi, biennial — February, odd years): The primary Gulf exhibition.
World Defence Show (Riyadh, biennial — 2024, 2026): New Saudi alternative.
DSEI: Gulf delegations attend. Good for pre-IDEX relationship building.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4.3 LEVANT AND JORDAN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Jordan: Small market (~$2B) but strategically positioned. US-aligned.
  Excellent relationship with UK (SAS training, bilateral cooperation).
  Jordan is a trusted US partner — receives significant FMF.
  Entry: UK military relationship is a genuine asset here.

Israel: Arkmurus cannot broker Israeli-origin equipment without careful
  legal structuring. Post-Gaza (2023+): European export licence environment
  has tightened. UK ECJU applying enhanced scrutiny. Monitor carefully.
  Israeli suppliers remain active in Africa and third markets.

Iraq: UN sanctions regime complex remnants. Active reconstruction procurement.
  High corruption risk. Legal advice required before any engagement.

Iran: Comprehensive UK, EU, US sanctions. No engagement. Intelligence monitoring only.
Yemen: Active conflict, UN arms embargo (partial). No engagement.
Syria: Comprehensive sanctions across all Western lists. No engagement.
"""


# =============================================================================
# REGION 5 — ASIA-PACIFIC
# =============================================================================

ASIA_PACIFIC = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — ASIA-PACIFIC NAVIGATION INTELLIGENCE                               ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5.1 SOUTH AND SOUTHEAST ASIA — KEY MARKETS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INDIA — The world's largest arms importer (declining):
  Budget: ~$84B. Strategic Atmanirbhar Bharat (self-reliance) policy.
  Make in India: Foreign OEMs must manufacture in India.
    DIPP licensing required for foreign defence investment.
    Mandatory offset: 30% for contracts above INR 2,000 crore (~$240M).
  Key dynamic: India is diversifying FROM Russia toward Western suppliers.
    Historically 60-70% Russian. Target: 50% domestic by 2027.
    French (Rafale), Israeli (Spyder, Barak), US (C-17, P-8) all present.
  CAATSA S-400: India purchased Russian S-400. US waiver granted but fragile.
    Ongoing CAATSA risk for any further significant Russian defence transactions.
  Procurement: DPP/DAP (Defence Acquisition Procedure 2020) — highly complex.
    Categories: Buy (Indian), Buy & Make (Indian), Buy & Make, Buy (Global).
    Navigating DPP requires Indian legal/BD partner expertise.
  UK-India: JETCO (Joint Economic and Trade Committee) + defence cooperation.
    UK has growing defence relationship with India. DSEI India increasing.

INDONESIA — Southeast Asia's largest market:
  Budget: ~$9B. Growing with maritime and air ambitions.
  Archipelago defence requirement: 17,000+ islands = persistent maritime need.
  Equipment: Diverse — US (F-16), French (Rafale incoming), Russian (Su-27/30),
    German (Type 209 submarine), South Korean (KF-21 joint development).
  Procurement culture: Relationship-based. Presidential-level decisions for
    major programmes. DPR (parliament) oversight.
  Entry: Indonesian defence attaché relationships at key exhibitions.
    INDO Defence (Jakarta, biennial): Primary regional exhibition.

SINGAPORE — The most process-driven market in Asia:
  Budget: ~$12B for ~6M population — exceptional per capita spend.
  MINDEF procurement: Extremely rigorous. Genuine competitive tender.
  STEBudget and DSTA (Defence Science and Technology Agency): Technical evaluators.
  Entry: Must compete properly. No shortcut. Quality and specification win.
    Being UK-registered is a positive — Singapore has strong UK defence ties.
  IDEF equivalent: IMDEX Asia (Singapore, biennial) — maritime/naval.
  Land: Singapore Airshow (biennial) — aviation + defence.

VIETNAM — Fast-growing, transitioning from Russia:
  Budget: ~$6B. Restructuring FROM Russian-dominant toward diversification.
  Post-Ukraine: Russian supply chain disruption accelerating diversification.
  Key needs: Maritime (South China Sea), air defence, coastal defence.
  US normalisation: Arms embargo lifted 2016. Gradual US FMS engagement.
  Entry: Long relationship cultivation. Vietnamese military very hierarchical.
    Language: Vietnamese. French legacy (colonial). English among younger officers.
  Arkmurus angle: UK weapons/systems not contaminated by US ITAR for Vietnam.

PHILIPPINES:
  Budget: ~$4.5B. Active AFP (Armed Forces Philippines) modernisation.
  Threats: Abu Sayyaf, NPA, South China Sea disputes.
  US alliance: VFA (Visiting Forces Agreement) + significant FMF.
  Recent diversification: South Korea (FA-50 jets), Israel (drones).
  Maritime: Strong requirement for OPVs and coast guard assets.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5.2 NORTHEAST ASIA — HIGH VALUE, HIGH COMPLEXITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SOUTH KOREA — Major exporter AND importer:
  Budget: ~$50B. NATO interoperable. ITAR-aware.
  K-defence: South Korea is now a major exporter (K2 MBT, K9 howitzer,
    FA-50 light fighter, KF-21 Boramae, Redback IFV, Chunmoo MLRS).
    Poland ($15B deal), Australia, Norway, UAE — growing export footprint.
  Procurement: Rigorous DAPA (Defence Acquisition Program Administration) process.
  Arkmurus angle: UK broker could facilitate Korean OEM access to African markets.
    Korean platforms: mostly STANAG-compatible, no ITAR, competitive pricing.
    Korean OEMs lack the Africa market relationship network Arkmurus has.

JAPAN:
  Budget: ~$56B (doubling from ~1% to ~2% GDP under Kishida/Kishida successor).
  Historically: Almost no defence exports (Article 9 constitutional limits).
  Now: Gradually exporting. Patriot, sonar, Osprey support.
  Procurement: ATLA (Acquisition, Technology and Logistics Agency) — rigorous.
  Entry: Government-to-government channel only. No commercial broker plays.

AUSTRALIA:
  Budget: ~$35B (AUKUS elevated trajectory).
  AUKUS: Nuclear-powered submarine programme (UK-US-Australia) transforms capability.
  Strong UK relationship: AUKUS Pillar II — advanced capability sharing.
  Procurement: CASG (Capability Acquisition and Sustainment Group) — rigorous.
    Australian process is genuinely competitive and merit-based.
  Arkmurus relevance: Australia-UK defence relationship creates introduction opportunities.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5.3 ASIA-PACIFIC — CULTURAL NAVIGATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Face culture: Across East and Southeast Asia, preserving face (never causing
  public embarrassment) is paramount. Criticism in front of others = fatal.
  Disagreement is expressed indirectly. "That is interesting" may mean "no."

Hierarchy: Extremely important. Business cards are exchanged formally.
  Receive cards with both hands. Study them before setting aside carefully.
  Never write on a business card — it is disrespectful.

Decision speed: Slower than Western markets in most cases.
  Consensus is built behind the scenes before meetings.
  The meeting where a decision is announced is not where the decision was made.

Gift giving: Common in many Asian markets. Know the limits for each country.
  In South Korea and Japan: modest corporate gifts are fine.
  In some Southeast Asian markets: understand corruption law implications.

Key exhibitions:
  Singapore Airshow (biennial, February): Southeast Asia + India
  LIMA (Langkawi, Malaysia, biennial): Southeast Asia maritime/air
  INDO Defence (Jakarta, biennial): Southeast Asia land + maritime
  IMDEX Asia (Singapore, biennial): Maritime/naval
  DVD Korea (Seoul): Korean market
  Aero India (Bangalore, biennial): India aviation + defence
"""


# =============================================================================
# REGION 6 — EUROPE (BEYOND NATO DOCTRINE — BD NAVIGATION)
# =============================================================================

EUROPE_NAVIGATION = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — EUROPE PROCUREMENT NAVIGATION INTELLIGENCE                         ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6.1 EUROPEAN PROCUREMENT — OVERVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Post-2022, European defence spending is surging. NATO 2% GDP commitment is
being met or exceeded by a growing number of members. This is the most
active period of European rearmament since the Cold War.

EU MECHANISMS:
  EDF (European Defence Fund): €8B for 2021-2027 for collaborative R&D.
    Only EU-registered companies or approved partners can access.
  EDIP (European Defence Industry Programme): Production ramp-up funding.
  EDIRPA (European Defence Industry Reinforcement through common Procurement Act):
    Joint procurement incentives. Countries buying together get EU co-funding.
  ReArm Europe / SAFE (2025): €150B+ mobilisation for European defence.
  Arkmurus relevance: EU mechanisms favour EU-based suppliers.
    UK post-Brexit is excluded from most EU defence funding.
    BUT: Third-country exceptions exist for strategic programmes.

UK-specific: UK is not in EU procurement frameworks.
  UK OEMs compete through OCCAR (where member) and bilateral.
  Arkmurus as UK broker: Can still access EU markets commercially.
    EU companies can contract with UK brokers — no restriction.

EASTERN EUROPE — THE HOTTEST PROCUREMENT ENVIRONMENT:
  Poland: $30B+ procurement pipeline. Largest European defence spender % GDP.
    K2 MBT (South Korea), FA-50, HIMARS, Patriot, Abrams — massive programme.
    Polish defence industry (PGZ) growing. Local content required.
  Romania: Growing NATO flank. F-16, Patriot, HIMARS incoming.
    Romanian ANAF/company registry: ARIA has adapter — useful for DD.
  Czech Republic: T-72 → Western transition. F-35 selected.
  Slovakia, Hungary, Baltic states: All on major procurement cycles.

BALKAN STATES:
  Serbia: Complex. EU aspirant but Russian equipment legacy + China engagement.
    Serbian arms exports: Known to re-export. Monitor carefully for diversion.
  North Macedonia, Montenegro, Albania, Bosnia: NATO members/aspirants.
    Small markets but real procurement for NATO integration requirements.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6.2 EUROPEAN BD CULTURE — COUNTRY BY COUNTRY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Germany (BAFA procurement):
  Highly process-driven. Bundeswehr procurement through BWB/BAAINBw.
  Rigorous specification compliance. No shortcuts.
  BAFA (Bundesamt für Wirtschaft und Ausfuhrkontrolle): Export control authority.
  German export controls: Very strict — often more restrictive than UK or France.
  Communication: Direct. Germans say what they mean. No reading between lines needed.
  BD approach: Lead with technical capability and specification compliance.
  KMW, Rheinmetall, Hensoldt: Dominant OEMs. Partnering with them = access.

France (DGA procurement):
  DGA (Direction Générale de l'Armement): Manages all French defence procurement.
  French procurement is heavily tilted toward French industry — THALES,
  Airbus DS, MBDA, Dassault, Naval Group have preferential access.
  For foreign suppliers: You need French industrial partnership or DGA-to-DGA.
  Communication: Formal. French is expected in professional contexts.
    English is spoken but French language engagement signals seriousness.
  COGES: French arms export promotion body. Works with DGA internationally.

Poland (aggressive rearmament):
  PGZ (Polska Grupa Zbrojeniowa): Polish defence conglomerate. Offset partner.
  Any major contract: Local production or technology transfer expected.
  Language: Polish. But English is common in defence procurement.
  Entry: MSPO (Kielce, annual) — the primary Polish defence exhibition.
  Speed: Fast decision-making under current security pressure. BD cycles shorter.

Nordics (Sweden, Norway, Finland, Denmark):
  Small, sophisticated, process-driven markets.
  Export control: Very strict. Nordic countries are rigorous on end-user controls.
  Saab (Sweden), Kongsberg (Norway), Patria (Finland): Regional OEMs.
  Opportunity for Arkmurus: Nordic OEMs seeking African market access
  where Arkmurus has the relationships they lack.
"""


# =============================================================================
# REGION 7 — CENTRAL ASIA AND CAUCASUS
# =============================================================================

CENTRAL_ASIA_CAUCASUS = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — CENTRAL ASIA AND CAUCASUS NAVIGATION INTELLIGENCE                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7.1 CAUCASUS — POST-CONFLICT PROCUREMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AZERBAIJAN:
  Budget: ~$3B. Post-Karabakh victory procurement continuing.
  Turkish relationship: Deepest defence partnership in the region.
    TB2 Bayraktar decisive in 2020 Karabakh war. Turkey = primary supplier.
    Arkmurus Measure Group Turkey angle: Direct relevance here.
  Israeli relationship: Second most important supplier post-2020.
  Russian legacy: Declining — diversifying aggressively.
  Opportunity: Non-Russian, non-Turkish alternatives where Turkey cannot supply.
  Communication: Azerbaijani (Turkic). Russian also functional.
    Elite speak English and Turkish. Formal communications in Azerbaijani.

GEORGIA:
  EU and NATO aspirant. Procurement aligning toward Western standards.
  Budget: ~$500M. Limited but growing.
  US FMF recipient. UK has training relationship.
  Opportunity: A market genuinely transitioning toward Western procurement.
    STANAG compliance increasingly required.
  Communication: Georgian. Russian (declining preference). English among officials.

ARMENIA:
  Post-2020 defeat, diversifying FROM Russia toward France (Bastian system,
  Caesar howitzer) and India (Pinaka MLRS, Akash air defence).
  Very active procurement cycle despite limited budget.
  Relations with Russia: Severely damaged. France emerging as primary partner.
  Opportunity: UK alternative supplier where French/Indian don't cover a category.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7.2 CENTRAL ASIA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Kazakhstan, Uzbekistan, Turkmenistan, Kyrgyzstan, Tajikistan:
  All have Russian-legacy equipment. All face the same post-2022 supply disruption.
  Kazakhstan: Largest market (~$1.5B). Active diversification. Turkey growing.
  Uzbekistan: Growing confidence post-Karimov era. More open to Western engagement.
  Compliance risk: All rank poorly on corruption indices. EDD mandatory.
  Language: Russian remains the military operational language across all five states.
  Entry: Very difficult without existing government relationship.
    These markets require in-country representation and patience.
  Arkmurus priority: LOW. Too complex, too corrupt, too distant from core moat.
    Monitor only unless specific named opportunity emerges.
"""


# =============================================================================
# REGION 8 — NORTH AMERICA
# =============================================================================

NORTH_AMERICA = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — NORTH AMERICA NAVIGATION INTELLIGENCE                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8.1 USA — THE WORLD'S LARGEST DEFENCE MARKET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Arkmurus relevance to US market: Limited direct procurement play.
The US DoD procurement system (FAR/DFARS) is closed to foreign brokers
without DDTC registration, US entity, and significant domestic presence.

BUT the US market matters to Arkmurus in two ways:

1. DSCA/FMS intelligence: US arms transfer decisions affect every market
   Arkmurus operates in. When US approves FMS to Angola, it signals
   relationship and creates follow-on commercial opportunity.
   ARIA monitors: dsca.mil monthly FMS notification releases.

2. ITAR compliance navigation: US-origin components in ANY platform
   being brokered by Arkmurus trigger ITAR compliance requirements.
   Understanding the US export control architecture helps Arkmurus
   advise clients on ITAR-free alternatives and structure deals correctly.

US PROCUREMENT PORTALS TO MONITOR:
  SAM.gov: Federal procurement opportunities
  DSCA.mil: Major arms sale notifications (monthly)
  USASpending.gov: Actual payment data against contracts
  FedBizOpps (now SAM.gov): Historical reference

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8.2 CANADA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Canada: ~$25B budget. Major rearmament cycle underway (NORAD modernisation).
  F-35 selected. Arctic capabilities being prioritised.
  Procurement: PSPC (Public Services and Procurement Canada) — rigorous process.
  Five Eyes relationship: UK has privileged access for intelligence-connected
    programmes. AUKUS Pillar II: Canada seeking association.
  Industrial benefit (IRB/ITB): Local content requirement for major programmes.
  Arkmurus relevance: UK-Canada defence relationship creates introduction
    opportunities. UK OEMs seeking Canadian partners welcome ARIA support.
"""


# =============================================================================
# REGION 9 — CROSS-REGIONAL NAVIGATION MATRIX
# =============================================================================

CROSS_REGIONAL_MATRIX = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — CROSS-REGIONAL NAVIGATION MATRIX                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROCUREMENT CULTURE SPECTRUM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PURE RELATIONSHIP ←────────────────────────────→ PURE PROCESS

Gulf (GCC)          Latin America       Anglophone Africa   Europe (North)
West Africa         SE Asia             Chile, South Korea  Australia
                    Middle East                             USA, Canada

WHAT THIS MEANS FOR BD:
Left side (relationship): Lead with relationship. No cold approaches.
  Local partner mandatory. Months/years before commercial conversation.
  Process exists but decisions made before formal process opens.

Right side (process): Lead with technical capability and specification.
  Respond to tenders properly. Price and compliance win.
  Relationships accelerate but process decides.

Mixed markets (most of the world): Build the relationship to get on the
  shortlist, then win on merit. Both components matter.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BD TIMELINE BY REGION (first relationship to first signed contract)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

6–12 months:   Chile, Singapore, Colombia (if relationship exists),
               Eastern Europe (post-2022 urgency), UAE (if IDEX meeting)

12–18 months:  UK, France, Germany, South Korea, Poland, Kenya, Ghana

18–24 months:  Brazil, Peru, India, Saudi Arabia (without wasta),
               CPLP Africa, Francophone Africa, Turkey

24–36 months:  Japan, Gulf states (new relationship, no wasta),
               Nigeria, Algeria, Russia-adjacent (high risk)

36+ months:    Any market with no prior relationship and complex local
               partner requirement and no government-to-government path

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ITAR SENSITIVITY BY REGION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HIGH ITAR SENSITIVITY (prefer ITAR-free):
  Brazil, India, Algeria, Vietnam, Indonesia, Turkey, Gulf states (some)
  → These markets actively prefer non-ITAR platforms to preserve future
    modification rights and avoid US oversight of their operations.
  → Arkmurus should flag ITAR-free alternatives in every proposal to these markets.

ITAR-COMFORTABLE (US FMS familiarity):
  Colombia, Chile, Morocco, Jordan, Gulf (UAE, KSA for major systems),
  Eastern Europe (NATO new members), Philippines, South Korea
  → US FMS is the established pathway. ITAR is not a dealbreaker.

ITAR-INDIFFERENT (limited US exposure):
  Most of CPLP Africa, Francophone Africa (non-French equipment),
  Central Asia, parts of Latin America
  → ITAR is less understood as a concept. Education opportunity for Arkmurus.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMPLIANCE RISK MAP BY REGION (Arkmurus BD decisions)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CRITICAL RISK (no engagement without specialist legal advice):
  Yemen, Syria, Iran, Russia, Libya, North Korea, Belarus, Myanmar
  Active embargoes or comprehensive sanctions.

HIGH RISK (EDD mandatory, compliance counsel before proceeding):
  DRC (partial embargo), Sudan (Darfur partial), South Sudan,
  Somalia, Venezuela, Nigeria, Mexico, CAR, Libya supply chain

MEDIUM RISK (standard DD + compliance review):
  Nigeria, Ethiopia, Cameroon, Algeria, Egypt, Pakistan,
  Francophone Sahel (Mali, Burkina, Niger — junta context)

STANDARD RISK (normal DD, no special compliance flags):
  Brazil, Chile, Colombia, Peru, CPLP Africa, Gulf (non-sanctioned),
  Southeast Asia (Philippines, Malaysia, Indonesia, Vietnam),
  Eastern Europe (NATO members), Morocco, Kenya, South Africa, India

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARKMURUS MOAT BY REGION — WHERE WE ARE STRONGEST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEEPEST MOAT (irreplaceable advantage):
  CPLP/Lusophone Africa — Portuguese language + network + knowledge.
  No other UK advisory firm matches this depth.

STRONG POSITION (developing):
  Turkey — Measure Group Danışmanlık. Real local presence.
  Francophone Africa — French language capability + CPLP adjacency.

DEVELOPING POSITION (building):
  Latin America — Brazil CPLP bridge is the entry angle.
  Gulf — growing through exhibition engagement.
  Eastern Europe — UK-AUKUS + NATO relationships create access.

EARLY STAGE (monitoring, no active BD):
  Asia-Pacific — South Korea OEM angle (no ITAR) for African markets.
  Central Asia — monitor, no active BD currently.

NOT PRIORITISED:
  USA (market closed to foreign brokers at this stage).
  Japan (government-only procurement, no commercial broker role).
"""


# =============================================================================
# INJECTION FUNCTIONS
# =============================================================================

ALL_REGIONAL_SECTIONS = {
    "latin_america": {
        "content": LATIN_AMERICA,
        "tags": ["Latin-America", "Brazil", "Colombia", "Peru", "Chile",
                 "Argentina", "Mexico", "BD-navigation", "procurement-culture",
                 "relationship-based", "LAAD", "SEACE", "personalismo",
                 "technology-transfer", "EMBRAER", "ITAR-free"],
        "domain": "general",
        "priority": "HIGH",
    },
    "sub_saharan_africa_beyond_cplp": {
        "content": SUB_SAHARAN_AFRICA_BEYOND_CPLP,
        "tags": ["Africa", "Nigeria", "Kenya", "South-Africa", "Ghana",
                 "Anglophone-Africa", "Francophone-Africa", "Denel",
                 "ARMSCOR", "French-withdrawal", "Sahel", "COIN",
                 "BD-navigation", "EDGE-Group-Africa"],
        "domain": "general",
        "priority": "HIGH",
    },
    "north_africa_sahel": {
        "content": NORTH_AFRICA_SAHEL,
        "tags": ["North-Africa", "Algeria", "Morocco", "Egypt", "Libya",
                 "Sahel", "ITAR-sensitivity", "Arabic", "BD-navigation",
                 "Abraham-Accords", "Vision-2030-Africa"],
        "domain": "general",
        "priority": "MEDIUM",
    },
    "middle_east_gulf": {
        "content": MIDDLE_EAST_GULF,
        "tags": ["Gulf", "Saudi-Arabia", "UAE", "Qatar", "Kuwait",
                 "IDEX", "SAMI", "EDGE-Group", "wasta", "GCC",
                 "BD-navigation", "relationship-based", "Arabic",
                 "ITAR-sensitivity", "Jordan", "Israel"],
        "domain": "relationships",
        "priority": "HIGH",
    },
    "asia_pacific": {
        "content": ASIA_PACIFIC,
        "tags": ["Asia-Pacific", "India", "Indonesia", "Singapore",
                 "South-Korea", "Japan", "Australia", "Vietnam",
                 "Philippines", "AUKUS", "Make-in-India", "BD-navigation",
                 "face-culture", "hierarchy", "ITAR-sensitivity",
                 "technology-transfer"],
        "domain": "general",
        "priority": "MEDIUM",
    },
    "europe_navigation": {
        "content": EUROPE_NAVIGATION,
        "tags": ["Europe", "Germany", "France", "Poland", "Nordics",
                 "Eastern-Europe", "EDF", "EDIP", "BAFA", "DGA",
                 "BD-navigation", "NATO", "rearmament", "Balkans",
                 "procurement-culture"],
        "domain": "general",
        "priority": "MEDIUM",
    },
    "central_asia_caucasus": {
        "content": CENTRAL_ASIA_CAUCASUS,
        "tags": ["Central-Asia", "Caucasus", "Azerbaijan", "Georgia",
                 "Armenia", "Kazakhstan", "Turkey-influence",
                 "BD-navigation", "Russian-legacy", "diversification"],
        "domain": "general",
        "priority": "LOW",
    },
    "north_america": {
        "content": NORTH_AMERICA,
        "tags": ["USA", "Canada", "FMS", "DSCA", "ITAR", "DDTC",
                 "North-America", "BD-navigation", "AUKUS",
                 "SAM.gov", "NORAD"],
        "domain": "procurement",
        "priority": "MEDIUM",
    },
    "cross_regional_matrix": {
        "content": CROSS_REGIONAL_MATRIX,
        "tags": ["global", "cross-regional", "procurement-culture",
                 "BD-timeline", "ITAR-sensitivity", "compliance-risk",
                 "Arkmurus-moat", "relationship-vs-process",
                 "BD-navigation", "market-entry"],
        "domain": "general",
        "priority": "HIGH",
    },
}


def inject_to_mem0(mem0_client, user_id: str = "arkmurus-aria") -> dict:
    results = {}
    for name, data in ALL_REGIONAL_SECTIONS.items():
        try:
            mem0_client.add(
                messages=[{"role": "system", "content": data["content"]}],
                user_id=user_id,
                metadata={
                    "type": "regional_navigation_library",
                    "section": name,
                    "tags": data["tags"],
                    "domain": data["domain"],
                    "priority": data["priority"],
                    "source": "ARIA Regional Navigation Intelligence v1.0",
                    "injected_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            results[name] = "SUCCESS"
            logger.info(f"Injected: {name}")
        except Exception as e:
            results[name] = f"ERROR: {e}"
            logger.error(f"Failed [{name}]: {e}")
    success = sum(1 for v in results.values() if v == "SUCCESS")
    return {"sections_injected": success, "total": len(ALL_REGIONAL_SECTIONS), "detail": results}


def inject_to_chromadb(chroma_collection) -> dict:
    """Inject regional nav into ChromaDB with section-boundary-aware chunking.

    Previous version used naive 2000-char splitting which cut mid-sentence.
    Now splits at section boundaries (━━━ lines) so each chunk is a coherent
    topic block.
    """

    total = 0
    for name, data in ALL_REGIONAL_SECTIONS.items():
        content = data["content"]
        # Split at section boundaries (━━━ header lines)
        raw_sections = re.split(r"\n━{20,}\n", content)
        chunks = []
        current = ""
        for section in raw_sections:
            section = section.strip()
            if not section:
                continue
            if len(current) + len(section) > 2500 and current:
                chunks.append(current.strip())
                current = section
            else:
                current += "\n\n" + section if current else section
        if current.strip():
            chunks.append(current.strip())

        for i, chunk in enumerate(chunks):
            if len(chunk) < 100:
                continue
            cid = hashlib.md5(f"regional_{name}_{i}".encode()).hexdigest()
            try:
                chroma_collection.add(
                    documents=[chunk], ids=[cid],
                    metadatas=[{
                        "source": f"regional_nav:{name}",
                        "source_type": "regional_navigation",
                        "domain": data["domain"],
                        "priority": data["priority"],
                        "tags": json.dumps(data["tags"]),
                    }]
                )
                total += 1
            except Exception:
                pass
    return {"total_chunks": total, "status": "COMPLETE"}


# =============================================================================
# KNOWLEDGE BASE INJECTION — Seeds facts into ARIA's Redis knowledge store
# =============================================================================

async def inject_to_knowledge_base() -> dict:
    """Extract key facts from regional intelligence and store in ARIA's KB.

    Does NOT dump the full text — extracts actionable facts that
    searchKnowledge() can surface during chat. Each fact is stored with
    source='regional_nav:<region>' and confidence='ASSESSED'.
    """
    from . import knowledge as kb

    facts_stored = 0
    errors = 0

    for name, data in ALL_REGIONAL_SECTIONS.items():
        content = data["content"]
        tags = data["tags"]
        # Extract key lines that look like actionable facts
        # (lines with budgets, entry strategies, key programmes, exhibitions)
    
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if not line or len(line) < 40 or len(line) > 500:
                continue
            # Skip decorative lines
            if line.startswith("━") or line.startswith("╔") or line.startswith("╚") or line.startswith("║"):
                continue
            # Keep lines with factual markers
            is_fact = any([
                re.search(r"Budget:\s*~?\$", line),
                re.search(r"Defence budget", line, re.I),
                re.search(r"Entry:", line),
                re.search(r"Key programmes?:", line, re.I),
                re.search(r"Exhibition:", line, re.I),
                re.search(r"Procurement culture", line, re.I),
                re.search(r"https?://", line),
                re.search(r"ITAR", line),
                re.search(r"embargo", line, re.I),
                re.search(r"BD entry", line, re.I),
                line.startswith("NEVER ") or line.startswith("ALWAYS "),
            ])
            if is_fact:
                # Derive topic from section name + first country/keyword match
                topic = name.replace("_", " ").title()
                for tag in tags:
                    if tag.replace("-", " ").lower() in line.lower():
                        topic = tag.replace("-", " ")
                        break
                try:
                    await kb.store_fact(
                        topic=topic,
                        content=line,
                        source=f"regional_nav:{name}",
                        confidence="ASSESSED",
                    )
                    facts_stored += 1
                except Exception:
                    errors += 1

    logger.info("Regional navigation KB injection: %d facts stored, %d errors", facts_stored, errors)
    return {"facts_stored": facts_stored, "errors": errors, "status": "COMPLETE"}


# =============================================================================
# RAG STORE INJECTION — Bulk-seeds ChromaDB via ARIA's existing rag_store
# =============================================================================

async def inject_to_rag_store() -> dict:
    """Inject regional navigation into ARIA's RAG store (aria_documents collection).

    Uses the existing rag_store.ingest_document() which handles chunking,
    embedding, and staleness tracking.
    """
    try:
        from . import rag_store
    except ImportError:
        return {"status": "SKIPPED", "reason": "rag_store not available"}

    if not hasattr(rag_store, "ingest_document"):
        return {"status": "SKIPPED", "reason": "rag_store.ingest_document not available"}

    total_chunks = 0
    for name, data in ALL_REGIONAL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                text=data["content"],
                source=f"regional_nav:{name}",
                source_type="manual",
                title=f"Regional Navigation — {name.replace('_', ' ').title()}",
                market=name,
                extra_metadata={
                    "domain": data["domain"],
                    "priority": data["priority"],
                },
            )
            total_chunks += result.get("chunks", 0)
        except Exception as e:
            logger.warning("RAG injection failed for %s: %s", name, e)

    logger.info("Regional navigation RAG injection: %d chunks", total_chunks)
    return {"total_chunks": total_chunks, "status": "COMPLETE"}


# =============================================================================
# COUNTRY → REGION MAPPING (used by get_regional_context)
# =============================================================================

_COUNTRY_TO_REGION: dict[str, str] = {}


def _build_country_map() -> None:
    """Build a lowercase country/keyword → section name mapping."""
    _explicit = {
        # Latin America
        "brazil": "latin_america", "brasil": "latin_america",
        "colombia": "latin_america", "peru": "latin_america",
        "chile": "latin_america", "argentina": "latin_america",
        "mexico": "latin_america", "ecuador": "latin_america",
        "latin america": "latin_america", "latam": "latin_america",
        "laad": "latin_america", "embraer": "latin_america",
        # Sub-Saharan Africa (beyond CPLP)
        "nigeria": "sub_saharan_africa_beyond_cplp",
        "kenya": "sub_saharan_africa_beyond_cplp",
        "south africa": "sub_saharan_africa_beyond_cplp",
        "ghana": "sub_saharan_africa_beyond_cplp",
        "ethiopia": "sub_saharan_africa_beyond_cplp",
        "cote d'ivoire": "sub_saharan_africa_beyond_cplp",
        "ivory coast": "sub_saharan_africa_beyond_cplp",
        "senegal": "sub_saharan_africa_beyond_cplp",
        "cameroon": "sub_saharan_africa_beyond_cplp",
        "drc": "sub_saharan_africa_beyond_cplp",
        "congo": "sub_saharan_africa_beyond_cplp",
        "denel": "sub_saharan_africa_beyond_cplp",
        "armscor": "sub_saharan_africa_beyond_cplp",
        "francophone africa": "sub_saharan_africa_beyond_cplp",
        "anglophone africa": "sub_saharan_africa_beyond_cplp",
        # North Africa
        "algeria": "north_africa_sahel", "morocco": "north_africa_sahel",
        "egypt": "north_africa_sahel", "libya": "north_africa_sahel",
        "tunisia": "north_africa_sahel", "sahel": "north_africa_sahel",
        "north africa": "north_africa_sahel",
        # Middle East / Gulf
        "saudi arabia": "middle_east_gulf", "saudi": "middle_east_gulf",
        "uae": "middle_east_gulf", "qatar": "middle_east_gulf",
        "kuwait": "middle_east_gulf", "bahrain": "middle_east_gulf",
        "oman": "middle_east_gulf", "jordan": "middle_east_gulf",
        "iraq": "middle_east_gulf", "iran": "middle_east_gulf",
        "yemen": "middle_east_gulf", "syria": "middle_east_gulf",
        "israel": "middle_east_gulf", "gulf": "middle_east_gulf",
        "gcc": "middle_east_gulf", "idex": "middle_east_gulf",
        "wasta": "middle_east_gulf", "sami": "middle_east_gulf",
        "edge group": "middle_east_gulf",
        # Asia-Pacific
        "india": "asia_pacific", "indonesia": "asia_pacific",
        "singapore": "asia_pacific", "vietnam": "asia_pacific",
        "philippines": "asia_pacific", "south korea": "asia_pacific",
        "korea": "asia_pacific", "japan": "asia_pacific",
        "australia": "asia_pacific", "aukus": "asia_pacific",
        "asean": "asia_pacific", "asia pacific": "asia_pacific",
        "make in india": "asia_pacific",
        # Europe
        "germany": "europe_navigation", "france": "europe_navigation",
        "poland": "europe_navigation", "romania": "europe_navigation",
        "czech": "europe_navigation", "slovakia": "europe_navigation",
        "hungary": "europe_navigation", "nordic": "europe_navigation",
        "sweden": "europe_navigation", "norway": "europe_navigation",
        "finland": "europe_navigation", "denmark": "europe_navigation",
        "balkans": "europe_navigation", "serbia": "europe_navigation",
        "eastern europe": "europe_navigation",
        "edf": "europe_navigation", "edip": "europe_navigation",
        "bafa": "europe_navigation", "dga": "europe_navigation",
        # Caucasus / Central Asia
        "azerbaijan": "central_asia_caucasus",
        "georgia": "central_asia_caucasus",
        "armenia": "central_asia_caucasus",
        "kazakhstan": "central_asia_caucasus",
        "uzbekistan": "central_asia_caucasus",
        "caucasus": "central_asia_caucasus",
        "central asia": "central_asia_caucasus",
        # North America
        "usa": "north_america", "united states": "north_america",
        "canada": "north_america", "fms": "north_america",
        "dsca": "north_america",
    }
    _COUNTRY_TO_REGION.update(_explicit)


_build_country_map()


# =============================================================================
# SEARCH INDEX (built once at import time)
# =============================================================================

_SEARCH_INDEX: list[dict] = []


def _build_search_index() -> None:
    """Pre-compute searchable entries from all sections (same pattern as
    procurement_knowledge.py)."""
    for section_name, data in ALL_REGIONAL_SECTIONS.items():
        text_lower = data["content"].lower()
        tags_lower = " ".join(t.lower() for t in data["tags"])
        _SEARCH_INDEX.append({
            "id": section_name,
            "search_text": f"{text_lower} {tags_lower}",
            "content": data["content"],
            "tags": data["tags"],
            "domain": data["domain"],
            "priority": data["priority"],
        })


_build_search_index()


# =============================================================================
# PUBLIC API — get_regional_context(query)
# =============================================================================

def get_regional_context(query: str) -> str:
    """Return relevant regional navigation intelligence for a given query.

    Two-stage retrieval:
      1. Direct country/keyword → region mapping (fast, precise)
      2. Fallback to token-level search across all sections

    Returns a formatted string (max ~2500 chars) for inclusion in the
    ARIA system prompt addenda. Empty string if no matches.

    Follows the exact pattern of nato_standards.get_nato_context() and
    procurement_knowledge.get_procurement_context().
    """
    if not query or not query.strip():
        return ""

    query_lower = query.lower().strip()
    tokens = [t for t in query_lower.split() if len(t) > 2]

    # ── Stage 1: direct country/keyword → region mapping ──────────────
    matched_regions: set[str] = set()
    for keyword, region in _COUNTRY_TO_REGION.items():
        if keyword in query_lower:
            matched_regions.add(region)

    # Always include cross-regional matrix if we matched any region
    if matched_regions:
        matched_regions.add("cross_regional_matrix")

    # ── Stage 2: fallback token search if no direct match ─────────────
    if not matched_regions:
        scored: list[tuple[int, dict]] = []
        for entry in _SEARCH_INDEX:
            score = 0
            for token in tokens:
                if token in entry["search_text"]:
                    score += 10
                    # Boost for tag matches (more specific)
                    if any(token in t.lower() for t in entry["tags"]):
                        score += 15
            if score > 0:
                scored.append((score, entry))

        if not scored:
            return ""

        scored.sort(key=lambda x: -x[0])
        matched_regions = {s[1]["id"] for s in scored[:2]}

    # ── Build output — extract relevant subsections, cap at ~2500 chars ──

    parts: list[str] = []
    total_chars = 0
    MAX_CHARS = 2500

    for region_id in matched_regions:
        if region_id not in ALL_REGIONAL_SECTIONS:
            continue
        section = ALL_REGIONAL_SECTIONS[region_id]
        content = section["content"]

        # Find sub-sections that match the query best
        sub_blocks = re.split(r"\n━{20,}\n", content)
        for block in sub_blocks:
            block = block.strip()
            if not block or len(block) < 50:
                continue
            # Score this sub-block against query tokens
            block_lower = block.lower()
            relevance = sum(1 for t in tokens if t in block_lower)
            if relevance == 0 and len(matched_regions) > 1:
                continue  # Skip irrelevant sub-blocks when we have multiple matches

            # Take the block, truncated if needed
            excerpt = block[:1200] if len(block) > 1200 else block
            if total_chars + len(excerpt) > MAX_CHARS:
                break
            parts.append(excerpt)
            total_chars += len(excerpt)

        if total_chars >= MAX_CHARS:
            break

    if not parts:
        return ""

    header = "[REGIONAL NAVIGATION INTELLIGENCE — Arkmurus BD operational guidance]\n"
    result = header + "\n\n".join(parts)
    # R-F994 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="regional_navigation",
        summary=f"Regional context: {len(matched_regions)} regions matched",
        detail=f"Matched regions: {', '.join(matched_regions)}. Tokens: {len(tokens)}.",
        confidence="ASSESSED",
        source_id="regional_navigation:R-F994",
    )
    return result


if __name__ == "__main__":
    print("ARIA Regional Navigation Intelligence v1.0")
    print("=" * 55)
    total_chars = sum(len(v["content"]) for v in ALL_REGIONAL_SECTIONS.values())
    print(f"\n{len(ALL_REGIONAL_SECTIONS)} regional modules | {total_chars:,} characters\n")
    for name, data in ALL_REGIONAL_SECTIONS.items():
        priority = data["priority"]
        marker = "🔴" if priority == "HIGH" else "🟡" if priority == "MEDIUM" else "⚪"
        print(f"  {marker} {name.replace('_', ' ').title()}")
    print(f"\nInjection: inject_to_mem0(client) + inject_to_chromadb(collection)")
    print("\nContext test:")
    for test in ["Brazil procurement", "Nigeria defence", "Saudi Arabia IDEX", "Poland rearmament"]:
        ctx = get_regional_context(test)
        print(f"  '{test}' → {len(ctx)} chars")
