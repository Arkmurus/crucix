# =============================================================================
# ARIA v3.1 — Deception Detection Module
# aria_service/intel/deception_detection.py
#
# RESEARCH FOUNDATION:
#   This module is grounded in three validated academic datasets:
#
#   1. Mafiascum Dataset (Bopjesvla/thesis, Bitbucket)
#      700+ online social deception games with ground-truth labels.
#      Extracted features: pronoun ratios, disjunctive language, negation,
#      activity patterns, vocabulary diversity, message length.
#      Key finding: third-person distancing and disjunctive "or" usage
#      are statistically significant predictors of deception.
#      Source: https://bitbucket.org/bopjesvla/thesis/src
#
#   2. UNIDECOR (University of Stuttgart IMS)
#      Cross-domain deception corpus: product reviews, court testimony,
#      hotel reviews, news satire, and casual conversation.
#      Key finding: deception patterns shift by context — indicators
#      that predict lying in reviews differ from those in testimony.
#      Source: https://www.ims.uni-stuttgart.de/data/unidecor
#
#   3. Embedded Lies Dataset (2025, Nature Scientific Reports)
#      Deception mixed with truthful statements — the realistic case.
#      Key finding: pure-lie detection models fail on embedded deception;
#      sentence-level analysis outperforms document-level.
#      Source: search "Embedded Lies Dataset" Nature Scientific Reports 2025
#
# IMPORTANT — WHAT THIS MODULE IS AND IS NOT:
#   This is NOT a lie detector. No such thing exists reliably.
#   This IS a structured risk-scoring tool that flags linguistic and
#   behavioural patterns that are statistically associated with deception
#   across validated research. Combined with other intelligence, these
#   signals raise or lower concern — they do not determine guilt.
#
#   Applied in ARIA's context:
#   - Entity communications during DD investigations
#   - Claims made in capability statements or proposals
#   - Contact intelligence assessment
#   - OSINT analysis of public statements by officials or counterparties
#
# SCORING:
#   Output is a DeceptionRiskScore (0.0-1.0) with a categorical tier
#   and a structured breakdown of which signals were present.
#   The score is a risk indicator, not a verdict.
# =============================================================================

import re
import math
import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger("aria.deception_detection")


# =============================================================================
# RISK TIERS
# =============================================================================

class DeceptionRiskTier(Enum):
    LOW      = "LOW"       # 0.0–0.25: Few or no deception signals
    MODERATE = "MODERATE"  # 0.26–0.50: Some signals present — note and monitor
    ELEVATED = "ELEVATED"  # 0.51–0.70: Multiple signals — apply extra scrutiny
    HIGH     = "HIGH"      # 0.71–1.0: Strong signal cluster — treat as alert


# =============================================================================
# VALIDATED LINGUISTIC INDICATORS
# Source: Mafiascum research + deception literature (Vrij, DePaulo, Newman)
# =============================================================================

# First-person pronouns — ownership and accountability signals
# Research finding: LOWER first-person usage correlates with deception
# (people distance themselves from untrue statements)
FPP_WORDS = {"i", "we", "my", "mine", "our", "ours"}

# Third-person pronouns — distancing language
# Research finding: HIGHER third-person usage correlates with deception
TPP_WORDS = {"he", "she", "it", "him", "her", "his", "hers", "its",
             "they", "them", "their", "theirs"}

# Disjunctive language — "or" signals uncertainty and hedging
# Mafiascum finding: higher "or" ratio is a significant predictor
DISJUNCTIVE_WORDS = {"or"}

# Contradictory language — "but" signals internal contradiction
# Research: high "but" usage associated with managing inconsistency
CONTRADICTORY_WORDS = {"but", "however", "although", "though", "yet",
                        "nevertheless", "nonetheless", "despite"}

# Negation words — research shows deceivers use more negation
# Source: Mafiascum combined.py negation list + Pennebaker LIWC research
NEGATION_WORDS = {
    "cannot", "neither", "never", "no", "nobody", "none", "nope",
    "nor", "not", "nothing", "nowhere", "without", "zero",
    "can't", "couldn't", "didn't", "doesn't", "don't", "hadn't",
    "hasn't", "haven't", "isn't", "weren't", "wasn't", "shouldn't",
    "aren't", "won't",
}

