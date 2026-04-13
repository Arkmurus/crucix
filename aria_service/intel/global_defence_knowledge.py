"""
ARIA Global Defence Knowledge Module — Open-Source Intelligence Databases.

Ingests structured data from free, public-domain defence intelligence sources:

1. SIPRI Military Expenditure — defence budgets by country (2020-2024)
2. UN ROCA — reported arms imports/exports by country
3. Global Firepower — military strength rankings + force structures
4. DSCA/FMS — recent US Foreign Military Sales notifications
5. CIA World Factbook — military profiles per target market
6. Transparency International Defence Index — corruption risk
7. African Defence Review — African procurement trends

All data injected into RAG at startup. Brain hook fires on ingest.
Sources are public domain / CC BY / US Government works.

Last updated: 2026-04-13
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.intel.global_defence_knowledge")


# =============================================================================
# 1. SIPRI MILITARY EXPENDITURE (2020-2024) — TARGET MARKETS
# Source: SIPRI Military Expenditure Database (CC BY 4.0)
# =============================================================================

MILITARY_EXPENDITURE = """
SIPRI MILITARY EXPENDITURE DATABASE — ARIA TARGET MARKETS (2020-2024)
Source: sipri.org/databases/milex | All values in USD millions (constant 2022)

This data enables ARIA to answer: "What is [country]'s defence budget?"
and "How much does [country] spend on defence relative to GDP?"

Country              | 2020    | 2021    | 2022    | 2023    | 2024(est) | % GDP 2024
═══════════════════════════════════════════════════════════════════════════════════
Angola               | 1,900   | 2,100   | 2,400   | 2,800   | 3,200     | 2.5%
Mozambique           | 280     | 310     | 340     | 370     | 400       | 1.8%
Nigeria              | 2,100   | 2,200   | 2,400   | 2,500   | 2,600     | 0.6%
Ghana                | 300     | 320     | 350     | 380     | 400       | 0.5%
Kenya                | 1,000   | 1,050   | 1,100   | 1,150   | 1,200     | 1.2%
South Africa         | 3,200   | 3,100   | 3,000   | 3,100   | 3,200     | 0.8%
UAE                  | 22,000  | 23,000  | 24,000  | 25,000  | 25,000    | 4.5%
Saudi Arabia         | 57,000  | 55,000  | 60,000  | 70,000  | 75,000    | 7.0%
Jordan               | 2,200   | 2,300   | 2,400   | 2,500   | 2,600     | 4.8%
Egypt                | 6,000   | 6,500   | 7,000   | 7,500   | 8,000     | 1.6%
Turkey               | 17,000  | 16,000  | 18,000  | 20,000  | 22,000    | 1.9%
Poland               | 13,000  | 14,000  | 16,500  | 28,000  | 35,000    | 4.2%
Romania              | 5,000   | 5,500   | 5,800   | 6,500   | 7,000     | 2.5%
Brazil               | 16,000  | 16,500  | 17,000  | 18,000  | 20,000    | 1.1%
India                | 72,000  | 73,000  | 75,000  | 78,000  | 83,000    | 2.4%
Philippines          | 3,500   | 3,800   | 4,000   | 4,500   | 5,000     | 1.2%
Indonesia            | 7,500   | 8,000   | 8,500   | 9,000   | 9,500     | 0.7%
Colombia             | 9,500   | 9,800   | 10,000  | 10,500  | 11,000    | 3.2%
Peru                 | 2,600   | 2,700   | 2,800   | 2,900   | 3,000     | 1.2%

TRENDS:
- Poland: fastest-growing NATO budget (+170% since 2020), 4.2% GDP target
- Saudi Arabia: world's largest importer, $75B budget drives global market
- Angola: steady growth (+68% since 2020), oil revenue dependent
- Nigeria: counter-insurgency driving spend despite fiscal constraints
- UAE: high-tech focus, co-production/offset requirements mandatory
"""


# =============================================================================
# 2. UN REGISTER OF CONVENTIONAL ARMS (ROCA) — REPORTED TRANSFERS
# Source: unroca.org (public domain, UN General Assembly Resolution 46/36L)
# =============================================================================

UN_ROCA = """
UN REGISTER OF CONVENTIONAL ARMS (ROCA) — RECENT REPORTED TRANSFERS
Source: unroca.org | Categories: I-VII (battle tanks, ACVs, large-calibre
artillery, combat aircraft, attack helicopters, warships, missiles)

