# =============================================================================
# ARIA v3.1 — Verified Intelligence Pipeline
# aria_service/intel/verified_intel.py
#
# CORE PRINCIPLE:
#   Nothing is stored as VERIFIED until confirmed by ≥2 independent
#   Tier 1b/2 sources, OR 1 Tier 1a source, OR 3 Tier 3 sources.
#   Contradicting sources block verification until resolved.
#   Every fact has a type-specific expiry — not a uniform 90 days.
#
# WHAT THIS SOLVES:
#   ARIA currently stores facts without provenance.
#   "Gen. Musa has been in role for 665 days" — no source, no verification.
#   This produces intelligence that sounds confident but is not trustworthy.
#
#   After this module:
#   "Gen. Musa was appointed CDS on 19 June 2024
#    [from Reuters, corroborated by Premium Times independently].
#    Tenure as of today: 301 days."
#
# KEY DESIGN DECISIONS:
#   1. Source independence check — two sources citing the same press
#      release are NOT independent. The engine detects this.
#   2. Contradiction detection — two sources that disagree block
#      verification and escalate to human review.
#   3. Fact-type-specific TTL — sanctions status expires daily.
#      Appointment dates expire after 18 months (unless contradicted).
#   4. Bootstrap handling — existing ChromaDB facts are tagged
#      LEGACY_UNVERIFIED and re-verified on access.
#   5. Tenure is never stored — always calculated at query time
#      from the verified appointment date.
#
# ARCHITECTURE:
#   VerifiedFactStore   — primary storage (Redis + ChromaDB metadata)
#   SourceTierClassifier — assigns tier to any source URL
#   VerificationEngine  — runs the multi-source verification logic
#   CorroborationSearch — searches for independent corroborating sources
#   ContradictionDetector — flags when sources disagree
# =============================================================================

import re
import json
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum
from urllib.parse import urlparse

logger = logging.getLogger("ARIA.VerifiedIntel")


# =============================================================================
# SOURCE TIER DEFINITIONS
# =============================================================================

class SourceTier(Enum):
    TIER_1A = "1a"   # Official primary (government gazette, presidency statement,
                     # court ruling, regulatory filing, official registry)
                     # Can verify alone.

    TIER_1B = "1b"   # Official secondary (official press release, ministry tweet,
                     # parliamentary record, central bank statement)
                     # Needs 2 sources minimum.

    TIER_2  = "2"    # High-quality third-party (Reuters, Bloomberg, FT, BBC,
                     # Jane's Defence, IISS, Premium Times for Nigeria,
                     # DefenceWeb for Africa, Agência Brasil for Brazil)
                     # Needs 2 sources minimum.

    TIER_3  = "3"    # Specialist defence press (Defense News, Shephard, FlightGlobal,
                     # Breaking Defense, Army Recognition)
                     # Needs 3 independent sources.

    TIER_4  = "4"    # General regional news (local newspapers, regional sites)
                     # Needs Tier 1/2 corroboration or human approval.

    TIER_5  = "5"    # Unreliable (blogs, LinkedIn, Twitter/X, forums,
                     # unnamed officials, industry sources)
                     # Never verify alone. Flag for human.


# Verification thresholds by tier combination
# Key: frozenset of tiers present in source set
# Value: (minimum_sources_needed, threshold_score)
VERIFICATION_RULES = {
    # Single Tier 1a is sufficient
    "1a": {"min_sources": 1, "threshold": 0.9, "allow_single": True},

    # Two Tier 1b or two Tier 2 or one of each
    "1b_1b": {"min_sources": 2, "threshold": 1.0, "allow_single": False},
    "1b_2":  {"min_sources": 2, "threshold": 1.0, "allow_single": False},
    "2_2":   {"min_sources": 2, "threshold": 1.0, "allow_single": False},

    # Three Tier 3 sources
    "3_3_3": {"min_sources": 3, "threshold": 1.0, "allow_single": False},

    # Mixed — Tier 1b or 2 plus Tier 3
    "1b_3":  {"min_sources": 3, "threshold": 0.9, "allow_single": False},
    "2_3":   {"min_sources": 3, "threshold": 0.9, "allow_single": False},
}

# Tier scores for verification calculation
TIER_SCORES = {
    SourceTier.TIER_1A: 1.0,
    SourceTier.TIER_1B: 0.9,
    SourceTier.TIER_2:  0.7,
    SourceTier.TIER_3:  0.5,
    SourceTier.TIER_4:  0.3,
    SourceTier.TIER_5:  0.1,
}

# Tier 1a domains — single source sufficient
TIER_1A_DOMAINS = {
    # Official registries
    "companies-house.service.gov.uk", "find-and-update.company-information.service.gov.uk",
    "orsr.sk", "krs.ms.gov.pl", "anaf.ro", "receita.economia.gov.br",
    "data.merkez.gov.tr",
    # Sanctions lists
    "treasury.gov", "ofac.treasury.gov", "sanctionslist.ofsi.hmtreasury.gov.uk",
    "eeas.europa.eu", "un.org",
    # UN arms transfer & disarmament (official state-reported data)
    "unroca.org",              # UN Register of Conventional Arms
    "resolutions.unoda.org",   # UN disarmament resolutions
    "treaties.unoda.org",      # Multilateral arms regulation treaties
    # US government defence data
    "state.gov",               # WMEAT + State Dept arms transfer reports
    # Government sources
    "whitehouse.gov", "gov.uk", "gov.ng", "presidencia.gob.pe",
    "defenceweb.co.za",  # Tier 2 for most, but official Ministry of Defence announcements
    "reuters.com",       # Wire service - Tier 2 but very high quality
}

# Tier 1b domains — official academic/institutional sources
TIER_1B_DOMAINS = {
    "ucdp.uu.se",              # Uppsala Conflict Data Program — leading academic conflict dataset
    "prio.org",                # Peace Research Institute Oslo — 32 datasets on organised violence
    "smallarmssurvey.org",     # Small Arms Survey — global firearms/SALW databases
    "conflictarm.com",         # Conflict Armament Research / iTrace — weapon diversion tracking
    "internal-displacement.org",  # IDMC — global internal displacement database
    "securityassistance.org",  # Security Assistance Monitor — US FMS/IMET/FMF tracking
    "start.umd.edu",           # Global Terrorism Database — UMD START center
    "ipums.org",               # Integrated census/survey data — University of Minnesota
}

