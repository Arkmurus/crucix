"""ARIA Procurement

 Intelligence Knowledge Module.

Permanent knowledge base covering global defence procurement lifecycle,
procurement portal mastery (TED, SAM.gov, Contracts Finder, UNGM, AfDB),
US Foreign Military Sales (FMS) process, offset and industrial
participation requirements, budget execution rate analysis, and
procurement intelligence analysis techniques.

Ingested into ARIA's knowledge store and RAG store at startup so that
procurement-related queries automatically surface relevant domain
context.  The ``get_procurement_context`` helper performs lightweight
keyword matching for prompt-time injection (similar to
``nato_standards.get_nato_context``).

All sources: open-source, publicly verifiable."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
logger = logging.getLogger("aria.procurement_knowledge")


# =============================================================================
# KNOWLEDGE BLOCKS
# =============================================================================

PROCUREMENT_LIFECYCLE = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 GLOBAL DEFENCE PROCUREMENT LIFECYCLE AND PROCESS                  \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

1. UNIVERSAL PROCUREMENT LIFECYCLE

Every defence procurement -- regardless of country -- follows variants of:

PHASE 1 -- CAPABILITY DEFINITION:
  Operational requirement identified (force commander / doctrine review)
  Staff Target drafted (broad operational capability need)
  Staff Requirement refined (specific capability in operational context)
  ARIA ENTRY POINT: Informal consultation, workshops, site visits.
  This is where relationships determine who shapes the requirement.

PHASE 2 -- MARKET SURVEY / REQUEST FOR INFORMATION (RFI):
  Contracting authority issues RFI to industry.
  Purpose: understand market, refine requirements, identify suppliers.
  RFI is NOT a contract -- responses create no obligation.
  ARIA ACTION: Respond to all relevant RFIs. Shape the requirement.
  If you are not responding, competitors are.

PHASE 3 -- TENDER PUBLICATION:
  Request for Proposal (RFP) / Invitation to Tender (ITT) published.
  Contains: technical specification, evaluation criteria, timeline.
  Key documents: Statement of Work (SoW), Contract Data Requirements List.
  EU/UK: must be published on TED / Contracts Finder above threshold.
  USA: published on SAM.gov.
  Many countries: national procurement portal (often opaque).

PHASE 4 -- EVALUATION:
  Technical evaluation: does it meet the specification?
  Commercial evaluation: price, lifecycle cost, offset compliance.
  Risk evaluation: financial stability, delivery track record, legal.
  Combined scoring: weighted formula (e.g., 60% technical, 40% price).

PHASE 5 -- AWARD:
  Preferred bidder notification (standstill period in EU/UK -- 10 days).
  Standstill: unsuccessful bidders can challenge before contract signed.
  Contract signature: commitment. Prior to this: no obligations.
  Award notice: published on TED/CF/SAM.gov for competitive tenders.

PHASE 6 -- CONTRACT EXECUTION:
  Milestones, payments, GQA inspections, acceptance testing.
  EUC submitted at this stage -- before physical transfer of goods.
  Delivery: CIF, DAP, EXW Incoterms relevant to defence.

PHASE 7 -- THROUGH-LIFE SUPPORT:
  Spare parts, training, maintenance contracts.
  Often larger in value over full life than initial procurement.
  ARIA: through-life support is recurring revenue -- prime BD opportunity.

2. CONTRACT TYPES -- DEFINITIONS

FIRM FIXED PRICE (FFP):
  Contractor bears all cost risk. Fixed price regardless of actual cost.
  Used when: scope is well-defined and risk manageable.
  Most defence equipment procurement uses FFP.

COST PLUS FIXED FEE (CPFF):
  Government reimburses actual allowable costs + fixed fee.
  Used when: scope is uncertain, R&D, complex development.
  Risk on government -- contractor has limited cost incentive.

INDEFINITE DELIVERY, INDEFINITE QUANTITY (IDIQ):
  Establishes terms for delivering unspecified quantity over period.
  Used by: US federal government extensively.
  Task orders placed against the IDIQ contract.
  ARIA: Many US AFRICOM security assistance contracts are IDIQ.

FRAMEWORK AGREEMENT:
  Sets terms and conditions for multiple call-off orders.
  EU law: max 4 years, competition among framework suppliers.
  Common in: UK, EU for recurring supply (ammunition, spare parts).
  ARIA: Once on a framework = guaranteed future orders. Valuable.

SINGLE SOURCE / SOLE SOURCE:
  Awarded without competition. Justified by: unique capability,
  national security, urgency, only source.
  Common in: classified programmes, urgent operational requirements.
  ARIA: sole source awards rarely appear on procurement portals.

G2G (Government-to-Government):
  One government contracts with another government for defence goods.
  Examples: US FMS, UK government-facilitated exports, French DGA.
  Advantage: bypasses normal commercial competition. Relationship-based.
  ARIA: The highest-value route -- requires government-level relationship.
"""

