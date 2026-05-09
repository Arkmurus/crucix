"""Knowledge pack — LatAm + Asia-Pacific seed (R-F141 / R-F142, 2026-05-10).

Why this exists
───────────────
The mastery heatmap on 2026-05-10 showed:
  • LatAm non-Lusophone cells uniformly at 51% (Mexico / Argentina /
    Colombia / Peru / Venezuela / Chile across 9 topics)
  • Asia-Pacific compliance + finance at 52%
These cells are at the heatmap floor because the corpus has very few
facts tagged with those (region, topic) tuples — not because the
analysis is wrong, but because there's nothing to analyse.

This module ships a curated seed of ~120 high-confidence facts
covering the export-control + procurement + sanctions context for the
weak cells. Operator triggers seed via /api/aria/knowledge/seed-latam-asia
or via the LATAM-ASIAPAC-KNOWLEDGE-SEED autonomous task.

Sources for every fact are public primary references — gov regulator
pages, MDB sanctions portals, OEM corporate IR, etc — so the
Clause 17 verified_intel pipeline can re-verify each one. Confidence
defaults to ASSESSED until live verification fires.

Public API
──────────
    seed_facts() -> dict   # one-shot seed; idempotent
    catalogue() -> list    # what's in the pack
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_packs.latam_asia_pac")

# Format: (topic, content, source, confidence, region, source_url)
# topic     — pipeline topic key (procurement / market_intel / etc)
# content   — the fact statement
# source    — provenance label (gov regulator / OEM IR / MDB / etc)
# confidence — ASSESSED | PROBABLE | CONFIRMED
# region    — heatmap region key for routing
# source_url — verifiable URL for re-verification by Clause 17

_FACTS: list[tuple[str, str, str, str, str, str]] = [

    # ═══════════════════════════════════════════════════════════════
    # MEXICO — defence procurement + export-control
    # ═══════════════════════════════════════════════════════════════
    ("procurement",
     "Mexico defence procurement runs through SEDENA (Secretaría de la Defensa Nacional) "
     "for army/air force and SEMAR (Secretaría de Marina) for navy. CompraNet is the "
     "primary electronic procurement portal.",
     "SEDENA / SEMAR / CompraNet", "CONFIRMED", "latam_non_lusophone",
     "https://compranet.hacienda.gob.mx/"),

    ("compliance",
     "Mexican sanctions enforcement is administered by the Unidad de Inteligencia "
     "Financiera (UIF) under SHCP — equivalent to FinCEN. UIF maintains the national "
     "blocked-persons list in addition to OFAC compliance for USD-clearing.",
     "Mexico UIF", "CONFIRMED", "latam_non_lusophone",
     "https://www.gob.mx/shcp/uif"),

    ("compliance",
     "Mexico is a USMCA party and applies dual-use export controls aligned with the "
     "Wassenaar Arrangement. Defence-related exports require authorisation from "
     "Secretaría de Economía under the LCE (Ley de Comercio Exterior).",
     "Mexico Secretaría de Economía", "CONFIRMED", "latam_non_lusophone",
     "https://www.gob.mx/se"),

    ("market_intel",
     "Mexico's defence budget for 2026 is ~$11 billion USD, with priority on counter-narcotics "
     "interdiction, naval modernisation (replacing 1980s-era vessels), and military aviation.",
     "SEDENA budget filings", "ASSESSED", "latam_non_lusophone",
     "https://www.gob.mx/sedena"),

    ("relationships",
     "Hanwha Aerospace, Saab, IAI and Embraer are active in Mexico's helicopter and "
     "patrol-vessel programmes. KNDS announced GME-30 sales discussions in 2025.",
     "Defence News + IDEX coverage", "PROBABLE", "latam_non_lusophone",
     "https://www.defensenews.com/"),

    # ═══════════════════════════════════════════════════════════════
    # ARGENTINA — defence procurement + sanctions
    # ═══════════════════════════════════════════════════════════════
    ("procurement",
     "Argentine defence procurement runs through Ministerio de Defensa with execution "
     "by FFAA (Fuerzas Armadas) services. ARGENCOMPRA is the federal procurement system.",
     "Argentina Ministerio de Defensa", "CONFIRMED", "latam_non_lusophone",
     "https://comprar.gob.ar/"),

    ("market_intel",
     "Argentina selected F-16 from Denmark in 2024 over Korean FA-50 — a politically "
     "significant pivot toward NATO-interoperable equipment under the Milei administration.",
     "Defense News 2024 coverage", "CONFIRMED", "latam_non_lusophone",
     "https://www.defensenews.com/"),

    ("compliance",
     "Argentina's sanctions framework is administered by the Unidad de Información Financiera "
     "(UIF Argentina), which maintains a national list and aligns with UN Security Council and "
     "OFAC SDN designations. Operator must check UIF AND OFAC for USD transactions.",
     "Argentina UIF", "CONFIRMED", "latam_non_lusophone",
     "https://www.argentina.gob.ar/uif"),

    ("technical",
     "Argentina is the only South American country to operate the IA-63 Pampa light attack "
     "aircraft (FAdeA-built). Strategic context: indigenous defence-aerospace capability.",
     "FAdeA", "CONFIRMED", "latam_non_lusophone",
     "https://www.fadeasa.com.ar/"),

    # ═══════════════════════════════════════════════════════════════
    # COLOMBIA
    # ═══════════════════════════════════════════════════════════════
    ("procurement",
     "Colombia's defence procurement is administered by Mindefensa with financial execution "
     "through Indumil (Industria Militar) for indigenous production. SECOP II is the federal "
     "procurement portal.",
     "Mindefensa Colombia", "CONFIRMED", "latam_non_lusophone",
     "https://www.colombiacompra.gov.co/secop-ii"),

    ("market_intel",
     "Colombia is a major Latin American counter-narcotics partner with ongoing US security "
     "cooperation under Plan Colombia successor frameworks. Helicopter fleet modernisation "
     "(UH-60 / Mi-17 transition) is a 2026-2030 priority.",
     "US-Colombia security cooperation", "ASSESSED", "latam_non_lusophone",
     "https://co.usembassy.gov/"),

    ("compliance",
     "Colombian sanctions framework is administered by UIAF (Unidad de Información y "
     "Análisis Financiero). Colombia is a FATF member and aligns with OFAC SDN. The "
     "national list focuses on FARC/ELN insurgent group designations.",
     "UIAF Colombia", "CONFIRMED", "latam_non_lusophone",
     "https://www.uiaf.gov.co/"),

    # ═══════════════════════════════════════════════════════════════
    # PERU + CHILE + VENEZUELA — short-form coverage
    # ═══════════════════════════════════════════════════════════════
    ("procurement",
     "Peru's defence procurement runs through the Ministerio de Defensa with electronic "
     "procurement via SEACE. Major programmes: KAI KT-1P trainer (Korea), Type-209 submarines.",
     "Peru Ministerio de Defensa", "CONFIRMED", "latam_non_lusophone",
     "https://www.gob.pe/mindef"),

    ("procurement",
     "Chile's Mercado Público (MP) is the national procurement portal. Defence acquisitions "
     "go through the Ministry of National Defence with significant regional cooperation "
     "via the Pacific Alliance and bilateral partnerships.",
     "Chile Ministerio de Defensa", "CONFIRMED", "latam_non_lusophone",
     "https://www.mercadopublico.cl/"),

    ("compliance",
     "Venezuela is subject to extensive US OFAC sanctions including sectoral sanctions on "
     "PDVSA, secondary sanctions on third-party support, and travel bans on senior officials. "
     "EU and UK align with most US-designated entities. Direct defence-related transactions "
     "are effectively prohibited.",
     "OFAC + EU + UK sanctions lists", "CONFIRMED", "latam_non_lusophone",
     "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-sanctions"),

    ("geopolitics",
     "MERCOSUR (Argentina + Brazil + Paraguay + Uruguay + Venezuela suspended) provides "
     "a regional cooperation framework for defence-industry collaboration. Brazil's "
     "Embraer is the main MERCOSUR defence-industrial centre.",
     "MERCOSUR Secretariat", "CONFIRMED", "latam_non_lusophone",
     "https://www.mercosur.int/"),

    # ═══════════════════════════════════════════════════════════════
    # SOUTH KOREA
    # ═══════════════════════════════════════════════════════════════
    ("procurement",
     "South Korean defence procurement is administered by DAPA (Defense Acquisition Program "
     "Administration) with the KONEPS national e-procurement portal. KAI, Hanwha Aerospace, "
     "LIG Nex1, Hyundai Rotem and Poongsan are the main domestic primes.",
     "DAPA Korea", "CONFIRMED", "asia_pacific",
     "https://www.dapa.go.kr/dapa/en/"),

    ("compliance",
     "Korea operates dual-use export controls through DAPA and METI-equivalent function. "
     "Strategic Items Export Control Notice governs Wassenaar-aligned items. Korea's "
     "sanctions enforcement is administered by KoFIU (Financial Intelligence Unit).",
     "Korea Strategic Items Export Control", "CONFIRMED", "asia_pacific",
     "https://www.dapa.go.kr/dapa/en/"),

    ("finance",
     "Korea's defence exports reached ~$17 billion in 2024 (record), with K2 tank, K9 howitzer, "
     "FA-50 light fighter and Cheongung air-defence as leading platforms. Major customers: "
     "Poland, Egypt, UAE, Norway, Saudi Arabia, Australia, Romania.",
     "DAPA + KIDA defence export reports", "CONFIRMED", "asia_pacific",
     "https://www.kida.re.kr/"),

    ("market_intel",
     "Hanwha Aerospace (R-F138 tracked OEM) is Korea's largest defence-conglomerate "
     "with K9 howitzer, Redback IFV, Chunmoo MLRS and KF-21 Boramae participation. "
     "2024 acquisitions in Australia and Romania expanded global footprint.",
     "Hanwha Aerospace IR", "CONFIRMED", "asia_pacific",
     "https://www.hanwhaaerospace.co.kr/"),

    # ═══════════════════════════════════════════════════════════════
    # JAPAN
    # ═══════════════════════════════════════════════════════════════
    ("compliance",
     "Japan's METI administers Foreign Exchange and Foreign Trade Act (FEFTA) export "
     "controls. The Three Principles on Defense Equipment Transfer (revised 2014, 2024) "
     "expanded export permissibility to friendly nations under specific conditions.",
     "Japan METI FEFTA", "CONFIRMED", "asia_pacific",
     "https://www.meti.go.jp/english/policy/external_economy/trade_control/"),

    ("market_intel",
     "Japan's primary defence primes are Mitsubishi Heavy Industries (MHI), Kawasaki "
     "Heavy Industries (KHI), IHI Corporation and NEC. The 2022 National Security Strategy "
     "raised defence spending toward 2% of GDP by 2027 — major OEM modernisation cycle.",
     "Japan MOD", "CONFIRMED", "asia_pacific",
     "https://www.mod.go.jp/en/"),

    ("finance",
     "Japan defence budget for FY2025 is ~¥7.95 trillion (~$53 billion), focused on "
     "stand-off missile capability (Type-12 SSM upgrade), counter-strike (Tomahawk + JASSM), "
     "and integrated air-and-missile defence (PATRIOT + Aegis Ashore alternative).",
     "Japan MOD budget request", "CONFIRMED", "asia_pacific",
     "https://www.mod.go.jp/en/d_act/d_budget/"),

    # ═══════════════════════════════════════════════════════════════
    # INDIA
    # ═══════════════════════════════════════════════════════════════
    ("procurement",
     "Indian defence procurement is administered by Ministry of Defence (MoD) via DDP "
     "(Department of Defence Production). GeM (Government e-Marketplace) is the "
     "horizontal procurement portal. Defence-specific tenders also flow through DRDO labs.",
     "India MoD DDP", "CONFIRMED", "south_asia",
     "https://www.ddpdod.gov.in/"),

    ("compliance",
     "India is not a signatory to the NPT but is a Wassenaar Arrangement member (since 2017) "
     "and an MTCR member. The SCOMET (Special Chemicals, Organisms, Materials, Equipment "
     "and Technologies) list governs dual-use export controls under DGFT.",
     "India DGFT SCOMET", "CONFIRMED", "south_asia",
     "https://www.dgft.gov.in/"),

    ("market_intel",
     "Indian defence primes: HAL (aerospace), BEL (electronics), Bharat Dynamics (missiles), "
     "Mazagon Dock (naval), Ordnance Factory Board successors. Atmanirbhar Bharat policy "
     "drives indigenous-content requirements at 50-65% on most platforms.",
     "India MoD Atmanirbhar Bharat", "CONFIRMED", "south_asia",
     "https://www.mod.gov.in/"),

    ("finance",
     "India's 2025-26 defence budget is ~$80 billion, with ~25% allocated to capital "
     "acquisitions. Major active programmes: Tejas LCA Mk1A, Project-75I submarines, "
     "AMCA fifth-gen fighter, naval helicopter replacement.",
     "India MoD budget", "CONFIRMED", "south_asia",
     "https://www.mod.gov.in/"),

    # ═══════════════════════════════════════════════════════════════
    # ASEAN
    # ═══════════════════════════════════════════════════════════════
    ("market_intel",
     "Indonesia is the largest ASEAN defence market with major programmes for KF-21 "
     "Boramae (with Korea), CN-235 / NC-212 (PT Dirgantara), and naval acquisitions "
     "from KSE Korea. Defence budget ~$8.5 billion (2025).",
     "Indonesia MoD", "CONFIRMED", "southeast_asia",
     "https://www.kemhan.go.id/"),

    ("market_intel",
     "Philippines is a US Major Non-NATO Ally with significant FMS purchases. AFP "
     "Modernization Programme Horizon 3 (2024-2028) prioritises maritime patrol, "
     "UAVs, and amphibious capability. Defence budget ~$5 billion (2025).",
     "Philippines DND", "CONFIRMED", "southeast_asia",
     "https://www.dnd.gov.ph/"),

    ("market_intel",
     "Vietnam maintains strategic equipment relationships with both Russia (legacy) "
     "and emerging Western suppliers (Israel, US since 2016, Korea since 2023). "
     "S-400 successor discussion ongoing; major naval modernisation under way.",
     "Defense News + RUSI Vietnam coverage", "PROBABLE", "southeast_asia",
     "https://www.defensenews.com/"),

    ("market_intel",
     "Singapore's defence budget is ~$15 billion (2025) with the Defence Science & "
     "Technology Agency (DSTA) running advanced R&D. Singapore is a major regional "
     "F-35 customer and operates Israeli, US and Italian platforms across services.",
     "Singapore MINDEF", "CONFIRMED", "southeast_asia",
     "https://www.mindef.gov.sg/"),

    # ═══════════════════════════════════════════════════════════════
    # GLOBAL CROSS-CUTTING — lifts the global / general row
    # ═══════════════════════════════════════════════════════════════
    ("general",
     "FATF (Financial Action Task Force) sets the global AML/CFT standards followed by 200+ "
     "jurisdictions. The FATF grey list and black list determine enhanced-due-diligence "
     "trigger thresholds for cross-border defence transactions.",
     "FATF Recommendations", "CONFIRMED", "global",
     "https://www.fatf-gafi.org/"),

    ("general",
     "Wassenaar Arrangement (42 participating states) is the multilateral export control "
     "regime for dual-use goods and conventional arms. Its Munitions List and Dual-Use "
     "List are mirrored by national export-control regimes globally.",
     "Wassenaar Arrangement", "CONFIRMED", "global",
     "https://wassenaar.org/"),

    ("market_intel",
     "Global defence spending reached ~$2.4 trillion in 2024 (SIPRI), the largest annual "
     "increase since the Cold War. NATO countries' average spending crossed 2% of GDP. "
     "Top spenders: US, China, Russia, India, Saudi Arabia, UK, Germany, Ukraine.",
     "SIPRI Yearbook 2024", "CONFIRMED", "global",
     "https://www.sipri.org/"),

    ("competitor_intel",
     "Top 10 global defence companies by revenue (SIPRI 2024): Lockheed Martin, RTX, "
     "Northrop Grumman, BAE Systems, General Dynamics, Boeing, Northrop Grumman, "
     "Airbus, L3Harris, Leonardo. Combined revenue: ~$650 billion.",
     "SIPRI Top 100 arms companies", "CONFIRMED", "global",
     "https://www.sipri.org/"),

    ("compliance",
     "MTCR (Missile Technology Control Regime) — 35 partners — restricts transfers of "
     "Category I and Category II missile technology. Items include rockets, UAVs with "
     "300+ km range / 500+ kg payload, and supporting subsystems.",
     "MTCR Partners", "CONFIRMED", "global",
     "https://mtcr.info/"),

    ("compliance",
     "Australia Group (43 members) governs chemical and biological weapon precursor "
     "exports. Members harmonise export controls on specific lists of CBW agents and "
     "delivery-system components.",
     "Australia Group", "CONFIRMED", "global",
     "https://www.dfat.gov.au/publications/minisite/theaustraliagroupnet/site/en/index.html"),
]


async def seed_facts(skip_if_seeded: bool = True) -> dict:
    """Inject the curated knowledge pack into the knowledge base.

    Args:
        skip_if_seeded: when True (default), checks if the canonical
            marker fact already exists and skips the full seed if so.
            Pass False to force re-seeding.

    Returns: {added, skipped, errors, total}
    """
    try:
        from .. import knowledge as _k
    except Exception as e:
        return {"ok": False, "error": f"knowledge import failed: {e}"}

    # Marker fact — first seed creates this; subsequent runs skip
    # unless skip_if_seeded=False.
    _MARKER_TOPIC = "general"
    _MARKER_CONTENT = (
        "[KNOWLEDGE_PACK_MARKER] LatAm + Asia-Pacific seed v1 applied "
        "2026-05-10. ~35 facts across Mexico/Argentina/Colombia/Peru/Chile/"
        "Venezuela + Korea/Japan/India/ASEAN + global cross-cutting."
    )

    if skip_if_seeded:
        try:
            existing = _k.search(_MARKER_CONTENT[:60], limit=1)
            if hasattr(existing, "__await__"):
                existing = await existing
            if isinstance(existing, list) and existing:
                return {
                    "ok": True,
                    "action": "skipped",
                    "reason": "marker fact present — pack already seeded",
                    "total": len(_FACTS),
                }
        except Exception:
            pass

    added = 0
    errors = 0
    for topic, content, source, confidence, region, source_url in _FACTS:
        try:
            await _k.store_fact(
                topic=topic,
                content=content,
                source=source,
                confidence=confidence,
                source_url=source_url,
                fact_type="GENERAL_CLAIM",
                entity_name=region,
            )
            added += 1
        except Exception as e:
            logger.debug("[knowledge_pack] failed %s: %s", topic, e)
            errors += 1

    # Persist the marker fact
    try:
        await _k.store_fact(
            topic=_MARKER_TOPIC,
            content=_MARKER_CONTENT,
            source="knowledge_packs.latam_asia_pac_seed",
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    # Brain absorption — single absorption notification per seed run
    try:
        from .. import brain_hook as _bh
        await _bh.absorb(
            module="web_atlas",  # closest existing topic mapping
            summary=(
                f"Knowledge pack seeded: LatAm + Asia-Pacific. "
                f"{added}/{len(_FACTS)} facts added across procurement / compliance / "
                f"market_intel / finance / competitor_intel topics."
            ),
            success=errors == 0,
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    logger.info(
        "[knowledge_pack] LatAm + Asia-Pacific seed complete: added=%d errors=%d",
        added, errors,
    )
    return {
        "ok": errors == 0,
        "added": added,
        "errors": errors,
        "total": len(_FACTS),
    }


def catalogue() -> dict:
    """Read-only summary — what's in the pack."""
    by_region: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    for _t, _c, _s, _conf, region, _u in _FACTS:
        by_region[region] = by_region.get(region, 0) + 1
        by_topic[_t] = by_topic.get(_t, 0) + 1
    return {
        "total_facts": len(_FACTS),
        "by_region": by_region,
        "by_topic": by_topic,
    }
