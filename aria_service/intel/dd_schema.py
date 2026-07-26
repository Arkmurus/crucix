# =============================================================================
# ARIA — ARK-DD Report Schema
# aria_service/intel/dd_schema.py
#
# Pure data model for the 7-layer due-diligence orchestrator output.
# Dataclasses only — zero runtime dependency on other ARIA modules so
# this can be imported by any caller without triggering import cycles.
#
# SCHEMA VERSION: 1.0
# Every ARKDDReport carries its schema version so future changes don't
# break old stored reports. Consumers should check schema_version and
# gracefully degrade if they encounter a newer major version.
#
# Design principles:
#   - Every section has `findings` (positive signals) and `data_gaps`
#     (what we couldn't resolve) as first-class fields. Never let a
#     missing data point disappear silently.
#   - Every section has a `status` enum: OK / PARTIAL / SKIPPED / ERROR.
#     SKIPPED means the orchestrator short-circuited before this layer.
#   - Cost is tracked per layer so the cost cap can halt runs that burn
#     their budget on a single layer.
#   - confidence_tag on the final report is the WEAKEST tag across all
#     sections — mirroring the confidence_footer body-rule so the
#     headline never oversells.
# =============================================================================

from __future__ import annotations
from .engine_wiring import wire_failure

import re as _re  # R-F2803 — registry-status negation detection
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from .wire import fail_wire  # R-F1789 §21 brain-wiring


# =============================================================================
# ENUMS
# =============================================================================

class LayerStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    SKIPPED = "skipped"   # short-circuited before reaching this layer
    ERROR = "error"
    PREREQ_FAIL = "prereq_fail"      # R-F1923: prerequisite missing, zero signal
    PREREQ_DEGRADED = "degraded"     # R-F1923: prerequisite missing but self-reported data exists


class RiskClassification(str, Enum):
    GREEN = "GREEN"
    AMBER_LIGHT = "AMBER-LIGHT"
    AMBER_DARK = "AMBER-DARK"
    RED = "RED"
    HARD_STOP = "HARD_STOP"


class EntityType(str, Enum):
    COMPANY = "company"
    PERSON = "person"
    ADDRESS = "address"
    VESSEL = "vessel"
    AIRCRAFT = "aircraft"
    UNKNOWN = "unknown"


# =============================================================================
# PRIMITIVES — reused across sections
# =============================================================================

@dataclass
class SectionMeta:
    """Bookkeeping for every layer section."""
    status: str = LayerStatus.OK.value
    started_at: Optional[str] = None
    duration_ms: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None
    # Number of sub-calls actually made (useful for cost audit)
    subcalls: int = 0


# R-5005 (2026-05-11) — Tier-1a source allowlist for the verification gate
# below. Findings citing a SINGLE source from this allowlist may be tagged
# [CONFIRMED] without corroboration. Everything else requires ≥2 independent
# sources OR the confidence is demoted to ASSESSED. Aligns with Clause 17
# (multi-source verification) + Clause 24 (confidence-tag decay) at the
# CODE level — prompt-only enforcement was insufficient (R-F284 showed
# headers tagged [CONFIRMED] on single-source self-reported data despite
# the constitutional rule).
_TIER_1A_SOURCE_PREFIXES: tuple[str, ...] = (
    # Government registries
    "companies_house", "company-information.service.gov.uk",
    "sec.gov", "sec_edgar",
    # Official sanctions lists (aggregator counts: OpenSanctions queries
    # OFAC SDN, OFSI, EU FSF, UN SC, BIS Entity, etc.)
    "opensanctions", "sanctions.opensanctions",
    "ofac", "sanctionssearch.ofac.treas.gov",
    "ofsi", "gov.uk/government/publications/the-uk-sanctions-list",
    "eu_fsf", "webgate.ec.europa.eu/fsd",
    "un_sc", "un.org/securitycouncil",
    "bis_entity", "bis.doc.gov",
    # ARIA's internal sanctions-module labels (wrap OpenSanctions calls)
    "sanctions.person_screen", "sanctions.screen_with_aliases",
    "sanctions.fuzzy_screen", "sanctions.classify",
    "sanctions.director_screen",  # same engine as person_screen, distinct label
    # ARIA's network-walker (registry-backed) labels
    "network_walker", "ubo_chain",
    # Domain ownership verifier — RDAP is a single authoritative source
    # for domain registration data (registrar / registrant / dates)
    "domain_ownership_verifier", "rdap",
    # Multi-jurisdiction registry adapter wrappers (covers SEC EDGAR US,
    # Handelsregister DE, Infogreffe FR, etc.)
    "registry_adapters",
    # Regulatory filings (single official-source confirmations OK)
    "fca.org.uk", "bafin.de", "cnmv.es",
    # Court records (a court judgment is a single authoritative source)
    "courtlistener", "bailii.org",
    # Treaty / multilateral records
    "un.org", "icrc.org", "icj-cij.org",
    # FATF — international AML standard-setter (Tier-1a, R-F601)
    "fatf-gafi.org", "risk_indices.fatf",
)


def _is_tier_1a_source(source: str) -> bool:
    """True if `source` is an authoritative single-source. R-5005."""
    if not source:
        return False
    s = source.lower()
    return any(prefix in s for prefix in _TIER_1A_SOURCE_PREFIXES)


@dataclass
class Finding:
    """A single structured finding inside any layer.

    R-5005 (2026-05-11) — added `sources: list[str]` for multi-source
    corroboration tracking. The `__post_init__` enforces the verification
    gate: a finding tagged [CONFIRMED] must have either (a) ≥2 sources,
    or (b) a single source that's in the Tier-1a allowlist. Otherwise
    the confidence is demoted to [ASSESSED] with a `_gate_demoted=True`
    marker so the renderer can flag it. This is the CODE-level companion
    to prompt Clause 24 (R-F284) — prompt-only enforcement was leaky.
    """
    severity: str  # "info" | "amber" | "red" | "hard_stop"
    title: str
    detail: str = ""
    source: str = ""
    confidence: str = "ASSESSED"  # CONFIRMED | PROBABLE | ASSESSED | UNCERTAIN | SPECULATIVE
    # R-5005: list of source identifiers backing this finding. Default
    # empty list; __post_init__ populates from `source` for legacy callers.
    sources: list[str] = field(default_factory=list)
    # R-5005: whether the gate downgraded the original confidence.
    # Set by __post_init__; renderers can surface this so operator
    # sees that a stronger tag was demoted (not just a weak tag from
    # the start).
    gate_demoted: bool = False
    gate_reason: str = ""
    # R-F2691 — STRUCTURED provenance (DD Grade-A Phase-0, gap #4). `Evidence` has
    # carried url/source_tier/retrieved_at since the start; `Finding` — the thing an
    # analyst actually reads and acts on — carried only a bare `source` string, so
    # callers that HAD a url could only smuggle it into free text
    # (`f"{name} [from {url}]"`, dd_orchestrator), recoverable solely by parsing a
    # display string, and "when was this true?" stayed unanswerable.
    #
    # PURELY ADDITIVE, and that is a deliberate constraint, not laziness. These fields
    # do NOT supersede the `[from {url}]` suffix, which MEASURES as load-bearing:
    # `origin_key`/`_is_tier_1a_source` match on DOMAINS, so the embedded url is what
    # makes "bailii [from https://www.bailii.org/…]" resolve to pub:bailii.org and clear
    # the R-5005 Tier-1a gate, where a bare "bailii" yields external_unclassified and
    # FAILS it. Removing the suffix silently demotes findings and collapses distinct
    # sources into one origin. Making those two functions prefer `url` is the real fix
    # and is a separate R-number (it touches the tier gate + ~127 construction sites).
    #
    # All optional → every existing construction site keeps working unchanged; a site
    # that cannot supply provenance honestly reports none rather than inventing it.
    #
    # Vocabulary is deliberately Evidence's, NOT a new one: a second tier spelling
    # would silently fork the meaning of "OFFICIAL" across the report.
    url: Optional[str] = None
    source_tier: str = "UNKNOWN"  # OFFICIAL | INDUSTRY | QUALITY_PRESS | UNVERIFIED
    retrieved_at: Optional[str] = None
    # ── R-F3098 — CONTEXT IS NOT A FINDING ABOUT THE SUBJECT ────────────────
    #
    # THE DEFECT (Mitie, operator report 2026-07-26). Under the heading
    # "⚖ Compliance & Sanctions" — the section a reader scans to decide whether they
    # may transact — sat two items that are not compliance findings about anyone:
    #
    #   "Sovereign macro context: central-govt debt 130.7% of GDP"
    #        …whose own detail text ends "not a finding against this entity"
    #   "US federal contracts: 4 award(s), $3,409,511"
    #        …a commercial fact, filed under compliance
    #
    # Placement is a claim. A jurisdiction-level statistic rendered beside a sanctions
    # screen reads as an adverse signal about the subject, and a reader who scans
    # headings rather than bodies will carry that away — the detail line disclaiming
    # it arrives too late to undo the impression the position created.
    #
    # `context_only` marks a finding as ABOUT THE ENVIRONMENT (a country, a market, a
    # sector) rather than about the subject. It never changes a verdict, never
    # suppresses anything, and is purely additive — an unflagged finding behaves
    # exactly as before. Renderers group these under their own heading so the
    # decision-driving section contains only decision-driving material.
    context_only: bool = False
    #: Short label for the grouped block, e.g. "Country & market context".
    context_kind: str = ""

    def has_provenance(self) -> bool:
        """True when this finding can point at WHERE it came from.

        Deliberately requires a url — a tier alone is a claim about a source we
        cannot show the analyst, which is the gap this field exists to close.
        """
        return bool(self.url)

    def __post_init__(self) -> None:
        # Back-fill `sources` from legacy `source` field
        if not self.sources and self.source:
            self.sources = [self.source]
        # Verification gate: [CONFIRMED] requires ≥2 sources OR single Tier-1a
        if self.confidence == "CONFIRMED":
            unique_sources = [s for s in (self.sources or []) if s]
            if len(unique_sources) >= 2:
                # Multi-source: gate passes
                pass
            elif len(unique_sources) == 1 and (
                _is_tier_1a_source(unique_sources[0])
                # R-F2696 — a single source is ALSO Tier-1a when its STRUCTURED url
                # is. The allowlist mixes bare labels ("courtlistener") with DOMAINS
                # ("bailii.org", "fca.org.uk"), so the clean label "bailii" does not
                # match ("bailii.org" is not a substring of "bailii") and the finding
                # was silently demoted CONFIRMED -> ASSESSED — even though its url,
                # https://www.bailii.org/…, IS the authority the allowlist names.
                # The only thing that had been rescuing it was dd_orchestrator
                # concatenating the url INTO the source string (f"{name} [from {url}]"),
                # i.e. the gate's verdict rode on display formatting. Authority now
                # travels in a field. NOT a bypass: the url is tier-checked against the
                # same allowlist, so a non-authoritative link still demotes; and it is
                # only consulted when there IS a source to tier (a bare link certifies
                # nothing).
                or _is_tier_1a_source(self.url or "")
            ):
                # Single Tier-1a source: gate passes
                pass
            else:
                # Demote to ASSESSED with reason marker
                original = self.confidence
                self.confidence = "ASSESSED"
                self.gate_demoted = True
                if len(unique_sources) == 1:
                    self.gate_reason = (
                        f"R-5005 demoted from {original}: single source "
                        f"'{unique_sources[0]}' is not in Tier-1a allowlist"
                    )
                else:
                    self.gate_reason = (
                        f"R-5005 demoted from {original}: no source provided"
                    )


@dataclass
class Evidence:
    """A piece of evidence cited somewhere in the report."""
    source: str
    source_tier: str = "UNKNOWN"  # OFFICIAL | INDUSTRY | QUALITY_PRESS | UNVERIFIED
    url: Optional[str] = None
    snippet: Optional[str] = None
    retrieved_at: Optional[str] = None


# =============================================================================
# LAYER SECTIONS
# =============================================================================

@dataclass
class IdentitySection:
    """Layer 1 — who is this entity on paper?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    entity_name: str = ""
    entity_type: str = EntityType.UNKNOWN.value
    jurisdiction: Optional[str] = None
    jurisdiction_iso2: Optional[str] = None
    registration_number: Optional[str] = None
    registration_status: Optional[str] = None  # active | dissolved | dormant | …
    incorporation_date: Optional[str] = None
    registered_address: Optional[str] = None
    declared_activity: Optional[str] = None
    directors: list[dict] = field(default_factory=list)
    shareholders: list[dict] = field(default_factory=list)
    ubo_chain: list[dict] = field(default_factory=list)
    # R-F3024 — the registry's own name history: [{name, effective_from, ceased_on}].
    # A recent change (and especially a swap with a co-located company) is a finding,
    # not trivia — and it also explains why evidence gathered under the OLD name can
    # belong to a different legal entity.
    previous_names: list[dict] = field(default_factory=list)
    # R-F3021 — GLEIF LEI registration state as STRUCTURED data, not prose. A LAPSED
    # LEI (the holder stopped renewing) was reported only inside the sentence of an
    # info-severity finding, so nothing downstream could see it: not the scorecard,
    # not the PDF, not a filter. Shape:
    # {lei, registration_status, entity_status, lapsed: bool, source_url}
    lei_registration: dict = field(default_factory=dict)
    sanctions_screen: dict = field(default_factory=dict)  # from sanctions.screen_with_aliases
    ghost_score: dict = field(default_factory=dict)       # from due_diligence_playbooks.score_ghost_indicators
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class NetworkSection:
    """Layer 2 — who is connected to this entity?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    director_graph: dict = field(default_factory=dict)       # nodes + edges
    cross_linked_entities: list[dict] = field(default_factory=list)
    # R-F2730 — ANCHORED controlled_by edges (grade A): a corporate PSC identified by
    # its own registry number is a VERIFIED control relationship. Populated from
    # companies_house.investigate_uk_entity (R-F2726) and written to the relationship
    # graph. Distinct from cross_linked_entities (the disabled name-match source).
    controlled_by: list[dict] = field(default_factory=list)
    # R-F3027 — corporate controllers disclosed by the subject's PSC filing that
    # Companies House gives no registration number for. NOT Grade A (nothing to
    # anchor to) and NOT traversable, but a 75-100% controller that no surface
    # renders is indistinguishable from one that was never found.
    controlled_by_unanchored: list[dict] = field(default_factory=list)
    address_cluster: dict = field(default_factory=dict)       # addresses with shared entities
    pep_connections: list[dict] = field(default_factory=list)
    sanctions_network: list[dict] = field(default_factory=list)  # flagged entities in the chain
    # R-F435 (2026-05-13) — UBO chain walker output. `ubo_chain` is the
    # flattened node list (preserves the pre-existing reader contract at
    # dd_orchestrator.py:4252 — iterate `u.get("name")`). `ubo_chain_walk`
    # is the full walker result dict (graph + sanctioned_in_chain +
    # coverage_gaps + verdict + stats) for renderers that want richness.
    ubo_chain: list[dict] = field(default_factory=list)
    ubo_chain_walk: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class VerificationSection:
    """Layer 3 — how well-grounded is the picture from prior layers?

    R-F393 (2026-05-13): naming is historical — this layer DOES NOT do
    independent source verification (URL fetch + claim re-check against
    external sources). It triangulates source-counts from claims that
    Layers 1/2/4/5 already collected, detects conflicts between sections,
    and computes a confidence floor. The honest scope flags below are
    populated by `_run_verification` so report consumers (dashboards,
    chat audit, BLUF) can render the truth instead of inferring
    "verification" from the section name.
    """
    meta: SectionMeta = field(default_factory=SectionMeta)
    triangulated_claims: list[dict] = field(default_factory=list)  # claim, n_sources, confidence
    conflicts: list[dict] = field(default_factory=list)
    grounded_rate: Optional[float] = None
    unverified_claim_count: int = 0
    # R-F2662 — independent-corroboration signal (step toward full independent
    # verification). Fraction of material claims backed by >=2 DISTINCT EXTERNAL
    # origins — ARIA's own internal memory/RAG never counts (grounded_rate does count
    # it, over-stating grounding). This does NOT set independent_source_verification_run
    # (that still requires full re-fetch re-verification, R-F2413).
    independent_corroboration_rate: Optional[float] = None
    independent_corroborated_count: int = 0
    # R-F2671 — C-3 v2 out-of-band independent verification (re-fetch cited press +
    # cluster same-story republications). Populated by the followup ONLY when
    # ARIA_DD_INDEPENDENT_VERIFY is on. independent_source_verification_run flips to True
    # ONLY in "enforce" mode (operator-gated, after reviewing measure-mode live data) —
    # R-F2413 stays honestly False otherwise.
    independent_verification: dict = field(default_factory=dict)
    confidence_floor: str = "ASSESSED"
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    # R-F393: honest-scope flags. Set by _run_verification.
    # independent_source_verification_run means FULL independent re-verification
    # of each claim against re-fetched external sources. R-F2413: this stays False
    # — source_verifier IS invoked (R-F2282) but only for CITATION GROUNDING
    # (were cited URLs fetched into evidence?), tracked separately by
    # citation_grounding_rate; citation grounding is NOT full source verification.
    independent_source_verification_run: bool = False
    scope_note: str = ""
    # R-F2282: real source-verifier output. citation_grounding_rate = fraction of
    # URLs cited across the report's findings that were actually fetched into the
    # evidence set (source_verifier.verify_response). Distinct from grounded_rate
    # (the triangulation source-count metric). None = no inline URL citations to check.
    citation_grounding_rate: Optional[float] = None
    citation_verdict: str = ""
    citations_checked: int = 0
    citations_grounded: int = 0


