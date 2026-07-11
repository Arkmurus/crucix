"""ARIA North Africa Knowledge Module.

Tier 2 positioning region per 2026-04-17 heat-map expansion. Covers
Morocco, Egypt, Algeria, Tunisia, Libya. Portuguese language is
incidentally relevant in Morocco as a secondary language, so the
Lusophone moat has partial application.

IMPORTANT BRIGHT-LINE — Algeria is Russia's primary African arms
client. Any intermediary active in BOTH Morocco and Algeria faces a
proliferation / dual-exposure flag. See regional_bright_lines.py.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging

logger = logging.getLogger("aria.knowledge_north_africa")


NORTH_AFRICA_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — NORTH AFRICA DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

KEY COUNTRIES + PROCURMENT POSTURE

Morocco:
  - Ministry of Defence (Administration de la Défense Nationale)
  - FAR (Forces Armées Royales) modernisation programme:
    * F-1 fleet replacement (ongoing)
    * Naval modernisation (FREMM-class frigates French + indigenous)
    * Growing domestic integration capability (HAMV, Maroc Aéro)
  - Bilateral: historically French + US; increasingly UK, Türkiye,
    Israel (post-Abraham Accords 2020)
  - Compliance: NATO-adjacent "Major Non-NATO Ally" status with US
  - Portuguese-language use: secondary — some Sahrawi / Atlantic
    documents surface in PT; CPLP moat has partial transfer

Egypt:
  - Ministry of Defence + Arab Organisation for Industrialisation (AOI)
  - Largest defence budget in Africa (~USD 4.6bn+, under-reported)
  - Triple-supplier pattern: French (Rafale, FREMM), US FMS (M1A2,
    F-16), Russian (Su-35 contested, Ka-52), growing Chinese (Wing Loong,
    BeiDou integration)
  - Arkmurus angle: sub-programme diversification — advisory on European
    supply alternatives in areas where Russian + US routes are blocked
    or politically constrained
  - EDEX biennial Cairo show is primary access point

Algeria:
  - Ministry of National Defence (MDN)
  - Russia's largest African arms client (~$7bn stockpile value; T-90,
    S-400, Su-30, MiG-29, Pantsir)
  - CAATSA §231 secondary-sanctions pressure applies to third-country
    brokers facilitating significant Russian defence transactions
  - Growing Chinese and Turkish supplementary supplies (CH-5 drones,
    TB2 reported interest)
  - Bright-line: Algeria exposure requires enhanced compliance review
    per regional_bright_lines.ALGERIA_DUAL_EXPOSURE_RULE

Tunisia:
  - Ministry of National Defence
  - US FMS-dominated + modest European procurement
  - Growing Turkish relationship (TB2, ASELSAN COMINT)
  - Limited domestic defence-industrial base

Libya:
  - Fragmented governance — two parallel MoDs (GNU Tripoli, HoR Tobruk)
  - UN arms embargo: Resolution 1970 (2011) still in force
  - ANY defence engagement requires UN Panel of Experts clearance
  - Bright-line: hard-stop on arms transfer; advisory-only services
    possible with enhanced DD

COMPLIANCE OVERLAY — DUAL-EXPOSURE RULE

Any counterparty or intermediary who is active in BOTH Morocco and
Algeria simultaneously triggers a proliferation-risk flag:
  1. Algeria's Russian-defence dependency + CAATSA §231 exposure
  2. Morocco's NATO-adjacent + US MNNA status
  3. Re-export / re-transfer pathway risk — an item routed via one
     becomes a dual-use liability in the other
Always escalate to enhanced DD before accepting a dual-exposure mandate.

KEY MEDIA / OPEN SOURCES

  - Jeune Afrique (French-language Africa reporting)
  - Middle East Eye (North Africa desk)
  - Asharq Al-Awsat (Arabic, pan-Arab)
  - MAP Maroc (Moroccan state agency)
  - Ahram Online (Egyptian state)
  - TSA Algérie (private, generally critical of MDN)
  - Libya Observer (English + Arabic, post-2011 politics)
"""


NORTH_AFRICA_ARKMURUS_ANGLE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — NORTH AFRICA / ARKMURUS POSITIONING
═══════════════════════════════════════════════════════════════════════

Primary anchor: Morocco
  Morocco is the NATO-adjacent African partner with the most active
  European defence procurement programme. French primacy is yielding
  share to UK, Türkiye, Israel. European advisory anchored in UK/EU
  compliance has a credible entry point.

Secondary anchor: Egypt sub-programmes
  Egypt's scale rules out advisory on prime-vendor selection (those
  are G-to-G / FMS). The advisory opportunity is in sub-programmes
  where Egyptian MoD is diversifying. UK-anchored facilitators who can
  thread French primes (Dassault, Naval Group) and US FMS constraints
  have a structural edge.

Avoid until cleared:
  - Algeria: CAATSA §231 exposure is a hard compliance gate
  - Libya: UN embargo — advisory-only, hard-stop on transfer

Cross-regional play:
  - Morocco ↔ Sub-Saharan Lusophone (Portuguese partial + Atlantic
    maritime cooperation)
  - Egypt ↔ Gulf sovereign-wealth (PIF, ADIA co-investment on port /
    maritime infrastructure adjacent to defence)
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "north_africa_landscape",
        "text": NORTH_AFRICA_DEFENCE_LANDSCAPE,
        "keywords": [
            "north africa", "maghreb", "sahara",
            "morocco", "rabat", "casablanca", "far",
            "egypt", "cairo", "edex", "aoi", "rafale egypt",
            "algeria", "algiers", "mdn",
            "tunisia", "tunis",
            "libya", "tripoli", "tobruk", "un embargo libya",
        ],
    },
    {
        "id": "north_africa_arkmurus",
        "text": NORTH_AFRICA_ARKMURUS_ANGLE,
        "keywords": [
            "morocco arkmurus", "morocco advisory",
            "egypt sub-programme", "egypt french", "egypt fms",
            "algeria caatsa", "algeria exposure",
            "libya embargo",
            "north africa arkmurus", "maghreb advisory",
        ],
    },
]


def get_north_africa_context(query: str, max_blocks: int = 2) -> str:
    """Return North Africa knowledge blocks matching a query."""
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
        module="knowledge_north_africa",
        summary="Get North Africa Context",
        source_id="knowledge_north_africa:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
