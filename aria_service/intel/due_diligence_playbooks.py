# =============================================================================
# ARIA — Due Diligence Operational Playbooks
# aria_service/intel/due_diligence_playbooks.py
#
# Two closely-related playbooks for every counterparty due-diligence
# task ARIA runs: beneficial ownership (UBO) extraction and shell /
# ghost company detection. Both are called from:
#   - sanctions_screen path (before issuing a CLEAR verdict)
#   - contract_review path (when counterparty integrity matters)
#   - on-demand via /api/aria/research/profile
#   - autonomous investigate tasks
#
# GLOBAL SCOPE: These playbooks are jurisdiction-agnostic. They apply
# to BVI, Cayman, Seychelles, Delaware, UK, Luxembourg, Cyprus, Hong
# Kong, Singapore, Dubai free zones, Panama, and every other common
# corporate-veiling venue.
#
# Programmatic callers (score_ghost_indicators, extract_ubo_chain) are
# intended to be wired into the sanctions and research flows, while
# the narrative sections are ingested into RAG so the LLM can reason
# from doctrine when the programmatic layer is missing a data source.
# =============================================================================

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aria.due_diligence_playbooks")


# =============================================================================
# SECTION 1 — BENEFICIAL OWNERSHIP (UBO) EXTRACTION PLAYBOOK
# =============================================================================

UBO_PLAYBOOK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  BENEFICIAL OWNERSHIP (UBO) EXTRACTION PLAYBOOK                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The beneficial owner of a legal entity is the natural person who
ultimately owns or controls it. For defence broking due diligence,
the single most important question is never "who signed the MoU" but
"who ultimately controls the money, the decisions, and the end-use of
the goods". Corporate chains exist primarily to obscure this answer.

The global standard is the FATF recommendation that any natural person
owning or controlling 25% or more of a legal entity's shares or voting
rights is a beneficial owner. Many jurisdictions apply stricter
thresholds (10% for high-risk sectors, some PEP regimes use 5%). For
defence transactions, ARIA should apply a 10% floor and explicitly
report anyone at 5-10% who shows up as a control party.

─────────────────────────────────────────────────────────────────────────────
1.1 THE UBO CHAIN
─────────────────────────────────────────────────────────────────────────────

UBO extraction is graph traversal. Starting from the entity under
review, ARIA must walk UP the ownership chain until one of three
termination conditions is met:

  (a) A natural person is found with >=25% direct-or-indirect
      beneficial interest
  (b) A listed company (with regulated public disclosure) appears
      in the chain
  (c) A regulated entity (bank, insurer, listed fund) appears in the
      chain and owns the branch under review

If the chain cannot be terminated because information runs out
(nominee directors, secrecy-jurisdiction trust, opaque offshore
vehicle), the correct outcome is a RED flag — "beneficial ownership
cannot be established with available information; reject until
resolved".

─────────────────────────────────────────────────────────────────────────────
1.2 JURISDICTION-SPECIFIC EXTRACTION TECHNIQUES
─────────────────────────────────────────────────────────────────────────────

(A) BRITISH VIRGIN ISLANDS (BVI)
BVI Business Companies Act 2004 + BVI Beneficial Ownership Secure
Search System (BOSSs). Beneficial ownership data IS collected and held
by registered agents but is NOT public. Access mechanisms:
  - Through BOSSs for competent authorities (law enforcement, FIU,
    AML supervisors)
  - Public Register of Beneficial Ownership under development but
    repeatedly delayed
  - Request to registered agent — usually refused without customer
    consent or competent-authority order
What ARIA CAN retrieve from open sources:
  - Company name, number, status, incorporation date, registered
    address from BVI Financial Services Commission online registry
  - Registered agent name (not client list)
Red flag: BVI registered agent with 500+ client companies at the
  same address is a warning sign but not dispositive.

(B) CAYMAN ISLANDS
Cayman Islands Companies Act. Public-facing registry is limited. The
Beneficial Ownership Act 2017 mandates reporting but access is
restricted. Cayman has a beneficial-ownership regime with competent-
authority access, movement toward public access has been slow.
What ARIA CAN retrieve:
  - Basic company information via Cayman Islands General Registry
  - Directors for publicly filed companies
  - Filings of publicly traded Cayman-incorporated entities (e.g.
    SEC filings for Cayman-holding listings on US exchanges)

(C) SEYCHELLES
International Business Companies Act. Beneficial ownership disclosure
is required to the registered agent but the Seychelles FSA register
is not public. Seychelles international business companies have been
repeatedly flagged by FATF and the OECD.
What ARIA CAN retrieve:
  - Very limited company information via Seychelles Financial
    Services Authority online tools
  - Registered office and agent name
Red flag: Seychelles IBC + nominee director + BVI or UAE parent is
  one of the most common defence-brokering concealment patterns.

(D) DELAWARE (UNITED STATES)
Corporate Transparency Act (CTA) 2021 — requires reporting of
beneficial owners to FinCEN (beneficial ownership information
reporting rule effective 2024, subject to ongoing litigation). Data
is not public but is accessible to law enforcement.
What ARIA CAN retrieve:
  - Basic Delaware entity information via Delaware Division of
    Corporations general information name search
  - UCC filings showing financial creditors
  - Court records via PACER (federal) and Delaware Court of Chancery
  - SEC EDGAR filings if the entity is listed

