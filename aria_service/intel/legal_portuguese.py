"""ARIA Portuguese Law Knowledge Module.

Portugal is the Lusophone moat anchor. Portuguese commercial law
(Código das Sociedades Comerciais), procurement law (Código dos
Contratos Públicos), and defence-export regime cross-apply
structurally to Angola, Mozambique, Cabo Verde, Guiné-Bissau and
São Tomé — same civil-law tradition, same procurement vocabulary,
heavy precedent reuse. ARIA's Lusophone advisory work needs
first-class Portuguese legal context, not just translated English-law
analogues.

Coverage:
  - Corporate / commercial — Código das Sociedades Comerciais (CSC),
    Decreto-Lei 262/86 (foundational), entity types (S.A., Lda.,
    SGPS holdings, Sociedade Unipessoal por Quotas)
  - Public procurement — Código dos Contratos Públicos (CCP),
    Decreto-Lei 18/2008 (consolidated 2017 reform + 2021 update),
    Portal BASE / Portal dos Contratos Públicos
  - Defence-industrial — Decreto-Lei 75/2007 (defence-industrial
    regime), DGAIED authority, idD-Portugal Defence
  - Export control — Decreto-Lei 80/2007 (defence material export),
    DGPE (Direção-Geral de Política Externa) licensing, EU dual-use
    411/2021 + national overlay
  - Sanctions — DGPE clearing list, EU regimes via direct
    applicability of EU Regulations
  - AML — Lei do Branqueamento de Capitais (Law 83/2017),
    SICCRBE register
  - Dispute resolution — Centro de Arbitragem Comercial (CAC) Lisbon,
    Tribunal Arbitral Voluntário, Tribunais Judiciais commercial
    sections
  - Civil-law tradition cross-applicable to PALOP jurisdictions —
    structural overlap explicitly noted

Why Portuguese-specific (beyond international_law / general civil-law):
  - CSC entity-type choices have direct DD implications when an
    Angolan or Mozambican counterparty is structured as a Portuguese
    Lda. or SGPS for Lusophone trade
  - CCP procurement framework's tender-type vocabulary (concurso
    público / ajuste direto / consulta prévia / procedimento de
    negociação) IS the vocabulary used in Angolan + Mozambican
    procurement portals — fluency saves DD friction
  - Decreto-Lei 80/2007 export regime is unique to Portugal;
    licensing path differs from UK SITCL and US ITAR
  - CAC Lisbon arbitration is the obvious neutral seat for
    Portugal-PALOP commercial disputes
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.legal_portuguese")


PORTUGUESE_COMMERCIAL_LAW = """
═══════════════════════════════════════════════════════════════════════
  ARIA — PORTUGUESE COMMERCIAL & CORPORATE LAW
═══════════════════════════════════════════════════════════════════════

PRIMARY STATUTE — Código das Sociedades Comerciais (CSC)

  - Decreto-Lei 262/86 of 2 September 1986; in force since 1 November
    1986; multiple amendments through 2024
  - Companion: Código Comercial (1888) for trader status, commercial
    obligations
  - Companion: Código Civil (1966) general contract law backstop
  - All Portuguese legal codes are accessible via dre.pt (Diário da
    República Eletrónico)

ENTITY TYPES (Articles 1-7 CSC)

  Sociedade Anónima (S.A.):
    - Min capital: EUR 50,000 (lower for some specialised purposes)
    - Min shareholders: 5 (or 1 if shareholder is a company under
      Article 488 CSC) — recently relaxed
    - Mandatory governance: Conselho de Administração + Conselho
      Fiscal (or Audit Committee model adopted via 2006 reform)
    - Public-listing eligible (Euronext Lisbon, regulated by CMVM)
    - Most defence-relevant Portuguese counterparties (Edisoft S.A.,
      OGMA — Indústria Aeronáutica de Portugal S.A., idD-Portugal
      Defence S.A.)

  Sociedade por Quotas (Lda.):
    - Min capital: EUR 1 (post-2011 reform; legacy EUR 5,000)
    - Min partners: 2 (1 for "Sociedade Unipessoal por Quotas")
    - Lighter governance (gerência); single or multiple managers
    - Audit not mandatory unless thresholds exceeded
    - Most common SME form; common Lusophone-Africa trading vehicle

  Sociedade Unipessoal por Quotas:
    - Single-member Lda. (Decreto-Lei 257/96)
    - Common holding structure for individual UBOs

  Sociedade em Nome Coletivo (SNC) — general partnership:
    - Joint + several liability
    - Rare in defence

  Sociedade em Comandita (SC) — limited partnership
    Sociedade em Comandita por Ações — partnership limited by shares
    Both rare modern usage

  SGPS (Sociedade Gestora de Participações Sociais):
    - Decreto-Lei 495/88 — pure-holding company regime
    - Tax + governance benefits for groups holding subsidiaries
    - Common Portuguese-anchored holding for Lusophone-Africa
      operating companies