# Hedging and uncertainty markers
# Research: excessive hedging signals unwillingness to commit to truth
HEDGING_WORDS = {
    "maybe", "perhaps", "possibly", "probably", "approximately",
    "roughly", "around", "about", "sort of", "kind of", "somewhat",
    "might", "could", "may", "seem", "appear", "believe", "think",
    "guess", "suppose", "assume", "suggest", "seems",
}

# Vagueness markers — non-specific language
# Research: deceivers avoid specificity that could be contradicted
VAGUENESS_MARKERS = {
    "various", "several", "some", "many", "few", "certain", "different",
    "multiple", "number of", "range of", "series of", "type of",
    "kind of", "etc", "and so on", "and so forth", "among others",
}

# Overly formal distancing language
# UNIDECOR finding: formal register shifts in deceptive business communications
FORMAL_DISTANCING = {
    "one must", "it is noted", "it should be noted", "as per",
    "pursuant to", "in accordance with", "with reference to",
    "with regard to", "it is understood", "it is assumed",
}

# Implicit claim markers — stating consequences without proving premises
IMPLICIT_CLAIMS = {
    "as you know", "obviously", "clearly", "of course", "naturally",
    "it goes without saying", "needless to say", "everyone knows",
    "as is well known", "as stated",
}

# Irrelevant detail inflation
# Embedded Lies finding: truthful embedded statements have appropriate
# detail; deceptive ones either pad or strip detail unnaturally
DETAIL_INFLATION_PHRASES = {
    "as i mentioned", "as i said", "as previously stated",
    "to reiterate", "to clarify", "let me explain", "allow me to",
    "i want to make clear", "to be perfectly clear",
}

# Defensive language — guilt by grammar
DEFENSIVE_PHRASES = {
    "i would never", "i have never", "i assure you", "i promise",
    "trust me", "believe me", "honestly", "to be honest",
    "frankly", "to tell you the truth", "if i'm being honest",
    "i swear", "on my honour", "i can guarantee",
}

# Passive voice constructions — the grammar of avoiding responsibility
PASSIVE_MARKERS = {
    "was done", "was made", "was taken", "was given", "was provided",
    "was established", "was approved", "was agreed", "was confirmed",
    "is understood", "is believed", "is known", "has been", "have been",
    "were done", "were made", "were given",
}


# =============================================================================
# DEFENCE-SPECIFIC DECEPTION INDICATORS
# These supplement the academic features with domain-specific signals
# relevant to Arkmurus's work in defence BD, DD, and procurement
# =============================================================================

DEFENCE_DECEPTION_INDICATORS = {

    "unverifiable_credential": {
        "description": "Claims of credentials, clearances, or relationships that cannot be immediately verified",
        "examples": [
            "former general/minister/director",
            "government clearance",
            "high-level contacts",
            "privileged access",
            "exclusive arrangement",
            "direct presidential access",
        ],
        "weight": 0.15,
        "note": "Common in broker fraud. Verify every credential before relying on it.",
    },

    "artificial_urgency": {
        "description": "Time pressure designed to prevent due diligence",
        "examples": [
            "window closes in 48 hours",
            "decision must be made this week",
            "offer expires",
            "competitors are already in discussions",
            "this opportunity will not come again",
            "act now",
        ],
        "weight": 0.20,
        "note": "Genuine procurement opportunities rarely require immediate commitment without DD.",
    },

    "mandate_without_evidence": {
        "description": "Claims of exclusive mandate or authority without documentation",
        "examples": [
            "sole representative",
            "exclusive mandate",
            "authorised representative",
            "only route",
            "direct channel",
            "special arrangement",
        ],
        "weight": 0.20,
        "note": "Real mandates are documented. Demand the document.",
    },

    "commission_front_loading": {
        "description": "Unusual fee structures that front-load payment before delivery",
        "examples": [
            "retainer before engagement",
            "advance fee",
            "success fee due upon introduction",
            "payment required to secure position",
            "registration fee",
        ],
        "weight": 0.25,
        "note": "Standard brokerage commission is paid on transaction completion. Front-loading is a red flag.",
    },

    "beneficial_ownership_evasion": {
        "description": "Deliberate avoidance of UBO disclosure",
        "examples": [
            "partnership arrangement",
            "nominee",
            "on behalf of interests",
            "consortium",
            "holding vehicle",
            "investment group",
        ],
        "weight": 0.20,
        "note": "Any resistance to UBO disclosure is itself a red flag regardless of reason given.",
    },

    "false_specificity": {
        "description": "Highly specific details in areas that cannot be verified, combined with vagueness in verifiable areas",
        "examples": [
            "USD [very specific amount] has been allocated",
            "the programme was approved on [specific date]",
            "general [specific name] has confirmed",
        ],
        "weight": 0.15,
        "note": "Specific numbers without documents, named officials without verifiable titles — classic false specificity pattern.",
    },
}


