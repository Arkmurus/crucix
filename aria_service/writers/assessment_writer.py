# =============================================================================
# ARIA v3.1 — Assessment Writer
# aria_service/writers/assessment_writer.py
#
# Produces formally structured intelligence assessments using:
#   - NATO STANAG 2511 source/information grading (A-F reliability, 1-6 accuracy)
#   - BLUF (Bottom Line Up Front) structure
#   - Graded key judgements with confidence levels
#   - Alternative hypothesis consideration
#   - Direct mapping from ARIA's verified_intel source tier system
#
# NATO GRADING MAP:
#   Source reliability: A (completely reliable) → F (reliability cannot be judged)
#   Information accuracy: 1 (confirmed by other sources) → 6 (cannot be judged)
#
# ARIA TIER → NATO GRADE MAPPING:
#   Tier 1a (official primary)    → A1 / A2
#   Tier 1b (official secondary)  → B2 / B3
#   Tier 2  (Reuters/BBC/FT)      → B3 / C3
#   Tier 3  (specialist defence)  → C3 / D4
#   Tier 4  (general regional)    → D4 / E5
#   Tier 5  (blogs/Twitter)       → E5 / F6 — never verifies alone
# =============================================================================

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("ARIA.AssessmentWriter")

# R-F1645: no longer hard-requires anthropic SDK. The writer accepts an
# optional llm_provider (from llm/factory.py) and falls back to the
# resilient_llm helper which tries DeepSeek when Anthropic is unavailable.
# The old `import anthropic` gate is removed — the writer never crashes
# at import time due to a missing SDK.



# =============================================================================
# NATO GRADING SYSTEM
# =============================================================================

class SourceReliability(Enum):
    A = "A"  # Completely reliable
    B = "B"  # Usually reliable
    C = "C"  # Fairly reliable
    D = "D"  # Not usually reliable
    E = "E"  # Unreliable
    F = "F"  # Reliability cannot be judged

class InformationAccuracy(Enum):
    ONE   = "1"  # Confirmed by other independent sources
    TWO   = "2"  # Probably true
    THREE = "3"  # Possibly true
    FOUR  = "4"  # Doubtful
    FIVE  = "5"  # Improbable
    SIX   = "6"  # Cannot be judged

class ConfidenceLevel(Enum):
    HIGH     = "HIGH"
    MODERATE = "MODERATE"
    LOW      = "LOW"

class ClassificationLevel(Enum):
    UNCLASSIFIED           = "UNCLASSIFIED"
    ARKMURUS_CONFIDENTIAL  = "ARKMURUS CONFIDENTIAL"
    CLIENT_CONFIDENTIAL    = "CLIENT CONFIDENTIAL"
    RESTRICTED             = "RESTRICTED"

# ARIA source tier → NATO grade mapping
TIER_TO_NATO: dict[str, tuple[SourceReliability, InformationAccuracy]] = {
    "1a": (SourceReliability.A, InformationAccuracy.ONE),
    "1b": (SourceReliability.B, InformationAccuracy.TWO),
    "2":  (SourceReliability.B, InformationAccuracy.THREE),
    "3":  (SourceReliability.C, InformationAccuracy.THREE),
    "4":  (SourceReliability.D, InformationAccuracy.FOUR),
    "5":  (SourceReliability.E, InformationAccuracy.FIVE),
}

def tier_to_nato_grade(tier: str) -> str:
    rel, acc = TIER_TO_NATO.get(tier, (SourceReliability.F, InformationAccuracy.SIX))
    return f"{rel.value}{acc.value}"

def confidence_from_sources(source_tiers: list[str]) -> ConfidenceLevel:
    """Derive confidence level from the tiers of supporting sources."""
    if not source_tiers:
        return ConfidenceLevel.LOW
    tier_scores = {"1a": 5, "1b": 4, "2": 3, "3": 2, "4": 1, "5": 0}
    scores = [tier_scores.get(t, 0) for t in source_tiers]
    avg = sum(scores) / len(scores)
    independent_sources = len(set(source_tiers))
    if avg >= 3.5 and independent_sources >= 2:
        return ConfidenceLevel.HIGH
    elif avg >= 2.0 or independent_sources >= 2:
        return ConfidenceLevel.MODERATE
    return ConfidenceLevel.LOW


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class SourceRecord:
    source_id:   str              # e.g. "SRC-001"
    description: str              # e.g. "SIMPORTEX official procurement notice"
    url:         Optional[str]
    tier:        str              # ARIA tier: 1a, 1b, 2, 3, 4, 5
    date:        Optional[str]    # ISO date string
    nato_grade:  str = field(init=False)

    def __post_init__(self):
        self.nato_grade = tier_to_nato_grade(self.tier)


