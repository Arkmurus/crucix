"""ARIA Gulf Law Knowledge Module.

Tier 1 priority market per memory `heatmap_expansion_regions`. The
Gulf is the highest-value defence-procurement region for the next
decade — Saudi Vision 2030 + UAE EDGE consolidation + Qatar/Kuwait/
Bahrain modernisation programmes — and ARIA needs first-class legal
context to advise on counterparty DD, contract structure, and
compliance.

Coverage:
  - UAE: federal Commercial Companies Law (Federal Decree-Law
    32/2021), the DIFC + ADGM common-law enclaves, DFSA + FSRA
    regulators, UAE federal AML regime (Federal Decree-Law 20/2018),
    Tawazun + GAMI offset doctrine, Federal Customs Authority +
    EXC export-control regime, EDGE Group as state defence holding
  - Saudi Arabia: Companies Law (Royal Decree M/3 2015 as amended by
    Royal Decree M/132 2022), Government Tenders and Procurement
    Law (Royal Decree M/128 / 1440H 2019) — usually called "PPL",
    GAMI / SAMI defence-industrial regime, Saudi Center for
    Commercial Arbitration (SCCA), Vision 2030 procurement framework
  - Bahrain / Qatar / Kuwait: brief adjacent coverage of distinct
    arbitration / company-law regimes
  - Sharia overlay — what it actually means for commercial contracts
    in 2024-2025 (much narrower than common-law misperception)

Why Gulf-specific (beyond international_law / generic Middle East):
  - UAE DIFC vs onshore is a binary choice with very different
    regulatory + dispute-resolution outcomes
  - Saudi PPL 1440H + GAMI offset rules + IKTVA local-content scoring
    is the dominant procurement framework for the next decade —
    NOT captured in any other ARIA module
  - Tawazun + GAMI offset doctrines are unique structures the
    operator must understand at deal-structuring time
  - Sharia courts in onshore UAE / Saudi commercial disputes have
    distinct enforcement profiles compared with DIFC / ADGM /
    international arbitration awards
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.legal_gulf")


UAE_LEGAL_FRAMEWORK = """
═══════════════════════════════════════════════════════════════════════
  ARIA — UAE COMMERCIAL & DEFENCE LAW
═══════════════════════════════════════════════════════════════════════

ARCHITECTURE — ONSHORE / FREE-ZONE / DIFC + ADGM SPLIT

  Three distinct legal regimes coexist in the UAE:

  1. Federal onshore — civil-law tradition with Sharia overlay.
     Federal courts + emirate-level courts (Dubai Courts, Abu Dhabi
     Judicial Department, etc). Arabic-language proceedings.

  2. Free zones (40+) — administratively governed by free-zone
     authority (e.g., JAFZA, DMCC, DAFZA, Sharjah Airport Free
     Zone, Ras Al Khaimah Economic Zone). Mostly operate under
     federal law but offer 100% foreign-ownership + simplified
     incorporation; disputes typically go to onshore courts unless
     otherwise contracted.

  3. DIFC + ADGM — independent common-law enclaves with own courts:
     - DIFC (Dubai International Financial Centre) — common-law
       jurisdiction since 2004; DIFC Courts (English language,
       English-trained judges); DFSA financial regulator
     - ADGM (Abu Dhabi Global Market) — common-law jurisdiction
       since 2015; ADGM Courts apply English common law
       directly (subject to ADGM legislation); FSRA financial regulator
     - DIFC + ADGM judgments enforce on-onshore via UAE Federal
       Court ratification (Federal Law 11/1992, JJDIFC + JJADGM
       protocols)

ONSHORE COMPANY LAW — FEDERAL DECREE-LAW 32/2021 (CCL)

  - Replaced Federal Law 2/2015 (Commercial Companies Law); in force
    since 2 January 2022
  - Key 2021 reform: removed mandatory 51% UAE-national ownership
    for many onshore activities (defence + strategic-sector lists
    retain restrictions, periodically updated by Cabinet)
  - 100% foreign ownership now possible for many commercial activities

  Entity types:
    - LLC (Limited Liability Company) — most common; min capital not
      federally fixed (varies by emirate / activity); 1-50 partners;
      managers (mudirs)
    - Public Joint Stock Company (PJSC) — Borse Dubai / ADX listing
      possible; min capital AED 30 million
    - Private Joint Stock Company (Private JSC / PJSC-private) —
      min capital AED 5 million
    - Sole proprietorship — for natural-person trading
    - Civil company — professional activities (consulting, etc.)

