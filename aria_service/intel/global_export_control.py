# =============================================================================
# ARIA — Global Export Control Framework
# aria_service/intel/global_export_control.py
#
# The universal export-control layer. ARIA is a GLOBAL defence broking
# advisor — every transaction she touches intersects one or more of these
# regimes. This module is the reference library she consults on every
# licensing, classification, end-use, and jurisdictional-nexus question,
# regardless of which country the deal concerns.
#
# SCOPE: Every major national and multilateral export-control regime
#        relevant to defence and dual-use transfers, with the
#        enforcement hooks, list references, and routing logic a
#        working broker actually needs.
#
# ARIA APPLICATION: Activates automatically when
#   - classifying a product (ML/ECCN/USML/CCL/ML-equivalent)
#   - assessing licensing path (SIEL/OIEL/OGEL/SITCL/DSP-5/TAA/MLA/DU)
#   - identifying which regime has primary jurisdiction
#   - assessing re-export, re-transfer, or technology-transfer risk
#   - advising on extraterritorial reach (ITAR long-arm, UK trade controls)
#   - checking multilateral regime coverage (Wassenaar, MTCR, NSG, AG, CWC)
#
# ALL SOURCES: Open-source, verifiable, citable.
# Primary: UK ECJU, US BIS/DDTC, EU Commission, Wassenaar Secretariat,
#          MTCR, NSG, AG, OPCW, national export-control authorities.
# =============================================================================

import logging

logger = logging.getLogger("aria.global_export_control")


# =============================================================================
# SECTION 1 — THE UK EXPORT CONTROL SYSTEM (ECJU / HMRC / DBT)
# =============================================================================

UK_EXPORT_CONTROL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  UK EXPORT CONTROL — SECL / SITCL / SIEL / OIEL / OGEL / TRADE CONTROL   ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The UK is one of the three major export-control jurisdictions worldwide
(alongside the US and EU). It combines a civilian Export Control Act (ECA
2002), secondary legislation (Export Control Order 2008), and trade
(brokering) controls on activities done from or by UK persons abroad. As
of 2026 the administering authority is the Export Control Joint Unit
(ECJU) inside the Department for Business and Trade (DBT, formerly DIT).

ARIA must reason about UK export control on every transaction where
(a) the broker is UK-based, (b) goods transit the UK, (c) UK-origin
technology is involved, or (d) a UK person is involved in arranging or
negotiating the transfer anywhere in the world. The last point is the
critical broker trap: UK trade controls apply to UK nationals operating
from third countries on goods that never touch the UK.
from .engine_wiring import wired

─────────────────────────────────────────────────────────────────────────────
1.1 THE UK STRATEGIC EXPORT CONTROL LISTS (SECL)
─────────────────────────────────────────────────────────────────────────────

The SECL is consolidated and published by DBT. It is the UK's master
list and combines:

  A. UK Military List (UKML) — categories ML1 through ML22
  B. UK Dual-Use List — mirroring (with UK adjustments) the EU Dual-Use
     Regulation Annex I (which itself implements Wassenaar, MTCR, NSG,
     Australia Group, and CWC lists)
  C. UK Security and Human Rights List — non-ML items controlled for
     human-rights reasons (e.g. cyber-surveillance technology,
     restraint equipment)
  D. UK Radioactive Sources List — radioactive source security controls

UKML categories ML1-ML22 mirror the Wassenaar Munitions List:
  ML1  Smoothbore weapons <20mm; sub-machine guns; accessories
  ML2  Smoothbore weapons >=20mm; arms with muzzle energy >= 10kJ
  ML3  Ammunition and fuze-setting devices
  ML4  Bombs, torpedoes, rockets, missiles, related equipment
  ML5  Fire-control, surveillance, warning equipment
  ML6  Ground vehicles and components
  ML7  Chemical/biological toxic agents, riot-control agents
  ML8  Energetic materials (propellants, explosives, pyrotechnics)
  ML9  Vessels of war, special naval equipment, accessories
  ML10 Aircraft, UAVs, aero-engines, aircraft equipment
  ML11 Electronic equipment, spacecraft, components (military)
  ML12 High-velocity kinetic energy weapon systems
  ML13 Armoured or protective equipment
  ML14 Specialised training equipment
  ML15 Imaging equipment, countermeasure equipment
  ML16 Forgings, castings, unfinished products
  ML17 Miscellaneous equipment, materials, libraries
  ML18 Production equipment for ML items
  ML19 Directed-energy weapon systems
  ML20 Cryogenic and superconductive equipment
  ML21 Software for military items
  ML22 Technology for military items

Dual-use categories (Category 0-9, matching Wassenaar and EU Annex I):
  Category 0  Nuclear materials, facilities, equipment
  Category 1  Special materials and related equipment (toxins, composites)
  Category 2  Materials processing
  Category 3  Electronics
  Category 4  Computers
  Category 5  Telecommunications (Part 1) and Information Security (Part 2)
  Category 6  Sensors and lasers
  Category 7  Navigation and avionics
  Category 8  Marine
  Category 9  Aerospace and propulsion

─────────────────────────────────────────────────────────────────────────────
1.2 LICENCE TYPES — THE WORKING PORTFOLIO
─────────────────────────────────────────────────────────────────────────────