@dataclass
class KeyJudgement:
    number:      int
    judgement:   str
    confidence:  ConfidenceLevel
    supporting_sources: list[str]          # source_ids
    reasoning:   str
    caveat:      Optional[str] = None

    @property
    def label(self) -> str:
        return f"KJ{self.number}"

    def render(self) -> str:
        src_refs = ", ".join(f"[{s}]" for s in self.supporting_sources)
        caveat_block = f"\n        CAVEAT: {self.caveat}" if self.caveat else ""
        return (
            f"  {self.label} [{self.confidence.value}]: {self.judgement}\n"
            f"        Basis: {self.reasoning} {src_refs}{caveat_block}"
        )


@dataclass
class AlternativeHypothesis:
    hypothesis:  str
    probability: str        # "LOW" / "MODERATE" / "HIGH"
    rationale:   str
    would_change_if: str    # What indicator would raise this hypothesis


@dataclass
class AssessmentRequest:
    """Input spec for the assessment writer."""
    subject:           str                  # What the assessment is about
    region:            str                  # Geographic scope
    requester:         str                  # Client or Arkmurus team
    classification:    ClassificationLevel  = ClassificationLevel.ARKMURUS_CONFIDENTIAL
    reference_number:  Optional[str]        = None
    source_material:   list[dict]           = field(default_factory=list)
    additional_context: str                 = ""
    output_language:   str                  = "en"  # en, pt, fr


@dataclass
class IntelligenceAssessment:
    """A fully structured intelligence assessment."""
    reference:       str
    subject:         str
    region:          str
    classification:  ClassificationLevel
    produced_by:     str = "ARIA — Arkmurus Research Intelligence Agent"
    produced_for:    str = ""
    produced_at:     str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    )
    bluf:            str = ""
    situation:       str = ""
    key_judgements:  list[KeyJudgement]           = field(default_factory=list)
    alternative_hypotheses: list[AlternativeHypothesis] = field(default_factory=list)
    intelligence_gaps: list[str]                  = field(default_factory=list)
    sources:         list[SourceRecord]            = field(default_factory=list)
    outlook_90_days: str = ""
    recommended_actions: list[str]                = field(default_factory=list)

    def render_text(self) -> str:
        """Render the assessment as formatted text."""
        sep = "=" * 72
        thin = "-" * 72

        # Source annex
        source_annex = ""
        for src in self.sources:
            source_annex += (
                f"  [{src.source_id}] {src.description}\n"
                f"           NATO Grade: {src.nato_grade} | "
                f"Tier: {src.tier} | Date: {src.date or 'undated'}"
            )
            if src.url:
                source_annex += f" | {src.url}"
            source_annex += "\n"

        # Key judgements block
        kj_block = "\n\n".join(kj.render() for kj in self.key_judgements)

        # Alternative hypotheses
        alt_block = ""
        for i, ah in enumerate(self.alternative_hypotheses, 1):
            alt_block += (
                f"  AH{i} [{ah.probability}]: {ah.hypothesis}\n"
                f"       Rationale: {ah.rationale}\n"
                f"       Would reconsider if: {ah.would_change_if}\n\n"
            )

        # Intelligence gaps
        gaps_block = "\n".join(f"  - {g}" for g in self.intelligence_gaps)

        # Recommended actions
        actions_block = "\n".join(
            f"  {i}. {a}" for i, a in enumerate(self.recommended_actions, 1)
        )

        return f"""
{sep}
{self.classification.value}
{sep}
INTELLIGENCE ASSESSMENT
Reference:  {self.reference}
Subject:    {self.subject}
Region:     {self.region}
Produced:   {self.produced_at}
Produced by:{self.produced_by}
For:        {self.produced_for}
{thin}

BOTTOM LINE UP FRONT (BLUF)
{self.bluf}

{thin}
SITUATION
{self.situation}

{thin}
KEY JUDGEMENTS

{kj_block}

{thin}
ALTERNATIVE HYPOTHESES

{alt_block}
{thin}
INTELLIGENCE GAPS
{gaps_block}

{thin}
90-DAY OUTLOOK
{self.outlook_90_days}

{thin}
RECOMMENDED ACTIONS
{actions_block}

{thin}
SOURCE ANNEX
{source_annex}
{sep}
{self.classification.value}
SOURCE GRADING: NATO STANAG 2511
  Reliability: A=Completely reliable  B=Usually reliable  C=Fairly reliable
               D=Not usually reliable E=Unreliable        F=Cannot be judged
  Accuracy:    1=Confirmed  2=Probably true  3=Possibly true
               4=Doubtful   5=Improbable     6=Cannot be judged
{sep}
""".strip()