DIFC + ADGM COMPANIES

  - DIFC Companies Law (DIFC Law 5/2018) — registered offices in DIFC
  - ADGM Companies Regulations 2020 — registered offices in ADGM
  - Both regimes allow 100% foreign ownership without UAE-national
    sponsor; English / English-language documentation; common-law
    governance

ARKMURUS COUNTERPARTY DD CHECKS (UAE)

  - Onshore: relevant emirate's economic department registry (Dubai
    DED, Abu Dhabi ADDED, Sharjah SEDD) — paid extracts via the
    department or via UAE Pass + commercial-services apps
  - DIFC: dfsa.ae public register; difccourts.ae for litigation history
  - ADGM: adgm.com public register; adgmcourts.com for litigation
  - Free zones: free-zone authority registry (each has its own
    portal — e.g., jafza.ae for JAFZA, dmcc.ae for DMCC)
  - UBO disclosure: Cabinet Resolution 58/2020 — mandatory UBO
    register for onshore + free-zone companies
  - Federal Tax Authority (FTA) corporate tax registration since
    2023 — primary corporate-touch identifier

CONTRACT FORMATION

  - Onshore: Federal Decree-Law 50/2022 (Commercial Transactions
    Law, replaced Federal Law 18/1993); UAE Civil Code (Federal Law
    5/1985 as amended) backstops
  - Sharia residual: onshore courts CAN apply Sharia principles
    where Federal Law is silent — in modern commercial practice this
    is narrow (riba/interest treatment, gharar/uncertainty doctrine,
    insurance-validity historically) and largely codified in the
    Civil Code itself
  - Choice of law: onshore courts will apply foreign law if validly
    chosen and not contrary to UAE public order; defence + sovereign
    contracts often mandate UAE law
  - DIFC + ADGM: full English-common-law principles; choice-of-law
    enforced as in English courts

DISPUTE RESOLUTION

  Onshore courts:
    - Court of First Instance → Court of Appeal → Court of Cassation /
      Federal Supreme Court
    - Arabic-language; multi-year cycles common for complex commercial

  DIFC Courts (Dubai):
    - English-language; common-law procedure; specialised commercial
      docket (DIFC Court of First Instance, Court of Appeal)
    - DIFC-LCIA arbitration centre (rebranded Dubai International
      Arbitration Centre / DIAC after 2021 restructure — verify
      current institutional posture; DIFC-LCIA was dissolved by
      Decree 34/2021; DIAC is the consolidated successor)
    - DIFC Courts judgments enforce onshore via mutual-recognition
      protocol

  ADGM Courts (Abu Dhabi):
    - English-language; full English-common-law application
    - Specialised commercial chambers
    - Arbitration: ADGM Arbitration Centre (ADGMAC); ICC + LCIA
      seats also available

  DIAC (Dubai International Arbitration Centre):
    - Onshore-Dubai arbitration centre; consolidated successor to
      DIFC-LCIA after 2021; Rules 2022 govern current proceedings
    - English / Arabic procedural language

  Other arbitration centres: Ras Al Khaimah Centre for Reconciliation
    and Commercial Arbitration (RAKCRCA); Sharjah International
    Commercial Arbitration Centre (Tahkeem)

UAE FEDERAL DECREES + AML

  - Federal Decree-Law 20/2018 — federal AML / Combating Financing
    of Terrorism law
  - Cabinet Decision 10/2019 — implementing regulations
  - FATF Grey List exit: UAE removed from grey list February 2024;
    enhanced compliance posture remains
  - FIU: UAE Financial Intelligence Unit (FIU)
  - Sanctions: UAE applies UN Security Council sanctions; selective
    application of unilateral sanctions (commercial alignment with
    US OFAC variable; recent 2022-2024 trend toward closer alignment
    on Russia / Iran)
  - Executive Office of Anti-Money Laundering and Counter-Terrorism
    Financing — federal-level coordinator