SIEL — Standard Individual Export Licence
  Scope: One exporter, one consignee, specified goods, specified quantity,
  specified destination, fixed validity (typically 24 months).
  Use when: one-off deliveries, high-sensitivity goods, any item to a
  restricted destination, any ML item to a new customer.
  Application via SPIRE (https://www.spire.trade.gov.uk).

SITCL — Standard Individual Trade Control Licence
  Scope: One trader (the UK broker), one supplier-consignee pair,
  specified goods, specified quantity, specified movement.
  Use when: a UK person arranges the transfer of controlled goods between
  TWO NON-UK parties. This is the Arkmurus case for most deals. ECJU
  publishes Cat A / Cat B / Cat C categorisation — Cat A (torture
  equipment, long-range missiles) is always SITCL, no exceptions.
  Permitted only between OIEL-approved destinations for most Cat B/C
  trades; Cat A requires individual approval regardless.

OIEL — Open Individual Export Licence
  Scope: Pre-approved exporter, defined list of goods, defined list of
  destinations, repeat shipments allowed without per-shipment licence.
  Validity: up to 5 years. Must report shipments half-yearly.
  Use when: ongoing supply relationship to approved customers; reduces
  administrative burden.

OGEL — Open General Export Licence
  Scope: Published by ECJU, exporter registers and self-certifies
  compliance with conditions. No per-shipment application.
  Main OGELs relevant to defence BD:
    OGEL (Military Goods, Government or NATO End-Use)
    OGEL (Military Goods, Industry End-Use — for manufacturers)
    OGEL (Dual-Use Items — for low-risk destinations)
    OGEL (Exports after Repair/Replacement under Warranty)
    OGEL (Exports for Exhibition, Demonstration, Testing)
  Use when: goods/destinations match the OGEL conditions exactly.
  Critical: OGELs can be suspended overnight (e.g. Russia 2022).

SITEL — Standard Individual Transhipment Licence
  Scope: Goods entering and leaving the UK without clearance into UK
  free circulation. Requires full notification.

OGTCL — Open General Trade Control Licence
  Brokering equivalent of OGEL. Lower-risk brokering activities allowed
  without per-transaction application, subject to registration and
  compliance conditions.

TEC — Temporary Export Certificate
  For military goods leaving the UK for demonstration/trial/repair with
  intent to return.

─────────────────────────────────────────────────────────────────────────────
1.3 THE UK CONSOLIDATED CRITERIA (CRITERION 1-8)
─────────────────────────────────────────────────────────────────────────────

Every SIEL/SITCL/OIEL application is assessed against the UK Strategic
Export Licensing Criteria ("Consolidated Criteria"), published 2021 and
based on the EU Common Position 2008/944/CFSP which the UK continues to
apply post-Brexit with minor amendments.

Criterion 1 — International obligations and commitments
  UN embargoes, EU embargoes (by extension for UK), OSCE arms control,
  non-proliferation commitments, chemical weapons convention obligations.
  Absolute bar if triggered.

Criterion 2 — Respect for human rights and international humanitarian
law in the country of final destination
  Assesses risk that equipment would be used for internal repression,
  serious violations of IHL, torture, or other grave breaches.
  Requires "clear risk" assessment — the threshold that has been
  tested in UK courts (e.g. R (Campaign Against Arms Trade) v Secretary
  of State [2019] EWCA Civ 1020 on Saudi Arabia / Yemen).

Criterion 3 — Internal situation in the country of final destination
  Active internal conflict, tensions, stability. Refusal risk if goods
  would fuel or prolong internal conflict.

Criterion 4 — Regional peace, security, and stability
  Risk of aggression against a neighbour, regional arms race, unresolved
  territorial disputes.

Criterion 5 — National security of UK and allies
  Risk to UK or allied forces from the exported capability (e.g. reverse
  engineering, technology leak to adversary).

Criterion 6 — Behaviour of the buyer country with respect to international
community, terrorism, international law
  State sponsorship of terrorism, compliance with international law.

Criterion 7 — Diversion risk
  Risk of diversion to unintended end-user, re-export, or undesirable
  end-use. This is where end-use certificates, end-user undertakings,
  and delivery verification matter most.

Criterion 8 — Sustainable development impact on recipient country
  Goods/services inappropriate to technical/economic capacity, diverting
  resources from development. Rarely a lone refusal ground but often a
  conditioning factor.

PRACTICAL ARIA RULE: Criteria 2 and 7 are the most common refusal
grounds. If a brief involves a destination with active IHL concerns,
ARIA must flag Criterion 2 risk explicitly. If the end-user relationship
is unclear or layered, ARIA must flag Criterion 7 risk and require an
end-use certificate before any SIEL/SITCL path is recommended.

─────────────────────────────────────────────────────────────────────────────
1.4 UK TRADE (BROKERING) CONTROLS — THE CRITICAL LONG-ARM
─────────────────────────────────────────────────────────────────────────────

The UK regulates brokering under the Trade in Goods (Control) Order 2003
and subsequent amendments, plus the Export Control Order 2008 Parts 4
and 4A. Trade controls apply to UK persons wherever they are operating.

The UK person test:
  - British citizens
  - Permanent UK residents
  - Companies incorporated in the UK
  - Companies otherwise registered/operating in the UK

If a UK person "arranges or negotiates" the supply, delivery,
acquisition, or disposal of controlled goods, a trade control licence is
required even if:
  - The goods never enter the UK
  - The goods originated outside the UK
  - The buyer and seller are both non-UK
  - The UK person is physically in a third country at the time

Category A goods (absolute controls — always SITCL, no OGTCL possible):
  Cluster munitions
  Anti-personnel landmines (under Ottawa)
  Long-range missiles >=300km
  Torture and repression equipment (leg-irons, certain electro-shock)
  Chemical and biological weapons materials

Category B goods: most other military goods. OGTCL available for low-risk
destinations; SITCL otherwise.

Category C goods: small arms and light weapons and their ammunition —
SITCL required for most destinations; no OGTCL coverage.

PRACTICAL ARIA RULE: A UK person operating in (say) Dubai who negotiates
sale of Turkish UAVs to an African buyer is committing an offence
without a SITCL, even though no goods, money, or people touch UK soil.
This is the most common broker compliance trap and ARIA must catch it
proactively on any brokering brief.

─────────────────────────────────────────────────────────────────────────────
1.5 SANCTIONS AND ANTI-MONEY LAUNDERING (SAMLA 2018 / OFSI)
─────────────────────────────────────────────────────────────────────────────

SAMLA 2018 (Sanctions and Anti-Money Laundering Act) is the UK's
primary post-Brexit autonomous sanctions authority. Under it HMT/OFSI
imposes financial sanctions, and DBT/ECJU handles trade sanctions and
export controls interaction.

Key regimes ARIA must check on every transaction:
  Russia (The Russia (Sanctions) (EU Exit) Regulations 2019)
  Belarus (Belarus (Sanctions) (EU Exit) Regulations 2019)
  Iran (multiple regimes — Iran nuclear, Iran human rights)
  North Korea (The Democratic People's Republic of Korea (Sanctions)
    (EU Exit) Regulations 2019)
  Syria, Libya, Somalia, Sudan, South Sudan, Yemen, Zimbabwe
  Venezuela, Nicaragua, Myanmar (Burma)
  Global Human Rights (autonomous thematic regime)
  Global Anti-Corruption (autonomous thematic regime)
  Cyber (autonomous thematic regime)
  ISIL/Al-Qaida (UN-mandated, UK-implemented)

OFSI general licences: standing licences that pre-authorise certain
categories of activity (e.g. humanitarian exceptions, legal fees,
frozen assets management). ARIA should check whether any applies.

─────────────────────────────────────────────────────────────────────────────
1.6 ARIA DECISION FLOW — "CAN I BROKER THIS DEAL UNDER UK LAW?"
─────────────────────────────────────────────────────────────────────────────

Step 1: Is the good on the SECL?
  YES → export/trade control applies. Continue.
  NO  → non-controlled; check sanctions + WMD catch-all.

Step 2: What is the destination status?
  UK sanctions target / embargoed → hard stop, refer to OFSI/ECJU.
  UK-listed high-risk → enhanced Criterion 2/3/4/7 review.
  Permitted → continue.

Step 3: Is a UK person arranging/negotiating?
  YES → trade controls apply even if goods are extraterritorial. SITCL
         or OGTCL evaluation required.
  NO  → physical export licence (SIEL/OIEL/OGEL) path.

Step 4: End-user verification
  EUC (End-User Certificate) from buying government required.
  Delivery Verification Certificate (DVC) may be required post-delivery.
  Diversion risk assessment per Criterion 7.

Step 5: Licence route selection
  Low-volume, one-off → SIEL/SITCL.
  Multi-shipment to approved destinations → OIEL / OGTCL.
  Published general authorisation fits → OGEL / OGTCL (self-register).

Step 6: Record-keeping and audit
  5-year retention under regulations. Shipment reports where required.
"""


# =============================================================================
# SECTION 2 — THE US EXPORT CONTROL SYSTEM (ITAR / EAR / OFAC / FMS)
# =============================================================================

US_EXPORT_CONTROL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  US EXPORT CONTROL — ITAR / EAR / OFAC / FMS / CAATSA                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The US has the most extraterritorial export-control regime in the world.
Even a non-US broker handling a non-US transaction can be caught by US
law if US-origin content, US persons, or US dollars are involved. Every
global defence broker must master the US system whether or not they
ever directly touch US soil.

US export control is split across three primary pillars:

  ITAR — International Traffic in Arms Regulations (22 CFR 120-130)
         Administered by the State Department Directorate of Defense
         Trade Controls (DDTC). Covers defence articles, services, and
         technical data on the US Munitions List (USML).

  EAR  — Export Administration Regulations (15 CFR 730-774)
         Administered by the Commerce Department Bureau of Industry and
         Security (BIS). Covers dual-use items, certain military items
         moved off the USML, and commercial items with security
         relevance. Items are classified on the Commerce Control List
         (CCL) by Export Control Classification Number (ECCN).

  OFAC — Office of Foreign Assets Control (Treasury)
         Administers US sanctions programmes (country, sectoral,
         thematic, SDN list) and prohibits transactions with designated
         persons and entities.

Plus Foreign Military Sales (FMS) — the government-to-government
purchasing channel run by the Defense Security Cooperation Agency
(DSCA), and Direct Commercial Sales (DCS) — the private licensed
channel under ITAR.

─────────────────────────────────────────────────────────────────────────────
2.1 ITAR — THE US MUNITIONS LIST (USML), CATEGORIES I-XXI
─────────────────────────────────────────────────────────────────────────────

USML categories (22 CFR 121):
  I     Firearms and related articles
  II    Guns and armament
  III   Ammunition and ordnance
  IV    Launch vehicles, missiles, rockets, torpedoes, bombs, mines
  V     Explosives, propellants, incendiary agents, their constituents
  VI    Surface vessels of war and special naval equipment
  VII   Ground vehicles
  VIII  Aircraft and related articles
  IX    Military training equipment and training
  X     Personal protective equipment
  XI    Military electronics
  XII   Fire control, laser, imaging, and guidance equipment
  XIII  Materials and miscellaneous articles
  XIV   Toxicological agents, including chemical and biological agents
  XV    Spacecraft and related articles
  XVI   Nuclear weapons–related articles
  XVII  Classified articles, technical data, and defence services
  XVIII Directed energy weapons
  XIX   Gas turbine engines and associated equipment
  XX    Submersible vessels and related articles
  XXI   Articles, technical data, and defence services not otherwise
        enumerated (catch-all)

Export Control Reform (ECR) moved many commercial-derived items from
USML to CCL (the "600-series" ECCNs) starting in 2013. ARIA must
recognise that a helmet, ground vehicle part, or aircraft component
that used to be ITAR may now be EAR 600-series — same sensitivity,
different agency, different licensing path.

─────────────────────────────────────────────────────────────────────────────
2.2 ITAR LICENSING CHANNELS
─────────────────────────────────────────────────────────────────────────────

DSP-5 — Permanent Export of Unclassified Defense Articles and Related
        Technical Data
DSP-85 — Permanent Export of Classified Defense Articles and Related
         Classified Technical Data
DSP-61 — Temporary Import of Unclassified Defense Articles
DSP-73 — Temporary Export of Unclassified Defense Articles
DSP-6 — Application for Registration under ITAR (all US manufacturers,
        exporters, brokers must register)

TAA — Technical Assistance Agreement
  Controls the provision of defence services and transfer of technical
  data to foreign persons. Required for engineering support, training,
  integration work, or any hand-holding on defence articles.

MLA — Manufacturing License Agreement
  Required when granting a foreign person the right to manufacture a
  defence article or its components.

WDA — Warehouse and Distribution Agreement
  For in-country stocking and distribution of ITAR articles.

DEFENSE SERVICES — any furnishing of assistance (training, design,
development, engineering, manufacture, operation) to foreign persons on
defence articles, including when the service itself is not classified.
This is the ITAR trap that catches consulting and training work.

─────────────────────────────────────────────────────────────────────────────
2.3 EAR / BIS / THE COMMERCE CONTROL LIST (CCL)
─────────────────────────────────────────────────────────────────────────────

Organised by ECCN (Export Control Classification Number), a 5-character
code: category digit (0-9), product group letter (A-E), and three
reason-for-control-specific digits.

Categories (match Wassenaar dual-use categories):
  0  Nuclear and miscellaneous
  1  Materials, chemicals, microorganisms, toxins
  2  Materials processing
  3  Electronics
  4  Computers
  5  Telecommunications and information security
  6  Sensors and lasers
  7  Navigation and avionics
  8  Marine
  9  Aerospace and propulsion

Product groups:
  A  Systems, equipment, components
  B  Test, inspection, and production equipment
  C  Materials
  D  Software
  E  Technology

Reasons for control (the 600-series military items, the 900-series
multilateral items, AT-only items for Iran/Syria/Cuba/Sudan/North
Korea, etc.). Each ECCN has a Country Chart indicating which reasons
apply to which destinations.

EAR99 — catch-all for items subject to EAR but not on the CCL. Most
commercial goods. Still subject to embargoes and SDN screening.

Key EAR licensing paths:
  Standard individual export licence (BIS-748P via SNAP-R)
  Licence exceptions (LVS, GBS, TSR, TMP, RPL, GOV, TSU, ENC, APR,
  GFT, BAG, AVS, CCD, STA — each with conditions)
  No licence required (NLR) — applicable to many EAR99 items to
  non-embargoed destinations

─────────────────────────────────────────────────────────────────────────────
2.4 OFAC — US SANCTIONS (THE ENFORCEMENT HAMMER)
─────────────────────────────────────────────────────────────────────────────

OFAC administers country and thematic sanctions programs. The most
aggressive US enforcement tool is the SDN (Specially Designated
Nationals) List: any US person, or any non-US person with US-nexus
contact, is prohibited from dealing with SDN-listed entities or
individuals, their 50%-or-more owned subsidiaries (the "50% rule"), and
anyone designated as acting on their behalf.

US nexus triggers — when does OFAC apply to a non-US broker?
  1. US persons involved (citizens, residents, US-incorporated
     companies, US subsidiaries, US-based employees)
  2. Transactions denominated or cleared in US dollars (catches the
     correspondent-bank chain — nearly every international wire in USD)
  3. Goods, technology, or services of US origin (even minimal
     content — de minimis analysis required for EAR, no de minimis
     for ITAR)
  4. Transit through US territory, free-trade zones, or airspace
  5. Provision of US-origin financial, legal, insurance, shipping,
     or other services

CAATSA Section 231 — the Russia sector trap
  Countering America's Adversaries Through Sanctions Act (CAATSA),
  Section 231: any person (including non-US) engaging in a "significant
  transaction" with the Russian defence or intelligence sectors is
  exposed to US sanctions. Turkey's S-400 purchase was the most
  visible trigger. ARIA must flag any proposed deal that involves
  Russian-origin defence systems, sub-systems, training, or support
  under CAATSA 231.

─────────────────────────────────────────────────────────────────────────────
2.5 FMS VS DCS — THE TWO CHANNELS US ALLIES CAN BUY THROUGH
─────────────────────────────────────────────────────────────────────────────

FMS (Foreign Military Sales) — government-to-government. The US
government (DSCA) acts as purchaser on behalf of the foreign
government. Advantages: predictable pricing, US oversight, standard
terms, access to classified systems. Disadvantages: slower, less
flexible, subject to Congressional notification (CN) thresholds
($14M major defence equipment, $50M defence articles/services, $200M
for NATO/major non-NATO allies under Arms Export Control Act).

DCS (Direct Commercial Sales) — private US contractor to foreign
customer, licensed under ITAR (DSP-5 / DSP-85). More flexible, faster,
but the foreign customer must manage the relationship and the US
contractor holds the licence risk.

Broker note: many deals use a hybrid — an Arkmurus-style broker
introduces and shapes the requirement, the end user files an FMS Letter
of Request (LOR), and DSCA fulfils. The broker earns through a separate
advisory or industrial-participation arrangement.

─────────────────────────────────────────────────────────────────────────────
2.6 ARIA US-NEXUS DECISION FLOW
─────────────────────────────────────────────────────────────────────────────

Step 1: Does any USML item feature in the transaction?
  YES → ITAR jurisdiction. DDTC licensing. Registration required for US
        manufacturers/exporters/brokers. Foreign brokers dealing in ITAR
        articles may require ITAR brokering registration (22 CFR 129).
  NO  → continue to EAR check.

Step 2: Does any EAR-controlled item or US-origin content feature?
  YES → BIS jurisdiction. CCL classification. Country chart check.
  NO  → no EAR licence required; sanctions-only check.

Step 3: Does the transaction touch a US person, US-cleared USD payment,
or US-origin services?
  YES → OFAC SDN screening + sanctions regime check. CAATSA 231 check
        if any Russian defence nexus.
  NO  → OFAC risk lower but still check SDN (the list is de facto
        treated as authoritative worldwide by major banks).

Step 4: End-user verification
  DSP-83 (non-transfer and use certificate) required for many ITAR
  exports. End-User Monitoring (Blue Lantern for ITAR, Sentinel for
  EAR 600-series) may follow delivery.

Step 5: Documentation
  All ITAR transactions require registration and per-licence filings.
  EAR transactions need proper AES/EEI filing (Automated Export
  System / Electronic Export Information). BIS-711 statement of
  ultimate consignee and purchaser required.
"""


# =============================================================================
# SECTION 3 — THE EU EXPORT CONTROL SYSTEM
# =============================================================================

EU_EXPORT_CONTROL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  EU EXPORT CONTROL — DUAL-USE REG 2021/821 / COMMON MILITARY LIST        ║
║  COMMON POSITION 2008/944/CFSP / CUSTOMS UNION                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The EU operates a hybrid system: dual-use items are regulated at EU
level through a directly-applicable regulation, while military items
remain subject to national authorities applying a common EU framework
(the Common Position 2008/944/CFSP) with a Common Military List as the
anchor. Each EU member state issues its own military licences but must
respect common refusal criteria and consult on denials.

Post-Brexit, the UK has left this framework but continues to apply
equivalent criteria (Consolidated Criteria, see Section 1). The UK and
EU diverge in practice on several categories (cyber-surveillance, UAVs,
precision munitions guidance, human-rights listings).

─────────────────────────────────────────────────────────────────────────────
3.1 REGULATION (EU) 2021/821 — THE RECAST DUAL-USE REGULATION
─────────────────────────────────────────────────────────────────────────────

Replaced Council Regulation (EC) No 428/2009 on 9 September 2021.

Directly applicable in all EU member states (no national transposition
needed for the core rules). Covers:

  Annex I — the EU Dual-Use List (categories 0-9, mirroring Wassenaar
    and the other multilateral regimes with EU-specific additions)
  Annex II — EU General Export Authorisations (EU GEAs) — pre-approved
    licence routes for low-risk destinations. EU GEA 001 covers exports
    to Australia, Canada, Iceland, Japan, New Zealand, Norway,
    Switzerland/Liechtenstein, UK, US (with conditions).
  Annex IV — intra-EU transfer controls for most-sensitive items
    (genuinely cross-border prior-authorisation requirement)

Key 2021 recast additions:
  a) Cyber-surveillance items — new catch-all control on items that
     "may be intended" for use in internal repression or serious
     violations of human rights (Article 5). This catches commercial
     items that would otherwise fall outside the Annex I list.
  b) Human-security end-use catch-all — Article 4 expanded to cover
     serious violations of human rights, grave violations of IHL, and
     acts of terrorism.
  c) Harmonised enforcement — information-sharing, peer review, a
     formal coordination mechanism (DUCG).
  d) Transmissible software and technology — clarified treatment of
     cloud-delivered and intangible transfers.

