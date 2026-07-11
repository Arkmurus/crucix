"""ARIA Balkans Knowledge Module.

Tier 2 positioning region per 2026-04-17 heat-map expansion. Covers
Serbia, Bosnia-Herzegovina, Kosovo, North Macedonia, Albania,
Montenegro, Slovenia, Croatia — the post-Yugoslav + west-Balkans
defence market.

Structural picture: Serbia is the largest defence industrial base in
the region (Yugoimport SDPR, Zastava, Krusik) with legacy Russian +
active Chinese + growing Turkish relationships. Croatia + Slovenia are
NATO / EU members with conventional NATO-aligned supply. Kosovo is
NATO-adjacent; Bosnia carries OHR + EUFOR oversight. Albania + North
Macedonia are NATO members.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging

logger = logging.getLogger("aria.knowledge_balkans")


BALKANS_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — BALKANS DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

KEY COUNTRIES

Serbia:
  - Ministry of Defence + Military Technical Institute (VTI)
  - State primes: Yugoimport-SDPR (holding / export), Zastava Arms
    (small arms), Krušik Valjevo (munitions), Prvi Partizan (ammunition),
    PPT Namenska (artillery), Sloboda Čačak (mortars)
  - Supply pattern: legacy Russian + active Chinese (FK-3 SAM ≈ HQ-22),
    growing Turkish (ASELSAN CESA-X), indigenous innovation (M-20 AFV,
    Lazanski MRAP, ALAS ATGM, Nora-B52 SP artillery)
  - Active exporter — Yugoimport SDPR customers span Africa, Asia, LatAm;
    ALAS + Nora exports are live programmes
  - Compliance: EU candidate status + CFSP alignment gap on Russia
    sanctions creates friction for EU-aligned counterparties
  - Partner Belgrade biennial defence show is the access point

Bosnia and Herzegovina:
  - Two-entity structure (FBiH + RS) under state MoD; OHR + EUFOR Althea
    oversight ongoing
  - Zrak (optics), BNT Novi Travnik (munitions), Binas (munitions),
    Igman (small-arms ammo), TRB Bratunac (system integrator of defence
    solutions, Republika Srpska, founded 1955) — export-oriented
  - Compliance: EU CFSP + OHR Bonn-powers framework — advisory-compliance
    heavy, procurement light
  - Export-control regime (TWO-STAGE licence chain):
      Stage 1 — BiH Ministry of Foreign Trade & Economic Relations
        (Sektor za vanjskotrgovinsku politiku) authorises any export or
        re-export from BiH territory. Required regardless of counterparty
        jurisdiction.
      Stage 2 — UK SITCL (if Arkmurus is party / UK re-export touch) or
        equivalent EU / US control on the downstream side.
      ARIA must raise this two-stage dependency early in any BiH deal —
      single-licence assumption is a common broker mistake and changes
      deal tempo by weeks.
  - Geographic sensitivities:
      Bratunac municipality (Republika Srpska, eastern BiH) is adjacent
      to Srebrenica. Any engagement with RS-based defence counterparties
      from this municipality requires operator awareness of the political
      and historical geography. Not a reason to avoid; a reason never to
      be flippant, and to keep the conversation commercial and technical.
      Do not make jokes about, or casually reference, wartime history.
      ARIA surfaces this as a flag, not an obstacle.

Kosovo:
  - Ministry of Defence + KSF (Kosovo Security Force, transitioning to
    army 2018 decision)
  - NATO-non-member; KFOR presence; US / UK / Türkiye supplying
    training + modest equipment
  - Compliance: five EU non-recognisers (ES / CY / GR / RO / SK) limits
    certain EU framework access

North Macedonia:
  - Ministry of Defence + NATO member (since 2020)
  - Modest budget; NSPA framework access; standardisation-driven procurement

Albania:
  - Ministry of Defence + NATO member (since 2009)
  - Growing Turkish (TB2) + US relationship; Pashaliman naval base
    reconstruction (2023–)

Montenegro:
  - Ministry of Defence + NATO member (since 2017)
  - Very small force; NSPA framework access

Slovenia:
  - Ministry of Defence + NATO / EU member
  - Modern procurement aligned with German / EU frameworks

Croatia:
  - Ministry of Defence + NATO / EU member
  - More capable industrial base (HS Produkt — VHS-2, XD9 pistol; DOK-ING
    mine-clearance); Rafale acquisition 2023

SUPPLY-CHAIN CROSSOVER — SERBIA-ADJACENT RISK

Serbian primes (Yugoimport SDPR etc) are active in African markets
(including Arkmurus-relevant Lusophone). Any mandate involving a Serbian
prime requires:
  1. CFSP-alignment compliance check (Serbia's non-alignment with EU
     Russia sanctions creates a re-export risk for European intermediaries)
  2. End-user certificate chain (Serbian OEM → African MoD → verify
     re-transfer / re-export undertakings)
  3. Sanctions screen for downstream partners, especially Russian /
     Belarusian component suppliers

KEY MEDIA / OPEN SOURCES

  - Janes Balkans desk
  - Balkan Insight (BIRN — regional investigative, strong on procurement)
  - Politika (Serbia, state-aligned)
  - Nova.rs (Serbia, independent)
  - N1 Info (regional)
  - Partner defence show catalogues (biennial, Belgrade)
"""


BALKANS_ARKMURUS_ANGLE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — BALKANS / ARKMURUS POSITIONING
═══════════════════════════════════════════════════════════════════════