UAE DEFENCE / EXPORT-CONTROL REGIME

  - UAE Federal Customs Authority — primary customs gate
  - Tawazun (Tawazun Economic Council, now part of EDGE Group
    structure post-2019 consolidation) — historically the offset
    administrator; offset doctrine remains: foreign suppliers above
    AED 10M defence contract typically commit to local economic
    return obligations
  - GAMI-equivalent UAE structure: EDGE Group is the state defence
    holding (consolidated from 25+ entities in 2019); not a regulator
    but a counterparty
  - Strategic Industries Council oversight under MBZ chairmanship
  - Export control: dual-use items reviewed by Ministry of Economy +
    Federal Authority for Identity, Citizenship, Customs and Ports
    Security (ICP); defence-listed items reviewed by Ministry of
    Defence

KEY UAE DEFENCE COUNTERPARTIES

  - EDGE Group (PJSC, ADX-listed since 2024) — state defence
    holding; subsidiaries: NIMR Automotive, ADASI, EARTH, Caracal,
    Sign4l, GAL (Global Aerospace Logistics), AMMROC, etc.
  - Tawazun Council — offset coordinator (EDGE-affiliated)
  - International Golden Group (IGG) — private defence trader
  - Calidus — armoured vehicles (Calidus B-250 trainer aircraft)
"""


SAUDI_LEGAL_FRAMEWORK = """
═══════════════════════════════════════════════════════════════════════
  ARIA — SAUDI ARABIA COMMERCIAL & DEFENCE LAW
═══════════════════════════════════════════════════════════════════════

ARCHITECTURE — SHARIA + STATUTORY OVERLAY

  - Constitutional foundation: Quran + Sunnah (Sharia); Basic Law of
    Governance (Royal Decree A/90 / 1412H, 1992)
  - Modern statutory codes adopted across the 2010s-2020s: Companies
    Law, Government Tenders and Procurement Law, Civil Transactions
    Law (Royal Decree M/191 / 1444H, in force June 2024 — landmark
    codification of contract / property law)
  - Sharia courts retain jurisdiction primarily for personal-status
    + criminal matters; commercial courts (Diwan al-Mazalim heritage,
    now Specialised Commercial Courts) handle commercial disputes
    using statutory + Sharia principles
  - 2024 Civil Transactions Law represents the most substantive
    convergence with civil-code commercial-law tradition since the
    foundation of the Kingdom

COMPANIES LAW

  - Royal Decree M/3 / 1437H (2015) as substantively amended by
    Royal Decree M/132 / 1444H (2022); current in force
  - 2022 amendment introduced the Simple Joint Stock Company (SJSC)
    + relaxed minimum-capital and shareholder requirements
  - Implementing regulations issued by Ministry of Commerce

  Entity types:
    - Joint Stock Company (JSC) — min capital SAR 500,000 (private);
      higher for listed; min shareholders 1 (post-2022 reform; was 5)
    - Simple Joint Stock Company (SJSC) — 2022-introduced flexible
      form
    - Limited Liability Company (LLC) — min capital not fixed (was
      SAR 500,000 in many activities; 2022 reform deregulated);
      1-50 partners
    - General Partnership / Limited Partnership — partnership forms
    - Branch of foreign company — registered with Ministry of
      Investment (MISA, formerly SAGIA)

ARKMURUS COUNTERPARTY DD CHECKS (SAUDI)

  - Ministry of Commerce (mc.gov.sa) — commercial register;
    Saudi Business Center (saudibusiness.gov.sa) — unified portal
  - National Investor Service (NIS) under Ministry of Investment
    for foreign-investment applications
  - Sharia-compliant due-diligence: commercial counterparties
    increasingly disclose Sharia advisory boards for Sharia-touched
    products
  - Wathq (verified-business platform) — official ID / commercial-
    register-extract verification
  - National Anti-Corruption Commission (Nazaha) — criminal
    enforcement; integrity-record check appropriate for high-value
    counterparties