ARIA CRITICAL: The EU post-2021 cyber-surveillance control (Article 5)
is broader than UK or US equivalents. A UK-made cyber-surveillance item
destined for an EU re-exporter may require an EU-level assessment that
the UK exporter does not anticipate.

─────────────────────────────────────────────────────────────────────────────
3.2 EU COMMON MILITARY LIST (CML)
─────────────────────────────────────────────────────────────────────────────

Adopted annually by the Council (most recent update CML 2024). Member
states refer to it when applying their national military export laws.
The CML uses ML1-ML22 headings parallel to the Wassenaar Munitions List
and the UK Military List but with EU-specific interpretations and
clarifications.

Notable UK-EU divergences to watch:
  ML5 (fire-control, surveillance, warning) — EU CML notes broader
    scope on passive imaging in recent updates.
  ML11 (military electronics) — EU CML may list items the UK has not
    reflected in annual updates.
  ML15 (imaging equipment) — EU CML 2024 expanded airborne-imaging
    clarifications.

─────────────────────────────────────────────────────────────────────────────
3.3 COMMON POSITION 2008/944/CFSP — THE EIGHT CRITERIA
─────────────────────────────────────────────────────────────────────────────

EU member states (and the UK, equivalent-basis post-Brexit) assess
licences against the same eight criteria used in the UK Consolidated
Criteria (see Section 1.3):

  1. International obligations/commitments
  2. Human rights and IHL in destination
  3. Internal situation of destination
  4. Regional peace, security, stability
  5. National security of member state(s) and allies
  6. Behaviour of buyer country
  7. Diversion risk
  8. Sustainable development / technical and economic capacity

