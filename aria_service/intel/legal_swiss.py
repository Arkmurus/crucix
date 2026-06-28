"""ARIA Swiss Law Knowledge Module.

Switzerland is the settlement, arbitration, and structuring hub for
sensitive cross-border defence deals. Geneva and Zurich host the
arbitration tribunals; Swiss banks settle the high-value transfers;
the Code of Obligations sits behind a large fraction of cross-border
defence supply contracts; the neutrality regime creates a unique
export-control posture.

Coverage:
  - Corporate / commercial — Swiss Code of Obligations (CO / OR /
    CO-CC), entity types (AG / Aktiengesellschaft, GmbH /
    Gesellschaft mit beschränkter Haftung, einfache Gesellschaft)
  - Defence / war-material — KMG (Kriegsmaterialgesetz) /
    AMG (Ausfuhrgesetz), SECO licensing, RUAG / Mowag positioning
  - Goods / dual-use — GKG (Güterkontrollgesetz) covers
    Wassenaar / MTCR / NSG / AG dual-use items
  - Sanctions — Federal Embargo Act (EmbG); Switzerland adopts UN +
    selectively follows EU autonomous sanctions; SECO FAQ-driven
  - AML — GwG (Geldwäschereigesetz, AMLA); FINMA enforcement
  - Banking — banking secrecy formally relaxed post-2018 AEOI
    framework; commercial impact on counterparty traceability
  - Dispute resolution — Swiss Rules (SCAI), Swiss Federal Tribunal
    enforcement, Geneva ICC, neutrality + favourable choice-of-law

Why Swiss-specific (beyond international_law):
  - Neutrality regime CREATES distinct export-licensing constraints
    (re-export restrictions, conflict-zone rules) that don't appear
    in Wassenaar overview alone
  - AG / GmbH choice has direct DD implications (board structure,
    audit thresholds, UBO disclosure regime)
  - SCAI / Swiss Federal Tribunal enforceability matters far beyond
    Swiss-only deals — favoured arbitration seat globally
  - GwG triggers specific compliance behaviour by financial
    intermediaries that ARIA needs to anticipate when a deal touches
    Swiss banking
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.legal_swiss")


SWISS_COMMERCIAL_LAW = """
═══════════════════════════════════════════════════════════════════════
  ARIA — SWISS COMMERCIAL & CORPORATE LAW
═══════════════════════════════════════════════════════════════════════

PRIMARY STATUTE — Code of Obligations (Obligationenrecht / OR / CO)

  - Swiss Federal Code, in force since 1911; corporate-law sections
    last major revision in 2023 (corporate-law modernisation, in
    force 1 January 2023)
  - Civil-law tradition; backed by Swiss Civil Code (ZGB / CC)
  - General contract law in Articles 1-551 OR; corporate forms in
    Articles 552-916 OR
  - Cantonal courts apply CO uniformly (federal law); trial language
    follows canton (DE / FR / IT / RM)

ENTITY TYPES (Article 552 OR onward)

  Aktiengesellschaft (AG / société anonyme / SA) — public limited:
    - Min capital: CHF 100,000 (CHF 50,000 must be paid in)
    - Min shareholders: 1 (single-shareholder AG allowed)
    - Mandatory board (Verwaltungsrat); CEO (Geschäftsführer) optional
    - Bearer shares effectively abolished post-2020 reforms (must be
      registered, with limited exceptions)
    - Public-offer eligible (SIX Swiss Exchange listing)
    - Audit threshold — opting-out for small companies; full audit
      mandatory for large or public AGs
    - Most defence-relevant Swiss companies are AG (RUAG AG,
      Mowag AG, Pilatus Aircraft Ltd / SA)

  Gesellschaft mit beschränkter Haftung (GmbH / Sàrl) — limited:
    - Min capital: CHF 20,000
    - Min partners: 1; max not capped (post-2008 reform)
    - Shareholders register publicly visible (commercial register
      filing required) — distinct from AG anonymity
    - Manager-led; lower governance overhead
    - Common for defence-adjacent SMEs and trading vehicles

  Other forms:
    - Einfache Gesellschaft (simple partnership) — informal contract;
      not a legal entity
    - Kollektivgesellschaft (general partnership) — joint + several
      liability
    - Kommanditgesellschaft (limited partnership) — limited +
      unlimited partners
    - Genossenschaft (cooperative) — rare in defence
    - Branch (Zweigniederlassung) — foreign-incorporated company's
      Swiss presence; commercial register entry required