ARKMURUS COUNTERPARTY DD CHECKS

  - Registo Comercial (Conservatórias do Registo Comercial) —
    canonical company registration; certified extracts (Certidão
    Permanente Online) at app.empresaonline.pt
  - NIPC (Número de Identificação de Pessoa Coletiva) — primary
    corporate identifier
  - Beneficial-ownership: SICCRBE (Sistema de Informação Centralizada
    do Beneficiário Efetivo) created by Law 83/2017 — UBO declaration
    mandatory; partial public visibility through Portal Justiça
  - Annual financial accounts: filed with Registo Comercial; large
    companies + listed companies have higher disclosure obligations
  - CMVM registry — for listed entities + qualified investors

CONTRACT FORMATION (Código Civil)

  - General contract law: Articles 217-294 Código Civil
  - Form: written form NOT generally required unless statute
    specifies (e.g., real estate, marriage settlements); defence
    contracts customarily written
  - Choice of law: Rome I Regulation directly applicable (Portugal
    is EU member); Portuguese public-order limits apply
  - Limitation period: 20 years general (Article 309 CC); 5 years
    for periodic obligations (Article 310 CC); shorter for specific
    commercial disputes

DISPUTE RESOLUTION

  Centro de Arbitragem Comercial — Câmara de Comércio e Indústria
  Portuguesa (CAC Lisbon):
    - Lisbon-seated arbitration centre; Lei da Arbitragem Voluntária
      (Law 63/2011) governs procedure
    - Common neutral seat for Portugal-PALOP disputes (PALOP =
      Países Africanos de Língua Oficial Portuguesa)
    - Procedural language: Portuguese typical; English / Spanish
      possible by agreement
    - Recognition: Portugal ratified NY Convention; Portuguese
      arbitral awards enforceable in Angola / Mozambique under
      Lusophone-bilateral cooperation framework

  Tribunais Judiciais (state courts):
    - Tribunal de Comércio (Comércio do Distrito de Lisboa, Porto,
      etc.) — first-instance commercial cases
    - Tribunais da Relação — appeal courts (Lisboa, Porto, Coimbra,
      Évora, Guimarães)
    - Supremo Tribunal de Justiça (STJ) — final appeal
    - Slow trial cycles (multi-year for complex commercial disputes);
      arbitration is typically faster + commercially preferred
"""


PORTUGUESE_DEFENCE_AND_PROCUREMENT = """
═══════════════════════════════════════════════════════════════════════
  ARIA — PORTUGUESE PUBLIC PROCUREMENT, DEFENCE-INDUSTRIAL, EXPORT
═══════════════════════════════════════════════════════════════════════

PUBLIC PROCUREMENT — Código dos Contratos Públicos (CCP)

  - Decreto-Lei 18/2008 of 29 January 2008; in force since 30 July
    2008; consolidated by Lei 30/2021 (latest major reform)
  - Transposes EU Procurement Directives (2014/24/EU classic, 2014/25/EU
    utilities, 2009/81/EC defence + security)
  - Defence procurement specifically: Articles 16-bis + Title V

  Tender types (procedimentos pré-contratuais):
    - Concurso público (open tender) — default for above-threshold
      contracts; most transparent
    - Concurso limitado por prévia qualificação (restricted with
      prior qualification)
    - Procedimento de negociação (negotiated procedure with prior
      publication) — defence frequently uses this
    - Diálogo concorrencial (competitive dialogue) — complex
      contracts, defence-relevant
    - Parceria para a inovação (innovation partnership) — R&D
      defence procurement
    - Consulta prévia — three-supplier minimum invitation (under
      threshold)
    - Ajuste direto — direct award (under threshold OR sole-source
      with justification)

  Thresholds (2024-2025 EU directives):
    - EU directive thresholds apply to public-sector buyers
    - National thresholds for sub-EU procurement set in Article 19
      CCP

  Portal BASE (base.gov.pt):
    - Mandatory transparency portal for ALL Portuguese public
      contracts
    - Open data via OCDS standard
    - Tender_monitor source — should already be ingesting this