Denial consultation: a member state proposing to authorise a licence
that another member state has previously denied for an "essentially
identical transaction" must consult and provide reasoning before
issuing. The EU maintains a denial database (not public).

─────────────────────────────────────────────────────────────────────────────
3.4 EU DEFENCE INDUSTRIAL POLICY — NEW FUNDING AND CONTROL SURFACES
─────────────────────────────────────────────────────────────────────────────

ARIA must also reason about the EU's defence industrial policy
instruments, which create BD opportunities and new compliance touch-
points:

EDF (European Defence Fund) — joint R&D funding, €8bn 2021-2027
PESCO (Permanent Structured Cooperation) — binding member-state
  commitments, project portfolio including common platforms
EDA (European Defence Agency) — capability-planning and cooperation
EDIRPA (European Defence Industry Reinforcement through common
  Procurement Act) — €500m short-term joint procurement
ASAP (Act in Support of Ammunition Production) — €500m for ramp-up of
  ammunition and missile production
EDIP (European Defence Industry Programme) — successor to EDIRPA/ASAP,
  2025-2027 framework

BD implication: non-EU primes (including UK) can participate only
under specific conditions (e.g. via EU subsidiary, qualified EU member
state team, or third-country agreements). Arkmurus non-EU status in
EDF-funded work requires structured workaround.

─────────────────────────────────────────────────────────────────────────────
3.5 EU CUSTOMS UNION AND INTRA-EU TRANSFERS
─────────────────────────────────────────────────────────────────────────────

Most dual-use items move freely within the EU customs union. Annex IV
(genuinely sensitive items) requires prior authorisation even intra-EU.
Military items are subject to national intra-EU transfer rules, with
Directive 2009/43/EC introducing general transfer licences for simpler
cases between member states.

