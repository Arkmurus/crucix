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


# =============================================================================
# ENUMS
# =============================================================================

class LayerStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    SKIPPED = "skipped"   # short-circuited before reaching this layer
    ERROR = "error"


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


@dataclass
class Finding:
    """A single structured finding inside any layer."""
    severity: str  # "info" | "amber" | "red" | "hard_stop"
    title: str
    detail: str = ""
    source: str = ""
    confidence: str = "ASSESSED"  # CONFIRMED | PROBABLE | ASSESSED | UNCERTAIN | SPECULATIVE


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
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class VerificationSection:
    """Layer 3 — how well-grounded is the picture we have?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    triangulated_claims: list[dict] = field(default_factory=list)  # claim, n_sources, confidence
    conflicts: list[dict] = field(default_factory=list)
    grounded_rate: Optional[float] = None
    unverified_claim_count: int = 0
    confidence_floor: str = "ASSESSED"
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ComplianceSection:
    """Layer 4 — which legal / regulatory regimes bite on this transaction?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    country_risk: dict = field(default_factory=dict)           # from risk_indices.get_country_risk
    export_control: dict = field(default_factory=dict)         # from tech_classifier.classify_export_control
    sanctions_regimes: list[str] = field(default_factory=list)  # applicable regimes (UK/US/EU/UN)
    ihl_criterion_2_risk: Optional[str] = None                 # low | medium | high | clear
    regional_bloc_requirements: list[dict] = field(default_factory=list)  # ECOWAS/SADC/GCC/etc
    licence_path: Optional[str] = None                         # SIEL | SITCL | OIEL | OGEL | DSP-5 | ...
    findings: list[Finding] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class DigitalSection:
    """Layer 5 — what does the open web say about this entity?"""
    meta: SectionMeta = field(default_factory=SectionMeta)
    web_footprint: dict = field(default_factory=dict)            # from search_multilingual
    press_coverage: list[Evidence] = field(default_factory=list)
    procurement_history: list[dict] = field(default_factory=list)
    exhibition_presence: list[dict] = field(default_factory=list)
    knowledge_base_hits: list[dict] = field(default_factory=list)  # existing RAG hits
    neural_associations: list[str] = field(default_factory=list)
    source_tier_breakdown: dict = field(default_factory=dict)    # OFFICIAL/INDUSTRY/PRESS counts
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

    # ── Serialisation helpers ────────────────────────────────────────────

    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict. Dataclasses.asdict recursively
        handles every nested dataclass including Finding and SectionMeta."""
        return asdict(self)

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
            for f in self.digital.findings[:5 if concise else 20]:
                lines.append(f"  • [{f.severity}] {f.title}")
            lines.append("")

        # 6. Synthesis
        lines.append(_sec_header("🧠", "Synthesis", self.synthesis.meta))
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


def weakest_confidence(tags: list[str]) -> str:
    """Return the weakest confidence tag from a list. Mirrors
    confidence_footer._dominant_tag — the headline never oversells."""
    ranked = [t for t in tags if t in _CONFIDENCE_RANK]
    if not ranked:
        return "ASSESSED"
    return min(ranked, key=lambda t: _CONFIDENCE_RANK[t])