PROCUREMENT_PORTALS_MASTERY = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 PROCUREMENT PORTAL MASTERY \u2014 SEARCHING AND INTERPRETING           \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

TED -- Tenders Electronic Daily (EU)

URL: https://ted.europa.eu
Coverage: All EU above-threshold public procurement + EEA.
Defence: Article 346 TFEU means classified defence contracts are excluded.
BUT: Most defence supplies, services, and works appear as they are not
classified -- they are just military procurement.

HOW TO SEARCH TED FOR DEFENCE:
  CPV codes (Common Procurement Vocabulary) for defence:
    35000000-4: Security, fire-fighting, police, defence equipment
    35100000-5: Emergency and security equipment
    35200000-6: Police equipment
    35400000-8: Military vehicles and associated parts
    35500000-9: Warships and associated parts
    35600000-0: Air warfare equipment
    35700000-1: Military electronic systems
    35800000-2: Personal and support equipment
    35900000-3: Miscellaneous military equipment

  Country filtering: filter by buyer country to find relevant contracts.
  For Angola, Mozambique: search EU-funded programmes purchasing locally.

TED NOTICE TYPES:
  Contract Notice (CN): active tender -- opportunity is open.
  Contract Award Notice (CAN): already awarded -- shows winner + value.
  Prior Information Notice (PIN): advance notice -- opportunity coming.
  ARIA reads CAN backwards: who won what, at what value, from whom.

SAM.GOV -- US Federal Procurement

URL: https://sam.gov
Coverage: All US federal procurement. Includes all DoD and DSCA/FMS.
NAICS codes for defence:
  336411: Aircraft manufacturing
  336413: Aircraft parts
  336992: Military armoured vehicles
  332994: Small arms
  325920: Explosives

REGISTRATION REALITY -- FOREIGN ENTITIES
  SAM.gov registration for a non-US entity requires:
    1. UEI (Unique Entity Identifier) -- issued by SAM during registration
    2. NATO Commercial and Government Entity (NCAGE) code -- obtained from
       the supporting country's CAGE authority (UK: the MoD DE&S CAGE team)
       BEFORE SAM registration can be completed
    3. Notarised signature on the Entity Administrator letter
  Realistic end-to-end timeline: 4-6 weeks. Do NOT quote 1-2 weeks to
  anyone -- that is the US-domestic registration timeline.

ARKMURUS-SPECIFIC NOTE:
  Arkmurus already holds a NATO CAGE code. SAM.gov registration for
  Arkmurus does NOT require the CAGE-acquisition step and can proceed
  on the shorter UEI-only timeline (2-3 weeks typical). Operator should
  confirm current CAGE validity before starting SAM registration.

DSCA Foreign Military Sales awards: appear on SAM.gov with recipient country.
Search: "Foreign Military Sales" + country name for relevant awards.
USASpending.gov: companion site showing actual payments against contracts.

CONTRACTS FINDER -- UK Government (shop window)

URL: https://www.contractsfinder.service.gov.uk
Coverage: UK public sector procurement above 10,000 GBP.
UK MoD contracts: all non-classified above-threshold appear here as
notices. However, CF is the publication surface, not the workflow.
FCDO Security programmes: Conflict Stability and Security Fund (CSSF)
contracts appear here -- often Africa-facing security assistance.
ARIA filters by: buyer "Ministry of Defence", keyword search.

UNGM -- UN Global Marketplace

