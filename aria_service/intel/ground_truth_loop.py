# =============================================================================
# ARIA v3.1 — Ground Truth Feedback Loop + Living Constitution
# aria_service/intel/ground_truth_loop.py
#
# WHAT THIS SOLVES (GAPS THE OTHER ANALYSIS MISSED):
#
# GAP 1 — GROUND TRUTH:
#   ARIA produces intelligence assessments with confidence scores.
#   There is currently no mechanism to check whether her assessments
#   were correct after the fact. A system that cannot measure its own
#   accuracy cannot improve its accuracy.
#
#   This module creates an outcome feedback loop:
#   - ARIA's predictions and assessments are logged at creation time
#   - When outcomes are known (tender found/not found, contact in post/not,
#     counterparty passed/failed DD), they are recorded
#   - ARIA's confidence calibration is updated based on accuracy history
#   - Weekly calibration report surfaces systematic over/under-confidence
#
# GAP 2 — LIVING CONSTITUTION:
#   The 14-clause constitution is fixed. It reflects incidents that occurred
#   before it was written. Every week ARIA operates, she encounters new
#   edge cases that the constitution does not address. There is no mechanism
#   to add clause 15, 16, 17 as new incidents occur.
#
#   This module creates a living constitution:
#   - New incidents are logged when they occur
#   - ARIA drafts a proposed new clause from each incident
#   - Draft clauses are queued for human review and approval
#   - Approved clauses are injected into ARIA's system prompt immediately
#   - The constitution version is tracked and auditable
#
# AUTONOMY TIER:
#   - Outcome logging: Auto-allowed (internal record only)
#   - Calibration reporting: Auto-allowed (weekly team DM)
#   - New clause DRAFTING: Auto-allowed (ARIA drafts, human approves)
#   - New clause ACTIVATION: Human-approved (never auto-activate)
# =============================================================================

import re
import json
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum

import os
import anthropic
from aria_service.config import Settings

_settings = Settings()


class _ConfigShim:
    @property
    def anthropic_api_key(self) -> str:
        return _settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")

    @property
    def model_standard(self) -> str:
        return os.getenv("ARIA_CHALLENGE_MODEL", "claude-sonnet-4-5")


config = _ConfigShim()

logger = logging.getLogger("ARIA.GroundTruth")


# =============================================================================
# GROUND TRUTH FEEDBACK LOOP
# =============================================================================

class AssessmentType(Enum):
    TENDER_OPPORTUNITY   = "TENDER_OPPORTUNITY"    # ARIA said there was a tender
    CONTACT_IN_POST      = "CONTACT_IN_POST"       # ARIA said contact holds position
    COUNTERPARTY_CLEAN   = "COUNTERPARTY_CLEAN"    # ARIA said entity is low risk
    COUNTRY_RISK         = "COUNTRY_RISK"          # ARIA rated country risk
    WIN_PROBABILITY      = "WIN_PROBABILITY"       # ARIA gave deal win probability
    PROGRAMME_ACTIVE     = "PROGRAMME_ACTIVE"      # ARIA said programme is active
    INTELLIGENCE_CLAIM   = "INTELLIGENCE_CLAIM"    # General intelligence assessment


class OutcomeResult(Enum):
    CORRECT              = "CORRECT"
    INCORRECT            = "INCORRECT"
    PARTIALLY_CORRECT    = "PARTIALLY_CORRECT"
    UNABLE_TO_VERIFY     = "UNABLE_TO_VERIFY"


@dataclass
class AssessmentRecord:
    """A recorded ARIA assessment, pending outcome verification."""
    assessment_id: str
    assessment_type: AssessmentType
    subject: str                    # Entity, country, programme being assessed
    aria_prediction: str            # What ARIA predicted
    aria_confidence: float          # ARIA's stated confidence (0-1)
    context: str                    # Brief context of the assessment
    domain: str                     # Which ARIA domain made this assessment
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Outcome fields (filled in later)
    outcome: Optional[OutcomeResult] = None
    outcome_evidence: str = ""
    outcome_recorded_at: Optional[str] = None
    outcome_recorded_by: str = ""

    @property
    def is_verified(self) -> bool:
        return self.outcome is not None

    @property
    def days_pending(self) -> int:
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - created).days