GOVERNMENT TENDERS AND PROCUREMENT LAW (PPL) — Royal Decree M/128 / 1440H

  - In force since 1 December 2019; replaced 2006 procurement law
  - Centralised + transparent procurement; Etimad portal
    (etimad.sa) is mandatory for all government procurement
  - Implementing regulations: Cabinet Resolution 1242/1441H

  Tender types:
    - General competition (open tender) — default
    - Limited competition — restricted tender for specialised contracts
    - Direct purchase — for items below threshold or sole-source
      with justification
    - Two-envelope (technical + commercial separately) — common
      for defence
    - Auction — primarily real-estate

  Defence procurement specifics:
    - Ministry of Defence (MoD) + General Authority for Military
      Industries (GAMI) jointly govern defence procurement
    - GAMI mandate (Royal Decree, 2017): industrial-policy authority
      for defence sector; offset + IKTVA enforcement
    - SAMI (Saudi Arabian Military Industries, public investment
      fund subsidiary) — state defence holding; consolidated industrial
      capability
    - Vision 2030 target: 50% of defence spend localised by 2030

OFFSET / IKTVA DOCTRINE

  - IKTVA (In-Kingdom Total Value Add) — local-content scoring
    system applicable to large state contractors (especially Aramco,
    Sabic; expanding to defence)
  - GAMI Offset Programme — separate from IKTVA; mandates local
    investment, technology transfer, training for foreign defence
    suppliers above contract threshold
  - Offset categories typically include: local manufacturing, JV
    formation, R&D, training, exports support
  - Saudi-localisation has become the dominant deal-structuring
    constraint — foreign primes increasingly enter via Saudi-domiciled
    JV with SAMI or PIF-affiliated co-investors

SAUDI ARBITRATION

  - Saudi Center for Commercial Arbitration (SCCA, sadr.org) —
    Riyadh + Jeddah seats; SCCA Rules 2023 (revised) align with
    UNCITRAL Model Law
  - Saudi Arbitration Law (Royal Decree M/34 / 1433H, 2012) governs
    domestic + international arbitration
  - Saudi Arabia ratified NY Convention; Sharia public-order
    review on enforcement is the historical concern but in modern
    practice (post-SCCA) is narrowly applied
  - Procedural language: Arabic typical; English / multilingual
    by agreement

OTHER GULF JURISDICTIONS — BRIEF ADJACENTS

  Bahrain:
    - Bahrain Chamber for Dispute Resolution (BCDR-AAA) — Manama
    - Bahrain Companies Law (Decree-Law 21/2001 as amended)
    - Common entity types: WLL (LLC), BSC (BSC closed / BSC public)

  Qatar:
    - Qatar International Court (QIC, common-law) — Qatar Financial
      Centre's adjudication forum
    - Qatar Companies Law (Law 11/2015) for onshore
    - QFC (Qatar Financial Centre) — common-law enclave, parallel
      to DIFC / ADGM
    - Qatar International Centre for Conciliation and Arbitration
      (QICCA)

  Kuwait:
    - Kuwait Chamber of Commerce arbitration centre
    - Kuwait Companies Law (Decree-Law 25/2012)
    - Common entity types: WLL (LLC), KSCC (Kuwaiti shareholding)

  Oman / Iraq: distinct, less commercially active for defence.

SANCTIONS + AML POSTURE

  - Saudi Arabia + UAE + Bahrain + Qatar + Kuwait: all apply UN
    Security Council sanctions
  - GCC sanctions on Iran-aligned non-state actors variable
  - Compliance with US OFAC / UK OFSI is COMMERCIALLY enforced (via
    correspondent banking) but not legally automatic
  - Cabinet-level coordination on Russian sanctions in 2022-2024 has
    been politically calibrated — full alignment NOT to be assumed;
    case-by-case verification

