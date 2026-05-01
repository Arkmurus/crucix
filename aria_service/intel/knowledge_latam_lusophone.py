"""ARIA Latin America (Lusophone) Knowledge Module — Brazil-focused.

Brazil is the largest Latin American defence industrial base and the
obvious extension of Arkmurus's Lusophone moat (Angola → Mozambique →
Brazil). Heat-map evidence 2026-05-01: latam_lusophone column at 51%
across procurement / market_intel / technical / geopolitics / osint /
relationships / competitor_intel / compliance / finance — uniform weak
state because no dedicated module covered Brazil-specific content.

This module fills that gap:
  - Defence primes + state institutions (EMBRAER, AVIBRAS, IMBEL,
    Taurus, MoD, COMAER, COMARMAR, EME)
  - Procurement framework (Lei 14.133/2021 — substituting Lei 8.666),
    PGD (Plano de Gestão da Defesa), LBDN (Livro Branco de Defesa)
  - Export control regime (PNEMEM, COMDEX/SECEX, MD declarations)
  - PROSUB / FX-2 (Gripen) / Guarani / SISFRON — flagship programmes
  - Compliance overlay: Lei Anticorrupção (12.846/2013), CGU oversight,
    Lava Jato precedents

Why Brazil-specific (not folded into generic LatAm):
  - Portuguese-language procurement (cross-applicable to Angola/Mozambique)
  - Different commercial-code structure from Spanish-speaking LatAm
  - Distinct export-control regime (PNEMEM ≠ Spanish-LatAm equivalents)
  - Major OEM in its own right (KC-390, Super Tucano, Astros II) — not
    just a buyer
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.knowledge_latam_lusophone")


BRAZIL_DEFENCE_LANDSCAPE = """
═══════════════════════════════════════════════════════════════════════
  ARIA — BRAZIL DEFENCE LANDSCAPE
═══════════════════════════════════════════════════════════════════════

INSTITUTIONAL STRUCTURE

Ministério da Defesa (MD):
  - Civilian-led ministry overseeing the three forces and joint commands
  - Estado-Maior Conjunto das Forças Armadas (EMCFA) — joint staff
  - Secretaria de Produtos de Defesa (SEPROD) — regulates the EED
    (Empresa Estratégica de Defesa) classification, which gives tax +
    credit advantages to qualifying defence firms
  - Secretaria de Comércio Exterior do Ministério da Defesa — defence
    export licensing (PNEMEM regime)

Forces:
  - Exército Brasileiro (EB) — Estado-Maior do Exército (EME),
    Comando Logístico (COLOG), Departamento de Ciência e Tecnologia
    (DCT), Comando Militar de cada região
  - Marinha do Brasil (MB) — Comando de Operações Navais, ComOpNav,
    Diretoria-Geral de Material da Marinha (DGMM)
  - Força Aérea Brasileira (FAB) — Comando da Aeronáutica (COMAER),
    DCTA (Departamento de Ciência e Tecnologia Aeroespacial)

Defence-industrial state apparatus:
  - ABDI (Agência Brasileira de Desenvolvimento Industrial) — strategic
    sector mandate touching defence
  - BNDES — major financier of strategic-sector deals (FX-2, PROSUB
    historic financing)

KEY DEFENCE PRIMES (state + private)

EMBRAER:
  - Public-private; world's third-largest commercial aviation OEM, with
    a defence arm (Embraer Defense & Security)
  - Programmes: KC-390 Millennium (medium tactical airlifter, NATO-
    supported buyers Portugal / Netherlands / Hungary / Czech Rep / Austria
    / South Korea), A-29 Super Tucano (light attack / training, exported
    to ~15 air forces incl. US Combat Wing partner-nation programmes)
  - Defence revenue ~10-15% of group; growing
  - Counterparty type: Tier-1 OEM, full export-licence experience

AVIBRAS:
  - Private (state-affiliated programmes); MLRS / artillery rockets
  - Astros II (SS-30/SS-40/SS-60/SS-80) — Brazilian flagship MLRS, exported
    to Saudi Arabia, Qatar, Indonesia, Malaysia, Bahrain
  - 2024 financial restructuring under judicial recovery — operational
    but financially constrained

IMBEL (Indústria de Material Bélico do Brasil):
  - State-owned; small arms (Imbel pistols, IA2 rifle), munitions,
    electronics, cryptography
  - Supplies primarily Exército Brasileiro; selective export

Forjas Taurus / Taurus Armas:
  - Private (Bovespa-listed); pistols + revolvers + long arms
  - Major US market (Smith & Wesson competitor); civilian + LE focus,
    expanding military

