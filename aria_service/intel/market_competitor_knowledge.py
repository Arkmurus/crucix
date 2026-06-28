"""ARIA Market and Competitor Intelligence Knowledge Module.

Permanent knowledge base covering global defence market sizing, data
sources (SIPRI, IISS, Jane's), defence spending by region, budget
execution rate intelligence, demand signal analysis, and competitor
mapping for Russia, China, Turkey, France, Israel, and South Africa.

Ingested into ARIA's knowledge store and RAG store at startup so that
market and competitor queries automatically surface relevant domain
context.  The ``get_market_context`` helper performs lightweight
keyword matching for prompt-time injection (similar to
``nato_standards.get_nato_context``).

All sources: open-source, publicly verifiable.
"""
from __future__ import annotations

import logging
logger = logging.getLogger("aria.market_competitor_knowledge")


# =============================================================================
# KNOWLEDGE BLOCKS
# =============================================================================

GLOBAL_DEFENCE_MARKET = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 GLOBAL DEFENCE MARKET INTELLIGENCE                                 \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

KEY DATA SOURCES FOR DEFENCE MARKET INTELLIGENCE

SIPRI (Stockholm International Peace Research Institute):
  Arms Transfers Database: https://armstransfers.sipri.org
    Shows: actual deliveries of major conventional weapons by country.
    Unit: TIV (Trend Indicator Value) -- comparable unit not USD.
    Use: Who is buying from whom. Supply relationship mapping.
    Search: exporter + importer + date range.

  Military Expenditure Database: https://www.sipri.org/databases/milex
    Shows: defence spending in constant USD and % of GDP.
    Updated annually. Use for: budget trend analysis.
    Find: Angola, Mozambique, Nigeria, Kenya -- all included.

  Arms Industry Database: https://www.sipri.org/databases/armsindustry
    Shows: top 100 arms companies by revenue.
    Useful for: identifying potential OEM clients.

  Arms Embargo Database: https://www.sipri.org/databases/embargoes
    Shows: all multilateral arms embargoes currently in force.
    ARIA checks this before any new market engagement.

IISS Military Balance (Annual Publication):
  Publisher: International Institute for Strategic Studies, London.
  Content: Defence budgets, force structures, equipment inventories.
  Accessible: library access or subscription. Extracts widely published.
  Use: understand what a country operates and what they are missing.

Jane's by Janes (IHS Markit / S&P Global):
  Professional subscription service.
  Covers: Defence budgets, equipment, procurement programmes.
  ARIA references Jane's indirectly through secondary reporting.

GlobalFirepower.com:
  Free aggregator. Useful for quick country comparisons.
  Quality: lower accuracy on specifics -- verify with SIPRI/IISS.
  Use for: quick orientation, not primary sourcing.

ArmyRecognition.com:
  Free military equipment database.
  Covers: specifications, photos, operational history.
  Trust: Tier C -- useful for technical specs, verify with manufacturer.

GLOBAL DEFENCE SPENDING -- KEY FIGURES (SIPRI 2024)

GLOBAL TOTAL: ~$2.4 trillion (2024, SIPRI estimate).
Growth: 6.8% real increase (2023-2024) -- largest annual increase since 2009.
Driver: Russia-Ukraine war, China-Taiwan tension, Sahel instability.

TOP SPENDERS (approximate, SIPRI):
  1. USA: ~$916 billion (38% of global total)
  2. China: ~$296 billion (12%)
  3. Russia: ~$109 billion (4.5%)
  4. India: ~$84 billion (3.5%)
  5. Saudi Arabia: ~$75 billion (3.1%)
  6. UK: ~$74 billion
  7. Germany: ~$67 billion
  8. Ukraine: ~$65 billion (wartime)
  9. France: ~$62 billion
  10. Japan: ~$56 billion

AFRICA DEFENCE SPENDING (approximate, SIPRI):
  Nigeria: ~$4.8 billion
  Egypt: ~$4.6 billion
  Algeria: ~$9.1 billion (largest in Africa)
  Morocco: ~$5.4 billion
  South Africa: ~$2.8 billion
  Angola: ~$1.9 billion
  Kenya: ~$0.9 billion
  Ethiopia: ~$0.9 billion
  Mozambique: ~$0.3 billion
  Tanzania: ~$0.5 billion

CPLP AFRICA -- PRIORITY MARKETS:
  Angola: ~$1.9B total, ~40-60% execution = ~$760M-$1.1B actual spend.
  Mozambique: ~$0.3B total, ~55-75% execution = ~$165M-$225M actual.
  Guinea-Bissau: <$0.05B -- very small, externally funded security dominant.
  Cape Verde: <$0.1B -- EEZ-focused, internationally funded.