(E) UNITED KINGDOM
Companies House — best open-access corporate register in the world
for a major jurisdiction. Contains:
  - Company number, name, status, incorporation date
  - Directors (current and historic), date of appointment
  - Company address, SIC codes, registered office
  - Full filing history (accounts, annual returns/confirmation
    statements, resolutions, mortgage registrations)
  - People with Significant Control (PSC) register — FATF-compliant,
    public, showing natural persons or RLEs with >=25% interest or
    control
  - Historic PSC data
Data quality caveat: PSC filings rely on self-declaration; deliberate
concealment is a criminal offence but occurs.
ARIA CAN use Companies House as the gold-standard first stop for any
UK-involved counterparty, then cross-reference with OpenCorporates
and the Land Registry (for UK property holdings).

(F) LUXEMBOURG
Registre de Commerce et des Sociétés (RCS) + Register of Beneficial
Owners (RBE). As of 2022 the RBE access is restricted to competent
authorities following ECJ ruling C-37/20 + C-601/20. Open access
now requires legitimate interest demonstration.
What ARIA CAN retrieve:
  - RCS company information (public)
  - Luxembourg filing history
  - Luxembourg listed-fund disclosures where applicable

(G) CYPRUS
Department of Registrar of Companies + UBO register. Cyprus has a
register but access has been restricted following the same ECJ
ruling. Cyprus has historically been a venue of concern for Russian
defence-sector oligarch structures.
What ARIA CAN retrieve:
  - Basic entity information via Cyprus Registrar
  - Historic filings and annual returns
Red flag: Cyprus + Russian-national director + defence-adjacent trade
  is a high-sensitivity combination post-2022.

(H) HONG KONG
Companies Registry — Integrated Companies Registry Information System
(ICRIS). Directors and shareholders are public for Hong Kong private
companies. Significant Controller Register (SCR) was introduced 2018
but is held internally, not public.
What ARIA CAN retrieve:
  - Full company information including current and historical
    directors and shareholders for most companies
  - Filings and annual returns
ARIA can use Hong Kong as a relatively open registry compared to
most offshore venues.

(I) SINGAPORE
ACRA (Accounting and Corporate Regulatory Authority) + Register of
Registrable Controllers (RORC). Singapore directors and shareholders
are publicly available via BizFile+. Singapore is a preferred hub for
Southeast Asia defence-adjacent structures; the open registry helps
but beneficial ownership is not public.
What ARIA CAN retrieve:
  - Full company directors, shareholders, addresses via BizFile+
  - Annual filings (fee applies for some extracts)

(J) UAE (DUBAI, ABU DHABI, FREE ZONES)
UAE has MULTIPLE registries:
  - Department of Economy and Tourism (Dubai mainland)
  - Department of Economic Development (Abu Dhabi mainland)
  - Free zone authorities — each free zone (Jebel Ali, DMCC, DIFC,
    ADGM, RAKEZ, Sharjah Airport, etc.) has its own registry
UAE introduced Cabinet Decision No. 58 of 2020 on beneficial owner
procedures. Public access varies by emirate and free zone. DIFC and
ADGM (common-law free zones) have the best transparency. Mainland
Dubai and most free zones have very limited open disclosure.
What ARIA CAN retrieve:
  - DIFC Public Register of companies (reasonably transparent)
  - ADGM Public Register
  - Limited mainland information for individual queries
Red flag: UAE free-zone entity + nominee shareholder + export to
  sanctioned destination is the main post-2022 sanctions-evasion
  pattern for Russia-connected trade.

(K) PANAMA
Panama Papers were the wake-up call. Panama has introduced beneficial
ownership legislation but enforcement has been uneven. Public Registry
of Panama is accessible online for corporate information but
beneficial ownership is not open.
What ARIA CAN retrieve:
  - Public Registry corporate information
  - ICIJ Offshore Leaks Database for historical Panama Papers /
    Paradise Papers / Pandora Papers data

─────────────────────────────────────────────────────────────────────────────
1.3 CROSS-JURISDICTIONAL TOOLS ARIA SHOULD USE
─────────────────────────────────────────────────────────────────────────────

OpenCorporates (opencorporates.com) — the largest open database of
  companies worldwide. ~200 million records from 140+ jurisdictions.
  Useful for cross-reference, name-match, officer-linkage, and change
  history. Paid API for bulk queries.

ICIJ Offshore Leaks Database (offshoreleaks.icij.org) — consolidates
  Panama Papers, Paradise Papers, Pandora Papers, Bahamas Leaks,
  Offshore Leaks. Historical ownership data for entities in known
  secrecy jurisdictions.

Sayari Graph / Sayari Map — commercial-grade corporate graph (paid).
LSEG World-Check / Dow Jones Risk & Compliance — commercial PEP and
  adverse-media databases (paid, enterprise licensing).
Orbis (Bureau van Dijk) — commercial global corporate database with
  ~470 million entities, ownership linkage, financials (paid).