URL: https://www.ungm.org
Coverage: UN system procurement worldwide.
Security: UNDP, UNOPS, UNPOL, DPKO supply chain tenders appear.
Registration: vendors must register to bid -- free, takes 1-2 weeks
for Level 1 (most vendors); higher levels (UNPD Level 2) require
audited financials and take longer.
ARIA: UNGM is underutilised. Significant volume of
security-related procurement for peacekeeping missions in Africa.

AFRICAN DEVELOPMENT BANK (AfDB)

URL: https://www.afdb.org/en/projects-and-operations/procurement
Coverage: AfDB-financed procurement in African nations.
Includes: maritime security, border management, some equipment.
STEP system: Standard Electronic Procurement System.

DACON -- Database of Consultants:
  Login: https://clientconnection.afdb.org (Client Connection)
  For advisory / services roles (not goods procurement) register on
  AfDB DACON as a consulting firm. This is the correct route for
  Arkmurus-style market-access, regulatory, and capability advisory
  mandates tied to AfDB-financed programmes. DACON registration
  sits alongside, not inside, the main STEP tender portal.

  WORKFLOW -- primarily DOWNLOAD-based:
    AfDB publishes consultancy Terms of Reference as downloadable
    documents. Firms download the TOR, prepare proposals offline,
    and upload the completed submission. ARIA RULE on any DACON
    opportunity: track the internal proposal-pack deadline as the
    real deadline -- the portal's event log alone is not enough.

ARIA: AfDB procurement in security and maritime is live and growing.
Angola, Mozambique, Senegal, Nigeria all active borrowers.
"""

US_FMS_MASTERY = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 US FOREIGN MILITARY SALES (FMS) MASTERY                           \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

Source: DSCA.mil / SAMM (Security Assistance Management Manual)

FMS PROCESS -- COMPLETE LIFECYCLE

STEP 1 -- REQUEST: Foreign nation submits Letter of Request (LOR) to
  DSCA (Defence Security Cooperation Agency).
  LOR specifies: items requested, quantity, end use, recipient unit.

STEP 2 -- PRICE AND AVAILABILITY (P&A): US government checks availability.
  P&A is a non-binding estimate. Takes 30-180 days.

STEP 3 -- LETTER OF OFFER AND ACCEPTANCE (LOA):
  The core FMS document. US government formally offers sale.
  Contains: price, delivery schedule, terms and conditions.
  Foreign nation accepts = FMS case is activated.
  LOA pricing: cost + administrative surcharge (3.5% typically).

STEP 4 -- CONGRESSIONAL NOTIFICATION:
  Major defence articles above thresholds require 15-30 day
  Congressional notification before LOA can be signed.
  Thresholds: $14M for major defence equipment to most nations.
  ARIA tracks: DSCA major arms sale notifications at dsca.mil.

STEP 5 -- DELIVERY AND PAYMENT:
  Payment: advance, in trust fund, before delivery.
  Delivery: managed by US government (no commercial shipping haggling).
  End-use monitoring: Blue Lantern program checks equipment use.

FMS vs DCS (Direct Commercial Sales):
  FMS: US government as intermediary. US government liability.
    Slower but: government-backed warranty, no ITAR licence needed.
  DCS: Commercial contract between company and foreign buyer.
    Faster but: company manages export licence, all liability.
    Requires ITAR licence from DDTC.

FMS TRACKING -- WHERE TO FIND INTELLIGENCE:
  DSCA.mil/resources: monthly FMS notification press releases.
  USASpending.gov: shows actual FMS delivery payments.
  State.dept 655 Report: annual commercial and military export report.
  ARIA: search "DSCA major arms sales [country] [year]" for tracking.

FMS AND AFRICA -- KEY INTELLIGENCE

Angola: Limited US FMS history. Russian/Chinese legacy. No SOFA.
  US engagement: primarily through IMET (training) and limited EXBS.
  FMS is not the primary route for Angola -- relationship-based procurement.

Mozambique: Growing US FMS engagement post-Cabo Delgado.
  EUTM Mozambique + US training creates equipment demand.
  FMS track record: small arms, comms, some vehicles.

Cape Verde: US PSI partner. Some FMS and EDA (Excess Defence Articles).
  EDA (Excess Defence Articles): surplus US military equipment
  transferred at reduced cost. ARIA: EDA is a procurement opportunity.

Guinea-Bissau: Very limited FMS. Primarily UN/EU funded security.
"""

