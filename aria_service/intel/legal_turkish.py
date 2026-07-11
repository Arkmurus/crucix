"""ARIA Turkish Law Knowledge Module.

Türkiye is a major defence-export hub for Lusophone Africa, MENA,
and Balkans. Bayraktar / ASELSAN / Roketsan / FNSS / Otokar deals
are typically structured through Turkish corporate vehicles. ARIA
needs first-class Turkish legal context to advise on counterparty
DD, contract structure, export licensing, and compliance posture.

Coverage:
  - Corporate / commercial — Türk Ticaret Kanunu (TTK 6102), entity
    types (Anonim Şirket, Limited Şirket), shareholder regime
  - Defence-procurement — SSB (Savunma Sanayii Başkanlığı) regime,
    EYDEP supplier qualification, offset doctrine
  - Export control — SSB licensing for defence-listed items, MoT
    Wassenaar/MTCR compliance, dual-use via TÜBİTAK
  - Data / privacy — KVKK (Kişisel Verilerin Korunması Kanunu)
    parallel to GDPR
  - Dispute resolution — ISTAC arbitration, Turkish Commercial Code
    courts, parallel ICC/LCIA enforcement
  - Sanctions / AML — MASAK, Turkish OFAC-equivalent (Bakanlar
    Kurulu sanctions decrees)

Why Turkish-specific (not folded into general international_law):
  - SSB licensing has unique structure (parallel offset commitment
    + supplier-class registration) not captured in Wassenaar overview
  - EYDEP supplier-qualification system gates ALL Turkish defence
    procurement — foreign primes must understand it
  - Anonim Şirket vs Limited Şirket choice has direct implications
    for Arkmurus advisory engagements (UBO disclosure, board
    composition, audit thresholds)
  - KVKK has distinct enforcement profile from GDPR
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging

logger = logging.getLogger("aria.legal_turkish")


TURKISH_COMMERCIAL_LAW = """
═══════════════════════════════════════════════════════════════════════
  ARIA — TURKISH COMMERCIAL & CORPORATE LAW
═══════════════════════════════════════════════════════════════════════

PRIMARY STATUTE — Türk Ticaret Kanunu (TTK) Law 6102

  - In force since 1 July 2012; replaced TTK 6762 (1957)
  - Modernised Turkish company law along EU directives lines
  - Codified in Turkish Code of Obligations (Türk Borçlar Kanunu, TBK
    Law 6098, in force 1 July 2012) — general contract law backstops TTK
  - Implementing regulation: Companies Regulation
    (Şirketler Yönetmeliği, MoT 2012)

ENTITY TYPES (Article 124 TTK)

  Anonim Şirket (A.Ş.) — Joint Stock Company:
    - Min capital: TRY 250,000 (TRY 500,000 for non-public; check
      current MoT update — threshold has moved with inflation)
    - Min shareholders: 1 (since 2012 reform — single-shareholder A.Ş. allowed)
    - Mandatory board (Yönetim Kurulu); CEO not separately mandatory
    - Independent audit threshold (varies by sector / size)
    - Public-offer eligible (Borsa İstanbul listings via SPK regime)
    - Defence-procurement preferred form: most prime-tier defence
      counterparties are A.Ş. (ASELSAN A.Ş., Roketsan A.Ş., TUSAŞ A.Ş.)

  Limited Şirket (Ltd. Şti.) — Limited Liability Company:
    - Min capital: TRY 50,000
    - Min partners: 1 (since 2012); max 50
    - Manager-led (Müdürler) instead of board
    - Lower audit / disclosure burden — common for SME defence suppliers
    - Tax + social-security identical to A.Ş. — choice is governance
      not fiscal

  Komandit (Limited Partnership), Kollektif (Unlimited Partnership):
    - Rare in defence; legacy structures

  Sermayesi Paylara Bölünmüş Komandit Şirket — partnership limited
    by shares; rare modern usage

ARKMURUS COUNTERPARTY DD CHECKS

  - Trade Registry Gazette (Türkiye Ticaret Sicili Gazetesi, ticaretsicil.gov.tr)
    is the authoritative registration source — NOT chamber-of-commerce
    web pages
  - Beneficial-ownership: 2021 reform requires UBO declaration to
    Revenue Administration (GİB) — not always public-visible but
    obtainable via due-diligence service providers
  - Tax ID (Vergi Kimlik No, VKN) is the primary corporate identifier
  - MERSIS number — central registration system
  - Annual GENEL KURUL (general assembly) minutes filed with Trade Registry
    are the strongest signal of ongoing operational compliance

CONTRACT FORMATION (TBK)

  - Written form requirement: arms / defence contracts of any size
    are typically written; specific writing requirements for
    transfer of immovables, marriage settlements, surety
  - Limitation period: 10 years general; 5 years for periodic
    obligations (Article 146 TBK)
  - Choice of law: Turkish courts will apply foreign law if validly
    chosen and not contrary to Turkish public order; defence
    contracts touching SSB regime typically mandate Turkish law +
    Turkish-seat arbitration

DISPUTE RESOLUTION

  ISTAC (Istanbul Arbitration Centre):
    - Operational since 2015 — neutral seat for Turkish counterparty
      arbitrations
    - Rules align with UNCITRAL Model Law / NY Convention (Türkiye
      ratified NY Convention)
    - Procedural language: Turkish or English typical
    - Increasingly preferred over ICC Paris for Turkey-touching deals
      because of cost + judicial-deference advantages

  Turkish Commercial Courts (Asliye Ticaret Mahkemeleri):
    - First-instance commercial courts; specialised for commercial
      disputes
    - 2-year average to first-instance judgment (variable)
    - Appeal: Bölge Adliye Mahkemesi (regional appeal court) →
      Yargıtay (Court of Cassation)

  ICC / LCIA enforcement in Türkiye:
    - NY Convention foundation — Turkish courts enforce foreign
      arbitral awards subject to public-order review
    - Enforcement procedure: Asliye Hukuk Mahkemesi (general civil
      court) — can take 12-24 months; bond requirements may apply
"""


TURKISH_DEFENCE_REGIME = """
═══════════════════════════════════════════════════════════════════════
  ARIA — TÜRKİYE DEFENCE PROCUREMENT, EXPORT CONTROL, SANCTIONS
═══════════════════════════════════════════════════════════════════════

DEFENCE PROCUREMENT — SSB (Savunma Sanayii Başkanlığı)

  - Presidential agency since 2018 (previously SSM under MoND);
    reports directly to the Presidency
  - Centralised procurement authority for armed-forces acquisition
    AND export-promotion of Turkish defence products
  - Annual procurement budget — multi-billion-USD; SSB executes both
    domestic supply and inward-licensing (TLK / OEM partnerships)
  - Strategic plans: 2024-2028 Strategic Plan (replaced 2019-2023);
    PRESIDENTIAL Defence Industry Strategy Document (Cumhurbaşkanlığı
    Savunma Sanayii Strateji Belgesi)

EYDEP — Endüstriyel Yetkinlik Değerlendirme ve Destekleme Programı

  - Mandatory supplier-qualification programme — every supplier in
    Turkish defence supply chain must register and pass capability
    assessment (production / quality / cybersecurity / financial)
  - Five tiers — A1/A2 (prime + system integrator), B (major sub),
    C (qualified), D (registered), E (exit/no-go)
  - Foreign suppliers can register via Turkish subsidiary or a
    Turkish licensee — direct foreign-entity EYDEP registration is
    not standard
  - Renewal: bi-annual; any change of control triggers re-assessment
  - Non-EYDEP-qualified suppliers cannot bid on SSB-managed tenders

OFFSET DOCTRINE

  - SSB Offset Directive (latest revision 2017, technical updates
    quarterly) — mandatory offset for procurement above ~USD 5M
  - Three categories:
      A — direct (within procured system)
      B — semi-direct (related defence sector)
      C — indirect (Turkish economic activity, civil OK)
  - Multipliers — A=1.0, B=0.7, C=0.3 typically; high-value programmes
    can negotiate higher multipliers for strategic technology transfer
  - Specific Local Industry Participation (SLIP) targets — 50%+ on
    most current programmes

EXPORT CONTROL

  - SSB controls export of Turkish-origin defence goods (Karar
    Sayısı / Council of Ministers decree licensing)
  - Dual-use export — TÜBİTAK Bilim Kurulu reviews; MoT (Ticaret
    Bakanlığı) issues licence
  - Türkiye is a participant in:
      Wassenaar Arrangement
      Missile Technology Control Regime (MTCR)
      Australia Group (chemical / biological)
      Nuclear Suppliers Group (NSG)
  - End-User Certificate (EUC) regime aligned with Wassenaar standards
  - Exports to embargoed destinations: Türkiye applies UN Security
    Council embargoes; UNILATERAL US / EU embargoes are NOT
    automatically applied (sometimes politically followed, sometimes not)
  - Re-export consent on Turkish-origin items: NOT generally required
    by Türkiye after delivery (commercially favourable; different
    from US ITAR / UK SITCL re-export consent regimes)