# =============================================================================
# RESULT DATA STRUCTURE
# =============================================================================

@dataclass
class DeceptionSignal:
    """A single detected deception signal."""
    category: str
    description: str
    evidence: list[str] = field(default_factory=list)
    weight: float = 0.1
    source: str = ""  # which research framework flagged this


@dataclass
class DeceptionRiskScore:
    """
    Complete deception risk assessment for a piece of text.

    Not a verdict. A structured risk indicator to inform further scrutiny.
    """
    raw_score: float = 0.0          # 0.0-1.0
    tier: DeceptionRiskTier = DeceptionRiskTier.LOW
    signals_detected: list[DeceptionSignal] = field(default_factory=list)
    linguistic_features: dict = field(default_factory=dict)
    context_type: str = "general"   # general, business_communication, testimony, proposal

    # Specific counts
    fpp_ratio: float = 0.0          # first-person pronoun ratio (low = risk)
    tpp_ratio: float = 0.0          # third-person pronoun ratio (high = risk)
    negation_ratio: float = 0.0
    hedging_ratio: float = 0.0
    defensive_count: int = 0
    passive_voice_count: int = 0
    word_count: int = 0
    sentence_count: int = 0

    # Metadata
    analyst_note: str = ""
    confidence: float = 0.0         # confidence in the score itself
    requires_human_review: bool = False

    @property
    def percentage(self) -> str:
        return f"{self.raw_score:.0%}"

    @property
    def summary(self) -> str:
        if not self.signals_detected:
            return f"Deception risk: {self.tier.value} ({self.percentage}) — no significant signals detected."
        top_signals = [s.category for s in self.signals_detected[:3]]
        return (
            f"Deception risk: {self.tier.value} ({self.percentage}) — "
            f"{len(self.signals_detected)} signals: {', '.join(top_signals)}"
        )

    def to_report(self) -> str:
        """Format as a structured deception risk section for ARIA reports."""
        divider = "─" * 55
        lines = [
            divider,
            "DECEPTION RISK ASSESSMENT",
            divider,
            f"  Risk tier:  {self.tier.value}",
            f"  Score:      {self.percentage}",
            f"  Confidence: {self.confidence:.0%}",
            f"  Context:    {self.context_type}",
            "",
            "  LINGUISTIC INDICATORS:",
            f"  First-person pronouns (I/we):  {self.fpp_ratio:.3f} ratio",
            f"  Third-person pronouns (he/they):{self.tpp_ratio:.3f} ratio",
            f"  Negation usage:                {self.negation_ratio:.3f} ratio",
            f"  Hedging/uncertainty:           {self.hedging_ratio:.3f} ratio",
            f"  Defensive assertions:          {self.defensive_count} found",
            f"  Passive voice constructions:   {self.passive_voice_count} found",
            f"  Word count:                    {self.word_count}",
        ]

        if self.signals_detected:
            lines += ["", "  SIGNALS DETECTED:"]
            for sig in self.signals_detected:
                lines.append(f"  [{sig.weight:.0%} weight] {sig.category}: {sig.description}")
                if sig.evidence:
                    for ev in sig.evidence[:2]:
                        lines.append(f"    Evidence: \"{ev[:80]}\"")

        if self.analyst_note:
            lines += ["", f"  NOTE: {self.analyst_note}"]

        lines += [
            "",
            "  IMPORTANT: This score is a risk indicator, not a verdict.",
            "  Elevated scores require additional scrutiny, not automatic rejection.",
            divider,
        ]
        return "\n".join(lines)