OFFSET_AND_INDUSTRIAL_PARTICIPATION = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 OFFSET AND INDUSTRIAL PARTICIPATION IN DEFENCE PROCUREMENT        \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

Offset = industrial compensation required by the buying country
as a condition of the arms purchase. ALSO CALLED:
  Industrial Participation (IP)
  Defence Industrial Participation (DIP)
  Counter Trade
  Industrial Benefits
  Local Content Requirement (LCR)

OFFSET TYPES

DIRECT OFFSET:
  Related to the purchased item itself.
  Examples: Local assembly of a portion of equipment,
  technology transfer for maintenance, local sourcing of sub-components.
  Value: typically 1:1 or 1.2:1 multiplier.

INDIRECT OFFSET:
  Unrelated to the purchased item.
  Examples: Investment in local industry, export facilitation,
  training programmes, technology transfer to unrelated sector.
  Value: lower multipliers (0.5:1 to 1:1 typically).

TECHNOLOGY TRANSFER:
  Seller provides technology, patents, know-how to buyer nation.
  Can be direct or indirect offset.
  Increasingly demanded by: India, Brazil, Turkey, South Africa.

IN-COUNTRY PRODUCTION:
  Portion of items manufactured locally.
  Examples: AK assembly in Angola (historical), ammunition production.
  Creates long-term dependency on seller for IP and technical support.

KEY OFFSET REQUIREMENTS BY MARKET

Saudi Arabia (Vision 2030):
  SAMI (Saudi Arabian Military Industries) leads.
  Target: 50% local content in defence by 2030.
  Requirement: significant offset for any major procurement.
  ARIA: any major Saudi procurement must include SAMI partnership.

India (DAP 2020 -- Defence Acquisition Procedure):
  Make in India: preference for domestic production.
  Category: Buy Indian (Indian designed), Buy & Make Indian preferred.
  Offset: applies to major imports -- technology transfer expected.

South Africa (NIPP -- National Industrial Participation Programme):
  Managed by DTI (Department of Trade and Industry).
  Applies to: government procurement above R10 million with foreign content.
  BBBEE (Broad-Based Black Economic Empowerment) requirement additional.

Brazil:
  Strategic programmes: technology transfer and local production required.
  EMBRAER: historically beneficiary of technology transfer from partnerships.
  ITAR-free Brazilian platforms (A-29 Tucano) avoid US offset complications.

Turkey (SSB):
  Domestic industry preference (SAHA members preferred).
  Significant offset for foreign procurement of major systems.
  Aselsan, Roketsan, BMC preferred for local content.

Angola:
  No formal offset regime but political expectation of local benefit.
  Investment in Angola (beyond equipment) creates goodwill and future access.
  Practical: training, maintenance capacity building, local employment.

CPLP African States Generally:
  No formal offset laws but: procurement decisions influenced by
  economic benefit to buying country.
  Non-price factors include: training provision, technology transfer,
  local assembly potential, financing terms, political alignment.
  ARIA: frame all CPLP proposals with economic benefit narrative.

BUDGET EXECUTION RATE -- THE MOST MISUNDERSTOOD PROCUREMENT METRIC

DEFINITION: Percentage of allocated defence budget actually spent.
CRITICAL: Allocated != Spent != Paid.

WHY IT MATTERS FOR BD:
  A country with $500M defence budget but 40% execution rate
  actually spends $200M. A country with $200M budget and 80% execution
  spends $160M. The second country is the better BD prospect.

TYPICAL EXECUTION RATES:
  USA, UK, France: 90-95% execution (mature procurement systems)
  Angola: historically 50-70% (administrative bottlenecks)
  Nigeria: historically 40-65% (corruption, bureaucracy)
  Mozambique: historically 55-75% (capacity constraints)
  Saudi Arabia: 70-85% (improving with Vision 2030)
  Gulf states generally: higher than Africa -- cash-rich, improving systems