DEMAND SIGNAL ANALYSIS -- HOW ARIA PREDICTS PROCUREMENT

SIGNAL TYPE 1 -- CONFLICT EVENTS:
  IED attack on military convoy -> MRAP procurement follows (6-18 months)
  Aircraft/helicopter loss -> replacement procurement (12-36 months)
  Naval incident -> patrol vessel upgrade (12-24 months)
  ARIA tracks: ACLED conflict data for civilian/military casualty events.

SIGNAL TYPE 2 -- POLICY CHANGES:
  New defence white paper -> budget and priority signal (12-24 months)
  New president/minister of defence -> policy review period -> spending
  Defence cooperation agreement signed -> joint procurement may follow
  UN mission mandate -> TCC contributes equipment -> replacement need

SIGNAL TYPE 3 -- BUDGET ANNOUNCEMENTS:
  Parliamentary budget speech -> defence line item increase -> opportunity
  IMF programme exit -> more fiscal space -> procurement resumes
  Oil price (Angola/Nigeria) -> correlates with defence spending capacity

SIGNAL TYPE 4 -- TRAINING RELATIONSHIPS:
  EU training mission established -> future equipment standardisation demand
  US IMET training -> long-term preference for US-compatible equipment
  Portuguese military training -> alignment with Portuguese/EU catalogue
  Chinese PLA training -> preference for Chinese equipment

SIGNAL TYPE 5 -- EQUIPMENT AGE:
  Calculate: service entry date + typical service life = replacement window
  Soviet equipment (entered service 1970s-1980s): replacement overdue
  Cold War era aircraft (MiG-21 etc): almost universally being replaced
  ARIA applies this to: force inventories globally