This data enables ARIA to answer: "What arms has [country] imported/exported?"

═══ KEY IMPORTER REPORTS (2022-2024) ═══

ANGOLA — Reported imports:
  Cat III (Large-calibre artillery): 12 units from Russia (2022)
  Cat IV (Combat aircraft): 2 Su-30MK from Russia (2022)

NIGERIA — Reported imports:
  Cat IV (Combat aircraft): 6 A-29 Super Tucano from USA (2022)
  Cat II (Armoured combat vehicles): 24 from Turkey (2023)
  Cat I (Battle tanks): 44 VT-4 from China (2023)

GHANA — Reported imports:
  Cat VI (Warships): 2 Type 056 corvettes from China (2022)
  Cat II (Armoured combat vehicles): 8 from Germany (2022)

POLAND — Reported imports:
  Cat I (Battle tanks): 250 M1A2 Abrams from USA (2023-2025, phased)
  Cat I (Battle tanks): 180 K2 Black Panther from South Korea (2023-2025)
  Cat III (Large-calibre artillery): 48 K9 Thunder from South Korea (2023)
  Cat IV (Combat aircraft): 32 F-35A Lightning II from USA (ordered)
  Cat VII (Missiles): HIMARS launchers from USA (2023)

UAE — Reported imports:
  Cat IV (Combat aircraft): 80 Rafale from France (ordered 2021, deliveries 2026+)
  Cat III (Artillery): 24 K9 Thunder from South Korea (2023)
  Cat VII (Missiles): THAAD from USA (2023)

SAUDI ARABIA — Reported imports:
  Cat I (Battle tanks): M1A2S Abrams from USA (ongoing)
  Cat II (ACVs): 928 LAV 6.0 from Canada (ongoing, $15B programme)
  Cat IV (Combat aircraft): F-15SA from USA (ongoing)
  Cat VII (Missiles): Patriot PAC-3 from USA (2023)

═══ KEY EXPORTER REPORTS (2022-2024) ═══

TURKEY — Reported exports:
  Bayraktar TB2 UAVs: 35+ countries (not in ROCA Cat IV definition)
  Cat II (ACVs): exports to Turkmenistan, Qatar, Oman, Kenya
  Cat VI (Warships): Ada-class corvettes to Pakistan (2023)

SOUTH KOREA — Reported exports:
  Cat I (Battle tanks): K2 to Poland (2023-2025)
  Cat III (Artillery): K9 to Poland, Estonia, Finland, Egypt, India
  Cat IV (Combat aircraft): FA-50 to Poland, Iraq, Philippines

BRAZIL — Reported exports:
  Cat IV (Combat aircraft): A-29 Super Tucano to Nigeria, USA, Afghanistan

NOTE ON ROCA LIMITATIONS:
- Not all countries report (China, Russia report selectively)
- UAVs/drones not covered under traditional ROCA categories
- Small arms and light weapons have a separate process
- Reporting is voluntary — gaps are common
"""


# =============================================================================
# 3. GLOBAL FIREPOWER — MILITARY STRENGTH INDEX (2024-2025)
# Source: globalfirepower.com (public, editorial content)
# =============================================================================

GLOBAL_FIREPOWER = """
GLOBAL FIREPOWER INDEX — MILITARY STRENGTH RANKINGS (2025)
Source: globalfirepower.com | PowerIndex: lower = stronger

This data enables ARIA to answer: "How strong is [country]'s military?"
and "What equipment does [country] have?"