# =============================================================================
# DECEPTION ANALYSER
# =============================================================================

class ARIADeceptionAnalyser:
    """
    ARIA's deception risk analysis engine.

    Applies validated linguistic features from Mafiascum research,
    UNIDECOR cross-domain patterns, and Embedded Lies methodology,
    supplemented by defence-sector-specific behavioural indicators.

    Usage:
        analyser = ARIADeceptionAnalyser()

        # Analyse a counterparty communication
        score = analyser.analyse(
            text="We have exclusive access to the Presidential mandate...",
            context_type="business_communication",
        )
        print(score.summary)
        print(score.to_report())

        # Quick check during DD
        if score.tier in (DeceptionRiskTier.ELEVATED, DeceptionRiskTier.HIGH):
            # Flag for enhanced scrutiny
            ...
    """

    # Thresholds derived from Mafiascum cross-validation results
    # The model achieved AUC ~0.57 on pure linguistic features alone.
    # Combined with defence-specific indicators, we expect better performance
    # in the narrower, more structured context of business communications.

    # Baseline ratios from truthful communications (approximate, from LIWC/Mafiascum)
    BASELINE_FPP = 0.060    # ~6% first-person pronouns in honest communication
    BASELINE_TPP = 0.035    # ~3.5% third-person in honest communication
    BASELINE_NEG = 0.020    # ~2% negation in honest communication
    BASELINE_HED = 0.015    # ~1.5% hedging in honest communication

    async def analyse_async(
        self,
        text: str,
        context_type: str = "general",
        reference_entity: str = "",
    ) -> DeceptionRiskScore:
        """Async wrapper that runs the synchronous analyser, then feeds
        the result to brain_hook so DD pipelines + the predictor learn
        from repeat patterns. Use this from any async context (DD
        orchestrator, chat path, autonomous tasks). The plain `analyse`
        method remains available for synchronous callers (CLI, tests)."""
        score = self.analyse(text, context_type=context_type, reference_entity=reference_entity)
        try:
            from . import brain_hook as _bh
            top_signals = ", ".join(s.category for s in score.signals_detected[:3]) or "none"
            await _bh.absorb(
                module="deception_detection",
                summary=(
                    f"Deception score {score.percentage} {score.tier.value} "
                    f"on {context_type}{f' for {reference_entity}' if reference_entity else ''} "
                    f"({score.word_count}w): {top_signals}"
                ),
                detail=score.analyst_note or score.summary,
                entity_name=reference_entity,
                success=True,
                gap_type=("counterparty_deception_high"
                          if score.tier == DeceptionRiskTier.HIGH else None),
                gap_detail=(
                    f"HIGH deception risk on {reference_entity or context_type}: "
                    f"{len(score.signals_detected)} signals — {top_signals}"
                    if score.tier == DeceptionRiskTier.HIGH else None
                ),
                confidence="ASSESSED",
            )
        except Exception:
            from .engine_wiring import wire_failure as _wf
            _wf(
                module="deception_detection",
                detail="brain_hook.absorb failed in analyse_async",
                gap_type="engine_failure",
                source="deception_detection:analyse_async",
            )
            pass
        # R-F996 — wire to brain
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="deception_detection",
            summary="Analyse Async",
            source_id="deception_detection:R-F996",
        )

        return score

    def analyse(
        self,
        text: str,
        context_type: str = "general",
        reference_entity: str = "",
    ) -> DeceptionRiskScore:
        """
        Analyse text for deception risk signals.

        Args:
            text:             The text to analyse
            context_type:     "general" | "business_communication" |
                              "testimony" | "proposal" | "entity_claim"
            reference_entity: Optional entity name for targeted signal detection

        Returns:
            DeceptionRiskScore with full breakdown
        """
        if not text or not text.strip():
            return DeceptionRiskScore(confidence=0.0)

        text_lower = text.lower()
        words = re.findall(r"\b\w+\b", text_lower)
        wc = max(len(words), 1)

        # Very short texts are unreliable — flag but score conservatively
        is_short = wc < 50
        confidence_base = 0.40 if is_short else 0.70

        # ── LINGUISTIC FEATURE EXTRACTION (Mafiascum methodology) ──────────

        # Pronoun ratios
        fpp = sum(words.count(w) for w in FPP_WORDS)
        tpp = sum(words.count(w) for w in TPP_WORDS)
        neg = sum(words.count(w) for w in NEGATION_WORDS)
        hed = sum(words.count(w) for w in HEDGING_WORDS)
        but = sum(words.count(w) for w in CONTRADICTORY_WORDS)

        fpp_ratio = fpp / wc
        tpp_ratio = tpp / wc
        neg_ratio = neg / wc
        hed_ratio = hed / wc
        but_ratio = but / wc

        # Average token length — shorter average = simpler language
        token_length = sum(len(w) for w in words) / wc

        # Vocabulary diversity — lower unique/total = repetitive language
        unique_ratio = len(set(words)) / wc

        # Count sentences (approximate)
        sentence_count = max(len(re.findall(r"[.!?]+", text)), 1)
        avg_sentence_length = wc / sentence_count

        # Defensive assertions count
        defensive_count = sum(
            1 for phrase in DEFENSIVE_PHRASES
            if phrase in text_lower
        )

        # Passive voice constructions
        passive_count = sum(
            1 for phrase in PASSIVE_MARKERS
            if phrase in text_lower
        )

        # Vagueness markers
        vagueness_count = sum(
            1 for marker in VAGUENESS_MARKERS
            if marker in text_lower
        )

        # Implicit claims
        implicit_count = sum(
            1 for phrase in IMPLICIT_CLAIMS
            if phrase in text_lower
        )

        # ── SCORE CONSTRUCTION ─────────────────────────────────────────────

        score_components = []
        signals = []

        # 1. Low first-person pronoun ratio (distancing from claims)
        if fpp_ratio < self.BASELINE_FPP * 0.5 and wc > 50:
            deficit = (self.BASELINE_FPP - fpp_ratio) / self.BASELINE_FPP
            score_components.append(min(deficit * 0.12, 0.12))
            signals.append(DeceptionSignal(
                category="low_first_person_usage",
                description=(
                    f"First-person pronoun ratio ({fpp_ratio:.3f}) is below baseline "
                    f"({self.BASELINE_FPP:.3f}). Research indicates reduced first-person "
                    "usage when distancing from untruthful claims."
                ),
                weight=0.12,
                source="Mafiascum research + DePaulo et al.",
            ))

        # 2. High third-person pronoun ratio (distancing language)
        if tpp_ratio > self.BASELINE_TPP * 1.5:
            excess = (tpp_ratio - self.BASELINE_TPP) / self.BASELINE_TPP
            score_components.append(min(excess * 0.10, 0.10))
            signals.append(DeceptionSignal(
                category="high_third_person_usage",
                description=(
                    f"Third-person pronoun ratio ({tpp_ratio:.3f}) exceeds baseline. "
                    "Elevated use of he/she/they indicates linguistic distancing."
                ),
                weight=0.10,
                source="Mafiascum feature extraction",
            ))

        # 3. High negation usage
        if neg_ratio > self.BASELINE_NEG * 2.0:
            score_components.append(0.08)
            signals.append(DeceptionSignal(
                category="elevated_negation",
                description=(
                    f"Negation ratio ({neg_ratio:.3f}) significantly above baseline. "
                    "Excessive denial and negation language is a documented deception signal."
                ),
                weight=0.08,
                source="Mafiascum + LIWC research",
            ))

        # 4. High hedging / uncertainty language
        if hed_ratio > self.BASELINE_HED * 2.0:
            score_components.append(0.07)
            signals.append(DeceptionSignal(
                category="excessive_hedging",
                description=(
                    f"Hedging language ratio ({hed_ratio:.3f}) significantly above baseline. "
                    "Excessive uncertainty markers (maybe, perhaps, possibly) signal "
                    "unwillingness to commit to verifiable statements."
                ),
                weight=0.07,
                source="UNIDECOR cross-domain analysis",
            ))

        # 5. Defensive assertions — "I would never", "trust me"
        if defensive_count >= 2:
            contribution = min(defensive_count * 0.05, 0.15)
            score_components.append(contribution)
            examples = [p for p in DEFENSIVE_PHRASES if p in text_lower][:3]
            signals.append(DeceptionSignal(
                category="defensive_assertions",
                description=(
                    f"{defensive_count} defensive assertion phrases detected. "
                    "Unprompted denials and trust appeals are associated with deception "
                    "across multiple research corpora."
                ),
                evidence=examples,
                weight=contribution,
                source="Vrij deception research + Embedded Lies findings",
            ))

        # 6. Excessive passive voice
        if passive_count >= 3:
            contribution = min(passive_count * 0.03, 0.10)
            score_components.append(contribution)
            signals.append(DeceptionSignal(
                category="passive_voice_concentration",
                description=(
                    f"{passive_count} passive voice constructions. "
                    "Passive voice is used to obscure agency — who did what, when, and why."
                ),
                weight=contribution,
                source="UNIDECOR business communication analysis",
            ))

        # 7. High contradictory language (but/however)
        if but_ratio > 0.04:
            score_components.append(0.06)
            signals.append(DeceptionSignal(
                category="contradictory_language",
                description=(
                    f"Contradictory conjunction ratio ({but_ratio:.3f}) elevated. "
                    "Frequent use of but/however/although signals internal inconsistency management."
                ),
                weight=0.06,
                source="Mafiascum but_ratio feature",
            ))

        # 8. Very short average sentence length — fragmented, non-committal
        if avg_sentence_length < 8 and sentence_count > 5:
            score_components.append(0.05)
            signals.append(DeceptionSignal(
                category="fragmented_sentences",
                description=(
                    f"Average sentence length is {avg_sentence_length:.1f} words. "
                    "Very short sentences avoid commitment to verifiable claims."
                ),
                weight=0.05,
                source="Embedded Lies sentence-level analysis",
            ))

        # ── DEFENCE-SPECIFIC SIGNALS ───────────────────────────────────────

        for signal_name, signal_def in DEFENCE_DECEPTION_INDICATORS.items():
            detected_examples = []
            for example in signal_def["examples"]:
                if example.lower() in text_lower:
                    detected_examples.append(example)

            if detected_examples:
                contribution = signal_def["weight"] * min(len(detected_examples), 3) / 3
                score_components.append(contribution)
                signals.append(DeceptionSignal(
                    category=signal_name,
                    description=signal_def["description"],
                    evidence=detected_examples,
                    weight=contribution,
                    source=f"Arkmurus defence DD framework | Note: {signal_def['note']}",
                ))

        # ── CONTEXT ADJUSTMENT (UNIDECOR finding) ─────────────────────────
        # Deception patterns shift by context. In formal business communication,
        # some indicators (e.g. passive voice) are more diagnostic.

        context_multiplier = {
            "general":               1.0,
            "business_communication":1.15,  # higher stakes, more structured deception
            "entity_claim":          1.20,  # verification is possible — more diagnostic
            "proposal":              1.10,
            "testimony":             1.25,  # most structured — most diagnostic
        }.get(context_type, 1.0)

        # ── FINAL SCORE ────────────────────────────────────────────────────
        raw = sum(score_components) * context_multiplier
        raw = max(0.0, min(1.0, raw))

        # Determine tier
        if raw <= 0.25:
            tier = DeceptionRiskTier.LOW
        elif raw <= 0.50:
            tier = DeceptionRiskTier.MODERATE
        elif raw <= 0.70:
            tier = DeceptionRiskTier.ELEVATED
        else:
            tier = DeceptionRiskTier.HIGH

        # Confidence — reduced for short texts and when few signals
        confidence = confidence_base
        if len(signals) < 2:
            confidence *= 0.7
        if wc > 200:
            confidence = min(confidence * 1.2, 0.85)

        # Analyst note
        analyst_note = ""
        if is_short:
            analyst_note = (
                f"Text is short ({wc} words) — score has lower reliability. "
                "Seek additional text before acting on this assessment."
            )
        elif tier == DeceptionRiskTier.HIGH:
            analyst_note = (
                "Multiple significant signals detected. Apply Enhanced Due Diligence. "
                "Do not act on claims in this text without independent verification."
            )

        return DeceptionRiskScore(
            raw_score=raw,
            tier=tier,
            signals_detected=sorted(signals, key=lambda s: s.weight, reverse=True),
            linguistic_features={
                "fpp_ratio": round(fpp_ratio, 4),
                "tpp_ratio": round(tpp_ratio, 4),
                "neg_ratio": round(neg_ratio, 4),
                "hed_ratio": round(hed_ratio, 4),
                "but_ratio": round(but_ratio, 4),
                "defensive_count": defensive_count,
                "passive_count": passive_count,
                "vagueness_count": vagueness_count,
                "implicit_claims": implicit_count,
                "unique_ratio": round(unique_ratio, 4),
                "avg_sentence_length": round(avg_sentence_length, 1),
                "context_multiplier": context_multiplier,
            },
            context_type=context_type,
            fpp_ratio=fpp_ratio,
            tpp_ratio=tpp_ratio,
            negation_ratio=neg_ratio,
            hedging_ratio=hed_ratio,
            defensive_count=defensive_count,
            passive_voice_count=passive_count,
            word_count=wc,
            sentence_count=sentence_count,
            analyst_note=analyst_note,
            confidence=confidence,
            requires_human_review=(tier in (DeceptionRiskTier.ELEVATED,
                                            DeceptionRiskTier.HIGH)),
        )

    def analyse_claims(
        self,
        claims: list[str],
        context_type: str = "entity_claim",
    ) -> list[tuple[str, DeceptionRiskScore]]:
        """
        Analyse a list of specific claims individually.

        This implements the Embedded Lies methodology — analysing at the
        sentence level rather than document level to detect deception
        embedded within otherwise truthful text.

        Args:
            claims:       List of individual claim strings
            context_type: Context for analysis

        Returns:
            List of (claim, score) tuples sorted by risk descending
        """
        results = []
        for claim in claims:
            score = self.analyse(claim.strip(), context_type=context_type)
            results.append((claim, score))
        results.sort(key=lambda x: x[1].raw_score, reverse=True)
        return results

    def compare(
        self,
        text_a: str,
        text_b: str,
        context_type: str = "general",
    ) -> dict:
        """
        Compare two texts for relative deception risk.
        Useful for comparing a counterparty's initial claim vs follow-up statement.
        Inconsistency between the two is itself a signal.
        """
        score_a = self.analyse(text_a, context_type)
        score_b = self.analyse(text_b, context_type)

        delta = score_b.raw_score - score_a.raw_score
        escalation = delta > 0.15

        return {
            "text_a_score": score_a.raw_score,
            "text_b_score": score_b.raw_score,
            "delta": round(delta, 3),
            "escalation_detected": escalation,
            "note": (
                "Risk increased significantly in second text — "
                "possible pressure or contradiction response."
                if escalation else
                "Risk levels consistent between texts."
            ),
            "score_a": score_a,
            "score_b": score_b,
        }