Primary opportunity: Serbia supply-chain advisory
  Arkmurus's Lusophone + West-Africa + Gulf access is exactly the
  downstream market Yugoimport SDPR is scaling into. Advisory on:
    - CFSP-compliance routing
    - EUC chain structuring for European re-export
    - Turkish / Gulf co-supply integration
  is a credible fee-earning position.

Secondary: NATO-member trimmed procurement
  Croatia, Slovenia, Albania, Montenegro, North Macedonia — small
  procurement volumes but NSPA framework access provides standardised
  entry. Not primary BD targets but relationship + framework-awareness
  has strategic value.

Compliance rails:
  - Serbia: EU CFSP alignment gap on Russia — advisory must handle
    compliance routing; otherwise EU-anchored clients face CFSP risk
  - Bosnia: OHR + EUFOR framework — advisory-only posture
  - Kosovo: five EU non-recognisers limit EU framework access
"""


BIH_EXPORT_CONTROL_AND_SENSITIVITIES = """
═══════════════════════════════════════════════════════════════════════
  ARIA — BOSNIA AND HERZEGOVINA — EXPORT CONTROL + SENSITIVITIES
═══════════════════════════════════════════════════════════════════════

TWO-STAGE EXPORT-LICENCE CHAIN (COMMON BROKER MISTAKE)

Any export or re-export from a BiH defence counterparty requires BOTH:

  Stage 1 — BiH side
    Ministry of Foreign Trade and Economic Relations (Ministarstvo vanjske
    trgovine i ekonomskih odnosa BiH) — Sektor za vanjskotrgovinsku
    politiku authorises every export of defence-controlled goods.
    BiH is NOT an EU member and NOT a NATO member (Membership Action
    Plan + Stabilisation and Association Agreement only). It maintains
    its own Law on the Import and Export of Arms and Military Equipment.

  Stage 2 — UK / EU / US side (as applicable)
    If Arkmurus (UK) is party to the transaction, UK SITCL applies —
    trade-control licensing on top of the BiH export authorisation.
    Re-export to a third country layers a further licence requirement.

Practical deal-tempo implication: assume a minimum of 6–12 weeks for
dual licensing on a clean, non-embargoed end-user. Embargo-adjacent
destinations: do not pursue without specialist counsel.

ARIA MUST raise this two-stage chain in the first substantive call —
it signals competence to legitimate counterparties and filters out
counterparties uncomfortable with compliance.

GEOGRAPHIC SENSITIVITY — BRATUNAC / SREBRENICA

TRB (Tehnički Remont Bratunac) and any other Republika Srpska defence
counterparty located in the Bratunac municipality — eastern Bosnia,
near the Serbian border — sits adjacent to Srebrenica.

Operator guidance:
  - Keep the conversation commercial and technical.
  - Do not joke about, casually reference, or probe wartime history.
  - Do not assume political alignment from company location. Treat
    the counterparty on its own corporate record.
  - Awareness is for the operator — do not volunteer the geography
    to the counterparty as a talking point.

This is not a reason to avoid engagement. It is a reason never to be
flippant. ARIA surfaces this as a flag on first mention of Bratunac,
Srebrenica, or TRB in any brief.

OTHER REPUBLIKA SRPSKA / FBiH CONTEXT

  - RS entity has periodic secession rhetoric and OHR Bonn-powers
    tension. EU Restrictive Measures have touched specific RS officials
    (not entities like TRB). Check OpenSanctions before every call.
  - FBiH-based manufacturers (Igman, BNT Novi Travnik) operate under
    the federal entity; different political optics but same state-level
    export regime.
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "balkans_landscape",
        "text": BALKANS_DEFENCE_LANDSCAPE,
        "keywords": [
            "balkans", "former yugoslav",
            "serbia", "belgrade", "yugoimport", "vti", "zastava",
            "krušik", "krusik", "prvi partizan", "sloboda čačak", "ppt namenska",
            "bosnia", "sarajevo", "ohr", "eufor althea", "binas", "igman",
            "kosovo", "pristina", "ksf", "kfor",
            "north macedonia", "skopje",
            "albania", "tirana", "pashaliman",
            "montenegro", "podgorica",
            "slovenia", "ljubljana",
            "croatia", "zagreb", "hs produkt", "dok-ing",
            "partner belgrade",
        ],
    },
    {
        "id": "bih_export_control_sensitivities",
        "text": BIH_EXPORT_CONTROL_AND_SENSITIVITIES,
        "keywords": [
            "trb", "tehnicki remont", "tehnički remont", "bratunac",
            "srebrenica", "trb.ba", "trb bratunac",
            "bih export", "bosnia export", "bosnia licence", "bosnia license",
            "bih licence", "bih license",
            "biha ministry of foreign trade",
            "ministarstvo vanjske trgovine",
            "republika srpska", "rs entity",
            "bosnia re-export", "bih re-export",
            "two-stage licence", "two stage licence",
            "bosnia sitcl",
        ],
    },
    {
        "id": "balkans_arkmurus",
        "text": BALKANS_ARKMURUS_ANGLE,
        "keywords": [
            "serbia advisory", "yugoimport advisory",
            "cfsp alignment", "serbia cfsp",
            "balkans arkmurus", "balkans advisory",
        ],
    },
]


def get_balkans_context(query: str, max_blocks: int = 2) -> str:
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
        module="knowledge_balkans",
        summary="Get Balkans Context",
        source_id="knowledge_balkans:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