ARKMURUS COMMERCIAL CONSIDERATIONS

  - Saudi-localisation / IKTVA is the dominant deal-structuring
    pressure — foreign primes typically enter via SAMI JV or
    via Saudi-domiciled subsidiary with substantial local content
    + GAMI offset commitment
  - UAE EDGE consolidation makes UAE-side counterparty easier to
    identify (most state-defence-adjacent counterparties are now
    EDGE subsidiaries); but EDGE-affiliation by itself is NOT a
    compliance shortcut — same DD posture applies
  - GCC inter-state defence cooperation creates triangulation
    opportunities (UAE-Saudi joint procurement, GCC Standardisation
    Bureau frameworks) — Arkmurus advisory positioning value
  - Sharia-touched contracts: commercial-products generally
    structured around Sharia-compliance certification (Murabaha,
    Ijara, Sukuk) — not directly relevant to defence procurement
    but can affect financing structure

SOURCES

  - mc.gov.sa, mom.gov.sa — Saudi Ministry of Commerce + Investment
  - etimad.sa — Saudi government procurement portal
  - gami.gov.sa — Saudi GAMI defence-industrial authority
  - sami.com.sa — Saudi Arabian Military Industries
  - sadr.org — Saudi Center for Commercial Arbitration (SCCA)
  - moe.gov.ae, jafza.ae, dmcc.ae — UAE federal economy + free zones
  - difc.ae, difccourts.ae — DIFC
  - adgm.com, adgmcourts.com — ADGM
  - edgegroup.ae — UAE EDGE Group state defence holding
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "uae_legal_framework",
        "text": UAE_LEGAL_FRAMEWORK,
        "keywords": [
            "uae", "united arab emirates", "emirates",
            "dubai", "abu dhabi", "sharjah", "ras al khaimah",
            "rak", "ajman", "fujairah", "umm al quwain",
            "ccl uae", "federal decree-law 32/2021",
            "uae llc", "uae pjsc", "uae jsc",
            "difc", "dubai international financial centre",
            "adgm", "abu dhabi global market",
            "dfsa", "fsra adgm",
            "jafza", "dmcc", "dafza",
            "edge group", "edge uae", "tawazun",
            "nimr", "adasi", "earth uae", "caracal", "sign4l",
            "ammroc", "gal global aerospace logistics", "calidus",
            "international golden group", "igg uae",
            "uae federal customs",
            "diac", "dubai international arbitration centre",
            "difc courts", "adgm courts",
            "fed decree-law 50/2022", "uae civil code",
            "uae aml", "fed decree-law 20/2018",
            "uae fatf",
        ],
    },
    {
        "id": "saudi_legal_framework",
        "text": SAUDI_LEGAL_FRAMEWORK,
        "keywords": [
            "saudi arabia", "saudi", "ksa", "kingdom of saudi arabia",
            "ppl saudi", "1440h", "royal decree m/128",
            "etimad", "etimad portal", "etimad.sa",
            "saudi companies law", "royal decree m/3", "royal decree m/132",
            "saudi llc", "saudi jsc", "saudi sjsc",
            "saudi simple joint stock",
            "saudi civil transactions law", "royal decree m/191", "1444h civil",
            "gami", "gami saudi", "saudi gami",
            "sami", "saudi arabian military industries",
            "vision 2030", "vision 2030 procurement",
            "iktva", "in-kingdom total value add",
            "saudi offset", "gami offset",
            "scca", "saudi center for commercial arbitration",
            "saudi arbitration law",
            "misa", "ministry of investment saudi", "sagia",
            "wathq", "nazaha",
            "specialised commercial courts saudi",
            "diwan al-mazalim",
            "bahrain", "bcdr-aaa",
            "qatar", "qfc", "qicca",
            "kuwait", "kscc", "wll kuwait", "wll bahrain",
            "gcc sanctions",
            "sharia commercial",
        ],
    },
]


def get_gulf_legal_context(query: str, max_blocks: int = 2) -> str:
    """Return Gulf-law context blocks (UAE + Saudi + adjacents)
    ranked by keyword match."""
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
        module="legal_gulf",
        summary="Get Gulf Legal Context",
        source_id="legal_gulf:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2119 §21a — wire failure handler for legal_gulf
try:
    wire_failure(module="legal_gulf", detail="module shutdown",
                gap_type="engine_failure", source="legal_gulf:shutdown")
except Exception:
    pass
