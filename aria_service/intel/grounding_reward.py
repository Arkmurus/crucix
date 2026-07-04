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
            "reasons": self.reasons,
        }


def score(answer: str, context: str, *, fabrication_weight: float = 0.6,
          answerable: bool | None = None, expected_keywords=None) -> RewardBreakdown:
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
    """
    ctx_sources = extract_context_sources(context)  # R-F2397 — production + synthetic
    ans_cites = extract_citations(answer)
    grounded = [c for c in ans_cites if _cite_grounded(c, ctx_sources)]
    fabricated = [c for c in ans_cites if not _cite_grounded(c, ctx_sources)]
    b = RewardBreakdown(
        score=0.0,
        total_citations=len(ans_cites),
        grounded_citations=len(grounded),
        fabricated_citations=len(fabricated),
        context_has_sources=bool(ctx_sources),
        abstained=_is_abstention(answer),
        answerable=answerable,
        keyword_recall=_keyword_recall(answer, expected_keywords),
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
        b.score = 0.5 * precision_score + 0.5 * b.keyword_recall
        if b.keyword_recall > 0:
            b.reasons.append(f"keyword_recall={b.keyword_recall:.2f}")
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
           expected_keywords=None) -> float:
    """Scalar reward (for GRPO / DPO ranking)."""
    return score(answer, context, answerable=answerable,
                 expected_keywords=expected_keywords).score

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
