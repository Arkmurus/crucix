"""ARIA Gulf / Middle East Knowledge Module.

Covers UAE, Saudi Arabia, Qatar, Kuwait, Bahrain, Oman — the Tier 1
expansion target from the 2026-04-17 heat map review. Same pattern as
market_competitor_knowledge.get_market_context(): text blocks + a keyword
search index, surfaced into chat via aria_engine._sync_correlation_context
(already wires multiple *_context helpers).

Public sources only. Facts that move frequently (budget figures, named
officials) are deliberately omitted — ARIA cites procurement_calendar
and live tender_monitor/opensanctions for those.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging

logger = logging.getLogger("aria.knowledge_gulf")


GULF_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — GULF / MIDDLE EAST DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

KEY GOVERNMENT STAKEHOLDERS

United Arab Emirates (UAE):
  - Ministry of Defence — primary procurement authority
  - Tawazun Economic Council — defence-industrial offset + localisation
  - EDGE Group (2019-) — state-owned OEM conglomerate, ~25 subsidiaries
    spanning UAVs (Adasi), munitions (Caracal, Al Tariq), cyber (Beacon
    Red), missiles (Halcon), EW, naval. Fastest-growing Gulf OEM.
  - IDEX (biennial, February) — primary regional defence exhibition

Saudi Arabia (KSA):
  - Ministry of Defence (MoD)
  - General Authority for Military Industries (GAMI) — regulator
  - Saudi Arabian Military Industries (SAMI, 2017-) — Vision 2030
    localisation champion; 50% domestic defence content by 2030 target
  - World Defense Show (biennial, Riyadh, February) — launched 2022
  - Vision 2030 defence localisation explicit economic-diversification
    mandate with offset, joint-venture, and technology-transfer priorities

Qatar:
  - Ministry of Defence; Barzan Holdings (state defence investment arm)
  - DIMDEX (biennial maritime, Doha)
  - Growing shipbuilding + naval-integration programme

Kuwait:
  - Kuwait Ministry of Defence — long-standing US FMS buyer
  - Gulf Defense + Aerospace Exhibition (biennial)

Bahrain:
  - Ministry of Defence; BIDEC (biennial defence expo, Manama)
  - Home of US Navy 5th Fleet — high US-alignment

Oman:
  - Royal Army of Oman / Royal Air Force of Oman / Royal Navy of Oman
  - Balance-of-suppliers doctrine — UK, France, US, Turkey

COMMON COMMERCIAL PATTERNS

1. Offset requirements: most Gulf purchases above threshold values
   trigger defence offset obligations managed by Tawazun (UAE), GAMI
   (KSA), or equivalents. Offset multiples range 30-50% of contract value.
2. End-use certificates: Gulf buyers are end-users for European ITAR /
   UK ECJU / US EAR licensed equipment. Re-export restrictions vary
   sharply — verify country-of-final-use on every mandate.
3. Indirect procurement via intermediaries: advisors / resellers /
   integrators common, especially for European equipment where direct
   OEM presence is limited. Arkmurus profile fits naturally here.
4. Sovereign wealth co-investment: ADIA (UAE), PIF (KSA), QIA (Qatar)
   occasionally co-invest in regional defence-industrial joint ventures
   — specifically relevant for African MoDs seeking Gulf capital for
   their own defence-infrastructure build-out (Angola, Mozambique).

COMPLIANCE OVERLAY — UAE-ADJACENT PROLIFERATION RISK

UAE is positioned as a trade hub — its free zones (Jebel Ali, DAFZA,
DMCC) see frequent front-company activity in sanctions evasion cases.
Relevant sanctions lists to screen against before engaging UAE entities:
  - OFAC SDN — persistent UAE-based entity designations (Iran proxies,
    Houthi network finance, Russia sanctions evasion)
  - UK OFSI — mirroring OFAC with UK-specific additions
  - EU consolidated list
  - UN Sanctions Committee — Yemen / Iran / Libya / DPRK panels
Risk pattern: look for "UAE-registered front / shell" flags in mandate
diligence — especially entities with recent incorporation, thin staff,
and mismatches between registered address and operational footprint.

SANCTIONS / EMBARGO BRIGHT LINES (partial)

- Iran: comprehensive US + EU restrictions; UAE free-zone routing risk
- Yemen: UN arms embargo, especially Houthi-controlled territory
- Russia: EU + UK + US sanctions regime affecting dual-use + defence
- DPRK: UN comprehensive; trans-shipment monitoring
- Cuba / Syria: US primary sanctions

KEY MEDIA / OPEN SOURCES TO MONITOR

  - Defense News Middle East / Breaking Defense Middle East (English)
  - Al-Monitor Gulf Pulse (analysis)
  - Saudi Gazette / Arab News — state-aligned, useful for announcements
  - WAM (UAE state news agency)
  - Gulf News
  - MENA-focused SIPRI + IISS Military Balance entries
"""


GULF_ARKMURUS_ANGLE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — GULF / ARKMURUS CONVERGENCE PLAY
═══════════════════════════════════════════════════════════════════════

Arkmurus thesis (per 2026-04-17 strategic memory):
  Vision 2030 localisation + UAE EDGE growth + active Gulf seeking of
  European / Turkish intermediaries = exact fit for Arkmurus profile.

  Convergence angle: Angola + Mozambique are both seeking Gulf sovereign
  wealth co-investment on defence-adjacent infrastructure (naval, air,
  training). Arkmurus can bridge both sides IF ARIA has matching Gulf
  intelligence + matching Lusophone intelligence (which she does).

Questions Arkmurus should be able to answer with ARIA support:
  1. Which SAMI / EDGE subsidiary matches a given African procurement
     requirement? (requires SAMI/EDGE OEM structure knowledge — seeded
     here and in oem_contact_graph / competitor_tracker)
  2. What's the EUC pathway for UAE-origin or Turkish-origin equipment
     re-exported into Angola, Mozambique, Guinea-Bissau?
  3. Are any UAE-registered entities in a given mandate flagged on
     OFAC / OFSI / UN lists? (sanctions.fuzzy_screen + enrich_with_relationships)
  4. What's the next KSA or UAE budget milestone that could open a
     procurement window? (procurement_calendar — seeded)

Compliance rails:
  - Bright-line rule: if a mandate involves simultaneous UAE + Iran-adjacent
    free-zone parties, escalate to enhanced DD before accepting.
  - Algeria + Morocco dual-exposure rule ALSO applies to counterparties
    active in Gulf that also touch Algeria (Russia's largest MENA client).
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "gulf_landscape",
        "text": GULF_DEFENCE_LANDSCAPE,
        "keywords": [
            "gulf", "middle east", "mena", "gcc",
            "uae", "emirates", "abu dhabi", "dubai",
            "saudi arabia", "ksa", "riyadh", "vision 2030",
            "qatar", "doha",
            "kuwait", "bahrain", "oman",
            "edge group", "sami", "tawazun", "gami",
            "idex", "world defense show", "dimdex", "bidec",
        ],
    },
    {
        "id": "gulf_arkmurus_angle",
        "text": GULF_ARKMURUS_ANGLE,
        "keywords": [
            "gulf convergence", "lusophone gulf", "angola gulf",
            "sovereign wealth", "adia", "pif", "qia",
            "offset gulf", "euc gulf", "re-export gulf",
            "sami structure", "edge structure",
        ],
    },
]


def get_gulf_context(query: str, max_blocks: int = 2) -> str:
    """Return relevant Gulf knowledge for a query. Keyword-matched, capped."""
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
        module="knowledge_gulf",
        summary="Get Gulf Context",
        source_id="knowledge_gulf:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    """Block IDs — used by the dashboard to verify module coverage."""
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2119 §21a — wire failure handler for knowledge_gulf
try:
    wire_failure(module="knowledge_gulf", detail="module shutdown",
                gap_type="engine_failure", source="knowledge_gulf:shutdown")
except Exception:
    pass