@dataclass
class ComplianceSection:
    """Layer 4 — which legal / regulatory regimes bite on this transaction?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    country_risk: dict = field(default_factory=dict)           # from risk_indices.get_country_risk
    financial_health: dict = field(default_factory=dict)       # R-F2322 — from financial_health.assess (SEC EDGAR + search + vault)
    export_control: dict = field(default_factory=dict)         # from tech_classifier.classify_export_control
    sanctions_regimes: list[str] = field(default_factory=list)  # applicable regimes (UK/US/EU/UN)
    ihl_criterion_2_risk: Optional[str] = None                 # low | medium | high | clear
    regional_bloc_requirements: list[dict] = field(default_factory=list)  # ECOWAS/SADC/GCC/etc
    licence_path: Optional[str] = None                         # SIEL | SITCL | OIEL | OGEL | DSP-5 | ...
    # R-F635 (2026-05-17): cultural intelligence read — Hofstede + Hall
    # + Erin Meyer + practical norms (weekend, formality, gift-giving).
    # Populated from cultural_atlas.render_context_block() when the
    # counterparty jurisdiction is non-UK/US and is seeded in the atlas.
    # Empty string means: jurisdiction outside the operator's familiar
    # west, but the atlas hasn't been seeded with cultural data yet —
    # NOT a default of "no culture context exists".
    cultural_context: str = ""
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class DigitalSection:
    """Layer 5 — what does the open web say about this entity?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    web_footprint: dict = field(default_factory=dict)            # from search_multilingual
    people: list[dict] = field(default_factory=list)             # R-F1816: investigated named individuals (deep_researcher recursive person drill-down)
    press_coverage: list[Evidence] = field(default_factory=list)
    procurement_history: list[dict] = field(default_factory=list)
    exhibition_presence: list[dict] = field(default_factory=list)
    knowledge_base_hits: list[dict] = field(default_factory=list)  # existing RAG hits
    neural_associations: list[str] = field(default_factory=list)
    source_tier_breakdown: dict = field(default_factory=dict)    # OFFICIAL/INDUSTRY/PRESS counts
    search_ecosystem: dict = field(default_factory=dict)         # per-run backend health snapshot
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class SweepDataSection:
    """Layer 5b — real-time intelligence from the 49-source Node sweep.

    The sweep runs every 5-7 minutes and collects data from 49 sources
    (sanctions updates, procurement tenders, defence news, conflict events,
    trade data, etc.). This section queries the brain for recent signals
    relevant to the target entity and jurisdiction.

    This bridges the gap between the DD orchestrator (which uses 7 vendor
    integrations) and the sweep (which covers 49 sources in real time).
    """
    meta: SectionMeta = field(default_factory=SectionMeta)
    recent_signals: list[dict] = field(default_factory=list)       # sweep signals from last 7d
    relevant_news: list[dict] = field(default_factory=list)        # news items mentioning target
    jurisdiction_events: list[dict] = field(default_factory=list)  # conflict/protest events
    procurement_alerts: list[dict] = field(default_factory=list)   # relevant tender opportunities
    sanctions_updates: list[dict] = field(default_factory=list)    # recent sanctions changes
    trade_signals: list[dict] = field(default_factory=list)        # trade/economic indicators
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class CommercialCoherenceSection:
    """Layer 5c — is the commercial/legal structure coherent for the deal?

    Assesses three questions that no other layer asks:
      1. Corporate coherence — does the corporate structure fit the claimed
         activity and deal size?
      2. Commercial terms coherence — are payment ratios, bonds, liability
         caps consistent with market norms for this sector/jurisdiction?
      3. Legal framework coherence — does the counterparty acknowledge the
         full licence chain their deal actually requires?

    Populated by commercial_coherence.assess_commercial_coherence() and
    consumed by deception scoring + Layer 6 synthesis + BLUF assembly.
    """
    meta: SectionMeta = field(default_factory=SectionMeta)
    coherence_score: float = 1.0              # 0.0 (incoherent) → 1.0 (clean)
    tier: str = "GREEN"                       # GREEN | ELEVATED | HIGH
    anomalies: list[dict] = field(default_factory=list)              # {kind, severity, detail}
    jurisdiction_flags: list[str] = field(default_factory=list)      # jurisdiction-specific structural flags
    licence_chain_gaps: list[dict] = field(default_factory=list)     # {deal_shape, missing_step, authority}
    payment_anomalies: list[dict] = field(default_factory=list)      # {market, claimed, norm_range, severity}
    corporate_anomalies: list[dict] = field(default_factory=list)    # {kind, severity, detail}
    offset_issues: list[dict] = field(default_factory=list)          # {regime, issue, detail}
    assessment_summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class SynthesisSection:
    """Layer 6 — what does it all mean?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    ach_matrix: dict = field(default_factory=dict)     # hypothesis → evidence support
    ghost_score_total: int = 0                         # 0-20
    ghost_classification: str = "GREEN"
    risk_classification: str = RiskClassification.GREEN.value
    sar_trigger: bool = False
    sar_rationale: Optional[str] = None
    key_findings: list[Finding] = field(default_factory=list)
    competing_narratives: list[str] = field(default_factory=list)
    residual_unknowns: list[str] = field(default_factory=list)


# =============================================================================
# THE ARK-DD REPORT
# =============================================================================

@dataclass
class ARKDDReport:
    """Layer 7 — the assembled output of the DD orchestrator run.

    Stored in Redis under crucix:dd:report:{run_id} + appended to the
    intel ledger as a DD-class signal. Also serialised as markdown for
    chat output via render_markdown().
    """

    # Provenance
    run_id: str = field(default_factory=lambda: f"dd_{uuid.uuid4().hex[:12]}")
    schema_version: str = "1.0"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    generator: str = "aria.dd_orchestrator"
    trace_id: Optional[str] = None

    # Input
    target: dict = field(default_factory=dict)            # raw trigger input
    orchestrator_mode: str = "standard"                   # quick | standard | deep
    layers_run: list[str] = field(default_factory=list)
    layers_skipped: list[str] = field(default_factory=list)
    # R-F2494 — per-run diagnostics: search/Brave state + per-query counts, registry
    # adapter attempt/result, executed mode + auto-deep escalation + layer counts.
    run_diagnostics: dict = field(default_factory=dict)

    # Sections
    identity: IdentitySection = field(default_factory=IdentitySection)
    network: NetworkSection = field(default_factory=NetworkSection)
    verification: VerificationSection = field(default_factory=VerificationSection)
    compliance: ComplianceSection = field(default_factory=ComplianceSection)
    digital: DigitalSection = field(default_factory=DigitalSection)
    sweep_data: SweepDataSection = field(default_factory=SweepDataSection)
    commercial_coherence: CommercialCoherenceSection = field(default_factory=CommercialCoherenceSection)
    synthesis: SynthesisSection = field(default_factory=SynthesisSection)

    # Output headline (BLUF)
    bottom_line: str = ""
    recommendation: str = ""
    risk_classification: str = RiskClassification.GREEN.value
    confidence_tag: str = "ASSESSED"         # weakest across all sections
    next_actions: list[str] = field(default_factory=list)
    data_gaps_summary: list[str] = field(default_factory=list)

    # Cost + timing
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    layer_costs_usd: dict = field(default_factory=dict)

    # Verification gate (2026-04-18) — populated on RED/HARD_STOP verdicts
    # when a secondary provider has reviewed the same evidence. Empty dict
    # when the gate didn't fire (GREEN/AMBER or no secondary available).
    verification_gate: dict = field(default_factory=dict)

    # R-F298: confidence-gate stamping. When AMBER-LIGHT was reached
    # purely because the data was too thin to issue GREEN (NOT because of
    # a real amber risk finding), `confidence_gate_triggered=True` so the
    # BLUF reads "INSUFFICIENT EVIDENCE" instead of "can proceed".
    confidence_gate_triggered: bool = False
    confidence_gate_reasons: list[str] = field(default_factory=list)
    # R-F2786 — customer-facing answer to "may this report be relied on?".
    # Separate from risk_classification: no observed red flags is not the same
    # as complete evidence. Populated by _assemble_bluf and recomputed for
    # historical reports by structured_view.
    decision_readiness: dict = field(default_factory=dict)

    # R-F305: ecosystem awareness — populated by _emit_ecosystem_status()
    # at end of run with per-layer activity + wired-but-silent flags so
    # the dashboard / self_diagnostic can flag dormant code paths.
    ecosystem_status: dict = field(default_factory=dict)

    # R-F295 backfill bookkeeping (and any future post-link-tree backfill).
    discipline_coverage: dict = field(default_factory=dict)
    adverse_media: dict = field(default_factory=dict)

    # ── R-F591 (2026-05-16) — case-file versioning fields ────────────────
    # R-F573 added canonical_entity_id + version_number + previous_run_id
    # + version_diff to the PERSISTED body (the Redis blob) inside
    # _persist_report. But the synchronous /api/aria/dd/orchestrate
    # response is built from `report.as_dict()` which serialises the
    # dataclass — so the chat / WhatsApp / web client got None for
    # these fields even though they were correctly set on the Redis
    # copy that /dd/report/{run_id} returns later. R-F591 promotes the
    # 4 fields to first-class dataclass attributes so as_dict()
    # exports them on every code path (orchestrate response AND Redis
    # body AND case-file endpoint AND markdown render). _persist_report
    # now sets these directly on the dataclass instead of injecting
    # into the dict after as_dict().
    canonical_entity_id: Optional[str] = None
    version_number: int = 1
    previous_run_id: Optional[str] = None
    version_diff: Optional[dict] = None

    # ── R-F607 (2026-05-16) — per-user scoping ───────────────────────────
    # The DD report index used to be globally readable: every authenticated
    # user calling /api/aria/dd/reports got the full Redis index, including
    # reports run by other operators. R-F607 stamps the originating user's
    # identity onto the report at orchestrate time so list_reports can
    # filter to "your own runs only" by default. Email-domain shoulders
    # the R-F608 company-shared follow-up (members of the same company
    # email domain see each other's runs). All three fields are
    # nullable for back-compat — pre-R-F607 entries persist with None
    # and are excluded from user-filtered lists (admin-only path can
    # still see them via the no-filter branch).
    user_id: Optional[str] = None
    user_email_lower: Optional[str] = None
    user_email_domain: Optional[str] = None

    # ── R-F608 (2026-05-16) — per-DD company-share toggle ────────────────
    # Default True: every DD a user runs is visible to colleagues on the
    # same email domain. Set to False to keep an individual DD private
    # (sensitive personal screens, board-only counterparty work, etc.).
    # list_reports honours this via the share_to_company key on the index
    # entry, NOT this dataclass field — but the dataclass field is the
    # source of truth at persist time, so the orchestrate response and
    # the /dd/report/{run_id} body both reflect the operator's intent.
    share_to_company: bool = True

    # ── R-F875 (2026-05-25) — counter-intel / deception / divergence ─────
    # Layers 5b (deception), 8 (counter_intelligence), 9 (sanctions_divergence)
    # attached their results as INSTANCE attributes (report.deception = {...})
    # — never declared fields. as_dict() == asdict(self) only serialises
    # declared dataclass fields, so a `deception={tier: HIGH}` signal rendered
    # into the markdown prose (render_markdown reads them via getattr) but was
    # SILENTLY DROPPED from every JSON consumer: the dashboard, the re-screen
    # diff, the case-file endpoint, brain absorb. A HIGH deception / counter-
    # intel verdict invisible to the structured surface is an honesty defect
    # (a hidden risk signal). Same root cause + fix as R-F591 (canonical_entity_id
    # et al). Declaring them here makes asdict() export them on every path;
    # the existing `report.deception = {...}` assignments now set the field.
    deception: Optional[dict] = None
    counter_intelligence: Optional[dict] = None
    sanctions_divergence: Optional[dict] = None

    # ── Serialisation helpers ────────────────────────────────────────────

    @fail_wire(module="dd_schema", gap_type="engine_failure")
    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict. Dataclasses.asdict recursively
        handles every nested dataclass including Finding and SectionMeta."""
        return asdict(self)

    @fail_wire(module="dd_schema", gap_type="engine_failure")
    def render_markdown(self, *, concise: bool = False) -> str:
        """Render the report as Markdown for chat / WhatsApp delivery.

        `concise` trims sub-sections to their bottom-line + top findings
        so the output fits inside a single WhatsApp message. Full render
        is for API / email / dashboard.
        """
        lines: list[str] = []
        # BLUF
        lines.append(f"*🔎 ARK-DD REPORT — {self.identity.entity_name or self.target.get('query','(unnamed)')}*")
        lines.append("")
        lines.append(f"*{self.risk_classification}* — {self.bottom_line}")
        lines.append("")
        if self.recommendation:
            lines.append(f"*Recommendation:* {self.recommendation}")
            lines.append("")

        # R-F2681 — surface the already-computed EVIDENCE-DEPTH grade + blockers
        # so the operator reads "Grade B because X — to reach A: Z" in the report
        # itself, not only in the structured_view JSON. This is an evidence-
        # sufficiency grade, SEPARATE from the risk classification (a GREEN report
        # can still be low-grade on sparse/ungrounded evidence). Live-computed each
        # render (no persisted snapshot → no drift). Must never break the report.
        try:
            _qa = _dd_quality_assessment(self.as_dict())
            _grade = _qa.get("grade", "?")
            _gscore = _qa.get("score")
            _gblk = [b for b in (_qa.get("blocking_reasons") or []) if b]
            _gemoji = {"A": "🟢", "B": "🔵", "C": "🟡",
                       "D": "🔴", "INCOMPLETE": "⚪"}.get(_grade, "📊")
            _score_txt = f" ({_gscore}/100)" if isinstance(_gscore, int) else ""
            lines.append(
                f"*{_gemoji} Evidence Grade: {_grade}{_score_txt}* "
                f"— evidence depth, not risk"
            )
            if _grade == "A":
                lines.append("Evidence depth meets all Grade-A thresholds.")
            elif _gblk:
                _hdr = "Grade withheld —" if _grade == "INCOMPLETE" else "To reach Grade A:"
                lines.append(_hdr)
                _cap = 2 if concise else 8
                for _b in _gblk[:_cap]:
                    lines.append(f"  • {_b}")
                if len(_gblk) > _cap:
                    lines.append(f"  • …and {len(_gblk) - _cap} more (see full report)")
            lines.append("")
        except Exception:
            pass  # evidence-grade render must never break the report

        # R-F3091 — entity scope, on the THIRD surface. The online view and the PDF
        # both render it; markdown (the WhatsApp/chat/copy surface) must not be the
        # one that silently blends a subsidiary's registry facts with its group's
        # press coverage. Same computed object, same wording.
        try:
            _sc = _dd_entity_scope(self.as_dict())
            if _sc.get("is_subsidiary"):
                lines.append("*🏛️ Entity scope*")
                lines.append(
                    f"  Registry subject: {_sc.get('subject_name') or '(unnamed)'}"
                    + (f" ({_sc['subject_registration']})" if _sc.get("subject_registration") else "")
                )
                _par = _sc.get("immediate_parent") or {}
                if _par.get("name"):
                    lines.append(
                        f"  Controlled by: {_par['name']}"
                        + (f" ({_par['registration_number']})" if _par.get("registration_number")
                           else " — no registration number; chain not walked")
                    )
                _chain = _sc.get("ownership_chain_traced") or []
                if len(_chain) > 1:
                    lines.append("  Chain traced: " + " → ".join(_chain))
                for _w in (_sc.get("warnings") or []):
                    lines.append(f"  {_w}")
                lines.append("")
        except Exception:
            pass  # scope is context, never a reason to lose the report

        # R-F2786 — put the USP contract next to the BLUF: five questions,
        # five explicit answers. This prevents a reader from mistaking a risk
        # colour for permission to transact when evidence coverage is incomplete.
        try:
            _dr = _dd_decision_readiness(self.as_dict())
            _dr_icon = "🟢" if _dr.get("clearance_ready") else "🟡"
            lines.append(
                f"*{_dr_icon} Decision Readiness: {_dr.get('status')} "
                f"({_dr.get('answered', 0)}/{_dr.get('required', 5)} — "
                f"{_dr.get('completion_pct', 0)}%)*"
            )
            _qcap = 2 if concise else 5
            for _q in list((_dr.get("questions") or {}).values())[:_qcap]:
                _answered = bool(_q.get("answered"))
                _mark = "✓" if _answered else "✗"
                _suffix = "" if _answered else f" — {_q.get('blocker', 'unresolved')}"
                lines.append(f"  {_mark} {_q.get('label', 'Check')}: {_q.get('status')}{_suffix}")
            lines.append(
                f"Reliance threshold: Evidence Grade {_dr.get('evidence_grade', 'INCOMPLETE')} "
                f"({'met' if _dr.get('evidence_ready') else 'not met'}); risk is reported separately."
            )
            lines.append("")
        except Exception:
            pass  # readiness rendering must never break report delivery

        def _sec_header(emoji: str, name: str, meta: SectionMeta) -> str:
            return f"━━━ {emoji} {name} [{_status_label(meta.status)}] ━━━"

        # 1. Identity
        _is_person = (self.identity.entity_type or "").lower() == "person"
        lines.append(_sec_header("👤" if _is_person else "🪪", "Identity", self.identity.meta))
        lines.append(f"{'Person' if _is_person else 'Entity'}: {self.identity.entity_name}  ·  Type: {self.identity.entity_type}")
        if self.identity.jurisdiction:
            lines.append(f"{'Nationality' if _is_person else 'Jurisdiction'}: {self.identity.jurisdiction}")
        if self.identity.declared_activity and _is_person:
            lines.append(f"Role: {self.identity.declared_activity}")
        if self.identity.registration_number and not _is_person:
            lines.append(f"Reg No: {self.identity.registration_number}  ·  Status: {self.identity.registration_status or '?'}")
        # R-F3021 — surface the LEI registration state on its own line. Only when
        # GLEIF actually returned one; a lapsed LEI is called out, an issued one is
        # stated plainly so the reader knows it WAS checked.
        # R-F3024 — former names, on the surface a human reads
        _prev = [x for x in (_format_previous_name(p) for p in (self.identity.previous_names or [])) if x]
        if _prev:
            lines.append(f"Former name(s): {'; '.join(_prev[:5])}")
        # R-F3026 — the directors and beneficial owners the scorecard already claims
        _dirs = [x for x in (_format_officer(o) for o in (self.identity.directors or [])) if x]
        if _dirs:
            lines.append(f"Directors / officers ({len(_dirs)}):")
            for _d in _dirs[:5 if concise else 25]:
                lines.append(f"  • {_d}")
            if len(_dirs) > (5 if concise else 25):
                lines.append(f"  … and {len(_dirs) - (5 if concise else 25)} more")
        _pscs = [x for x in (_format_psc(p) for p in (self.identity.shareholders or [])) if x]
        if _pscs:
            lines.append(f"Persons with significant control / shareholders ({len(_pscs)}):")
            for _p in _pscs[:5 if concise else 25]:
                lines.append(f"  • {_p}")
        _lei_reg = self.identity.lei_registration or {}
        if isinstance(_lei_reg, dict) and _lei_reg.get("lei"):
            _lei_note = (
                " — LAPSED: no longer GLEIF-validated (renewal not maintained); not a "
                "sanctions or solvency signal"
                if _lei_reg.get("lapsed") else ""
            )
            lines.append(
                f"LEI: {_lei_reg.get('lei')}  ·  Registration: "
                f"{_lei_reg.get('registration_status') or '?'}{_lei_note}"
            )
        if _is_person and self.identity.sanctions_screen:
            _vars = self.identity.sanctions_screen.get("variants_screened") or []
            if _vars:
                lines.append(f"Variants screened: {len(_vars)} — {', '.join(_vars[:5])}{'…' if len(_vars) > 5 else ''}")
        if self.identity.sanctions_screen:
            # Derive verdict from the screen result: match count + the
            # severity of any sanctions-derived finding emitted earlier.
            # The orchestrator always emits exactly one sanctions finding
            # (CLEAN / info / amber / red / hard_stop); fall back to the
            # match count for cases where the finding list is empty.
            _matches = self.identity.sanctions_screen.get("matches") or []
            _sanc_finding = next(
                (f for f in self.identity.findings if "sanctions" in (f.source or "").lower() or "Sanctions screen" in (f.title or "")),
                None,
            )
            if _sanc_finding:
                if _sanc_finding.severity == "hard_stop":
                    verdict = f"HIT — {_sanc_finding.title}"
                elif _sanc_finding.severity == "red":
                    verdict = f"HIT (crime/debarment) — {len(_matches)} match(es)"
                elif _sanc_finding.severity == "amber":
                    verdict = f"PEP / adverse-media — {len(_matches)} match(es)"
                elif _sanc_finding.severity == "info" and "CLEAN" in (_sanc_finding.title or ""):
                    verdict = "CLEAN ✅"
                elif _sanc_finding.severity == "info":
                    verdict = f"transparency register ({len(_matches)} match(es), informational)"
                else:
                    verdict = _sanc_finding.title
            elif self.identity.sanctions_screen.get("error"):
                verdict = f"ERROR — {self.identity.sanctions_screen.get('error')}"
            elif _matches:
                verdict = f"{len(_matches)} match(es) — see findings"
            else:
                verdict = "CLEAN ✅"
            lines.append(f"Sanctions screen: {verdict}")
        if self.identity.ghost_score and not _is_person:
            g = self.identity.ghost_score
            lines.append(f"Ghost score: {g.get('total','?')}/{g.get('max_total','20')} — {g.get('classification','?')}")
        for f in self.identity.findings[:6 if concise else 20]:
            lines.append(f"  • [{f.severity}] {f.title}")
        if self.identity.data_gaps:
            lines.append(f"  ⚠ Gaps: {', '.join(self.identity.data_gaps[:6])}")
        lines.append("")

        # 2. Network
        if self.network.meta.status != LayerStatus.SKIPPED.value:
            lines.append(_sec_header("🕸", "Network", self.network.meta))
            if self.network.cross_linked_entities:
                lines.append(f"Cross-linked entities: {len(self.network.cross_linked_entities)}")
            if self.network.pep_connections:
                lines.append(f"PEP connections: {len(self.network.pep_connections)}")
            if self.network.sanctions_network:
                lines.append(f"Flagged entities in chain: {len(self.network.sanctions_network)}")
            for f in self.network.findings[:5 if concise else 20]:
                lines.append(f"  • [{f.severity}] {f.title}")
            lines.append("")

        # 3. Verification
        lines.append(_sec_header("🧪", "Verification", self.verification.meta))
        if self.verification.grounded_rate is not None:
            lines.append(f"Grounded rate: {self.verification.grounded_rate:.0%}")
        if self.verification.conflicts:
            lines.append(f"Conflicts detected: {len(self.verification.conflicts)}")
        lines.append(f"Confidence floor: [{self.verification.confidence_floor}]")
        for f in self.verification.findings[:5 if concise else 20]:
            lines.append(f"  • [{f.severity}] {f.title}")
        lines.append("")

        # 4. Compliance
        lines.append(_sec_header("⚖", "Compliance", self.compliance.meta))
        if self.compliance.country_risk.get("headline_risk"):
            lines.append(f"Country risk: {self.compliance.country_risk.get('headline_risk')}")
        if self.compliance.export_control.get("recommendation"):
            lines.append(f"Export control: {self.compliance.export_control.get('recommendation')}")
        if self.compliance.sanctions_regimes:
            lines.append(f"Sanctions regimes implicated: {', '.join(self.compliance.sanctions_regimes[:6])}")
        if self.compliance.regional_bloc_requirements:
            lines.append(f"Regional bloc requirements: {len(self.compliance.regional_bloc_requirements)}")
        if self.compliance.licence_path:
            lines.append(f"Licence path: {self.compliance.licence_path}")
        # R-F3019 — NAME THE LISTS AND THE DATE. The screen already derives a
        # per-source verdict (`_sanctions_classify.derive_verified_sources`) and
        # R-F3019 stamps `screened_at`, but the Compliance section printed
        # neither — so a reader saw a sanctions verdict with no way to know which
        # regimes it covered or when. "Clean" without a list and a date is not a
        # compliance statement, it is an assertion. Renders what was measured;
        # computes nothing (R-F2837).
        for _sc_line in _render_screened_lists(self.identity.sanctions_screen):
            lines.append(_sc_line)
        # R-F3098 — subject findings first; environment-level context after, labelled.
        # A country debt statistic printed inline among compliance findings reads as a
        # signal about the counterparty, whatever its detail text says.
        _comp_subject = [f for f in self.compliance.findings if not getattr(f, "context_only", False)]
        _comp_context = [f for f in self.compliance.findings if getattr(f, "context_only", False)]
        for f in _comp_subject[:5 if concise else 20]:
            lines.append(f"  • [{f.severity}] {f.title}")
        if _comp_context and not concise:
            _kinds = sorted({(f.context_kind or "Context") for f in _comp_context})
            lines.append(f"  {' / '.join(_kinds)} — about the environment, not about this entity:")
            for f in _comp_context[:10]:
                lines.append(f"    · {f.title}")
        lines.append("")

        # 5. Digital
        if self.digital.meta.status != LayerStatus.SKIPPED.value:
            lines.append(_sec_header("🌐", "Digital", self.digital.meta))
            if self.digital.press_coverage:
                lines.append(f"Press coverage: {len(self.digital.press_coverage)} item(s)")
                # R-F1592: surface the actual source URLs so the downstream
                # source_verifier counts them as grounded citations. Without
                # this, render_markdown said only "12 item(s)" with no URLs, so
                # the verifier found 0 cited → the footer read "0 grounded /
                # NO_TOOL / from memory" on a DD that genuinely gathered press
                # (operator complaint 2026-06-15: "not bringing any results").
                for _p in self.digital.press_coverage[:5 if concise else 15]:
                    _u = getattr(_p, "url", None)
                    if not _u:
                        continue
                    _src = (getattr(_p, "source", "") or "").strip()
                    _tier = (getattr(_p, "source_tier", "") or "").strip()
                    _prefix = " ".join(x for x in (_src, f"[{_tier}]" if _tier else "") if x)
                    lines.append(f"  • {_prefix + ' ' if _prefix else ''}{_u}")
            if self.digital.source_tier_breakdown:
                parts = [f"{k}:{v}" for k, v in self.digital.source_tier_breakdown.items()]
                lines.append(f"Source tiers: {', '.join(parts)}")
            # R-F3060 — lead with the SO-WHAT. The detail lines below say what was
            # run; a reader had to assemble the concern and the next step themselves
            # out of a status, a count and a filter arithmetic.
            _am_sum = _adverse_media_summary(
                getattr(self, "adverse_media", None), self.digital.as_dict()
                if hasattr(self.digital, "as_dict") else None,
                entity_type=self.identity.entity_type)          # R-F3068
            lines.append(f"⚑ {_am_sum['headline']}")
            lines.append(f"   Concern: {_am_sum['concern']}")
            lines.append(f"   Advice:  {_am_sum['advice']}")
            # R-F3055 — the adverse-media screening, on the surface a human reads.
            # `adverse_media` is a TOP-LEVEL report key, which is exactly why every
            # renderer had been missing it.
            for _am_line in _render_adverse_media(getattr(self, "adverse_media", None),
                                                  entity_type=self.identity.entity_type):
                lines.append(_am_line)
            # Deep-research summary (deep_researcher.investigate)
            _wf = self.digital.web_footprint or {}
            if isinstance(_wf, dict):
                if _wf.get("summary"):
                    lines.append(f"Deep-research ({_wf.get('depth','quick')}, {_wf.get('articles_read',0)} articles, {_wf.get('facts_learned',0)} facts): {str(_wf.get('summary'))[:500]}")
                # Link-investigator tree walk (Phase 2 — deep mode only)
                _lt = _wf.get("link_tree") or {}
                if _lt:
                    _budget = " (budget exceeded)" if _lt.get("budget_exceeded") else ""
                    lines.append(
                        f"Link tree: seed={_lt.get('seed_url','?')} · "
                        f"{_lt.get('pages_fetched',0)} pages fetched "
                        f"(failed {_lt.get('pages_failed',0)}), depth "
                        f"{_lt.get('max_depth_reached',0)}, "
                        f"{_lt.get('fused_fact_count',0)} fused facts · "
                        f"{_lt.get('duration_ms',0)}ms{_budget}"
                    )
                    if _lt.get('tree_id'):
                        lines.append(f"  → full tree: /api/aria/research/link-tree/{_lt['tree_id']}")
            # R-F1816 — NAMED INDIVIDUALS investigated (deep_researcher recursive
            # person drill-down). Without this the people we now investigate
            # (PEP/sanctions-proximity/adverse-media) never reached WA or web —
            # the operator's "Zero named individuals" complaint.
            if self.digital.people:
                lines.append(f"People investigated: {len(self.digital.people)}")
                for _pp in self.digital.people[:3 if concise else 10]:
                    if not isinstance(_pp, dict):
                        continue
                    _name = _pp.get("name", "?")
                    _role = _pp.get("role") or ""
                    _dos = _pp.get("dossier") or {}
                    _risk = _dos.get("risk_assessment", "?") if isinstance(_dos, dict) else "?"
                    _pep = _dos.get("pep_status", "") if isinstance(_dos, dict) else ""
                    _flags = "; ".join((_dos.get("red_flags") or [])[:2]) if isinstance(_dos, dict) else ""
                    _bits = [b for b in (
                        f"role: {_role}" if _role else "",
                        f"risk={_risk}",
                        f"PEP: {_pep}" if _pep else "",
                        f"flags: {_flags}" if _flags else "",
                    ) if b]
                    lines.append(f"  • {_name} — {', '.join(_bits)}")
            for f in self.digital.findings[:5 if concise else 20]:
                lines.append(f"  • [{f.severity}] {f.title}")
            lines.append("")

        # 5c. Commercial coherence (only render if the layer actually ran)
        if self.commercial_coherence.meta.status not in (
            LayerStatus.SKIPPED.value, ""
        ) and (
            self.commercial_coherence.anomalies
            or self.commercial_coherence.licence_chain_gaps
            or self.commercial_coherence.payment_anomalies
            or self.commercial_coherence.corporate_anomalies
            or self.commercial_coherence.offset_issues
            or self.commercial_coherence.coherence_score < 1.0
        ):
            lines.append(_sec_header("🧾", "Commercial Coherence", self.commercial_coherence.meta))
            lines.append(
                f"Coherence score: {self.commercial_coherence.coherence_score:.2f} "
                f"({self.commercial_coherence.tier})"
            )
            if self.commercial_coherence.assessment_summary:
                lines.append(self.commercial_coherence.assessment_summary)
            if self.commercial_coherence.licence_chain_gaps:
                lines.append(f"Licence chain gaps: {len(self.commercial_coherence.licence_chain_gaps)}")
                for g in self.commercial_coherence.licence_chain_gaps[:3 if concise else 8]:
                    _step = g.get("missing_step", "step")
                    _auth = g.get("authority", "")
                    lines.append(f"  • {_step}{f' — {_auth}' if _auth else ''}")
            if self.commercial_coherence.payment_anomalies:
                for p in self.commercial_coherence.payment_anomalies[:3 if concise else 8]:
                    lines.append(
                        f"  • Payment anomaly ({p.get('market','?')}): "
                        f"{p.get('kind','?')} — claimed {p.get('claimed','?')}, "
                        f"norm {p.get('norm_range','?')}"
                    )
            if self.commercial_coherence.corporate_anomalies:
                for c in self.commercial_coherence.corporate_anomalies[:3 if concise else 8]:
                    lines.append(f"  • Corporate: {c.get('detail', c.get('kind','anomaly'))}")
            if self.commercial_coherence.offset_issues:
                for o in self.commercial_coherence.offset_issues[:3 if concise else 8]:
                    lines.append(f"  • Offset ({o.get('regime','?')}): {o.get('issue','')}")
            if self.commercial_coherence.jurisdiction_flags:
                for j in self.commercial_coherence.jurisdiction_flags[:3 if concise else 8]:
                    lines.append(f"  • Jurisdiction: {j}")
            for f in self.commercial_coherence.findings[:5 if concise else 20]:
                lines.append(f"  • [{f.severity}] {f.title}")
            lines.append("")

        # 8. Counter-intelligence (R-F121, attached as instance attribute)
        _ci = getattr(self, "counter_intelligence", None)
        if isinstance(_ci, dict) and _ci.get("composite_score", 0) >= 0.3:
            lines.append("━━━ 🛡 Counter-intelligence [COMPLETED] ━━━")  # R-F3020
            _patterns = _ci.get("patterns") or {}
            lines.append(
                f"Composite score: {_ci.get('composite_score', 0):.2f} "
                f"· n_signals: {_ci.get('n_signals', 0)} "
                f"· window: {_ci.get('window_days', '?')}d"
            )
            if _patterns:
                _top = sorted(_patterns.items(), key=lambda kv: kv[1], reverse=True)[:3]
                lines.append("Patterns: " + ", ".join(
                    f"{k}={v:.2f}" if isinstance(v, (int, float)) else f"{k}={v}"
                    for k, v in _top
                ))
            if _ci.get("narrative"):
                lines.append(f"  • {str(_ci['narrative'])[:400]}")
            lines.append("")

        # 9. Sanctions divergence (R-F122, attached as instance attribute)
        _sdiv = getattr(self, "sanctions_divergence", None)
        if (
            isinstance(_sdiv, dict)
            and _sdiv.get("matches", 0) > 0
            and _sdiv.get("divergence_count", 0) >= 1
        ):
            lines.append("━━━ ⚖ Sanctions Divergence [COMPLETED] ━━━")  # R-F3020
            _listed = _sdiv.get("jurisdictions_listed") or []
            _silent = _sdiv.get("jurisdictions_not_listed") or []
            lines.append(f"Listed by: {', '.join(_listed) if _listed else '(none)'}")
            lines.append(f"Silent on: {', '.join(_silent) if _silent else '(none)'}")
            if _sdiv.get("narrative"):
                lines.append(f"  • {str(_sdiv['narrative'])[:400]}")
            lines.append("")

        # 10. Forensic (Benford + TBML — R-F123, attached as instance attribute)
        _fo = getattr(self, "forensic", None)
        if isinstance(_fo, dict) and _fo:
            lines.append("━━━ 🔬 Forensic [COMPLETED] ━━━")  # R-F3020
            _benf = _fo.get("benford") or {}
            if _benf:
                lines.append(
                    f"Benford: n={_benf.get('n','?')}, "
                    f"χ²={_benf.get('chi_square','?')}, "
                    f"p={_benf.get('p_value','?')} → "
                    f"{_benf.get('tier','?')}"
                )
                if _benf.get("narrative"):
                    lines.append(f"  • {str(_benf['narrative'])[:400]}")
            _tbml = _fo.get("tbml") or {}
            if _tbml:
                # R-F2496 — never-false-clean render. Distinguish trade-flow
                # benchmark UNAVAILABLE (COMTRADE source/key down -> NOT screened)
                # from actually-screened-clean; count anomalies by grade.
                _cov = str(_tbml.get("coverage") or "")
                _scr = int(_tbml.get("transactions_screened", _tbml.get("transactions_analysed", 0)) or 0)
                _ind = int(_tbml.get("transactions_indeterminate", 0) or 0)
                _mat = int(_tbml.get("material_anomalies", _tbml.get("high_anomalies", 0)) or 0)
                if _cov == "unavailable" or (_scr == 0 and _ind > 0):
                    lines.append(
                        f"TBML: trade-flow benchmark UNAVAILABLE ({_ind} txn(s) indeterminate — "
                        f"COMTRADE source/key not reachable). NOT screened — no clean or anomaly claim."
                    )
                else:
                    _tail = f" · {_ind} indeterminate" if _ind else ""
                    lines.append(
                        f"TBML: {_scr} txn(s) screened, {_mat} material anomaly(ies){_tail}"
                    )
            lines.append("")

        # 6. Synthesis
        lines.append(_sec_header("🧠", "Synthesis", self.synthesis.meta))
        if self.synthesis.ghost_classification and not _is_person:
            lines.append(f"Ghost score (authoritative): {self.synthesis.ghost_score_total}/20 — {self.synthesis.ghost_classification}")
        lines.append(f"Risk classification: {self.synthesis.risk_classification}")
        if self.synthesis.sar_trigger:
            lines.append(f"⚠ SAR trigger: {self.synthesis.sar_rationale or 'threshold met'}")
        if self.synthesis.key_findings:
            lines.append("Key findings:")
            for f in self.synthesis.key_findings[:5 if concise else 20]:
                lines.append(f"  • [{f.severity}] {f.title}")
        if self.synthesis.residual_unknowns:
            lines.append("Residual unknowns:")
            for u in self.synthesis.residual_unknowns[:4 if concise else 10]:
                lines.append(f"  • {u}")
        lines.append("")

        # Next actions
        if self.next_actions:
            lines.append("*Next actions:*")
            for i, a in enumerate(self.next_actions, 1):
                lines.append(f"  {i}. {a}")
            lines.append("")

        # Footer
        lines.append(
            f"─────\n"
            f"*Confidence:* [{self.confidence_tag}]  ·  "
            f"*Layers run:* {', '.join(self.layers_run)}  ·  "
            f"*Cost:* ${self.total_cost_usd:.4f}  ·  "
            f"*Duration:* {self.total_duration_ms}ms  ·  "
            f"*run_id:* {self.run_id}"
        )
        # R-F996 — wire to brain
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="dd_schema",
            summary="Render Markdown",
            source_id="dd_schema:R-F996",
        )

        return "\n".join(lines)


