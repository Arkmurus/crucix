"""dd_disciplines — the comprehensive Due Diligence discipline framework.

Why this exists
═══════════════
Operator request 2026-05-10: "ARIA needs to be an expert, do not cut corners.
Same for defence and security." Earlier in the same session: "ensure she
really digs in deep not just saying so but doing it actually" (adverse media).

ARIA's existing DD pipeline has good infrastructure (10 layers in
dd_orchestrator) but the *disciplines* are implicit. A defence-broking
buyer or a commodity-trading buyer asking "what do you check?" should get
a structured, complete answer — not a shrug. This module is that answer.

What this module is
═══════════════════
A declarative catalogue of every Due Diligence discipline ARIA should
exercise on a counterparty, with for each discipline:
  - Definition (what it is)
  - Why it matters (the failure mode it prevents)
  - Evidence sources (where the data comes from)
  - Verification procedure (how to actually check, not just claim)
  - Common failure modes (how DD typically goes wrong on this discipline)
  - Output format (what a covered finding looks like in a DD report)

Plus:
  - ENTITY_TYPE_DISCIPLINES — which disciplines apply to which kind of target
  - required_disciplines(entity_type) — the discipline list for an entity type
  - discipline_coverage_check(report, entity_type) — grade a DD report against
    its required disciplines (catches "the report mentions sanctions but didn't
    actually run a litigation check")
  - adverse_media_query_templates(entity_name) — structured deep-search
    templates so adverse media is RUN not just CLAIMED

What this module is NOT
═══════════════════════
- Not a replacement for dd_orchestrator's layer pipeline. dd_orchestrator
  EXECUTES layers; this module DEFINES the discipline standards each layer
  output is judged against.
- Not auto-wired. Operator opts into discipline_coverage_check() at the
  end of the orchestrator pipeline (or post-hoc on a stored report).
- Not a positioning change. ARIA stays defence-broking + compliance; this
  module just makes the DD discipline EXPLICIT so a buyer can audit it.

Discipline taxonomy
═══════════════════
Disciplines are grouped into three tiers:
  - UNIVERSAL: applies to any commercial counterparty regardless of sector
  - DEFENCE: specific to defence/security broking (Arkmurus core)
  - COMMODITY: specific to oil/LNG/crude/regulated-commodity broking
                (regulated_commodity_pack adjacency)

A DD on a defence-OEM counterparty runs UNIVERSAL + DEFENCE.
A DD on an LNG broker runs UNIVERSAL + COMMODITY.
A DD on a hybrid (defence-trader who also touches dual-use) runs all three.

Authoring discipline
════════════════════
Per Clauses 14 + 17, every verification procedure cites primary-source URLs
where possible. Where a procedure depends on operator-domain knowledge
(e.g. "verify against Arkmurus's known-fraud counterparty list"), the
procedure says so explicitly. No fabricated sources.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.dd_disciplines")


# ══════════════════════════════════════════════════════════════════════
# THE DISCIPLINE CATALOGUE
# ══════════════════════════════════════════════════════════════════════

DD_DISCIPLINES: dict[str, dict[str, Any]] = {

    # ──────────────────────────────────────────────────────────────────
    # UNIVERSAL DISCIPLINES — apply to any commercial counterparty
    # ──────────────────────────────────────────────────────────────────

    "identity_verification": {
        "tier": "UNIVERSAL",
        "definition": (
            "Confirm the entity claimed by the counterparty actually exists, "
            "in the jurisdiction claimed, with the legal form claimed, at "
            "the address claimed, with the registration number claimed."
        ),
        "why_it_matters": (
            "Identity is the foundation of every other discipline — sanctions "
            "screening, UBO trace, banking verification all depend on the "
            "right entity being identified. Wrong identity = entire DD void. "
            "Past incident pattern (Modirum 2026-04-09): ARIA fabricated "
            "registry data for an unverified entity."
        ),
        "evidence_sources": [
            "Companies House (UK), Handelsregister (DE), CNPJ (BR), AE Federal Authority (UAE), KRS (PL), ANRC (RO), Cégbíróság (HU), ARES (CZ), Slovak Trade Register, OpenCorporates aggregator, jurisdiction-specific registries",
            "Apostille / consular-attested incorporation certificate (international transactions)",
            "Government gazette publications (incorporation notices)",
        ],
        "verification_procedure": [
            "1. Confirm entity exists in named jurisdiction's primary registry",
            "2. Cross-reference registration number, legal form, registered address against the registry record",
            "3. Verify company status is ACTIVE (not dissolved, struck off, in liquidation)",
            "4. Pull the most recent annual / confirmation statement filing date — stale filings are a red flag",
            "5. Where multiple jurisdictions are claimed (HQ vs operations), verify each separately",
        ],
        "common_failure_modes": [
            "Counterparty provides a registration number that doesn't exist in the named registry",
            "Number exists but for a different (similar-named) entity",
            "Entity exists but is in liquidation / struck off",
            "Address provided is a virtual office / mail-forwarding service",
            "Legal form claimed (e.g. 'Inc.') doesn't match jurisdiction (US-style suffix on Italian entity)",
        ],
        "output_format": {
            "discipline": "identity_verification",
            "status": "verified | partial | failed",
            "registry_source": "<URL>",
            "registration_number": "<actual>",
            "legal_form": "<actual>",
            "incorporation_date": "<YYYY-MM-DD>",
            "registered_address": "<actual>",
            "company_status": "active | dissolved | struck_off | liquidating",
            "discrepancies_with_counterparty_claim": "<list or empty>",
        },
    },

    "sanctions_screening": {
        "tier": "UNIVERSAL",
        "definition": (
            "Screen the entity, its directors, its UBOs, and any vessels / "
            "assets it owns or operates against ALL applicable sanctions "
            "lists. Multi-jurisdiction by default — single-list screen is "
            "insufficient for international transactions."
        ),
        "why_it_matters": (
            "Sanctions exposure is the most common cause of broker enforcement "
            "actions. A single missed designation can void an insurance claim, "
            "trigger correspondent-bank de-risking, or expose the broker to "
            "criminal prosecution. US OFAC enforcement actions against brokers "
            "regularly settle for $1M-$200M (Vitol 2020 $163M for Iran)."
        ),
        "evidence_sources": [
            "OFAC SDN List (treasury.gov, search.ofac.treas.gov)",
            "OFAC Non-SDN lists (NS-PLC, NS-CMIC, FSE-IR, BIS Entity List)",
            "UK OFSI Consolidated List (gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets)",
            "EU Consolidated Financial Sanctions List (sanctionsmap.eu)",
            "UN Security Council Sanctions List (un.org/securitycouncil/sanctions)",
            "Country-specific autonomous lists: Australia DFAT, Canada SEMA, Switzerland SECO, Japan METI",
            "OpenSanctions.org (aggregator with API for cross-list search)",
            "World-Check / Refinitiv (paid; broader coverage including PEP + adverse media)",
            "Dow Jones Risk & Compliance (paid alternative)",
        ],
        "verification_procedure": [
            "1. Run name-variant set (transliterations, abbreviations, common misspellings) against each list",
            "2. For each potential match, examine match score + identifiers (DOB for individuals, registration number for entities) — name-only match is INSUFFICIENT",
            "3. Apply 50% Rule (OFAC) and equivalent control rules (UK OFSI) to detect indirect designation via UBO chain",
            "4. Cross-list check: an entity may be on EU but not UK, on UK but not US — record divergence explicitly",
            "5. Date-stamp the screen — sanctions lists update daily; screen has a 24-hour TTL for material transactions",
        ],
        "common_failure_modes": [
            "Single-list screening (only OFAC; missing EU/UK/UN coverage)",
            "Name-only match without identifier verification → false negative on sanctioned entity with common name",
            "Failure to apply 50% Rule → indirect designation via subsidiary missed",
            "Stale screen relied on for a transaction days/weeks later",
            "Shared-source 'independent' screens (multiple feeds derived from same upstream)",
        ],
        "output_format": {
            "discipline": "sanctions_screening",
            "status": "clean | match_blocking | match_partial | divergent",
            "lists_screened": ["OFAC SDN", "UK OFSI", "EU Consolidated", "UN SC", "..."],
            "screen_date": "<ISO timestamp>",
            "matches": [{"list": "...", "match_score": 0.0, "match_record_id": "...", "verification_status": "..."}],
            "fifty_percent_rule_applied": True,
            "divergence_notes": "<text>",
        },
        "fail_consequences": [
            "MATCH_BLOCKING → HARD_STOP — DD orchestrator short-circuits remaining layers (per dd_orchestrator design)",
            "MATCH_PARTIAL → human review required",
            "DIVERGENT → record both positions; transaction structure must satisfy MOST RESTRICTIVE position applicable to all parties",
        ],
    },

    "pep_screening": {
        "tier": "UNIVERSAL",
        "definition": (
            "Politically Exposed Person screening — identify whether the "
            "entity, its directors, or its UBOs hold or have recently held "
            "prominent public functions. Includes domestic + foreign PEPs + "
            "international-organisation PEPs + close associates + family."
        ),
        "why_it_matters": (
            "PEP exposure does NOT prohibit transaction (PEPs are not "
            "sanctioned by definition) but triggers Enhanced Due Diligence "
            "obligations under FATF Recommendations 12+22 — failure to apply "
            "EDD on a PEP-linked counterparty is itself an AML enforcement "
            "exposure. Source-of-funds verification becomes mandatory."
        ),
        "evidence_sources": [
            "World-Check / Refinitiv (paid — broadest PEP coverage)",
            "Dow Jones Risk & Compliance (paid)",
            "OpenSanctions PEP dataset (free aggregator)",
            "Country-specific PEP lists where published (some EU members publish national lists)",
            "Public-record cross-check: government websites, parliamentary databases, OECD State-Owned Enterprise database",
        ],
        "verification_procedure": [
            "1. Screen entity directors + UBOs (≥25% control per FATF) against PEP databases",
            "2. For each PEP match: identify role + jurisdiction + tenure + currency (current vs historical PEP — historical PEP risk decays, typically 12-24 months post-departure for declassification)",
            "3. Family + close-associate screen (PEP's spouse, children, business partners — FATF includes these)",
            "4. Source-of-funds documentation requirement: PEP-linked transactions need explicit SOF / SOW evidence",
            "5. Senior-management approval at the broker before commercial commitment (Arkmurus governance)",
        ],
        "common_failure_modes": [
            "Director-only PEP screen (missing UBO PEPs)",
            "Active-only screen (missing recently-departed PEPs whose risk hasn't decayed)",
            "Missing family + close-associate dimension",
            "Treating PEP designation as automatic blocker (it's an EDD trigger, not a block)",
            "No documented SOF / SOW for PEP-linked transactions",
        ],
        "output_format": {
            "discipline": "pep_screening",
            "status": "clean | pep_identified | recent_pep | family_pep",
            "pep_matches": [{"name": "...", "role": "...", "jurisdiction": "...", "active_since": "...", "active_until": "current|YYYY-MM"}],
            "edd_required": True,
            "sof_documentation_status": "obtained | pending | refused",
        },
    },

    "adverse_media": {
        "tier": "UNIVERSAL",
        "definition": (
            "Targeted deep-search for negative information about the entity, "
            "its directors, its UBOs, and known associates. Covers: criminal "
            "indictments, civil litigation, regulatory enforcement actions, "
            "fraud allegations, bankruptcy / insolvency, reputational harm "
            "articles, investigative journalism, leaked-document mentions."
        ),
        "why_it_matters": (
            "OPERATOR PRIORITY 2026-05-10: 'Aria needs to really dig in deep "
            "not just saying so but doing it actually'. Adverse media is the "
            "discipline that catches what sanctions screening can't — a "
            "counterparty might be clean on every list but have a documented "
            "pattern of contract disputes, fraud allegations, or whistleblower "
            "claims. Surface-level web search MISSES this; depth requires "
            "structured query templates against multiple source classes."
        ),
        "evidence_sources": [
            # Court records + litigation
            "PACER (US federal courts, paid per-page)",
            "Justia.com (US case law summaries — free)",
            "CourtListener.com (US federal + state, free)",
            "BAILII (UK + Ireland case law, free)",
            "European Court of Justice (curia.europa.eu — free)",
            "Country-specific court databases (LexisNexis paid for cross-jurisdiction)",
            # Regulatory enforcement
            "OFAC Recent Actions (treasury.gov/recent-actions)",
            "OFSI Recent Enforcement (gov.uk/government/publications/financial-sanctions-enforcement)",
            "SEC EDGAR (litigation releases, AAERs)",
            "DOJ press releases (justice.gov/news)",
            "FCA Final Notices (fca.org.uk/news)",
            "EU Commission DG-COMP (decisions on competition + state aid)",
            "SFO Cases (sfo.gov.uk/cases)",
            "BaFin enforcement (Germany)",
            "AMF enforcement (France)",
            # Investigative journalism + leaks
            "ICIJ (Pandora Papers, Panama Papers, Paradise Papers, OpenLux searchable databases)",
            "OCCRP (Organized Crime and Corruption Reporting Project)",
            "Bellingcat investigations",
            "Reuters Investigates",
            "FT Investigations",
            "Bloomberg Investigates",
            "Wall Street Journal Investigations",
            # News archive (10+ year depth)
            "Google News (current)",
            "Bing News (current)",
            "Wayback Machine (archived versions of news pages)",
            "Common Crawl (historical web corpus, queryable by URL/domain)",
            "NewspaperArchive.com (paid, deep historical news)",
            "ProQuest (paid academic + news archive)",
            # Industry-specific
            "World-Check / Refinitiv adverse-media component (paid)",
            "Sayari Graph (paid; corporate networks + adverse media + leaks)",
            "Industry trade press (Defense News, Argus, Platts, Tradewinds, Lloyd's List Maritime — paid for full archive)",
        ],
        "verification_procedure": [
            "1. Run STRUCTURED query templates (see adverse_media_query_templates() below) — NOT freeform 'search the entity name' which produces noise",
            "2. Templates target each source class: legal/court, regulatory, journalism, leaks, news",
            "3. Time-window: minimum 10 years back; longer for historical-pattern detection",
            "4. Each finding documented with source URL + publication date + finding-type tag (litigation/enforcement/allegation/leak/etc.)",
            "5. Follow-the-thread: a single allegation often leads to related parties — screen those too",
            "6. Confidence tier per finding (Tier 1a = court ruling; Tier 1b = regulator action; Tier 2 = investigative journalism with documents; Tier 3 = unverified allegation)",
            "7. Materiality assessment — distinguish minor commercial disputes from serious findings (criminal, sanctions-related, fraud)",
            "8. Cross-check that absent findings are because nothing exists, not because the search was shallow",
        ],
        "common_failure_modes": [
            "Treating Google search top-10 as 'adverse media check complete'",
            "English-only search when the entity operates primarily in another language",
            "Stopping at the first 'no matches found' without trying name variants, transliterations, or director-name searches",
            "Missing leak-database checks (Pandora/Panama/OpenLux)",
            "No time-window discipline — only recent results visible",
            "Not following threads — finding allegation against director X but not searching for X's other associations",
        ],
        "output_format": {
            "discipline": "adverse_media",
            "status": "clean | findings_present | thin_coverage | refused",
            "templates_executed": "<count>",
            "source_classes_searched": ["legal_court", "regulatory_enforcement", "investigative_journalism", "leak_databases", "news_archive_10y", "industry_trade_press"],
            "findings": [
                {"type": "litigation | enforcement | allegation | leak | conviction",
                 "source_url": "...", "publication_date": "...", "finding_tier": "1a|1b|2|3",
                 "materiality": "high|medium|low", "summary": "..."}
            ],
            "thin_coverage_reason": "<text if status=thin_coverage>",
        },
        "operator_priority_note": (
            "Per operator request 2026-05-10, this discipline is the FIRST-PRIORITY "
            "depth-investment area. Surface-level only is unacceptable. The "
            "adverse_media_query_templates() function below provides the structured "
            "query set that distinguishes 'really dug in' from 'said we did'."
        ),
    },

    "ubo_chain": {
        "tier": "UNIVERSAL",
        "definition": (
            "Trace the ownership chain from the immediate counterparty entity "
            "up to the Ultimate Beneficial Owner(s) — natural persons who "
            "ultimately own or control 25%+ of the entity. Multi-jurisdiction "
            "+ multi-layer where required."
        ),
        "why_it_matters": (
            "Sanctions exposure flows through the UBO chain (50% Rule). PEP "
            "designation often hides at UBO level, not director level. AML "
            "Source-of-Funds verification requires identifying the ultimate "
            "natural person sources. Shell-company structures designed to "
            "obscure UBO are themselves a fraud signal (Clause 16 beneficial-"
            "ownership-evasion pattern)."
        ),
        "evidence_sources": [
            "EU Beneficial Ownership Registers (UBOs since 2017 4th AML Directive; access varies by member state post-2022 ECJ ruling)",
            "UK Persons with Significant Control (PSC) register (free, public, searchable via Companies House)",
            "FinCEN Beneficial Ownership Information (BOI — US, in-force from 2024)",
            "Cayman Beneficial Ownership Register (post-2017 reform, law-enforcement-only access)",
            "BVI BOSS (Beneficial Ownership Secure Search system, post-Pandora Papers; competent-authority access)",
            "OpenCorporates beneficial ownership data (where filed publicly)",
            "Sayari Graph (paid; cross-jurisdiction UBO synthesis)",
            "Bureau van Dijk Orbis (paid; comprehensive corporate-structure data)",
        ],
        "verification_procedure": [
            "1. Pull immediate-shareholder data from primary registry",
            "2. For each corporate shareholder, recursively trace until natural person OR until 4 layers without resolution (4-layer-deep failure = obfuscation flag)",
            "3. Apply 25% control threshold per FATF (some jurisdictions use 10% for higher-risk sectors)",
            "4. Identify nominee directors / nominee shareholders — flag as opacity layer",
            "5. Cross-check identified UBOs against sanctions + PEP lists (feeds those disciplines)",
            "6. Where UBO opacity persists despite 4-layer trace, document explicitly — opacity itself is a finding",
        ],
        "common_failure_modes": [
            "Treating immediate shareholder as 'UBO' when it's itself a corporate entity",
            "Stopping at the first opaque jurisdiction without flagging the gap",
            "Missing nominee directors (they appear as people but represent hidden principals)",
            "Failing to apply 50% Rule for sanctions through aggregated UBO",
        ],
        "output_format": {
            "discipline": "ubo_chain",
            "status": "fully_traced | opacity_flagged | refused",
            "depth_traced": "<int layers>",
            "ultimate_beneficial_owners": [{"name": "...", "control_pct": 0.0, "jurisdiction": "...", "verified_against": "registry|self-declaration"}],
            "opacity_layers": [{"layer": <int>, "jurisdiction": "...", "reason": "registry private | nominee | shell"}],
            "nominee_directors_flagged": [{"name": "...", "service_provider": "..."}],
        },
    },

    "source_of_funds": {
        "tier": "UNIVERSAL",
        "definition": (
            "Documentation of the specific source of funds for the contemplated "
            "transaction (typically the buyer's funds for the cargo / goods). "
            "Distinct from Source of Wealth (which is general)."
        ),
        "why_it_matters": (
            "AML obligation under FATF + national AML law. Without SOF, "
            "transaction proceeds may be proceeds of crime (PoC) — a UK MLR "
            "2017 / Bribery Act / POCA exposure. PEP-linked transactions "
            "REQUIRE explicit SOF documentation."
        ),
        "evidence_sources": [
            "Bank statements demonstrating accumulation of transaction funds",
            "Recent corporate financial statements showing cash flow consistent with transaction size",
            "Loan documentation (if funds are debt-financed)",
            "Investor / shareholder injection documentation (if equity-financed)",
            "Buyer's bank Reference Letter confirming funds availability",
            "MT760 (SWIFT confirmed LC notification) for LC-financed transactions",
        ],
        "verification_procedure": [
            "1. Funds size matches transaction value (within reasonable working-capital margin)",
            "2. Source institution is in a non-sanctioned jurisdiction with credible AML supervision",
            "3. If transaction-specific funding (loan, investor injection), verify the lender / investor independently",
            "4. Banking institution itself screened against sanctions + correspondent-banking risk",
            "5. Documents independently verified with issuing institution (NEVER via seller-provided contact)",
        ],
        "common_failure_modes": [
            "Bank Reference Letter accepted at face value — letterhead forgery is endemic",
            "MT760 'preview' or 'sample' accepted instead of actual SWIFT confirmation",
            "Funds source disclosed as 'private investors' without identifying them",
            "Source bank in opacity jurisdiction (off-shore secrecy bank)",
        ],
        "output_format": {
            "discipline": "source_of_funds",
            "status": "documented | partial | undocumented | refused",
            "funds_amount_demonstrated": "<currency + amount>",
            "source_type": "operating_cash | loan | equity | mixed",
            "source_institution": "<bank name + jurisdiction>",
            "verification_method": "independent_bank_contact | document_only | refused",
        },
    },

    "source_of_wealth": {
        "tier": "UNIVERSAL (PEP-linked: REQUIRED)",
        "definition": (
            "Broader documentation of the historical accumulation of wealth "
            "for individual UBOs / PEPs. Different from Source of Funds — "
            "SOW is the LIFETIME picture, SOF is the TRANSACTION picture."
        ),
        "why_it_matters": (
            "PEP-linked transactions require SOW per FATF Recommendation 12. "
            "Wealth disproportionate to demonstrated career / business "
            "history is a major fraud / corruption signal."
        ),
        "evidence_sources": [
            "Career history documentation (professional + executive positions, dates, employers)",
            "Inherited-wealth documentation (probate records, settlement agreements)",
            "Business sale / IPO documentation",
            "Investment performance records",
            "Tax filings (if disclosed)",
            "Real-estate ownership history (public registers)",
        ],
        "verification_procedure": [
            "1. Wealth claim consistent with documented career / business history",
            "2. Major wealth events (business sales, IPOs) verifiable against public records",
            "3. Disproportion between claimed wealth and documented sources = red flag",
            "4. For PEPs especially: scrutinise wealth accumulation during public office period (corruption indicator)",
        ],
        "common_failure_modes": [
            "Self-attestation accepted without documentary backing",
            "Career history claims unverified against employer records",
            "Large 'inheritance' claims without probate / settlement documentation",
        ],
    },

    "litigation_history": {
        "tier": "UNIVERSAL",
        "definition": (
            "Comprehensive search for civil + criminal litigation involving "
            "the entity, its directors, its UBOs. Civil suits, judgments, "
            "arbitration awards, criminal prosecutions, regulatory actions."
        ),
        "why_it_matters": (
            "Pattern of litigation indicates commercial reliability + ethical "
            "posture. Recent fraud judgments are the strongest signal a "
            "counterparty will produce the same with you. Arbitration awards "
            "are particularly informative because they reveal disputes that "
            "didn't reach public court."
        ),
        "evidence_sources": [
            "PACER (US federal courts)",
            "CourtListener.com",
            "Justia.com",
            "BAILII (UK + Ireland)",
            "European Court of Justice (curia.europa.eu)",
            "Lloyd's List Intelligence + Insurance — maritime arbitration",
            "GAR (Global Arbitration Review) for international commercial arbitration",
            "ICSID database (investor-state)",
            "London Court of International Arbitration awards (where published)",
            "AAA / ICDR (US arbitration)",
            "Country court systems via official portals",
        ],
        "verification_procedure": [
            "1. Search by entity name + by each director name + by each UBO name",
            "2. Time-window minimum 10 years; longer for serious findings",
            "3. Categorise findings: contract dispute (commercial signal) vs criminal (ethical signal) vs regulatory (compliance signal)",
            "4. For each finding: outcome (won / lost / settled / pending) + counterparty + amount where disclosed",
            "5. Pattern-match — multiple disputes of same type = systematic behaviour pattern",
        ],
        "common_failure_modes": [
            "US-only search (missing UK / EU / Asia litigation)",
            "Entity-only search (missing director-level cases)",
            "Treating 'settled' as exoneration — settlement frequently includes denial of liability but the underlying conduct may be informative",
        ],
        "output_format": {
            "discipline": "litigation_history",
            "status": "clean | findings_present | thin_coverage",
            "jurisdictions_searched": ["..."],
            "time_window_years": 10,
            "findings": [{"case": "...", "court": "...", "year": "...", "type": "civil_commercial|criminal|regulatory|arbitration",
                          "outcome": "...", "counterparty": "...", "amount": "..."}],
        },
    },

    "regulatory_enforcement": {
        "tier": "UNIVERSAL",
        "definition": (
            "Search for regulator-imposed enforcement actions: fines, "
            "censures, license revocations, deferred-prosecution agreements, "
            "monitorships. Across all relevant regulators in the entity's "
            "operating jurisdictions + sectors."
        ),
        "why_it_matters": (
            "Regulators only act on evidence sufficient to enforce — "
            "regulatory enforcement is HIGHER signal than journalism "
            "allegation. Past enforcement predicts future compliance "
            "behaviour. Some regulators (FCA, OFAC) maintain enforcement "
            "monitorship that the broker should know about."
        ),
        "evidence_sources": [
            "OFAC enforcement (treasury.gov/recent-actions)",
            "OFSI enforcement (gov.uk OFSI page)",
            "BIS enforcement (US Commerce — bis.doc.gov)",
            "DDTC enforcement (US State — pmddtc.state.gov)",
            "DOJ press releases (justice.gov)",
            "SEC enforcement (sec.gov litigation releases + AAERs)",
            "FINRA enforcement",
            "FCA Final Notices (fca.org.uk)",
            "PRA enforcement",
            "SFO Cases (sfo.gov.uk)",
            "BaFin (Germany)",
            "AMF (France)",
            "FINMA (Switzerland)",
            "MAS (Singapore)",
            "HKMA (Hong Kong)",
            "ASIC (Australia)",
            "CSSF (Luxembourg)",
            "EU competition cases (DG-COMP)",
            "Sector-specific: FERC (US energy), OFGEM (UK energy), CFTC (US derivatives)",
        ],
        "verification_procedure": [
            "1. Sector + jurisdiction map → relevant regulator list",
            "2. Search each regulator's enforcement database for entity name + director names + UBO names",
            "3. Categorise: monetary fine | censure | license action | DPA | criminal referral | monitorship",
            "4. Status: closed | active monitorship | open investigation",
            "5. Amount + date for materiality assessment",
        ],
        "output_format": {
            "discipline": "regulatory_enforcement",
            "status": "clean | findings_present | active_monitorship",
            "regulators_searched": ["..."],
            "actions": [{"regulator": "...", "year": "...", "type": "...", "amount": "...", "status": "..."}],
        },
    },

    "operational_substance": {
        "tier": "UNIVERSAL",
        "definition": (
            "Verify the entity has REAL operations matching its claimed "
            "business — physical premises, employees, demonstrated commercial "
            "activity, real customers / suppliers."
        ),
        "why_it_matters": (
            "Shell companies fail on operational substance. The Panama LNG-"
            "broker DD pattern (today's lngtradinginternationalpanamasa.com "
            "case) hinges on this discipline — a real LNG broker has Kpler-"
            "documented cargo movement; a paper broker doesn't."
        ),
        "evidence_sources": [
            "Site visit (in-person inspection — gold standard, expensive)",
            "Google Maps Street View of registered address",
            "Satellite imagery (Sentinel Hub free, Maxar paid) for warehouses / facilities",
            "LinkedIn employee count + employee profile validity",
            "Glassdoor / Indeed reviews",
            "Demonstrated transaction history — Kpler / Lloyd's List / Bureau van Dijk",
            "Customer / supplier references (verified independently, not via target)",
            "Industry-association membership (verified via the association directly)",
            "Trade-show attendance records (defence: DSEI, IDEX, Eurosatory; oil: ADIPEC, OTC Houston, Gastech)",
        ],
        "verification_procedure": [
            "1. Registered address is NOT a virtual-office service (cross-check known virtual-office providers)",
            "2. LinkedIn presence: company has plausible employee count for claimed activity; employees have plausible profile depth",
            "3. Documented commercial activity matches claimed scale (e.g. broker claiming 1 mbpd cargo movement should appear in Kpler)",
            "4. Customer / supplier references verified via independent contact",
            "5. If any of the above fail, escalate to Enhanced DD requiring physical confirmation",
        ],
        "common_failure_modes": [
            "Counterparty website + LinkedIn presence accepted as substance proof",
            "Failing to cross-check address against virtual-office provider lists",
            "Reference contacts provided BY the target (which may be co-conspirators)",
            "Treating recent domain registration + slick website as 'professional' rather than 'newly fabricated'",
        ],
    },

    "financial_soundness": {
        "tier": "UNIVERSAL",
        "definition": (
            "Assessment of the counterparty's ability to perform — solvency, "
            "liquidity, leverage, going-concern status, audit opinion, debt "
            "service capacity."
        ),
        "why_it_matters": (
            "Counterparty insolvency mid-transaction is a major loss vector. "
            "Failing audit opinion (qualified, adverse, disclaimer) signals "
            "material accounting issues. Going-concern qualification = high "
            "probability of failure within 12 months."
        ),
        "evidence_sources": [
            "Latest audited annual report + auditor's opinion",
            "Interim financial statements",
            "Credit rating agency reports (S&P, Moody's, Fitch — paid)",
            "Bond price + CDS spread (if traded — implied credit risk)",
            "Trade-credit insurance availability + cost (Atradius, Coface, Euler Hermes)",
            "Bank-arranged trade finance availability",
            "Bureau van Dijk Orbis financial data (paid)",
            "Dun & Bradstreet credit reports (paid)",
        ],
        "verification_procedure": [
            "1. Latest audited financials: most recent < 18 months old (older = stale)",
            "2. Auditor opinion: clean (unqualified) is the baseline; qualified / adverse / disclaimer = red flag",
            "3. Going-concern note review",
            "4. Liquidity ratios consistent with transaction size",
            "5. Recent rating-action history (downgrades cluster before defaults)",
        ],
        "common_failure_modes": [
            "Self-prepared 'management accounts' accepted as audited financials",
            "Auditor unverified (small unknown firm vs Big 4 / Tier 2 with verifiable registration)",
            "Going-concern qualification missed in audit-report fine print",
        ],
    },

    "reputational_intelligence": {
        "tier": "UNIVERSAL",
        "definition": (
            "Industry-grapevine / off-record / reputation-network assessment. "
            "What does the broker community say about this counterparty? "
            "What does prior-deal counterparty experience reveal?"
        ),
        "why_it_matters": (
            "Some signals never reach public record — a counterparty who "
            "consistently chisels on demurrage, walks away from minor losses, "
            "or has a pattern of bad-faith arbitration positioning leaves a "
            "trail in the industry network long before it shows in court."
        ),
        "evidence_sources": [
            "Arkmurus's own counterparty experience records",
            "Industry-association members in the relevant sector (DSEI / ADIPEC / Gastech contacts)",
            "Trade-finance bank intelligence (informally — credit officers track behaviour)",
            "Broker-network references",
            "Off-record counterparty references via mutual acquaintances",
        ],
        "verification_procedure": [
            "Operator-domain — discipline definition, but execution requires Arkmurus's network",
            "ARIA's role: prompt for and structure operator-supplied reputational signals",
            "Document each signal with attribution + materiality + freshness",
        ],
    },

    "banking_verification": {
        "tier": "UNIVERSAL",
        "definition": (
            "Verify the counterparty's nominated banking institution is "
            "real, in good standing, in a non-sanctioned jurisdiction, with "
            "credible AML supervision, and that the named account is "
            "actually held by the counterparty (not an intermediary)."
        ),
        "why_it_matters": (
            "Wire-fraud + email-compromise interception of payment "
            "instructions is one of the most common loss vectors in "
            "international trade. Counterparty's bank itself may be on "
            "sanctions list (e.g. specific Russian banks) → transaction "
            "blocks or correspondent-bank de-risks."
        ),
        "evidence_sources": [
            "BIC / SWIFT directory verification (swift.com)",
            "Banking regulator's licensed-institution list for the bank's home jurisdiction",
            "Bank's own published address + institutional contact (NOT via counterparty-provided contact)",
            "OFAC SDN check on the bank itself + intermediary / correspondent banks in payment chain",
            "FATF mutual evaluation of bank's home jurisdiction",
        ],
        "verification_procedure": [
            "1. SWIFT BIC valid + matches claimed bank",
            "2. Bank licensed in claimed jurisdiction (regulator confirms)",
            "3. Bank not on OFAC SDN / EU consolidated / UK OFSI",
            "4. Correspondent bank chain identified for USD / EUR / GBP transactions",
            "5. Account-holder name verified directly with bank (small test transfer) BEFORE main payment",
            "6. Any change to wire instructions during the transaction = STOP, verify by phone with known bank contact",
        ],
        "common_failure_modes": [
            "Wire instructions in email accepted without phone verification",
            "Mid-transaction 'updated wire instructions' accepted without verification (classic email-compromise pattern)",
            "Bank in opacity jurisdiction (offshore secrecy banks — Cayman + BVI not always but sometimes)",
            "Correspondent-bank chain ignored — USD transaction touches OFAC even if direct parties don't",
        ],
    },

    "insurance_verification": {
        "tier": "UNIVERSAL (commodity / shipping: REQUIRED)",
        "definition": (
            "Verify cargo + transit + liability insurance is in place with "
            "credible insurer, covers the value + risks of the transaction, "
            "and is enforceable in the relevant jurisdictions."
        ),
        "why_it_matters": (
            "Russia G7 oil price cap regime hinges on insurance — non-IG "
            "(non-International Group of P&I Clubs) insurance is a "
            "compliance flag. Cargo loss without enforceable insurance = "
            "uncovered loss to the broker if the buyer disputes."
        ),
        "evidence_sources": [
            "Certificate of Insurance — direct verification with insurer",
            "P&I Club confirmation (vessel insurance)",
            "Cargo insurance policy + named-perils coverage",
            "Trade-credit insurance (where buyer credit is the risk)",
            "International Group of P&I Clubs membership (igpandi.org)",
        ],
        "verification_procedure": [
            "1. Insurer is licensed + solvent in its home jurisdiction",
            "2. P&I Club is IG member (the 13 IG clubs handle ~90% of world tonnage)",
            "3. Coverage amount + types match transaction risk",
            "4. Policy enforceable in relevant jurisdictions (sanctions exclusions checked)",
            "5. For commodity: alternative (non-IG) insurance arrangements are a Russia-evasion-pattern flag",
        ],
        "common_failure_modes": [
            "Certificate of Insurance from an unverifiable issuer",
            "Coverage limits inadequate for cargo value",
            "Sanctions exclusion clauses voiding coverage in relevant transaction context",
        ],
    },

    "jurisdiction_country_risk": {
        "tier": "UNIVERSAL",
        "definition": (
            "Assessment of the country / jurisdiction risk for each leg of "
            "the transaction — counterparty home jurisdiction, transit "
            "jurisdictions, end-user jurisdiction, banking jurisdictions."
        ),
        "why_it_matters": (
            "Country risk drives sanctions exposure, AML scrutiny, "
            "convertibility risk, and political-risk insurance pricing. "
            "FATF grey-list jurisdictions trigger Enhanced Due Diligence; "
            "FATF black-list (Iran, DPRK) trigger counter-measures."
        ),
        "evidence_sources": [
            "FATF list of jurisdictions under increased monitoring (grey list) + call for counter-measures (black list)",
            "FCDO Travel Advice (UK)",
            "US State Department Country Reports + Travel Advisories",
            "Transparency International Corruption Perceptions Index",
            "World Bank Worldwide Governance Indicators",
            "EIU Country Risk Service",
            "S&P / Moody's / Fitch sovereign ratings",
            "Coface country risk assessments",
            "Atradius country reports",
        ],
        "verification_procedure": [
            "1. FATF status check (regular review cycles; status changes plenary by plenary)",
            "2. Sanctions regime check for the jurisdiction",
            "3. Convertibility + capital-control check (relevant for currency-trade structuring)",
            "4. Sovereign credit rating check (default risk on government-counterparty contracts)",
        ],
    },

    "anti_bribery_corruption": {
        "tier": "UNIVERSAL",
        "definition": (
            "Assessment of bribery + corruption risk in the transaction — "
            "FCPA, UK Bribery Act, OECD Anti-Bribery Convention, French "
            "Sapin II, German anti-bribery law all create extraterritorial "
            "broker liability."
        ),
        "why_it_matters": (
            "FCPA / UK Bribery Act enforcement against brokers carries "
            "criminal exposure for individuals. 'Books and records' "
            "violations alone (no proof of actual bribe) can settle for "
            "$100M+ (e.g. Glencore 2022)."
        ),
        "evidence_sources": [
            "DOJ FCPA enforcement database",
            "SFO bribery enforcement (UK)",
            "Bribery Act 2010 specifically: failure-to-prevent strict liability for commercial organisations",
            "Country Corruption Perceptions Index (TI)",
        ],
        "verification_procedure": [
            "1. Counterparty's anti-bribery program documentation (policy + training records)",
            "2. Third-party intermediary screening (agents, distributors are highest-risk vector)",
            "3. Commission structures vetted — large unsplit commissions to single intermediary = red flag",
            "4. Government counterparty involvement → enhanced FCPA scrutiny on any payment touching foreign officials",
            "5. Country corruption-risk overlay on transaction structure",
        ],
        "common_failure_modes": [
            "Treating 'consulting fees' to a single agent as legitimate business expense without verification of actual services",
            "Accepting buyer-side instructions to route payments through specific intermediaries without scrutiny",
            "Ignoring 'commercial gift' patterns that exceed reasonable hospitality value",
        ],
    },

    "modern_slavery_human_rights": {
        "tier": "UNIVERSAL",
        "definition": (
            "Assessment of modern-slavery + human-rights risk in the "
            "counterparty's supply chain. UK Modern Slavery Act 2015 + EU "
            "Corporate Sustainability Due Diligence Directive 2024 + US "
            "UFLPA (Uyghur Forced Labor Prevention Act 2021)."
        ),
        "why_it_matters": (
            "UK MSA requires §54 statements; EU CSDDD imposes due diligence "
            "+ liability; UFLPA creates rebuttable presumption of forced "
            "labour for Xinjiang-linked goods. Defence + dual-use cleared "
            "goods can still be tainted at component level."
        ),
        "evidence_sources": [
            "Counterparty's UK MSA §54 statement (where required)",
            "US Department of Labor List of Goods Produced by Child or Forced Labor",
            "Sheffield Hallam University Forced Labour Reports (Xinjiang)",
            "Walk Free Global Slavery Index",
            "Industry-specific risk maps (cobalt DRC, cotton Xinjiang, palm oil SE Asia)",
        ],
    },

    "cyber_data_protection": {
        "tier": "UNIVERSAL",
        "definition": (
            "Assessment of cybersecurity posture + data-protection compliance "
            "of the counterparty — relevant where the transaction involves "
            "data exchange, integrated systems, or sensitive technology."
        ),
        "why_it_matters": (
            "Counterparty cyber compromise can be a vector to the broker. "
            "Defence-controlled data exchanged with non-CMMC-compliant US "
            "counterparties violates DFARS. EU GDPR + UK GDPR violations "
            "carry 4% global revenue fines."
        ),
        "evidence_sources": [
            "Cyber-rating services (BitSight, SecurityScorecard, RiskRecon — paid)",
            "Public breach disclosures",
            "Have I Been Pwned for known credential compromises",
            "Counterparty cyber-certification (ISO 27001, SOC 2, CMMC for US defence)",
        ],
    },

    "tax_fiscal_evasion": {
        "tier": "UNIVERSAL",
        "definition": (
            "Indicators of tax evasion / fiscal-fraud risk — particularly "
            "VAT carousel fraud, transfer-pricing abuse, base-erosion "
            "arrangements that touch the broker via the transaction."
        ),
        "why_it_matters": (
            "UK Criminal Finances Act 2017 + EU DAC6 + OECD BEPS create "
            "broker / intermediary liability for facilitating tax evasion. "
            "VAT carousel fraud exposure is endemic in cross-border commodity "
            "trade."
        ),
    },

    "environmental_esg": {
        "tier": "UNIVERSAL (commodity: REQUIRED)",
        "definition": (
            "Assessment of environmental + ESG risk — emissions, methane, "
            "carbon-pricing exposure, biodiversity impact, indigenous-rights "
            "conflicts, ESG-rating posture."
        ),
        "why_it_matters": (
            "EU CBAM phasing in by 2026-2034 imposes carbon pricing on "
            "imports; EU CSRD requires disclosure; methane regulations "
            "affect oil + gas counterparty viability. ESG-poor counterparty "
            "exposes broker to reputational + financing-pipeline risk."
        ),
    },

    # ──────────────────────────────────────────────────────────────────
    # DEFENCE-SPECIFIC DISCIPLINES
    # ──────────────────────────────────────────────────────────────────

    "end_use_verification": {
        "tier": "DEFENCE",
        "definition": (
            "Verification that the END USER of the controlled goods is who "
            "the export licence application says they are — typically the "
            "named armed forces / government agency / approved private end-"
            "user."
        ),
        "why_it_matters": (
            "End-use diversion is the single most common cause of UK SIEL / "
            "US ITAR enforcement actions. Goods reaching unauthorised end "
            "users (re-export, secondary sale, cross-border smuggling) "
            "creates broker liability under UK Export Control Order 2008 "
            "and US ITAR §126."
        ),
        "evidence_sources": [
            "End-User Certificate (EUC) — official document signed by buying-state authority",
            "End-Use Statement (US BIS-equivalent)",
            "Buyer-state Ministry of Defence verification via UK FCDO / US State Department channel",
            "Site verification (in-country audit by qualified third party)",
            "Stockholm International Peace Research Institute (SIPRI) Arms Transfers database for historical patterns",
        ],
        "verification_procedure": [
            "1. EUC signed by authorised signatory (verify signature pattern against published examples)",
            "2. End-user is the named entity in the licence application (no last-minute switches)",
            "3. End-use description specific (not vague 'security services')",
            "4. Re-export prohibition explicit + enforceable",
            "5. Post-shipment verification rights reserved (audit clause)",
        ],
    },

    "reexport_diversion_risk": {
        "tier": "DEFENCE",
        "definition": (
            "Assessment of risk that controlled goods will be re-exported / "
            "diverted to an unauthorised end-user post-delivery. Includes "
            "intermediate-country risk (transit hubs that historically "
            "serve as diversion points)."
        ),
        "why_it_matters": (
            "Diversion is the operational failure mode of end-use "
            "verification. Some destinations + intermediate countries have "
            "documented diversion patterns (UAE → Russia post-2022, "
            "Türkiye → Iran historically)."
        ),
    },

    "technology_classification": {
        "tier": "DEFENCE",
        "definition": (
            "Correct classification of technology under applicable export-"
            "control regimes — UK Strategic Export Control Lists (Categories "
            "A, B, C), US Commerce Control List ECCN, EU Dual-Use Annex I, "
            "Wassenaar List, Munitions List."
        ),
        "why_it_matters": (
            "Misclassification → wrong licence → unlicensed export → criminal "
            "exposure. ECCN + ML ratings are technical determinations "
            "requiring engineering review, not commercial classification."
        ),
        "evidence_sources": [
            "OEM-supplied classification (where available)",
            "BIS Commodity Classification Automated Tracking System (CCATS) ruling",
            "UK ECJU Control List Classification Service",
            "EU dual-use Annex I (current published version)",
            "Wassenaar Munitions List + Dual-Use List (current)",
        ],
    },

    "nato_stanag_compliance": {
        "tier": "DEFENCE",
        "definition": (
            "Verification that equipment claims of NATO STANAG compliance "
            "are independently certifiable — STANAG 2310 (small arms ammunition), "
            "STANAG 4569 (vehicle armour), STANAG 4671 (UAV airworthiness), "
            "STANAG 4609 (motion imagery), STANAG 5066 (HF data)."
        ),
        "why_it_matters": (
            "NATO procurement specifications cite STANAGs; non-compliance "
            "= exclusion. Self-certified STANAG claims need independent "
            "verification (usually via certified test laboratories)."
        ),
    },

    "defence_offset": {
        "tier": "DEFENCE",
        "definition": (
            "Assessment of offset / industrial-cooperation obligations "
            "imposed by buyer countries — direct (in-country production), "
            "indirect (non-related industrial investment), or technology-"
            "transfer offsets."
        ),
        "why_it_matters": (
            "Offset obligations can dwarf the contract value (some "
            "countries require 100%+ offset). Misjudging offset cost = "
            "loss-making contract. Offset programmes themselves carry "
            "FCPA / Bribery Act exposure."
        ),
    },

    # ──────────────────────────────────────────────────────────────────
    # COMMODITY-SPECIFIC DISCIPLINES
    # ──────────────────────────────────────────────────────────────────

    "price_cap_attestation": {
        "tier": "COMMODITY",
        "definition": (
            "For Russian-origin crude / refined products: complete "
            "attestation chain (Tier 1 / 2 / 3) demonstrating transaction "
            "is at or below G7+ coalition price cap."
        ),
        "why_it_matters": (
            "Service-provider safe harbour under price cap requires "
            "attestation. Trade above cap → no safe harbour → secondary-"
            "sanctions exposure for shipping / insurance / finance / "
            "broking."
        ),
        "verification_procedure": [
            "1. Russian-origin determination (cargo, blend, declared origin)",
            "2. Cargo loaded AFTER cap effective date (Dec 2022 crude / Feb 2023 products)",
            "3. Tier 1 attestation from charterer / cargo owner",
            "4. Tier 2 attestation from commodity trader / storage provider",
            "5. Tier 3 attestation from bank / insurer / shipowner",
            "6. Documentation retention 5 years per UK OFSI / US OFAC",
        ],
    },

    "vessel_tracking_ais": {
        "tier": "COMMODITY",
        "definition": (
            "AIS-based vessel tracking through full voyage to verify "
            "claimed loading + transit + discharge against declared cargo "
            "movement. Detect AIS-dark patterns indicating sanctions evasion "
            "or dark-fleet behaviour."
        ),
        "why_it_matters": (
            "AIS gaps near sanctioned-jurisdiction waters are the primary "
            "indicator of dark-fleet sanctions-evasion shipping. Vessel "
            "loading at claimed port verifiable against AIS history."
        ),
        "evidence_sources": [
            "MarineTraffic (paid for full history)",
            "VesselFinder",
            "Lloyd's List Intelligence (paid)",
            "Equasis (free, basic vessel data)",
            "United Against Nuclear Iran tankers tracker (free for Iranian dark fleet)",
            "TankerTrackers.com (subscription)",
        ],
    },

    "cargo_quality_specifications": {
        "tier": "COMMODITY",
        "definition": (
            "Verification that cargo quality (API gravity, sulphur content, "
            "TBP cuts, contaminants) matches contract specification + "
            "inspection certificate."
        ),
        "why_it_matters": (
            "Quality disputes are the #1 source of post-discharge claims. "
            "Off-spec cargo can void the transaction or force renegotiation "
            "at reduced price. Forged inspection certs frequently misstate "
            "quality."
        ),
        "evidence_sources": [
            "SGS / Saybolt / Intertek inspection certificate (verified directly with house)",
            "Independent shore-tank survey before / after loading",
            "Independent shore-tank survey at discharge",
            "Lab analysis of loaded sample",
        ],
    },

    "contractual_structure": {
        "tier": "COMMODITY",
        "definition": (
            "Assessment of Incoterms, payment instrument, demurrage terms, "
            "title-transfer point, dispute-resolution clause, force majeure."
        ),
    },

    "storage_inventory_verification": {
        "tier": "COMMODITY",
        "definition": (
            "Verification of cargo location + ownership in storage — for "
            "stored / staged cargo vs in-transit cargo."
        ),
        "evidence_sources": [
            "Tank-farm operator confirmation (independent contact)",
            "Storage receipt / warehouse warrant verified with terminal",
            "Genscape / Vortexa / Kayrros tank-farm-monitoring data (paid)",
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════
# ENTITY-TYPE → DISCIPLINE MAPPING
# ══════════════════════════════════════════════════════════════════════

ENTITY_TYPE_DISCIPLINES = {
    "defence_broker": [
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "ubo_chain", "source_of_funds",
        "litigation_history", "regulatory_enforcement", "operational_substance",
        "financial_soundness", "reputational_intelligence",
        "banking_verification", "jurisdiction_country_risk",
        "anti_bribery_corruption", "modern_slavery_human_rights",
        "cyber_data_protection",
        "end_use_verification", "reexport_diversion_risk",
        "technology_classification", "nato_stanag_compliance", "defence_offset",
    ],
    "defence_oem": [
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "ubo_chain", "litigation_history",
        "regulatory_enforcement", "operational_substance", "financial_soundness",
        "banking_verification", "jurisdiction_country_risk",
        "anti_bribery_corruption", "modern_slavery_human_rights",
        "cyber_data_protection",
        "technology_classification", "nato_stanag_compliance",
    ],
    "commodity_broker_oil_lng": [
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "ubo_chain", "source_of_funds", "source_of_wealth",
        "litigation_history", "regulatory_enforcement", "operational_substance",
        "financial_soundness", "reputational_intelligence",
        "banking_verification", "insurance_verification",
        "jurisdiction_country_risk",
        "anti_bribery_corruption", "modern_slavery_human_rights",
        "tax_fiscal_evasion", "environmental_esg",
        "price_cap_attestation", "vessel_tracking_ais",
        "cargo_quality_specifications", "contractual_structure",
        "storage_inventory_verification",
    ],
    "commodity_broker_crude": [
        # Same as oil_lng above (oil + crude DD overlap is ~95%)
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "ubo_chain", "source_of_funds", "source_of_wealth",
        "litigation_history", "regulatory_enforcement", "operational_substance",
        "financial_soundness", "reputational_intelligence",
        "banking_verification", "insurance_verification",
        "jurisdiction_country_risk",
        "anti_bribery_corruption", "modern_slavery_human_rights",
        "tax_fiscal_evasion", "environmental_esg",
        "price_cap_attestation", "vessel_tracking_ais",
        "cargo_quality_specifications", "contractual_structure",
    ],
    "commodity_trader_major": [
        # The Vitol/Glencore/Trafigura-class — UNIVERSAL + COMMODITY
        # but with FINANCIAL_SOUNDNESS heavily weighted (their bond + CDS data is rich)
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "ubo_chain",
        "litigation_history", "regulatory_enforcement", "financial_soundness",
        "banking_verification", "jurisdiction_country_risk",
        "anti_bribery_corruption", "environmental_esg",
        "price_cap_attestation", "vessel_tracking_ais",
    ],
    "individual_pep_check": [
        # Individual person check focused on PEP + adverse media depth
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "litigation_history", "regulatory_enforcement",
        "source_of_wealth", "reputational_intelligence",
        "anti_bribery_corruption", "jurisdiction_country_risk",
    ],
    "individual_general": [
        # Lighter individual screen
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "litigation_history",
    ],
    "financial_counterparty": [
        # Bank, insurer, fund, financier
        "identity_verification", "sanctions_screening", "pep_screening",
        "adverse_media", "ubo_chain",
        "litigation_history", "regulatory_enforcement", "financial_soundness",
        "banking_verification", "jurisdiction_country_risk",
        "anti_bribery_corruption", "cyber_data_protection",
        "tax_fiscal_evasion",
    ],
}


# ══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════

def required_disciplines(entity_type: str) -> list[str]:
    """Return the ordered list of DD disciplines required for a given
    entity type. Raises if entity_type unknown."""
    if entity_type not in ENTITY_TYPE_DISCIPLINES:
        raise ValueError(
            f"Unknown entity_type: {entity_type}. "
            f"Known: {sorted(ENTITY_TYPE_DISCIPLINES.keys())}"
        )
    return list(ENTITY_TYPE_DISCIPLINES[entity_type])


def discipline_definition(discipline_id: str) -> dict[str, Any]:
    """Return the full definition of a discipline. Raises if unknown."""
    if discipline_id not in DD_DISCIPLINES:
        raise ValueError(f"Unknown discipline: {discipline_id}")
    return dict(DD_DISCIPLINES[discipline_id])


def discipline_coverage_check(
    report_disciplines_covered: list[str],
    entity_type: str,
) -> dict[str, Any]:
    """Grade a DD report against its required disciplines.

    Args:
      report_disciplines_covered: list of discipline_ids the report
        actually addressed (caller responsibility to extract this from
        the report — typically by checking which sections are populated).
      entity_type: which entity_type the target is (drives required list).

    Returns:
      {
        required: [...],
        covered: [...],
        missing: [...],
        partial: [...],     # in current version always empty (binary cov)
        coverage_pct: float,
        gate_passes: bool,  # True iff ALL required are covered
      }

    Use this at the END of dd_orchestrator runs to surface discipline
    gaps explicitly. A buyer asking 'did you check X?' deserves a yes
    or a documented why-not, not silence.
    """
    required = set(required_disciplines(entity_type))
    covered_set = set(report_disciplines_covered)
    actual_covered = required & covered_set
    missing = required - covered_set
    coverage_pct = round(100 * len(actual_covered) / len(required), 1) if required else 0.0
    return {
        "entity_type": entity_type,
        "required": sorted(required),
        "covered": sorted(actual_covered),
        "missing": sorted(missing),
        "partial": [],
        "coverage_pct": coverage_pct,
        "gate_passes": len(missing) == 0,
    }


def adverse_media_query_templates(
    entity_name: str,
    director_names: list[str] | None = None,
    ubo_names: list[str] | None = None,
    sectors: list[str] | None = None,
    years_back: int = 10,
) -> list[dict[str, Any]]:
    """Generate STRUCTURED query templates for adverse-media deep search.

    Per operator priority: deep search not surface. This function returns
    a list of (source_class, query, target_url_pattern) templates the
    caller (researcher / deep_research / web_search) executes. Each
    template targets a specific source class — court records, regulatory
    enforcement, leaks, journalism, news archive — rather than a single
    'search the entity name' approach.

    The caller (e.g. researcher.deep_research) executes each template and
    aggregates findings. Findings get tier-classified per Clause 17
    + Clause 15 citation discipline.
    """
    if not entity_name:
        return []

    director_names = director_names or []
    ubo_names = ubo_names or []
    sectors = sectors or []
    all_names = [entity_name] + list(director_names) + list(ubo_names)

    templates: list[dict[str, Any]] = []

    # ── Court records + litigation ─────────────────────────────────────
    for name in all_names:
        templates.append({
            "source_class": "legal_court_us_federal",
            "query": f'"{name}" site:courtlistener.com',
            "search_backend": "google + ddg",
            "purpose": "US federal court litigation involving this party",
        })
        templates.append({
            "source_class": "legal_court_uk",
            "query": f'"{name}" site:bailii.org',
            "search_backend": "google + ddg",
            "purpose": "UK + Ireland case law involving this party",
        })
        templates.append({
            "source_class": "legal_arbitration",
            "query": f'"{name}" arbitration award OR ICSID OR LCIA OR ICC',
            "search_backend": "google + bing news + crossref",
            "purpose": "International commercial arbitration involvement",
        })

    # ── Regulatory enforcement ─────────────────────────────────────────
    for name in all_names:
        templates.append({
            "source_class": "regulatory_us_ofac",
            "query": f'"{name}" site:treasury.gov',
            "search_backend": "google",
            "purpose": "OFAC enforcement actions / settlement / designation",
        })
        templates.append({
            "source_class": "regulatory_us_doj",
            "query": f'"{name}" site:justice.gov',
            "search_backend": "google",
            "purpose": "DOJ press releases / indictments / settlements",
        })
        templates.append({
            "source_class": "regulatory_us_sec",
            "query": f'"{name}" site:sec.gov litigation OR enforcement',
            "search_backend": "google",
            "purpose": "SEC enforcement actions",
        })
        templates.append({
            "source_class": "regulatory_uk_fca",
            "query": f'"{name}" site:fca.org.uk',
            "search_backend": "google",
            "purpose": "FCA Final Notices",
        })
        templates.append({
            "source_class": "regulatory_uk_ofsi",
            "query": f'"{name}" site:gov.uk OFSI enforcement',
            "search_backend": "google",
            "purpose": "OFSI enforcement",
        })
        templates.append({
            "source_class": "regulatory_uk_sfo",
            "query": f'"{name}" site:sfo.gov.uk',
            "search_backend": "google",
            "purpose": "Serious Fraud Office investigations",
        })

    # ── Investigative journalism + leaks ───────────────────────────────
    for name in all_names:
        templates.append({
            "source_class": "leak_database_icij",
            "query": f'"{name}" site:offshoreleaks.icij.org',
            "search_backend": "google",
            "purpose": "ICIJ Pandora/Panama/Paradise/OpenLux Papers",
        })
        templates.append({
            "source_class": "investigative_journalism_occrp",
            "query": f'"{name}" site:occrp.org',
            "search_backend": "google",
            "purpose": "Organized Crime and Corruption Reporting Project",
        })
        templates.append({
            "source_class": "investigative_journalism_bellingcat",
            "query": f'"{name}" site:bellingcat.com',
            "search_backend": "google",
            "purpose": "Bellingcat investigations",
        })
        templates.append({
            "source_class": "investigative_journalism_majors",
            "query": f'"{name}" (site:reuters.com OR site:ft.com OR site:bloomberg.com OR site:wsj.com) (investigation OR fraud OR allegations)',
            "search_backend": "google + bing news",
            "purpose": "Tier-1 financial press investigations",
        })

    # ── News archive depth ─────────────────────────────────────────────
    for name in all_names:
        templates.append({
            "source_class": "news_archive_general",
            "query": f'"{name}" (fraud OR allegations OR lawsuit OR investigation OR settlement OR sanctioned OR indicted)',
            "search_backend": "google news + bing news + duckduckgo",
            "purpose": f"General adverse-news scan, last {years_back} years",
        })
        templates.append({
            "source_class": "news_archive_wayback",
            "query": f'"{name}" wayback OR archived',
            "search_backend": "wayback machine search",
            "purpose": "Historical web snapshots — find content that's been removed",
        })

    # ── Sector-specific trade press ────────────────────────────────────
    for sector in sectors:
        sl = sector.lower()
        if "defence" in sl or "defense" in sl or "security" in sl:
            for name in all_names:
                templates.append({
                    "source_class": "trade_press_defence",
                    "query": f'"{name}" (site:defensenews.com OR site:janes.com OR site:shephardmedia.com OR site:breakingdefense.com)',
                    "search_backend": "google + bing news",
                    "purpose": "Defence trade press archive search",
                })
        if "oil" in sl or "lng" in sl or "crude" in sl or "energy" in sl:
            for name in all_names:
                templates.append({
                    "source_class": "trade_press_energy",
                    "query": f'"{name}" (site:argusmedia.com OR site:spglobal.com OR site:tradewinds.com OR site:lloydslist.com OR site:upstreamonline.com)',
                    "search_backend": "google + bing news",
                    "purpose": "Energy / commodity / shipping trade press archive search",
                })

    # ── Multi-language fan-out for non-English entities ────────────────
    # Caller (researcher) may add language-specific variants based on
    # detected jurisdiction. This template list is English-baseline.

    return templates


def discipline_summary_for_chat(entity_type: str = "defence_broker") -> str:
    """Render a chat-friendly summary of what disciplines ARIA will run
    for a given entity type. Use to answer 'what do you check?' questions
    with a concrete, bounded answer rather than vague reassurance."""
    if entity_type not in ENTITY_TYPE_DISCIPLINES:
        return f"Unknown entity type: {entity_type}. Supported: {', '.join(sorted(ENTITY_TYPE_DISCIPLINES.keys()))}"

    disciplines = ENTITY_TYPE_DISCIPLINES[entity_type]
    universal = [d for d in disciplines if DD_DISCIPLINES.get(d, {}).get("tier") == "UNIVERSAL"]
    defence = [d for d in disciplines if DD_DISCIPLINES.get(d, {}).get("tier") == "DEFENCE"]
    commodity = [d for d in disciplines if DD_DISCIPLINES.get(d, {}).get("tier") == "COMMODITY"]
    universal_with_pep = [d for d in disciplines if DD_DISCIPLINES.get(d, {}).get("tier") == "UNIVERSAL (PEP-linked: REQUIRED)"]
    universal_with_commodity = [d for d in disciplines if DD_DISCIPLINES.get(d, {}).get("tier") in (
        "UNIVERSAL (commodity / shipping: REQUIRED)", "UNIVERSAL (commodity: REQUIRED)")]

    lines = [f"For a `{entity_type}` target, ARIA runs the following disciplines:"]
    lines.append("")
    if universal:
        lines.append(f"**UNIVERSAL ({len(universal)}):**")
        for d in universal:
            lines.append(f"  - {d}: {DD_DISCIPLINES[d]['definition'][:120]}...")
        lines.append("")
    if universal_with_pep:
        lines.append(f"**UNIVERSAL (PEP-linked required, {len(universal_with_pep)}):**")
        for d in universal_with_pep:
            lines.append(f"  - {d}")
        lines.append("")
    if universal_with_commodity:
        lines.append(f"**UNIVERSAL (commodity required, {len(universal_with_commodity)}):**")
        for d in universal_with_commodity:
            lines.append(f"  - {d}")
        lines.append("")
    if defence:
        lines.append(f"**DEFENCE-SPECIFIC ({len(defence)}):**")
        for d in defence:
            lines.append(f"  - {d}: {DD_DISCIPLINES[d]['definition'][:120]}...")
        lines.append("")
    if commodity:
        lines.append(f"**COMMODITY-SPECIFIC ({len(commodity)}):**")
        for d in commodity:
            lines.append(f"  - {d}: {DD_DISCIPLINES[d]['definition'][:120]}...")
        lines.append("")
    lines.append(f"Total: {len(disciplines)} disciplines.")
    lines.append("")
    lines.append(
        "Each discipline has a defined verification procedure + evidence sources + "
        "common failure modes — see `discipline_definition(<id>)` for the full "
        "specification. Coverage of every required discipline is checked at the "
        "end of the DD run via `discipline_coverage_check()` — gaps are surfaced "
        "explicitly rather than hidden."
    )
    return "\n".join(lines)