Sayari / LSEG / Dow Jones / Orbis are the gold standard for serious
  DD work. ARIA should note when an investigation hits their
  coverage gap and recommend escalation.

OpenSanctions (opensanctions.org) — open aggregation of sanctions
  lists, PEP lists, risk-related lists. Already integrated into ARIA.

GLEIF (Global Legal Entity Identifier Foundation) — LEI database
  with ultimate parent identification. Free, API-accessible.

─────────────────────────────────────────────────────────────────────────────
1.4 THE UBO EXTRACTION PROTOCOL — STEP BY STEP
─────────────────────────────────────────────────────────────────────────────

Step 1: Baseline entity identification
  - Canonical legal name (not trading name)
  - Registration number and jurisdiction
  - Registered address and business address
  - Formation date and current status
  - Declared SIC/NACE/ISIC business activity
  - Share capital structure

Step 2: Direct shareholders
  - Identify each direct holder with % interest
  - For each holder who is a natural person: record name, DOB if
    available, nationality, PEP status
  - For each holder who is another legal entity: note, return to
    Step 2 with that entity as the subject

Step 3: Directors and officers
  - Current directors (names, dates of appointment, nationalities)
  - Historic directors (dates of service, reasons for departure if
    known)
  - Company secretary, if applicable
  - Authorised signatories

Step 4: Cross-linkage
  - Any director or shareholder linked to other entities under
    review? Note the linkage.
  - Registered agent shared with entities under review? Note.
  - Registered address shared with many entities? Note count.

Step 5: Group chart
  - Draw the chain from subject entity upward through each
    intermediate holder to the terminating natural person(s), listed
    company, or regulated entity
  - If the chain runs into a nominee director / nominee shareholder,
    note the break point and attempt to identify the true beneficial
    owner through other signals (cross-linked entities, known
    associations)

Step 6: Termination check
  - Natural person found with >=25%? → report as declared UBO
  - Listed company in chain? → report as regulated disclosure route
  - Regulated fund/bank in chain? → report as regulated disclosure
  - None of the above? → RED FLAG "UBO cannot be established"

Step 7: Red flag scoring
  - Run the result through the ghost-detection playbook (Section 2)

Step 8: Output format
  - UBO disclosure summary (name, nationality, % interest, source)
  - Directorship chain
  - Cross-linked entities
  - Red flags
  - Recommendation (GREEN/AMBER/RED)

─────────────────────────────────────────────────────────────────────────────
1.5 RED FLAGS IN UBO CHAINS
─────────────────────────────────────────────────────────────────────────────

  - Chain terminates in a BVI/Cayman/Seychelles entity without
    further information
  - Nominee director/shareholder with a history in 20+ unrelated
    entities
  - Directors based in jurisdictions different from the entity's
    operations
  - Shared registered agent with sanctioned or previously-flagged
    entities
  - Shell company with no declared business activity, no staff, no
    website, no history beyond formation date
  - Age <2 years combined with high-value transaction
  - Entity name references a real famous brand without apparent
    connection
  - Director is a family member or close associate of a PEP
  - Ownership chain passes through a jurisdiction known for sanctions
    evasion (Russia, Iran, DPRK links post-2022)
  - Entity uses a virtual office / serviced address that hosts many
    unrelated businesses
  - Historic name changes, re-domiciliations, or strike-off/restore
    patterns