ARKMURUS COUNTERPARTY DD CHECKS

  - Commercial register (zefix.ch) — federally consolidated, free,
    canonical source. UBO is NOT on zefix; obtain via Swiss UBO
    register access for AML purposes (Art. 73 GwG / AMLA)
  - SHAB (Schweizerisches Handelsamtsblatt) — official gazette for
    all corporate changes
  - UID number (Unternehmens-Identifikationsnummer) is the primary
    corporate identifier (CHE-XXX.XXX.XXX format)
  - Annual financial statements: filed with cantonal commercial
    register; large companies' filings publicly accessible via SHAB
  - Audit reports: mandatory for "ordentliche Revision" companies;
    "eingeschränkte Revision" for small; opt-out for very small
    (under 10 employees + below thresholds)

CONTRACT FORMATION (CO General Part)

  - General principle: written form NOT required unless statute
    specifies (Art. 11 OR); defence contracts of any size
    customarily written
  - Choice of law: Swiss courts apply parties' choice of law if
    legitimate connection or commercial-international context exists
    (Federal Act on Private International Law, IPRG / LDIP); Swiss
    public-order limits apply
  - Limitation period: 10 years general (Art. 127 OR); 5 years for
    periodic obligations (Art. 128 OR)
  - Pre-contractual liability (culpa in contrahendo) is recognised

DISPUTE RESOLUTION

  Swiss arbitration — Swiss Rules of International Arbitration:
    - Administered by SCAI (Swiss Arbitration Centre) since 2021;
      previously by Swiss Chambers' Arbitration Institution
    - Procedural language: any (DE / FR / IT / EN typical)
    - Federal Statute on Private International Law (IPRG/LDIP)
      Chapter 12 governs international arbitration; Article 190 PILA
      sets the narrow grounds for award annulment
    - Swiss Federal Tribunal (Bundesgericht / Tribunal fédéral) is
      the SOLE annulment forum — typical 6-9 month decision; very
      narrow review
    - Switzerland is one of the most arbitration-friendly seats
      globally — minimal court intervention, fast enforcement,
      ratified NY Convention without reservations

  ICC Geneva — when parties prefer ICC Rules but Swiss seat
    LCIA Geneva — when parties prefer LCIA Rules but Swiss seat

  Cantonal commercial courts (Handelsgerichte):
    - Specialised commercial courts in Zürich, Bern, Aargau,
      St. Gallen — first-instance commercial litigation
    - Other cantons: regular cantonal courts with commercial chambers
    - Federal Tribunal (Lausanne) is the sole appeal layer
"""


SWISS_DEFENCE_AND_SANCTIONS = """
═══════════════════════════════════════════════════════════════════════
  ARIA — SWISS DEFENCE LAW, EXPORT CONTROL, SANCTIONS, AML
═══════════════════════════════════════════════════════════════════════

WAR MATERIAL — KMG / AMG / GKG TRIPLE STRUCTURE

  Federal Act on War Material (Kriegsmaterialgesetz, KMG / Loi
  fédérale sur le matériel de guerre, AMG):
    - Governs production, brokerage, transit, and export of
      war-material-listed items
    - SECO (State Secretariat for Economic Affairs) issues licences
    - War Material Ordinance (KMV) implements the act
    - List of war material is published and updated; covers small
      arms through to large platforms

  Goods Control Act (Güterkontrollgesetz, GKG / Loi sur le contrôle
  des biens, LCB):
    - Implements Wassenaar / MTCR / NSG / Australia Group dual-use
      controls
    - Goods Control Ordinance (GKV) lists controlled items
    - Single-use military items typically fall under KMG; civilian-
      capable controlled items fall under GKG; some items under both

  Federal Act on Embargoes (Embargogesetz, EmbG):
    - Authorises Federal Council to enact embargo ordinances
    - Switzerland AUTOMATICALLY adopts UN Security Council embargoes
    - EU autonomous sanctions adopted SELECTIVELY by Federal Council
      decision (sometimes immediate, sometimes delayed, sometimes
      not — assess case-by-case)