CBC (Companhia Brasileira de Cartuchos) / Magtech / Sellier & Bellot:
  - Munitions; major US export footprint via Magtech brand
  - Recent CBC Group acquired Sellier & Bellot (Czech) and Manurhin (FR)
  - Now one of the largest small-arms ammunition groups globally

Helibras:
  - Subsidiary of Airbus Helicopters; H225M (EC725 Caracal) production
    line in Itajubá supports Brazilian armed forces + regional export

Other notables:
  - Engepron (naval engineering — joint Marinha + DGMM / state-mixed)
  - Mac Jee (smart munitions, guided weapons, joint with Israeli partners)
  - Iveco Defense Vehicles (Iveco Brazil produces Guarani 6×6 + LMV)
  - Akaer (aerospace structures / Gripen NG cooperation)

PROCUREMENT FRAMEWORK

Procurement law:
  - Lei 14.133/2021 — current procurement statute (replaces Lei 8.666/93
    after a transition period; Lei 8.666 still applies to legacy
    contracts and select procedures)
  - Lei 14.133 brings Brazilian procurement closer to European
    competitive-tender norms; introduces "procedimento de manifestação
    de interesse" (PMI) for innovation tenders
  - Defence-specific exemptions: Decreto 8.794 (national security
    secrecy), Lei das Estatais 13.303 (state-enterprise carve-outs)

Strategic plans:
  - PND (Política Nacional de Defesa) + END (Estratégia Nacional de
    Defesa) + LBDN (Livro Branco de Defesa Nacional) — quadrennial
    documents; current cycle covers 2024-2027
  - Cycle of operational plans flows down through PEMD (Plano Estratégico
    Militar de Defesa) and force-specific plans (PEMAER, PEM, PAEMB)

PROCUREMENT GATES (typical flow for foreign supplier):
  1. EED qualification (or via Brazilian partner)
  2. Compensation / offset agreement (Decreto 7.546/2011) — usually
    mandatory for procurement above ~R$ 12M
  3. Tender via Comprasnet / SIASG (federal portal) or force-specific
    tender room
  4. Contract approval — TCU (Tribunal de Contas da União) review
    threshold at ~R$ 200M
  5. CGU (Controladoria-Geral da União) compliance audit thread

FLAGSHIP PROGRAMMES (active)

  - FX-2 / F-39 Gripen — Saab partnership, F-X2 is the FAB designation;
    36+ aircraft, technology-transfer-heavy contract; production at
    Embraer Gavião Peixoto facility
  - PROSUB — 4 conventional + 1 SSN (Álvaro Alberto), French DCNS / Naval
    Group partnership; chronic schedule slips but politically protected
  - Guarani (VBTP-MR) — Iveco 6×6 family for EB; >800 vehicles delivered
  - SISFRON — frontier-monitoring system (Embraer/Saab/Bradar consortium);
    EB sensor + C2 modernisation
  - Astros 2020 — AVIBRAS MLRS upgrade for EB
  - HX-BR — H225M Helibras line, Marinha + EB + FAB
  - SISGAAZ — Marinha maritime-domain awareness system
  - Tamandaré — corvette programme, ThyssenKrupp Marine Systems / Embraer
    consortium; first-of-class delivered 2025

EXPORT-CONTROL REGIME (PNEMEM)

  - Política Nacional de Exportação de Material de Emprego Militar
  - Single-window licensing through Ministério da Defesa (SECOMEX-MD) +
    interministerial committee for sensitive items
  - SECEX (Secretaria de Comércio Exterior) handles dual-use; MD handles
    defence-listed items
  - Brazil is a participant in the MTCR, Wassenaar Arrangement, NSG,
    Australia Group — full multilateral compliance
  - End-user certificate (EUC) regime aligned with Wassenaar standards
  - No re-export consent requirement on Brazilian-origin items unless
    specifically stipulated (commercially favourable — different from
    US ITAR / UK SITCL re-export consent requirements)

COMPLIANCE OVERLAY (FCPA + Lei Anticorrupção)

  - Lei Anticorrupção (12.846/2013) — strict-liability corporate
    responsibility for bribery; CGU enforces; severe debarment regime
  - "Lava Jato" precedents (2014-2021) created robust compliance
    infrastructure — most major defence primes have CGU-validated
    integrity programmes
  - FCPA exposure: any US-touch (US investor, USD financing, US-origin
    component) brings full US DOJ jurisdiction
  - UK Bribery Act exposure: UK-touch brings full UK SFO jurisdiction
  - Brazilian jurisprudence: corporate self-disclosure (acordo de leniência)
    is well-developed and operationally important

