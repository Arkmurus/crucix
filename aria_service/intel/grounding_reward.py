"""R-F1942 — VERIFIABLE grounding reward for ARIA-LLM reasoning training.

The coder broke its distillation cap with a verifiable reward (tests pass = an
objective, ungameable signal). The reasoning analog is GROUNDING: an answer's
[Source: ...] / [from ...] citations must map to sources actually present in the
retrieved context, and the model must ABSTAIN when the context can't support an
answer — never fabricate facts or sources. That is objectively checkable from
{answer, context} alone, so it can drive DPO/GRPO past the SFT teacher ceiling.

score() returns a float in [0,1] + a breakdown. Pure + deterministic — no LLM,
no network — so it is unit-testable and usable as a GRPO reward or to rank DPO
preference pairs.

Reward design (all objective):
  - citation_precision = grounded_citations / total_citations  (fabricated
    citations — labels NOT in the context — are the cardinal sin, penalised hard)
  - has_grounding: an answering response must carry >=1 grounded citation
  - abstention: when the context is empty/insufficient, an answer that abstains
    scores high; one that fabricates an answer scores ~0
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import re
from dataclasses import dataclass, field

# Citation labels the corpus uses: "[Source: web_search:...]" and "[from <x>]".
_CITE_RE = re.compile(r"\[(?:Source:|from )\s*([^\]]+?)\s*\]", re.IGNORECASE)

# R-F2397 — ARIA's REAL production RAG context format (get_rag_context_with_sources)
# does NOT use "[Source: Sx]"; it uses authoritative "↳ source: <label>" lines and
# "• [n.nn] <type>:..." chunk headers. The old _CITE_RE extracted ~0 sources from a
# production context, so score() flagged every genuine citation as fabricated (the
# bug behind 3 false FAIL verdicts). These recognise the production source labels.
_CTX_SOURCE_RE = re.compile(r"↳\s*source:\s*([^|\n]+)", re.IGNORECASE)      # "↳ source: <label> | <date>"
_CTX_HEADER_RE = re.compile(                                                # "• [1.04] web_search:...:" typed header prefix
    r"[•\-\*]\s*\[\s*\d+(?:\.\d+)?\s*\]\s*"
    r"([A-Za-z][\w-]*(?::[\w-]+)*:[^\n:]{0,80})",
)
_ABSTAIN_MARKERS = (
    "cannot confirm", "does not contain", "not supported", "cannot determine",
    "no information", "context does not", "not enough information", "cannot answer",
    "insufficient", "does not provide", "unable to", "cannot be determined",
    "no relevant", "i cannot", "not available in the", "context lacks",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def extract_citations(text: str) -> list[str]:
    return [_norm(m) for m in _CITE_RE.findall(text or "")]


def extract_context_sources(context: str) -> set[str]:
    """R-F2397 — the set of source labels ACTUALLY present in a retrieved context.

    Recognises BOTH formats so grounding is measured against real evidence:
      - synthetic  "[Source: Sx]" / "[from x]"     (extract_citations — back-compat)
      - production "↳ source: <label>"             (the authoritative per-chunk label)
      - production "• [n.nn] <type>:..." headers   (typed chunk-header prefix)
    Format recognition only — no scoring weight/threshold is involved here.
    """
    ctx = context or ""
    srcs = set(extract_citations(ctx))
    for m in _CTX_SOURCE_RE.findall(ctx):
        n = _norm(m)
        if n:
            srcs.add(n)
    for m in _CTX_HEADER_RE.findall(ctx):
        n = _norm(m)
        if n:
            srcs.add(n)
    return {s for s in srcs if s}


def _cite_grounded(cite: str, ctx_sources: set[str]) -> bool:
    """Is a normalized answer citation grounded in the context's source set?

    Exact match, OR hierarchical containment against a REAL extracted label only
    (production labels are hierarchical — a model legitimately cites the leaf
    "investigation:X" of "research:investigation:X", or appends "| <date>" to a
    label). Bounded to the extracted label set + a length floor so a fabricated
    citation cannot be credited by trivial/free-text overlap (no over-crediting).
    """
    c = _norm(cite)
    if not c:
        return False
    if c in ctx_sources:
        return True
    if len(c) < 8:
        return False
    for lb in ctx_sources:
        if len(lb) >= 8 and (c in lb or lb in c):
            return True
    return False


def _is_abstention(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in _ABSTAIN_MARKERS)


def _keyword_recall(answer: str, keywords) -> float:
    """Fraction of expected_keywords present in the answer — the 'did it actually
    say the substantive fact' signal (R-F2033). 0.0 when no keywords given."""
    ks = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    if not ks:
        return 0.0
    a = (answer or "").lower()
    return sum(1 for k in ks if k in a) / len(ks)


def _grounded_keywords(keywords, context) -> list:
    """R-F2805 (cycle 7) — the subset of expected_keywords that ACTUALLY appear in
    the context, i.e. are extractable from the grounded evidence. Measured on the
    500-Q eval, only ~25% of gold keywords are in-context; the other ~75% are expert
    domain-vocab (SIPRI, OIEL, FCDO…) NOT in the retrieved context. Rewarding raw
    keyword_recall therefore paid the model to state UNGROUNDED domain terms — which
    for a grounded model is fabrication (why cycles 5/6 raised fabrication and never
    moved recall). Restricting recall to grounded keywords rewards honest extraction
    of the available substance and gives ZERO credit for ungrounded vocab."""
    ctx = (context or "").lower()
    return [k for k in (keywords or []) if str(k).strip() and str(k).strip().lower() in ctx]


@dataclass
class RewardBreakdown:
    score: float
    total_citations: int = 0
    grounded_citations: int = 0
    fabricated_citations: int = 0
    citation_precision: float = 0.0
    abstained: bool = False
    context_has_sources: bool = False
    answerable: bool | None = None       # R-F2033 — was this question answerable from ctx?
    keyword_recall: float = 0.0          # R-F2033 — substance signal (expected_keywords hit)
    coverage_bonus: float = 0.0          # cycle-4 — grounded-citation coverage bonus applied
    recall_bonus: float = 0.0            # R-F2788 cycle-6 — additive-capped recall reward earned
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "total_citations": self.total_citations,
            "grounded_citations": self.grounded_citations,
            "fabricated_citations": self.fabricated_citations,
            "citation_precision": round(self.citation_precision, 4),
            "abstained": self.abstained,
            "context_has_sources": self.context_has_sources,
            "answerable": self.answerable,
            "keyword_recall": round(self.keyword_recall, 4),
            "coverage_bonus": round(self.coverage_bonus, 4),
            "recall_bonus": round(self.recall_bonus, 4),
            "reasons": self.reasons,
        }