# =============================================================================
# HELPER — compute weakest confidence floor across a report
# =============================================================================

_CONFIDENCE_RANK = {
    "SPECULATIVE": 0,
    "UNCERTAIN":   1,
    "ASSESSED":    2,
    "PROBABLE":    3,
    "CONFIRMED":   4,
}


@fail_wire(module="dd_schema", gap_type="engine_failure")
def weakest_confidence(tags: list[str]) -> str:
    """Return the weakest confidence tag from a list. Mirrors
    confidence_footer._dominant_tag — the headline never oversells."""
    ranked = [t for t in tags if t in _CONFIDENCE_RANK]
    if not ranked:
        return "ASSESSED"
    return min(ranked, key=lambda t: _CONFIDENCE_RANK[t])

# ── R-F2331: render-ready STRUCTURED view ────────────────────────────────────
# The web report page used to re-parse the WhatsApp markdown (render_markdown) back
# into cards, silently dropping each finding's detail / source / confidence / citations
# and breaking whenever the prose wording changed. structured_view() emits a stable,
# typed, DECISION-FIRST contract straight from the persisted report dict so the frontend
# is a dumb renderer over real structured data — evidence, sources and confidence kept.

_SEVERITY_RANK = {"hard_stop": 0, "red": 1, "amber": 2, "amber_dark": 2,
                  "amber_light": 3, "info": 4}