@dataclass
class CalibrationReport:
    """Weekly accuracy report for ARIA's assessments."""
    period_days: int = 30
    total_assessments: int = 0
    verified_assessments: int = 0
    correct: int = 0
    incorrect: int = 0
    partially_correct: int = 0
    unable_to_verify: int = 0
    accuracy_by_domain: dict = field(default_factory=dict)
    accuracy_by_type: dict = field(default_factory=dict)
    overconfident_assessments: list[dict] = field(default_factory=list)
    underconfident_assessments: list[dict] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def overall_accuracy(self) -> float:
        if self.verified_assessments == 0:
            return 0.0
        return self.correct / self.verified_assessments

    def to_report(self) -> str:
        lines = [
            "═" * 50,
            "ARIA CALIBRATION REPORT",
            f"Period: last {self.period_days} days",
            f"Generated: {self.generated_at[:10]}",
            "═" * 50,
            "",
            f"Total assessments made:  {self.total_assessments}",
            f"Verified:                {self.verified_assessments}",
            f"Overall accuracy:        {self.overall_accuracy:.0%}",
            "",
            f"  Correct:               {self.correct}",
            f"  Partially correct:     {self.partially_correct}",
            f"  Incorrect:             {self.incorrect}",
            f"  Unverifiable:          {self.unable_to_verify}",
        ]

        if self.accuracy_by_domain:
            lines += ["", "ACCURACY BY DOMAIN:"]
            for domain, acc in sorted(
                self.accuracy_by_domain.items(),
                key=lambda x: x[1], reverse=True
            ):
                lines.append(f"  {domain:<25} {acc:.0%}")

        if self.overconfident_assessments:
            lines += ["", "⚠ OVERCONFIDENT ASSESSMENTS (confidence >> accuracy):"]
            for item in self.overconfident_assessments[:3]:
                lines.append(
                    f"  • {item['subject']}: stated {item['confidence']:.0%}, "
                    f"was {item['outcome']}"
                )

        if not self.verified_assessments:
            lines += [
                "",
                "⚠ No verified outcomes yet. To improve calibration:",
                "  1. When a tender is found/not found — record the outcome",
                "  2. When a contact is verified in post — record the outcome",
                "  3. When a counterparty passes/fails DD — record the outcome",
            ]

        lines.append("\n" + "═" * 50)
        return "\n".join(lines)


