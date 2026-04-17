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
    Igman (small-arms ammo) — export-oriented
  - Compliance: EU CFSP + OHR Bonn-powers framework — advisory-compliance
    heavy, procurement light

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
    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