HOW TO FIND EXECUTION DATA:
  IMF Article IV consultations: published annually, include budget analysis.
  World Bank Country Reports: budget execution analysis.
  SIPRI Data: total military spending (actual expenditure).
  IISS Military Balance: spending data by country.
  National audit office reports (where published).

ARIA BD APPLICATION:
  Before sizing any market opportunity, check execution rate.
  Low execution = long payment delays even when contracts are signed.
  Structure contracts: milestone payments reduce execution risk.
  Escrow from initial advance: protects against payment failure.
"""

PROCUREMENT_INTELLIGENCE_ANALYSIS = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 PROCUREMENT INTELLIGENCE ANALYSIS TECHNIQUES                      \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

READING A CONTRACT AWARD NOTICE -- INTELLIGENCE EXTRACTION

A contract award notice contains:
  1. Contracting authority: who bought it.
  2. Contract description: what was bought (sometimes vague).
  3. CPV codes: what category (cross-check for verification).
  4. Contract value: how much was paid.
  5. Winning contractor: who won.
  6. Number of offers received: how competitive was it.
  7. Date: when it was awarded.
  8. Duration: contract length.

ARIA INTELLIGENCE EXTRACTION FROM AWARD NOTICES:
  -> Who is the incumbent? (creates relationship intelligence)
  -> What was the contract value? (sizes the market)
  -> When does it expire? (predicts future tender date)
  -> How many bids were received? (indicates competition level)
  -> What was bought? (identifies procurement pattern)

IF ONLY ONE BID WAS RECEIVED: either sole source or open competition
with effectively only one qualified supplier -- incumbent advantage strong.

IF HIGH VALUE + SINGLE BIDDER: sole source or restricted competition.
Opportunity: position as alternative before next tender.

PROCUREMENT PIPELINE MAPPING:
  Award date + contract duration = expected re-tender date.
  Example: 3-year contract awarded Jan 2024 -> re-tender ~Q4 2026.
  ARIA flags these 12 months before predicted re-tender.

DEMAND SIGNAL IDENTIFICATION

LEADING INDICATORS that procurement will follow:
  New defence white paper / strategy published
  UN Security Council resolution mandating capability
  New military doctrine adopted
  Loss of equipment in conflict (creates replacement demand)
  International training mission identifies capability gap
  Defence budget increase in parliamentary/ministerial speech
  New bilateral defence agreement with equipment-holding state
  NATO capability target assigned to member nation
  EUTM/EUMAM mission established (creates equipment standardisation pressure)
  Recent conflict incident exposing capability gap (e.g. IED attack -> MRAP demand)

ARIA applies these signals to predict procurement before it is published:
  -> EUTM Mozambique established -> Mozambique will need STANAG-compatible equipment
  -> Cabo Delgado IED incidents -> MRAP requirement predictable
  -> Angola increased defence budget -> near-term procurement likely
  -> Cape Verde EEZ enforcement challenge -> OPV procurement predictable
"""