For non-EU brokers: goods transiting through an EU port or warehouse
may trigger EU jurisdiction if customs procedures involve free
circulation, even if final destination is outside the EU.
"""


# =============================================================================
# SECTION 4 — MULTILATERAL EXPORT-CONTROL REGIMES
# =============================================================================

MULTILATERAL_REGIMES = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  MULTILATERAL EXPORT-CONTROL REGIMES — WASSENAAR / MTCR / NSG / AG / CWC ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
Five multilateral, voluntary regimes harmonise export controls among
participating states. They are not treaties in the binding sense, but
participating states commit to implement control lists through their
national laws. The UK, US, EU member states, and most NATO allies are
participants in all five. Russia participates in Wassenaar, MTCR,
NSG but not AG. China participates in NSG only among the "big four"
list-based regimes. Non-participation does not mean non-restriction —
non-participants are often treated as higher-risk destinations.

─────────────────────────────────────────────────────────────────────────────
4.1 THE WASSENAAR ARRANGEMENT
─────────────────────────────────────────────────────────────────────────────

Full name: The Wassenaar Arrangement on Export Controls for Conventional
Arms and Dual-Use Goods and Technologies.
Established: 1996 (successor to COCOM).
Secretariat: Vienna.
Participating States: 42 (as of 2026), including UK, US, EU members,
Russia, Japan, Republic of Korea, Turkey, South Africa, Argentina,
Australia, India (joined 2017).

Two control lists:
  Munitions List (ML1-ML22) — conventional arms, ammunition,
    military vehicles, aircraft, naval vessels, electronics,
    armour, training equipment, production equipment. This is the
    master template that national ML lists (UK, EU CML, US USML
    partially, etc.) are based on.
  List of Dual-Use Goods and Technologies — categories 0-9 (same
    as EU and UK dual-use categories). Updated annually at the
    December plenary.

Wassenaar decisions are taken by consensus, which means updates are
slow. Watch for annual plenary outcomes — new controls on emerging
technologies (quantum, AI training chips, high-end lithography) have
been the subject of recent negotiations.

BD implication: a product that is "Wassenaar dual-use category 4A003
(high-performance computers)" will typically be controlled identically
under UK ECCN, EU Annex I, US CCL ECCN 4A003, and most other
participants' national regimes. The classifying broker can reason once
and apply the classification globally.

─────────────────────────────────────────────────────────────────────────────
4.2 THE MISSILE TECHNOLOGY CONTROL REGIME (MTCR)
─────────────────────────────────────────────────────────────────────────────

Established: 1987 by G7 (US, UK, France, Germany, Italy, Canada, Japan).
Expanded to 35 partners (as of 2026).

Purpose: limit proliferation of delivery systems capable of delivering
weapons of mass destruction.

Two categories:
  Category I — complete delivery systems and their major sub-systems,
    rocket stages, re-entry vehicles, rocket engines, guidance sets,
    thrust vector controls; applies to items capable of delivering
    500kg payload to 300km range. Strong presumption of denial.
  Category II — dual-use items and components that could contribute to
    Category I systems; applies to a broader set of technology with
    case-by-case review.

The 300 km / 500 kg threshold is not arbitrary — it is the MTCR
definition of a WMD-capable missile. ARIA must flag any rocket, missile,
UAV, or cruise-missile item approaching this threshold as MTCR-sensitive
regardless of whether the item itself is listed.

Note: India, despite being a participant, remains outside Russia's
S-400 / S-500 supply chain discipline in practice, and China remains
outside MTCR entirely — which is why China's missile exports are the
primary regime-erosion concern.

─────────────────────────────────────────────────────────────────────────────
4.3 THE NUCLEAR SUPPLIERS GROUP (NSG)
─────────────────────────────────────────────────────────────────────────────

Established: 1974 (post India "peaceful nuclear explosion").
Participating States: 48.

Two control lists:
  Part 1 Trigger List — items specifically designed or prepared for
    nuclear use (reactors, fuel fabrication, reprocessing, enrichment).
  Part 2 Dual-Use List — items with both nuclear and non-nuclear uses
    (e.g. certain high-strength materials, machine tools, electronics).

Export to a non-nuclear-weapon state requires IAEA safeguards as a
condition of supply. India is the key exception — given a waiver in
2008 after the US-India civil nuclear deal.

ARIA application: most defence brokering does not cross NSG triggers,
but any item with possible nuclear dual-use (e.g. machined titanium,
specialty alloys, certain radiation-hardened electronics) should be
cross-checked against the NSG Dual-Use List.

─────────────────────────────────────────────────────────────────────────────
4.4 THE AUSTRALIA GROUP (AG)
─────────────────────────────────────────────────────────────────────────────

Established: 1985 (in response to Iraqi CW use in Iran-Iraq War).
Participating States: 43 (plus EU).

Controls:
  Chemical Weapons Precursors list
  Chemical Manufacturing Facilities and Equipment list
  Biological Agents (pathogens and toxins) list
  Biological Equipment list
  Plant pathogens list
  Animal pathogens list

Complementary to the Chemical Weapons Convention and the Biological
Weapons Convention but more restrictive on dual-use precursors and
equipment.

BD relevance: CBRN defence equipment (masks, detectors, decontamination
systems) is NOT usually AG-controlled, but production equipment and
many precursors are. Brokers in CBRN defence must check AG.

─────────────────────────────────────────────────────────────────────────────
4.5 THE CHEMICAL WEAPONS CONVENTION (CWC) / OPCW
─────────────────────────────────────────────────────────────────────────────

The CWC is a true treaty (entered into force 1997, 193 states parties
as of 2026). It is implemented by the Organisation for the Prohibition
of Chemical Weapons (OPCW, The Hague).

Three schedules:
  Schedule 1 — highly dangerous chemicals with few or no legitimate
    uses (e.g. nerve agents). Very strict limits on quantity,
    movement, and declared use. No transfer to non-States Parties.
  Schedule 2 — chemicals that can be precursors to Schedule 1 or
    which themselves are hazardous. Limits on transfer to non-States
    Parties (3 years after entry into force).
  Schedule 3 — produced in larger commercial quantities, with dual-use
    potential (e.g. phosgene, triethanolamine). Transfer to non-States
    Parties requires end-use certificates.

Riot-control agents (CS, CR, CN, OC pepper spray) are NOT scheduled
but their use in warfare is prohibited. Law enforcement use is
permitted. This is the grey zone for "less lethal" brokers.

─────────────────────────────────────────────────────────────────────────────
4.6 THE BIOLOGICAL AND TOXIN WEAPONS CONVENTION (BTWC)
─────────────────────────────────────────────────────────────────────────────

Entered into force 1975. 184 States Parties. No verification organisation
(unlike CWC/OPCW) — the BTWC lacks an OPCW equivalent.

Complemented by the Australia Group for export-control harmonisation.
"""


# =============================================================================
# SECTION 5 — NATIONAL EXPORT-CONTROL SYSTEMS (KEY NON-UK/US/EU)
# =============================================================================

NATIONAL_REGIMES = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  NATIONAL EXPORT-CONTROL SYSTEMS — TURKEY / ISRAEL / KOREA / JAPAN       ║
║  BRAZIL / INDIA / RUSSIA / CHINA / UAE                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
Any realistic defence broker must reason across the national regimes of
the major non-UK/US/EU exporter states. ARIA must recognise which
national authority controls which products and which licensing path
applies, both when advising on procurement FROM these countries and when
Arkmurus is facilitating transactions involving their OEMs.

─────────────────────────────────────────────────────────────────────────────
5.1 TURKEY
─────────────────────────────────────────────────────────────────────────────

Primary authority: Presidency of Defence Industries (SSB, Savunma
Sanayii Başkanlığı). SSB both promotes Turkish defence exports and
issues export authorisations for defence items.

Legal framework:
  Law 6475 on Defence Industries
  SSB Export Control Regulation
  Turkey is Wassenaar participant (ML + dual-use implementation via
    national customs and trade authorities).

Licensing paths:
  End-User Certificate (EUC) required for all defence exports.
  Export Authorisation Certificate issued by SSB for Turkish-origin
    military items.
  Re-export restrictions: Turkey requires prior SSB consent for
    re-export, re-transfer, or re-integration of Turkish components into
    third-country systems.

Notable Turkish defence exports: Baykar (TB2, Akıncı, Kızılelma UAVs),
ASELSAN (electronics, radars, EW), ROKETSAN (missiles, rockets),
Otokar / FNSS / BMC (armoured vehicles), TAI (helicopters, aircraft),
STM (naval modernisation).

ARIA watch: Turkish exports to Africa, Central Asia, and Southeast
Asia are growing rapidly. SSB re-export restrictions can block
downstream Arkmurus brokering if not planned into the original EUC.

─────────────────────────────────────────────────────────────────────────────
5.2 ISRAEL
─────────────────────────────────────────────────────────────────────────────

Primary authority: Defence Export Control Agency (DECA, Agaf HaYitzu
L'Bitzurim Bit'honim) under the Israeli Ministry of Defense (IMOD).

Legal framework:
  Defence Export Control Law (2007)
  Supervision of Defence Exports and Defence Transfer Regulations