def score(answer: str, context: str, *, fabrication_weight: float = 0.6,
          answerable: bool | None = None, expected_keywords=None,
          precision_weight: float = 0.5, coverage_weight: float = 0.0,
          coverage_target: float = 2.0,
          answerable_nocite_penalty: float = 0.1,
          recall_bonus_weight: float = 0.0,
          recall_bonus_cap: float = 0.25,
          grounded_recall_only: bool = False) -> RewardBreakdown:
    """Objective grounding reward in [0,1]. Higher = better grounded / honestly
    abstained; near 0 = fabricated sources, OR abstained when the answer WAS available.

    R-F2033 — ANSWERABLE-AWARE. The original reward let an answer abstain "despite
    context" for a flat 0.5, so GRPO learned to abstain ~90% of the time (it maxed
    reward by never answering — held-out grounding stayed at the SFT 0.21). The fix:
    when ``answerable`` is known, abstaining on an ANSWERABLE question is penalised
    (you had the facts and refused), while abstaining on an UNANSWERABLE one is
    rewarded (honest). ``expected_keywords`` adds a substance signal so a grounded
    answer that actually states the expected facts scores above one that merely
    carries a valid citation. Backward-compatible: answerable=None + no keywords ->
    the original behaviour exactly.

    GRPO cycle 4 — COVERAGE lever (closes the confirmed v3 gap). v1→v3 the model's
    precision-WHEN-it-cites rose (0.85→0.93, better than DeepSeek) but the fraction
    of answerable rows it cited on FELL (zero-citation rows 12.7%→22.4%, avg
    citations 1.59→1.30): the reward taught cite-perfectly-or-not-at-all, so overall
    precision (which scores a 0-citation answerable row as 0.0) and recall both
    slipped below DeepSeek despite record-low fabrication (0.056). Two knobs, both
    DEFAULT-OFF (score() with defaults is byte-identical to R-F2586, so the
    objective eval stays comparable):
      - ``coverage_weight`` (>0) adds a bonus for GROUNDED citation breadth, scaled
        by ``coverage_target`` grounded citations AND by citation_precision — so
        citing MORE real facts pays, but a fabricated citation (which lowers
        precision and raises fab_rate) shrinks the bonus AND the precision_score:
        double-gated, it can NEVER reward spamming/inventing citations.
      - ``answerable_nocite_penalty`` is the score for answering an ANSWERABLE
        question with ZERO citations (the coverage failure). Default 0.1 = current;
        a cycle may lower it, but it must stay >= the fabrication floor (0.05) so
        honest silence is never scored worse than inventing a source.

    GRPO cycle 6 — RECALL BONUS lever (R-F2788). Cycle 5 tried to lift keyword
    recall by LOWERING ``precision_weight`` 0.5->0.4, which just re-weighted the
    REALLOCATION blend (``pw*precision + (1-pw)*recall``) to hand recall a bigger
    slice of the SAME budget — it TRADED precision away (precision fell below the
    0.750 floor AND below same-run DeepSeek) and recall did not even rise. Cycle 6
    keeps ``precision_weight`` at its cycle-4 winner (0.5) and adds an ADDITIVE,
    CAPPED recall lever that does NOT subtract precision the way the blend does:
      - ``recall_bonus_weight`` (>0) REPLACES the reallocation blend with a
        HEADROOM formula on answering, keyworded, grounded rows:
            score = precision_score * (1 - rbw + rbw*keyword_recall)
                  = precision_score * (1 - rbw*(1 - keyword_recall))
        where ``rbw`` = recall_bonus_weight clamped to [0, ``recall_bonus_cap``].
        This reserves only a SMALL ``rbw`` band below precision_score for a recall
        gradient, so a terse grounded answer keeps ``(1-rbw)`` of its precision
        (e.g. 0.85 at rbw=0.15) instead of the blend's ``pw``=0.5 — precision is
        preserved, not traded. Crucially it solves the SATURATION problem: when
        precision_score is 1.0 (fully grounded), a naive ``min(1.0, P+bonus)`` has
        ZERO gradient, but the headroom band means substantive-grounded (recall 1.0
        -> 1.0) still beats terse-grounded (recall 0 -> 1-rbw). The whole recall
        term is MULTIPLIED by precision_score (which already folds in
        citation_precision and the fabrication penalty) AND gated on
        ``grounded_citations > 0`` — double-gated exactly like the coverage lever,
        so a fabricated / keyword-stuffed uncited answer can never farm the bonus.
      - ``recall_bonus_cap`` hard-ceilings ``rbw`` so recall can never dominate the
        answering-row score (default 0.25 = recall may move at most 25% of the band).
      DEFAULT-OFF: with ``recall_bonus_weight=0.0`` the headroom branch is skipped
      and the reallocation blend runs exactly as before, so score() is byte-
      identical to R-F2586 (the frozen objective eval stays comparable).
    """
    ctx_sources = extract_context_sources(context)  # R-F2397 — production + synthetic
    ans_cites = extract_citations(answer)
    grounded = [c for c in ans_cites if _cite_grounded(c, ctx_sources)]
    fabricated = [c for c in ans_cites if not _cite_grounded(c, ctx_sources)]
    # R-F2805 (cycle 7) — GROUNDED recall: when enabled, score recall only over
    # keywords that are in the context (extractable), so the reward never pays for
    # stating ungrounded domain vocab (= fabrication). DEFAULT OFF: with the flag
    # off, keyword_recall is the original raw recall and score() is byte-identical.
    _kws_for_recall = (
        _grounded_keywords(expected_keywords, context)
        if grounded_recall_only else expected_keywords
    )
    b = RewardBreakdown(
        score=0.0,
        total_citations=len(ans_cites),
        grounded_citations=len(grounded),
        fabricated_citations=len(fabricated),
        context_has_sources=bool(ctx_sources),
        abstained=_is_abstention(answer),
        answerable=answerable,
        keyword_recall=_keyword_recall(answer, _kws_for_recall),
    )

    # Case 1: context has no usable sources -> the only correct move is abstain.
    if not ctx_sources:
        if b.abstained and not ans_cites:
            b.score = 1.0; b.reasons.append("correct_abstention_no_context")
        elif b.fabricated_citations:
            b.score = 0.0; b.reasons.append("fabricated_sources_with_no_context")
        else:
            b.score = 0.2; b.reasons.append("answered_without_grounding_no_context")
        return b

    # Case 2: the answer ABSTAINS though the context has sources. R-F2033: this is the
    # gaming hole — make it answerable-aware so GRPO can't farm reward by abstaining.
    if b.abstained and b.total_citations == 0:
        if answerable is True:
            # the answer WAS in the context and it refused — the failure we're fixing.
            b.score = 0.1; b.reasons.append("abstained_on_answerable")
        elif answerable is False:
            # context has sources but not for THIS question — honest abstention.
            b.score = 1.0; b.reasons.append("correct_abstention_unanswerable")
        else:
            # answerability unknown — keep the original conservative partial credit.
            b.score = 0.5; b.reasons.append("abstained_despite_context")
        return b

    # Case 3: answering response — reward precision, punish fabrication, require
    # at least one grounded citation; R-F2033 adds a substance (keyword) bonus.
    if b.total_citations == 0:
        # Answering an ANSWERABLE question with zero citations is the COVERAGE
        # failure cycle-4 targets (v3: 22% of answerable rows, each scored 0.0
        # precision). Configurable penalty; floored at the fabrication floor (0.05)
        # so honest silence is never scored below inventing a source.
        if answerable is True:
            b.score = max(0.05, answerable_nocite_penalty)
            b.reasons.append("answered_answerable_without_citation")
        else:
            b.score = 0.1; b.reasons.append("answered_without_any_citation")
        return b
    b.citation_precision = b.grounded_citations / b.total_citations
    fab_rate = b.fabricated_citations / b.total_citations
    # base = precision; fabrication penalised by its own weight on top.
    precision_score = max(0.0, b.citation_precision - fabrication_weight * fab_rate)
    b.score = precision_score
    # R-F2033: a genuinely grounded answer that also STATES the expected facts beats
    # one that carries only a bare citation. Bonus applies ONLY when there is real
    # grounding (>=1 grounded citation) so it can never reward a fabricated answer.
    if expected_keywords and b.grounded_citations > 0:
        # R-F2586 — precision_weight controls the precision/recall split of the
        # composite. Default 0.5 = the v2 (R-F2558) 50/50 behaviour (backward-
        # compatible; the objective eval stays comparable). Cycle 3 raised it to 0.75,
        # which de-weighted recall and drove the model toward cite-perfectly-or-not-
        # at-all (see coverage regression above) — cycle 4 restores 0.5 and adds the
        # coverage_weight lever instead of pushing precision_weight further.
        pw = min(1.0, max(0.0, precision_weight))
        # R-F2788 (cycle 6) — additive-capped recall lever. When ON it REPLACES the
        # reallocation blend (below) rather than adding on top: adding on top would
        # keep the blend's harsh precision trade (a terse grounded answer stuck at
        # pw*precision=0.5*P) that cycle 5 proved backfires. The headroom formula
        # keeps precision as the dominant MULTIPLICATIVE factor and only reserves a
        # small `rbw` band for recall, so precision is preserved, not traded — and it
        # still has a recall gradient at precision_score=1.0 (the saturation case).
        # Recall term is scaled by precision_score (double-gated with grounded>0) so a
        # fabricated / keyword-stuffed answer can never farm it.
        rbw = min(max(0.0, recall_bonus_cap), max(0.0, recall_bonus_weight))
        if rbw > 0.0:
            floor = precision_score * (1.0 - rbw)          # terse/zero-recall keeps (1-rbw) of precision
            recall_term = precision_score * rbw * b.keyword_recall
            b.score = floor + recall_term                   # = precision_score*(1 - rbw*(1-recall))
            b.recall_bonus = recall_term
            b.reasons.append(f"recall_bonus={recall_term:.3f}")
        else:
            b.score = pw * precision_score + (1.0 - pw) * b.keyword_recall
        if b.keyword_recall > 0:
            b.reasons.append(f"keyword_recall={b.keyword_recall:.2f}")
    # Cycle-4 COVERAGE bonus — reward citing MORE grounded facts on answerable rows
    # so the model stops leaving ~22% of answerable rows uncited. Implemented as a
    # REALLOCATION blend (not an additive bonus): a coverage_weight slice of the
    # reward is reassigned to the coverage term, so it still differentiates 1 vs 2
    # grounded citations even when the base is already saturated at 1.0 (a perfect
    # single citation on a keyword-less row — 37% of the dataset — otherwise had ZERO
    # coverage gradient). DOUBLE-gated so it can never reward fabrication: (1) only
    # grounded citations count toward coverage, (2) the coverage term is scaled by
    # citation_precision, so any fabricated citation both raises fab_rate (lowering
    # the base precision_score above) AND shrinks this term. Skipped on known-
    # unanswerable rows (answerable is False) — those should abstain.
    if coverage_weight > 0.0 and b.grounded_citations > 0 and answerable is not False:
        cw = min(1.0, max(0.0, coverage_weight))
        tgt = max(1.0, float(coverage_target))
        coverage = min(1.0, b.grounded_citations / tgt)
        base_before = b.score
        b.score = (1.0 - cw) * base_before + cw * coverage * b.citation_precision
        b.coverage_bonus = b.score - base_before
        b.reasons.append(f"coverage={coverage:.2f}")
    if b.grounded_citations == 0:
        b.score = min(b.score, 0.05); b.reasons.append("no_grounded_citation")
    # R-F2033 (symmetry): if the gold was to ABSTAIN (context insufficient for this
    # question) but the model answered anyway, that's over-claiming — cap it low so
    # honest abstention (1.0) clearly beats answering. Prevents the reward from
    # pushing the model to over-answer once abstention-gaming is penalised.
    if answerable is False:
        b.score = min(b.score, 0.25); b.reasons.append("answered_when_should_abstain")
    if b.fabricated_citations:
        b.reasons.append(f"{b.fabricated_citations}_fabricated_citation(s)")
    if b.citation_precision == 1.0:
        b.reasons.append("fully_grounded")
    return b