def _sv_finding(f: dict) -> dict:
    """Normalise one persisted finding dict into the render contract (loses nothing)."""
    if not isinstance(f, dict):
        return {}
    src = (f.get("source") or "").strip()
    sources = [s for s in (f.get("sources") or []) if s] or ([src] if src else [])
    return {
        "severity": (f.get("severity") or "info").lower(),
        "title": f.get("title") or "",
        "detail": f.get("detail") or "",
        "source": src,
        "sources": sources,
        "confidence": f.get("confidence") or "",
        "gate_demoted": bool(f.get("gate_demoted")),
        "gate_reason": f.get("gate_reason") or "",
        # R-F3098 — about the ENVIRONMENT, not about the subject.
        "context_only": bool(f.get("context_only")),
        "context_kind": f.get("context_kind") or "",
    }


def _sv_findings(section: dict) -> list[dict]:
    """Findings ABOUT THE SUBJECT, worst first.

    R-F3098 — context findings are split out by `_sv_context_findings` and rendered
    under their own heading. They are never dropped: a reader loses nothing, and the
    decision-driving section stops carrying material that is not decision-driving.
    """
    out = [_sv_finding(f) for f in (section.get("findings") or []) if f]
    out = [f for f in out if f.get("title") and not f.get("context_only")]
    out.sort(key=lambda f: _SEVERITY_RANK.get(f.get("severity", "info"), 5))
    return out


def _sv_context_findings(section: dict) -> list[dict]:
    """R-F3098 — the environment-level items this layer collected.

    Country statistics, market/procurement footprint: true, sourced, worth showing,
    and NOT a statement about the counterparty. Kept in the same section object so a
    surface that ignores the split still renders everything it did before."""
    out = [_sv_finding(f) for f in (section.get("findings") or []) if f]
    out = [f for f in out if f.get("title") and f.get("context_only")]
    out.sort(key=lambda f: (f.get("context_kind") or "", f.get("title") or ""))
    return out


# ── R-F3091 — WHICH ENTITY IS THIS REPORT ABOUT? ────────────────────────────
#
# THE DEFECT (Mitie, operator report 2026-07-26). The registry layer described
# MITIE FACILITIES MANAGEMENT LIMITED (02938041) — a subsidiary whose PSC is Mitie
# Treasury Management Limited. Every other layer described Mitie Group PLC, the
# listed parent: the press coverage (OCS's £3.1bn recommended acquisition), the
# financial references (mitie.com annual reports), the USAspending awards, the
# Deloitte audit fine. The report never said which entity any layer was about, so a
# reader had one company name at the top and five companies' worth of evidence
# below it, silently blended.
#
# THE PRINCIPLE. A layer resolved by REGISTRATION NUMBER describes one exact legal
# person. A layer resolved by NAME SEARCH describes a BRAND, and for a subsidiary
# the brand's coverage is overwhelmingly the group's. Those are different claims
# and must be labelled as different claims. This never silently re-points the
# report at the parent — it states the scope and flags the mismatch, because
# quietly swapping the subject would be its own fabrication.
#: Layers whose evidence is resolved by registry identifier — one exact legal entity.
_SCOPE_REGISTRY_LAYERS = {"identity", "network"}
#: Layers whose evidence is resolved by NAME SEARCH — brand/group scope.
_SCOPE_SEARCH_LAYERS = {"digital", "commercial", "sweep"}


def _dd_entity_scope(r: dict) -> dict:
    """R-F3091 — the report's entity scope: the registry subject, its corporate
    parent(s) where the registry anchored them, and what each layer is ABOUT.

    Pure and derived entirely from the persisted blob — it asserts no relationship
    the registry did not record."""
    r = r or {}
    ident = r.get("identity") or {}
    net = r.get("network") or {}

    subject = str(ident.get("entity_name") or "").strip()
    is_person = str(ident.get("entity_type") or "").strip().lower() == "person"

    # Corporate controllers, anchored (registry number) first, then disclosed-only.
    anchored, unanchored = [], []
    for c in (net.get("controlled_by") or []):
        if isinstance(c, dict) and c.get("controller_name"):
            anchored.append({
                "name": str(c["controller_name"]).strip(),
                "registration_number": str(c.get("controller_registration_number") or "") or None,
                "country": str(c.get("controller_country_registered") or "") or None,
                "anchored": True,
            })
    for c in (net.get("controlled_by_unanchored") or []):
        if isinstance(c, dict) and c.get("controller_name"):
            unanchored.append({
                "name": str(c["controller_name"]).strip(),
                "registration_number": None, "country": None, "anchored": False,
            })
    # A corporate PSC on the identity layer is the same signal by another route.
    for p in (ident.get("shareholders") or []):
        if not isinstance(p, dict):
            continue
        _kind = str(p.get("kind") or p.get("type") or "").lower()
        _nm = str(p.get("name") or "").strip()
        if not _nm or "corporate" not in _kind:
            continue
        if not any(_nm.lower() == c["name"].lower() for c in anchored + unanchored):
            (anchored if p.get("registration_number") else unanchored).append({
                "name": _nm,
                "registration_number": str(p.get("registration_number") or "") or None,
                "country": str(p.get("country_registered") or "") or None,
                "anchored": bool(p.get("registration_number")),
            })

    controllers = anchored + unanchored
    is_subsidiary = bool(controllers) and not is_person
    immediate_parent = controllers[0] if controllers else None

    # The deepest node the walker actually reached. NOT asserted to be the ultimate
    # parent — only that the walk got there and stopped.
    chain = [str(u.get("name") or "").strip()
             for u in (net.get("ubo_chain") or []) if isinstance(u, dict) and u.get("name")]
    deepest_traced = chain[-1] if chain else None

    warnings: list[str] = []
    if is_subsidiary:
        _p = immediate_parent["name"] if immediate_parent else "a corporate controller"
        warnings.append(
            f"SCOPE: the registry subject is {subject or 'this entity'}"
            + (f" ({ident.get('registration_number')})" if ident.get("registration_number") else "")
            + f", a subsidiary controlled by {_p}. Findings resolved by registration "
              "number describe THIS legal entity; findings resolved by name search "
              "(press, adverse media, published financials, procurement awards) "
              "describe the BRAND and will usually be about the wider group. Do not "
              "read group-level coverage as a statement about this subsidiary, or "
              "vice versa."
        )
    if is_subsidiary and not any(c["anchored"] for c in controllers):
        warnings.append(
            "The corporate controller carries no registration number, so the chain "
            "above this entity was NOT walked — the ultimate parent is UNKNOWN.")

    layers = []
    for key, title in (("identity", "Identity"), ("compliance", "Compliance & Sanctions"),
                       ("network", "Network & Ownership"), ("digital", "Digital & Adverse Media"),
                       ("commercial", "Commercial Coherence"), ("sweep", "Live Sweep Signals")):
        if key in _SCOPE_REGISTRY_LAYERS:
            basis, scope = "registry identifier", subject or "the registry subject"
        elif key in _SCOPE_SEARCH_LAYERS:
            basis = "name search"
            scope = (f"{subject} and/or its group" if is_subsidiary
                     else subject or "the named entity")
        else:
            # Compliance is MIXED: sanctions screens by name+aliases, financials and
            # procurement by name, country risk by jurisdiction. Say so rather than
            # picking one and being wrong about the rest.
            basis = "mixed (screens by name + aliases; financials/awards by name)"
            scope = (f"{subject} and/or its group" if is_subsidiary
                     else subject or "the named entity")
        layers.append({"key": key, "title": title, "basis": basis, "scope": scope,
                       "group_scope": bool(is_subsidiary and key not in _SCOPE_REGISTRY_LAYERS)})

    return {
        "subject_name": subject,
        "subject_registration": ident.get("registration_number"),
        "subject_jurisdiction": ident.get("jurisdiction"),
        "entity_type": ident.get("entity_type"),
        "is_subsidiary": is_subsidiary,
        "immediate_parent": immediate_parent,
        "controllers": controllers,
        "ownership_chain_traced": chain,
        "deepest_node_traced": deepest_traced,
        "layers": layers,
        "warnings": warnings,
    }


def _sanctions_match_metric(screen) -> str | None:
    """R-F3090 — the sanctions chip, rendered from the CLASSIFIED result.

    `classify_matches` splits real hits from name-overlap noise (an unrelated entity
    that shares only a generic token). The structured view used to print
    `len(screen["matches"])`, so a screen whose every hit was noise rendered
    "Sanctions matches 2" immediately above its own finding saying "No real hits."

    Returns None when nothing was screened (no chip beats a misleading 0), and states
    the arithmetic when anything was filtered — a dropped match that is never
    accounted for is indistinguishable from a match never found."""
    if not isinstance(screen, dict) or not screen:
        return None
    cls = screen.get("match_classification")
    if not isinstance(cls, dict):
        # Legacy blob written before the classification was persisted. Do NOT
        # silently fall back to the raw count that this fix exists to remove —
        # label it for what it is.
        raw = len(screen.get("matches") or [])
        return f"{raw} raw (unclassified)" if raw else None
    actionable = int(cls.get("actionable") or 0)
    noise = int(cls.get("noise_filtered") or 0)
    if not actionable:
        return (f"none — {noise} name-overlap match(es) filtered" if noise
                else "none")
    return (f"{actionable}" + (f" (+{noise} filtered as name-overlap noise)"
                               if noise else ""))


def _sv_section(key, title, icon, section, highlights, *, kind="standard", evidence=None):
    """Assemble one render-ready section. Returns None when a non-core section has no content."""
    section = section or {}
    meta = section.get("meta") or {}
    findings = _sv_findings(section)
    context = _sv_context_findings(section)          # R-F3098
    gaps = [g for g in (section.get("data_gaps") or []) if g]
    hl = [{"label": l, "value": (str(v) if not isinstance(v, str) else v)}
          for (l, v) in highlights if v not in (None, "", [], {}, 0)]
    ev = evidence or []
    # R-F3098 — a section carrying ONLY context still has something to show, so it
    # must not be dropped as empty.
    if not (findings or context or gaps or hl or ev) and kind != "core":
        return None
    return {
        "key": key, "title": title, "icon": icon,
        "status": (meta.get("status") or "ok"),
        "duration_ms": meta.get("duration_ms") or 0,
        "subcalls": meta.get("subcalls") or 0,
        "error": meta.get("error"),
        "highlights": hl, "findings": findings, "data_gaps": gaps, "evidence": ev,
        "context_findings": context,
    }