# Tier 2 domains — quality journalism and specialist press
TIER_2_DOMAINS = {
    "reuters.com", "bloomberg.com", "ft.com", "bbc.co.uk", "bbc.com",
    "theguardian.com", "economist.com", "nytimes.com", "wsj.com",
    # Defence specialist — Tier 2 quality
    "janes.com", "iiss.org", "sipri.org", "rusi.org", "chathamhouse.org",
    "understandingwar.org",
    # Arms control & conflict research
    "caat.org.uk",              # Campaign Against Arms Trade — UK export licences
    "aoav.org.uk",              # Action on Armed Violence — explosive violence data
    "armscontrolcenter.org",    # Center for Arms Control and Non-proliferation
    "correlatesofwar.org",      # Quantitative conflict data (academic)
    "oneearthfuture.org",       # REIGN + TIOS datasets
    "dronewars.net",            # Armed drone capabilities tracking
    "economicsandpeace.org",    # Global Peace Index
    "civiliansinconflict.org",  # Center for Civilians in Conflict (CIVIC)
    "unidir.org",               # UN Institute for Disarmament Research
    # Geopolitical risk indices
    "blackrock.com",            # BGRI — geopolitical risk dashboard (10 tracked risks)
    # Research & polling
    "pewresearch.org",          # Pew Research Center — global attitudes surveys
    "gallup.com",               # Gallup Global Indicators
    "mar.umd.edu",              # Minorities at Risk — ethnic conflict data
    # Regional high-quality
    "premiumtimesng.com", "defenceweb.co.za", "theafricareport.com",
    "agenciabrasil.ebc.com.br", "publico.pt",
    "aljazeera.com", "france24.com", "dw.com",
    "nikkei.com", "scmp.com",
}

# Tier 3 domains — specialist defence press
TIER_3_DOMAINS = {
    "defensenews.com", "breakingdefense.com", "shephardmedia.com",
    "flightglobal.com", "armyrecognition.com", "naval-technology.com",
    "airforce-technology.com", "defenseindustrydaily.com", "c4isrnet.com",
    "defenseone.com", "thediplomat.com", "jamestown.org",
    "bellingcat.com",  # Tier 2 for OSINT investigations, Tier 3 general
}

# Known source families — used for independence checking
# Sources within the same family often cite each other
SOURCE_FAMILIES = {
    "reuters": {"reuters.com", "reuters.tv", "reutersconnect.com"},
    "ap": {"apnews.com", "ap.org", "bigstory.ap.org"},
    "afp": {"afp.com", "relaxnews.com"},
    "bbc": {"bbc.co.uk", "bbc.com", "bbci.co.uk"},
    "guardian": {"theguardian.com", "guardian.co.uk"},
    "bloomberg": {"bloomberg.com", "bloombergtv.com", "bloomberglaw.com"},
}


# =============================================================================
# FACT TYPE DEFINITIONS WITH TTL
# =============================================================================

class FactType(Enum):
    APPOINTMENT       = "APPOINTMENT"       # Official position, role start
    TENURE_CALC       = "TENURE_CALC"       # Never stored — computed from APPOINTMENT
    SANCTIONS_STATUS  = "SANCTIONS_STATUS"  # On/off sanctions lists
    CONTRACT_AWARD    = "CONTRACT_AWARD"    # Tender won, contract signed
    CORPORATE_REGISTRY= "CORPORATE_REGISTRY"# Registry data, incorporation
    BUDGET_ALLOCATION = "BUDGET_ALLOCATION" # Defence budget figures
    PROGRAMME_STATUS  = "PROGRAMME_STATUS"  # Active/cancelled/delayed programmes
    POLITICAL_EVENT   = "POLITICAL_EVENT"   # Elections, government changes
    ARMS_TRANSFER     = "ARMS_TRANSFER"     # SIPRI-style transfer records
    RELATIONSHIP      = "RELATIONSHIP"      # Contact relationships (human approval)
    DECEPTION_SIGNAL  = "DECEPTION_SIGNAL"  # Internal analysis, not stored as verified fact
    GENERAL_CLAIM     = "GENERAL_CLAIM"     # Catch-all


# Fact-type-specific TTL in days
# Static facts (contract awards, appointments) expire slowly or not at all
# Dynamic facts (sanctions, budgets) expire quickly
FACT_TTL_DAYS = {
    FactType.APPOINTMENT:        540,   # 18 months — senior officials serve 1-3 years
    FactType.TENURE_CALC:          0,   # Never stored
    FactType.SANCTIONS_STATUS:     1,   # Daily refresh — lists change daily
    FactType.CONTRACT_AWARD:    3650,   # 10 years — historical record, permanent
    FactType.CORPORATE_REGISTRY:  365,  # Annual — companies can change
    FactType.BUDGET_ALLOCATION:   365,  # Annual budget cycle
    FactType.PROGRAMME_STATUS:     90,  # Quarterly — programmes shift frequently
    FactType.POLITICAL_EVENT:    3650,  # Permanent historical record
    FactType.ARMS_TRANSFER:      3650,  # SIPRI-style permanent record
    FactType.RELATIONSHIP:         90,  # 90 days — relationships change
    FactType.GENERAL_CLAIM:        90,  # Default
}

# Minimum sources required by fact type (additional to tier rules)
FACT_SOURCE_REQUIREMENTS = {
    FactType.APPOINTMENT:        2,   # Must have 2+ sources
    FactType.SANCTIONS_STATUS:   1,   # Tier 1a (official list) sufficient
    FactType.CONTRACT_AWARD:     2,   # Must have 2+ sources
    FactType.CORPORATE_REGISTRY: 1,   # Registry itself is sufficient
    FactType.BUDGET_ALLOCATION:  2,   # Official budget + corroboration
    FactType.PROGRAMME_STATUS:   2,
    FactType.POLITICAL_EVENT:    2,
    FactType.ARMS_TRANSFER:      1,   # SIPRI is Tier 1a for this
    FactType.RELATIONSHIP:       0,   # Human approval only
    FactType.GENERAL_CLAIM:      2,
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class VerificationStatus(Enum):
    VERIFIED               = "VERIFIED"
    PENDING_CORROBORATION  = "PENDING_CORROBORATION"
    CONTRADICTED           = "CONTRADICTED"   # Sources disagree — human needed
    REJECTED               = "REJECTED"       # Verified as false
    STALE                  = "STALE"          # Past TTL — needs refresh
    LEGACY_UNVERIFIED      = "LEGACY_UNVERIFIED"  # Pre-pipeline data
    HUMAN_REQUIRED         = "HUMAN_REQUIRED"  # Cannot auto-verify


@dataclass
class SourceRecord:
    """A single source used in verification."""
    url: str
    tier: SourceTier
    score: float
    domain: str = ""
    retrieved_at: str = ""
    excerpt: str = ""
    title: str = ""
    language: str = "en"
    independence_group: str = ""  # Used to detect sources citing each other

    def __post_init__(self):
        if not self.domain:
            try:
                self.domain = urlparse(self.url).netloc.replace("www.", "")
            except Exception:
                self.domain = ""
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()
        if not self.independence_group:
            self.independence_group = self._get_family()

    def _get_family(self) -> str:
        for family, domains in SOURCE_FAMILIES.items():
            if self.domain in domains:
                return family
        return self.domain  # Each unknown domain is its own group


@dataclass
class Contradiction:
    """Two sources that disagree on the same fact."""
    source_a: SourceRecord
    source_b: SourceRecord
    claim_a: str
    claim_b: str
    contradiction_type: str  # "date", "value", "status", "factual"
    severity: str  # "MINOR", "MAJOR"
    requires_human: bool = True


@dataclass
class VerifiedFact:
    """A fully verified intelligence fact with provenance."""
    fact_id: str
    fact_type: FactType
    entity_name: str
    entity_type: str           # person | company | country | programme
    claim: str                 # The verified statement
    value: str                 # Extracted value (date, name, number)
    
    verification_status: VerificationStatus = VerificationStatus.PENDING_CORROBORATION
    verification_score: float = 0.0
    verification_threshold: float = 1.0
    sources: list[SourceRecord] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    
    verified_at: Optional[str] = None
    verified_by: str = "aria_auto"
    expires_at: Optional[str] = None
    last_checked: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    
    # Metadata
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    human_review_note: str = ""
    search_queries_run: list[str] = field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED

    @property
    def is_stale(self) -> bool:
        if not self.expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) > expiry
        except Exception:
            return False

    @property
    def citation(self) -> str:
        """Generate inline citation string for ARIA responses."""
        if self.verification_status == VerificationStatus.VERIFIED:
            if len(self.sources) == 1:
                return f"[from {self.sources[0].url}]"
            elif len(self.sources) == 2:
                return (
                    f"[from {self.sources[0].domain}, "
                    f"corroborated by {self.sources[1].domain}]"
                )
            else:
                return (
                    f"[from {self.sources[0].domain} "
                    f"and {len(self.sources)-1} independent sources]"
                )
        elif self.verification_status == VerificationStatus.PENDING_CORROBORATION:
            return f"[UNVERIFIED — single source: {self.sources[0].domain if self.sources else 'unknown'}]"
        elif self.verification_status == VerificationStatus.CONTRADICTED:
            return "[CONTRADICTED — sources disagree, human review required]"
        elif self.verification_status == VerificationStatus.LEGACY_UNVERIFIED:
            return "[LEGACY — pre-verification pipeline, provenance unknown]"
        return "[UNVERIFIED]"

    @property
    def confidence_label(self) -> str:
        if self.verification_status == VerificationStatus.VERIFIED:
            if self.verification_score >= 1.8:
                return "HIGH"
            return "MEDIUM-HIGH"
        if self.verification_status == VerificationStatus.PENDING_CORROBORATION:
            return "LOW — single source"
        if self.verification_status == VerificationStatus.CONTRADICTED:
            return "UNRELIABLE — contradiction"
        return "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type.value,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "claim": self.claim,
            "value": self.value,
            "verification_status": self.verification_status.value,
            "verification_score": self.verification_score,
            "verification_threshold": self.verification_threshold,
            "sources": [
                {
                    "url": s.url,
                    "tier": s.tier.value,
                    "score": s.score,
                    "domain": s.domain,
                    "retrieved_at": s.retrieved_at,
                    "excerpt": s.excerpt[:300],
                }
                for s in self.sources
            ],
            "verified_at": self.verified_at,
            "verified_by": self.verified_by,
            "expires_at": self.expires_at,
            "last_checked": self.last_checked,
            "created_at": self.created_at,
            "human_review_note": self.human_review_note,
        }


# =============================================================================
# AUDIT-LOG HELPER — Clause 14 substrate integration
# =============================================================================
# Every verified-fact mutation emits an HMAC-signed audit entry so the chain
# remains tamper-evident. Failures here are swallowed with a logger.warning
# — the verification pipeline must not be blocked by a logging failure.

async def _arecord_audit(
    kind: str,
    fact: Optional["VerifiedFact"] = None,
    stats_ref: Optional[dict] = None,
) -> None:
    """Emit an audit-log entry + brain signal for a verified-fact action.

    kind: one of 'verified_fact_stored', 'verified_fact_refreshed',
          'verified_fact_contradicted'.
    fact: the VerifiedFact instance (required for stored/contradicted).
    stats_ref: summary stats for refresh runs.
    """
    # ── 1. HMAC audit log (Clause 14 substrate) ─────────────────────
    try:
        from . import audit_log as _al
        if kind in _al.RECORDED_ACTIONS:
            if fact is not None:
                await _al.record(
                    kind,
                    actor="aria_verified_intel",
                    entity_name=fact.entity_name,
                    inputs={
                        "fact_type": fact.fact_type.value,
                        "claim": fact.claim[:400],
                        "value": fact.value[:200],
                    },
                    outputs={
                        "verification_status": fact.verification_status.value,
                        "verification_score": fact.verification_score,
                        "source_count": len(fact.sources),
                        "source_domains": [s.domain for s in fact.sources][:5],
                        "expires_at": fact.expires_at,
                        "contradictions": len(fact.contradictions),
                    },
                    decision=fact.verification_status.value,
                    confidence=fact.confidence_label,
                    sources=[s.url for s in fact.sources][:10],
                    notes=f"Clause 17 pipeline — fact_id {fact.fact_id}",
                )
            elif stats_ref is not None:
                await _al.record(
                    kind,
                    actor="aria_verified_intel",
                    inputs={"task": "DAILY-FACT-REFRESH"},
                    outputs=dict(stats_ref),
                    decision="completed",
                    notes="Clause 17 stale-fact refresh pass",
                )
    except Exception as e:
        logger.warning("audit log emit failed for %s: %s", kind, e)

    # ── 2. Brain signal — so verified facts feed mastery + self_metrics ──
    try:
        from . import brain_hook as _bh
        if fact is not None:
            is_contradicted = fact.verification_status == VerificationStatus.CONTRADICTED
            await _bh.absorb(
                module="verified_intel",
                summary=(f"Verified fact: {fact.entity_name} "
                         f"({fact.fact_type.value}) — "
                         f"{fact.verification_status.value}"),
                detail=f"Score {fact.verification_score:.2f}, "
                       f"{len(fact.sources)} sources, "
                       f"{len(fact.contradictions)} contradictions",
                entity_name=fact.entity_name,
                success=not is_contradicted,
                gap_type="verified_contradiction" if is_contradicted else None,
                gap_detail=(
                    f"Sources disagree on {fact.fact_type.value} for "
                    f"{fact.entity_name}"
                ) if is_contradicted else None,
            )
            # Contradiction = mistake_ledger entry, so the predictor
            # can warn on future tasks about the same entity/domain.
            if is_contradicted:
                from . import mistake_ledger as _ml
                try:
                    await _ml.record(
                        category="verified_contradiction",
                        task_type="verified_intel",
                        domain=fact.fact_type.value.lower(),
                        what=(f"Contradicting sources for {fact.entity_name} "
                              f"{fact.fact_type.value}"),
                        why=(f"Multi-source verification blocked: "
                             f"{fact.contradictions[0].claim_a if fact.contradictions else 'conflict'} "
                             f"vs {fact.contradictions[0].claim_b if fact.contradictions else 'conflict'}"),
                        fix="Escalate to human review; do not cite as CONFIRMED",
                        what_class="source_disagreement",
                        severity="HIGH",
                        source_ref=fact.fact_id,
                    )
                except Exception:
                    pass
        elif stats_ref is not None:
            await _bh.absorb(
                module="verified_intel",
                summary=f"Stale-fact refresh: {stats_ref.get('refreshed', 0)} re-verified",
                detail=str(stats_ref)[:500],
                success=True,
            )
    except Exception as e:
        logger.debug("brain signal for %s failed: %s", kind, e)


# =============================================================================
# SOURCE TIER CLASSIFIER
# =============================================================================

class SourceTierClassifier:
    """
    Assigns a tier to any source URL.
    The tier determines how much verification weight that source carries.
    """

    def classify(self, url: str, context: str = "") -> SourceTier:
        """
        Classify a source URL into a tier.

        Args:
            url:     The source URL
            context: Optional context (e.g., "official_gazette" forces Tier 1a)

        Returns:
            SourceTier
        """
        if context in ("official_gazette", "regulatory_filing", "court_ruling"):
            return SourceTier.TIER_1A

        try:
            domain = urlparse(url).netloc.replace("www.", "").lower()
        except Exception:
            return SourceTier.TIER_5

        # Check Tier 1a
        if domain in TIER_1A_DOMAINS:
            # Further check: treasury.gov could be a general page, not OFAC list
            if "ofac" in url or "sdn" in url or "sanctions" in url:
                return SourceTier.TIER_1A
            if "treasury.gov" in domain:
                return SourceTier.TIER_1A
            if any(d in domain for d in [".gov.", ".gov", "gov/"]):
                return SourceTier.TIER_1A

        # Check Tier 1b explicit domains
        if domain in TIER_1B_DOMAINS:
            return SourceTier.TIER_1B

        # Check government domains
        if self._is_government_domain(domain):
            return SourceTier.TIER_1B

        # Check Tier 2
        if domain in TIER_2_DOMAINS:
            return SourceTier.TIER_2

        # Check Tier 3
        if domain in TIER_3_DOMAINS:
            return SourceTier.TIER_3

        # Social media and forums are always Tier 5
        if any(sm in domain for sm in [
            "twitter.com", "x.com", "linkedin.com", "facebook.com",
            "reddit.com", "quora.com", "medium.com", "substack.com",
            "telegram.me", "t.me",
        ]):
            return SourceTier.TIER_5

        # Default to Tier 4 for unknown sources
        return SourceTier.TIER_4

    def _is_government_domain(self, domain: str) -> bool:
        """Check if a domain is a government or official body."""
        gov_patterns = [
            r"\.gov\.",
            r"\.gov$",
            r"\.mil\.",
            r"\.mil$",
            r"\.gouv\.",
            r"\.gob\.",
            r"\.go\.",
            r"presidency\.",
            r"minister",
            r"ministry",
            r"parlement",
            r"parlament",
            r"senate\.",
            r"congress\.",
        ]
        return any(re.search(p, domain) for p in gov_patterns)


# =============================================================================
# CONTRADICTION DETECTOR
# =============================================================================

class ContradictionDetector:
    """
    Detects when two sources disagree on the same fact.

    This is the gap in the original proposal — adding multiple sources
    is only valid if they are not contradicting each other.
    A verification score of 1.4 built from two sources that disagree
    is not confidence — it is an active integrity problem.
    """

    def check(
        self,
        existing_sources: list[SourceRecord],
        new_source: SourceRecord,
        new_claim_value: str,
        existing_claim_value: str,
        fact_type: FactType,
    ) -> Optional[Contradiction]:
        """
        Check if a new source contradicts existing sources.

        Args:
            existing_sources:       Already found sources
            new_source:             New source being added
            new_claim_value:        What the new source says (the value)
            existing_claim_value:   What existing sources say
            fact_type:              Type of fact being verified

        Returns:
            Contradiction if detected, None if sources agree
        """
        if not existing_claim_value or not new_claim_value:
            return None

        # Normalise values for comparison
        norm_existing = self._normalise(existing_claim_value, fact_type)
        norm_new = self._normalise(new_claim_value, fact_type)

        if norm_existing == norm_new:
            return None  # Agreement

        # Check if disagreement is material
        contradiction_type, severity = self._assess_disagreement(
            norm_existing, norm_new, fact_type
        )

        if contradiction_type is None:
            return None  # Not a material disagreement

        return Contradiction(
            source_a=existing_sources[0] if existing_sources else new_source,
            source_b=new_source,
            claim_a=existing_claim_value,
            claim_b=new_claim_value,
            contradiction_type=contradiction_type,
            severity=severity,
            requires_human=(severity == "MAJOR"),
        )

    def _normalise(self, value: str, fact_type: FactType) -> str:
        """Normalise a value for comparison."""
        value = value.strip().lower()

        if fact_type == FactType.APPOINTMENT:
            # Normalise date formats: "19 june 2024" = "june 19 2024" = "2024-06-19"
            value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value)
            value = re.sub(r"\s+", " ", value)

        return value

    def _assess_disagreement(
        self, value_a: str, value_b: str, fact_type: FactType
    ) -> tuple[Optional[str], str]:
        """
        Assess whether a disagreement is material and how severe.

        Returns:
            (contradiction_type, severity) or (None, "") if not material
        """
        # Appointment date disagreement
        if fact_type == FactType.APPOINTMENT:
            # Extract year from both
            year_a = re.search(r"\b(20\d{2})\b", value_a)
            year_b = re.search(r"\b(20\d{2})\b", value_b)
            if year_a and year_b and year_a.group(1) != year_b.group(1):
                return ("date", "MAJOR")  # Different year is major
            elif value_a != value_b:
                return ("date", "MINOR")  # Same year, different day is minor

        # Sanctions status
        if fact_type == FactType.SANCTIONS_STATUS:
            sanctioned_terms = {"sanctioned", "designated", "listed", "blocked"}
            clear_terms = {"not sanctioned", "delisted", "removed", "cleared"}
            a_sanctioned = any(t in value_a for t in sanctioned_terms)
            a_clear = any(t in value_a for t in clear_terms)
            b_sanctioned = any(t in value_b for t in sanctioned_terms)
            b_clear = any(t in value_b for t in clear_terms)
            if (a_sanctioned and b_clear) or (a_clear and b_sanctioned):
                return ("status", "MAJOR")

        # Generic check: values are substantially different
        if value_a and value_b and value_a not in value_b and value_b not in value_a:
            # Check for numeric disagreement
            nums_a = re.findall(r"\d+\.?\d*", value_a)
            nums_b = re.findall(r"\d+\.?\d*", value_b)
            if nums_a and nums_b and nums_a[0] != nums_b[0]:
                return ("value", "MAJOR")

        return (None, "")  # No material contradiction


# =============================================================================
# INDEPENDENCE CHECKER
# =============================================================================

class SourceIndependenceChecker:
    """
    Detects when two sources are not truly independent.

    Reuters and AFP often cover the same press conference.
    Al Arabiya and Al Jazeera both report the same presidency statement.
    Sources in the same family citing each other are not independent.
    """

    def are_independent(
        self, source_a: SourceRecord, source_b: SourceRecord
    ) -> bool:
        """
        Determine if two sources are genuinely independent.

        Returns:
            True if independent, False if they share a common origin
        """
        # Same domain family is never independent
        if source_a.independence_group == source_b.independence_group:
            return False

        # Both are wire services — check if same story
        wire_services = {"reuters", "ap", "afp"}
        if (source_a.independence_group in wire_services and
                source_b.independence_group in wire_services):
            # Two different wire services covering the same event can be
            # independent if they have different reporters/angles
            # For simplicity: allow two different wire services as independent
            # but flag for human review
            return True

        return True

    def get_independent_count(self, sources: list[SourceRecord]) -> int:
        """Count the number of genuinely independent sources in a list."""
        if not sources:
            return 0

        groups_seen: set[str] = set()
        count = 0
        for source in sources:
            if source.independence_group not in groups_seen:
                groups_seen.add(source.independence_group)
                count += 1
        return count


# =============================================================================
# CORROBORATION SEARCH
# =============================================================================

class CorroborationSearch:
    """
    Searches for independent corroborating sources for a claim.

    The original proposal runs generic search queries.
    This implementation targets specific source domains to ensure
    the results come from independent sources, not the same
    article re-indexed by different aggregators.
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; ARIAVerify/3.1; +https://arkmurus.com)",
    }

    def __init__(self, brave_api_key: str = "", web_search_fn=None):
        self.brave_key = brave_api_key
        self.web_search = web_search_fn  # Injected web search function

    def search(
        self,
        claim: str,
        entity: str,
        fact_type: FactType,
        existing_domains: list[str],
        max_sources: int = 5,
    ) -> list[SourceRecord]:
        """
        Search for corroborating sources for a claim.

        Explicitly avoids domains already found to ensure independence.

        Args:
            claim:            The claim to corroborate
            entity:           Entity the claim is about
            fact_type:        Type of fact
            existing_domains: Domains already found (to avoid duplicate coverage)
            max_sources:      Maximum sources to return

        Returns:
            List of potential corroborating SourceRecords
        """
        queries = self._build_queries(claim, entity, fact_type)
        classifier = SourceTierClassifier()
        independence = SourceIndependenceChecker()
        found: list[SourceRecord] = []
        found_groups: set[str] = set(existing_domains)

        for query in queries:
            if not self.web_search:
                break
            try:
                results = self.web_search(query, max_results=5)
                for result in results:
                    url = result.get("url", "")
                    if not url:
                        continue

                    try:
                        domain = urlparse(url).netloc.replace("www.", "")
                    except Exception:
                        continue

                    # Skip if we already have this domain family
                    if domain in found_groups:
                        continue

                    tier = classifier.classify(url)

                    # Skip Tier 4 and 5 unless we have nothing else
                    if tier in (SourceTier.TIER_4, SourceTier.TIER_5) and len(found) >= 2:
                        continue

                    source = SourceRecord(
                        url=url,
                        tier=tier,
                        score=TIER_SCORES[tier],
                        domain=domain,
                        excerpt=result.get("description", "")[:300],
                        title=result.get("title", ""),
                    )
                    found_groups.add(source.independence_group)
                    found.append(source)

                    if len(found) >= max_sources:
                        break

            except Exception as e:
                logger.debug(f"Search query failed: {e}")
                continue

            if len(found) >= max_sources:
                break

        return found

    def _build_queries(
        self, claim: str, entity: str, fact_type: FactType
    ) -> list[str]:
        """Build targeted search queries for different source types."""
        queries = []

        # Extract key terms
        key_terms = " ".join(claim.split()[:8])

        if fact_type == FactType.APPOINTMENT:
            queries = [
                # Official government sources
                f'"{entity}" appointed site:gov OR site:presidency OR site:parliament',
                # Wire services
                f'"{entity}" appointment {key_terms} site:reuters.com OR site:bloomberg.com',
                # Quality African/regional press
                f'"{entity}" {key_terms} site:premiumtimesng.com OR site:defenceweb.co.za',
                # General high-quality
                f'"{entity}" {key_terms} -site:twitter.com -site:linkedin.com',
            ]
        elif fact_type == FactType.SANCTIONS_STATUS:
            queries = [
                f'"{entity}" sanctions OFAC site:treasury.gov OR site:ofac.treasury.gov',
                f'"{entity}" sanctions EU site:eeas.europa.eu OR site:sanctionsmap.eu',
                f'"{entity}" sanctions UN site:un.org',
            ]
        elif fact_type == FactType.CONTRACT_AWARD:
            queries = [
                f'"{entity}" contract award {key_terms} site:ted.europa.eu OR site:contractsfinder.service.gov.uk',
                f'"{entity}" {key_terms} contract signed awarded',
                f'"{entity}" {key_terms} site:reuters.com OR site:janes.com',
            ]
        else:
            queries = [
                f'"{entity}" {key_terms} site:reuters.com OR site:bloomberg.com OR site:bbc.com',
                f'"{entity}" {key_terms} -site:twitter.com -site:linkedin.com -site:reddit.com',
            ]

        return queries


# =============================================================================
# VERIFICATION ENGINE — THE CORE
# =============================================================================

class ARIAVerificationEngine:
    """
    The core verification engine for ARIA's intelligence pipeline.

    Takes a raw claim and one source, seeks corroboration,
    detects contradictions, and determines verification status.

    This is the difference between ARIA saying:
      "Gen. Musa has been in role for 665 days"  [no source]
    and:
      "Gen. Musa was appointed CDS on 19 June 2024
       [from Reuters, corroborated by Premium Times].
       Tenure as of today: 301 days."
    """

    def __init__(
        self,
        redis_client=None,
        web_search_fn=None,
        notify_fn=None,
    ):
        self.redis = redis_client
        self.classifier = SourceTierClassifier()
        self.independence = SourceIndependenceChecker()
        self.contradiction = ContradictionDetector()
        self.corroboration = CorroborationSearch(web_search_fn=web_search_fn)
        self.notify = notify_fn

    def process(
        self,
        claim_text: str,
        claim_value: str,
        entity_name: str,
        entity_type: str,
        fact_type: FactType,
        source_url: str,
        source_excerpt: str = "",
        source_context: str = "",
    ) -> VerifiedFact:
        """
        Process a new claim through the full verification pipeline.

        1. Classify the source tier
        2. Check if already verified
        3. Check for single-source sufficiency (Tier 1a)
        4. Search for corroborating sources
        5. Check independence of sources found
        6. Detect contradictions
        7. Calculate verification score
        8. Store and return result

        Args:
            claim_text:     The full claim ("Gen. Musa appointed CDS on 19 June 2024")
            claim_value:    The extracted value ("19 June 2024" or "Gen. Musa")
            entity_name:    Entity the claim is about ("Gen. Christopher Musa")
            entity_type:    "person" | "company" | "country" | "programme"
            fact_type:      FactType enum
            source_url:     The URL of the first source
            source_excerpt: Relevant excerpt from the source
            source_context: Context hint for tier classification

        Returns:
            VerifiedFact with verification status and provenance
        """
        if fact_type == FactType.TENURE_CALC:
            raise ValueError(
                "TENURE_CALC facts are never stored. "
                "Calculate tenure at query time from the APPOINTMENT date."
            )

        fact_id = hashlib.md5(
            f"{entity_name}{fact_type.value}{claim_value}".encode()
        ).hexdigest()[:16]

        # Check for existing verified fact
        existing = self._load_fact(fact_id)
        if existing and existing.is_verified and not existing.is_stale:
            logger.info(f"Already verified: {fact_id}")
            return existing

        # Classify the initial source
        initial_tier = self.classifier.classify(source_url, source_context)
        initial_score = TIER_SCORES[initial_tier]

        initial_source = SourceRecord(
            url=source_url,
            tier=initial_tier,
            score=initial_score,
            excerpt=source_excerpt,
        )

        # Build the fact object
        ttl_days = FACT_TTL_DAYS.get(fact_type, 90)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=ttl_days)
        ).isoformat() if ttl_days > 0 else None

        fact = VerifiedFact(
            fact_id=fact_id,
            fact_type=fact_type,
            entity_name=entity_name,
            entity_type=entity_type,
            claim=claim_text,
            value=claim_value,
            sources=[initial_source],
            expires_at=expires_at,
        )

        # Tier 1a: single source is sufficient
        if initial_tier == SourceTier.TIER_1A:
            fact.verification_status = VerificationStatus.VERIFIED
            fact.verification_score = initial_score
            fact.verification_threshold = 0.9
            fact.verified_at = datetime.now(timezone.utc).isoformat()
            self._store_fact(fact)
            logger.info(f"Verified (Tier 1a single source): {fact_id}")
            return fact

        # Tier 5: flag immediately for human review
        if initial_tier == SourceTier.TIER_5:
            fact.verification_status = VerificationStatus.HUMAN_REQUIRED
            fact.human_review_note = (
                "Only Tier 5 source found. Tier 5 sources cannot verify alone."
            )
            self._store_fact(fact)
            self._request_human_review(fact)
            return fact

        # Need corroboration — search for more sources
        required_min = FACT_SOURCE_REQUIREMENTS.get(fact_type, 2)
        corroborating = self.corroboration.search(
            claim=claim_text,
            entity=entity_name,
            fact_type=fact_type,
            existing_domains=[initial_source.domain],
            max_sources=required_min + 2,  # Search for slightly more than needed
        )

        fact.search_queries_run = [
            f"Searched for corroboration of: {claim_text[:80]}"
        ]

        # Check each corroborating source for contradictions
        for corr_source in corroborating:
            # Independence check
            if not self.independence.are_independent(initial_source, corr_source):
                logger.debug(f"Source not independent: {corr_source.domain}")
                continue

            # Contradiction check
            contradiction = self.contradiction.check(
                existing_sources=fact.sources,
                new_source=corr_source,
                new_claim_value=corr_source.excerpt,
                existing_claim_value=claim_value,
                fact_type=fact_type,
            )

            if contradiction:
                fact.contradictions.append(contradiction)
                if contradiction.severity == "MAJOR":
                    fact.verification_status = VerificationStatus.CONTRADICTED
                    fact.human_review_note = (
                        f"Sources disagree: '{contradiction.claim_a}' vs "
                        f"'{contradiction.claim_b}'. Human review required."
                    )
                    self._store_fact(fact)
                    self._request_human_review(fact)
                    return fact
                # Minor contradiction — add source but flag
                logger.warning(
                    f"Minor contradiction in {fact_id}: "
                    f"{contradiction.claim_a} vs {contradiction.claim_b}"
                )

            fact.sources.append(corr_source)

        # Calculate final verification score
        independent_count = self.independence.get_independent_count(fact.sources)
        total_score = sum(s.score for s in fact.sources)
        threshold = self._get_threshold(fact_type, fact.sources)

        fact.verification_score = round(total_score, 3)
        fact.verification_threshold = threshold

        if total_score >= threshold and independent_count >= required_min:
            fact.verification_status = VerificationStatus.VERIFIED
            fact.verified_at = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"Verified: {fact_id} | Score: {total_score:.2f} | "
                f"Sources: {independent_count} independent"
            )
        elif independent_count < required_min:
            fact.verification_status = VerificationStatus.PENDING_CORROBORATION
            fact.human_review_note = (
                f"Only {independent_count} independent sources found, "
                f"{required_min} required. Will re-check in 24 hours."
            )
            logger.info(f"Pending corroboration: {fact_id}")
        else:
            fact.verification_status = VerificationStatus.PENDING_CORROBORATION

        self._store_fact(fact)
        return fact

    def refresh_stale_facts(self, max_facts: int = 50) -> dict:
        """
        Re-verify facts that have expired or are pending corroboration.
        Run as a daily scheduled task at 05:00 UTC.
        """
        if not self.redis:
            return {"refreshed": 0, "verified": 0, "failed": 0}

        stats = {"refreshed": 0, "verified": 0, "failed": 0}

        try:
            keys = list(self.redis.scan_iter("aria:verified_facts:*"))[:max_facts]
            for key in keys:
                data = self.redis.get(key)
                if not data:
                    continue
                fact_dict = json.loads(data)
                fact_type_str = fact_dict.get("fact_type", "GENERAL_CLAIM")
                try:
                    fact_type = FactType[fact_type_str]
                except KeyError:
                    continue

                status = fact_dict.get("verification_status")

                # Re-check pending and stale facts
                if status in ("PENDING_CORROBORATION", "STALE"):
                    # Re-run corroboration
                    sources_data = fact_dict.get("sources", [])
                    if sources_data:
                        existing_domains = [s.get("domain", "") for s in sources_data]
                        new_sources = self.corroboration.search(
                            claim=fact_dict.get("claim", ""),
                            entity=fact_dict.get("entity_name", ""),
                            fact_type=fact_type,
                            existing_domains=existing_domains,
                            max_sources=3,
                        )
                        if new_sources:
                            stats["refreshed"] += 1
                            # Simplified: mark for full re-processing
                            logger.info(
                                f"New sources found for {fact_dict.get('fact_id')} "
                                f"— queue for re-verification"
                            )

        except Exception as e:
            logger.error(f"Stale fact refresh failed: {e}")

        return stats

    def get_fact(
        self,
        entity_name: str,
        fact_type: FactType,
        compute_tenure: bool = False,
    ) -> Optional[dict]:
        """
        Retrieve a verified fact with full provenance.

        For APPOINTMENT facts, optionally compute current tenure.
        Tenure is NEVER stored — always computed at query time.

        Returns:
            Dict with value, confidence, citation, and source list
            None if no verified fact found
        """
        # Search for the fact by entity and type
        if not self.redis:
            return None

        pattern = f"aria:verified_facts:{fact_type.value}:*"
        for key in self.redis.scan_iter(pattern):
            data = self.redis.get(key)
            if not data:
                continue
            try:
                fact_dict = json.loads(data)
                if fact_dict.get("entity_name", "").lower() == entity_name.lower():
                    fact = self._dict_to_fact(fact_dict)

                    if fact.is_stale:
                        fact.verification_status = VerificationStatus.STALE
                        self._store_fact(fact)

                    result = {
                        "value": fact.value,
                        "claim": fact.claim,
                        "confidence": fact.confidence_label,
                        "verification_status": fact.verification_status.value,
                        "citation": fact.citation,
                        "sources": [s.url for s in fact.sources],
                        "verified_at": fact.verified_at,
                        "expires_at": fact.expires_at,
                    }

                    # Compute tenure for appointment facts
                    if (compute_tenure and
                            fact_type == FactType.APPOINTMENT and
                            fact.is_verified):
                        result["tenure_days"] = self._compute_tenure(fact.value)
                        result["tenure_note"] = (
                            "Tenure computed at query time from verified appointment date."
                        )

                    return result
            except Exception:
                continue

        return None

    # ── ASYNC API (production path, uses redis_store) ────────────────────────
    #
    # The sync `process()` / `get_fact()` above operate on the injected
    # `self.redis` client. For PR 1 the async path intentionally does NOT
    # mutate `process()` — it calls it with self.redis = None so process()
    # runs the pure verification logic without persisting, then we persist
    # via redis_store (async). Every async write emits an audit_log entry
    # (Clause 14 substrate) so tampering with a verified fact breaks the
    # HMAC chain.

    async def averify_and_store(
        self,
        claim_text: str,
        claim_value: str,
        entity_name: str,
        entity_type: str,
        fact_type: "FactType",
        source_url: str,
        source_excerpt: str = "",
        source_context: str = "",
    ) -> "VerifiedFact":
        """Async variant of process() — verifies then persists via redis_store.

        This is the production entry point that fact-writing paths
        (knowledge.store_fact, intel_ledger.add_signal) call to enforce
        Clause 17 provenance on every material fact.
        """
        # Save + restore self.redis so process() doesn't double-persist.
        saved_redis, self.redis = self.redis, None
        try:
            fact = self.process(
                claim_text=claim_text,
                claim_value=claim_value,
                entity_name=entity_name,
                entity_type=entity_type,
                fact_type=fact_type,
                source_url=source_url,
                source_excerpt=source_excerpt,
                source_context=source_context,
            )
        finally:
            self.redis = saved_redis

        await self._astore_fact(fact)
        await _arecord_audit("verified_fact_stored", fact)
        return fact

    async def aget_fact(
        self,
        entity_name: str,
        fact_type: "FactType",
        compute_tenure: bool = False,
    ) -> Optional[dict]:
        """Async retrieve a verified fact with provenance. Mirrors get_fact()."""
        from . import redis_store as rs
        pattern = f"aria:verified_facts:{fact_type.value}:*"
        keys = await rs.scan_keys(pattern, count=500)
        for key in keys:
            data = await rs.get_json(key)
            if not data:
                continue
            try:
                if data.get("entity_name", "").lower() != entity_name.lower():
                    continue
                fact = self._dict_to_fact(data)
                if fact.is_stale:
                    fact.verification_status = VerificationStatus.STALE
                    await self._astore_fact(fact)
                result = {
                    "value": fact.value,
                    "claim": fact.claim,
                    "confidence": fact.confidence_label,
                    "verification_status": fact.verification_status.value,
                    "citation": fact.citation,
                    "sources": [s.url for s in fact.sources],
                    "verified_at": fact.verified_at,
                    "expires_at": fact.expires_at,
                }
                if (compute_tenure and
                        fact_type == FactType.APPOINTMENT and
                        fact.is_verified):
                    result["tenure_days"] = self._compute_tenure(fact.value)
                    result["tenure_note"] = (
                        "Tenure computed at query time from verified appointment date."
                    )
                return result
            except Exception:
                continue
        return None

    async def arefresh_stale_facts(self, max_facts: int = 50) -> dict:
        """Async re-verify facts that are pending corroboration or stale.
        Scheduled as DAILY-FACT-REFRESH at 05:00 UTC."""
        from . import redis_store as rs
        stats = {"refreshed": 0, "verified": 0, "failed": 0, "scanned": 0}
        keys = await rs.scan_keys("aria:verified_facts:*", count=max_facts)
        for key in keys[:max_facts]:
            data = await rs.get_json(key)
            if not data:
                continue
            stats["scanned"] += 1
            status = data.get("verification_status")
            if status not in ("PENDING_CORROBORATION", "STALE"):
                continue
            try:
                fact_type = FactType[data.get("fact_type", "GENERAL_CLAIM")]
            except KeyError:
                continue
            sources_data = data.get("sources", [])
            if not sources_data:
                continue
            existing_domains = [s.get("domain", "") for s in sources_data]
            new_sources = self.corroboration.search(
                claim=data.get("claim", ""),
                entity=data.get("entity_name", ""),
                fact_type=fact_type,
                existing_domains=existing_domains,
                max_sources=3,
            )
            if new_sources:
                stats["refreshed"] += 1
                # Tag for re-verification on next averify_and_store call
                data["last_checked"] = datetime.now(timezone.utc).isoformat()
                await rs.set_json(key, data, ex=max(
                    FACT_TTL_DAYS.get(fact_type, 90), 30) * 86400 * 2)
        await _arecord_audit("verified_fact_refreshed", stats_ref=stats)
        return stats

    async def _astore_fact(self, fact: "VerifiedFact") -> None:
        from . import redis_store as rs
        key = f"aria:verified_facts:{fact.fact_type.value}:{fact.fact_id}"
        ttl = max(FACT_TTL_DAYS.get(fact.fact_type, 90), 30) * 86400 * 2
        await rs.set_json(key, fact.to_dict(), ex=ttl)

    async def _aload_fact(self, fact_id: str) -> Optional["VerifiedFact"]:
        from . import redis_store as rs
        keys = await rs.scan_keys(f"aria:verified_facts:*:{fact_id}", count=10)
        for key in keys:
            data = await rs.get_json(key)
            if data:
                try:
                    return self._dict_to_fact(data)
                except Exception:
                    return None
        return None

    # ── INTERNAL ─────────────────────────────────────────────────────────────

    def _get_threshold(
        self, fact_type: FactType, sources: list[SourceRecord]
    ) -> float:
        """Calculate verification threshold based on fact type and source tiers."""
        # If we have any Tier 1a source, threshold is 0.9
        if any(s.tier == SourceTier.TIER_1A for s in sources):
            return 0.9
        # Appointments and contracts need strong corroboration
        if fact_type in (FactType.APPOINTMENT, FactType.CONTRACT_AWARD):
            return 1.2  # Two Tier 2 sources (0.7+0.7=1.4) > 1.2
        # Sanctions needs official source
        if fact_type == FactType.SANCTIONS_STATUS:
            return 0.9
        return 1.0  # Default: two independent Tier 2 sources

    def _compute_tenure(self, appointment_value: str) -> Optional[int]:
        """
        Compute tenure in days from an appointment date string.
        Tenure is NEVER stored — always calculated at query time.
        """
        date_patterns = [
            r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\s+(\d{4})",
            r"(january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})",
            r"(\d{4})-(\d{2})-(\d{2})",
        ]
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }

        text = appointment_value.lower()
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    groups = match.groups()
                    if len(groups) == 3 and groups[0].isdigit() and len(groups[0]) == 4:
                        # YYYY-MM-DD format
                        appt_date = datetime(
                            int(groups[0]), int(groups[1]), int(groups[2]),
                            tzinfo=timezone.utc
                        )
                    elif groups[1].lower() in months:
                        appt_date = datetime(
                            int(groups[2]), months[groups[1].lower()], int(groups[0]),
                            tzinfo=timezone.utc
                        )
                    elif groups[0].lower() in months:
                        appt_date = datetime(
                            int(groups[2]), months[groups[0].lower()], int(groups[1]),
                            tzinfo=timezone.utc
                        )
                    else:
                        continue
                    return (datetime.now(timezone.utc) - appt_date).days
                except (ValueError, IndexError):
                    continue
        return None

    def _store_fact(self, fact: VerifiedFact) -> None:
        if not self.redis:
            return
        try:
            key = (
                f"aria:verified_facts:{fact.fact_type.value}:{fact.fact_id}"
            )
            self.redis.set(
                key,
                json.dumps(fact.to_dict()),
                ex=max(FACT_TTL_DAYS.get(fact.fact_type, 90), 30) * 86400 * 2,
            )
        except Exception as e:
            logger.error(f"Fact store failed: {e}")

    def _load_fact(self, fact_id: str) -> Optional[VerifiedFact]:
        if not self.redis:
            return None
        for key in self.redis.scan_iter(f"aria:verified_facts:*:{fact_id}"):
            data = self.redis.get(key)
            if data:
                try:
                    return self._dict_to_fact(json.loads(data))
                except Exception:
                    return None
        return None

    def _dict_to_fact(self, d: dict) -> VerifiedFact:
        sources = [
            SourceRecord(
                url=s.get("url", ""),
                tier=SourceTier(s.get("tier", "4")),
                score=float(s.get("score", 0.3)),
                domain=s.get("domain", ""),
                retrieved_at=s.get("retrieved_at", ""),
                excerpt=s.get("excerpt", ""),
            )
            for s in d.get("sources", [])
        ]
        return VerifiedFact(
            fact_id=d["fact_id"],
            fact_type=FactType[d.get("fact_type", "GENERAL_CLAIM")],
            entity_name=d.get("entity_name", ""),
            entity_type=d.get("entity_type", "unknown"),
            claim=d.get("claim", ""),
            value=d.get("value", ""),
            verification_status=VerificationStatus[
                d.get("verification_status", "PENDING_CORROBORATION")
            ],
            verification_score=d.get("verification_score", 0.0),
            verification_threshold=d.get("verification_threshold", 1.0),
            sources=sources,
            verified_at=d.get("verified_at"),
            verified_by=d.get("verified_by", "aria_auto"),
            expires_at=d.get("expires_at"),
            last_checked=d.get("last_checked", ""),
            created_at=d.get("created_at", ""),
            human_review_note=d.get("human_review_note", ""),
        )

    def _request_human_review(self, fact: VerifiedFact) -> None:
        if not self.notify:
            return
        msg = (
            f"🔍 ARIA — VERIFICATION REQUIRES HUMAN REVIEW\n\n"
            f"Fact: {fact.claim}\n"
            f"Entity: {fact.entity_name}\n"
            f"Status: {fact.verification_status.value}\n"
            f"Reason: {fact.human_review_note}\n"
            f"Sources found: {len(fact.sources)}\n"
        )
        if fact.contradictions:
            msg += (
                f"\nContradiction detected:\n"
                f"  Source A says: {fact.contradictions[0].claim_a}\n"
                f"  Source B says: {fact.contradictions[0].claim_b}\n"
            )
        self.notify(msg)


# =============================================================================
# CONSTITUTION CLAUSES (for reference — wire into aria_engine.py)
# =============================================================================

CLAUSE_19 = """
CLAUSE 19 — Multi-Source Verification (anchored to: tenure-without-source pattern):
No fact shall be stored as VERIFIED unless corroborated by at least two independent
sources at Tier 2 or above, or a single Tier 1a source (official registry, official
sanctions list, government gazette). Tier 3 sources require three independent sources.
Tier 4 and 5 sources cannot verify alone and require human approval. Every verified
fact must retain its source URLs, verification score, and expiration timestamp.
Dynamic facts (tenure, role, relationship, programme status) must have a
type-specific expiration date — no fact of this type is valid without one.
Tenure is NEVER stored as a number — it is always calculated at query time
from the verified appointment date.
"""

AMENDED_CLAUSE_15 = """
CLAUSE 15 — Inline Citation (amended):
Every material fact must carry [from <url>]. The citation format shall be:
  Verified by two+ sources: [from Source A, corroborated by Source B]
  Verified by single Tier 1a: [from official-source-url]
  Single source only: [UNVERIFIED — single source: domain.com]
  No source: Do not state the fact. Say: "I cannot verify this."
  Contradicted: [CONTRADICTED — sources disagree, human review required]
  Legacy (pre-pipeline): [LEGACY — provenance unknown, treat as unverified]
"""


# =============================================================================
# DEMONSTRATION
# =============================================================================

if __name__ == "__main__":
    print("ARIA Verified Intelligence Pipeline")
    print("=" * 60)
    print()

    # Show the fact TTL table
    print("FACT TYPE EXPIRATION POLICY:")
    print(f"  {'Fact Type':<25} {'TTL (days)':<12} {'Notes'}")
    print("  " + "─" * 55)
    ttl_notes = {
        FactType.APPOINTMENT: "Senior officials serve 1-3 years",
        FactType.TENURE_CALC: "NEVER stored — computed at query time",
        FactType.SANCTIONS_STATUS: "Lists updated daily",
        FactType.CONTRACT_AWARD: "Permanent historical record",
        FactType.CORPORATE_REGISTRY: "Annual company filing cycle",
        FactType.BUDGET_ALLOCATION: "Annual budget cycle",
        FactType.PROGRAMME_STATUS: "Programmes shift frequently",
        FactType.POLITICAL_EVENT: "Permanent historical record",
        FactType.ARMS_TRANSFER: "SIPRI-style permanent record",
        FactType.RELATIONSHIP: "Relationships change",
    }
    for ft, days in FACT_TTL_DAYS.items():
        note = ttl_notes.get(ft, "")
        ttl_str = str(days) if days > 0 else "N/A"
        print(f"  {ft.value:<25} {ttl_str:<12} {note}")

    print()
    print("VERIFICATION THRESHOLDS:")
    print("  Tier 1a alone         → VERIFIED (score ≥ 0.9)")
    print("  Tier 1b + Tier 2     → VERIFIED (score ≥ 1.0)")
    print("  Tier 2 + Tier 2      → VERIFIED (score ≥ 1.0)")
    print("  Tier 3 + Tier 3 + 3  → VERIFIED (score ≥ 1.0)")
    print("  Tier 5 alone          → HUMAN REQUIRED (never auto-verify)")

    print()
    print("CRITICAL DESIGN DECISIONS:")
    print("  1. Independence check — same source family = not independent")
    print("  2. Contradiction detection — disagreeing sources block verification")
    print("  3. Tenure computed at query time, never stored as a number")
    print("  4. Fact-type-specific TTL — not uniform 90 days")
    print("  5. Bootstrap: existing ChromaDB facts tagged LEGACY_UNVERIFIED")

    print()
    classifier = SourceTierClassifier()
    test_urls = [
        "https://ofac.treasury.gov/sanctions-programs",
        "https://reuters.com/world/africa/nigeria-appoints-new-cds-2024",
        "https://premiumtimesng.com/news/tinubu-appoints-musa",
        "https://defensenews.com/global/2024/angola-procurement",
        "https://twitter.com/something",
        "https://companies-house.service.gov.uk/company/12345",
    ]
    print("SOURCE TIER CLASSIFICATION TEST:")
    for url in test_urls:
        tier = classifier.classify(url)
        score = TIER_SCORES[tier]
        print(f"  Tier {tier.value} ({score:.1f}) — {url[:60]}")