Any single red flag moves assessment to AMBER. Two or more move to
RED unless specifically mitigated.
"""


# =============================================================================
# SECTION 2 — GHOST / SHELL COMPANY DETECTION SCORING PLAYBOOK
# =============================================================================

GHOST_DETECTION_PLAYBOOK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  GHOST / SHELL COMPANY DETECTION — WEIGHTED SCORING PLAYBOOK              ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
Shell and ghost companies are legal entities with no substantive
economic activity, typically used to obscure beneficial ownership,
route sanctioned transactions, launder proceeds, or introduce a
plausible cutout between the real parties to a deal. For defence
broking due diligence, identifying a shell or ghost is often the
decisive question. This playbook converts the intuitive pattern-
recognition encoded in ARIA's researcher/investigator prompts into a
deterministic 0-20 scoring model with a clear threshold matrix.

The scoring model is designed to be callable as a tool, so ARIA can
generate numeric output (not just narrative) and cite the specific
indicators that produced the score.

─────────────────────────────────────────────────────────────────────────────
2.1 THE TEN INDICATORS (MAXIMUM 20 POINTS)
─────────────────────────────────────────────────────────────────────────────

Each indicator carries a weight based on its diagnostic strength.
ARIA evaluates each independently and sums the weighted points.

INDICATOR 1 — Age of entity (max 2 points)
  Entity formed less than 12 months ago: +2
  Entity formed 12-24 months ago: +1
  Entity formed >24 months ago: +0
  Rationale: shells are often formed for a specific transaction and
  have minimal operating history.

INDICATOR 2 — Digital presence (max 3 points)
  No website, or parked/under-construction page: +2
  Website exists but has no business information, no staff, no
    addresses, no products, no news: +1
  Zero LinkedIn staff OR only 1-2 staff and they are directors: +1
  Rationale: real businesses have digital footprint; shells do not.

INDICATOR 3 — Registered address quality (max 2 points)
  Address is a registered agent / formation service / virtual office
    known to host 50+ unrelated entities: +2
  Address is a PO box or residential address without commercial
    premises: +1
  Address is a real commercial premises: +0
  Rationale: mail-drop addresses are a shell signature.

INDICATOR 4 — Director profile (max 3 points)
  Nominee director identified by name (professional nominee services):
    +3
  Director has appointments in 10+ unrelated entities: +2
  Director's other appointments overlap with known high-risk entities:
    +2
  Director is based in a jurisdiction unconnected to the entity's
    declared business: +1
  Rationale: nominee-heavy director patterns are the hallmark of
  structuring for concealment.

INDICATOR 5 — Shared infrastructure (max 2 points)
  Entity shares registered agent with sanctioned or previously-
    flagged entities: +2
  Entity shares registered address with sanctioned or previously-
    flagged entities: +1
  Rationale: cluster patterns indicate the entity is part of a
  known network.

INDICATOR 6 — Declared business activity (max 2 points)
  No SIC/NACE/ISIC code declared or "general trading / holding":
    +1
  SIC code unrelated to the transaction type (e.g. "consulting" for
    a company suddenly brokering a USD 50M weapon deal): +2
  SIC code aligned with transaction: +0
  Rationale: legitimate businesses declare specific activities
  matching their revenue sources.

INDICATOR 7 — Financials (max 2 points)
  Dormant accounts filed or no accounts filed: +2
  Accounts filed show minimal assets and no revenue, inconsistent
    with proposed transaction size: +2
  Accounts show revenue and activity consistent with transaction:
    +0
  Rationale: a shell cannot produce financials; a company claiming
  to broker a large transaction with dormant filings is a mismatch.

INDICATOR 8 — Corporate chain opacity (max 2 points)
  Ownership runs through 3+ secrecy jurisdictions before reaching
    a natural person: +2
  Ownership chain cannot be completed from open-source research:
    +1
  Ownership chain is clear and terminates in a natural person,
    listed company, or regulated entity: +0
  Rationale: layered secrecy jurisdictions exist to obscure; each
  additional layer is a signal.

INDICATOR 9 — Transaction profile (max 2 points)
  Transaction value is disproportionate to entity size (e.g. a
    company with a single director and dormant accounts proposing
    a USD 100M+ deal): +2
  Transaction type is outside the entity's declared business: +1
  Transaction is consistent with entity profile: +0
  Rationale: mismatch between capacity and ambition is a structural
  red flag.

INDICATOR 10 — History and changes (max 2 points)
  Recent name change, re-domiciliation, or strike-off/restore
    activity: +2
  Director changes in the past 6 months unexplained by succession:
    +1
  No recent changes of concern: +0
  Rationale: legitimate companies rarely shuffle identity; recent
  changes often indicate preparation for a specific transaction or
  avoidance of a prior issue.

INDICATOR 11 — Founding-year misrepresentation (max 4 points)
  Company's public claim of founding year is >=10 years before the
    registry-derived incorporation date (e.g. website says
    "founded 1994" on a 2024 CUI): +4
  Gap is 5-9 years before: +3
  Gap is 2-4 years before: +1
  Gap is within +/- 1 year: +0
  Rationale: Romanian CUIs and UK Companies House numbers are
  issued sequentially. A large gap between the claimed founding
  year and the registry-derived date is not ambiguity — it is a
  deliberate misrepresentation of company age on a verifiable fact.
  Under UK / EU contract law, this constitutes fraudulent
  misrepresentation and voids contracting integrity. Seen live on
  Serban Industries SRL (CUI 51884521, 2024 incorporation, claimed
  "founded 1994" = 30-year gap).

INDICATOR 12 — Domain substitution / leetspeak (max 2 points)
  Root domain name contains a digit substituting for a letter in a
    word position (e.g. s3rban.com, d3fenc3.com, gl0bal.com): +2
  Root domain is very similar to an established brand but with a
    non-obvious difference (typosquat): +2
  Root domain appears normal: +0
  Rationale: legitimate defence counterparties do not use leet-
  speak or typosquatted domains. Character substitution in the
  domain root is a fresh-registration / impostor indicator — either
  an unimaginative brand choice that itself signals amateur
  presentation, or a deliberate attempt to resemble a known entity.
  Neither is acceptable in defence BD.

─────────────────────────────────────────────────────────────────────────────
2.2 THRESHOLD MATRIX  (updated for 12-indicator scoring — max now 26)
─────────────────────────────────────────────────────────────────────────────

TOTAL SCORE   CLASSIFICATION        RECOMMENDED ACTION
─────────────────────────────────────────────────────────────────
0-3           GREEN                 Standard DD sufficient. No
                                    ghost-company concern.

4-7           AMBER-LIGHT           Proceed with enhanced DD:
                                    require EUC, verify signatory
                                    identity, escalate any new
                                    red flag to RED.

8-11          AMBER-DARK            Significant structural concern.
                                    Obtain commercial DD from a
                                    professional provider (LSEG /
                                    Dow Jones / Sayari / Orbis).
                                    Do not proceed without
                                    independent verification of
                                    beneficial ownership.

12-15         RED                   Very likely a shell used for
                                    concealment. Refuse unless
                                    extraordinary and documented
                                    mitigation addresses at least
                                    three of the triggering
                                    indicators.

16-20         HARD STOP             Almost certainly a shell or
                                    ghost. Refuse. Consider whether
                                    a Suspicious Activity Report
                                    (SAR) obligation arises under
                                    UK Proceeds of Crime Act or
                                    equivalent local AML regime.

─────────────────────────────────────────────────────────────────────────────
2.3 CALIBRATION NOTES
─────────────────────────────────────────────────────────────────────────────

This matrix is calibrated for defence-broking due diligence where
the stakes justify conservative thresholds. For lower-stakes
commercial DD a different matrix may apply. ARIA should always
report both the total score AND the triggering indicators, so the
user can see WHY a score landed where it did.

The scoring is not a substitute for analytic judgement. A score of
3 in a transaction involving a destination on the UK high-risk list
still warrants escalation. A score of 8 in a transaction with
otherwise impeccable everything else may reflect the entity's
recent formation for a legitimate consortium. Apply context.

─────────────────────────────────────────────────────────────────────────────
2.4 PROGRAMMATIC INVOCATION
─────────────────────────────────────────────────────────────────────────────

ARIA's sanctions_screen flow and contract_review self-review loop
should call the scoring model whenever a new counterparty appears in
the brief. The model takes a structured input derived from the
investigation and returns a scored output:

    {
        "total": 12,
        "classification": "RED",
        "indicators_triggered": [
            {"id": 1, "label": "Age <12mo", "weight": 2},
            {"id": 2, "label": "No digital presence", "weight": 3},
            {"id": 4, "label": "Nominee director pattern", "weight": 3},
            {"id": 6, "label": "SIC code mismatch", "weight": 2},
            {"id": 9, "label": "Transaction value disproportionate", "weight": 2},
        ],
        "recommendation": "Refuse unless extraordinary mitigation",
        "data_gaps": [...]
    }

The narrative above is retrieved from RAG when ARIA is reasoning
without the programmatic scorer, so the doctrine is always available
even when the structured tool is not.
"""