def _quality_metrics(r: dict) -> dict:
    """Extract evidence-depth metrics from a persisted DD report dict."""
    ident = (r or {}).get("identity") or {}
    comp = (r or {}).get("compliance") or {}
    dig = (r or {}).get("digital") or {}
    ver = (r or {}).get("verification") or {}
    adverse = (r or {}).get("adverse_media") or {}
    tier_breakdown = dig.get("source_tier_breakdown") or {}
    press_total = len(dig.get("press_coverage") or [])
    verified_sources = int(tier_breakdown.get("T1", 0)) + int(tier_breakdown.get("T2", 0))
    quality_press = int(tier_breakdown.get("T3", 0))
    own_site = int(tier_breakdown.get("ENTITY_SITE", 0))
    memory_only = int(tier_breakdown.get("MEMORY_ONLY", 0))
    unverified = max(
        0,
        sum(int(v) for v in tier_breakdown.values())
        - verified_sources - quality_press - own_site - memory_only,
    )

    citations_checked = int(ver.get("citations_checked") or 0)
    citations_grounded = int(ver.get("citations_grounded") or 0)
    citation_rate = ver.get("citation_grounding_rate")
    if not isinstance(citation_rate, (int, float)) and citations_checked:
        citation_rate = citations_grounded / max(citations_checked, 1)
    adverse_findings = int(adverse.get("findings_count") or len(adverse.get("findings") or []) or 0)
    adverse_templates_run = int(adverse.get("templates_run") or 0)
    # R-F2791 — `templates_run` counts templates ENTERED, not SEARCHED: researcher
    # incremented its counter before issuing the call, so a sweep in which every
    # backend call failed still reported 30/30. Combined with R-F2786 dropping the
    # zero-finding penalty, that scored a total backend failure IDENTICALLY to a
    # clean screening — a false clean, the one thing this system must never emit.
    # `templates_searched` counts templates that actually reached a backend.
    adverse_templates_searched = int(adverse.get("templates_searched") or 0)
    if not adverse_templates_searched and adverse_findings > 0:
        # Legacy blobs predate both counters. Real findings PROVE a backend
        # answered; zero findings prove nothing and must not self-certify.
        adverse_templates_searched = 1
    # Zero findings is negative EVIDENCE only where the search infrastructure is
    # shown to have been working. Findings are self-proving; otherwise we require
    # the recorded backend health. Unknown health (None, e.g. a legacy report)
    # fails CLOSED — "we cannot show the search worked" is not "the entity is clean".
    adverse_backends_answered = adverse.get("search_backends_answered")
    adverse_evidence_ok = adverse_findings > 0 or adverse_backends_answered is True
    adverse_run = bool(
        adverse
        and adverse.get("ok") is True
        and adverse_templates_searched > 0
        and adverse_evidence_ok
        and not adverse.get("partial")
        and not adverse.get("timed_out")
    )
    # R-F2657 — the adverse-media search now runs as an OUT-OF-BAND follow-up; while it is
    # still deferred (status=="in_progress") it has NOT run yet. Treat pending as NOT-RUN
    # for grading, so a triggered target cannot reach Grade A on adverse-media grounds
    # until the follow-up actually merges real findings (and a follow-up lost on restart
    # stays honestly penalised, never over-graded).
    _adverse_pending = bool(adverse.get("status") == "in_progress") if isinstance(adverse, dict) else False
    adverse_skipped = (
        (not adverse)
        or _adverse_pending
        or not adverse_run
        or bool(adverse.get("skipped") or adverse.get("error"))
    )
    sanctions_screen = ident.get("sanctions_screen") or {}
    sanctions_unavailable = bool(
        sanctions_screen.get("source_unavailable")
        or sanctions_screen.get("error") == "sanctions_source_unavailable"
    )
    export_control = comp.get("export_control") or {}
    export_checked = bool(
        export_control.get("recommendation")
        or export_control.get("classification")
        or export_control.get("findings")
        or comp.get("sanctions_regimes")
    )
    # A supplied registration number is only an identifier, not authority. Treat
    # identity as authority-backed only when an independent source confirmed
    # registry substance or a sanctions source was actually verified.
    #
    # R-F2693 — "unknown" is a STRING, and `bool("unknown")` is True. The registry
    # stub adapters (angola_gue_stub, kenya_brs_stub, saudi_moci_stub, …) return
    # company_status="unknown", which dd_orchestrator copies into
    # registration_status — so a lookup that established NOTHING, whose own
    # data_gaps say "no public registry API, recommend manual verification", read as
    # registry substance and skipped the 25-point no-identity-authority penalty.
    # Substance means a value that SAYS something; a placeholder does not.
    _NON_SUBSTANTIVE = {"", "unknown", "n/a", "na", "none", "not available", "unavailable"}

    def _substantive(v) -> bool:
        return bool(v) and str(v).strip().lower() not in _NON_SUBSTANTIVE

    registry_substance = bool(
        _substantive(ident.get("registration_status"))
        or _substantive(ident.get("incorporation_date"))
        or ident.get("directors")
        or ident.get("shareholders")
    )
    # R-F2693 — and even substantive-LOOKING fields are not authority when they came
    # from a stub/fallback. Only VERIFIED/PARTIAL is a registry actually answering.
    # ABSENT (legacy reports predate this field) is NOT treated as a stub: absence of
    # evidence is not evidence of a stub, and defaulting the other way would demote
    # every persisted report at once.
    from .registry_adapters import RegistryStatus as _RegStatus

    _reg_status = _RegStatus.coerce(ident.get("registry_status"))
    _registry_is_authority = registry_substance and (
        _reg_status is None or _reg_status.is_authority()
    )
    identity_authority = bool(
        _registry_is_authority or sanctions_screen.get("verified_sources")
    )
    # R-F2658 — did the identity/registry layer actually RUN, or did it error / get
    # clamped under load? A missing registry substance means "genuinely thin entity"
    # ONLY when the layer ran cleanly (status ok/partial). If it errored / was skipped /
    # its prerequisites failed, the emptiness is a FAILED CHECK, not a thin entity — and
    # the grade must say so (never-false-clean) instead of a bare Grade D. Read the
    # LayerStatus already persisted on the section meta (dd_schema LayerStatus enum).
    _ident_status = str(((ident.get("meta") or {}).get("status") or "ok")).lower()
    _comp_status = str(((comp.get("meta") or {}).get("status") or "ok")).lower()
    _incomplete_states = ("error", "skipped", "prereq_fail")
    registry_incomplete = (
        not registry_substance
        and (_ident_status in _incomplete_states or _comp_status in _incomplete_states)
    )
    return {
        "press_total": press_total,
        "verified_sources": verified_sources,
        "quality_press": quality_press,
        "unverified_sources": unverified,
        "own_site_sources": own_site,
        "memory_only_sources": memory_only,
        "citations_checked": citations_checked,
        "citations_grounded": citations_grounded,
        "citation_grounding_rate": citation_rate,
        "adverse_media_run": adverse_run,
        "adverse_media_templates_run": adverse_templates_run,
        "adverse_media_templates_searched": adverse_templates_searched,  # R-F2791
        "adverse_media_backends_answered": adverse_backends_answered,      # R-F2791
        "adverse_media_findings": adverse_findings,
        "adverse_media_skipped": adverse_skipped,
        "has_search_degradation_gap": _quality_has_search_gap(r),
        "registry_substance_present": registry_substance,
        "identity_authority_present": identity_authority,
        "sanctions_source_unavailable": sanctions_unavailable,
        "export_control_checked": export_checked,
        "confidence_gate_triggered": bool((r or {}).get("confidence_gate_triggered")),
        "registry_incomplete": registry_incomplete,
    }


def _quality_has_search_gap(r: dict) -> bool:
    """Return True when the report admits live-search degradation."""
    dig = (r or {}).get("digital") or {}
    digital_gaps = [str(g) for g in (dig.get("data_gaps") or []) if g]
    summary_gaps = [str(g) for g in ((r or {}).get("data_gaps_summary") or []) if g]
    severe_gap_text = " ".join(digital_gaps + summary_gaps).lower()
    return any(token in severe_gap_text for token in (
        "search unavailable", "web returned 0", "coverage gap",
        "rag memory only", "blocked or silent",
    ))


def _quality_grade(score: int, blockers: list[str]) -> str:
    """Map an evidence-depth score to an operator-facing grade."""
    if score >= 85 and not blockers:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    return "D"


# R-F3013 — the evidence grade is a RELIANCE grade. The evidence-DEPTH scorer can
# reach A/B off enriched (GLEIF/vault) identity while the decision-readiness
# scorecard leaves decision-critical questions UNRESOLVED — so a report read
# "3 of 5 answered | evidence grade A", which is incoherent (Grade A means
# decision-ready). Cap the grade by how many of the five questions are answered.
_GRADE_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3}


def _readiness_grade_cap(answered: int, required: int) -> str:
    """Highest grade the answered decision-critical questions can support.
    All answered → A; one gap → B; more → C. (D is never a cap — it can only be
    reached by the depth score, never forced up.)"""
    missing = max(0, int(required) - int(answered))
    if missing <= 0:
        return "A"
    if missing == 1:
        return "B"
    return "C"


def _cap_grade(grade: str, cap: str) -> str:
    """Return grade lowered to `cap` if it exceeds it; NEVER raises a grade.
    Non-letter grades (INCOMPLETE / unknown) are left untouched."""
    if grade not in _GRADE_ORDER or cap not in _GRADE_ORDER:
        return grade
    return grade if _GRADE_ORDER[grade] <= _GRADE_ORDER[cap] else cap


def _quality_penalties(metrics: dict) -> list[tuple[int, str]]:
    """Return score penalties and reasons for DD evidence-depth weaknesses."""
    reputable = metrics["verified_sources"] + metrics["quality_press"]
    citation_rate = metrics["citation_grounding_rate"]
    # ── R-F3132 — A 2-URL SAMPLE IS NOT A GROUNDING RATE ────────────────────
    #
    # LIVE on the Babcock DD (dd_8c7242c2b45b, 2026-07-26):
    #     "Cited URLs that resolve: 0 of 2 cited links checked and reachable"
    #     "Quality blocked by: ... citation grounding rate below 80% (0%)"  -> Grade C
    # while the SAME report listed FIFTEEN cited sources.
    #
    # `citations_checked` does not count the report's citations. It counts URLs that
    # happened to appear INLINE in prose `detail` text — here a GLEIF search link and
    # an OpenSanctions verify link, neither of which is in the evidence blob. The
    # check is `source_verifier.verify_response`, built for a CHAT answer that cites
    # URLs in its prose; a DD cites through structured evidence instead, so the
    # sample is incidental to how a finding happened to be worded.
    #
    # Penalising the evidence grade on a 2-item incidental sample is measuring
    # formatting, not evidence. And "0 of 2" is not a measured failure — it is a
    # sample too small to support any rate at all. This is the phase-gate rule
    # (R-F2639) applied to a quality metric: COULD NOT MEASURE is not MEASURED AND
    # FAILED, and it must never be rendered as a score of zero.
    #
    # The check still runs and is still reported — a genuinely ungrounded set of
    # prose citations is a real signal — but it cannot move the grade until the
    # sample can carry the claim.
    _MIN_CITATION_SAMPLE = 3
    low_citation_rate = (
        metrics["citations_checked"] >= _MIN_CITATION_SAMPLE
        and isinstance(citation_rate, (int, float))
        and citation_rate < 0.8
    )
    citation_rate_text = f"{citation_rate:.0%}" if isinstance(citation_rate, (int, float)) else "unknown"
    candidates = [
        (metrics["press_total"] < 8, 20,
         f"only {metrics['press_total']} cited press/source item(s)"),
        (reputable < 5, 20,
         f"only {reputable} reputable independent source(s)"),
        (not metrics["identity_authority_present"], 25,
         "no Tier-1 identity authority, verified registry substance, or sanctions verification source present"),
        (metrics["sanctions_source_unavailable"], 25,
         "sanctions screen source was unavailable or stale"),
        (not metrics["export_control_checked"], 15,
         "export-control or sanctions-regime check is not evidenced"),
        (metrics["unverified_sources"] > reputable, 10,
         "unverified sources outnumber reputable independent sources"),
        (metrics["own_site_sources"] and reputable == 0, 15,
         "evidence is own-site/self-reported without independent corroboration"),
        # R-F3183 — this penalty names the R-F188 DEGRADED-SEARCH case: live web
        # returned nothing and the digital layer was served wholesale from RAG
        # (dd_orchestrator sets press_coverage = memory_press and flags
        # degraded_search). Firing it on ANY non-zero count was safe only while
        # memory:// hits were mis-tiered as UNVERIFIED and this counter sat at 0.
        # Now that they are classified truthfully, a single incidental self-citation
        # among live results would trigger a 15-point penalty written for total
        # search failure — over-stating the defect as surely as hiding it understated
        # it. Fire when memory-only evidence is at least HALF the pool, i.e. the
        # report genuinely rests on ARIA's own memory. The wholesale R-F188 case
        # (memory_only == press_total) still trips it.
        (metrics["memory_only_sources"]
         and metrics["memory_only_sources"] * 2 >= max(metrics["press_total"], 1), 15,
         "live web returned memory-only evidence"),
        (metrics["citations_checked"] == 0, 20,
         "no citations were grounded by source verifier"),
        (low_citation_rate, 15,
         f"citation grounding rate below 80% ({citation_rate_text})"),
        (metrics["adverse_media_skipped"], 15,
         "structured adverse-media deep search did not run"),
        (metrics["has_search_degradation_gap"], 20,
         "report contains explicit search/coverage degradation gaps"),
        (metrics["confidence_gate_triggered"], 25,
         "confidence gate triggered — evidence is insufficient for Grade A"),
    ]
    return [(points, reason) for active, points, reason in candidates if active]


def _dd_quality_assessment(r: dict) -> dict:
    """Assess whether a persisted DD report has Grade-A evidence depth.

    This is an evidence-sufficiency grade, not the risk classification. A GREEN
    report can still be low-grade if it rests on sparse, ungrounded, or
    self-reported evidence.
    """
    metrics = _quality_metrics(r or {})
    penalties = _quality_penalties(metrics)
    blockers = [reason for _, reason in penalties]
    score = 100 - sum(points for points, _ in penalties)
    score = max(0, min(100, score))
    # R-F2493 — HARD CAP: a report whose own verdict is INSUFFICIENT EVIDENCE (the
    # confidence gate fired — via the GREEN confidence gate OR a data-starved AMBER,
    # see dd_orchestrator _data_starved) can NEVER carry a high evidence grade. The
    # additive penalties alone could still land at C/60 when unrelated signals are
    # present; the gate means "evidence is insufficient", so the evidence-depth grade
    # must reflect that. Cap below the C floor (50) → Grade D.
    if metrics.get("confidence_gate_triggered"):
        score = min(score, 40)
    grade = _quality_grade(score, blockers)
    # R-F2658 — never-false-clean on the GRADE. A Grade D means "we ran the identity /
    # registry checks and the evidence is genuinely thin". If instead that layer ERRORED
    # or was CLAMPED under load (registry_incomplete), the low grade reflects a FAILED
    # CHECK, not a thin entity — so a real company is not mislabelled Grade D purely from
    # timing. Relabel the GRADE ONLY to INCOMPLETE; confidence_gate_triggered, the AMBER
    # verdict bump, and the R-F409 re-run all stay untouched (an errored registry SHOULD
    # still bump to AMBER and SHOULD trigger a re-run). Bounded to the low grades where
    # the incomplete registry is what pinned the score.
    if metrics.get("registry_incomplete") and grade in ("D", "C"):
        grade = "INCOMPLETE"
        blockers = [
            "identity/registry check did not complete (errored or clamped) — grade "
            "WITHHELD, not a thin-evidence verdict; re-run may resolve"
        ] + blockers
    # R-F3013 — cap the reliance grade by decision-readiness completeness WHEN the
    # scorecard is already on the report (this call is the vault + BLUF path, which
    # run after _dd_decision_readiness set it). The readiness builder itself applies
    # the identical cap with its freshly-computed count, so all three surfaces —
    # header, BLUF, vault — agree. Caps DOWN only; INCOMPLETE is left untouched.
    _dr = r.get("decision_readiness") if isinstance(r, dict) else None
    if isinstance(_dr, dict):
        _ans, _req = _dr.get("answered"), _dr.get("required")
        if isinstance(_ans, int) and isinstance(_req, int) and _req > 0:
            _capped = _cap_grade(grade, _readiness_grade_cap(_ans, _req))
            if _capped != grade:
                blockers = [
                    f"grade capped to {_capped}: only {_ans}/{_req} decision-critical "
                    f"questions answered (Grade A requires all five)"
                ] + blockers
                grade = _capped
    public_metrics = dict(metrics)
    public_metrics.pop("adverse_media_skipped", None)
    public_metrics.pop("has_search_degradation_gap", None)
    return {
        "grade": grade,
        "score": score,
        "blocking_reasons": blockers,
        "metrics": public_metrics,
        "grade_a_requirements": {
            "min_cited_sources": 8,
            "min_reputable_sources": 5,
            "min_citation_grounding_rate": 0.8,
            "requires_identity_authority": True,
            "requires_fresh_sanctions_source": True,
            "requires_export_control_or_sanctions_regime_check": True,
            "requires_adverse_media_search": True,
            "requires_confidence_gate_clear": True,
        },
    }



# R-F2845 — module-level so it is DIRECTLY TESTABLE. It was a nested closure, which
# is how a false ANSWERED shipped unnoticed (same reason R-F2839 extracted the GLEIF
# profile mapping).
#
# THE DEFECT IT FIXES. On live run dd_86cce59b5bb1 (SOCAR Trading SA) the readiness
# framework reported ownership_control: ANSWERED while directors, shareholders,
# identity.ubo_chain, controlled_by and findings were ALL empty. The only node in
# network.ubo_chain was the subject itself:
#     {name: "SOCAR Trading SA", role: "subject", hop_depth: 0, id: "seed"}
# Its name is substantive, so the old test passed and the company answered the
# ownership question by being its own seed. Zero ownership information, ANSWERED.
#
# That is a FALSE ANSWERED — the mirror of a false clean. A false clean turns "no
# findings" into "safe"; a false ANSWERED turns "we looked" into "we know". Both
# convert an absence into a positive, and this one inflated the headline (3/5 when
# the truth was 2/5) on precisely the question where a competitor's report on the
# same entity carried real data.
#
# The rule already existed ONE LAYER AWAY, in the edge-writer
# (dd_orchestrator.py:281): "unanchorable, or the subject itself — never guess an
# id". The graph layer knows the subject is not a relationship; the readiness layer
# did not. Same architecture as R-F2821 / R-F2832 / R-F2840.
_SUBJECT_ROLES = frozenset({"subject", "seed", "self", "target"})

# Mirrors the in-function _substantive() (dd_schema ~:1430). Kept verbatim so the
# module-level holder test rejects exactly the same placeholder vocabulary — the
# closure it used to capture is not reachable from module scope.
_PLACEHOLDER_VALUES = frozenset({
    "unknown", "unavailable", "not available", "n/a", "na", "none",
})


def _substantive_value(value) -> bool:
    return bool(value) and str(value).strip().lower() not in _PLACEHOLDER_VALUES


def _is_subject_node(h) -> bool:
    """True when this entry is the DD subject rather than a holder of it.

    Any ONE marker is sufficient — the walker sets all three on the seed, but a
    partial node from another producer may carry only one.
    """
    if not isinstance(h, dict):
        return False
    if str(h.get("role") or "").strip().lower() in _SUBJECT_ROLES:
        return True
    if str(h.get("id") or "").strip().lower() in _SUBJECT_ROLES:
        return True
    hop = h.get("hop_depth")
    if isinstance(hop, (int, float)) and int(hop) == 0:
        return True
    return False


def _has_named_holder(holders) -> bool:
    """True iff `holders` contains at least one NAMED party OTHER than the subject.

    A bare string carries no role/hop, so it cannot be the seed and counts as a
    holder — that legacy shape is how shareholders are sometimes supplied.
    """
    if not isinstance(holders, (list, tuple)):
        return False
    for h in holders:
        if _is_subject_node(h):
            continue                     # the subject is not evidence of its own ownership
        name = h.get("name") if isinstance(h, dict) else h
        if isinstance(name, str) and len(name.strip()) >= 2 and _substantive_value(name):
            return True
    return False


#: R-F3020 — DISPLAY labels for LayerStatus. The stored enum values are the wire
#: contract and do NOT change; only what a human reads does. "OK" on a section
#: header was read as a QUALITY verdict ("this section is fine") when it only ever
#: meant "this layer ran to completion" — and it sat on reports graded D, next to
#: real blockers. "COMPLETED" says the one thing the field actually knows.
_STATUS_LABELS: dict[str, str] = {
    LayerStatus.OK.value: "COMPLETED",
    LayerStatus.PARTIAL.value: "PARTIAL",
    LayerStatus.SKIPPED.value: "SKIPPED",
    LayerStatus.ERROR.value: "ERROR",
    LayerStatus.PREREQ_FAIL.value: "PREREQ_FAIL",
    LayerStatus.PREREQ_DEGRADED.value: "DEGRADED",
}


def _status_label(status) -> str:
    """R-F3020 — human label for a layer status; unknown values pass through
    upper-cased so a new enum member never renders blank."""
    raw = str(status or "").strip()
    return _STATUS_LABELS.get(raw.lower(), raw.upper())


# ── R-F3026 — RENDER THE PEOPLE ──────────────────────────────────────────────
#
# THE DEFECT. `identity.directors` and `identity.shareholders` are fully populated
# by the Companies House investigation — officer_id, person_number, appointment
# dates, nationality, natures of control — and then EVERY renderer dropped them:
# markdown printed entity/jurisdiction/reg-no/sanctions/ghost and no people; the
# structured view emitted counts only ("UBO chain depth: 3"); the PDF had no code
# path for officers at all. Meanwhile the decision-readiness scorecard asserted its
# identity evidence was "live registry status plus number and DIRECTORS/incorporation".
# So the report claimed directors as evidence on a surface where no director was ever
# shown. Same class as R-F2998 (lists screened, never rendered) and R-F3012 (scrubbed
# the source list, not the render surface).
def _format_officer(o) -> str:
    """R-F3026 — one director/officer line. Renders only fields that are present."""
    if isinstance(o, str):
        return o.strip()
    if not isinstance(o, dict):
        return ""
    name = str(o.get("name") or "").strip()
    if not name:
        return ""
    bits = []
    role = str(o.get("officer_role") or o.get("role") or "").replace("-", " ").strip()
    if role:
        bits.append(role)
    if o.get("appointed_on"):
        bits.append(f"appointed {o['appointed_on']}")
    if o.get("resigned_on"):
        bits.append(f"RESIGNED {o['resigned_on']}")
    if o.get("nationality"):
        bits.append(str(o["nationality"]))
    if o.get("occupation"):
        bits.append(str(o["occupation"]))
    return f"{name}" + (f" — {', '.join(bits)}" if bits else "")


def _format_psc(p) -> str:
    """R-F3026 — one PSC / beneficial-owner line, with the natures of control that
    make it meaningful (a name alone does not say 'holds 75-100%')."""
    if isinstance(p, str):
        return p.strip()
    if not isinstance(p, dict):
        return ""
    name = str(p.get("name") or "").strip()
    if not name:
        return ""
    bits = []
    kind = str(p.get("kind") or "").replace("-person-with-significant-control", "")
    kind = kind.replace("-", " ").strip()
    if kind:
        bits.append(kind)
    natures = [str(n).replace("-", " ") for n in (p.get("natures_of_control") or [])]
    if natures:
        bits.append("; ".join(natures[:4]))
    ident = p.get("identification") if isinstance(p.get("identification"), dict) else {}
    if ident.get("registration_number"):
        bits.append(f"reg {ident['registration_number']}")
    if p.get("ceased_on"):
        bits.append(f"CEASED {p['ceased_on']}")
    if p.get("notified_on"):
        bits.append(f"notified {p['notified_on']}")
    return f"{name}" + (f" — {', '.join(bits)}" if bits else "")


def _format_previous_name(p) -> str:
    """R-F3024/R-F3026 — one former-name line."""
    if not isinstance(p, dict):
        return str(p or "").strip()
    name = str(p.get("name") or "").strip()
    if not name:
        return ""
    return (f"{name} (until {p.get('ceased_on') or '?'}"
            f"{', from ' + str(p['effective_from']) if p.get('effective_from') else ''})")


# ── R-F3055 — ADVERSE MEDIA MUST BE ON EVERY SURFACE ────────────────────────
#
# OPERATOR (2026-07-25): "the adverse media section does not show on the pdf report
# it must be included also, the online report must match 100% the downloaded version".
#
# Two causes, both real:
#   1. `adverse_media` is a TOP-LEVEL key on the report, not a layer, so the PDF's
#      `_DD_LAYER_PLAN` never reached it — the downloaded report showed nothing.
#   2. The online view has "Adverse Media" in its section TITLE but rendered only
#      press coverage and source tiers, so it under-reported it too.
#
# And the state itself is load-bearing: on every completed report checked,
# `adverse_media.status` was still `in_progress`, because the sweep is a detached
# follow-up that merges after the DD returns. A section that is simply ABSENT while
# a screening is unfinished reads as "nothing adverse found" — the false clean this
# product exists to prevent. So the renderer states the STATE first, always.
_ADVERSE_STATUS_WORDS = {
    "completed": "COMPLETED",
    "complete": "COMPLETED",
    # R-F3067 — a self-bounded sweep that returned real findings is NOT the same as
    # one that failed. It ran, it answered, and it stopped early: say so.
    "partial": "COMPLETED (bounded — stopped early)",
    "in_progress": "STILL RUNNING",
    "running": "STILL RUNNING",
    "incomplete": "DID NOT COMPLETE",
    "failed": "FAILED",
    "error": "FAILED",
}


def _render_adverse_media(am, entity_type: str = "") -> list[str]:
    """R-F3055 — the adverse-media screening as report lines, for BOTH surfaces.

    Renders only what the blob records. Module-level and pure so the PDF's mirror
    implementation can be tested against the same expectations."""
    if not isinstance(am, dict) or not am:
        # R-F3068 — be accurate about WHY there is nothing here. For an INDIVIDUAL
        # the deep media sweep is deliberately not run (it is gated to company
        # subjects, `_is_company_dd`), but a sanctions/PEP screen — which covers the
        # adverse-media LISTS — did run and is reported under Compliance. Saying
        # "NOT RUN … treat as UNCHECKED" flatly is therefore wrong for a person: it
        # implies nothing was done. Naming the real position is both honest and
        # more useful than an alarm that overstates the gap.
        if str(entity_type or "").strip().lower() == "person":
            return ["Adverse media: MEDIA SWEEP NOT RUN for an individual subject — the "
                    "sanctions/PEP screen (which covers adverse-media watchlists) DID run "
                    "and is reported under Compliance. A dedicated press/court media search "
                    "on this individual has NOT been performed; commission one before "
                    "relying on this file."]
        return ["Adverse media: NOT RUN — no adverse-media screening is recorded on "
                "this report. Treat as UNCHECKED, not as clean."]
    raw_status = str(am.get("status") or "").strip().lower()
    if not raw_status:
        raw_status = "completed" if am.get("ok") else ""
    label = _ADVERSE_STATUS_WORDS.get(raw_status, raw_status.upper() or "UNKNOWN")
    out: list[str] = [f"Adverse media screening: {label}"]

    searched = am.get("templates_searched")
    if searched is not None:
        out.append(f"  • Query templates actually searched: {searched}"
                   f"{' of ' + str(am.get('templates_total_in_set')) if am.get('templates_total_in_set') else ''}")
    if am.get("search_backends_answered") is not None:
        out.append("  • Search backends answered: "
                   f"{'yes' if am.get('search_backends_answered') else 'NO — the sweep could not observe the web'}")
    raw_findings = am.get("findings") if isinstance(am.get("findings"), list) else []
    # The materiality arithmetic, when the verdict path recorded it (R-F3022).
    mat = am.get("materiality") if isinstance(am.get("materiality"), dict) else {}
    review_findings = (
        am.get("findings_for_review")
        if isinstance(am.get("findings_for_review"), list)
        else []
    )
    if mat:
        out.append(f"  • Raw search results returned: {len(raw_findings)}")
        # R-F3089 — the exclusion ledger now includes the two stages that let court
        # INDEX pages onto the Mitie report as "subject-named" items. Report every
        # non-zero reason; a dropped item that is never accounted for reads as an
        # item that was never found.
        _excl = [
            f"{mat.get('duplicates_dropped', 0)} duplicate",
            f"{mat.get('self_references_dropped', 0)} self-referential",
        ]
        if mat.get("index_pages_dropped"):
            _excl.append(f"{mat['index_pages_dropped']} index/listing page")
        if mat.get("subject_unnamed_dropped"):
            _excl.append(f"{mat['subject_unnamed_dropped']} not naming this entity")
        _excl.append(f"{mat.get('non_adverse_dropped', 0)} non-adverse")
        out.append(
            f"  • After de-duplication and filtering: {mat.get('credible_count', 0)} credible "
            f"adverse item(s) from {mat.get('raw_count', 0)} raw hit(s) "
            f"({', '.join(_excl)})")
        out.append(
            f"  • {len(review_findings)} item(s) require human review after filtering"
            + ("" if mat.get("subject_attribution") != "unverified" else
               " — NOTE: the subject name could not be resolved, so these items have "
               "NOT been confirmed to reference this entity")
        )
        findings_to_render = review_findings
    else:
        # R-F3084 — a legacy blob with raw hits but no persisted materiality has
        # NOT established subject attribution or adverse content. Keep the data
        # visible for audit, but never call it "subject-named" or say it survived
        # filtering.
        out.append(
            f"  • Raw, unfiltered search results returned: {len(raw_findings)}"
        )
        findings_to_render = raw_findings
    for f in findings_to_render[:5]:
        if not isinstance(f, dict):
            continue
        url = str(f.get("source_url") or f.get("url") or "").strip()
        title = str(f.get("title") or "").strip()
        prefix = "" if mat else "[RAW/UNFILTERED] "
        out.append(
            f"    - {prefix}{title[:90]}"
            f"{' — ' + url[:110] if url else ' [no URL carried]'}"
        )
    if len(findings_to_render) > 5:
        out.append(f"    … and {len(findings_to_render) - 5} more")

    if raw_status in ("in_progress", "running"):
        out.append("  ⚠ This screening had NOT finished when the report was rendered — "
                   "it is UNCHECKED, not clean. Re-open the report to pick up the result.")
    elif not raw_findings:
        out.append("  • No adverse coverage found in the sources searched. That is an "
                   "absence of COVERAGE, not proof of good standing — the sources that "
                   "did not answer are listed in the data gaps.")
    return out


def _adverse_media_summary(am, digital=None, entity_type: str = "") -> dict:
    """R-F3060 — a decision-ready SUMMARY of the adverse-media position: what the
    concern is (if any), and what the reader should do next.

    Operator ask: the digital layer needs "a summary section highlighting any adverse
    media concerns or advice". The detail lines (`_render_adverse_media`) answer WHAT
    was run; a reader still had to assemble the SO-WHAT themselves from a screening
    state, a count, and a filter arithmetic.

    Returns {"headline", "concern", "advice", "severity"}. Every field is derived
    from what the blob records — this never invents a concern, and never reports
    reassurance the evidence does not support. `severity` is presentational only; it
    does NOT feed the verdict (R-F3022 owns escalation).
    """
    am = am if isinstance(am, dict) else {}
    digital = digital if isinstance(digital, dict) else {}
    status = str(am.get("status") or "").strip().lower()
    if not status:
        status = "completed" if am.get("ok") else ""
    findings = am.get("findings") if isinstance(am.get("findings"), list) else []
    mat = am.get("materiality") if isinstance(am.get("materiality"), dict) else {}
    credible = int(mat.get("credible_count") or 0)
    review_findings = (
        am.get("findings_for_review")
        if isinstance(am.get("findings_for_review"), list)
        else []
    )

    # A layer that did not finish cannot support ANY adverse-media conclusion.
    layer_broken = str((digital.get("meta") or {}).get("status") or "").lower() in ("error", "partial")

    if not am:
        if str(entity_type or "").strip().lower() == "person":
            # R-F3068 — a person DID get a sanctions/PEP screen; only the media sweep
            # is absent. Overstating the gap is its own kind of dishonesty.
            return {
                "severity": "unknown",
                "headline": "Adverse media: media sweep not run for an individual",
                "concern": "The sanctions/PEP screen ran and is reported under Compliance, "
                           "but no press, court or regulatory media search was performed on "
                           "this individual.",
                "advice": "Commission a dedicated media search on this person before relying "
                          "on the file. Do not read the sanctions/PEP result as adverse-media "
                          "coverage.",
            }
        return {
            "severity": "unknown",
            "headline": "Adverse media: NOT SCREENED",
            "concern": "No adverse-media screening is recorded on this report, so nothing "
                       "is known either way.",
            "advice": "Do not treat this as clean. Re-run the DD, or commission a manual "
                      "adverse-media search, before relying on the file.",
        }
    if status in ("in_progress", "running"):
        return {
            "severity": "unknown",
            "headline": "Adverse media: SCREENING UNFINISHED",
            "concern": "The screening had not completed when this report was rendered, so "
                       "the absence of findings below is not evidence of absence.",
            "advice": "Re-open the report to pick up the completed sweep before making a "
                      "decision. Until then treat adverse media as UNCHECKED.",
        }
    if status in ("incomplete", "failed", "error") or am.get("error"):
        return {
            "severity": "unknown",
            "headline": "Adverse media: SCREENING DID NOT COMPLETE",
            "concern": ("The sweep failed or was cut short"
                        + (f" ({str(am.get('error'))[:120]})" if am.get("error") else "")
                        + ", so coverage is unknown."),
            "advice": "Re-run the DD. Do not record this entity as adverse-media clear on "
                      "the strength of this run.",
        }
    if credible or review_findings:
        n = credible or len(review_findings)
        # R-F3089 — "subject-named" is now a MEASURED property (the attribution stage
        # in `_adverse_media_materiality`), not a phrase the renderer asserts. When
        # the subject could not be resolved the gate failed OPEN, and the wording has
        # to admit that rather than certify an attribution nobody tested.
        attributed = mat.get("subject_attribution") != "unverified"
        return {
            "severity": "amber" if credible else "info",
            "headline": f"Adverse media: {n} item(s) require review",
            "concern": (
                (f"{n} item(s) name this entity and carry adverse content; they survived "
                 "de-duplication, self-reference and index-page filtering. "
                 if attributed else
                 f"{n} item(s) carry adverse content, but the subject name could not be "
                 "resolved, so it is NOT established that they reference this entity. ")
                + "They are listed with their sources below."),
            "advice": "Open each cited source and judge it on its merits before relying on "
                      "this file — the count alone is not a finding, and ARIA does not "
                      "decide materiality on your behalf.",
        }
    if findings and not mat:
        # R-F3084 — raw search output is not a subject-attributed allegation.
        # Historic blobs created before materiality persistence must fail closed
        # rather than rebranding every search hit as an item that survived
        # de-duplication and relevance filtering.
        return {
            "severity": "unknown",
            "headline": "Adverse media: RAW SEARCH RESULTS REQUIRE FILTERING",
            "concern": (
                f"{len(findings)} raw search result(s) were stored, but subject "
                "attribution has not been verified and no persisted materiality "
                "decision shows which, if any, are adverse."
            ),
            "advice": (
                "Do not treat the raw count as a finding or a clean result. Re-run "
                "the screening or review and classify every cited source."
            ),
        }
    # Completed, nothing found — the honest wording matters most here.
    return {
        "severity": "info" if not layer_broken else "unknown",
        "headline": ("Adverse media: nothing found in the sources searched"
                     if not layer_broken else
                     "Adverse media: nothing found, but the digital layer did not complete"),
        "concern": ("No adverse coverage was returned by the sources that answered. This is "
                    "an absence of COVERAGE, not proof of good standing."
                    + (" The digital layer did not complete, so coverage is narrower than "
                       "intended." if layer_broken else "")),
        "advice": ("Check the data gaps for sources that did not answer. For a high-value "
                   "counterparty, commission a native-language and offline media check — "
                   "ARIA searches what is indexed and reachable, which is not everything."),
    }


def _render_screened_lists(screen) -> list[str]:
    """R-F3019 — the sanctions lists actually screened, their per-list verdict, and
    the screening DATE, as report lines. Module-level so it is directly testable.

    Renders ONLY what the screen recorded. An UNAVAILABLE list is named FIRST and
    separately: a reader must never infer coverage from a summary that quietly
    dropped the list that failed to answer (never-false-clean). Returns [] when the
    screen carries no per-source detail — an empty section beats an invented one.
    """
    if not isinstance(screen, dict):
        return []
    sources = screen.get("verified_sources")
    if not isinstance(sources, dict) or not sources:
        return []
    hit, clean, unavailable = [], [], []
    for _name, _info in sources.items():
        status = str((_info or {}).get("status") or "").upper() if isinstance(_info, dict) else ""
        if status == "HIT":
            hit.append(str(_name))
        elif status == "CLEAN":
            clean.append(str(_name))
        elif status:
            unavailable.append(str(_name))
    out: list[str] = []
    total = len(hit) + len(clean) + len(unavailable)
    when = str(screen.get("screened_at") or "").strip()
    when_txt = f" · screened {when}" if when else " · screening date not recorded"
    out.append(f"Sanctions lists screened: {total}{when_txt}")
    if hit:
        out.append(f"  • MATCH ({len(hit)}): {', '.join(sorted(hit))}")
    if unavailable:
        out.append(
            f"  • DID NOT ANSWER ({len(unavailable)}): {', '.join(sorted(unavailable))} "
            "— NOT screened, treat as unchecked"
        )
    if clean:
        out.append(f"  • No match ({len(clean)}): {', '.join(sorted(clean))}")
    return out


def _dd_decision_readiness(r: dict) -> dict:
    """Measure the five customer questions required before a DD can be relied on.

    Risk and readiness are deliberately separate. A report may find no adverse
    risk in the checks that completed while remaining NOT_CLEARED because a
    decision-critical check did not answer. Missing evidence never becomes a
    clean result.
    """
    def _mapping(value) -> dict:
        return value if isinstance(value, dict) else {}

    r = _mapping(r)
    ident = _mapping(r.get("identity"))
    comp = _mapping(r.get("compliance"))
    network = _mapping(r.get("network"))
    adverse = _mapping(r.get("adverse_media"))

    def _substantive(value) -> bool:
        return bool(value) and str(value).strip().lower() not in {
            "unknown", "unavailable", "not available", "n/a", "na", "none",
        }

    # R-F2793 — identity must rest on a recognisably LIVE registry status.
    # `_substantive()` only rejects {unknown, unavailable, n/a, na, none}, so a
    # company registered as `dissolved`, `struck off`, `liquidation` — or literally
    # `banana` — used to answer "Verified legal identity: ANSWERED". A struck-off
    # entity is one of the most decision-critical negative signals in DD; reading
    # it as verified identity is a false clean.
    #
    # Unrecognised values fail CLOSED. "We cannot tell whether this company is
    # alive" is not "identity verified" — and for a foreign registry whose status
    # vocabulary we do not model, the honest answer is UNRESOLVED, not a pass.
    registration_status_raw = str(ident.get("registration_status") or "").strip()
    _status = registration_status_raw.lower().replace("_", " ").replace("-", " ")
    # R-F2803 — vocabulary widened beyond UK/US English. Previously `In Existence`
    # (Texas SoS), `Immatriculée`/`En activité` (FR), `Activa`/`Activo` (ES),
    # `Ativa` (PT), `aktiv` (DE), `Attiva` (IT) and `Ingeschreven` (NL KvK) all
    # matched NEITHER list and so failed closed — reporting "cannot verify
    # identity" for correctly-registered companies across most of ARIA's
    # non-UK/US market. Fail-closed is right for a status we cannot read; it is
    # wrong for one we simply had not modelled.
    _LIVE_STATUS_TOKENS = (
        "active", "activ", "aktiv", "attiva", "registered", "live",
        "good standing", "current", "incorporated", "operating", "in business",
        "trading", "existing", "existence", "immatricul", "ingeschreven",
        "ativa", "ativo",   # PT — note "inativa" is caught by the dead list below
        "en regla", "vigente", "bestaand",
    )
    _DEAD_STATUS_TOKENS = (
        "dissolved", "liquidat", "struck", "closed", "terminated",
        "administration", "receivership", "insolven", "cancelled", "canceled",
        "revoked", "suspended", "inactive", "defunct", "deregistered",
        "wound up", "winding up", "bankrupt", "expired", "radiée", "radiee",
        "inativ",   # PT inativa/inativo — must beat the "ativa" live token
        "cessée", "cessee", "baja", "erloschen", "cessata",
    )
    # R-F2803 (FALSE CLEAN) — a NEGATED live phrase contains a live token and no
    # dead token, so it used to read as LIVE. Verified before this fix:
    #   'Not In Good Standing' -> ANSWERED   ('good standing')
    #   'No longer trading'    -> ANSWERED   ('trading')
    #   'Ceased trading'       -> ANSWERED   ('trading')
    #   'Not active'           -> ANSWERED   ('active')
    # "Not In Good Standing" is a literal Maryland/Delaware SoS value and one of
    # the strongest "this entity is in trouble" signals in US due diligence, so
    # certifying it as VERIFIED LEGAL IDENTITY is exactly the false clean this
    # scorecard exists to prevent. A negation makes the status not-live, full
    # stop — we do not try to infer what it IS, only that it is not a clean pass.
    _NEGATED = _re.search(
        r"(?:^|\s)(?:not|non|no longer|never|ceased|cease|former|formerly|ex|"
        r"out of|failed to|pending)(?:\s|$)",
        _status,
    ) is not None
    _status_dead = any(tok in _status for tok in _DEAD_STATUS_TOKENS)
    # Dead and negation both beat live, so "converted-closed", "active - in
    # liquidation" and "not active" can never pass on a live token alone.
    registry_status_live = bool(
        _status
        and not _status_dead
        and not _NEGATED
        and any(tok in _status for tok in _LIVE_STATUS_TOKENS)
    )
    registration_number = _substantive(ident.get("registration_number"))
    registry_profile = bool(
        registry_status_live
        and (ident.get("directors") or _substantive(ident.get("incorporation_date")))
    )
    # R-F2995 — identity must NOT score YES on "live registry status" when the
    # registry was UNAVAILABLE this run (R-F1636) and the fields were enriched from
    # OSINT/vault/GLEIF instead. Scoring it ANSWERED then over-claims registry
    # verification the run never performed — the values may be correct, but ARIA
    # did not stand them against the register. Mirrors the sanctions_verified guard
    # below, which already rejects source_unavailable.
    registry_unavailable = any(
        ("r-f1636" in _g or "registry unavailable" in _g or "not registry-verified" in _g)
        for _g in (str(x).lower() for x in (ident.get("data_gaps") or []))
    )
    identity_ok = bool(registration_number and registry_profile and not registry_unavailable)

    sanctions = _mapping(ident.get("sanctions_screen"))
    sanctions_verified = bool(sanctions.get("verified_sources")) and not bool(
        sanctions.get("source_unavailable") or sanctions.get("error")
    )
    export_control = _mapping(comp.get("export_control"))
    export_checked = bool(
        export_control.get("recommendation")
        or export_control.get("classification")
        or export_control.get("findings")
        or comp.get("sanctions_regimes")
    )
    sanctions_export_ok = sanctions_verified and export_checked

    # R-F2791 — mirror the quality-metric rule exactly, so the scorecard cannot
    # certify a sweep the quality scorer just rejected. `templates_run` counted
    # templates ENTERED, so it answered this question for a sweep in which every
    # backend call failed. Require a template that actually SEARCHED, plus either
    # real findings (self-proving) or verified-healthy backends. Unknown health
    # fails CLOSED: absence of evidence is not evidence of absence.
    _adv_findings = int(adverse.get("findings_count") or len(adverse.get("findings") or []) or 0)
    _adv_searched = int(adverse.get("templates_searched") or 0)
    # R-F2808 — a blob written BEFORE R-F2791 has neither counter, so it cannot be
    # proven complete. It still fails closed (below); this only distinguishes
    # "unprovable" from "known-failed" so the blocker can say which.
    _adverse_is_legacy_blob = bool(
        adverse
        and adverse.get("ok") is True
        and "templates_searched" not in adverse
        and "search_backends_answered" not in adverse
        and not adverse.get("error")
        and not adverse.get("skipped")
        and adverse.get("status") != "in_progress"
    )
    if not _adv_searched and _adv_findings > 0:
        _adv_searched = 1  # legacy blob: findings prove a backend answered
    adverse_ok = bool(
        adverse.get("ok") is True
        and _adv_searched > 0
        and (_adv_findings > 0 or adverse.get("search_backends_answered") is True)
        and adverse.get("status") != "in_progress"
        and not adverse.get("partial")
        and not adverse.get("timed_out")
        and not adverse.get("error")
        and not adverse.get("skipped")
    )

    walk = _mapping(network.get("ubo_chain_walk"))
    walk_stats = _mapping(walk.get("stats"))
    walk_gaps = [str(g) for g in (walk.get("coverage_gaps") or [])]
    budget_exhausted = bool(walk_stats.get("budget_exhausted")) or any(
        "budget exhausted" in gap.lower() for gap in walk_gaps
    )
    # R-F2793 — a holder LIST is not ownership evidence; a holder with SUBSTANCE is.
    # This was the truthiness of the list, so `[{}]` or `['x']` answered
    # "Ownership and control: ANSWERED".
    ownership_present = any(
        _has_named_holder(src) for src in (
            ident.get("shareholders"), ident.get("ubo_chain"), network.get("ubo_chain"),
        )
    )
    # R-F3027 — an UNTRAVERSED corporate controller means ownership is NOT answered.
    # Live on dd_16db41eb5fa8: `Raven Delta Limited` held 75-100% of shares and votes
    # plus the right to appoint and remove directors; it appeared exactly ONCE in the
    # whole report (in identity.shareholders) and was absent from ubo_chain,
    # ubo_chain_walk.graph and controlled_by — while the walk reported verdict
    # "traced" with no coverage gaps, and this gate said ANSWERED. What the report
    # called a 3-deep UBO chain was the subject plus its two DIRECTORS, who are not
    # beneficial owners at all.
    untraversed_controllers = [
        c for c in (network.get("controlled_by_unanchored") or [])
        if isinstance(c, dict) and c.get("controller_name")
    ]
    ownership_ok = ownership_present and not budget_exhausted and not untraversed_controllers

    financial = _mapping(comp.get("financial_health"))
    financial_verdict = str(financial.get("health_verdict") or "UNKNOWN").upper()
    financial_ok = bool(financial.get("data_available")) and financial_verdict not in {
        "", "UNKNOWN", "UNAVAILABLE", "NOT_AVAILABLE",
    }
    # R-F3017 — when the registry DOES hold accounts but they are not machine-readable
    # (every large listed group: Companies House stores them as scanned documents),
    # say so. Truncated because it rides in a one-line blocker.
    _fin_unavail = _mapping(financial.get("financial_figures_unavailable"))
    _financial_unknown_reason = str(_fin_unavail.get("explanation") or "").strip()

    questions = {
        "identity": {
            "label": "Verified legal identity",
            "status": "ANSWERED" if identity_ok else "UNRESOLVED",
            "answered": identity_ok,
            "evidence": "live registry status plus number and directors/incorporation",
            # R-F2793 — name the offending status so the blocker is actionable:
            # "registry status is 'dissolved'" is a different instruction to the
            # reader than a generic "not substantiated".
            # R-F3054 — name the condition that ACTUALLY failed.
            #
            # Live on dd_fd2216746c15 (Rheinmetall AG, DE) the blocker read
            # "registry status 'active' is not a recognised live status" — while
            # 'active' is precisely the live status the gate recognises. The status
            # branch fired only because it was the last truthy test in the chain;
            # the real failure was that the registry returned no directors and no
            # incorporation date (the report's own bottom_line said so). Telling a
            # customer the wrong reason is worse than telling them nothing: it sends
            # them to check a field that is already correct, and on a non-GB DD —
            # where this path is the norm — it makes the whole gate look broken.
            "blocker": (
                "" if identity_ok
                else "identity enriched from OSINT/vault — registry was unavailable this "
                     "run (not registry-verified)" if registry_unavailable
                else f"registry status is {registration_status_raw!r}" if _status_dead
                # status present but NOT recognised as live — the original case
                else f"registry status {registration_status_raw!r} is not a recognised live status"
                if registration_status_raw and not registry_status_live
                # status IS live; the substantiating fields are what is missing
                else "registry status is live but the registry returned neither directors "
                     "nor an incorporation date — identity is not registry-substantiated"
                if registry_status_live and not registry_profile
                else "no registration number was established for this entity"
                if not registration_number
                else "legal identity is not registry-substantiated"
            ),
        },
        "sanctions_export_control": {
            "label": "Sanctions and export-control exposure",
            "status": "ANSWERED" if sanctions_export_ok else "UNRESOLVED",
            "answered": sanctions_export_ok,
            "evidence": "fresh sanctions source plus export-control assessment",
            "blocker": (
                "sanctions and export-control checks are not both evidenced"
                if not sanctions_export_ok else ""
            ),
        },
        "adverse_media": {
            "label": "Adverse media, corruption and litigation",
            "status": "ANSWERED" if adverse_ok else "UNRESOLVED",
            "answered": adverse_ok,
            "evidence": "completed dedicated adverse-media search",
            # R-F2808 — say WHICH of the two things is true. A blob written before
            # R-F2791 carries neither `templates_searched` nor
            # `search_backends_answered`, so a zero-finding legacy screening
            # cannot be PROVEN complete and correctly fails closed. But telling
            # the customer it "did not complete" asserts something we do not
            # know, and a delivered GREEN report flipping to that wording reads
            # as a retraction rather than a disclosure.
            #
            # This is also what reconciles the convention with R-F2693 (:1181):
            # absence of a field is never evidence of a negative. Both rules are
            # the same principle — do not assert what you cannot show — applied
            # to opposite polarities: R-F2693 avoids a false ACCUSATION, R-F2791
            # avoids a false CLEAN. Failing closed stays; the wording gets honest.
            # R-F3068 — for an INDIVIDUAL the deep media sweep is deliberately not
            # run (it is gated to company subjects), so "did not complete" reports a
            # failure that never occurred and contradicts the adverse-media summary
            # rendered alongside it. Name the real position instead.
            "blocker": (
                "" if adverse_ok
                else "adverse-media MEDIA SWEEP not run for an individual subject — the "
                     "sanctions/PEP screen ran; a dedicated press/court search has not"
                if (str(ident.get("entity_type") or "").strip().lower() == "person"
                    and not _mapping(r.get("adverse_media")))
                else "adverse-media screening predates backend-verification "
                     "(cannot be confirmed complete — re-run to clear)"
                if _adverse_is_legacy_blob
                else "adverse-media screening did not complete"
            ),
        },
        "ownership_control": {
            "label": "Ownership and control",
            "status": (
                "ANSWERED" if ownership_ok else "INCOMPLETE" if budget_exhausted else "UNRESOLVED"
            ),
            "answered": ownership_ok,
            "evidence": "shareholder/UBO chain without an exhausted traversal",
            "blocker": (
                "" if ownership_ok
                # R-F3027 — name the controller that was never walked; "unresolved"
                # gives the reader nothing to act on.
                else (
                    "corporate controller "
                    + ", ".join(str(c.get("controller_name")) for c in untraversed_controllers[:2])
                    + " holds significant control but was NOT traversed (no registry "
                      "number at Companies House) — ownership is not fully traced"
                ) if untraversed_controllers
                else "ownership/UBO traversal is incomplete" if budget_exhausted
                else "ownership/control is unresolved"
            ),
        },
        "financial_capacity": {
            "label": "Financial capacity",
            "status": "ANSWERED" if financial_ok else "UNRESOLVED",
            "answered": financial_ok,
            "evidence": "financial data with a substantive health verdict",
            # R-F3017 — an unknown with a NAMED obstacle is actionable; a bare
            # "unknown" is indistinguishable from "nothing was filed" and from
            # "we never looked". Still UNRESOLVED (this does not answer capacity)
            # — only the wording earns its keep.
            "blocker": (
                "" if financial_ok
                else f"financial capacity is unknown — {_financial_unknown_reason}"
                if _financial_unknown_reason
                else "financial capacity is unknown"
            ),
        },
    }
    # ── R-F3063 (P0) — the scorecard must be ENTITY-TYPE AWARE ──────────────
    #
    # "Ownership and control" and "Financial capacity" are COMPANY properties:
    # shareholders, a UBO chain, filed accounts. Asked of an INDIVIDUAL they can
    # never be answered, so every person DD was permanently capped at 3/5 and told
    # the reader "ownership/control is unresolved" and "financial capacity is
    # unknown" — which reads as a deficiency in the subject rather than a question
    # that does not apply. It was also the pressure that made the company-registry
    # contamination look like progress: filling those boxes for a person REQUIRES
    # attaching some company to them.
    #
    # NOT_APPLICABLE is scored out of the APPLICABLE questions, never counted as
    # answered — a person can reach decision-ready on the questions that apply to a
    # person, and can never be made to look cleared on questions nobody asked.
    if str(ident.get("entity_type") or "").strip().lower() == "person":
        for _qk in ("ownership_control", "financial_capacity"):
            _q = questions.get(_qk)
            if not _q or _q.get("answered"):
                continue          # a person WITH real company evidence keeps it
            _q["status"] = "NOT_APPLICABLE"
            _q["answered"] = False
            _q["not_applicable"] = True
            _q["blocker"] = ""    # not a blocker: it is not a question about a person
            _q["evidence"] = ("not applicable to an individual — this is a corporate "
                              "register question")
    _applicable = {k: q for k, q in questions.items() if not q.get("not_applicable")}
    answered = sum(1 for q in _applicable.values() if q["answered"])
    blockers = [q["blocker"] for q in questions.values() if q["blocker"]]
    try:
        # R-F3013 — strip any STALE decision_readiness left on `r` from a prior
        # refresh so the internal depth-grade call is UNCAPPED (self-capping there
        # would use an out-of-date answered count and _cap_grade never re-raises).
        # We apply the fresh cap below with this run's answered count. Grade A ⟺
        # decision-ready, so the header can never read "3 of 5 answered | grade A".
        _r_grade = {k: v for k, v in r.items() if k != "decision_readiness"} if isinstance(r, dict) else r
        evidence_grade = str(_dd_quality_assessment(_r_grade).get("grade") or "INCOMPLETE")
    except Exception:
        evidence_grade = "INCOMPLETE"
    evidence_grade = _cap_grade(evidence_grade, _readiness_grade_cap(answered, len(_applicable)))
    evidence_ready = evidence_grade == "A"
    if not evidence_ready:
        blockers.append(
            f"evidence grade {evidence_grade} does not meet the Grade A reliance threshold"
        )
    ready = answered == len(_applicable) and evidence_ready
    return {
        # R-F2793 — "CLEARED_FOR_RELIANCE" over-claims and R-F2786's own author
        # flagged it. ARIA is an open-source DD system: search recall is not
        # independently measured and commercial datasets are incomplete, so five
        # answered questions at Grade A means the file is READY FOR A HUMAN TO
        # DECIDE — it is not a compliance sign-off. Saying "cleared" invites a
        # customer to treat an automated report as clearance, which is the same
        # false-confidence failure this whole scorecard exists to prevent.
        "status": "DECISION_READY_FOR_HUMAN_REVIEW" if ready else "NOT_CLEARED",
        "clearance_ready": ready,
        "completion_pct": int(answered * 100 / max(1, len(_applicable))),
        "answered": answered,
        "required": len(_applicable),   # R-F3063 — APPLICABLE questions only
        "evidence_grade": evidence_grade,
        "evidence_ready": evidence_ready,
        "questions": questions,
        "blocking_reasons": blockers,
        "scope_note": (
            "Decision readiness requires all five answers plus Evidence Grade A; "
            "it does not replace the risk verdict."
        ),
    }


