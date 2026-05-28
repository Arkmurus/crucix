"""ARIA Central / East Africa Knowledge Module (non-Lusophone).

Tier 2 region per 2026-04-17 heat-map expansion. Covers DRC, Rwanda,
Kenya, Tanzania, Uganda, Burundi — the Great Lakes / EAC / partial
SADC overlap. Explicitly distinguishes stable buyers (Kenya, Rwanda)
from conflict-zone markets (DRC) where ARIA requires a bright-line
rule before any counterparty engagement.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_central_africa")


CENTRAL_AFRICA_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — CENTRAL / GREAT LAKES DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

KEY COUNTRIES

DRC (Democratic Republic of the Congo):
  - Ministry of Defence + FARDC (Forces Armées de la RDC)
  - Active M23 conflict in the east (North + South Kivu)
  - Parallel procurement by multiple parties:
    1. Congolese government (French, Israeli, Chinese supply)
    2. Regional peacekeeping — SADC SAMIDRC mission, EAC Regional Force
       (withdrawn 2023), MONUSCO (UN, drawdown ongoing)
    3. Irregular / parallel supply chains — flagged by UN Group of Experts
  - Bright-line: see regional_bright_lines.DRC_COUNTERPARTY_RULE — any
    DRC mandate requires UN GoE awareness + armed-group source-of-funds
    check. HIGH opportunity, HIGH compliance risk.

Rwanda:
  - Ministry of Defence + RDF (Rwanda Defence Force)
  - Sophisticated C4ISR posture relative to size; growing drone capability
  - Airbus + Turkish (Bayraktar) + Israeli (Aeronautics) supply
  - Compliance note: ongoing UN / US / EU scrutiny re: alleged M23 support;
    sanctions designations on specific individuals (not state-level)
  - Stable buyer — suitable for European advisory with enhanced DD

Kenya:
  - Ministry of Defence + KDF (Kenya Defence Forces)
  - UK defence relationship (BATUK training estate) + growing Turkish
    (TB2 reported), US CENTCOM cooperation on Somalia/Al-Shabaab
  - Most sophisticated buyer in East Africa; active modernisation
    (naval, air transport, ATGM / mortar upgrades)
  - Natural entry point for UK-anchored advisory into Central/East Africa

Tanzania:
  - Ministry of Defence + TPDF
  - Chinese-dominated since Nyerere era; growing Turkish presence
  - Stable but modest procurement; UK relationship survives SADC dynamic

Uganda:
  - Ministry of Defence + UPDF
  - Involved in regional operations (Somalia ATMIS, eastern DRC)
  - Growing Israeli + Turkish + Chinese supply; US CTU funding thread
  - Compliance note: historic UN GoE Congo findings re: minerals routing

Burundi:
  - Ministry of National Defence; modest procurement, Chinese + Russian
    legacy
  - EAC + SAMIDRC participation creates crossover exposure
  - Not a primary target; flagged for compliance if encountered

REGIONAL MULTILATERAL OVERLAYS

EAC (East African Community): 8 member states; defence integration
thin, economic integration deeper. EAC Regional Force deployed 2022
withdrew from DRC 2023 following political friction.

ICGLR (International Conference on the Great Lakes Region): 12-state
body focused on DRC stabilisation; produces joint mineral-certification
scheme relevant to conflict-minerals compliance.

SADC (Southern African Development Community): 16 member states; SAMIDRC
mission active in eastern DRC 2023–ongoing. Cross-overlap with East/
Central African buyers via DRC + Tanzania.

COMPLIANCE OVERLAY — CONFLICT-ZONE CONSTRAINTS

- UN Security Council resolutions on DRC — 2688 (2023) renewed arms
  embargo exemptions require pre-notification; armed-group sanctions list
- Conflict-minerals / Dodd-Frank s.1502 compliance for any US-supply
  chain product touching Great Lakes region
- Rwanda: individual US + EU designations related to alleged M23 support
- UN Group of Experts on DRC — annual report; monitor for supply-chain
  mentions of any counterparty in scope

KEY MEDIA / OPEN SOURCES

  - UN Group of Experts on DRC annual reports
  - ISS Africa (Institute for Security Studies, Pretoria)
  - Africa Center for Strategic Studies (US NDU)
  - Actualité CD (DRC, French)
  - The EastAfrican (Nation Media Group, pan-EAC)
  - KFM Kenya / Daily Nation (Kenya)
"""


CENTRAL_AFRICA_ARKMURUS_ANGLE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — CENTRAL / EAST AFRICA / ARKMURUS POSITIONING
═══════════════════════════════════════════════════════════════════════

Primary anchor: Kenya
  Stable, UK-anchored, sophisticated. BATUK estate + KDF modernisation
  programmes. Natural entry point for Central / East Africa advisory.

Secondary anchor: Rwanda
  Small but technically advanced; European (Airbus) + Turkish + Israeli
  relationships. Enhanced-DD entry viable.

Approach-with-bright-line: DRC
  HIGH opportunity (multi-party procurement + post-conflict reconstruction)
  coupled with HIGH compliance risk. Pre-mandate rule:
    (a) Confirm counterparty is not on UN DRC sanctions list
    (b) Confirm no minerals-funded armed-group source-of-funds
    (c) If peacekeeping-mission supply, confirm UN Panel notification
  Without all three, decline or escalate.

Tanzania + Uganda:
  Monitor only — not primary targets. Enhanced DD on encounter.

Burundi:
  Compliance-first; treat as enhanced-DD even on advisory-only mandates.
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "central_africa_landscape",
        "text": CENTRAL_AFRICA_DEFENCE_LANDSCAPE,
        "keywords": [
            "central africa", "great lakes",
            "drc", "congo", "fardc", "kinshasa", "m23", "monusco", "samidrc",
            "rwanda", "kigali", "rdf",
            "kenya", "kdf", "batuk", "nairobi",
            "tanzania", "tpdf", "dar es salaam",
            "uganda", "updf", "kampala",
            "burundi", "bujumbura",
            "eac", "icglr", "sadc",
        ],
    },
    {
        "id": "central_africa_arkmurus",
        "text": CENTRAL_AFRICA_ARKMURUS_ANGLE,
        "keywords": [
            "kenya arkmurus", "rwanda arkmurus",
            "drc arkmurus", "drc bright line",
            "central africa advisory", "east africa advisory",
        ],
    },
]


def get_central_africa_context(query: str, max_blocks: int = 2) -> str:
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
        module="knowledge_central_africa",
        summary="Get Central Africa Context",
        source_id="knowledge_central_africa:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