"""

COMPETITOR_MAPPING = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  ARIA \u2014 GLOBAL COMPETITOR INTELLIGENCE MAPPING                             \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

RUSSIA / ROSOBORONEXPORT -- POST-2022 POSITION

Current status: Heavily sanctioned. Supply chain severely disrupted.
Production: focused on Ukraine war. Export capacity reduced.
SANCTIONS: EU, UK, US comprehensive arms embargo on Russia.
Effect on Africa: Russian spare parts supply disrupted for Su-30, T-72, etc.
This disruption is a major market opportunity driver.

Countries with Russian equipment needing alternatives:
  Angola: MiG-21/23/29, Su-22/27/30, T-54/55/62/72, BMP-1/2, BTR-60/80
  Mozambique: T-54/55, BMP-1, BTR-60, small arms (AKM)
  Guinea-Bissau: AKM/RPG -- Russian-calibre ammunition ongoing need

Africa Corps (Wagner successor, rebranded 2024):
  Operational: Mali, Burkina Faso, Niger, Libya, CAR, Sudan.
  Former: Mozambique (2019-2021), Syria.
  Capability: PMC advisory + actual combat + equipment supply.
  ARIA tracks presence as: competitor + threat assessment + market indicator.
  Where Africa Corps is entrenched: Western access more difficult.
  Where Africa Corps has failed/been expelled: market opens.

BD SIGNAL: Russian disruption in African markets = replacement demand.
Every country with Russian-origin equipment that cannot get spares =
opportunity for Western/non-Russian supplier with compatible solution.

CHINA / NORINCO / CSGC -- AFRICA STRATEGY

NORINCO (China North Industries Group): primary ground systems exporter.
AVIC: aircraft and aerospace.
CSGC (China South Industries Group): ammunition and explosives.
CASC: missiles and space.
China Shipbuilding Industry: naval.

COMPETITIVE ADVANTAGES of Chinese arms:
  Price: 20-40% cheaper than Western equivalents typically.
  No political conditions: no human rights, no IHL requirements.
  Package deals: arms + infrastructure + financing in one package.
  BRI: Belt and Road Initiative often bundles defence with roads/ports.
  No ITAR: Chinese equipment avoids US re-export restrictions.
  Speed: delivery timelines often faster than Western procurement.

WEAKNESSES of Chinese arms:
  Quality concerns: some platforms have poor field reliability record.
  Technology transfer limited: after-sales dependency on China.
  Maintenance: spares supply can be unreliable.
  VT-4 tank: limited combat proven record vs Western alternatives.
  Wing Loong UAS: less capable than TB2 in many operational scenarios.
  Western interoperability: Chinese systems not STANAG compliant.

KEY CHINESE SYSTEMS IN AFRICA:
  VT-4 / MBT-3000 (MBT): sold to Nigeria, Pakistan, Morocco.
  Type 056 Jiangdao (corvette): sold to Nigeria, Bangladesh.
  Wing Loong II (MALE UAS): Mali, Saudi Arabia, UAE, Egypt.
  CH-4 (UAS): sold to Iraq, Jordan, Nigeria.
  PKM (licensed) / QBZ-95 derivatives: various African states.
  AR-3 / SR-5 MLRS: Saudi Arabia, multiple African states.

TRACKING: when China wins in a market, analyse what they offered vs
what could have been offered. Identify quality gap = re-entry opportunity.

TURKEY / SSB -- RELEVANT COMPETITOR AND PARTNER

Turkey is BOTH competitor and potential partner/client.

KEY TURKISH EXPORTERS (SAHA members):
  Baykar: Bayraktar TB2, TB3, Akinci UAS -- Africa's #1 armed drone.
  ASELSAN: electronics, communications, fire control systems.
  Roketsan: rockets, missiles, ammunition.
  STM: maritime systems, autonomous systems.
  FNSS: armoured fighting vehicles (ACV-15, Kaplan MT).
  BMC: military trucks and armoured vehicles (Kirpi MRAP).
  Otokar: wheeled and tracked armoured vehicles (Cobra).
  TAI (TUSAS): Hurkus trainer/attack, T129 ATAK helicopter.
  MKE: ammunition manufacturer.

TURKISH ADVANTAGES (highly relevant):
  NO ITAR: Turkish-origin platforms avoid US re-export restrictions.
  Bayraktar TB2: no export limitations, significantly cheaper than MQ-9.
  Political: Turkey is NATO member -- Western political alignment.
  Combat proven: TB2 proven in Libya, Nagorno-Karabakh, Ukraine, Mali.
  Price: competitive vs European platforms.
  Financing: Eximbank Turkey offers competitive export financing.

TURKEY-AFRICA ENGAGEMENT:
  Active in: Somalia (training + Bayraktar sales), Ethiopia (attempted),
  Libya (active), Morocco, Tunisia, Kenya (discussions).
  Strategic: Turkey has defence agreements with 20+ African states.
  Ambition: Africa is a stated priority market for Turkish defence exports.

MONITORING SOURCES:
  SSB.gov.tr: Turkish procurement and export announcements.
  saha.org.tr: SAHA defence industry association news.
  defenceturkey.com: English-language industry news.
  savunmasanayii.com: Turkish-language industry news.

FRANCE -- RETRENCHMENT AND MARKET OPPORTUNITY

France was the dominant external defence partner in Francophone Africa.
2022-2024 WITHDRAWALS:
  Barkhane (Mali): expelled 2022.
  Mali, Niger, Burkina Faso: France withdrawn.
  Chad: partial rebalancing 2024.
  Senegal: renegotiating basing.
  Cote d'Ivoire: under pressure.

WHAT THIS CREATES:
  Equipment vacuum: French-trained forces face supply disruption.
  Some French clients looking for new Western suppliers.
  France is less of a competitor in Anglophone Africa.
  Francophone Africa remains French domain -- but weakening.
  Entry opportunity: position as alternative to French-dependent supply.

French equipment in Africa: VAB APCs, Mirage jets (many older types),
FAMAS rifles, Famas ammunition, MILAN ATGM, HOT ATGM.
Any of these creating supply/support problems = market opening.

ISRAEL / SIBAT -- AFRICA ENGAGEMENT

Israel has significant defence export relationships in Africa.
Key Israeli exporters: Elbit Systems, IAI (Israel Aerospace Industries),
Rafael Advanced Defence Systems, Soltam (artillery).

Israeli systems in Africa: Hermes 450/900 UAS (Rwanda, Kenya),
Spike ATGM (widely exported), LAHAT gun-launched ATGM, border security.
IMI (now Elbit): ammunition -- 155mm including Excalibur-equivalent.

POST-GAZA (2023+): Israeli exports facing political scrutiny.
Some African nations reducing engagement due to domestic political pressure.
This creates market opening in some states where Israel was incumbent.
ARIA tracks: Israeli export licence controversies for market intelligence.

SOUTH AFRICA / DENEL -- DOMESTIC MARKET AND COMPETITOR

Denel: South Africa's state defence manufacturer. CURRENTLY IN CRISIS.
  Financial: multiple rounds of government bailout, near-collapse.
  Workforce: severely reduced. Production capacity limited.
  Products (historically): G5/G6 artillery, RG-31 Nyala MRAP,
  Casspir MRAP, Rooivalk attack helicopter, Ratel IFV.

RG-31 Nyala (now General Dynamics Land Systems - South Africa):
  One of the most deployed MRAPs in African markets. No ITAR.
  South African origin = no US re-export restrictions.
  Highly relevant for Mozambique, Angola, COIN environments.

BD NOTE: Denel's weakness creates opportunity.
Where Denel has strong through-life support relationships in Africa,
their inability to deliver creates competitor entry opportunity.
"""