NEUTRALITY DOCTRINE — DEFENCE EXPORT IMPACT

  - Neutrality is a CONSTITUTIONAL principle (Federal Constitution
    Art. 173, 185)
  - Practical export-control consequence: KMV Art. 5 — export
    licence DENIED if recipient state is involved in international
    or non-international armed conflict, or if there's clear risk
    that war material would be used against civilians
  - "Wiederausfuhrerklärung" (re-export declaration) — purchaser
    must declare end-use commitment; re-export to third party
    requires fresh Swiss licence (similar in spirit to US ITAR
    re-export consent, but different legal vehicle)
  - 2023 reform partially loosened Ukraine-related restriction on
    re-export of Swiss-origin ammunition; politically tested and
    narrowly applied — operational caution required

SECO LICENSING PROCESS

  - Application via SECO online (Elic / e-licensing) — counterparty,
    end-use, end-user, technical specs
  - Inter-departmental review: SECO + DFA (Foreign Affairs) + DFD
    (Defence) + FIS (Federal Intelligence Service)
  - Decision typical timeline: 2-12 weeks (variable)
  - Refusal grounds: armed-conflict involvement, IHL concerns,
    UN/EU sanction overlap, undisclosed end-use
  - Appeal: SECO refusal can be appealed to the Federal
    Administrative Court (Bundesverwaltungsgericht) within 30 days

SANCTIONS REGIME (EmbG + Federal Council ordinances)

  - SECO maintains the consolidated Swiss sanctions list — UN
    designations + adopted EU/Switzerland-specific entries
  - Russia-Ukraine: Switzerland has progressively aligned with EU
    Russia sanctions (April 2022 onward); not 100% mirror — check
    SECO consolidated list for delta
  - Iran / DPRK / Syria / Belarus: aligned with EU + UN architecture
  - Venezuela / Myanmar: limited adoption (selective)
  - Compliance gating: Swiss banks pre-screen against SECO list
    before facilitating transactions; payment refusal common

AML — GwG / FINMA ENFORCEMENT

  Geldwäschereigesetz (GwG, Anti-Money Laundering Act):
    - Banking sector: full AML duties (KYC, transaction monitoring,
      MROS reporting)
    - Non-financial businesses: brokers / intermediaries — AML duties
      apply if engaged in financial intermediation (Art. 2 para. 3 GwG)
  - FINMA (Swiss Financial Market Supervisory Authority) supervises
    AML compliance for financial intermediaries; enforcement actions
    are public + commercially significant
  - MROS (Money Laundering Reporting Office Switzerland) — FIU;
    suspicious transaction reports

BANKING SECRECY POST-AEOI

  - 2018 onward: Switzerland implements OECD Common Reporting Standard
    (CRS) and FATCA — financial-account information automatic-exchange
    with treaty partners; Swiss banking secrecy as historically
    understood is gone for tax-resident foreign account holders
  - Swiss bank-customer relationship still confidential domestically
    but cross-border transparency is the new baseline
  - Commercial implication: Swiss-banked transactions are no longer
    a meaningful jurisdictional shield from foreign tax / regulatory
    inquiry — counterparty DD must consider the AEOI exposure

KEY DEFENCE / DEFENCE-ADJACENT COMPANIES

  - RUAG MRO Holding AG — Swiss federal armed-forces MRO
  - RUAG International — formerly the export-oriented arm; carved
    out and partially divested 2020-2024
  - Mowag GmbH (now part of General Dynamics European Land Systems);
    armoured vehicles (Piranha, Eagle)
  - Pilatus Aircraft Ltd — PC-21 trainer, PC-24 jet (commercial
    + military training)
  - Saab Switzerland (subsidiary of Saab AB) — Gripen E partial
    pipeline (decision-pending periodically)
  - SIG Sauer Switzerland — small arms (separated from US SIG
    Sauer Inc.)