@fail_wire(module="dd_schema", gap_type="engine_failure")
def structured_view(r: dict) -> dict:
    """Build a DECISION-FIRST, evidence-rich render contract from a persisted DD report
    dict (crucix:dd:report:{run_id} == ARKDDReport.as_dict()). Frontends render this
    directly instead of re-parsing markdown. Never raises on a partial/quick report."""
    r = r or {}
    ident = r.get("identity") or {}
    comp = r.get("compliance") or {}
    net = r.get("network") or {}
    dig = r.get("digital") or {}
    ver = r.get("verification") or {}
    comm = r.get("commercial_coherence") or {}
    sweep = r.get("sweep_data") or {}

    is_person = (ident.get("entity_type") or "").lower() == "person"
    sanc = ident.get("sanctions_screen") or {}
    ghost = ident.get("ghost_score") or {}
    cr = comp.get("country_risk") or {}
    fin = comp.get("financial_health") or {}
    ec = comp.get("export_control") or {}

    # Digital press evidence (real cited URLs)
    press_ev = []
    for _p in (dig.get("press_coverage") or [])[:20]:
        if not isinstance(_p, dict):
            continue
        _u = _p.get("url")
        if not _u:
            continue
        press_ev.append({
            "url": _u,
            "source": _p.get("source") or "",
            "tier": _p.get("source_tier") or "",
            "snippet": (_p.get("snippet") or "")[:240],
        })

    gr = ver.get("grounded_rate")
    # R-F2366 — press-coverage honesty: lead with the tier split rather than a
    # raw count, so "14 items" (12 UNVERIFIED) doesn't read as substantive
    # coverage. "verified" = independent reputable tiers ONLY; own-site (the
    # entity's own claims) and from-memory are shown separately, never folded
    # into "verified" (that would re-inflate the number this fix exists to cut).
    _tb = dig.get("source_tier_breakdown") or {}
    _press_total = len(dig.get("press_coverage") or [])
    # R-F2376 — reconcile the render vocabulary with the RUNTIME classifier.
    # web_explorer._classify_tier (web_explorer.py:312-352) emits T1/T2/T3/T4/
    # UNVERIFIED; dd_orchestrator (4611/4645) also emits ENTITY_SITE + MEMORY_ONLY.
    # The prior split counted only T1/T2 plus OFFICIAL/INDUSTRY — DEAD keys never
    # written into source_tier_breakdown (grep-confirmed: they appear only in
    # comments and unrelated modules), so T3 quality press (Reuters/BBC/FT/WSJ/AP)
    # and T4 social VANISHED from the headline, understating reputable adverse
    # media (e.g. "8 T3 + 2 T1" rendered "2 verified / 0 unverified").
    # Fix: T1/T2 = verified; T3 = quality press (shown distinctly); unverified is
    # the REMAINDER of the breakdown (UNVERIFIED + T4 social ≈ unverified + any
    # future/unmapped key) so no tier can ever silently drop again. own-site
    # (self-reported) and from-memory (RAG-only) stay separate — never folded
    # into "verified".
    _tb_total = sum(int(v) for v in _tb.values())
    _verified = int(_tb.get("T1", 0)) + int(_tb.get("T2", 0))
    _quality_press = int(_tb.get("T3", 0))
    _own_site = int(_tb.get("ENTITY_SITE", 0))
    _from_memory = int(_tb.get("MEMORY_ONLY", 0))
    _unverified = _tb_total - _verified - _quality_press - _own_site - _from_memory
    if _unverified < 0:
        _unverified = 0  # defensive: remainder can't be negative
    if _press_total:
        _pp = [f"{_verified} verified"]
        if _quality_press:
            _pp.append(f"{_quality_press} quality press")
        _pp.append(f"{_unverified} unverified")
        if _own_site:
            _pp.append(f"{_own_site} own-site")
        if _from_memory:
            _pp.append(f"{_from_memory} from memory")
        _press_metric = " / ".join(_pp)
    else:
        _press_metric = None
    sections = [
        # 1) IDENTITY — who is this on paper (core, always shown)
        _sv_section("identity", "Identity", "👤" if is_person else "🪪", ident, [
            ("Type", ident.get("entity_type")),
            ("Nationality" if is_person else "Jurisdiction", ident.get("jurisdiction")),
            ("Reg no", ident.get("registration_number")),
            ("Reg status", ident.get("registration_status")),
            ("Incorporated", ident.get("incorporation_date")),
            # R-F3024/R-F3026 — NAMED, not counted. The structured view emitted
            # counts only, so a director/PSC/former name never reached the screen.
            ("Former names", "; ".join(
                x for x in (_format_previous_name(p)
                            for p in (ident.get("previous_names") or [])[:5]) if x) or None),
            ("Directors / officers", "; ".join(
                x for x in (_format_officer(o)
                            for o in (ident.get("directors") or [])[:8]) if x) or None),
            ("PSC / beneficial owners", "; ".join(
                x for x in (_format_psc(p)
                            for p in (ident.get("shareholders") or [])[:8]) if x) or None),
            ("LEI", (f"{(ident.get('lei_registration') or {}).get('lei')} "
                     f"({(ident.get('lei_registration') or {}).get('registration_status')})"
                     if (ident.get("lei_registration") or {}).get("lei") else None)),
            # R-F3090 — the FILTERED count, and it says which. `n_matches` is the raw
            # pre-classification hit count; rendering it beside a finding that reads
            # "no entity-name match … No real hits" made one screen give two answers.
            ("Sanctions matches", _sanctions_match_metric(sanc)),
            ("Ghost score", (f"{ghost.get('total')}/{ghost.get('max_total', '20')} "
                             f"{ghost.get('classification', '')}".strip() if ghost.get("total") is not None else None)),
        ], kind="core"),
        # 2) COMPLIANCE & SANCTIONS — the decision drivers (core)
        _sv_section("compliance", "Compliance & Sanctions", "⚖", comp, [
            ("Country risk", cr.get("headline_risk") or cr.get("risk_level")),
            ("Financial health", fin.get("health_verdict")),
            ("Export control", ec.get("recommendation")),
            ("Sanctions regimes", ", ".join(comp.get("sanctions_regimes") or []) or None),
            ("Licence path", comp.get("licence_path")),
            ("IHL criterion-2", comp.get("ihl_criterion_2_risk")),
        ], kind="core"),
        # 3) NETWORK & OWNERSHIP
        _sv_section("network", "Network & Ownership", "🕸", net, [
            ("Cross-linked entities", len(net.get("cross_linked_entities") or []) or None),
            ("PEP connections", len(net.get("pep_connections") or []) or None),
            ("Flagged in chain", len(net.get("sanctions_network") or []) or None),
            # R-F3027 — "UBO chain depth: 3" was a NODE COUNT, and its nodes were the
            # subject plus its two directors. Directors are not beneficial owners, and
            # a count is not a depth. Say what it is, and name the controllers.
            ("UBO chain nodes traversed", len(net.get("ubo_chain") or []) or None),
            ("Controllers (registry-anchored)", "; ".join(
                str(c.get("controller_name")) for c in (net.get("controlled_by") or [])[:5]
                if isinstance(c, dict) and c.get("controller_name")) or None),
            ("Controllers NOT traversed", "; ".join(
                str(c.get("controller_name")) for c in (net.get("controlled_by_unanchored") or [])[:5]
                if isinstance(c, dict) and c.get("controller_name")) or None),
        ]),
        # 4) DIGITAL & ADVERSE MEDIA
        _sv_section("digital", "Digital & Adverse Media", "🌐", dig, [
            ("Press coverage", _press_metric),
            ("People investigated", len(dig.get("people") or []) or None),
            ("Source tiers", ", ".join(f"{k}:{v}" for k, v in (dig.get("source_tier_breakdown") or {}).items()) or None),
            # R-F3060 — the decision-ready summary first: concern + advice.
            ("Adverse media — assessment",
             (lambda s: f"{s['headline']} · Concern: {s['concern']} · Advice: {s['advice']}")(
                 _adverse_media_summary(r.get("adverse_media"), dig,
                                        entity_type=ident.get("entity_type"))) or None),
            # R-F3055 — this section is TITLED "Adverse Media" and showed none of it.
            # Rendered from the SAME `_render_adverse_media` contract the markdown and
            # the PDF use, so the three surfaces cannot disagree.
            ("Adverse media", " · ".join(
                l.strip().lstrip("•-⚠ ") for l in _render_adverse_media(
                    r.get("adverse_media"), entity_type=ident.get("entity_type"))
            )[:600] or None),
        ], evidence=press_ev),
        # 5) COMMERCIAL COHERENCE (only if populated)
        _sv_section("commercial", "Commercial Coherence", "🧭", comm, []),
        # 6) LIVE SWEEP SIGNALS (only if populated)
        _sv_section("sweep", "Live Sweep Signals", "📡", sweep, []),
        # 7) VERIFICATION & METHODOLOGY — trust footer (core)
        # R-F3092 — three numbers that look like they should agree, and do not,
        # because they measure three different things: an evidence-DEPTH grade
        # (Grade B · 80/100), the share of CLAIMS traced to a source (33%), and the
        # share of CITED URLS that resolve (5/5). Presented bare, a reader reads them
        # as a contradiction. Each now says what it counts.
        _sv_section("verification", "Verification & Methodology", "🧪", ver, [
            ("Claims traced to a source",
             (f"{gr:.0%} of claims carry a source" if isinstance(gr, (int, float)) else None)),
            ("Confidence floor", ver.get("confidence_floor")),
            ("Conflicts detected", len(ver.get("conflicts") or []) or None),
            ("Independent source verification",
             ("run" if ver.get("independent_source_verification_run") else "not run (triangulation + citation grounding only)")),
            # R-F3132 — say when the sample is too small to mean anything. "0 of 2"
            # rendered as a grounding figure invited exactly the reading it cannot
            # support; the denominator is incidental prose URLs, not the report's
            # citations (of which Babcock had fifteen).
            ("Cited URLs that resolve",
             (None if not ver.get("citations_checked") else
              (f"{ver.get('citations_grounded', 0)} of {ver.get('citations_checked', 0)} "
               "inline prose links checked and reachable"
               if int(ver.get("citations_checked") or 0) >= 3 else
               f"NOT MEASURABLE — only {ver.get('citations_checked')} inline prose "
               "link(s) appeared in this report, too few to support a rate. This is "
               "not a score of zero, and it does not affect the evidence grade; the "
               "report's cited sources are listed per section."))),
            ("What these measure",
             "Evidence GRADE scores depth and corroboration; the two figures above "
             "score traceability and link health. They are independent — a report can "
             "cite five working links and still be graded low on depth."),
        ], kind="core"),
    ]
    sections = [s for s in sections if s]

    # R-F3091 — stamp every section with the entity its evidence is actually about,
    # so a group-scope layer can never be read as a statement about the subsidiary.
    _entity_scope = _dd_entity_scope(r)
    _scope_by_key = {l["key"]: l for l in _entity_scope["layers"]}
    for _s in sections:
        _l = _scope_by_key.get(_s["key"])
        if _l:
            _s["scope_entity"] = _l["scope"]
            _s["scope_basis"] = _l["basis"]
            _s["scope_is_group"] = _l["group_scope"]

    target = r.get("target") if isinstance(r.get("target"), dict) else {}
    entity_name = (
        ident.get("entity_name")
        or r.get("entity_name")
        or target.get("name")
        or target.get("entity")
        or target.get("query")
        or "(unnamed)"
    )

    return {
        "run_id": r.get("run_id"),
        "entity_name": entity_name,
        "entity_type": ident.get("entity_type"),
        "jurisdiction": ident.get("jurisdiction"),
        "jurisdiction_iso2": ident.get("jurisdiction_iso2"),
        "registration_number": ident.get("registration_number"),
        "website_url": target.get("website_url") or target.get("website") or target.get("url"),
        "risk_classification": r.get("risk_classification") or "",
        "bottom_line": r.get("bottom_line") or "",
        "recommendation": r.get("recommendation") or "",
        "confidence_tag": r.get("confidence_tag") or "",
        "confidence_gate_triggered": bool(r.get("confidence_gate_triggered")),
        "generated_at": r.get("generated_at"),
        "orchestrator_mode": r.get("orchestrator_mode"),
        "run_diagnostics": r.get("run_diagnostics") or {},   # R-F2494
        "canonical_entity_id": r.get("canonical_entity_id"),
        "version_number": r.get("version_number") or 1,
        "entity_scope": _entity_scope,               # R-F3091
        "quality_assessment": _dd_quality_assessment(r),
        "decision_readiness": _dd_decision_readiness(r),
        "next_actions": [a for a in (r.get("next_actions") or []) if a],
        "data_gaps_summary": [g for g in (r.get("data_gaps_summary") or []) if g],
        "sections": sections,
    }


# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