ARKMURUS COMMERCIAL PATTERNS

  - Portuguese-language continuity: Brazilian procurement vocabulary +
    Lusophone Africa procurement vocabulary share ~80% terminology
    (licitação, edital, pregão, contratação direta, dispensa). The
    Lusophone procurement corpus in tender_monitor already captures
    Brazilian feeds.
  - Brazil-as-supplier-to-Lusophone-Africa: ARIA has surfaced multiple
    Brazil → Angola / Mozambique export threads (Astros II, Embraer
    aviation, IMBEL ammunition). This is a natural Arkmurus value-chain.
  - Brazil-as-buyer: Tier-1 institutional buyer; competitive tenders
    well-developed; lobbying / relationship discipline matters as much
    as technical fit.
  - G2G frameworks: Brazil ↔ UK (Defence Cooperation Treaty 1944,
    refreshed periodically), Brazil ↔ France (Strategic Partnership
    Agreement covering PROSUB), Brazil ↔ Italy (Iveco / Leonardo
    partnerships), Brazil ↔ Sweden (Saab / Gripen), Brazil ↔ Israel
    (Mac Jee / Elbit).

OPEN SOURCES

  - DefesaNet (defesanet.com.br) — daily Brazilian defence trade press
  - Tecnologia & Defesa (tecnodefesa.com.br) — trade journal
  - Forte (forte.jor.br) — investigative defence reporting
  - Defesa.tv — video / interview format with senior officers
  - SIPRI Brazil pages, IISS Military Balance 2024-2025 entries
  - O Globo / Folha de São Paulo / Estadão — mainstream press
    procurement coverage
  - Comprasnet / SIASG — federal procurement portal (login-walled but
    OCDS open data published)
  - TCU (portal.tcu.gov.br) — audit court rulings, often surface
    procurement disputes
"""


BRAZIL_OFFSETS_AND_OEM_NOTES = """
═══════════════════════════════════════════════════════════════════════
  ARIA — BRAZIL OFFSETS, OEM PARTNERSHIPS, BROKER NOTES
═══════════════════════════════════════════════════════════════════════

OFFSET / COMPENSATION (Decreto 7.546/2011)

  - Mandatory for foreign procurement contracts above ~R$ 12 million
    (threshold inflation-indexed; check current MD circular)
  - Three offset categories:
      1. Direct (related to the procured system — e.g. local production
         of Gripen subassemblies)
      2. Semi-direct (related sector — e.g. aerospace skill-transfer
         programmes funded as part of a non-aerospace deal)
      3. Indirect (unrelated economic activity — increasingly disfavoured
         after 2022 reforms)
  - COMPED (Comissão Mista de Indústria de Defesa) reviews offset
    proposals; SEPROD is the secretariat
  - Common offset shapes: technology transfer, local manufacturing,
    co-development, R&D investment, training, exports support

EED (EMPRESA ESTRATÉGICA DE DEFESA) STATUS

  - Tax + financing advantages for qualifying firms; SEPROD certifies
  - Foreign firms can NOT directly hold EED status — must be via a
    Brazilian-controlled subsidiary or joint venture
  - Examples of EED-certified primes: AEL Sistemas (Elbit Brazil),
    Iveco Defense Vehicles Brasil, Helibras, Mac Jee, AVIBRAS, IMBEL,
    Akaer, Embraer Defesa & Segurança, Saab Aeronáutica Montagens

SUBNATIONAL DIMENSION

  - São José dos Campos (SP) — aerospace cluster; Embraer + DCTA + ITA
  - Itajubá (MG) — Helibras + EFEI engineering school
  - Lorena / Guaratinguetá (SP) — AVIBRAS + propellant infrastructure
  - Curitiba / São José dos Pinhais (PR) — Iveco Defense + Lockheed
    partnership locations
  - Porto Alegre (RS) — Taurus Armas + AEL Sistemas (Elbit Brazil)
  - Rio de Janeiro (RJ) — naval cluster; Marinha + Engepron + AGES

BROKER ACCESS POINTS

  - LAAD Defence & Security (biennial Rio expo) — primary access
    event; full programme office + buyer delegations attend
  - LADS (Latin America Defense & Security) — São Paulo edition
  - Aerolatina / Air Show de Atibaia (regional)
  - SECEX-MD industry working groups — quarterly briefings