# =============================================================================
# ARIA SYSTEM PROMPT INJECTION
# =============================================================================

DECEPTION_DETECTION_SYSTEM_PROMPT = """
ARIA DECEPTION RISK AWARENESS — ACTIVE

When analysing communications, proposals, or claims from counterparties,
ARIA applies validated deception risk indicators derived from:
  - Mafiascum natural deception dataset (700+ ground-truth games)
  - UNIDECOR cross-domain deception corpus (reviews, testimony, conversation)
  - Embedded Lies research (sentence-level detection, 2025)
  - Arkmurus defence-sector DD framework

ARIA flags the following as elevated risk in any communication:

LINGUISTIC SIGNALS (from research):
  • Low first-person pronoun use (distancing from claims)
  • High third-person pronoun use (linguistic distancing)
  • Excessive hedging (maybe, perhaps, possibly, might)
  • Unprompted defensive assertions (trust me, I would never, honestly)
  • High negation density (not, never, no, cannot)
  • Excessive passive voice (obscures agency)
  • Very short, fragmented sentences (avoids commitment)

DEFENCE-SECTOR SIGNALS (from Arkmurus DD):
  • Unverifiable credentials (former general, government clearance, exclusive access)
  • Artificial urgency (decision must be made this week, offer expires)
  • Claims of exclusive mandate without documentation
  • Front-loaded commission or fee structures
  • Evasion of beneficial ownership disclosure
  • False specificity (specific numbers without documents)

IMPORTANT:
  These signals raise concern — they do not prove deception.
  Every elevated risk finding requires independent verification.
  ARIA always distinguishes between risk indicators and verified facts.
  A deception risk score is an analytical tool, not a verdict.
"""