Licensing paths:
  Marketing Licence — required BEFORE any marketing activity in a
    destination country (uncommon elsewhere — the marketing itself
    is controlled).
  Export Licence — transaction-specific, issued after marketing
    licence outcome.
  Knowledge Transfer Licence — for technical data or training.
  End-User Certificate and anti-re-export undertakings required.

Notable Israeli defence exports: Elbit (EW, C4ISR, UAVs, armoured
vehicles), IAI (UAVs, missiles, radar), Rafael (missiles, Iron
Dome, Trophy), IWI (small arms).

ARIA watch: Israel's marketing-licence rule is the most important
procedural difference from UK/US/EU. Arkmurus introducing an Israeli
OEM to a new prospect without DECA marketing clearance is a compliance
problem regardless of whether any goods move.

─────────────────────────────────────────────────────────────────────────────
5.3 REPUBLIC OF KOREA (SOUTH KOREA)
─────────────────────────────────────────────────────────────────────────────

Primary authority: Defense Acquisition Program Administration (DAPA,
방위사업청) — issues military export authorisations. Ministry of Trade,
Industry and Energy (MOTIE) handles dual-use.

Legal framework:
  Defense Acquisition Program Act
  Foreign Trade Act (dual-use)
  Korea is Wassenaar, MTCR, NSG, AG, CWC participant.

Licensing paths:
  DAPA Export Permit — for military items on the Korean military list.
  Strategic Items Export Permit (MOTIE) — for dual-use items.
  End-User Certificate required.

Notable Korean defence exports: Hanwha Aerospace (K9 self-propelled
howitzer, Redback IFV, Chunmoo MLRS), KAI (T-50/FA-50 trainer/light
combat aircraft, KF-21 in development, helicopters), LIG Nex1
(missiles, naval combat systems), Hyundai Rotem (K2 Black Panther
tank), Poongsan (ammunition).

ARIA watch: Korean exports to Poland, Turkey, Australia, Saudi Arabia,
and Southeast Asia are the fastest-growing segment of the global
defence market in 2025-2026. Korean offset and localisation
requirements are favourable to brokers.

─────────────────────────────────────────────────────────────────────────────
5.4 JAPAN
─────────────────────────────────────────────────────────────────────────────

Primary authority: METI (Ministry of Economy, Trade and Industry) for
export licensing under the Foreign Exchange and Foreign Trade Act
(FEFTA). Ministry of Defence (MoD) plays an advisory role.

Legal framework:
  FEFTA and Export Trade Control Order
  Japan is Wassenaar, MTCR, NSG, AG participant.

Three Principles on Transfer of Defense Equipment and Technology
(2014, revised 2023): fundamental liberalisation of previously-
restrictive defence export policy. Transfers permitted when:
  a) not violating international commitments
  b) contributing to peace and international cooperation
  c) contributing to Japan's security

2023 revision expanded to allow lethal-equipment transfer to nations
with which Japan has security cooperation arrangements.

Notable Japanese defence capabilities: Mitsubishi Heavy Industries
(aircraft, vehicles, naval), Mitsubishi Electric (radar, missiles),
Kawasaki Heavy Industries (aircraft, submarines), NEC (C4ISR), IHI
(engines), Komatsu (armoured vehicles — though Komatsu exited the
defence segment in 2019), Toshiba (dual-use electronics).

ARIA watch: Japan is a relatively new entrant into global defence
exports. The 2023 policy revision has unlocked deals around patrol
aircraft, maritime surveillance, and air-defence systems. The Global
Combat Air Programme (GCAP — UK-Italy-Japan next-gen fighter) is the
flagship.

─────────────────────────────────────────────────────────────────────────────
5.5 BRAZIL
─────────────────────────────────────────────────────────────────────────────

Primary authority: Ministry of Defense and Ministry of Foreign Affairs
(Itamaraty). Exports require authorisation under Decree 8135/2013 and
related instruments.

Legal framework:
  Política Nacional de Defesa (PND) and Estratégia Nacional de Defesa
    (END)
  Decreto 8.135/2013 on PRODE (Produto de Defesa) classification
  Brazil is MTCR, NSG participant; not Wassenaar, not AG.

Licensing paths:
  PRODE classification — the product must be formally classified as
    "Produto de Defesa" via Ministry of Defense process.
  Export authorisation via PORTARIAS issued by the Ministry of
    Defense and Foreign Affairs jointly.
  End-User Certificate and, for sensitive items, Ministry of Foreign
    Affairs political clearance.

Notable Brazilian defence exports: Embraer (Super Tucano A-29, C-390
Millennium, KC-390), Avibras (ASTROS rocket artillery), IMBEL (small
arms and ammunition), Taurus (handguns), CBC (ammunition), Engesa
(historic — armoured vehicles), AEL Sistemas (Elbit subsidiary —
electronics).

Offset policy (PCI — Política Comercial Industrial): significant
offset and industrial participation requirements for purchases into
Brazil, favourable for brokers structuring local partnership.

─────────────────────────────────────────────────────────────────────────────
5.6 INDIA
─────────────────────────────────────────────────────────────────────────────

Primary authority: Department of Defence Production (DDP) under the
Ministry of Defence; Directorate General of Foreign Trade (DGFT) under
the Ministry of Commerce for dual-use items; Strategic Trade and
Controls Division under the Ministry of External Affairs.

Legal framework:
  Foreign Trade (Development and Regulation) Act
  SCOMET List (Special Chemicals, Organisms, Materials, Equipment and
    Technologies) — India's dual-use and munitions list
  India joined Wassenaar (2017), MTCR (2016), AG (2018), NSG applying
    but not yet a member as of 2026.

Licensing paths:
  SCOMET Category 0-8 — analogous to Wassenaar categories with India-
    specific items.
  DGFT or DDP authorisation per category.
  End-User Certificate required.

Notable Indian defence capabilities: HAL (aircraft, helicopters),
BDL (missiles), Bharat Electronics (BEL — electronics, C4ISR),
Tata Advanced Systems, Larsen & Toubro (naval, land, missiles),
Mahindra Defence (armoured vehicles).

India's "Make in India" and "Atmanirbhar Bharat" policies push
domestic content on procurements. Major importers (historically from
Russia, shifting toward US/France/Israel) now require transfer of
technology and local manufacturing.

─────────────────────────────────────────────────────────────────────────────
5.7 RUSSIA
─────────────────────────────────────────────────────────────────────────────

Primary authority: Federal Service for Military-Technical Cooperation
(FSMTC, ФСВТС). Rosoboronexport is the state intermediary with
monopoly over most military exports.

Legal framework:
  Federal Law No. 114-FZ On Military-Technical Cooperation of the
    Russian Federation with Foreign States
  Federal Law No. 183-FZ On Export Control
  Russia is Wassenaar, MTCR, NSG participant (formally; practical
    engagement disrupted post-2022).

ARIA CRITICAL — POST-2022 SANCTIONS REGIME:
Since February 2022, Russia has been under comprehensive UK, EU, US,
Canadian, Australian, Japanese, and Swiss sanctions covering defence
exports in and out. CAATSA Section 231 (US) makes any significant
transaction with Russian defence sector a sanctions trigger for
non-US persons. UK has prohibited provision of defence-related
services. EU has prohibited export, import, and brokering of defence
goods with Russia.

Practical rule: any transaction involving Russian-origin military
items, Russian defence entities, or services to the Russian armed
forces is a hard stop for a UK-based broker as of 2022 and remains
so through 2026. ARIA must flag instantly.

Legacy Russian platforms still in service in target markets: T-72,
T-90, BMP, BTR, MiG-29, Su-24/25/27/30, Mi-8/17/35, Ka-52,
S-300/400, Tor, Pantsir, Igla, Strela, AK-pattern small arms and
ammunition. Spare-parts and maintenance for these platforms in
third-country end-users is a compliance minefield because
transactions may funnel through sanctioned Russian OEMs.

