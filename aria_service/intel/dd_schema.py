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
            elif len(unique_sources) == 1 and _is_tier_1a_source(unique_sources[0]):
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
    confidence_floor: str = "ASSESSED"
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    # R-F393: honest-scope flags. Set by _run_verification.
    # independent_source_verification_run is True only when
    # source_verifier.py is actually invoked against LLM outputs
    # (it is not, as of 2026-05-13; the integration is a placeholder).
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

        def _sec_header(emoji: str, name: str, meta: SectionMeta) -> str:
            return f"━━━ {emoji} {name} [{meta.status.upper()}] ━━━"

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
        for f in self.compliance.findings[:5 if concise else 20]:
            lines.append(f"  • [{f.severity}] {f.title}")
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
            lines.append("━━━ 🛡 Counter-intelligence [OK] ━━━")
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
            lines.append("━━━ ⚖ Sanctions Divergence [OK] ━━━")
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
            lines.append("━━━ 🔬 Forensic [OK] ━━━")
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
                lines.append(
                    f"TBML: {_tbml.get('transactions_analysed', 0)} txns analysed, "
                    f"{_tbml.get('high_anomalies', 0)} HIGH anomaly"
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
    }


def _sv_findings(section: dict) -> list[dict]:
    out = [_sv_finding(f) for f in (section.get("findings") or []) if f]
    out = [f for f in out if f.get("title")]
    out.sort(key=lambda f: _SEVERITY_RANK.get(f.get("severity", "info"), 5))
    return out


def _sv_section(key, title, icon, section, highlights, *, kind="standard", evidence=None):
    """Assemble one render-ready section. Returns None when a non-core section has no content."""
    section = section or {}
    meta = section.get("meta") or {}
    findings = _sv_findings(section)
    gaps = [g for g in (section.get("data_gaps") or []) if g]
    hl = [{"label": l, "value": (str(v) if not isinstance(v, str) else v)}
          for (l, v) in highlights if v not in (None, "", [], {}, 0)]
    ev = evidence or []
    if not (findings or gaps or hl or ev) and kind != "core":
        return None
    return {
        "key": key, "title": title, "icon": icon,
        "status": (meta.get("status") or "ok"),
        "duration_ms": meta.get("duration_ms") or 0,
        "subcalls": meta.get("subcalls") or 0,
        "error": meta.get("error"),
        "highlights": hl, "findings": findings, "data_gaps": gaps, "evidence": ev,
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
    n_matches = len(sanc.get("matches") or [])
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
    _verified = sum(int(v) for k, v in _tb.items() if k in ("T1", "T2", "OFFICIAL", "INDUSTRY"))
    _unverified = int(_tb.get("UNVERIFIED", 0))
    _own_site = int(_tb.get("ENTITY_SITE", 0))
    _from_memory = int(_tb.get("MEMORY_ONLY", 0))
    if _press_total:
        _pp = [f"{_verified} verified", f"{_unverified} unverified"]
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
            ("Sanctions matches", (n_matches if sanc else None)),
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
            ("UBO chain depth", len(net.get("ubo_chain") or []) or None),
        ]),
        # 4) DIGITAL & ADVERSE MEDIA
        _sv_section("digital", "Digital & Adverse Media", "🌐", dig, [
            ("Press coverage", _press_metric),
            ("People investigated", len(dig.get("people") or []) or None),
            ("Source tiers", ", ".join(f"{k}:{v}" for k, v in (dig.get("source_tier_breakdown") or {}).items()) or None),
        ], evidence=press_ev),
        # 5) COMMERCIAL COHERENCE (only if populated)
        _sv_section("commercial", "Commercial Coherence", "🧭", comm, []),
        # 6) LIVE SWEEP SIGNALS (only if populated)
        _sv_section("sweep", "Live Sweep Signals", "📡", sweep, []),
        # 7) VERIFICATION & METHODOLOGY — trust footer (core)
        _sv_section("verification", "Verification & Methodology", "🧪", ver, [
            ("Grounded rate", (f"{gr:.0%}" if isinstance(gr, (int, float)) else None)),
            ("Confidence floor", ver.get("confidence_floor")),
            ("Conflicts detected", len(ver.get("conflicts") or []) or None),
            ("Independent source verification",
             ("run" if ver.get("independent_source_verification_run") else "not run (triangulation only)")),
            ("Citations grounded",
             (f"{ver.get('citations_grounded', 0)}/{ver.get('citations_checked', 0)}"
              if ver.get("citations_checked") else None)),
        ], kind="core"),
    ]
    sections = [s for s in sections if s]

    return {
        "run_id": r.get("run_id"),
        "entity_name": ident.get("entity_name") or (r.get("target") or {}).get("query") or "(unnamed)",
        "entity_type": ident.get("entity_type"),
        "jurisdiction": ident.get("jurisdiction"),
        "risk_classification": r.get("risk_classification") or "",
        "bottom_line": r.get("bottom_line") or "",
        "recommendation": r.get("recommendation") or "",
        "confidence_tag": r.get("confidence_tag") or "",
        "confidence_gate_triggered": bool(r.get("confidence_gate_triggered")),
        "generated_at": r.get("generated_at"),
        "orchestrator_mode": r.get("orchestrator_mode"),
        "canonical_entity_id": r.get("canonical_entity_id"),
        "version_number": r.get("version_number") or 1,
        "next_actions": [a for a in (r.get("next_actions") or []) if a],
        "data_gaps_summary": [g for g in (r.get("data_gaps_summary") or []) if g],
        "sections": sections,
    }


# R-F2119 §21a — wire failure handler for dd_schema
try:
    wire_failure(module="dd_schema", detail="module shutdown",
                gap_type="engine_failure", source="dd_schema:shutdown")
except Exception:
    pass
