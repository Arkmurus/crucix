"""ARIA OHADA Law Knowledge Module.

OHADA — Organisation pour l'Harmonisation en Afrique du Droit des
Affaires — is the unified commercial-law framework covering 17 African
states. Single largest geographic ROI for any legal module: one
knowledge surface answers commercial-law questions for nearly half of
the continent.

Member states (17):
  Benin, Burkina Faso, Cameroon, Central African Republic, Chad,
  Comoros, Congo (Republic of), Côte d'Ivoire, Democratic Republic of
  the Congo, Equatorial Guinea, Gabon, Guinea, Guinea-Bissau, Mali,
  Niger, Senegal, Togo

Arkmurus-specific overlap:
  - Guinea-Bissau: PALOP Lusophone member AND OHADA member — unique
    legal-stack fusion (Portuguese-language commercial precedent over
    OHADA harmonised commercial code)
  - DRC: OHADA membership since 2012; Great Lakes defence opportunity
    surface
  - Côte d'Ivoire / Senegal / Cameroon: Tier-1 francophone Africa
    commercial hubs; defence-procurement portals already in tender_monitor
  - All 17 states share the same set of Actes Uniformes — write once,
    apply seventeen times

Coverage:
  - Acte Uniforme on Companies Law (AUSCGIE) — entity types (SA, SARL,
    SNC, SCS, GIE), governance, capital
  - Acte Uniforme on General Commercial Law (AUDCG) — trader status,
    commercial registration (RCCM), commercial leases, contracts
  - Acte Uniforme on Securities (AUS) — collateral, mortgages,
    business-asset pledges (nantissement de fonds de commerce)
  - Acte Uniforme on Debt Collection / Enforcement (AUVE) — uniform
    enforcement procedure across member states
  - Acte Uniforme on Arbitration (AUA) — OHADA arbitration regime
    (revised 2017)
  - Acte Uniforme on Accounting (AUDCIF) — uniform accounting
    framework (SYSCOHADA)
  - Acte Uniforme on Cooperative Companies — niche but applicable
  - CCJA (Cour Commune de Justice et d'Arbitrage) — Abidjan-seated
    common court; supreme arbitration + cassation forum
  - Interaction with national law — what OHADA pre-empts vs what
    member-state law continues to govern
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.legal_ohada")


OHADA_FRAMEWORK = """
═══════════════════════════════════════════════════════════════════════
  ARIA — OHADA UNIFIED COMMERCIAL LAW FRAMEWORK
═══════════════════════════════════════════════════════════════════════