─────────────────────────────────────────────────────────────────────────────
5.8 PEOPLE'S REPUBLIC OF CHINA
─────────────────────────────────────────────────────────────────────────────

Primary authority: State Administration of Science, Technology and
Industry for National Defence (SASTIND) and the Ministry of Commerce
(MOFCOM). China's North Industries Corporation (NORINCO) is the main
state intermediary; CATIC handles aerospace; Poly Technologies is
another major intermediary.

Legal framework:
  Export Control Law of the PRC (2020) — landmark national export-
    control law bringing China into line with international practice
  Regulations on Export Control of Military Products (2002, amended)
  Dual-Use Regulations
  China is NSG participant; NOT Wassenaar, NOT MTCR (formally),
  NOT AG.

The 2020 Export Control Law introduced:
  a) Controlled Items Lists (military, dual-use, civilian items
     with dual-use potential)
  b) Licensing requirement for designated exports
  c) Extraterritorial application — echoing US long-arm approach
  d) Counter-sanctions provisions (Article 14) — right to take
     "countermeasures" against foreign sanctions

ARIA watch: Chinese defence exports are a major and growing factor
in Africa (UAVs, small arms, armoured vehicles), the Middle East
(UAVs to Saudi Arabia, UAE), South Asia (Pakistan), and Southeast
Asia. China's non-participation in MTCR makes it the primary supplier
of missile technology to countries that cannot procure from MTCR
states. A UK broker cannot facilitate Chinese-origin deals without
very careful routing and cannot touch anything with US-origin content
under end-use restrictions.

─────────────────────────────────────────────────────────────────────────────
5.9 UAE
─────────────────────────────────────────────────────────────────────────────

Primary authority: Tawazun Council (industrial participation and
offset programme); Executive Office of the Strategic Development Fund;
Ministry of Defence.

Legal framework:
  Federal Law No. 13 of 2007 on Commodities Subject to Import and
    Export Control
  Supervisory Body for Commodities Subject to Import and Export Control
  UAE is dialogue partner but NOT participant in Wassenaar, MTCR,
  NSG, AG as of 2026.

EDGE Group — the UAE's major defence OEM consolidation (2019 merger
of 25+ companies). Product portfolio includes missiles, UAVs, armoured
vehicles, C4ISR, electronic warfare. Active exporter to regional
partners and African buyers.

BD note: UAE is both a major defence purchaser AND an increasingly
active exporter and re-export hub. UAE free zones (Jebel Ali, KIZAD)
are efficient routing for regional trade but carry due-diligence
exposure for sanctions screening.
"""


# =============================================================================
# SECTION 6 — EXTRATERRITORIALITY, RE-EXPORT, AND THE "CHAIN OF CONTROL"
# =============================================================================

EXTRATERRITORIALITY = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  EXTRATERRITORIALITY, RE-EXPORT, AND THE CHAIN OF CONTROL                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The most common broker error is assuming "my deal doesn't touch the
UK/US/EU so those regimes don't apply". In reality export-control
regimes reach across borders through six mechanisms: nationality of
persons, origin of goods, currency used, transit, end-use/end-user
restrictions, and sanctioned-party designations. ARIA must reason
about all six on every transaction and never treat jurisdiction as a
function of the physical movement of goods.

─────────────────────────────────────────────────────────────────────────────
6.1 THE SIX MECHANISMS OF EXPORT-CONTROL LONG-ARM
─────────────────────────────────────────────────────────────────────────────

(a) PERSON NATIONALITY
  UK trade controls reach UK persons wherever situated. US ITAR
  reaches US persons likewise. Even employment of a US or UK person
  in a foreign-based broker can introduce jurisdiction.

(b) GOODS ORIGIN
  ITAR has no de minimis for foreign-incorporated US items. Under EAR
  the de minimis is 10% or 25% by value depending on destination (with
  further reductions for specific items). Once US-origin content
  crosses the threshold, the entire product is re-exported under EAR
  jurisdiction.

(c) CURRENCY / CORRESPONDENT BANKING
  Any USD-denominated payment that clears through the US banking
  system triggers OFAC jurisdiction at the clearing bank. This is
  why sanctioned-party transactions are blocked by correspondent
  banks even when the ultimate parties are all non-US.

(d) TRANSIT
  Goods transiting through UK, US, or EU customs territory (even
  without entering free circulation) may trigger local jurisdiction
  depending on the procedure used. Transit under bond is generally
  less exposing than clearance into free circulation.

(e) END-USE / END-USER RESTRICTIONS
  "Catch-all" controls apply even to non-listed items where the
  exporter knows or has reason to know the item is destined for a
  WMD programme, military end-use in an embargoed destination, or
  human-rights abuse. The UK WMD catch-all is in Article 8 of the
  Export Control Order 2008. EU catch-all is in Article 4 of
  Regulation 2021/821. US catch-alls are in 15 CFR 744.

(f) DESIGNATED-PARTY PROHIBITIONS
  OFAC SDN, UK OFSI Consolidated List, EU Consolidated Financial
  Sanctions List, UN 1267, and others create absolute prohibitions
  on dealing with named persons/entities, their 50%-owned subsidiaries,
  and their agents.

─────────────────────────────────────────────────────────────────────────────
6.2 RE-EXPORT AND RE-TRANSFER
─────────────────────────────────────────────────────────────────────────────

Re-export: movement of previously-exported items from the first
foreign country to a third country.
Re-transfer: change of end-user within the same foreign country.

Most defence exports require written authorisation from the original
exporting authority before re-export or re-transfer. This is usually
captured in the original End-User Certificate (EUC) and the Non-
Transfer and Use Certificate (US DSP-83 equivalent, UK SITCL
undertakings).

Common trap: a client holds a legally-exported UK/US/EU platform,
wants to sell to a third-country buyer because of a fleet upgrade.
Without written authorisation from the original exporting authority,
this is an unlicensed re-export regardless of how long the client has
held the platform. ARIA must flag.

Retransferred technical data: intangible transfers (cloud uploads,
emailed specs, engineer briefings) cross borders without cargo. They
are still controlled. Training of foreign nationals inside the US or
UK on controlled items counts as "deemed export" (US) or "deemed
transfer" (UK).

─────────────────────────────────────────────────────────────────────────────
6.3 ARIA JURISDICTION-MAPPING CHECKLIST
─────────────────────────────────────────────────────────────────────────────

For every non-trivial transaction, ARIA should compute:

  [ ] Goods origin (UK / US / EU / Turkey / Israel / Korea / Japan /
      Brazil / India / Russia / China / other)
  [ ] Broker nationality + residence (UK person? US person?)
  [ ] Payment currency + clearing chain (USD → US nexus)
  [ ] Physical routing (transit through UK/US/EU?)
  [ ] End-use (military / dual-use / civilian)
  [ ] End-user identity + SDN/OFSI/OFAC/1267 screen
  [ ] Catch-all triggers (WMD / repression / terrorism)
  [ ] Re-export chain (original exporter consent?)
  [ ] Technology transfer (any deemed-export risk?)

Any YES on any item adds a regime to the stack. The deal complies only
when ALL implicated regimes authorise it.
"""


# =============================================================================
# SECTION 7 — ARIA APPLIED DECISION FRAMEWORK
# =============================================================================

ARIA_APPLIED = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA APPLIED — USING THIS MODULE IN REAL BRIEFS                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARIA uses this module as the universal export-control reference layer
behind every brief. The decision flow below is what ARIA should apply
automatically on any transaction, export-licence question, or
counterparty due-diligence task, regardless of region.

─────────────────────────────────────────────────────────────────────────────
7.1 THREE QUESTIONS ARIA MUST ANSWER FIRST
─────────────────────────────────────────────────────────────────────────────