DEFENCE-INDUSTRIAL REGIME (DECRETO-LEI 75/2007)

  - Defines national defence-industrial sector + interest-classification
  - DGAIED (Direção-Geral de Armamento e Infraestruturas de Defesa)
    is the cross-cutting authority
  - idD-Portugal Defence S.A. — state holding for defence-industrial
    interests (formerly EMPORDEF); manages stakes in OGMA, Edisoft,
    EID, Empordef-TI

  Key defence-industrial entities:
    - OGMA — Indústria Aeronáutica de Portugal (Embraer-controlled,
      Portuguese-state minority); aerospace MRO + manufacturing
    - Edisoft — software / C4I for armed forces + export
    - EID — military communications equipment
    - Empordef-TI — IT services for armed forces
    - OGMA / TAP M&E partnerships for engine MRO

EXPORT CONTROL — DECRETO-LEI 80/2007

  - Governs production, trade, and brokerage of defence material
  - DGPE (Direção-Geral de Política Externa) — primary licensing
    authority for defence-material export
  - List of controlled defence material follows EU Common Military
    List (updated annually via Council Decision)
  - EU Dual-Use Regulation (2021/821) directly applicable; DGPE
    handles dual-use national overlay

  Licensing:
    - Export licence application via DGPE; inter-ministerial review
      (DGPE + Ministry of Defence + Ministry of Economy)
    - Decision typical timeline: 4-12 weeks
    - End-User Certificate (EUC) regime aligned with Wassenaar
    - Re-export consent on Portuguese-origin defence items: required
      by export licence terms (parallel to UK SITCL / US ITAR)
    - Refusal grounds: EU Common Position 2008/944/CFSP eight criteria
      (IHL risk, regional stability, economic + technical capacity)

SANCTIONS REGIME

  - Portugal applies UN + EU sanctions DIRECTLY (EU Regulations are
    self-executing)
  - DGPE coordinates national implementation
  - No separate Portugal-specific sanctions list — alignment with EU
    is effectively automatic
  - Russia-Ukraine: full EU package adoption since February 2022

AML — LEI 83/2017

  - Lei 83/2017 of 18 August 2017 — full transposition of EU 4th
    AMLD; subsequent updates for 5th + 6th AMLD
  - SICCRBE — UBO register for Portuguese entities + branches of
    foreign entities
  - Banco de Portugal supervises banking-sector AML; CMVM for
    securities; ASF for insurance
  - Lei 25/2008 (legacy) replaced by Lei 83/2017
  - DCIAP (Departamento Central de Investigação e Ação Penal) —
    financial-crime prosecution; significant defence-corruption
    enforcement record

ARKMURUS COMMERCIAL CONSIDERATIONS

  Lusophone trade structuring:
    - Portuguese SGPS holding owning operating companies in Angola /
      Mozambique is the most-common structuring pattern for
      defence-adjacent commercial activity
    - Triangulation Brazil → Portugal → PALOP can use Portuguese tax
      treaties + investment-protection bilateral agreements
    - PALOP cooperation framework (CPLP — Comunidade dos Países de
      Língua Portuguesa) creates some procurement simplifications +
      legal-recognition shortcuts

  Compliance flags:
    - DCIAP investigations history — significant defence-related
      cases (Tancos arms theft, Operação Tutti-Frutti) inform
      heightened DD posture
    - Lei 83/2017 SICCRBE compliance status — counterparty UBO
      filing currency
    - DGPE licensing status — broker / facilitator must verify
      licence is in place

  Common deal structures:
    - Portuguese SGPS holding → Angolan operating co → procurement
      buyer chain — legitimate but layers of UBO triangulation
      required
    - Portuguese export licence + Angolan import licence + UK SITCL
      (where Arkmurus structures the deal) — three-licence chain
      for any UK-touched Lusophone trade