POLITICAL / GEOPOLITICAL POSTURE

  - Non-aligned posture: maintains Russian, Chinese, Western relationships
    in parallel; ARIA must NOT assume Western-aligned default
  - BRICS+ active member — financial / industrial implications for any
    Western counterparty exposure to Russian / Chinese co-investment
  - South Atlantic security focus — naval modernisation (Tamandaré, PROSUB)
    is the strategic backbone of this; Atlantic command-and-control
    investment will continue regardless of administration changes
  - Amazon sovereignty + Calha Norte programme drives ground-force
    procurement in remote-deployment systems (jungle aviation, riverine
    naval, frontier ISR)
  - Cooperation thread with Argentina (joint patrols), Uruguay (Mercosur
    technical), Paraguay (frontier security)

COMPLIANCE FLAGS (DD MUST RUN)

  - CGU debarment list (CEIS / CNEP) — auto-screen any Brazilian
    counterparty
  - TCU rulings touching the counterparty's prior contracts
  - Lava Jato / related operations: Odebrecht / Andrade Gutierrez /
    OAS / Camargo Corrêa networks — even resolved firms carry residual
    structural risk in some configurations
  - Ministério Público Federal investigations — slower-cycle but
    consequential

ARKMURUS POSITIONING

  - Brazil ↔ Lusophone Africa value chain is unique to Arkmurus —
    Portuguese-language continuity + UK Treaty access + Brazilian
    OEM relationships = three-way value
  - Direct UK-Brazil sales typical only via FCDO MoU framework + UK
    SITCL/SIEL on the UK side. Arkmurus's role is most defensible as
    advisory + structuring, not as direct broker, in this corridor.
  - Brazil-Africa as a triangulation: Arkmurus advises on the
    compliance + commercial structure (UK-anchored due diligence) while
    the technical sale runs Embraer / IMBEL / AVIBRAS direct to the
    African buyer. Memo this carefully — implicit commercial role
    creates regulatory exposure.
"""


_SEARCH_INDEX: list[dict] = [
    {
        "id": "brazil_defence_landscape",
        "text": BRAZIL_DEFENCE_LANDSCAPE,
        "keywords": [
            "brazil", "brasil", "brazilian", "brasileiro",
            "embraer", "kc-390", "super tucano", "a-29",
            "avibras", "astros",
            "imbel", "iveco defense", "helibras", "h225m", "ec725",
            "taurus armas", "forjas taurus", "cbc", "magtech",
            "engepron", "mac jee", "akaer",
            "marinha do brasil", "exército brasileiro", "fab",
            "força aérea brasileira", "exercito brasileiro",
            "ministério da defesa", "ministerio da defesa", "md",
            "estado-maior conjunto", "emcfa", "comaer", "dctaer",
            "dcta", "comdex", "secomex",
            "pnemem", "lei 14.133", "lei 8.666", "lei das estatais",
            "comprasnet", "siasg", "tcu", "cgu", "ceis", "cnep",
            "lei anticorrupção", "lava jato",
            "fx-2", "f-39", "gripen brasil", "prosub", "guarani vbtp",
            "sisfron", "tamandaré", "sisgaaz",
            "eed", "empresa estratégica de defesa",
            "decreto 7.546", "offset brasil", "compensação militar",
            "laad defence", "laad security", "lads", "aerolatina",
            "itar re-transfer brasil", "wassenaar brasil", "mtcr",
        ],
    },
    {
        "id": "brazil_offsets_oem",
        "text": BRAZIL_OFFSETS_AND_OEM_NOTES,
        "keywords": [
            "brazil offset", "compensação brasil", "decreto 7.546",
            "comped", "seprod", "eed brasil", "ael sistemas",
            "elbit brasil", "saab aeronáutica montagens",
            "são josé dos campos", "itajubá", "lorena", "guaratinguetá",
            "porto alegre defesa", "curitiba defesa",
            "calha norte", "amazon sovereignty", "amazonia defesa",
            "mercosur defence", "south atlantic security",
            "brasil non-aligned", "brics defence", "brics+",
            "brazil africa", "brazil angola", "brazil mozambique",
            "lusophone triangle", "brazil arkmurus",
            "ceis brasil", "cnep brasil", "lava jato compliance",
            "odebrecht", "andrade gutierrez", "camargo corrêa",
        ],
    },
]


def get_brazil_context(query: str, max_blocks: int = 2) -> str:
    """Score-and-rank lookup matching the same shape as the other
    regional knowledge modules. Returns concatenated context blocks
    when the query mentions Brazil-specific entities or procurement
    terminology.
    """
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