SANCTIONS / AML / FINANCIAL CRIME

  - MASAK (Mali Suçları Araştırma Kurulu) — Türkiye FIU; AML
    enforcement, financial-crime investigations
  - Turkish own-list sanctions: Bakanlar Kurulu (Council of Ministers)
    decrees, primarily UN-following + selective national designations
  - Türkiye does NOT automatically apply EU or US OFAC sanctions
    unless via UNSC mandate — important for ARIA when assessing
    cross-border deal exposure
  - FATF Grey List status — Türkiye removed from grey list June 2024;
    upgrade to FATF compliant; enhanced DD still recommended for
    cross-border counterparty work

ARKMURUS COMMERCIAL CONSIDERATIONS

  Counterparty type recognition:
    - Anonim Şirket (A.Ş.): publicly-disclosed company info, board
      structure visible, easier UBO triangulation
    - Limited Şirket: lighter disclosure; UBO often via private
      family-office structure — extra DD layer

  Common deal structures:
    - Turkish prime → Lusophone-Africa buyer: SSB export licence
      required Turkish-side; UK SITCL on the UK side IF Arkmurus is
      structuring or UK-touch present
    - Joint-venture Turkish-foreign prime: Turkish JV vehicle is
      almost always A.Ş.; foreign partner via shareholder agreement
      with EYDEP-qualified Turkish operating company
    - Turkish offset commitment to a Western prime: typically structured
      via SLIP with A or B-category obligations; structuring this is
      a substantive Arkmurus advisory engagement

  Compliance flags:
    - PKK / FETÖ-related entity disclosures — Turkish own-list and
      court rulings; cross-screen with both Turkish + Western lists
    - Sanctions misalignment: Russian-touch deals where the Turkish
      counterparty has parallel non-sanctioned trade — UK / EU side
      may treat the broker as constructively involved
    - Currency controls: TRY volatility creates payment-structure
      risk; USD / EUR settlement common but central-bank conversion
      controls have hardened periodically

  Key OEMs:
    - ASELSAN A.Ş. (electronics, communications, EW)
    - Roketsan A.Ş. (rockets, missiles, ammunition)
    - TUSAŞ A.Ş. (aerospace)
    - Otokar Otomotiv ve Savunma Sanayi A.Ş. (armoured vehicles)
    - FNSS Savunma Sistemleri A.Ş. (armoured vehicles, JV with BAE)
    - Havelsan A.Ş. (software, simulation, C4I)
    - MKE A.Ş. (Mechanical and Chemical Industry — small arms,
      ammunition, large-calibre)
    - Baykar Savunma (private; UAVs — TB2, Akıncı, Kızılelma)

SOURCES

  - SSB official site (ssb.gov.tr) + procurement portal
  - Türkiye Ticaret Sicili Gazetesi (commercial registry)
  - MASAK reports
  - SDIA / SAHA Istanbul (industry associations) — broker access
  - IDEF biennial expo (Istanbul) — primary access event
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "turkish_commercial_law",
        "text": TURKISH_COMMERCIAL_LAW,
        "keywords": [
            "turkey", "türkiye", "turkish", "turkiye",
            "ttk", "türk ticaret kanunu", "turk ticaret",
            "tbk", "türk borçlar kanunu", "turk borclar",
            "anonim şirket", "anonim sirket", "a.ş.", "a.s.", "a/s",
            "limited şirket", "limited sirket", "ltd. şti.", "ltd. sti.",
            "yönetim kurulu", "yonetim kurulu",
            "vergi kimlik", "vkn", "mersis",
            "turkish trade registry", "ticaret sicili",
            "spk", "borsa istanbul",
            "istac", "istanbul arbitration",
            "yargıtay", "yargitay",
            "asliye ticaret",
            "tbk article",
            "general assembly turkey", "genel kurul",
        ],
    },
    {
        "id": "turkish_defence_regime",
        "text": TURKISH_DEFENCE_REGIME,
        "keywords": [
            "ssb", "savunma sanayii başkanlığı", "savunma sanayii baskanligi",
            "ssm",
            "eydep", "endüstriyel yetkinlik",
            "tübitak", "tubitak",
            "masak",
            "turkish offset", "ssb offset", "slip turkey",
            "wassenaar turkey", "mtcr turkey",
            "aselsan", "roketsan", "tusaş", "tusas",
            "otokar defence", "fnss", "havelsan", "mke",
            "baykar defence", "baykar legal",
            "saha istanbul", "sdia",
            "idef expo", "idef istanbul",
            "kvkk",
            "turkish export licence", "turkish defence export",
            "fatf turkey", "turkey grey list",
            "fetö", "feto", "pkk sanctions turkey",
            "trl", "try volatility",
        ],
    },
]


def get_turkish_legal_context(query: str, max_blocks: int = 2) -> str:
    """Return Turkish-law context blocks ranked by keyword match."""
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
        module="legal_turkish",
        summary="Get Turkish Legal Context",
        source_id="legal_turkish:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