KEY ARKMURUS-ADJACENT ENTITIES

  - idD-Portugal Defence S.A. — state defence holding
  - OGMA, S.A. — aerospace MRO (Embraer + Portuguese state)
  - Edisoft, S.A. — C4I systems
  - EID, S.A. — military communications
  - Lusosider Aços Planos — steel for armoured platforms (Tata Steel
    / Aços de Portugal heritage)
  - Indra Portugal — defence + civil systems
  - TAP Maintenance & Engineering — engine + airframe MRO
  - Indústria Aeronáutica + Naval (CINAV) — research / shipyard
    (Arsenal do Alfeite) for naval

SOURCES

  - dre.pt — Diário da República Eletrónico (canonical legal text)
  - base.gov.pt — Portal BASE public procurement portal
  - dgpe.gov.pt — DGPE export-control + sanctions
  - app.empresaonline.pt — commercial register access
  - cmvm.pt — securities regulator
  - bportugal.pt — Banco de Portugal
  - cac.com.pt — Centro de Arbitragem Comercial Lisbon
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "portuguese_commercial_law",
        "text": PORTUGUESE_COMMERCIAL_LAW,
        "keywords": [
            "portugal", "portuguese", "português", "portugues",
            "código das sociedades", "codigo das sociedades", "csc portugal",
            "decreto-lei 262/86",
            "código civil", "codigo civil portugal",
            "código comercial", "codigo comercial portugal",
            "sociedade anónima", "sociedade anonima", "s.a.",
            "sociedade por quotas", "lda", "lda.",
            "sociedade unipessoal por quotas",
            "sgps", "sociedade gestora de participações sociais",
            "decreto-lei 495/88",
            "registo comercial", "conservatória do registo",
            "certidão permanente",
            "nipc", "siccrbe", "ubo portugal",
            "cmvm",
            "centro de arbitragem comercial", "cac lisbon",
            "lei 63/2011", "arbitragem voluntária",
            "tribunais judiciais portugal",
            "tribunal de comércio", "tribunal da relação",
            "supremo tribunal de justiça", "stj",
            "palop", "lusofonia legal", "cplp",
        ],
    },
    {
        "id": "portuguese_defence_and_procurement",
        "text": PORTUGUESE_DEFENCE_AND_PROCUREMENT,
        "keywords": [
            "código dos contratos públicos", "codigo dos contratos publicos",
            "ccp portugal", "decreto-lei 18/2008", "lei 30/2021",
            "concurso público", "concurso publico",
            "ajuste direto",
            "consulta prévia", "consulta previa",
            "diálogo concorrencial", "dialogo concorrencial",
            "parceria para a inovação",
            "concurso limitado", "procedimento de negociação",
            "portal base", "base.gov.pt",
            "decreto-lei 75/2007", "decreto-lei 80/2007",
            "dgaied", "direção-geral de armamento",
            "direcao-geral de armamento",
            "dgpe", "direção-geral de política externa",
            "direcao-geral de politica externa",
            "idd portugal", "idd defence", "idd-portugal",
            "ogma", "edisoft", "empordef", "empordef-ti", "eid portugal",
            "tap m&e", "tap maintenance",
            "lei 83/2017", "lei branqueamento", "siccrbe portugal",
            "dciap",
            "operação tutti-frutti", "tancos arms",
            "arsenal do alfeite", "cinav",
            "portuguese export licence", "licença de exportação portugal",
            "indra portugal",
        ],
    },
]


def get_portuguese_legal_context(query: str, max_blocks: int = 2) -> str:
    """Return Portuguese-law context blocks ranked by keyword match."""
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
        module="legal_portuguese",
        summary="Get Portuguese Legal Context",
        source_id="legal_portuguese:R-F996",
    )

    return "\n\n".join(e["text"] for _, e in scored[:max_blocks])


def list_blocks() -> list[str]:
    return [e["id"] for e in _SEARCH_INDEX]

# R-F2119 §21a — wire failure handler for legal_portuguese
try:
    wire_failure(module="legal_portuguese", detail="module shutdown",
                gap_type="engine_failure", source="legal_portuguese:shutdown")
except Exception:
    pass