class ARIAGroundTruthLoop:
    """
    Feedback loop that tracks whether ARIA's assessments were correct.

    CRITICAL DESIGN NOTE:
    ARIA validates her outputs against her own knowledge base.
    A self-referential system cannot be trusted to measure its own accuracy.
    This module creates the external reference point:
    real-world outcomes compared against prior predictions.

    Without this, ARIA's confidence scores are uncalibrated opinions.
    With this, they are empirically grounded probability estimates.
    """

    REDIS_KEY_PREFIX = "aria:ground_truth"

    def __init__(self, redis_client=None, notify_fn=None):
        self.redis = redis_client
        self.notify = notify_fn
        self._local_store: list[AssessmentRecord] = []

    def record_assessment(
        self,
        assessment_type: AssessmentType,
        subject: str,
        aria_prediction: str,
        aria_confidence: float,
        context: str = "",
        domain: str = "general",
    ) -> str:
        """
        Record an ARIA assessment at the time it is made.
        Call this whenever ARIA makes a testable prediction.

        Returns:
            assessment_id for later outcome recording
        """
        assessment_id = hashlib.md5(
            f"{assessment_type.value}{subject}"
            f"{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12]

        record = AssessmentRecord(
            assessment_id=assessment_id,
            assessment_type=assessment_type,
            subject=subject,
            aria_prediction=aria_prediction,
            aria_confidence=aria_confidence,
            context=context,
            domain=domain,
        )
        self._store_record(record)
        logger.debug(
            f"Assessment recorded: {assessment_id} | "
            f"{assessment_type.value} | {subject} | {aria_confidence:.0%}"
        )
        return assessment_id

    def record_outcome(
        self,
        assessment_id: str,
        outcome: OutcomeResult,
        evidence: str,
        recorded_by: str = "team",
    ) -> Optional[AssessmentRecord]:
        """
        Record what actually happened, compared to ARIA's prediction.
        Call this when you find out whether ARIA was right.

        Args:
            assessment_id:  ID returned by record_assessment()
            outcome:        What actually happened
            evidence:       How you know (source, document, observation)
            recorded_by:    Who verified (antonio | ari | aria-auto)
        """
        record = self._load_record(assessment_id)
        if not record:
            logger.warning(f"Assessment not found: {assessment_id}")
            return None

        record.outcome = outcome
        record.outcome_evidence = evidence
        record.outcome_recorded_at = datetime.now(timezone.utc).isoformat()
        record.outcome_recorded_by = recorded_by
        self._store_record(record)

        # Flag significant miscalibration
        if outcome == OutcomeResult.INCORRECT and record.aria_confidence > 0.75:
            logger.warning(
                f"HIGH CONFIDENCE MISS: {record.subject} — "
                f"ARIA was {record.aria_confidence:.0%} confident, outcome: INCORRECT"
            )

        return record

    def get_pending_verification(self, max_age_days: int = 90) -> list[AssessmentRecord]:
        """
        Return assessments that need outcome verification.
        Use in weekly team briefing to prompt outcome recording.
        """
        all_records = self._load_all_records()
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        return [
            r for r in all_records
            if not r.is_verified
            and datetime.fromisoformat(
                r.created_at.replace("Z", "+00:00")
            ) > cutoff
        ]

    def generate_calibration_report(
        self, period_days: int = 30
    ) -> CalibrationReport:
        """Generate accuracy calibration report for the specified period."""
        all_records = self._load_all_records()
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

        recent = [
            r for r in all_records
            if datetime.fromisoformat(
                r.created_at.replace("Z", "+00:00")
            ) > cutoff
        ]

        report = CalibrationReport(period_days=period_days)
        report.total_assessments = len(recent)
        verified = [r for r in recent if r.is_verified]
        report.verified_assessments = len(verified)

        for r in verified:
            if r.outcome == OutcomeResult.CORRECT:
                report.correct += 1
            elif r.outcome == OutcomeResult.INCORRECT:
                report.incorrect += 1
                if r.aria_confidence > 0.70:
                    report.overconfident_assessments.append({
                        "subject": r.subject,
                        "confidence": r.aria_confidence,
                        "outcome": "INCORRECT",
                    })
            elif r.outcome == OutcomeResult.PARTIALLY_CORRECT:
                report.partially_correct += 1
            else:
                report.unable_to_verify += 1

        # Accuracy by domain
        domains = set(r.domain for r in verified)
        for domain in domains:
            domain_records = [r for r in verified if r.domain == domain]
            correct = sum(1 for r in domain_records
                          if r.outcome == OutcomeResult.CORRECT)
            if domain_records:
                report.accuracy_by_domain[domain] = correct / len(domain_records)

        return report

    def _store_record(self, record: AssessmentRecord) -> None:
        if not self.redis:
            self._local_store = [
                r for r in self._local_store
                if r.assessment_id != record.assessment_id
            ]
            self._local_store.append(record)
            return
        try:
            key = f"{self.REDIS_KEY_PREFIX}:{record.assessment_id}"
            self.redis.set(key, json.dumps({
                "assessment_id": record.assessment_id,
                "assessment_type": record.assessment_type.value,
                "subject": record.subject,
                "aria_prediction": record.aria_prediction,
                "aria_confidence": record.aria_confidence,
                "context": record.context,
                "domain": record.domain,
                "created_at": record.created_at,
                "outcome": record.outcome.value if record.outcome else None,
                "outcome_evidence": record.outcome_evidence,
                "outcome_recorded_at": record.outcome_recorded_at,
                "outcome_recorded_by": record.outcome_recorded_by,
            }), ex=180 * 86400)  # 180 days
        except Exception as e:
            logger.error(f"Ground truth store failed: {e}")

    def _load_record(self, assessment_id: str) -> Optional[AssessmentRecord]:
        if not self.redis:
            return next(
                (r for r in self._local_store
                 if r.assessment_id == assessment_id), None
            )
        try:
            data = self.redis.get(f"{self.REDIS_KEY_PREFIX}:{assessment_id}")
            if not data:
                return None
            return self._dict_to_record(json.loads(data))
        except Exception:
            return None

    def _load_all_records(self) -> list[AssessmentRecord]:
        if not self.redis:
            return self._local_store.copy()
        try:
            records = []
            for key in self.redis.scan_iter(f"{self.REDIS_KEY_PREFIX}:*"):
                data = self.redis.get(key)
                if data:
                    try:
                        records.append(self._dict_to_record(json.loads(data)))
                    except Exception:
                        continue
            return records
        except Exception:
            return []

    def _dict_to_record(self, d: dict) -> AssessmentRecord:
        return AssessmentRecord(
            assessment_id=d["assessment_id"],
            assessment_type=AssessmentType[d["assessment_type"]],
            subject=d["subject"],
            aria_prediction=d["aria_prediction"],
            aria_confidence=d["aria_confidence"],
            context=d.get("context", ""),
            domain=d.get("domain", "general"),
            created_at=d["created_at"],
            outcome=OutcomeResult[d["outcome"]] if d.get("outcome") else None,
            outcome_evidence=d.get("outcome_evidence", ""),
            outcome_recorded_at=d.get("outcome_recorded_at"),
            outcome_recorded_by=d.get("outcome_recorded_by", ""),
        )


# =============================================================================
# LIVING CONSTITUTION
# =============================================================================

class ConstitutionClauseStatus(Enum):
    DRAFT     = "DRAFT"      # ARIA drafted, pending human review
    APPROVED  = "APPROVED"   # Human approved, active in system prompt
    REJECTED  = "REJECTED"   # Human rejected, not activated
    RETIRED   = "RETIRED"    # Previously active, now superseded


@dataclass
class ConstitutionClause:
    """A single clause in ARIA's living constitution."""
    clause_id: str
    clause_number: int              # Sequential (15, 16, 17...)
    incident_summary: str           # What triggered this clause
    incident_date: str
    clause_text: str                # The actual clause ARIA must follow
    rationale: str                  # Why this clause is needed
    status: ConstitutionClauseStatus = ConstitutionClauseStatus.DRAFT
    drafted_by: str = "ARIA"
    reviewed_by: str = ""
    reviewed_at: str = ""
    activated_at: str = ""
    examples: list[str] = field(default_factory=list)  # Concrete examples

    def to_system_prompt_clause(self) -> str:
        """Format for injection into ARIA's system prompt."""
        return (
            f"CLAUSE {self.clause_number} — "
            f"[Anchored to: {self.incident_summary[:60]}]:\n"
            f"{self.clause_text}"
        )


class ARIALivingConstitution:
    """
    ARIA's constitution evolves as new incidents occur.

    The current 14-clause constitution is anchored to 14 incidents.
    Every week ARIA operates, she encounters new edge cases.
    This module ensures those cases become permanent learning,
    not forgotten edge cases.

    CRITICAL CONSTRAINT:
    ARIA can DRAFT new clauses. She CANNOT activate them.
    Human approval is required before any new clause enters the system prompt.
    This maintains human control over ARIA's core behavioural constraints.
    """

    REDIS_KEY = "aria:constitution:living_clauses"
    CURRENT_CLAUSE_COUNT = 14  # The existing 14 anchored clauses

    def __init__(self, redis_client=None, notify_fn=None):
        self.redis = redis_client
        self.notify = notify_fn
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._local_clauses: list[ConstitutionClause] = []

    def report_incident(
        self,
        incident_description: str,
        incident_type: str,
        reported_by: str = "aria",
        context: str = "",
    ) -> ConstitutionClause:
        """
        Report a new incident that may warrant a constitutional clause.
        ARIA drafts the proposed clause for human review.

        Args:
            incident_description: What happened
            incident_type:        The category of incident
            reported_by:          Who is reporting
            context:              Additional context

        Returns:
            Draft ConstitutionClause (status=DRAFT, not yet active)
        """
        # Draft the clause
        clause_text, rationale, examples = self._draft_clause(
            incident_description, incident_type, context
        )

        # Assign next clause number
        existing = self._load_clauses()
        next_number = self.CURRENT_CLAUSE_COUNT + len(existing) + 1

        clause_id = hashlib.md5(
            f"{incident_description}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12]

        clause = ConstitutionClause(
            clause_id=clause_id,
            clause_number=next_number,
            incident_summary=incident_description[:100],
            incident_date=datetime.now(timezone.utc).isoformat()[:10],
            clause_text=clause_text,
            rationale=rationale,
            status=ConstitutionClauseStatus.DRAFT,
            drafted_by="ARIA",
            examples=examples,
        )

        self._store_clause(clause)

        # Notify for human review
        if self.notify:
            self.notify(
                f"📋 ARIA — NEW CONSTITUTION CLAUSE PROPOSED\n\n"
                f"Incident: {incident_description[:150]}\n\n"
                f"Proposed Clause {next_number}:\n{clause_text}\n\n"
                f"Rationale: {rationale}\n\n"
                f"→ Approve with: approve_clause('{clause_id}')\n"
                f"→ Reject with: reject_clause('{clause_id}')"
            )

        logger.info(
            f"Constitution clause {next_number} drafted: {clause_id}"
        )
        return clause

    def approve_clause(
        self, clause_id: str, reviewed_by: str
    ) -> Optional[ConstitutionClause]:
        """
        Human approves a drafted clause.
        Activates it in ARIA's system prompt immediately.
        """
        clause = self._get_clause(clause_id)
        if not clause:
            return None
        if clause.status != ConstitutionClauseStatus.DRAFT:
            return clause

        clause.status = ConstitutionClauseStatus.APPROVED
        clause.reviewed_by = reviewed_by
        clause.reviewed_at = datetime.now(timezone.utc).isoformat()
        clause.activated_at = datetime.now(timezone.utc).isoformat()
        self._store_clause(clause)

        logger.info(
            f"Constitution clause {clause.clause_number} APPROVED "
            f"by {reviewed_by} — now active"
        )
        return clause

    def reject_clause(
        self, clause_id: str, reviewed_by: str, reason: str = ""
    ) -> Optional[ConstitutionClause]:
        """Human rejects a drafted clause."""
        clause = self._get_clause(clause_id)
        if not clause:
            return None
        clause.status = ConstitutionClauseStatus.REJECTED
        clause.reviewed_by = reviewed_by
        clause.reviewed_at = datetime.now(timezone.utc).isoformat()
        clause.rationale = f"REJECTED: {reason} | Original: {clause.rationale}"
        self._store_clause(clause)
        return clause

    def get_active_clauses(self) -> list[ConstitutionClause]:
        """Return all approved/active clauses for system prompt injection."""
        return [
            c for c in self._load_clauses()
            if c.status == ConstitutionClauseStatus.APPROVED
        ]

    def get_system_prompt_addition(self) -> str:
        """
        Return the system prompt text for all active living clauses.
        Append this to ARIA's base 14-clause constitution.
        """
        active = self.get_active_clauses()
        if not active:
            return ""
        lines = ["\n\nLIVING CONSTITUTION — ADDITIONAL CLAUSES (human-approved):"]
        for clause in active:
            lines.append(f"\n{clause.to_system_prompt_clause()}")
        return "\n".join(lines)

    def get_pending_review(self) -> list[ConstitutionClause]:
        """Return clauses awaiting human review."""
        return [
            c for c in self._load_clauses()
            if c.status == ConstitutionClauseStatus.DRAFT
        ]

    def _draft_clause(
        self,
        incident: str,
        incident_type: str,
        context: str,
    ) -> tuple[str, str, list[str]]:
        """Use Claude to draft a constitution clause from an incident."""
        try:
            response = self.client.messages.create(
                model=config.model_standard,
                max_tokens=600,
                messages=[{"role": "user", "content": f"""
Draft a new constitutional clause for ARIA's operational guidelines based on this incident.

Incident: {incident}
Type: {incident_type}
Context: {context}

Existing clauses cover: self-output honesty checking, avoiding Omar Jones-type profiling, 
stale official verification, fabricated facts in action summaries, CDL document review errors,
Lebanon propaganda detection, Modirum fabricated facts, and 7 foundational principles.

Draft a clause that:
1. Is anchored specifically to this incident
2. Gives ARIA clear, actionable guidance
3. Is written in first person (ARIA speaking)
4. Prevents this specific failure mode from recurring

Respond with JSON:
{{
  "clause_text": "I must...",
  "rationale": "This clause is needed because...",
  "examples": ["Example 1 of how to apply this", "Example 2"]
}}"""}],
                timeout=20,
            )
            raw = re.sub(r"```json\s*|\s*```", "",
                         response.content[0].text.strip()).strip()
            data = json.loads(raw)
            return (
                data.get("clause_text", ""),
                data.get("rationale", ""),
                data.get("examples", []),
            )
        except Exception as e:
            logger.error(f"Clause drafting failed: {e}")
            return (
                f"When encountering {incident_type}, I must apply extra scrutiny "
                f"and verify all claims independently before acting.",
                f"Anchored to incident: {incident[:100]}",
                [],
            )

    def _store_clause(self, clause: ConstitutionClause) -> None:
        if not self.redis:
            self._local_clauses = [
                c for c in self._local_clauses
                if c.clause_id != clause.clause_id
            ]
            self._local_clauses.append(clause)
            return
        try:
            key = f"{self.REDIS_KEY}:{clause.clause_id}"
            self.redis.set(key, json.dumps({
                "clause_id": clause.clause_id,
                "clause_number": clause.clause_number,
                "incident_summary": clause.incident_summary,
                "incident_date": clause.incident_date,
                "clause_text": clause.clause_text,
                "rationale": clause.rationale,
                "status": clause.status.value,
                "drafted_by": clause.drafted_by,
                "reviewed_by": clause.reviewed_by,
                "reviewed_at": clause.reviewed_at,
                "activated_at": clause.activated_at,
                "examples": clause.examples,
            }), ex=365 * 86400)
        except Exception as e:
            logger.error(f"Constitution clause store failed: {e}")

    def _load_clauses(self) -> list[ConstitutionClause]:
        if not self.redis:
            return self._local_clauses.copy()
        try:
            clauses = []
            for key in self.redis.scan_iter(f"{self.REDIS_KEY}:*"):
                data = self.redis.get(key)
                if data:
                    d = json.loads(data)
                    clauses.append(ConstitutionClause(
                        clause_id=d["clause_id"],
                        clause_number=d["clause_number"],
                        incident_summary=d["incident_summary"],
                        incident_date=d["incident_date"],
                        clause_text=d["clause_text"],
                        rationale=d["rationale"],
                        status=ConstitutionClauseStatus[d["status"]],
                        drafted_by=d.get("drafted_by", "ARIA"),
                        reviewed_by=d.get("reviewed_by", ""),
                        reviewed_at=d.get("reviewed_at", ""),
                        activated_at=d.get("activated_at", ""),
                        examples=d.get("examples", []),
                    ))
            return clauses
        except Exception:
            return []

    def _get_clause(self, clause_id: str) -> Optional[ConstitutionClause]:
        return next(
            (c for c in self._load_clauses() if c.clause_id == clause_id),
            None
        )