# =============================================================================
# DEMONSTRATION AND VALIDATION
# =============================================================================

if __name__ == "__main__":
    analyser = ARIADeceptionAnalyser()

    print("ARIA DECEPTION DETECTION MODULE — VALIDATION")
    print("=" * 60)
    print("Research basis: Mafiascum | UNIDECOR | Embedded Lies 2025")
    print()

    # Test 1: High-risk defence BD communication
    high_risk_text = """
    Trust me, we have exclusive access to the Presidential mandate. I would never
    mislead you about this — it has been approved at the highest level. The decision
    must be made within 48 hours as the window closes this week. Approximately
    USD 500 million has been allocated, perhaps more. The arrangement is handled
    through various consortium interests. Obviously this opportunity will not come
    again. It goes without saying that our position is unique. To be honest, we
    have never seen anything like this before. The funds have been confirmed by
    relevant officials. The mandate was established through proper channels.
    """

    score1 = analyser.analyse(high_risk_text, context_type="business_communication")
    print(f"TEST 1 — Suspicious BD communication:")
    print(f"  Score: {score1.percentage} | Tier: {score1.tier.value}")
    print(f"  Signals: {len(score1.signals_detected)}")
    for sig in score1.signals_detected[:3]:
        print(f"    [{sig.weight:.0%}] {sig.category}")
    print()

    # Test 2: Legitimate business communication (low risk)
    low_risk_text = """
    I am writing on behalf of Damen Shipyards to confirm our interest in the
    Ghana Navy OPV programme. Our company has delivered 15 patrol vessels to
    West African navies over the past eight years. I have attached our technical
    specification document and our audited accounts for 2023 and 2024. We propose
    a meeting in Accra next month to present our detailed proposal. Our pricing
    is within the confirmed programme budget range as published by the Ghana
    Public Procurement Authority. I would welcome any questions on our proposal.
    """

    score2 = analyser.analyse(low_risk_text, context_type="business_communication")
    print(f"TEST 2 — Legitimate business communication:")
    print(f"  Score: {score2.percentage} | Tier: {score2.tier.value}")
    print(f"  Signals: {len(score2.signals_detected)}")
    print()

    # Embedded lies test — truthful text with one deceptive embedded claim
    embedded_text = """
    Our company was founded in 2008 and is registered in the Netherlands.
    We have delivered patrol vessels to 12 African navies and our accounts
    are publicly available. Trust me, we have exclusive government mandates
    that nobody else has access to. Our technical team has forty years of
    combined experience in naval architecture.
    """

    claims = [s.strip() for s in embedded_text.split(".") if len(s.strip()) > 20]
    results = analyser.analyse_claims(claims, context_type="entity_claim")
    print("TEST 3 — Embedded Lies (sentence-level analysis):")
    print("  Sentences ranked by deception risk:")
    for claim, score in results[:4]:
        print(f"  [{score.tier.value:8}] {score.percentage} — {claim[:70]}...")
    print()
    print("Module ready. Inject DECEPTION_DETECTION_SYSTEM_PROMPT into ARIA.")

# R-F2119 §21a — wire failure handler for deception_detection
try:
    wire_failure(module="deception_detection", detail="module shutdown",
                gap_type="engine_failure", source="deception_detection:shutdown")
except Exception:
    pass