def reward(answer: str, context: str, *, answerable: bool | None = None,
           expected_keywords=None, precision_weight: float = 0.5,
           coverage_weight: float = 0.0, coverage_target: float = 2.0,
           answerable_nocite_penalty: float = 0.1,
           recall_bonus_weight: float = 0.0,
           recall_bonus_cap: float = 0.25,
           grounded_recall_only: bool = False) -> float:
    """Scalar reward (for GRPO / DPO ranking). ``precision_weight`` (R-F2586) tunes
    the precision/recall split; ``coverage_weight``/``answerable_nocite_penalty``
    (cycle-4) tune answerable-row citation coverage; ``recall_bonus_weight``/
    ``recall_bonus_cap`` (R-F2788 cycle-6) add a precision-preserving recall lever.
    All defaults = backward-compatible."""
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="grounding_reward",
                     summary="grounding_reward module active",
                     source_id="grounding_reward:init")
    except Exception:
        try:
            wire_failure(module="grounding_reward", detail="module init failed",
                        gap_type="engine_failure", source="grounding_reward:init")
        except Exception:
            pass

    return score(answer, context, answerable=answerable,
                 expected_keywords=expected_keywords,
                 precision_weight=precision_weight,
                 coverage_weight=coverage_weight,
                 coverage_target=coverage_target,
                 answerable_nocite_penalty=answerable_nocite_penalty,
                 recall_bonus_weight=recall_bonus_weight,
                 recall_bonus_cap=recall_bonus_cap,
                 grounded_recall_only=grounded_recall_only).score