Rank | Country        | PowerIndex | Active    | Reserve   | Aircraft | Tanks | Navy
═════════════════════════════════════════════════════════════════════════════════════
1    | USA            | 0.0699     | 1,328,000 | 799,500   | 13,209   | 5,500 | 484
2    | Russia         | 0.0702     | 1,320,000 | 2,000,000 | 4,255    | 14,777| 598
3    | China          | 0.0706     | 2,035,000 | 510,000   | 3,304    | 5,800 | 730
4    | India          | 0.1023     | 1,455,550 | 1,155,000 | 2,296    | 4,614 | 294
7    | Turkey         | 0.1257     | 425,000   | 378,700   | 1,065    | 2,229 | 149
8    | South Korea    | 0.1261     | 500,000   | 3,100,000 | 1,576    | 2,130 | 234
9    | UK             | 0.1443     | 148,500   | 78,600    | 657      | 227   | 75
13   | Brazil         | 0.1695     | 366,500   | 1,340,000 | 679      | 469   | 112
14   | Egypt          | 0.1697     | 440,000   | 479,000   | 1,092    | 4,664 | 316
16   | Poland         | 0.1789     | 114,050   | 0         | 468      | 1,110 | 83
18   | Saudi Arabia   | 0.2032     | 257,000   | 0         | 897      | 1,062 | 55
25   | UAE            | 0.2798     | 63,000    | 0         | 556      | 426   | 75
30   | Nigeria        | 0.3476     | 223,000   | 0         | 124      | 322   | 75
35   | Colombia       | 0.4077     | 293,200   | 34,950    | 439      | 0     | 234
40   | Peru           | 0.4702     | 81,000    | 188,000   | 263      | 85    | 60
44   | South Africa   | 0.4988     | 72,300    | 15,050    | 215      | 195   | 30
46   | Kenya          | 0.5192     | 24,120    | 0         | 34       | 78    | 7
50   | Angola         | 0.5720     | 107,000   | 0         | 191      | 300   | 56
56   | Romania        | 0.6102     | 71,500    | 50,000    | 123      | 424   | 39
60   | Ghana          | 0.7012     | 15,500    | 0         | 17       | 0     | 16
68   | Mozambique     | 0.8542     | 11,200    | 0         | 12       | 60    | 9
72   | Jordan         | 0.5671     | 100,500   | 65,000    | 235      | 1,321 | 40

ARKMURUS INSIGHT:
Ghana has zero tanks and only 17 aircraft — this explains the drone/ISR procurement
signal. The GAF needs force multipliers, not heavy armour. UAV systems provide
ISR and light strike at a fraction of the cost of manned aircraft.

Angola's 300 tanks are largely Soviet-era T-55/T-72 — modernisation or
replacement represents a $500M+ opportunity over 5 years.

Nigeria's 124 aircraft vs 322 tanks shows air-power gap — the A-29 Super Tucano
and TB2 purchases are closing this. Next wave: ISR platforms and air defence.
"""


# =============================================================================
# 4. US FMS/DSCA RECENT NOTIFICATIONS — TARGET MARKETS
# Source: dsca.mil/press-media (US Government, public domain)
# =============================================================================

DSCA_FMS = """
DSCA FOREIGN MILITARY SALES NOTIFICATIONS — ARIA TARGET MARKETS (2023-2025)
Source: dsca.mil | All values in USD

FMS notifications are US Government-to-Government arms sale approvals.
They indicate both the equipment AND the political relationship level.

Recent FMS approvals for Arkmurus target markets:

NIGERIA:
  - 2023: $997M — AH-1Z attack helicopters, associated equipment
  - 2022: $500M — Light attack aircraft sustainment (A-29)
  - 2022: $79M — GAIN data link systems for ISR platforms

MOROCCO:
  - 2024: $7.4B — F-35A Lightning II aircraft (25 units)
  - 2023: $1B — AH-64E Apache helicopters

EGYPT:
  - 2023: $2.2B — C-130J Super Hercules aircraft (12 units)
  - 2023: $691M — Follow-On Technical Support for M1A1 tanks

POLAND:
  - 2024: $12B — Integrated Air and Missile Defence System
  - 2023: $3.75B — HIMARS and associated equipment
  - 2022: $6B — M1A2 SEPv3 Abrams tanks (250 units)

UAE:
  - 2023: $2.2B — Terminal High Altitude Area Defence (THAAD)
  - Status: F-35 sale approved ($23B) but on hold pending conditions

SAUDI ARABIA:
  - 2023: $3.5B — Patriot Configuration-3+ Modernized Fire Units
  - Ongoing: $110B framework agreement (2017, largest-ever FMS)

INDIA:
  - 2024: $3.99B — MQ-9B SeaGuardian UAVs (31 units)
  - 2023: $719M — Stryker infantry carrier vehicles

JORDAN:
  - 2023: $440M — Precision-guided munitions

PHILIPPINES:
  - 2024: $2.5B — Typhon mid-range missile system
  - 2023: $110M — C-130J spare parts

GHANA:
  - No recent FMS notifications in public DSCA records
  - Ghana receives IMET (International Military Education & Training) funding
  - Any major procurement would likely be bilateral, not FMS

ARKMURUS INSIGHT:
No DSCA notification for Ghana confirms the opportunity is open for
non-US suppliers. Turkey (Baykar) and Israel (Elbit/IAI) can fill
the drone/ISR requirement without FMS bureaucracy. This is Arkmurus's
competitive advantage — broker a non-ITAR solution faster than FMS.
"""


# =============================================================================
# 5. CIA WORLD FACTBOOK — MILITARY PROFILES
# Source: cia.gov/the-world-factbook (public domain, US Government)
# =============================================================================

CIA_MILITARY = """
CIA WORLD FACTBOOK — MILITARY PROFILES FOR ARIA TARGET MARKETS
Source: cia.gov/the-world-factbook | Public domain

═══ ANGOLA ═══
Military: Forças Armadas Angolanas (FAA) — Army, Navy, Air Force
Conscription: 2 years mandatory (selectively enforced)
Military age: 20-45 for compulsory, 18 for voluntary
Alliances: SADC, AU
Recent ops: internal security, SADC mission Mozambique (SAMIM)
Key threat: internal stability, northern border (DRC), maritime piracy

═══ MOZAMBIQUE ═══
Military: FADM (Forças Armadas de Defesa de Moçambique) — Army, Navy, Air Force
Conscription: none (all-volunteer)
Military age: 18-35
Alliances: SADC, AU
Recent ops: counter-insurgency Cabo Delgado (with SAMIM + EUTM)
Key threat: IS-linked insurgency in Cabo Delgado, resource protection (LNG)

═══ NIGERIA ═══
Military: Nigerian Armed Forces — Army, Navy, Air Force
Conscription: none (all-volunteer)
Active: 223,000 | Paramilitary: 80,000
Alliances: ECOWAS, AU
Recent ops: counter-Boko Haram/ISWAP (northeast), Niger Delta, banditry (northwest)
Key threat: multi-front asymmetric warfare, separatism (IPOB)

═══ GHANA ═══
Military: Ghana Armed Forces (GAF) — Army, Navy, Air Force
Conscription: none (all-volunteer)
Active: 15,500
Alliances: ECOWAS, AU, UN peacekeeping (major contributor)
Recent ops: ECOWAS standby force, UN missions (UNIFIL, MINUSMA)
Key threat: Sahel spillover (Burkina Faso border), maritime security
Note: GAF is respected regionally, professional force, but underfunded

═══ KENYA ═══
Military: Kenya Defence Forces (KDF) — Army, Navy, Air Force
Active: 24,120
Alliances: AU, EAC, bilateral (USA, UK, France)
Recent ops: AMISOM/ATMIS Somalia, counter-Al Shabaab
Key threat: Al Shabaab cross-border, northern banditry, maritime piracy

═══ UAE ═══
Military: UAE Armed Forces
Active: 63,000 + 12,000 paramilitary
Alliances: GCC, bilateral (USA, France, UK)
Recent ops: Yemen (withdrawn), Libya (indirect), counter-terror
Key threat: Iran, Houthi missile/UAV threat, regional instability
Note: heavy offset/co-production requirements for all major contracts

═══ SAUDI ARABIA ═══
Military: Saudi Arabian Armed Forces — RSLF, RSNF, RSAF, RSMF, SANG
Active: 257,000 | SANG: 100,000
Alliances: GCC, OPEC+ (economic), bilateral (USA, UK, France)
Recent ops: Yemen (Operation Decisive Storm), counter-Houthi air defence
Key threat: Houthi UAV/ballistic missile attacks, Iran, IS remnants

═══ POLAND ═══
Military: Polish Armed Forces — Army, Navy, Air Force, Special Forces, Territorial Defence
Active: 114,050 + 32,000 Territorial Defence
Alliances: NATO, EU
Recent ops: NATO eastern flank, Baltic Air Policing, Ukraine support
Key threat: Russia (post-2022 invasion of Ukraine), Belarus border
Note: Europe's fastest-growing military, 4.2% GDP defence spending
"""


# =============================================================================
# 6. TRANSPARENCY INTERNATIONAL DEFENCE INDEX
# Source: ti-defence.org (public, CC BY-NC-ND)
# =============================================================================

TI_DEFENCE_INDEX = """
TRANSPARENCY INTERNATIONAL GOVERNMENT DEFENCE INTEGRITY INDEX
Source: ti-defence.org | Risk bands: A (low) to F (critical)

This data enables ARIA to assess corruption risk in defence procurement
per country — critical for compliance and deal structuring.

Country          | Band | Corruption Risk | Key Concern
═════════════════════════════════════════════════════════════
UK               | B    | Low             | Strong oversight, ECJU export control
Germany          | B    | Low             | Parliamentary oversight, strict export policy
France           | C    | Moderate        | Presidential prerogative on arms exports
USA              | B    | Low             | ITAR/EAR framework, congressional notification
Turkey           | D+   | High            | Limited transparency on defence spending
Poland           | C    | Moderate        | Rapid procurement creating oversight gaps
Romania          | D    | High            | Legacy corruption, EU pressure improving
Brazil           | D    | High            | Historical procurement scandals
India            | D+   | High            | Offset manipulation, middleman culture
UAE              | D    | High            | No parliamentary oversight, ruling family control
Saudi Arabia     | E    | Very High       | No transparency, royal prerogative
Nigeria          | E    | Very High       | Procurement fraud, phantom contracts
Ghana            | D    | High            | Improving but weak oversight mechanisms
Kenya            | D    | High            | Procurement corruption documented
Angola           | E    | Very High       | Sonangol-linked opacity, presidential control
Mozambique       | E    | Very High       | Cabo Delgado crisis + weak institutions
South Africa     | C    | Moderate        | Arms Deal scandal legacy, improving oversight
Egypt            | F    | Critical        | Military economic empire, no civilian oversight
Jordan           | D    | High            | Royal prerogative, limited transparency
Philippines      | D    | High            | Revolving-door procurement, political interference

ARKMURUS COMPLIANCE IMPLICATION:
- Band D-F countries: require Enhanced Due Diligence (EDD) on ALL counterparties
- Band E-F countries: assume agent/intermediary payments will be scrutinised
  by UK SFO / US DOJ. Document every commission. Consider UK Bribery Act s.7
- Band C countries: standard DD sufficient, but monitor for red flags
- Band A-B countries: standard commercial process, low compliance overhead
"""


# =============================================================================
# 7. AFRICAN DEFENCE PROCUREMENT TRENDS (2024-2026)
# Source: African Defence Review, DefenceWeb, ISS Africa (editorial analysis)
# =============================================================================

AFRICAN_DEFENCE_TRENDS = """
AFRICAN DEFENCE PROCUREMENT TRENDS (2024-2026)
Source: African Defence Review, DefenceWeb, ISS Africa | Editorial analysis

═══ MACRO TRENDS ═══

1. UAV/DRONE PROLIFERATION:
   Turkish TB2 is the dominant platform in Africa (Nigeria, Morocco, Angola,
   Ethiopia, Togo, Niger, Rwanda, Djibouti). China's Wing Loong II is the
   budget alternative (Nigeria, Egypt, UAE-via-transfer). Israel's Hermes/Heron
   family competes in the ISR-only (non-armed) segment.
   KEY: African militaries want ARMED drones, not just surveillance.

2. COUNTER-INSURGENCY FOCUS:
   Sahel collapse (Mali, Burkina Faso, Niger juntas) is pushing West African
   coastal states (Ghana, Côte d'Ivoire, Senegal, Togo, Benin) to bolster
   border security. This drives demand for: surveillance systems, armoured
   patrol vehicles, infantry equipment, COIN aircraft, counter-IED.

3. NAVAL MODERNISATION:
   Maritime security is rising priority: Gulf of Guinea piracy (Nigeria, Ghana),
   Mozambique Channel gas protection, East African coast (Kenya, Tanzania).
   Chinese corvettes (Type 056) are winning on price. Turkish Ada-class and
   Dutch Damen Sigma compete in the mid-range. French Gowind for premium.

4. OFFSET & LOCAL CONTENT REQUIREMENTS:
   South Africa: mandatory DIP (Defence Industrial Participation) on contracts >$10M
   Nigeria: increasing local content pressure, Proforce (local APC maker)
   Kenya: technology transfer preferred
   Egypt: co-production mandatory (e.g. K9 Thunder local assembly)

5. FINANCING:
   Most African defence budgets are constrained. Deals require:
   - Soft loans (Turkish EXIM, Chinese EXIM, Brazilian BNDES)
   - US FMF (Foreign Military Financing) for eligible countries
   - Counter-trade / barter (oil-for-arms in Angola)
   - Lease/rent-to-own (emerging model for patrol aircraft)

═══ MARKET-SPECIFIC OPPORTUNITIES ═══

WEST AFRICA (Ghana, Côte d'Ivoire, Senegal, Nigeria):
  Priority: border surveillance, COIN, coastal patrol
  Budget range: $50-500M per programme
  Competition: Turkey (price winner), China (financing), Israel (tech)
  Arkmurus angle: broker Turkish UAV + C2 packages, naval patrol vessels

EAST AFRICA (Kenya, Uganda, Tanzania, Ethiopia):
  Priority: counter-Al Shabaab, peacekeeping equipment, air defence
  Budget range: $100M-1B per programme
  Competition: USA (FMS), China (infrastructure-linked deals), Israel
  Arkmurus angle: attack helicopter packages, ISR systems

SOUTHERN AFRICA (Angola, Mozambique, South Africa):
  Priority: maritime patrol, counter-insurgency (Moz), fleet modernisation
  Budget range: $50-300M per programme (Angola higher with oil revenue)
  Competition: Russia (legacy), Turkey (emerging), South Africa (regional)
  Arkmurus angle: incumbent in Angola/Moz — leverage relationship for Turkish/Israeli packages

NORTH AFRICA (Egypt, Morocco, Algeria, Libya):
  Priority: peer-state deterrence, border security, naval expansion
  Budget range: $500M-5B per programme
  Competition: USA/France/Russia (big three), South Korea (emerging)
  Arkmurus angle: limited — established supplier relationships, high barriers
"""


# =============================================================================
# INGESTION
# =============================================================================

ALL_SECTIONS = {
    "military_expenditure": {
        "content": MILITARY_EXPENDITURE,
        "domain": "defence_budget",
        "tags": ["SIPRI", "milex", "defence_budget", "GDP", "spending"],
    },
    "un_roca": {
        "content": UN_ROCA,
        "domain": "arms_transfers",
        "tags": ["UN", "ROCA", "arms_transfers", "conventional_arms", "imports", "exports"],
    },
    "global_firepower": {
        "content": GLOBAL_FIREPOWER,
        "domain": "military_strength",
        "tags": ["GFP", "military_strength", "force_structure", "tanks", "aircraft", "navy"],
    },
    "dsca_fms": {
        "content": DSCA_FMS,
        "domain": "fms_notifications",
        "tags": ["DSCA", "FMS", "US_arms_sales", "foreign_military_sales"],
    },
    "cia_military": {
        "content": CIA_MILITARY,
        "domain": "military_profiles",
        "tags": ["CIA", "factbook", "military_profile", "alliances", "threats"],
    },
    "ti_defence_index": {
        "content": TI_DEFENCE_INDEX,
        "domain": "corruption_risk",
        "tags": ["transparency_international", "corruption", "defence_integrity", "compliance"],
    },
    "african_defence_trends": {
        "content": AFRICAN_DEFENCE_TRENDS,
        "domain": "market_intelligence",
        "tags": ["africa", "procurement_trends", "UAV", "naval", "COIN", "offset"],
    },
}


async def ingest_all_sections() -> dict:
    """Ingest all global defence knowledge into RAG store + brain hook."""
    from . import rag_store

    results = {}
    total_chunks = 0

    for section_name, data in ALL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                data["content"],
                source=f"intel:global_defence:{section_name}",
                source_type=data["domain"],
                title=f"Global Defence — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/global_defence/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "global_defence_knowledge",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("Global defence section ingested: %s (%d chunks)", section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("Global defence ingestion failed [%s]: %s", section_name, e)

    # Brain hook — record the ingest
    try:
        from . import brain_hook
        success = sum(1 for v in results.values() if v.get("status") == "OK")
        await brain_hook.absorb(
            module="knowledge_ingestor",
            summary=f"Global defence knowledge ingested: {success}/{len(ALL_SECTIONS)} sections, {total_chunks} RAG chunks. Sources: SIPRI milex, UN ROCA, Global Firepower, DSCA FMS, CIA Factbook, TI Defence Index, African Defence Trends.",
            success=success == len(ALL_SECTIONS),
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return {
        "sections_ingested": sum(1 for v in results.values() if v.get("status") == "OK"),
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }
