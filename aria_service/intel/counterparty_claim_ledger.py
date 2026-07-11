from .engine_wiring import wire_failure

# =============================================================================
# ARIA v3.1 — Counterparty Claim Ledger
# aria_service/intel/counterparty_claim_ledger.py
#
# WHAT THIS SOLVES:
#   Counterparties make claims across email, WhatsApp, Zoom, and meetings.
#   Currently these claims live in separate channels with no cross-referencing.
#   A broker who said "no sanctions exposure" in email and "minor issues"
#   on a Zoom call — ARIA currently does not connect those two statements.
#
#   This module:
#   1. Extracts material claims from every inbound channel
#   2. Stores them in a structured ledger per counterparty per deal
#   3. Detects contradictions between claims across time and channel
#   4. Flags contradictions to the deal owner privately (not group channel)
#   5. Feeds into the deception detection module for scoring
#
# PERSISTENCE: Redis (claim ledger, 90-day rolling window)
# AUTONOMY TIER: Tier 1 — Auto-allowed (deal owner DM only, no external)
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
try:
    import anthropic
except ImportError:
    anthropic = None
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

logger = logging.getLogger("aria.counterparty_claim_ledger")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class ClaimCategory(Enum):
    IDENTITY          = "IDENTITY"           # Who they are, their role, credentials
    OWNERSHIP         = "OWNERSHIP"          # UBO, shareholders, control
    COMPLIANCE        = "COMPLIANCE"         # Sanctions, licences, legal status
    MANDATE           = "MANDATE"            # Authority to act, representation
    CAPABILITY        = "CAPABILITY"         # What they can deliver
    FINANCIAL         = "FINANCIAL"          # Payment capacity, financing, budget
    RELATIONSHIP      = "RELATIONSHIP"       # Contact access, government relationships
    TIMELINE          = "TIMELINE"           # Delivery dates, decision windows
    EXCLUSIVITY       = "EXCLUSIVITY"        # Sole agent, exclusive access claims
    PRIOR_EXPERIENCE  = "PRIOR_EXPERIENCE"   # Track record, past transactions


class ClaimStatus(Enum):
    PENDING       = "PENDING"        # Not yet verified
    CONFIRMED     = "CONFIRMED"      # Verified against external source
    CONTRADICTED  = "CONTRADICTED"   # Contradicts another claim by same party
    REFUTED       = "REFUTED"        # Verified as false against external source
    WITHDRAWN     = "WITHDRAWN"      # Counterparty retracted


class ContradictionSeverity(Enum):
    MINOR    = "MINOR"     # Different phrasing, same substance
    MODERATE = "MODERATE"  # Inconsistent but not necessarily deceptive
    SERIOUS  = "SERIOUS"   # Direct contradiction — material claim differs
    CRITICAL = "CRITICAL"  # Active deception — explicit denial of prior claim


@dataclass
class Claim:
    """A single material claim made by a counterparty."""
    claim_id: str
    counterparty_name: str
    deal_id: str
    channel: str                    # email | whatsapp | zoom | telegram | meeting
    claim_text: str                 # Original text of the claim
    claim_summary: str              # ARIA's one-line summary
    category: ClaimCategory
    status: ClaimStatus = ClaimStatus.PENDING
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source_message_id: str = ""
    verification_source: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "counterparty_name": self.counterparty_name,
            "deal_id": self.deal_id,
            "channel": self.channel,
            "claim_text": self.claim_text,
            "claim_summary": self.claim_summary,
            "category": self.category.value,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "source_message_id": self.source_message_id,
            "notes": self.notes,
        }


@dataclass
class Contradiction:
    """A detected contradiction between two claims by the same counterparty."""
    contradiction_id: str
    claim_a: Claim                  # Earlier claim
    claim_b: Claim                  # Later claim (the one that contradicts)
    severity: ContradictionSeverity
    explanation: str                # Why these claims contradict
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    notified: bool = False

    def to_alert(self) -> str:
        """Format as a deal owner DM alert."""
        severity_emoji = {
            ContradictionSeverity.MINOR:    "🟡",
            ContradictionSeverity.MODERATE: "🟠",
            ContradictionSeverity.SERIOUS:  "🔴",
            ContradictionSeverity.CRITICAL: "🚨",
        }.get(self.severity, "⚠")

        return (
            f"{severity_emoji} ARIA — COUNTERPARTY CONTRADICTION DETECTED\n\n"
            f"Counterparty: {self.claim_a.counterparty_name}\n"
            f"Deal: {self.claim_a.deal_id}\n"
            f"Severity: {self.severity.value}\n\n"
            f"EARLIER CLAIM ({self.claim_a.channel}, "
            f"{self.claim_a.timestamp[:10]}):\n"
            f"  \"{self.claim_a.claim_summary}\"\n\n"
            f"LATER CLAIM ({self.claim_b.channel}, "
            f"{self.claim_b.timestamp[:10]}):\n"
            f"  \"{self.claim_b.claim_summary}\"\n\n"
            f"WHY THIS CONTRADICTS:\n"
            f"  {self.explanation}\n\n"
            f"→ Do not proceed without resolving this discrepancy.\n"
            f"→ Consider requesting documentary evidence for both claims."
        )


# =============================================================================
# COUNTERPARTY CLAIM LEDGER
# =============================================================================

class ARIACounterpartyClaimLedger:
    """
    Tracks every material claim made by counterparties across all channels.

    Designed to catch the specific failure mode where:
    - A counterparty says one thing on email and another on a call
    - The team doesn't connect the two because they're in different channels
    - ARIA sees all channels and flags the contradiction to the deal owner

    Usage:
        ledger = ARIACounterpartyClaimLedger(redis_client, notify_fn)

        # When a new message arrives from a counterparty:
        claims = ledger.ingest_message(
            text="We have no sanctions exposure and are fully clean.",
            counterparty="BTG a.s.",
            deal_id="DEAL-001",
            channel="email",
        )

        # ARIA automatically checks for contradictions and notifies if found
    """

    REDIS_KEY_PREFIX = "aria:claim_ledger"
    CLAIM_TTL_DAYS = 36500  # 100 years — permanent; was 90d rolling before 2026-04-21

    def __init__(self, redis_client=None, notify_fn=None):
        self.redis = redis_client
        self.notify = notify_fn
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key) if anthropic else None
        self._local_cache: dict[str, list[Claim]] = {}

    def ingest_message(
        self,
        text: str,
        counterparty: str,
        deal_id: str,
        channel: str,
        message_id: str = "",
    ) -> list[Claim]:
        """
        Extract material claims from a counterparty message.
        Store them in the ledger.
        Check for contradictions.
        Alert deal owner if contradictions found.

        Args:
            text:          Full message text
            counterparty:  Counterparty entity name
            deal_id:       Deal identifier
            channel:       Communication channel
            message_id:    Original message ID for audit trail

        Returns:
            List of extracted claims
        """
        if not text.strip():
            return []

        # Extract claims using LLM
        new_claims = self._extract_claims(text, counterparty, deal_id, channel)

        if not new_claims:
            return []

        # Store each claim
        for claim in new_claims:
            claim.source_message_id = message_id
            self._store_claim(claim)

        # Check for contradictions with existing claims
        contradictions = self._detect_contradictions(counterparty, deal_id, new_claims)

        # Alert deal owner for serious+ contradictions
        serious = [c for c in contradictions
                   if c.severity in (ContradictionSeverity.SERIOUS,
                                     ContradictionSeverity.CRITICAL)]
        if serious and self.notify:
            for contradiction in serious:
                self.notify(contradiction.to_alert(), urgent=True)
                contradiction.notified = True
                self._store_contradiction(contradiction)

        # Store moderate contradictions without immediate alert
        moderate = [c for c in contradictions
                    if c.severity == ContradictionSeverity.MODERATE]
        for c in moderate:
            self._store_contradiction(c)

        logger.info(
            f"Claim ledger: {len(new_claims)} claims extracted from "
            f"{counterparty} via {channel} | "
            f"{len(contradictions)} contradictions detected"
        )
        return new_claims

    def get_claims(
        self,
        counterparty: str,
        deal_id: str = "",
        category: Optional[ClaimCategory] = None,
        status: Optional[ClaimStatus] = None,
    ) -> list[Claim]:
        """Retrieve claims for a counterparty, with optional filters."""
        all_claims = self._load_claims(counterparty, deal_id)

        if category:
            all_claims = [c for c in all_claims if c.category == category]
        if status:
            all_claims = [c for c in all_claims if c.status == status]

        return sorted(all_claims, key=lambda c: c.timestamp)

    def get_claim_summary(
        self, counterparty: str, deal_id: str = ""
    ) -> str:
        """Generate a formatted claim summary for DD reports."""
        claims = self.get_claims(counterparty, deal_id)
        if not claims:
            return f"No claims logged for {counterparty}."

        by_category: dict[str, list[Claim]] = {}
        for claim in claims:
            cat = claim.category.value
            by_category.setdefault(cat, []).append(claim)

        contradicted = [c for c in claims
                        if c.status == ClaimStatus.CONTRADICTED]

        lines = [
            f"CLAIM LEDGER — {counterparty}",
            f"Total claims logged: {len(claims)}",
            f"Contradictions detected: {len(contradicted)}",
            "─" * 40,
        ]

        if contradicted:
            lines += ["⚠ CONTRADICTED CLAIMS:"]
            for c in contradicted:
                lines.append(f"  • [{c.channel}] {c.claim_summary}")

        for category, cat_claims in by_category.items():
            lines += [f"\n{category}:"]
            for c in cat_claims:
                status_marker = "✓" if c.status == ClaimStatus.CONFIRMED else (
                    "⚠" if c.status == ClaimStatus.CONTRADICTED else "○"
                )
                lines.append(
                    f"  {status_marker} [{c.channel}, "
                    f"{c.timestamp[:10]}] {c.claim_summary}"
                )

        return "\n".join(lines)

    def verify_claim(
        self,
        claim_id: str,
        counterparty: str,
        verified_as: ClaimStatus,
        verification_source: str,
        notes: str = "",
    ) -> Optional[Claim]:
        """
        Mark a claim as verified, confirmed, or refuted.
        Call this when ARIA or the team verifies a claim against external sources.
        """
        claims = self._load_claims(counterparty)
        for claim in claims:
            if claim.claim_id == claim_id:
                claim.status = verified_as
                claim.verification_source = verification_source
                claim.notes = notes
                self._store_claim(claim)
                logger.info(
                    f"Claim {claim_id} updated: {verified_as.value} "
                    f"via {verification_source}"
                )
                return claim
        # R-F996 — wire to brain
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="counterparty_claim_ledger",
            summary="Verify Claim",
            source_id="counterparty_claim_ledger:R-F996",
        )

        return None

    # ── INTERNAL ─────────────────────────────────────────────────────────────

    def _extract_claims(
        self,
        text: str,
        counterparty: str,
        deal_id: str,
        channel: str,
    ) -> list[Claim]:
        """Use LLM to extract material claims from message text."""
        prompt = f"""Extract material claims from this counterparty message.

Counterparty: {counterparty}
Channel: {channel}
Message: {text[:2000]}

A material claim is a factual assertion about:
- Who they are (identity, credentials, role)
- What they own or control (ownership, UBO)
- Their legal/compliance status (sanctions, licences)
- Their mandate or authority
- What they can deliver (capability)
- Their financial position
- Their relationships or access
- Their track record
- Timelines or commitments
- Any claim of exclusivity

Extract ONLY factual assertions — not opinions, questions, or pleasantries.

Respond ONLY with JSON array. Each claim:
{{
  "claim_summary": "One sentence summary",
  "claim_text": "Exact quote or close paraphrase",
  "category": "IDENTITY|OWNERSHIP|COMPLIANCE|MANDATE|CAPABILITY|FINANCIAL|RELATIONSHIP|TIMELINE|EXCLUSIVITY|PRIOR_EXPERIENCE"
}}

Return [] if no material claims found."""

        if not self.client:
            return []
        try:
            response = self.client.messages.create(
                model=config.model_standard,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
                timeout=20,
            )
            raw = re.sub(r"```json\s*|\s*```", "",
                         response.content[0].text.strip()).strip()
            items = json.loads(raw)
            claims = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                claim_id = hashlib.md5(
                    f"{counterparty}{item.get('claim_summary', '')}"
                    f"{datetime.now().isoformat()}".encode()
                , usedforsecurity=False).hexdigest()[:12]
                try:
                    cat = ClaimCategory[item.get("category", "CAPABILITY")]
                except KeyError:
                    cat = ClaimCategory.CAPABILITY

                claims.append(Claim(
                    claim_id=claim_id,
                    counterparty_name=counterparty,
                    deal_id=deal_id,
                    channel=channel,
                    claim_text=item.get("claim_text", ""),
                    claim_summary=item.get("claim_summary", ""),
                    category=cat,
                ))
            return claims
        except Exception as e:
            logger.warning(f"Claim extraction failed: {e}")
            return []

    def _detect_contradictions(
        self,
        counterparty: str,
        deal_id: str,
        new_claims: list[Claim],
    ) -> list[Contradiction]:
        """Compare new claims against existing claims for the same counterparty."""
        existing = self._load_claims(counterparty, deal_id)
        if not existing:
            return []

        contradictions = []
        for new_claim in new_claims:
            # Only compare claims in the same category
            same_category = [c for c in existing
                             if c.category == new_claim.category
                             and c.claim_id != new_claim.claim_id]
            if not same_category:
                continue

            contradiction = self._check_contradiction_llm(
                same_category, new_claim
            )
            if contradiction:
                contradictions.append(contradiction)
                # Update claim status
                new_claim.status = ClaimStatus.CONTRADICTED

        return contradictions

    def _check_contradiction_llm(
        self,
        prior_claims: list[Claim],
        new_claim: Claim,
    ) -> Optional[Contradiction]:
        """Use LLM to detect semantic contradiction between claims."""
        prior_text = "\n".join(
            f"  [{c.timestamp[:10]}, {c.channel}]: {c.claim_summary}"
            for c in prior_claims[-5:]  # Last 5 claims in this category
        )

        prompt = f"""Determine if the new claim contradicts any prior claims by the same counterparty.

Prior claims ({new_claim.category.value}):
{prior_text}

New claim ({new_claim.channel}, today):
  {new_claim.claim_summary}

Does the new claim contradict any prior claim? Consider:
- Direct contradiction: new claim says opposite of prior claim
- Material inconsistency: claims are incompatible with each other
- Scope creep: new claim expands a prior claim implausibly
- Qualification contradiction: earlier unqualified, now qualified (or vice versa)

Respond ONLY with JSON:
{{
  "contradicts": true/false,
  "severity": "MINOR|MODERATE|SERIOUS|CRITICAL",
  "explanation": "One sentence explaining the contradiction",
  "prior_claim_summary": "The prior claim that is contradicted"
}}

If no contradiction, return: {{"contradicts": false}}"""

        if not self.client:
            return None
        try:
            response = self.client.messages.create(
                model=config.model_standard,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
                timeout=15,
            )
            raw = re.sub(r"```json\s*|\s*```", "",
                         response.content[0].text.strip()).strip()
            data = json.loads(raw)

            if not data.get("contradicts"):
                return None

            # Find the specific prior claim that was contradicted
            prior_claim = prior_claims[-1]  # Default to most recent

            return Contradiction(
                contradiction_id=hashlib.md5(
                    f"{new_claim.claim_id}{prior_claim.claim_id}".encode()
                , usedforsecurity=False).hexdigest()[:12],
                claim_a=prior_claim,
                claim_b=new_claim,
                severity=ContradictionSeverity[
                    data.get("severity", "MODERATE")
                ],
                explanation=data.get("explanation", ""),
            )

        except Exception as e:
            logger.debug(f"Contradiction check failed: {e}")
            return None

    def _store_claim(self, claim: Claim) -> None:
        """Persist claim to Redis."""
        if not self.redis:
            # Fall back to local cache
            key = f"{claim.counterparty_name}:{claim.deal_id}"
            self._local_cache.setdefault(key, []).append(claim)
            return
        try:
            redis_key = (
                f"{self.REDIS_KEY_PREFIX}:claims:"
                f"{claim.counterparty_name}:{claim.deal_id}"
            )
            existing = self.redis.get(redis_key)
            claims_list = json.loads(existing) if existing else []
            claims_list.append(claim.to_dict())
            self.redis.set(
                redis_key,
                json.dumps(claims_list),
                ex=self.CLAIM_TTL_DAYS * 86400,
            )
        except Exception as e:
            logger.error(f"Redis claim store failed: {e}")

    def _store_contradiction(self, contradiction: Contradiction) -> None:
        """Persist contradiction record to Redis."""
        if not self.redis:
            return
        try:
            key = (
                f"{self.REDIS_KEY_PREFIX}:contradictions:"
                f"{contradiction.claim_a.counterparty_name}"
            )
            existing = self.redis.get(key)
            contradictions = json.loads(existing) if existing else []
            contradictions.append({
                "id": contradiction.contradiction_id,
                "severity": contradiction.severity.value,
                "explanation": contradiction.explanation,
                "detected_at": contradiction.detected_at,
                "claim_a": contradiction.claim_a.claim_summary,
                "claim_b": contradiction.claim_b.claim_summary,
                "notified": contradiction.notified,
            })
            self.redis.set(key, json.dumps(contradictions),
                           ex=self.CLAIM_TTL_DAYS * 86400)
        except Exception as e:
            logger.error(f"Redis contradiction store failed: {e}")

    def _load_claims(
        self, counterparty: str, deal_id: str = ""
    ) -> list[Claim]:
        """Load claims from Redis or local cache."""
        if not self.redis:
            key = f"{counterparty}:{deal_id}"
            return self._local_cache.get(key, [])
        try:
            pattern = (
                f"{self.REDIS_KEY_PREFIX}:claims:{counterparty}:"
                f"{deal_id if deal_id else '*'}"
            )
            claims = []
            for redis_key in self.redis.scan_iter(pattern):
                data = self.redis.get(redis_key)
                if data:
                    for item in json.loads(data):
                        try:
                            claims.append(Claim(
                                claim_id=item["claim_id"],
                                counterparty_name=item["counterparty_name"],
                                deal_id=item["deal_id"],
                                channel=item["channel"],
                                claim_text=item["claim_text"],
                                claim_summary=item["claim_summary"],
                                category=ClaimCategory[item["category"]],
                                status=ClaimStatus[item["status"]],
                                timestamp=item["timestamp"],
                            ))
                        except (KeyError, ValueError):
                            continue
            return claims
        except Exception as e:
            logger.error(f"Redis claim load failed: {e}")
            return []

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
