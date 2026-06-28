"""ARIA South + Southeast Asia Knowledge Module.

Tier 2 positioning region per 2026-04-17 heat-map expansion. Covers
India (primary anchor), Indonesia, Vietnam, Philippines, Malaysia,
Bangladesh. 18-36 month positioning — relationship + corpus build now
so ARIA has the knowledge when BD conversations begin.

India's Make-in-India / DAP 2020 localisation mandate is creating a
massive intermediary market for European OEMs seeking Indian JV
partners. ASEAN nations are actively diversifying away from Russian
and Chinese suppliers — structural opportunity for UK-anchored
advisory post-AUKUS signalling.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_south_se_asia")


SOUTH_SE_ASIA_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — SOUTH + SOUTHEAST ASIA DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

SOUTH ASIA

India:
  - Ministry of Defence (MoD) + Defence Acquisition Council (DAC)
  - DAP 2020 (Defence Acquisition Procedure): Make-in-India mandate
    targets 75% indigenous content; Strategic Partnership model routes
    foreign OEM into Indian JV structure
  - OEM ecosystem: HAL (aerospace), BEL (electronics), BDL (missiles),
    MDL (naval), GSL (naval), Ordnance Factory Board (now corporatised
    into AWEIL/MIL and others), Tata Advanced Systems, L&T, Bharat Forge
  - Major programmes: AMCA 5th-gen fighter, Tejas Mk-2, P-75I submarines,
    Project-17A frigates, TEDBF naval fighter, Pralay + BrahMos missiles,
    MRFA fighter (180+ units, formerly MMRCA)
  - Offset: DAP 2020 offset banking; threshold 2000 crore (~$240m)
    triggers 30% offset obligation routed via IOPs (Indian Offset
    Partners); Arkmurus profile fits IOP-advisory
  - DRDO is R&D arm — partnership route for emerging tech
  - DefExpo + Aero India are the biennial access points

Pakistan:
  - Ministry of Defence + Strategic Plans Division (nuclear); NESCOM,
    KRL (strategic); POF, HIT, PAC (conventional)
  - Chinese-dominated supply (J-10CE, JF-17, HQ-9 variants)
  - UK-aligned advisory NOT typically viable under current sanctions /
    export-control posture. Advisory should steer clear unless Pakistan
    mandate is explicitly cleared by UK ECJU

Bangladesh:
  - Forces Goal 2030 — tri-service modernisation
  - Triple-supplier: China (majority), Türkiye (TB2, MKE munitions),
    Russian legacy + UK patrol vessels
  - Growing budget but compliance constraints re: Chinese primary route

Sri Lanka:
  - Post-conflict modest procurement; compliance constraints re:
    ongoing UN human-rights scrutiny

SOUTHEAST ASIA

Indonesia:
  - Ministry of Defence + TNI (Tentara Nasional Indonesia)
  - Strategic Arms Procurement Plan (Renstra) 2020-2024 → 2025-2029
  - Major moves 2024-2025: Rafale (42 units), F-15EX (MoU), Boeing
    P-8A (MoU), ANOA-2 IFV local production, PT PAL frigate build
  - PT PINDAD (land), PT PAL (naval), PT Dirgantara (aerospace) are the
    state primes — offset / JV partner ecosystem
  - Indo Defence biennial Jakarta show

Vietnam:
  - Ministry of National Defence (MoND)
  - Actively diversifying from Soviet/Russian legacy (T-90S, Su-30, Kilo
    submarines) toward Indian BrahMos, Israeli ELM-2288, Korean K-9
  - UK + European opening: post-CPTPP, post-free-trade arrangements;
    naval modernisation 2025-2030 window
  - Compliance constraint: Russian legacy means CAATSA §231 considerations

Philippines:
  - Department of National Defense (DND)
  - Horizon 3 modernisation (2023-2028) — post-AUKUS pivot, explicitly
    seeking non-Chinese suppliers; US MDT alliance primary
  - Major: BrahMos (first export customer), ATMOS howitzers, KAI FA-50,
    Mitsubishi naval, Airbus C-295
  - ADAS biennial Manila show

Malaysia:
  - Ministry of Defence + Defence White Paper 2020
  - Littoral Mission Ship (LMS) + MPA programme; Korean/Turkish presence
  - FPDA alignment — UK / Australia / NZ / Singapore / Malaysia
    preserves credible UK-anchored advisory lane

Singapore:
  - MINDEF / SAF; Singapore Airshow + IMDEX biennial
  - Technologically the region's most advanced. STE (formerly ST
    Engineering) is the prime — both buyer (via MINDEF) and exporter.
  - High compliance standard — always suitable for UK/EU vendors

Thailand:
  - Ministry of Defence; post-2014 coup compliance considerations softened
  - Growing Korean (K2, K9), Turkish (Kaplan-20), Chinese (VT-4) share

KEY MEDIA / OPEN SOURCES

  - Jane's Defence Weekly Asia desk (IISS)
  - SIPRI India + ASEAN transfer records
  - Janes (paid) — authoritative programme tracker
  - Defense News Asia Pacific
  - Army Recognition (programme announcements)
  - Regional specialists: Livefist (India), The Diplomat (ASEAN)
"""


SOUTH_SE_ASIA_ARKMURUS_ANGLE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — SOUTH + SE ASIA / ARKMURUS POSITIONING
═══════════════════════════════════════════════════════════════════════

Primary anchor: India IOP / offset advisory
  Arkmurus's European OEM relationships (Leonardo, Rheinmetall, KNDS,
  Thales, Saab, Airbus DS) are directly relevant to foreign-OEM
  Indian-JV routing under DAP 2020. Offset-banking advisory is a natural
  fit: fees come from OEM side, scope is procedural rather than
  programme-deciding.

Secondary anchor: Vietnam / Philippines / Indonesia naval + UAS
  ASEAN fragmentation from Russian + Chinese suppliers is creating
  specific windows:
    - Indonesia: European maritime patrol / frigate packages
    - Vietnam: Indian + Korean + Israeli; UK advisory on dual-use
    - Philippines: US MDT-aligned but wants European diversification
  UK-anchored advisory has structural post-AUKUS advantage.

Compliance rails:
  - Pakistan: avoid unless ECJU clears; Chinese-heavy sensitive
  - Vietnam: CAATSA §231 consideration on Russian-legacy transactions
  - Myanmar: comprehensive EU / UK / US sanctions — hard-stop
  - DPRK / North Korea: UN 1718 — hard-stop

Language + corpus transfer:
  - English is working language in India / Philippines / Malaysia / SG
  - Limited Portuguese transfer (GoA / Macao / Timor-Leste edge only)
  - Bahasa Indonesia / Malay require dedicated corpus additions
  - Hindi / Tamil / Vietnamese: rely on English-language press
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "south_se_asia_landscape",
        "text": SOUTH_SE_ASIA_DEFENCE_LANDSCAPE,
        "keywords": [
            "south asia", "southeast asia", "asean",
            "india", "hal", "drdo", "dap 2020", "make in india",
            "offset india", "iop", "mrfa", "amca", "tejas",
            "pakistan", "bangladesh", "forces goal 2030",
            "indonesia", "tni", "pt pindad", "pt pal", "renstra",
            "vietnam", "mond",
            "philippines", "dnd", "horizon 3",
            "malaysia", "fpda",
            "singapore", "ste engineering", "mindef",
            "thailand",
        ],
    },
    {
        "id": "south_se_asia_arkmurus",
        "text": SOUTH_SE_ASIA_ARKMURUS_ANGLE,
        "keywords": [
            "india iop", "india offset advisory", "india arkmurus",
            "indonesia advisory", "vietnam advisory", "philippines advisory",
            "asean arkmurus", "aukus advisory",
        ],
    },
]


def get_south_se_asia_context(query: str, max_blocks: int = 2) -> str:
    if not query or not query.strip():
        return ""
    q = query.lower().strip()
    tokens = set(q.split())
    scored: list[tuple[int, dict]] = []
    for entry in _SEARCH_INDEX:
        score = 0
        for kw in entry["keywords"]:
            if kw in q:
                score += 2
            elif any(t in kw for t in tokens):
                score += 1
        if score:
            scored.append((score, entry))
    if not scored:
        return ""
    scored.sort(key=lambda x: -x[0])
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="knowledge_south_se_asia",
        summary="Get South Se Asia Context",
        source_id="knowledge_south_se_asia:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