# =============================================================================
# SECTION REGISTRY
# =============================================================================

MARKET_SECTIONS: dict[str, dict] = {
    "global_defence_market": {
        "content": GLOBAL_DEFENCE_MARKET,
        "tags": [
            "market-intel", "SIPRI", "IISS", "defence-spending",
            "budget", "execution-rate", "demand-signals", "Africa",
        ],
        "domain": "market_intel",
    },
    "competitor_mapping": {
        "content": COMPETITOR_MAPPING,
        "tags": [
            "competitor-intel", "Russia", "China", "Turkey", "France",
            "Israel", "South-Africa", "Denel", "Wagner", "Africa-Corps",
            "NORINCO", "Baykar", "SSB", "market-positioning",
        ],
        "domain": "competitor_intel",
    },
}


# =============================================================================
# SEARCH INDEX (built once at import time)
# =============================================================================

_SEARCH_INDEX: list[dict] = []


def _build_search_index() -> None:
    """Pre-compute a flat list of searchable entries from all sections."""
    for section_name, data in MARKET_SECTIONS.items():
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

def get_market_context(query: str) -> str:
    """Return relevant market/competitor knowledge for a given query.

    Performs keyword matching against the knowledge blocks and returns
    a formatted string suitable for inclusion in ARIA prompts. Similar
    to ``nato_standards.get_nato_context``.

    Args:
        query: Free-text query (e.g., "Russian equipment Angola",
               "SIPRI defence spending", "Baykar TB2 Africa").

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

    # Sort by score descending, take top 2 (only 2 sections)
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:2]

    parts: list[str] = [
        "=== ARIA Market & Competitor Intelligence Context ===",
        "",
    ]
    for _score, entry in top:
        parts.append(f"--- {entry['id'].replace('_', ' ').upper()} ---")
        # Truncate to first ~3000 chars to keep prompt size manageable
        content = entry["content"].strip()
        if len(content) > 3000:
            content = content[:3000] + "\n[... truncated ...]"
        parts.append(content)
        parts.append("")

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="market_competitor_knowledge",
        summary="Get Market Context",
        source_id="market_competitor_knowledge:R-F996",
    )

    return "\n".join(parts)


# =============================================================================
# INGESTION (called at startup from _seed_knowledge_bg)
# =============================================================================

async def ingest_to_knowledge() -> dict:
    """Store market/competitor knowledge into the knowledge + RAG stores.

    Ingests all sections as permanent CONFIRMED facts via
    ``knowledge.store_fact``, and as RAG documents via
    ``rag_store.ingest_document`` for semantic retrieval.

    Follows the same pattern as ``dd_case_library.ingest_all_cases()``
    and ``nato_standards.ingest_to_knowledge()``.
    """
    from . import knowledge, rag_store

    results: dict = {}
    total_chunks = 0

    for section_name, data in MARKET_SECTIONS.items():
        try:
            # Permanent knowledge fact
            await knowledge.store_fact(
                topic=f"market_competitor_{section_name}",
                content=data["content"],
                source=f"market_competitor_knowledge:{section_name}",
                confidence="CONFIRMED",
            )

            # RAG ingest for semantic search
            result = await rag_store.ingest_document(
                text=data["content"],
                source=f"market_competitor_knowledge:{section_name}",
                source_type="market_competitor_knowledge",
                title=f"Market & Competitor Intel -- {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/market_competitor_knowledge/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "market_competitor_knowledge",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info(
                "Market/competitor section ingested: %s (%d chunks)",
                section_name, chunks,
            )
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error(
                "Market/competitor ingestion failed [%s]: %s", section_name, e,
            )

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info(
        "Market/competitor knowledge ingestion: %d/%d sections, %d total chunks",
        success, len(MARKET_SECTIONS), total_chunks,
    )
    return {
        "sections_ingested": success,
        "total_sections": len(MARKET_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }

# R-F2119 §21a — wire failure handler for market_competitor_knowledge
try:
    wire_failure(module="market_competitor_knowledge", detail="module shutdown",
                gap_type="engine_failure", source="market_competitor_knowledge:shutdown")
except Exception:
    pass