ARKMURUS COMMERCIAL CONSIDERATIONS

  Why deals route through Switzerland:
    - Swiss-seat arbitration enforceability (NY Convention,
      Federal Tribunal supervision)
    - CHF-denominated escrow accounts via Swiss banks
    - Swiss legal counsel for structuring multi-jurisdictional
      defence supply chains (Geneva, Zurich law firms with strong
      defence practices)
    - Confidentiality regime (commercial-secrecy obligations of
      lawyers / arbitrators / banks)

  Compliance flags:
    - SECO licence status — broker arranging Swiss-origin sale must
      verify licence is in place BEFORE marketing the transaction
    - Re-export commitment — Wiederausfuhrerklärung is a hard
      contractual + regulatory link; broker must structure end-user
      certificate properly
    - Russia / Belarus sanctions delta — SECO list ≠ EU list always;
      verify before assuming Western alignment
    - GwG triggers — if Arkmurus structures a Swiss-touch deal,
      assess whether Arkmurus role qualifies as "Finanzintermediär"
      under Art. 2 GwG

SOURCES

  - SECO official site (seco.admin.ch) — sanctions list,
    war-material licensing
  - Zefix.ch — federal commercial register
  - SHAB.ch — Swiss Official Gazette of Commerce
  - FINMA official site (finma.ch) — AML supervisory rulings
  - Swiss Arbitration Centre (swissarbitration.org) — Swiss Rules,
    case statistics
  - Federal Tribunal jurisprudence (bger.ch) — arbitration annulment
    decisions inform structuring
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "swiss_commercial_law",
        "text": SWISS_COMMERCIAL_LAW,
        "keywords": [
            "switzerland", "swiss", "schweiz", "suisse", "svizzera",
            "code of obligations", "obligationenrecht", "or",
            "code des obligations", "co",
            "swiss civil code", "zgb", "cc",
            "aktiengesellschaft", "ag swiss", "ag schweiz",
            "société anonyme", "societe anonyme", "sa swiss",
            "gmbh swiss", "gesellschaft mit beschränkter haftung",
            "sàrl", "sarl swiss",
            "verwaltungsrat", "geschäftsführer",
            "zefix", "uid swiss", "uid che",
            "shab", "schweizerisches handelsamtsblatt",
            "swiss commercial register",
            "scai", "swiss arbitration",
            "swiss rules", "swiss federal tribunal",
            "bundesgericht", "tribunal fédéral",
            "iprg", "ldip",
            "geneva arbitration", "zurich arbitration",
            "lcia geneva", "icc geneva",
            "handelsgericht", "swiss commercial court",
        ],
    },
    {
        "id": "swiss_defence_and_sanctions",
        "text": SWISS_DEFENCE_AND_SANCTIONS,
        "keywords": [
            "kmg", "kriegsmaterialgesetz", "amg swiss",
            "loi sur le matériel de guerre",
            "war material switzerland", "swiss war material",
            "gkg", "güterkontrollgesetz", "lcb swiss",
            "embg", "embargogesetz", "swiss embargo",
            "seco", "swiss seco", "seco licence",
            "wiederausfuhrerklärung", "swiss re-export",
            "swiss neutrality", "neutralitätsrecht",
            "kmv", "war material ordinance",
            "gwg swiss", "geldwäschereigesetz",
            "amla swiss", "finma", "finma aml",
            "mros switzerland",
            "aeoi", "fatca switzerland", "crs switzerland",
            "ruag", "mowag", "pilatus aircraft", "sig sauer switzerland",
            "swiss banking secrecy",
            "swiss sanctions list", "seco sanctions",
            "swiss russia sanctions",
        ],
    },
]


def get_swiss_legal_context(query: str, max_blocks: int = 2) -> str:
    """Return Swiss-law context blocks ranked by keyword match."""
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
        module="legal_swiss",
        summary="Get Swiss Legal Context",
        source_id="legal_swiss:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]