ARCHITECTURE

  - OHADA Treaty signed 1993 Port-Louis (Mauritius); revised 2008
    (Quebec)
  - Member states: 17 (currently). Original 14 plus Comoros (2002),
    DRC (2012), and continuing accessions
  - Institutions:
      Conférence des Chefs d'État et de Gouvernement — political
        decision-maker
      Conseil des Ministres — adopts Actes Uniformes
      Secrétariat Permanent (Yaoundé, Cameroon) — administrative
      ERSUMA (École Régionale Supérieure de la Magistrature, Porto-Novo,
        Benin) — judicial training
      CCJA (Cour Commune de Justice et d'Arbitrage, Abidjan, Côte
        d'Ivoire) — supreme judicial + arbitration forum
  - "Actes Uniformes" — uniform laws that PRE-EMPT inconsistent
    national law (Article 10 Treaty); directly applicable in all 17
    member states without national transposition

ACTES UNIFORMES (LIST AS OF 2024)

  AUDCG — Acte Uniforme relatif au Droit Commercial Général
    (revised 2010): general commercial law; trader status; commercial
    registration (Registre du Commerce et du Crédit Mobilier — RCCM);
    commercial leases (bail à usage professionnel); commercial sale;
    commercial intermediaries

  AUSCGIE — Acte Uniforme relatif au Droit des Sociétés Commerciales
    et du Groupement d'Intérêt Économique (revised 2014): entity types,
    governance, capital structure, mergers

  AUS — Acte Uniforme portant Organisation des Sûretés (revised 2010):
    securities — pledges, mortgages, business-asset pledges,
    sureties (cautionnement)

  AUVE — Acte Uniforme portant Organisation des Procédures Simplifiées
    de Recouvrement et des Voies d'Exécution (1998): debt-collection
    streamlined procedures (injonction de payer, injonction de
    délivrer ou de restituer); enforcement (saisies)

  AUPC — Acte Uniforme portant Organisation des Procédures Collectives
    d'Apurement du Passif (revised 2015): insolvency / collective
    proceedings

  AUDCIF — Acte Uniforme relatif au Droit Comptable et à l'Information
    Financière (revised 2017): SYSCOHADA accounting standards;
    individual + consolidated accounts

  AUSCOOP — Acte Uniforme relatif au Droit des Sociétés Coopératives
    (2010): cooperative company form

  AUA — Acte Uniforme relatif au Droit de l'Arbitrage (revised 2017):
    OHADA arbitration regime; institutional + ad-hoc

  AUME — Acte Uniforme relatif aux Contrats de Transport de Marchandises
    par Route (2003): road carriage of goods

ENTITY TYPES (AUSCGIE)

  SA — Société Anonyme:
    - Min capital: 10,000,000 XOF/XAF (~EUR 15,000) for non-public;
      100,000,000 XOF/XAF for public-offering
    - Min shareholders: 1 (single-shareholder SA allowed under 2014
      revision — formerly 7)
    - Mandatory governance: Conseil d'Administration (board) +
      Commissaire aux Comptes (statutory auditor)
    - Two governance models: classical (PCA + DG) or directoire +
      conseil de surveillance
    - Most defence-industrial counterparties in francophone Africa
      are SA

  SARL — Société à Responsabilité Limitée:
    - Min capital: 1,000,000 XOF/XAF (~EUR 1,500) — recently lowered
      in some states
    - Min partners: 1 (SARL Unipersonnelle allowed)
    - Manager (gérant); audit not always mandatory

  SAS — Société par Actions Simplifiée:
    - Introduced by 2014 revision; flexible governance
    - Min capital determined freely by founders
    - Increasing popularity for sophisticated structures (joint
      ventures, holding companies)

  SNC — Société en Nom Collectif: general partnership; joint and
    several liability
  SCS — Société en Commandite Simple: limited partnership
  GIE — Groupement d'Intérêt Économique: economic interest grouping
    (project-specific cooperation vehicle, no own capital required)

COMMERCIAL REGISTRATION — RCCM

  - Registre du Commerce et du Crédit Mobilier — uniform commercial
    register across all 17 member states
  - Held at first-instance commercial court of each domicile
  - Mandatory for traders + companies + branches
  - File extracts (extraits du registre) are the primary DD source
  - Centralised electronic register being rolled out (variable by state)

CONTRACT FORMATION (AUDCG + national civil codes)

  - General principle: written form NOT required unless statute
    specifies; defence-related commercial contracts customarily written
  - National civil codes (heavily French-derived) backstop AUDCG for
    matters AUDCG doesn't address
  - Choice of law: OHADA-state courts apply parties' choice subject
    to public-order limits
  - Limitation periods: 5 years general for commercial obligations
    (AUDCG Article 16)

CCJA — COMMON COURT OF JUSTICE AND ARBITRATION

  - Abidjan-seated; established by OHADA Treaty
  - Two functions:
      1. Supreme cassation court for OHADA-law disputes — replaces
         national supreme courts on OHADA-law questions
      2. Arbitration centre — administers institutional arbitration
         under CCJA Rules of Arbitration
  - CCJA arbitral awards have direct executory force in all 17
    member states (Article 25 Treaty) — automatic exequatur
  - This is the most powerful OHADA feature for commercial parties:
    a single arbitral award enforceable in 17 jurisdictions without
    re-litigation

OHADA ARBITRATION (AUA + CCJA Rules)

  - Two pathways:
      Ad-hoc arbitration / institutional non-CCJA — governed by AUA;
        award still enforceable across OHADA via national exequatur
      CCJA institutional — governed by CCJA Rules; award has direct
        executory force
  - Procedural language: French typical; English / Portuguese /
    Spanish possible by agreement
  - Common neutral seat for francophone-Africa cross-border disputes
"""


OHADA_DEFENCE_AND_ARKMURUS = """
═══════════════════════════════════════════════════════════════════════
  ARIA — OHADA APPLICATION TO DEFENCE / SECURITY COMMERCIAL
═══════════════════════════════════════════════════════════════════════

WHAT OHADA COVERS — AND DOESN'T

  OHADA covers commercial law uniformly (entity formation, contract
  general principles, securities, debt collection, accounting,
  arbitration). OHADA does NOT cover:
    - Public procurement rules — each member state has own procurement
      code (with WAEMU / CEMAC procurement-directive overlay for some)
    - Defence-specific export control — national regulation per state
    - Sanctions implementation — national, with regional bloc
      coordination (ECOWAS / AU)
    - Tax — each state has own tax code (UEMOA harmonisation partial)

PUBLIC PROCUREMENT — NATIONAL FRAMEWORKS

  - WAEMU member states (Benin, Burkina Faso, Côte d'Ivoire,
    Guinea-Bissau, Mali, Niger, Senegal, Togo): WAEMU directives
    2005-2009 harmonise public-procurement principles
  - CEMAC member states (Cameroon, CAR, Chad, Republic of Congo,
    Equatorial Guinea, Gabon): CEMAC directives parallel
  - DRC + Comoros + Guinea: own national codes outside WAEMU/CEMAC
    procurement harmonisation

  Common defence-procurement gates:
    - Direct procurement (gré à gré) for defence often allowed under
      "national-security exception"
    - Inter-government framework agreements (G2G) common — France,
      Russia, China, Türkiye, Israel
    - Formal-tender path uses national procurement portal; defence
      tenders frequently classified

SANCTIONS — COMPLEX OVERLAY

  - UN Security Council sanctions: directly applicable in all OHADA
    states (member-state UN obligations)
  - ECOWAS sanctions: bind 8 OHADA states (West Africa overlap);
    notable on Mali / Niger / Burkina Faso post-2020 transitions
  - AU sanctions: variable application
  - EU sanctions: NOT directly applicable (OHADA states are not EU
    members) but commercial counterparties exposed via banking +
    correspondent-bank channels
  - OFAC: NOT directly applicable BUT USD-touch transactions still
    exposed

KEY DEFENCE-RELATED COUNTERPARTY PATTERNS

  - State-owned defence-industrial primes: rare in OHADA region;
    most procurement is direct foreign-supplier
  - Common foreign supplier states: France, Russia, China, Türkiye,
    Israel, US (selectively), South Africa
  - Brokerage: OHADA-state-domiciled trading companies common; SA or
    SARL structure with French / Lebanese / Indian UBO frequent

ARKMURUS COMMERCIAL CONSIDERATIONS

  Why OHADA matters for Arkmurus:
    - Single legal-knowledge surface answers structural questions for
      17 jurisdictions — efficient advisory leverage
    - Guinea-Bissau is the unique Lusophone-OHADA crossover; ARIA
      can advise on a Lusophone-PALOP deal that runs through
      Bissau under OHADA commercial code
    - DRC + Cameroon + Côte d'Ivoire are active defence markets
      with foreign-broker access points
    - CCJA enforcement gives a strong neutral-arbitration option
      for cross-border deals — CCJA award executes in all 17 states

  Compliance flags:
    - National procurement portals are inconsistent — direct-tender
      visibility limited; relationship-based access dominates
    - State-owned trading vehicles in some markets (e.g., DGSP in
      certain states) act as procurement intermediaries — DD
      requires understanding of state-trading-vehicle disclosure
    - UBO traceability through OHADA RCCM is more reliable than
      pre-OHADA but still uneven; supplement with foreign-domicile
      DD for cross-border holdings

  Common deal structures:
    - SA in OHADA state → French / Portuguese / Belgian holding
      → European OEM (typical defence-procurement intermediary
      structure)
    - GIE for project-specific consortia (rare in defence but
      legally available)
    - Direct G2G with French / Russian / Chinese state — bypasses
      OHADA commercial framework but still triggers AML / sanctions
      obligations

KEY OHADA-SPECIFIC RESOURCES

  - ohada.com — official portal
  - Journal Officiel de l'OHADA (JO OHADA) — Acte Uniforme texts
  - ERSUMA library — judicial training, jurisprudence
  - CCJA (ccja.cm) — arbitration rules, case statistics
  - Recueil des Décisions de la CCJA — case-law digest

OPEN-SOURCE INTELLIGENCE

  - Jeune Afrique — francophone Africa business + defence press
  - Africa Intelligence — paywalled but authoritative
  - DefenceWeb — South African focus but covers regional
  - Group of Experts reports (UN Security Council Sanctions
    Committees on DRC, CAR, Mali, Sudan) — direct evidence of
    procurement / brokerage patterns
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "ohada_framework",
        "text": OHADA_FRAMEWORK,
        "keywords": [
            "ohada", "organisation pour l'harmonisation",
            "harmonisation africaine du droit",
            "actes uniformes", "acte uniforme",
            "auscgie", "audcg", "aus ohada", "auve", "aupc",
            "audcif", "auscoop", "aua",
            "ccja", "cour commune de justice et d'arbitrage",
            "ersuma",
            "rccm", "registre du commerce",
            "syscohada",
            "sa ohada", "sarl ohada", "sas ohada", "snc ohada",
            "scs ohada", "gie",
            "société anonyme africaine",
            "francophone africa law", "francophone commercial law",
            "yaoundé permanent secretariat",
            "abidjan ccja",
            "bail à usage professionnel",
            "nantissement de fonds de commerce",
            "injonction de payer",
        ],
    },
    {
        "id": "ohada_defence_application",
        "text": OHADA_DEFENCE_AND_ARKMURUS,
        "keywords": [
            "waemu", "uemoa", "cemac",
            "cameroon defence", "côte d'ivoire defence", "cote d'ivoire defence",
            "senegal defence", "burkina faso defence",
            "mali defence", "niger defence", "togo defence",
            "drc procurement", "rdc procurement",
            "guinea-bissau lusophone ohada",
            "ecowas sanctions", "au sanctions",
            "francophone defence broker",
            "g2g france africa", "g2g russia africa",
            "ohada arbitration", "ohada enforcement",
            "exequatur ohada",
            "ohada state-trading", "dgsp africa",
            "wamu procurement", "uemoa procurement",
            "cemac procurement",
        ],
    },
]


def get_ohada_legal_context(query: str, max_blocks: int = 2) -> str:
    """Return OHADA-law context blocks ranked by keyword match."""
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