UK_NATO_BROKER_PORTALS = """
╔══════════════════════════════════════════════════════════════════════╗
║  ARIA — UK MoD + NATO BROKER-REGISTER PORTALS                         ║
╚══════════════════════════════════════════════════════════════════════╝

These are the portals where Arkmurus ITSELF registers — distinct from
the country-market tender portals ARIA monitors for intelligence.
Broker positioning here is active bidder / framework participant,
not external observer.

DEFENCE CONTRACTS PORTAL (DCP / "DSP") — UK MoD actual workflow

URL (login):    https://contracts.mod.uk/esop/ogc-host/public/mod/web/login.html
URL (base):     https://contracts.mod.uk
Platform:       Bravo Solution eSourcing, hosted under the OGC/MoD domain
Coverage: UK Ministry of Defence commercial workflow — ITTs, PQQs,
bidder Q&A, submissions. Contracts Finder shows the public notice;
the Defence Contracts Portal is where the tender is actually run. Any
above-threshold MoD procurement (GBP 139,688 for services / supplies;
higher for works) lives here once a supplier is past the notice stage.

Registration: free, via supplier self-registration from the login page.
Requires a DUNS number and UK company identity.

Filters relevant to Arkmurus: Land, Air, Maritime, Weapons,
Information, Strategic Enablers. Opportunity feeds can be saved
against keywords.

ARIA RULE: any brief on a UK MoD tender MUST check BOTH Contracts
Finder (discovery) AND contracts.mod.uk (live status, closing date,
clarification log). Missing the MoD portal is a common reason for
"we didn't see the tender" failures.

NOTE: the earlier "defencesourcing.mod.uk" URL was incorrect — the
actual portal lives at contracts.mod.uk. Corrected 2026-04-19.

NSPA eSOURCING — NATO Support and Procurement Agency

URL: https://www.nspa.nato.int  (eProcurement portal linked from site)
Coverage: NATO-wide procurement on behalf of Allied nations and NATO
commands. Covers support, sustainment, fuel, munitions framework,
ISR services, medical, engineering.

Registration: via NSPA SRBPS (Source Selection Registration and
Bidding Preparation System). Requires a NATO Commercial and Government
Entity (NCAGE / CAGE) code for the supplier and eligibility from the
supplier's host nation.

ARKMURUS ELIGIBILITY: Arkmurus already holds a UK CAGE code, so the
NSPA registration prerequisite is satisfied. Operator still needs to
complete the SRBPS supplier profile + declare capability codes.

ARIA POSITIONING: NSPA is the right register for NATO-framework
sustainment, multinational consolidation buys, and allied-partner
capability programmes. Broker angle — intelligence + market-access
for NSPA-awarded primes seeking local partners in Arkmurus-reach
regions (Lusophone Africa, Gulf, West Africa, Balkans).

CAGE / NCAGE — REUSE ACROSS FRAMEWORKS

One CAGE code unlocks: NSPA, NATO framework contracts, SAM.gov (as
NCAGE replaces the CAGE-acquisition step in SAM registration),
several European MoD supplier databases. Because Arkmurus holds a
CAGE, the "4–6 weeks to register as a foreign entity" standard
timeline does NOT apply to Arkmurus — factor this into any briefing
that cites foreign-entity registration lead times.
"""


# =============================================================================
# SECTION REGISTRY
# =============================================================================

PROCUREMENT_SECTIONS: dict[str, dict] = {
    "procurement_lifecycle": {
        "content": PROCUREMENT_LIFECYCLE,
        "tags": [
            "procurement", "lifecycle", "tender", "RFP", "contract",
            "FMS", "DCS", "framework", "G2G",
        ],
        "domain": "procurement",
    },
    "procurement_portals": {
        "content": PROCUREMENT_PORTALS_MASTERY,
        "tags": [
            "procurement", "TED", "SAM.gov", "contracts-finder",
            "UNGM", "AfDB", "CPV-codes", "award-notice",
            "UEI", "NCAGE", "CAGE", "arkmurus-cage",
            "DACON", "afdb-dacon", "consultant-registration",
        ],
        "domain": "procurement",
    },
    "uk_nato_broker_portals": {
        "content": UK_NATO_BROKER_PORTALS,
        "tags": [
            "DSP", "defence-sourcing-portal", "defencesourcing",
            "uk-mod-portal", "uk-mod-tender",
            "NSPA", "nspa-esourcing", "nato-procurement", "nato-framework",
            "CAGE", "NCAGE", "arkmurus-cage", "cage-reuse",
            "broker-register", "broker-portal",
            "SRBPS",
        ],
        "domain": "procurement",
    },
    "us_fms_process": {
        "content": US_FMS_MASTERY,
        "tags": [
            "procurement", "FMS", "DSCA", "LOA", "DCS",
            "congressional-notification", "ITAR", "Africa-FMS",
        ],
        "domain": "procurement",
    },
    "offset_industrial_participation": {
        "content": OFFSET_AND_INDUSTRIAL_PARTICIPATION,
        "tags": [
            "procurement", "offset", "industrial-participation",
            "local-content", "technology-transfer", "budget-execution",
        ],
        "domain": "procurement",
    },
    "procurement_intelligence_analysis": {
        "content": PROCUREMENT_INTELLIGENCE_ANALYSIS,
        "tags": [
            "procurement", "intelligence", "contract-award",
            "demand-signals", "pipeline-mapping", "analysis",
        ],
        "domain": "procurement",
    },
}


# =============================================================================
# SEARCH INDEX (built once at import time)
# =============================================================================

