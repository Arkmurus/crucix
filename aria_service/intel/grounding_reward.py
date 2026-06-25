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

import re
from dataclasses import dataclass, field

# Citation labels the corpus uses: "[Source: web_search:...]" and "[from <x>]".
_CITE_RE = re.compile(r"\[(?:Source:|from )\s*([^\]]+?)\s*\]", re.IGNORECASE)
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


def _is_abstention(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in _ABSTAIN_MARKERS)


@dataclass
class RewardBreakdown:
    score: float
    total_citations: int = 0
    grounded_citations: int = 0
    fabricated_citations: int = 0
    citation_precision: float = 0.0
    abstained: bool = False
    context_has_sources: bool = False
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
            "reasons": self.reasons,
        }


def score(answer: str, context: str, *, fabrication_weight: float = 0.6) -> RewardBreakdown:
    """Objective grounding reward in [0,1]. Higher = better grounded / honestly
    abstained; near 0 = fabricated sources or answered when it should abstain."""
    ctx_sources = set(extract_citations(context))
    ans_cites = extract_citations(answer)
    grounded = [c for c in ans_cites if c in ctx_sources]
    fabricated = [c for c in ans_cites if c not in ctx_sources]
    b = RewardBreakdown(
        score=0.0,
        total_citations=len(ans_cites),
        grounded_citations=len(grounded),
        fabricated_citations=len(fabricated),
        context_has_sources=bool(ctx_sources),
        abstained=_is_abstention(answer),
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

    # Case 2: the answer abstains despite context — partial credit (over-cautious,
    # but honest); reward higher if it still cited what little it used.
    if b.abstained and b.total_citations == 0:
        b.score = 0.5; b.reasons.append("abstained_despite_context")
        return b

    # Case 3: answering response — reward precision, punish fabrication, require
    # at least one grounded citation.
    if b.total_citations == 0:
        b.score = 0.1; b.reasons.append("answered_without_any_citation")
        return b
    b.citation_precision = b.grounded_citations / b.total_citations
    fab_rate = b.fabricated_citations / b.total_citations
    # base = precision; fabrication penalised by its own weight on top.
    b.score = max(0.0, b.citation_precision - fabrication_weight * fab_rate)
    if b.grounded_citations == 0:
        b.score = min(b.score, 0.05); b.reasons.append("no_grounded_citation")
    if b.fabricated_citations:
        b.reasons.append(f"{b.fabricated_citations}_fabricated_citation(s)")
    if b.citation_precision == 1.0:
        b.reasons.append("fully_grounded")
    return b


def reward(answer: str, context: str) -> float:
    """Scalar reward (for GRPO / DPO ranking)."""
    return score(answer, context).score