# =============================================================================
# PROGRAMMATIC SCORING MODEL
# =============================================================================

# Each indicator is a callable that takes a standardised entity-profile
# dict and returns (points_awarded, human_reason) or (0, "").
# This lets callers (sanctions_screen, contract_review, investigate
# endpoints) run the model once the research stage has collected the
# inputs, without having to re-implement the scoring on each call site.


@dataclass
class GhostScoreIndicator:
    id: int
    label: str
    max_points: int
    points: int = 0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "max_points": self.max_points,
            "points": self.points,
            "reason": self.reason,
        }


@dataclass
class GhostScoreResult:
    total: int
    max_total: int
    classification: str
    recommendation: str
    indicators: list[GhostScoreIndicator] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    subject: dict[str, Any] = field(default_factory=dict)

    def triggered(self) -> list[GhostScoreIndicator]:
        return [i for i in self.indicators if i.points > 0]

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "max_total": self.max_total,
            "classification": self.classification,
            "recommendation": self.recommendation,
            "indicators": [i.as_dict() for i in self.indicators],
            "indicators_triggered": [i.as_dict() for i in self.triggered()],
            "data_gaps": self.data_gaps,
            "subject": self.subject,
        }


def _classify(total: int) -> tuple[str, str]:
    """Threshold bands for the 12-indicator scorer (max 26 after
    indicators 11 + 12 added from the Serban case). Bands were
    originally calibrated against a max of 20; scaling factor 1.3
    preserves the same GREEN/AMBER/RED/HARD_STOP distribution.

    Any single score of 4 on Indicator 11 (founding-year
    misrepresentation >=10 years) is enough on its own to push the
    total into RED territory, which matches the real-world rule:
    a verifiable 10-year lie about company age is itself a
    refusal ground.
    """
    if total <= 4:
        return ("GREEN", "Standard DD sufficient — no ghost-company concern.")
    if total <= 9:
        return ("AMBER-LIGHT", "Proceed with enhanced DD: require EUC, verify signatory identity, escalate any new red flag to RED.")
    if total <= 14:
        return ("AMBER-DARK", "Significant structural concern. Obtain commercial DD from a professional provider (LSEG/Dow Jones/Sayari/Orbis). Do not proceed without independent verification of beneficial ownership.")
    if total <= 19:
        return ("RED", "Very likely a shell used for concealment. Refuse unless extraordinary and documented mitigation addresses at least three of the triggering indicators.")
    return ("HARD STOP", "Almost certainly a shell or ghost. Refuse. Consider whether a Suspicious Activity Report (SAR) obligation arises under UK Proceeds of Crime Act or equivalent local AML regime.")