_SEARCH_INDEX: list[dict] = []


def _build_search_index() -> None:
    """Pre-compute a flat list of searchable entries from all sections."""
    for section_name, data in PROCUREMENT_SECTIONS.items():
        text_lower = data["content"].lower()
        tags_lower = " ".join(t.lower() for t in data["tags"])
        _SEARCH_INDEX.append({
            "id": section_name,
            "search_text": f"{text_lower} {tags_lower}",
            "content": data["content"],
            "tags": data["tags"],
            "domain": data["domain"],
        })


_build_search_index()


# =============================================================================
# KEYWORD CONTEXT RETRIEVAL
# =============================================================================

def get_procurement_context(query: str) -> str:
    """Return relevant procurement knowledge for a given query.

    Performs keyword matching against the knowledge blocks and returns
    a formatted string suitable for inclusion in ARIA prompts. Similar
    to ``nato_standards.get_nato_context``.

    Args:
        query: Free-text query (e.g., "FMS process Angola",
               "TED CPV codes", "offset requirements").

    Returns:
        Formatted multi-line string with matching sections.
        Empty string if no matches found.
    """
    if not query or not query.strip():
        return ""

    query_lower = query.lower().strip()
    tokens = query_lower.split()

    scored: list[tuple[int, dict]] = []
    for entry in _SEARCH_INDEX:
        score = 0
        # Exact section-name match
        if query_lower in entry["id"].lower():
            score += 100
        # Token matching
        for token in tokens:
            if len(token) < 2:
                continue
            if token in entry["search_text"]:
                score += 10
            if token in entry["id"].lower():
                score += 20
        if score > 0:
            scored.append((score, entry))

    if not scored:
        return ""

    # Sort by score descending, take top 3
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    parts: list[str] = [
        "=== ARIA Procurement Knowledge Context ===",
        "",
    ]
    for _score, entry in top:
        parts.append(f"--- {entry['id'].replace('_', ' ').upper()} ---")
        # Truncate to first ~5000 chars to keep prompt size manageable
        # (sections were growing past the old 3000-char cap and losing
        # their tail content — DACON, AfDB detail, offset annexes).
        content = entry["content"].strip()
        if len(content) > 5000:
            content = content[:5000] + "\n[... truncated ...]"
        parts.append(content)
        parts.append("")

    return "\n".join(parts)


# =============================================================================
# INGESTION (called at startup from _seed_knowledge_bg)
# =============================================================================

async def ingest_to_knowledge() -> dict:
    """Store procurement knowledge into the knowledge + RAG stores.

    Ingests all sections as permanent CONFIRMED facts via
    ``knowledge.store_fact``, and as RAG documents via
    ``rag_store.ingest_document`` for semantic retrieval.

    Follows the same pattern as ``dd_case_library.ingest_all_cases()``
    and ``nato_standards.ingest_to_knowledge()``.
    """
    from . import knowledge, rag_store

    results: dict = {}
    total_chunks = 0

    for section_name, data in PROCUREMENT_SECTIONS.items():
        try:
            # Permanent knowledge fact
            await knowledge.store_fact(
                topic=f"procurement_{section_name}",
                content=data["content"],
                source=f"procurement_knowledge:{section_name}",
                confidence="CONFIRMED",
            )

            # RAG ingest for semantic search
            result = await rag_store.ingest_document(
                text=data["content"],
                source=f"procurement_knowledge:{section_name}",
                source_type="procurement_knowledge",
                title=f"Procurement Knowledge -- {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/procurement_knowledge/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "procurement_knowledge",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info(
                "Procurement section ingested: %s (%d chunks)",
                section_name, chunks,
            )
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error(
                "Procurement ingestion failed [%s]: %s", section_name, e,
            )

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info(
        "Procurement knowledge ingestion: %d/%d sections, %d total chunks",
        success, len(PROCUREMENT_SECTIONS), total_chunks,
    )

    # R-F996 — wire to brain
    wire_success(
        module="procurement_knowledge",
        summary="Procurement knowledge",
        source_id="procurement_knowledge:R-F996",
    )
    return {
        "sections_ingested": success,
        "total_sections": len(PROCUREMENT_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }
