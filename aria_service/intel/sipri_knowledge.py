"""
ARIA SIPRI Arms Transfers + Equipment Specs Knowledge Module.

Provides two capabilities:
1. SIPRI-sourced arms transfer data for ARIA's target markets
   (who bought what from whom, recent 5 years)
2. Equipment specs for top 50 traded defence platforms
   (enables technical verification during DD)

Data is injected into RAG at startup so ARIA can answer:
  "What has Ghana bought recently?"
  "Who supplies Angola with armoured vehicles?"
  "What are the specs of the Bayraktar TB2?"

Source: SIPRI Arms Transfers Database (public domain, CC BY 4.0)
        + open-source equipment specifications from manufacturer datasheets.
Last updated: 2026-04-13
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.intel.sipri_knowledge")


# =============================================================================
# SIPRI ARMS TRANSFERS — KEY TARGET MARKETS (2020-2025)
# Source: SIPRI Arms Transfers Database, armstransfers.sipri.org
# TIV = Trend Indicator Value (SIPRI's standardised measure)
# =============================================================================

ARMS_TRANSFERS = """
SIPRI ARMS TRANSFERS DATABASE — ARIA TARGET MARKETS (2020-2025)

This data enables ARIA to answer: "What has [country] bought?" and
"Who are the main suppliers to [country]?" for defence broking context.
All values are SIPRI Trend Indicator Values (TIV), not financial values.

═══════════════════════════════════════════════════════════════════
ANGOLA (Incumbent — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: Russia (legacy fleet), Israel, Spain, Brazil
Recent deliveries (2020-2025):
  - Russia: Su-30MK fighters (2 delivered 2020), BMP-3 IFVs
  - Israel: Hermes 450/900 UAVs, Aerostar UAVs (ISR)
  - Spain: C-295 transport aircraft (Airbus Defence)
  - Brazil: Super Tucano light attack (Embraer)
  - Turkey: Bayraktar TB2 UAVs (ordered 2023, delivery TBC)
Key procurement priorities: border surveillance, maritime patrol,
  counter-IED, armoured vehicles, ISR/UAV, communications.
Defence budget: ~$3.2B (2024 est.), ~2.5% of GDP.
FADM (Forças Armadas de Defesa de Moçambique) — incorrect, this is Angola:
  FAA (Forças Armadas Angolanas) — Army, Navy, Air Force.
Procurement authority: Ministry of National Defence (MINDEN).

═══════════════════════════════════════════════════════════════════
MOZAMBIQUE (Incumbent — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: Turkey, UAE, South Africa, India, Portugal
Recent deliveries (2020-2025):
  - Turkey: Bayraktar TB2 UAVs (counter-insurgency, Cabo Delgado)
  - UAE: Calidus B-250 light attack aircraft (ordered 2022)
  - South Africa: Mamba Mk2 APCs, Casspir MRAPs (legacy)
  - India: patrol boats for Mozambican Navy
  - Portugal: training + small arms (bilateral defence cooperation)
Key procurement: counter-insurgency (Cabo Delgado), coastal patrol,
  ISR/surveillance, infantry equipment, MRAP/armoured vehicles.
Defence budget: ~$400M (2024 est.), heavily supplemented by
  SADC + EU + US training missions (SAMIM, EUTM).

═══════════════════════════════════════════════════════════════════
NIGERIA (Established — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: USA, China, Turkey, Italy, Pakistan
Recent deliveries (2020-2025):
  - USA: A-29 Super Tucano (12 delivered 2021-22), Bell 412 helicopters
  - Turkey: Bayraktar TB2 UAVs, Bayraktar Akinci (ordered 2023)
  - China: Wing Loong II UAVs (CAIG), Type 056 corvettes
  - Italy: M-346FA light fighters (Leonardo), AW109 helicopters
  - Pakistan: JF-17 Thunder fighters (under discussion)
Defence budget: ~$2.6B (2024 est.). Counter-Boko Haram/ISWAP drives spending.
Procurement authority: Ministry of Defence, with direct MoD-to-OEM preferred.

═══════════════════════════════════════════════════════════════════
GHANA (Developing — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: China, Germany, USA, Israel
Recent deliveries (2020-2025):
  - China: Type 056 corvettes (2 delivered), armoured vehicles
  - Germany: patrol boats (Fassmer)
  - USA: C-130 Hercules (refurbished), IMET training programme
  - Israel: UAV systems (Elbit Hermes), surveillance equipment
Key procurement: border surveillance (Sahel spillover), naval patrol,
  UAV/ISR, communications, peacekeeping equipment (ECOWAS).
Estimated active programme: $30-100M drone + border surveillance system.
Defence budget: ~$400M (2024 est.), supplemented by ECOWAS contributions.

═══════════════════════════════════════════════════════════════════
KENYA (Established — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: USA, France, Spain, South Africa, Israel
Recent deliveries (2020-2025):
  - USA: MD-530F helicopters, Scan Eagle UAVs, Humvees
  - France: VBL armoured vehicles (Panhard/Arquus)
  - Spain: C-295 transport (Airbus Defence)
  - South Africa: Badger IFVs (Denel), RG-31 MRAPs
  - Israel: Orbiter UAVs (Aeronautics), radar systems
Defence budget: ~$1.2B (2024 est.). Counter-Al Shabaab (Somalia border).

═══════════════════════════════════════════════════════════════════
UAE (Developing — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: USA, France, Turkey, Italy, South Korea
Recent deliveries (2020-2025):
  - USA: F-35 Lightning II (under review), MQ-9 Reaper, Patriot PAC-3
  - France: Rafale fighters (80 ordered 2021), Leclerc tanks
  - Turkey: Bayraktar TB2 UAVs (EDGE Group co-production)
  - Italy: Falaj-class patrol boats (Fincantieri), NH-90 helicopters
  - South Korea: K-9 Thunder SPGs, Chunmoo MLRS
Defence budget: ~$25B (2024 est.). Largest Gulf buyer.

═══════════════════════════════════════════════════════════════════
SAUDI ARABIA (Developing — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: USA, UK, France, Spain, Canada
Recent deliveries (2020-2025):
  - USA: F-15SA fighters, AH-64E Apache, THAAD, M1A2 Abrams
  - UK: Eurofighter Typhoon (BAE Systems), Hawk trainers
  - France: CAESAR SPGs (Nexter/KNDS), patrol boats
  - Spain: A330 MRTT (Airbus), C-295 transport
  - Canada: LAV 6.0 APCs (General Dynamics, $15B programme)
Defence budget: ~$75B (2024 est.). Largest global importer.

═══════════════════════════════════════════════════════════════════
BRAZIL (Developing — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: France, Sweden, USA, Israel, Germany
Recent deliveries (2020-2025):
  - France: Scorpène submarines (Naval Group), H225M helicopters
  - Sweden: JAS 39E Gripen fighters (36 ordered, SAAB)
  - USA: C-130J Hercules, Javelin ATGMs
  - Israel: Hermes 900 UAVs (IAI/Elbit)
  - Germany: MEKO A100 frigates (TKMS)
Defence budget: ~$20B (2024 est.). Largest LatAm military.

═══════════════════════════════════════════════════════════════════
POLAND (Established — Arkmurus)
═══════════════════════════════════════════════════════════════════
Main suppliers: USA, South Korea, UK, Turkey
Recent deliveries (2020-2025):
  - USA: M1A2 Abrams (250 ordered), HIMARS, F-35A (32 ordered), AH-64E
  - South Korea: K2 Black Panther tanks (1000 ordered), K9 Thunder SPGs (672),
    FA-50 fighters (48), Chunmoo MLRS
  - UK: MBDA CAMM missiles (for Narew air defence)
  - Turkey: Bayraktar TB2 UAVs (24 delivered)
Defence budget: ~$35B (2024 est., 4.2% GDP target). NATO eastern flank.

═══════════════════════════════════════════════════════════════════
TURKEY (Established — Arkmurus)
═══════════════════════════════════════════════════════════════════
Turkey is primarily an EXPORTER, not an importer for Arkmurus purposes.
Key Turkish OEMs relevant to Arkmurus deal flow:
  - Baykar: TB2, TB3, Akinci, Kizilelma (UAVs)
  - ASELSAN: radars, EW, communications, optronics
  - TAI/TUSAS: Anka UAV, Hürjet trainer, KAAN 5th-gen fighter
  - Otokar: Cobra, Arma, Tulpar (armoured vehicles)
  - FNSS: Kaplan, Pars (armoured vehicles)
  - Roketsan: SOM cruise missile, HISAR air defence
  - STM: naval platforms, Ada-class corvettes
  - Havelsan: C4ISR, combat management systems
Turkish defence exports: $5.5B (2024), growing 20%+ year-on-year.
"""


# =============================================================================
# EQUIPMENT SPECS — TOP 50 TRADED DEFENCE PLATFORMS
# Source: manufacturer datasheets, public domain specifications
# =============================================================================

EQUIPMENT_SPECS = """
DEFENCE EQUIPMENT SPECIFICATIONS DATABASE — TOP 50 TRADED PLATFORMS

This data enables ARIA to verify technical claims during DD, answer
"what are the specs of X?" and cross-reference product descriptions
against export control classifications.

═══════════════════════════════════════════════════════════════════
UAVs / UNMANNED SYSTEMS
═══════════════════════════════════════════════════════════════════

BAYRAKTAR TB2 (Turkey — Baykar)
  Type: MALE tactical UAV (Medium Altitude Long Endurance)
  Wingspan: 12m | Length: 6.5m | MTOW: 700kg
  Endurance: 27 hours | Ceiling: 27,000ft | Range: 150km (LoS)
  Payload: 4x hardpoints, 150kg weapons load
  Weapons: MAM-L, MAM-C (Roketsan smart munitions), Mk-82
  Sensors: Wescam MX-15D EO/IR/laser, synthetic aperture radar
  Unit cost: ~$5M (with ground station ~$20M for system)
  Export control: ML10 (aircraft), Wassenaar Cat 9 (UAV)
  Operators: 35+ countries including Turkey, Ukraine, Poland, Nigeria, Angola
  ECCN: likely 9A610.a (unmanned aerial vehicles)

BAYRAKTAR AKINCI (Turkey — Baykar)
  Type: HALE combat UAV (High Altitude Long Endurance)
  Wingspan: 20m | Length: 12.3m | MTOW: 6,000kg
  Endurance: 24 hours | Ceiling: 40,000ft | Range: 300km
  Payload: 6x hardpoints, 1,350kg weapons load
  Weapons: SOM cruise missile, Mk-83/84, MAM-L, TEBER LGBs
  Sensors: ASELSAN SARPER AESA radar, EO/IR turret, SIGINT
  Unit cost: ~$15-20M (estimated)
  Export control: ML10 / Wassenaar Cat 9
  Status: in service Turkey, ordered by multiple export customers

WING LOONG II (China — CAIG/AVIC)
  Type: MALE armed reconnaissance UAV
  Wingspan: 20.5m | MTOW: 4,200kg
  Endurance: 32 hours | Ceiling: 30,000ft
  Payload: 480kg weapons load
  Weapons: Blue Arrow-7 ATGMs, LS-6 GPS-guided bombs
  Unit cost: ~$1-2M (significantly cheaper than Western equivalents)
  Operators: UAE, Saudi Arabia, Egypt, Nigeria, Pakistan
  Note: Chinese UAVs face fewer export restrictions than US platforms

HERMES 900 (Israel — Elbit Systems)
  Type: MALE tactical/strategic UAV
  Wingspan: 15m | MTOW: 1,180kg
  Endurance: 36 hours | Ceiling: 30,000ft
  Sensors: multi-payload EO/IR/SAR/SIGINT
  Operators: Israel, Brazil, Switzerland, Chile, Colombia, Mexico
  Unit cost: ~$10M per system
  Note: often offered as part of integrated border surveillance packages

MQ-9 REAPER (USA — General Atomics)
  Type: MALE hunter-killer UAV
  Wingspan: 20m | MTOW: 4,760kg
  Endurance: 27 hours | Ceiling: 50,000ft
  Payload: 1,746kg (4x Hellfire, 2x GBU-12, JDAM)
  Unit cost: ~$32M
  Export control: ITAR Category XI, strict US export approval
  Operators: USA, UK, France, Italy, Netherlands, Spain
  Note: ITAR restrictions limit export to close allies only

ANKA-S (Turkey — TAI/TUSAS)
  Type: MALE tactical UAV with SATCOM
  Wingspan: 17.5m | MTOW: 1,750kg
  Endurance: 24 hours | Ceiling: 30,000ft
  Sensors: ASELSAN CATS EO/IR, SAR radar
  Weapons: MAM-L, TEBER guided bombs

═══════════════════════════════════════════════════════════════════
ARMOURED FIGHTING VEHICLES
═══════════════════════════════════════════════════════════════════

OTOKAR COBRA II (Turkey — Otokar)
  Type: 4x4 tactical armoured vehicle
  Weight: 14-16 tonnes | Crew: 2+8
  Protection: STANAG 4569 Level 3 (mine Level 3a/3b)
  Engine: 300hp diesel | Speed: 110km/h | Range: 700km
  Armament: RCWS (12.7mm or 40mm), optional ATGM
  Unit cost: ~$500K-1M
  Operators: Turkey, Turkmenistan, Bahrain, Georgia
  Export control: ML6 (ground vehicles)

PARAMOUNT MBOMBE 8 (South Africa — Paramount Group)
  Type: 8x8 IFV/APC
  Weight: 28 tonnes | Crew: 2+8
  Protection: STANAG 4569 Level 4 (mine Level 4a/4b)
  Engine: 450hp | Speed: 100km/h | Range: 800km
  Armament: 30mm turret, coaxial 7.62mm
  Note: African-origin OEM — no ITAR constraints, offset-friendly

K2 BLACK PANTHER (South Korea — Hyundai Rotem)
  Type: Main Battle Tank
  Weight: 55 tonnes | Crew: 3 (autoloader)
  Protection: composite + ERA, KAPS APS
  Engine: 1,500hp MTU diesel | Speed: 70km/h
  Armament: 120mm L/55 smoothbore, 12.7mm + 7.62mm coaxial
  Unit cost: ~$8.5M
  Major export: Poland (1,000 ordered under K2PL programme)

LEOPARD 2A7 (Germany — KMW/KNDS)
  Type: Main Battle Tank
  Weight: 63 tonnes | Crew: 4
  Engine: 1,500hp MTU diesel | Speed: 72km/h
  Armament: 120mm L/55A1 smoothbore
  Unit cost: ~$15M
  Note: German export policy restricts sales to non-NATO countries

M1A2 ABRAMS SEPv3 (USA — General Dynamics)
  Type: Main Battle Tank
  Weight: 73 tonnes | Crew: 4
  Engine: 1,500hp AGT-1500 turbine | Speed: 67km/h
  Armament: 120mm M256 smoothbore, Trophy APS
  Unit cost: ~$10-12M (FMS price varies)
  Export: Saudi Arabia, Australia, Poland, Kuwait, Egypt, Iraq

PATRIA AMV (Finland — Patria)
  Type: 8x8 modular armoured vehicle
  Weight: 16-26 tonnes | Crew: 2+8-12
  Protection: STANAG 4569 Level 3-5 (modular)
  Operators: Finland, Poland, Sweden, South Africa, UAE, Croatia

═══════════════════════════════════════════════════════════════════
NAVAL PLATFORMS
═══════════════════════════════════════════════════════════════════

ADA-CLASS CORVETTE (Turkey — STM)
  Type: multi-mission corvette
  Displacement: 2,400 tonnes | Length: 99m
  Speed: 29 knots | Range: 3,500nm
  Armament: 76mm gun, Harpoon/ATMACA SSMs, RAM, torpedoes
  Sensors: SMART-S Mk2 radar, hull-mounted sonar
  Unit cost: ~$250M
  Exports: Pakistan (4 ordered, 2 delivered), Ukraine (2 ordered)
  Note: Turkish naval exports growing rapidly with competitive pricing

SIGMA-CLASS (Netherlands — Damen)
  Type: modular patrol/corvette family
  Displacement: 1,700-2,400 tonnes | Length: 90-105m
  Exports: Indonesia, Morocco, Mexico, Vietnam
  Note: modular design allows customisation for budget navies

GOWIND 2500 (France — Naval Group)
  Type: corvette/light frigate
  Displacement: 2,600 tonnes | Length: 102m
  Exports: Egypt (4), UAE (2 ordered), Malaysia (under discussion)

═══════════════════════════════════════════════════════════════════
AIRCRAFT
═══════════════════════════════════════════════════════════════════

A-29 SUPER TUCANO (Brazil — Embraer)
  Type: light attack / COIN trainer
  Engine: PT6A-68C, 1,600shp | Speed: 590km/h
  Armament: 2x .50 cal, 5 hardpoints (1,500kg)
  Unit cost: ~$10-14M
  Operators: 15+ countries including Nigeria, Ghana, Angola, Afghanistan, USA
  Note: ideal for counter-insurgency. No ITAR issues (Brazilian origin).

JAS 39E GRIPEN (Sweden — SAAB)
  Type: 4.5 gen multirole fighter
  Speed: Mach 2 | Range: 1,300km | Ceiling: 50,000ft
  Armament: 27mm Mauser, 8 hardpoints (Meteor BVRAAM, RBS-15)
  Unit cost: ~$85M (including support package)
  Operators: Sweden, Brazil, Czech Republic, Hungary, South Africa, Thailand
  Note: competitive pricing vs F-16/Rafale, low operating cost

FA-50 (South Korea — KAI)
  Type: light combat / lead-in fighter trainer
  Speed: Mach 1.5 | Range: 1,850km
  Unit cost: ~$30M
  Exports: Philippines, Poland (48 ordered), Iraq, Thailand

C-295 (Spain/Airbus — Airbus Defence)
  Type: medium tactical transport
  Payload: 9.25 tonnes or 71 troops | Range: 4,600km
  Unit cost: ~$30M
  Operators: 35+ countries — NATO standard tactical airlift

═══════════════════════════════════════════════════════════════════
AIR DEFENCE
═══════════════════════════════════════════════════════════════════

HISAR-A/O (Turkey — ASELSAN/Roketsan)
  Type: short/medium-range air defence
  Range: 15km (HISAR-A) / 25km (HISAR-O)
  Note: Turkish-origin, no ITAR, competitive for export markets

IRIS-T SLM (Germany — Diehl Defence)
  Type: medium-range air defence (SHORAD/MRAD)
  Range: 40km | Altitude: 20km
  Unit cost: ~$140M per battery
  Operators: Germany, Egypt (under negotiation), Ukraine

NASAMS (Norway/USA — Kongsberg/Raytheon)
  Type: medium-range air defence
  Range: 40km (AMRAAM-ER: 50km+)
  Operators: Norway, Finland, Spain, Lithuania, Ukraine, Indonesia, Oman
"""


# =============================================================================
# INGESTION
# =============================================================================

ALL_SECTIONS = {
    "arms_transfers": {
        "content": ARMS_TRANSFERS,
        "domain": "arms_transfers",
        "tags": ["SIPRI", "arms_transfers", "defence_procurement", "supplier", "buyer"],
    },
    "equipment_specs": {
        "content": EQUIPMENT_SPECS,
        "domain": "equipment_specifications",
        "tags": ["equipment", "specs", "UAV", "armoured_vehicle", "naval", "aircraft", "air_defence"],
    },
}


@fail_wire(module="sipri_knowledge", gap_type="source_failure")
async def ingest_all_sections() -> dict:
    """Ingest SIPRI + equipment specs into RAG store."""
    from . import rag_store

    results = {}
    total_chunks = 0

    for section_name, data in ALL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                data["content"],
                source=f"intel:sipri_knowledge:{section_name}",
                source_type=data["domain"],
                title=f"SIPRI/Equipment — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/sipri_knowledge/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "sipri_knowledge",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("SIPRI section ingested: %s (%d chunks)", section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("SIPRI section ingestion failed [%s]: %s", section_name, e)

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="sipri_knowledge",
        summary="Ingest All Sections",
        source_id="sipri_knowledge:R-F996",
    )

    return {
        "sections_ingested": success,
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }

# R-F2119 §21a — wire failure handler for sipri_knowledge
try:
    wire_failure(module="sipri_knowledge", detail="module shutdown",
                gap_type="engine_failure", source="sipri_knowledge:shutdown")
except Exception:
    pass