# =============================================================================
# ASSESSMENT WRITER ENGINE
# =============================================================================

class AssessmentWriter:
    """
    Produces NATO-standard intelligence assessments from source material
    and ARIA's verified intel store.
    """

    SYSTEM_PROMPT = """You are ARIA's assessment writing module. You produce
structured intelligence assessments following NATO STANAG 2511 source grading.

When producing an assessment you MUST:
1. Lead with BLUF — the single most important conclusion in 2-3 sentences
2. Grade every claim to its source using NATO A-F/1-6 notation
3. State confidence levels explicitly: HIGH / MODERATE / LOW
4. Consider at least one alternative hypothesis and assess its probability
5. Identify intelligence gaps honestly — what you do not know
6. Never state conclusions beyond what the sources support
7. Distinguish clearly between FACT (sourced) and INFERENCE (your analysis)

Constitutional constraints apply — Clause 1 (epistemic honesty),
Clause 14 (no fabricated verifiable facts), Clause 15 (inline citation).
Return ONLY valid JSON matching the schema provided."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        """Initialise the assessment writer.

        R-F1645: no longer requires anthropic SDK at construction time.
        The client is created lazily on first use. Tests can mock
        self.client or set self.llm_provider after construction.
        """
        self.api_key = api_key
        self.model = model
        self.client = None
        self.llm_provider = None

    def write(self, request: AssessmentRequest) -> IntelligenceAssessment:
        """
        Generate a complete intelligence assessment from the request.
        """
        ref = self._generate_reference(request)
        source_records = self._build_source_records(request.source_material)
        source_context = self._format_sources_for_prompt(source_records)

        prompt = f"""Produce an intelligence assessment on:

SUBJECT: {request.subject}
REGION: {request.region}
CLASSIFICATION: {request.classification.value}
ADDITIONAL CONTEXT: {request.additional_context}

SOURCE MATERIAL:
{source_context}