1. WHAT IS IT?
   Classify the item against: UK SECL (ML1-22, dual-use 0-9),
   US USML I-XXI / CCL ECCN, EU CML + Annex I, Wassenaar ML + DU,
   and any applicable multilateral regime (MTCR, NSG, AG, CWC).
   If ARIA cannot classify with confidence, she must say so and
   recommend obtaining a formal classification from the exporting
   authority or a qualified technical classifier.

2. WHO IS INVOLVED?
   Identify every entity and individual in the chain: supplier,
   manufacturer, consignee, end-user, intermediaries, broker,
   financial parties. Screen each against UK OFSI, EU
   Consolidated, US OFAC SDN, UN 1267, and the 50-percent rule
   for ownership resolution. Flag any hit immediately.

3. WHERE DOES IT MOVE?
   Map the physical chain (manufacture → staging → ship →
   delivery) and the legal chain (exporter → freight forwarder →
   broker → intermediary → end-user). Flag every jurisdictional
   touch-point and the regime that applies at each.

─────────────────────────────────────────────────────────────────────────────
7.2 THE RAG DECISION TREE
─────────────────────────────────────────────────────────────────────────────

RED (do not proceed)
  - Item is prohibited (anti-personnel mines, cluster munitions,
    chemical weapons, biological weapons)
  - Destination is embargoed under a regime the broker is subject to
  - Any party is SDN/OFSI/EU consolidated listed
  - End-use is WMD or prohibited military use in embargoed destination
  - UK person brokering without SITCL/OGTCL where required

AMBER (proceed only with enhanced controls)
  - Destination is on UK high-risk list (Criterion 2 concern)
  - End-user identity layered or ultimate beneficial owner unclear
  - Routing via a sanctioned-party transit hub
  - Payment chain includes a correspondent-bank jurisdiction of concern
  - Item is Wassenaar dual-use with possible military end-use
  - Re-export chain unclear or original exporter consent not documented

GREEN (standard compliance)
  - All parties screen clean
  - Licence path available (SIEL/SITCL/OGEL/OGTCL)
  - End-use certificate in hand
  - Standard payment chain
  - No re-export chain complications

─────────────────────────────────────────────────────────────────────────────
7.3 CITATION DISCIPLINE
─────────────────────────────────────────────────────────────────────────────

Whenever ARIA applies this module in a brief, she must cite:

  - The specific control list entry (ML5 / ECCN 5A001 / USML VIII(h))
  - The specific licence type (SIEL / SITCL / DSP-5 / TAA / SCOMET)
  - The specific regime article where her reasoning turns on a
    multilateral obligation (Wassenaar plenary decision, MTCR
    Category I/II, NSG Part 2 item, AG schedule)
  - The specific sanctions regime (Russia 2019 Regs / CAATSA 231 /
    OFSI thematic regime)
  - The primary authority (ECJU / DDTC / BIS / DBT / SSB / DECA /
    MOFCOM / FSMTC)

This citation discipline is part of the constitutional clause on
inline citation of tool-derived facts. A compliance conclusion
without a named regime and list entry should be treated as
[UNCERTAIN] at best.
"""


# =============================================================================
# ALL SECTIONS — REGISTRY
# =============================================================================

ALL_SECTIONS = {
    "uk_export_control": {
        "content": UK_EXPORT_CONTROL,
        "tags": ["uk-export-control", "ECJU", "SECL", "UKML", "SIEL",
                 "SITCL", "OIEL", "OGEL", "OGTCL", "consolidated-criteria",
                 "criterion-2", "criterion-7", "trade-controls", "brokering",
                 "SAMLA", "OFSI", "SPIRE", "DBT"],
        "domain": "export_control",
    },
    "us_export_control": {
        "content": US_EXPORT_CONTROL,
        "tags": ["us-export-control", "ITAR", "USML", "EAR", "CCL", "ECCN",
                 "DDTC", "BIS", "DSP-5", "DSP-85", "TAA", "MLA", "OFAC",
                 "SDN", "FMS", "DCS", "CAATSA-231", "deemed-export",
                 "end-use-monitoring", "blue-lantern", "sentinel"],
        "domain": "export_control",
    },
    "eu_export_control": {
        "content": EU_EXPORT_CONTROL,
        "tags": ["eu-export-control", "dual-use-regulation", "2021-821",
                 "common-military-list", "CML", "common-position-2008",
                 "criterion-2", "denial-consultation", "cyber-surveillance",
                 "EDF", "PESCO", "EDA", "EDIRPA", "ASAP", "EDIP",
                 "intra-eu-transfer"],
        "domain": "export_control",
    },
    "multilateral_regimes": {
        "content": MULTILATERAL_REGIMES,
        "tags": ["wassenaar", "MTCR", "NSG", "australia-group", "CWC",
                 "OPCW", "BTWC", "multilateral", "munitions-list",
                 "dual-use", "category-I", "category-II", "trigger-list",
                 "schedule-1", "schedule-2", "schedule-3",
                 "non-proliferation"],
        "domain": "export_control",
    },
    "national_regimes": {
        "content": NATIONAL_REGIMES,
        "tags": ["turkey", "SSB", "baykar", "aselsan", "israel", "DECA",
                 "elbit", "IAI", "rafael", "korea", "DAPA", "hanwha",
                 "KAI", "japan", "METI", "MHI", "brazil", "embraer",
                 "PRODE", "PCI", "india", "SCOMET", "DDP", "DGFT",
                 "russia", "rosoboronexport", "FSMTC", "CAATSA-trap",
                 "china", "SASTIND", "NORINCO", "export-control-law-2020",
                 "UAE", "tawazun", "EDGE-group", "national-export-control"],
        "domain": "export_control",
    },
    "extraterritoriality": {
        "content": EXTRATERRITORIALITY,
        "tags": ["extraterritoriality", "long-arm", "re-export",
                 "re-transfer", "deemed-export", "de-minimis",
                 "correspondent-banking", "usd-clearing", "transit",
                 "catch-all", "SDN", "50-percent-rule", "technical-data",
                 "jurisdiction-mapping"],
        "domain": "export_control",
    },
    "aria_applied_framework": {
        "content": ARIA_APPLIED,
        "tags": ["aria-decision-flow", "RAG-decision-tree", "classification",
                 "entity-screening", "routing-analysis", "citation-discipline",
                 "compliance-framework", "broker-checklist"],
        "domain": "export_control",
    },
}


# =============================================================================
# RAG STORE INJECTION
# =============================================================================

@wired(module="global_export_control", summary="Global export control ingest")

async def ingest_all_sections() -> dict:
    """Ingest the global export control library into ARIA's RAG store.

    Called at startup (main.py _seed_knowledge_bg) and on MONTHLY-LAW-
    REFRESH. Safe to call repeatedly — rag_store.ingest_document
    deduplicates by source URL (internal://aria/global_export_control/<name>).
    """
    from . import rag_store

    results = {}
    total_chunks = 0

    for section_name, data in ALL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                data["content"],
                source=f"intlaw:global_export_control:{section_name}",
                source_type="export_control",
                title=f"Global Export Control — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/global_export_control/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "global_export_control",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("Export control section ingested: %s (%d chunks)", section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("Export control section ingestion failed [%s]: %s", section_name, e)

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info(
        "Global export control ingestion: %d/%d sections, %d total chunks",
        success, len(ALL_SECTIONS), total_chunks,
    )
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="global_export_control",
            summary=f"Export control knowledge ingested: {success}/{len(ALL_SECTIONS)} sections, {total_chunks} chunks (Wassenaar, MTCR, NSG, ITAR, EAR, EU dual-use)",
            success=success == len(ALL_SECTIONS),
            confidence="CONFIRMED",
        )
    except Exception:
        pass
    return {
        "sections_ingested": success,
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }
