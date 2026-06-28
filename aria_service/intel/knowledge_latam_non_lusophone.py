"""ARIA Latin America (non-Lusophone) Knowledge Module.

Peru, Colombia, Chile, Ecuador, Mexico, Argentina. Tier 1 expansion
target. Peru is already active via the SEACE procurement assessment
(ARK-INTEL-PERU-20260412) — natural starting anchor.

Spanish-language procurement environment has partial transfer from the
Lusophone corpus (similar civil-law structures, similar compliance
vocabulary). Lower build cost than starting from zero.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_latam_non_lusophone")


LATAM_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — LATIN AMERICA (NON-LUSOPHONE) DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

PERU (active market — ARIA anchor)

Core institutions:
  - Ministerio de Defensa; Fuerzas Armadas del Perú (Ejército, Marina, FAP)
  - SEACE (Sistema Electrónico de Contrataciones del Estado) — public
    procurement portal; tender_monitor already integrates this
  - CCFFAA (Comando Conjunto de las Fuerzas Armadas)
Context: Peru has an active DD history in ARIA — SEACE assessment
  ARK-INTEL-PERU-20260412 already in the DD library. Use it as prior.
Key recent programmes: F-16/Gripen decision (ongoing), patrol boats,
  jungle-aviation upgrades, counter-narcotics modernisation.

COLOMBIA

Core institutions:
  - Ministerio de Defensa Nacional
  - COTECMAR — state shipyard (naval platform production)
  - INDUMIL — Industria Militar (munitions, small arms)
  - Fuerzas Militares (Ejército, Armada, FAC)
Context: US-aligned; programme mix is counter-narco aviation, naval
  patrol, continued transition from demobilisation-era focus. European
  suppliers active where US options constrained by ITAR re-transfer.

CHILE

Core institutions:
  - Ministerio de Defensa Nacional
  - ENAER (aerospace), ASMAR (naval), FAMAE (land/munitions)
  - DGMN (Dirección General de Movilización Nacional) — oversight
Context: F-16 fleet modernisation, frigate / OPV programmes, UK + EU
  ties; strong institutional buyer reputation.

ECUADOR

Core institutions:
  - Ministerio de Defensa Nacional; FFAA
  - DINE (Dirección de Industrias del Ejército)
Context: Rising counter-narco + maritime-security priorities (Galápagos
  EEZ enforcement, Pacific drug routes); recent US security partnership
  deepening.

MEXICO

Core institutions:
  - SEDENA (Secretaría de la Defensa Nacional) — army + air force
  - SEMAR (Secretaría de Marina) — navy
  - Large budget, US-centric supplier relationships, strong growth
    in UAV + border-security procurement
Context: US ITAR environment dominates; European suppliers compete on
  maritime + ISR where US options limited.

ARGENTINA

Core institutions:
  - Ministerio de Defensa; FAA / Armada / Ejército
  - FAdeA (Fábrica Argentina de Aviones) — state aerospace
  - TANDANOR (naval shipyard)
Context: Long modernisation backlog, foreign-currency constraints;
  recent high-visibility F-16 deal (ex-Danish airframes via Denmark/US).

COMMON COMMERCIAL PATTERNS

- Public-procurement legalism: civil-law systems with extensive
  formal-tender requirements. Contract modifications require published
  addendums. Compliance discipline translates structurally from
  Lusophone (Portugal/Angola/Brazil) procurement-law knowledge.
- Spanish-language SIPRI + IISS Military Balance entries are primary
  open-source baselines.
- Sovereign guarantees + government-to-government (G2G) frameworks
  common — US FMS, UK G2G, French DGA state-to-state schemes.
- Arms embargo coverage: minimal active multilateral embargoes in-region
  (Venezuela limited US sanctions; Cuba US primary sanctions; generally
  a permissive procurement environment).

COMPLIANCE OVERLAY

  - US-centric sanctions: OFAC SDN (Venezuela, Cuba, selected
    Colombian individuals tied to narcotics trafficking)
  - Major FCPA / UK Bribery Act exposure on these programmes — bribery
    risk is a primary DD focus, not secondary
  - ITAR re-transfer: where Chilean / Peruvian / Colombian units operate
    US-origin equipment, European upgrades require explicit US consent

OPEN SOURCES

  - Infodefensa (Spanish-language trade press, wide LatAm coverage)
  - Pucará Defensa (Argentine-focused but regional)
  - Defensa.com
  - SIPRI / IISS / CSIS Latin America programme pages
  - El Mercurio (Chile), La Nación (Argentina), El Tiempo (Colombia)
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "latam_non_lusophone_landscape",
        "text": LATAM_DEFENCE_LANDSCAPE,
        "keywords": [
            "latin america", "latam", "south america", "hispanic", "spanish-speaking",
            "peru", "peruvian", "seace", "ccffaa", "fap",
            "colombia", "colombian", "cotecmar", "indumil", "fac",
            "chile", "chilean", "enaer", "asmar", "famae", "dgmn",
            "ecuador", "ecuadorian", "dine",
            "mexico", "mexican", "sedena", "semar",
            "argentina", "argentine", "fadea", "tandanor",
            "mercosur",
            "fcpa", "itar re-transfer",
        ],
    },
]


def get_latam_context(query: str, max_blocks: int = 1) -> str:
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
        module="knowledge_latam_non_lusophone",
        summary="Get Latam Context",
        source_id="knowledge_latam_non_lusophone:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