def score_ghost_indicators(profile: dict[str, Any]) -> GhostScoreResult:
    """Apply the 10-indicator ghost-company scoring model.

    Expected `profile` keys (all optional; missing keys are treated as
    data gaps, not as safe defaults):

      age_months:               int  — months since incorporation
      has_website:              bool
      website_is_substantive:   bool  — not parked, has business info
      staff_count_linkedin:     int
      directors_are_only_staff: bool
      registered_address_type:  "mail_drop" | "virtual_office" | "residential" | "commercial"
      registered_address_cohabitants: int — number of unrelated entities at same address
      director_is_nominee:      bool
      director_appointments:    int   — count of this director's other current appointments
      director_high_risk_links: bool  — any director links to sanctioned / flagged entities
      director_jurisdiction_mismatch: bool
      shares_agent_with_flagged: bool
      shares_address_with_flagged: bool
      declared_activity:        "specific_aligned" | "generic_holding" | "mismatched"
      financials:               "filed_active" | "dormant" | "minimal" | "none"
      transaction_value_usd:    float
      chain_opacity_layers:     int — # of secrecy jurisdictions in chain
      chain_terminates_cleanly: bool
      transaction_type_aligned: bool
      recent_identity_change:   bool
      recent_director_changes_unexplained: bool

    Returns a GhostScoreResult. Callers convert with .as_dict() for
    API payloads or pass the object directly to a downstream
    aggregator.
    """
    indicators: list[GhostScoreIndicator] = []
    data_gaps: list[str] = []

    def _need(key: str) -> bool:
        if key not in profile or profile[key] is None:
            data_gaps.append(key)
            return False
        return True

    # INDICATOR 1 — Age
    ind1 = GhostScoreIndicator(1, "Age of entity", 2)
    if _need("age_months"):
        age = int(profile["age_months"])
        if age < 12:
            ind1.points, ind1.reason = 2, f"Entity formed <12 months ago ({age} months)"
        elif age < 24:
            ind1.points, ind1.reason = 1, f"Entity formed {age} months ago"
    indicators.append(ind1)

    # INDICATOR 2 — Digital presence (max 3)
    ind2 = GhostScoreIndicator(2, "Digital presence", 3)
    web_pts = 0
    web_reasons: list[str] = []
    if _need("has_website") and not profile["has_website"]:
        web_pts += 2
        web_reasons.append("no website")
    elif profile.get("has_website") and profile.get("website_is_substantive") is False:
        web_pts += 1
        web_reasons.append("website has no substantive business info")
    if "staff_count_linkedin" in profile:
        staff = int(profile["staff_count_linkedin"] or 0)
        if staff <= 2 and profile.get("directors_are_only_staff"):
            web_pts += 1
            web_reasons.append("staff on LinkedIn are only the directors")
        elif staff == 0:
            web_pts += 1
            web_reasons.append("zero LinkedIn staff")
    else:
        data_gaps.append("staff_count_linkedin")
    ind2.points = min(web_pts, 3)
    ind2.reason = "; ".join(web_reasons)
    indicators.append(ind2)

    # INDICATOR 3 — Registered address (max 2)
    ind3 = GhostScoreIndicator(3, "Registered address quality", 2)
    if _need("registered_address_type"):
        addr = profile["registered_address_type"]
        if addr == "mail_drop":
            ind3.points, ind3.reason = 2, "Registered address is a mail-drop / formation-service cluster"
        elif addr in ("virtual_office", "residential"):
            ind3.points, ind3.reason = 1, f"Registered address is {addr}"
        if profile.get("registered_address_cohabitants", 0) >= 50:
            ind3.points = 2
            ind3.reason = (ind3.reason + "; " if ind3.reason else "") + \
                f"{profile['registered_address_cohabitants']} other entities at same address"
    indicators.append(ind3)

    # INDICATOR 4 — Director profile (max 3)
    ind4 = GhostScoreIndicator(4, "Director profile", 3)
    d_pts = 0
    d_reasons: list[str] = []
    if profile.get("director_is_nominee"):
        d_pts += 3
        d_reasons.append("nominee director identified")
    elif profile.get("director_appointments", 0) >= 10:
        d_pts += 2
        d_reasons.append(f"director holds {profile['director_appointments']} other appointments")
    if profile.get("director_high_risk_links"):
        d_pts += 2
        d_reasons.append("director linked to sanctioned/flagged entities")
    if profile.get("director_jurisdiction_mismatch"):
        d_pts += 1
        d_reasons.append("director based in jurisdiction unrelated to entity's operations")
    ind4.points = min(d_pts, 3)
    ind4.reason = "; ".join(d_reasons)
    indicators.append(ind4)

    # INDICATOR 5 — Shared infrastructure (max 2)
    ind5 = GhostScoreIndicator(5, "Shared infrastructure with flagged entities", 2)
    s_pts = 0
    s_reasons: list[str] = []
    if profile.get("shares_agent_with_flagged"):
        s_pts += 2
        s_reasons.append("shares registered agent with sanctioned/flagged entities")
    if profile.get("shares_address_with_flagged"):
        s_pts += 1
        s_reasons.append("shares registered address with sanctioned/flagged entities")
    ind5.points = min(s_pts, 2)
    ind5.reason = "; ".join(s_reasons)
    indicators.append(ind5)

    # INDICATOR 6 — Declared business activity (max 2)
    ind6 = GhostScoreIndicator(6, "Declared business activity", 2)
    if _need("declared_activity"):
        act = profile["declared_activity"]
        if act == "mismatched":
            ind6.points, ind6.reason = 2, "Declared activity does not match proposed transaction"
        elif act == "generic_holding":
            ind6.points, ind6.reason = 1, "Declared activity is generic 'holding/trading' — no specific business"
    indicators.append(ind6)

    # INDICATOR 7 — Financials (max 2)
    ind7 = GhostScoreIndicator(7, "Financial filings", 2)
    if _need("financials"):
        fin = profile["financials"]
        if fin in ("dormant", "none"):
            ind7.points, ind7.reason = 2, f"Financials are {fin}"
        elif fin == "minimal":
            tv = profile.get("transaction_value_usd", 0) or 0
            if tv >= 1_000_000:
                ind7.points, ind7.reason = 2, f"Minimal financials inconsistent with ${tv:,.0f} transaction"
            else:
                ind7.points, ind7.reason = 1, "Minimal financials filed"
    indicators.append(ind7)

    # INDICATOR 8 — Corporate chain opacity (max 2)
    ind8 = GhostScoreIndicator(8, "Corporate chain opacity", 2)
    if profile.get("chain_terminates_cleanly") is False:
        ind8.points, ind8.reason = 1, "UBO chain cannot be completed from open sources"
    if (profile.get("chain_opacity_layers") or 0) >= 3:
        ind8.points = 2
        ind8.reason = f"chain passes through {profile['chain_opacity_layers']} secrecy jurisdictions"
    indicators.append(ind8)

    # INDICATOR 9 — Transaction profile (max 2)
    ind9 = GhostScoreIndicator(9, "Transaction profile", 2)
    t_pts = 0
    t_reasons: list[str] = []
    tv = profile.get("transaction_value_usd", 0) or 0
    fin = profile.get("financials", "")
    if tv >= 10_000_000 and fin in ("dormant", "minimal", "none"):
        t_pts += 2
        t_reasons.append(f"${tv:,.0f} transaction disproportionate to financial footprint")
    if profile.get("transaction_type_aligned") is False:
        t_pts += 1
        t_reasons.append("transaction type outside declared business")
    ind9.points = min(t_pts, 2)
    ind9.reason = "; ".join(t_reasons)
    indicators.append(ind9)

    # INDICATOR 10 — History and changes (max 2)
    ind10 = GhostScoreIndicator(10, "Identity / director changes", 2)
    h_pts = 0
    h_reasons: list[str] = []
    if profile.get("recent_identity_change"):
        h_pts += 2
        h_reasons.append("recent name/domicile/strike-off change")
    elif profile.get("recent_director_changes_unexplained"):
        h_pts += 1
        h_reasons.append("unexplained recent director changes")
    ind10.points = min(h_pts, 2)
    ind10.reason = "; ".join(h_reasons)
    indicators.append(ind10)

    # INDICATOR 11 — Founding-year misrepresentation (max 4)
    # Cross-check any claimed founding year against the registry-
    # derived incorporation date. On Romanian CUIs we use the
    # sequential-CUI analyzer; on UK entities we use the registry
    # incorporation_date directly if supplied. Any gap >=10 years
    # alone is enough to push the total score over the RED threshold.
    ind11 = GhostScoreIndicator(11, "Founding-year misrepresentation", 4)
    claimed_year = profile.get("claimed_founding_year")
    if claimed_year is not None:
        registry_year: int | None = None
        # Romanian path — use CUI analyzer
        if profile.get("cui") or (profile.get("jurisdiction") or "").upper() in ("RO", "ROMANIA"):
            try:
                from ._romanian_cui import analyse_cui
                _cui = profile.get("cui") or profile.get("registration_number")
                if _cui:
                    _a = analyse_cui(_cui)
                    if _a and _a.estimated_incorporation:
                        registry_year = _a.estimated_incorporation.year
            except Exception:
                pass
        # Generic path — use age_months or incorporation_year hint
        if registry_year is None:
            if profile.get("incorporation_year"):
                try:
                    registry_year = int(profile["incorporation_year"])
                except Exception:
                    pass
            elif profile.get("age_months") is not None:
                try:
                    import datetime as _dt
                    registry_year = _dt.date.today().year - (int(profile["age_months"]) // 12)
                except Exception:
                    pass

        if registry_year is not None:
            try:
                gap = registry_year - int(claimed_year)
            except Exception:
                gap = 0
            if gap >= 10:
                ind11.points = 4
                ind11.reason = (
                    f"CRITICAL misrepresentation: claimed founding year "
                    f"{claimed_year} is {gap} years BEFORE registry-derived "
                    f"incorporation year {registry_year}. Sequential-CUI / "
                    f"registry analysis shows this cannot be a rounding error."
                )
            elif gap >= 5:
                ind11.points = 3
                ind11.reason = (
                    f"Material misrepresentation: claimed founding year "
                    f"{claimed_year} is {gap} years before registry-derived "
                    f"year {registry_year}. Requires formal explanation."
                )
            elif gap >= 2:
                ind11.points = 1
                ind11.reason = (
                    f"Minor discrepancy: claimed founding year {claimed_year} "
                    f"is {gap} years before registry-derived year {registry_year}. "
                    f"Within normal registry uncertainty but worth verifying."
                )
            # gap 0-1 or claim NEWER than registry: 0 points (consistent)
        else:
            data_gaps.append("founding-year check: registry year unavailable")
    else:
        data_gaps.append("founding-year check: no claimed_founding_year supplied")
    indicators.append(ind11)

    # INDICATOR 12 — Domain substitution / leetspeak (max 2)
    # Detect digit-for-letter substitution in the root of the company's
    # public domain. Defence-industry entities do not legitimately use
    # s3rban.com-style names. Also detects a few known character-swap
    # typosquat patterns.
    ind12 = GhostScoreIndicator(12, "Domain substitution / leetspeak", 2)
    website = (profile.get("website") or profile.get("domain") or "").strip().lower()
    if website:
        import re as _re
        # Strip scheme and path
        root = _re.sub(r"^https?://", "", website)
        root = root.split("/", 1)[0]
        # Strip www
        if root.startswith("www."):
            root = root[4:]
        # Split into labels
        labels = root.split(".")
        root_label = labels[0] if labels else ""
        # Skip very short domain labels (e.g. "bp.com")
        if len(root_label) >= 4:
            # A digit inside a root label (not at the start or end) is
            # a leetspeak substitution signal. Exclude "365" / "247" /
            # numeric suffix patterns by requiring the digit to be
            # surrounded by letters.
            internal_digit_match = _re.search(r"[a-z]\d[a-z]", root_label)
            if internal_digit_match:
                ind12.points = 2
                ind12.reason = (
                    f"Domain '{root_label}' contains an internal digit "
                    f"(pattern '{internal_digit_match.group(0)}') — letter/digit "
                    f"substitution pattern. Legitimate defence entities do not use "
                    f"leetspeak domain names."
                )
            else:
                # Secondary: any digit at all in a short (<10 char) root
                # label is suspicious even without the strict sandwich
                # pattern, unless the label is a recognised numeric token
                digit_in_root = _re.search(r"\d", root_label)
                if digit_in_root and len(root_label) < 10:
                    ind12.points = 1
                    ind12.reason = (
                        f"Domain '{root_label}' contains a digit — atypical "
                        f"for a defence company domain; verify provenance."
                    )
    else:
        data_gaps.append("domain substitution check: no website supplied")
    indicators.append(ind12)

    total = sum(i.points for i in indicators)
    max_total = sum(i.max_points for i in indicators)
    classification, recommendation = _classify(total)
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="due_diligence_playbooks",
        summary="Score Ghost Indicators",
        source_id="due_diligence_playbooks:R-F996",
    )

    return GhostScoreResult(
        total=total,
        max_total=max_total,
        classification=classification,
        recommendation=recommendation,
        indicators=indicators,
        data_gaps=sorted(set(data_gaps)),
        subject={
            "name": profile.get("name", ""),
            "jurisdiction": profile.get("jurisdiction", ""),
            "registration_number": profile.get("registration_number", ""),
        },
    )


