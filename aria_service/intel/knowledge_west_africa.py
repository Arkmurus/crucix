"""ARIA West Africa Knowledge Module.

Nigeria, Ghana, Senegal, Côte d'Ivoire, Cameroon + Sahel AES Alliance
(Niger, Mali, Burkina Faso). Tier 1 expansion target — per 2026-04-17
heat map review, a procurement vacuum is opening simultaneously in two
directions: AES states replacing Western suppliers with Russian/Turkish/
Chinese; Nigeria + Ghana stable buyers seeking Western alternatives.

Stability buyers vs AES bloc compliance handling diverges sharply.
Bright-line rules below MUST be checked before any mandate.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_west_africa")


WEST_AFRICA_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — WEST AFRICA DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

STABILITY BUYERS (Western-aligned, open procurement)

Nigeria — largest West African defence buyer:
  - Nigerian Army, Navy, Air Force (NAF)
  - Ministry of Defence, Abuja
  - DICON (Defence Industries Corporation of Nigeria) — small-arms + munitions
  - Key procurement programmes: counter-insurgency (Boko Haram, ISWAP),
    Niger Delta security, maritime domain awareness
  - Active with Turkish (Baykar TB2, JF-17 via Pakistan/China), US,
    UK suppliers; diversifying
  - Budget: Appropriation Act annual, signed ~January

Ghana — stable democracy, active procurement:
  - Ghana Armed Forces (GAF)
  - Ministry of Defence + Ministry of National Security
  - Growing maritime-security focus (Gulf of Guinea piracy), UN
    peacekeeping contributor
  - Recent history: Damen naval vessels; Airbus H225M helicopters;
    European supplier relationships open

Senegal:
  - Ministère des Forces Armées
  - Professionalised army; growing counter-terror mission with partners
  - Francophone procurement environment; French, Turkish, Brazilian
    suppliers active

Côte d'Ivoire, Cameroon, Togo, Benin:
  - Francophone-aligned; French industrial presence persistent
  - Smaller programmes but steady

AES ALLIANCE (Alliance of Sahel States, 2023-)

Members: Mali, Burkina Faso, Niger
Status: withdrew from ECOWAS (announced 2024), declared confederation
Suppliers of choice: Russia (Wagner → Africa Corps), Turkey (Baykar),
  China; also Iran in some drone programmes
Western presence: sharply reduced post-coups — France, US forces withdrawn

COMMERCIAL + COMPLIANCE BRIGHT LINES

ECOWAS sanctions exposure:
  - ECOWAS imposed then partially lifted sanctions on AES members over
    2023-2024. Residual member-specific restrictions persist.
  - Any advisor mandate involving AES counterparty requires
    pre-engagement verification of current ECOWAS posture.

UN + regional embargoes to screen against:
  - UNSC Resolution 2127 / 2262 / 2288 family (regional, historical)
  - Specific past/present applicability: Libya arms embargo (partial),
    Côte d'Ivoire (lifted 2016 but watch entity-level residuals), CAR

Russian intermediary flagging:
  - Entities with Russian parent / Russian finance / Russia-adjacent
    beneficial ownership appearing in AES mandates = flag for enhanced
    DD (Wagner/Africa Corps successor-entity networks). OpenSanctions
    and the sanctions module are primary screens here.

Dual-use / counter-terror end-use:
  - Equipment suitable for counter-insurgency use requires scrutiny
    of the receiving unit, not just the state. End-user certificates
    matter more than country-level risk ratings for these programmes.

WHY NIGERIA MASTERY MATTERS (2026-04-17 dashboard datum)

Nigeria contacts mastery flagged at 62% — lowest on the ARIA heat map.
The commercial implication is concrete: the largest West African defence
budget has the lowest ARIA coverage. Building Nigeria MoD procurement
calendar + contact graph + Gulf of Guinea maritime-security knowledge
is a direct revenue-enabler.

KEY SOURCES

  - Nigerian Defence News (English)
  - Defence Web (South Africa, wide continental coverage)
  - Janes Africa
  - Regional-office OSINT via Africa Center for Strategic Studies (ACSS)
  - ECOWAS Commission press releases
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "west_africa_landscape",
        "text": WEST_AFRICA_LANDSCAPE,
        "keywords": [
            "west africa", "ecowas",
            "nigeria", "nigerian", "abuja", "dicon", "naf",
            "ghana", "ghanaian", "accra", "gaf",
            "senegal", "senegalese",
            "ivory coast", "cote d'ivoire", "côte d'ivoire",
            "cameroon", "togo", "benin",
            "sahel", "aes alliance", "alliance sahel",
            "mali", "burkina faso", "niger",
            "boko haram", "iswap", "gulf of guinea",
            "africa corps", "wagner",
        ],
    },
]


def get_west_africa_context(query: str, max_blocks: int = 1) -> str:
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
    from .engine_wiring import wire_success
    wire_success(
        module="knowledge_west_africa",
        summary="Get West Africa Context",
        source_id="knowledge_west_africa:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