Return JSON with this exact schema:
{{
  "bluf": "2-3 sentence bottom line",
  "situation": "Detailed situation paragraph",
  "key_judgements": [
    {{
      "number": 1,
      "judgement": "The specific judgement",
      "confidence": "HIGH|MODERATE|LOW",
      "supporting_sources": ["SRC-001"],
      "reasoning": "Why this judgement is supported",
      "caveat": "Optional caveat or null"
    }}
  ],
  "alternative_hypotheses": [
    {{
      "hypothesis": "Alternative explanation",
      "probability": "LOW|MODERATE|HIGH",
      "rationale": "Why considered but assessed unlikely",
      "would_change_if": "What indicator would elevate this"
    }}
  ],
  "intelligence_gaps": ["Gap 1", "Gap 2"],
  "outlook_90_days": "Forward-looking paragraph",
  "recommended_actions": ["Action 1", "Action 2"]
}}"""

        try:
            from ._resilient_llm import resilient_complete
            # Lazily create Anthropic client only if needed
            if self.client is None and self.api_key:
                try:
                    import anthropic as _anthropic
                    self.client = _anthropic.Anthropic(api_key=self.api_key)
                except Exception:
                    self.client = None
            if self.client is not None:
                rr = resilient_complete(
                    anthropic_client=self.client,
                    anthropic_model=self.model,
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=prompt,
                    max_tokens=2000,
                )
            else:
                # No Anthropic client — try DeepSeek directly via resilient_llm
                from ._resilient_llm import resilient_complete, _call_deepseek, ResilientResponse
                try:
                    text, model_used = _call_deepseek(
                        self.SYSTEM_PROMPT, prompt, 2000, 90.0
                    )
                    rr = ResilientResponse(text=text, model_used=model_used, degraded=True, reason="No Anthropic client available")
                except Exception as fb_exc:
                    raise RuntimeError(f"No LLM provider available: {fb_exc}")
            # Carry degradation flags onto self so the writer's caller
            # (WriterOrchestrator) can propagate them into the final
            # WriterResult without changing the method signature.
            self._last_llm_degraded = rr.degraded
            self._last_llm_model = rr.model_used
            self._last_llm_reason = rr.reason
            raw = re.sub(r"```json\s*|\s*```", "", rr.text).strip()
            data = json.loads(raw)
        except Exception as e:
            logger.error(f"Assessment generation failed: {e}")
            raise

        kjs = [
            KeyJudgement(
                number=kj["number"],
                judgement=kj["judgement"],
                confidence=ConfidenceLevel(kj["confidence"]),
                supporting_sources=kj["supporting_sources"],
                reasoning=kj["reasoning"],
                caveat=kj.get("caveat"),
            )
            for kj in data.get("key_judgements", [])
        ]

        ahs = [
            AlternativeHypothesis(
                hypothesis=ah["hypothesis"],
                probability=ah["probability"],
                rationale=ah["rationale"],
                would_change_if=ah["would_change_if"],
            )
            for ah in data.get("alternative_hypotheses", [])
        ]

        assessment = IntelligenceAssessment(
            reference=ref,
            subject=request.subject,
            region=request.region,
            classification=request.classification,
            produced_for=request.requester,
            bluf=data.get("bluf", ""),
            situation=data.get("situation", ""),
            key_judgements=kjs,
            alternative_hypotheses=ahs,
            intelligence_gaps=data.get("intelligence_gaps", []),
            sources=source_records,
            outlook_90_days=data.get("outlook_90_days", ""),
            recommended_actions=data.get("recommended_actions", []),
        )

        # Brain signal — assessment writer outputs are gold-tier training
        # pairs (full IC-style assessments with key judgements, alternative
        # hypotheses, source reliability tiers, intelligence gaps). Each
        # finished assessment fires a high-weight signal so the
        # training_export pipeline picks them up + mastery on the region
        # gets credit. Fire-and-forget from sync context.
        try:
            import asyncio as _aio
            from ..intel import brain_hook as _bh

            high_conf_kjs = sum(
                1 for kj in kjs if kj.confidence == ConfidenceLevel.HIGH
            )

            async def _emit():
                await _bh.absorb(
                    module="assessment_writer",
                    summary=(
                        f"Assessment {ref}: {request.subject[:80]} ({request.region}) — "
                        f"{len(kjs)} key judgements ({high_conf_kjs} HIGH), "
                        f"{len(ahs)} alt hypotheses, {len(data.get('intelligence_gaps', []))} gaps"
                    ),
                    detail=(assessment.bluf or "")[:1500],
                    entity_name=request.subject[:120],
                    success=True,
                    extra_topics=["compliance", "geopolitics", "osint"],
                    source_id=ref,
                    confidence="CONFIRMED",
                )

            try:
                loop = _aio.get_running_loop()
                loop.create_task(_emit())
            except RuntimeError:
                pass
        except Exception:
            pass

        return assessment

    # ── helpers ──────────────────────────────────────────────────────────────

    def _generate_reference(self, request: AssessmentRequest) -> str:
        if request.reference_number:
            return request.reference_number
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        region_code = request.region.upper().replace(" ", "_")[:6]
        return f"ARK-INTEL-{region_code}-{ts}"

    def _build_source_records(self, raw_sources: list[dict]) -> list[SourceRecord]:
        records = []
        for i, src in enumerate(raw_sources, 1):
            records.append(SourceRecord(
                source_id=src.get("id", f"SRC-{i:03d}"),
                description=src.get("description", ""),
                url=src.get("url"),
                tier=str(src.get("tier", "4")),
                date=src.get("date"),
            ))
        return records

    def _format_sources_for_prompt(self, sources: list[SourceRecord]) -> str:
        if not sources:
            return "No specific sources provided — use general knowledge with appropriate caveats."
        lines = []
        for src in sources:
            lines.append(
                f"[{src.source_id}] {src.description} "
                f"(ARIA Tier {src.tier} → NATO {src.nato_grade})"
                + (f": {src.url}" if src.url else "")
            )
        return "\n".join(lines)