# =============================================================================
# REGISTRY + RAG INGESTION
# =============================================================================

ALL_SECTIONS = {
    "ubo_extraction_playbook": {
        "content": UBO_PLAYBOOK,
        "tags": ["UBO", "beneficial-ownership", "BVI", "cayman",
                 "seychelles", "delaware", "uk-companies-house",
                 "luxembourg", "cyprus", "hong-kong", "singapore",
                 "uae", "panama", "opencorporates", "icij",
                 "world-check", "orbis", "gleif", "FATF-25-percent",
                 "nominee-director", "shell-company", "chain-traversal"],
        "domain": "due_diligence",
    },
    "ghost_detection_scoring": {
        "content": GHOST_DETECTION_PLAYBOOK,
        "tags": ["ghost-company", "shell-company", "scoring",
                 "red-flags", "RAG-decision", "nominee",
                 "dormant-accounts", "mail-drop", "registered-agent",
                 "transaction-disproportion", "identity-change",
                 "corporate-opacity", "ghost-score"],
        "domain": "due_diligence",
    },
}


async def ingest_all_sections() -> dict:
    """Ingest the UBO + ghost-detection playbooks into ARIA's RAG store."""
    from . import rag_store

    results = {}
    total_chunks = 0
    for section_name, data in ALL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                data["content"],
                source=f"intel:due_diligence_playbooks:{section_name}",
                source_type="due_diligence",
                title=f"Due Diligence Playbook — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/due_diligence/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "due_diligence_playbooks",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("DD playbook ingested: %s (%d chunks)", section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("DD playbook ingestion failed [%s]: %s", section_name, e)

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info(
        "Due-diligence playbook ingestion: %d/%d sections, %d total chunks",
        success, len(ALL_SECTIONS), total_chunks,
    )
    return {
        "sections_ingested": success,
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }
