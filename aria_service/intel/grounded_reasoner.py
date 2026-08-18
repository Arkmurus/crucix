"""R-F1044 — Grounded Reasoning Engine (Track R driver).

The missing orchestrator that makes ARIA's reasoning grounded by construction.
Every claim is verified against evidence BEFORE it reaches the user.

Architecture
════════════
    User message
        ↓
    1. UNDERSTAND — parse intent, extract factual premises
    2. DECOMPOSE — break into sub-questions (each independently verifiable)
    3. GATHER EVIDENCE — per sub-question, search in order:
        a. Reasoning library (past Q→A with confirmed outcomes)
        b. Knowledge store (verified facts with provenance)
        c. Neural memory (associative recall)
        d. RAG store (semantic search over ingested documents)
        e. Premise verifier (deterministic fact-checking against canonical data)
        f. Live tools (web search, researcher, sanctions lookup) — only when
           memory is insufficient
    4. REASON — for each sub-question, reason over ONLY verified evidence.
       Every claim maps to an evidence item or is marked unverifiable.
    5. VERIFY INLINE — call honesty_judge BEFORE answering, not after.
       Every [CONFIRMED] claim must have a source. Downgrade or abstain if not.
    6. GROUND-OR-ABSTAIN — if a sub-question has no evidence, explicitly say
       "cannot verify" rather than guessing.
    7. CITE — attach evidence trace to every claim.
    8. ABSORB — write the result to brain_hook + reasoning_library.

Result shape
════════════
    ReasonResult {
        answer: str,                    # The final grounded answer
        claims: [{
            text: str,                  # The claim text
            evidence: [{
                source: str,            # URL, document ID, or knowledge key
                kind: str,              # "rag", "knowledge", "neural", "tool", "canonical"
                confidence: float,      # 0..1
            }],
            grounded: bool,             # True if supported by evidence
            confidence: float,          # Overall confidence in this claim
        }],
        steps: [{                       # Reasoning trace
            phase: str,                 # "decompose", "gather", "reason", "verify"
            detail: str,
            result: str,
        }],
        abstained: bool,                # True if any sub-question was unanswerable
    }

Gate: ARIA_GROUNDED_REASONER=1 to enable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.grounded_reasoner")

# Gate — R-F1047: enabled by default. The grounded reasoner is the primary
# response path for all chat queries. Set ARIA_GROUNDED_REASONER=0 to disable.
_ENABLED = os.getenv("ARIA_GROUNDED_REASONER", "1") == "1"

# R-F1057 — wall-time budget for the entire reason() pipeline. If the pipeline
# exceeds this, it falls through to the cloud LLM (fast fallthrough).
_EVIDENCE_GATHER_TIMEOUT = 15.0   # max seconds for all evidence gathering
_LLM_SYNTHESIS_TIMEOUT = 10.0     # max seconds for LLM claim synthesis
_TOTAL_PIPELINE_TIMEOUT = 25.0    # max seconds for the entire reason() call


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class EvidenceItem:
    source: str          # URL, document ID, or knowledge key
    kind: str            # "rag", "knowledge", "neural", "tool", "canonical"
    confidence: float    # 0..1
    content: str = ""    # The actual evidence text (truncated)


@dataclass
class Claim:
    text: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    grounded: bool = False
    confidence: float = 0.0


@dataclass
class ReasoningStep:
    phase: str       # "decompose", "gather", "reason", "verify"
    detail: str
    result: str = ""


@dataclass
class ReasonResult:
    answer: str
    claims: list[Claim] = field(default_factory=list)
    steps: list[ReasoningStep] = field(default_factory=list)
    abstained: bool = False
    duration_ms: float = 0.0


# ── Main reasoner ───────────────────────────────────────────────────────────

class GroundedReasoner:
    """Orchestrator that produces grounded, evidence-traced answers.

    Usage:
        reasoner = GroundedReasoner()
        result = await reasoner.reason("What is the export status of K9 Thunder?")
        if result.abstained:
            print("Cannot verify — insufficient evidence.")
        for claim in result.claims:
            print(f"{claim.text} (grounded={claim.grounded}, conf={claim.confidence})")
    """

    def __init__(self) -> None:
        self._llm = None   # lazy-imported LLM provider
        self._rag = None
        self._knowledge = None
        self._neural = None
        self._premise_verifier = None
        self._reasoning_library = None
        self._honesty_judge = None

    @fail_wire(module="grounded_reasoner", gap_type="engine_failure")
    async def reason(
        self,
        message: str,
        context: Optional[dict] = None,
    ) -> ReasonResult:
        """Run the full grounded reasoning pipeline."""
        start = time.monotonic()
        steps: list[ReasoningStep] = []
        claims: list[Claim] = []

        if not _ENABLED:
            return ReasonResult(
                answer="Grounded reasoner is disabled (set ARIA_GROUNDED_REASONER=1).",
                steps=[ReasoningStep(phase="gate", detail="disabled")],
                abstained=True,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        try:
            # R-F1057 — wall-time budget for the entire pipeline. If we exceed
            # this, we fall through to the cloud LLM (fast fallthrough) rather
            # than making the user wait.
            async def _pipeline() -> tuple[str, bool, list[Claim]]:
                """Run the full pipeline with its own time budget."""
                # ── Phase 1: Understand ──────────────────────────────────
                steps.append(ReasoningStep(phase="understand", detail="Parsing intent and premises"))
                premises = await self._extract_premises(message)
                steps[-1].result = f"Extracted {len(premises)} premises"

                # ── Phase 2: Decompose ───────────────────────────────────
                steps.append(ReasoningStep(phase="decompose", detail="Breaking into sub-questions"))
                sub_questions = await self._decompose(message, premises)
                steps[-1].result = f"Decomposed into {len(sub_questions)} sub-questions"

                # ── Phase 3: Gather evidence (CONCURRENT per sub-question) ──
                steps.append(ReasoningStep(phase="gather", detail="Gathering evidence per sub-question"))
                evidence_map: dict[str, list[EvidenceItem]] = {}

                # R-F1057 — gather evidence for ALL sub-questions CONCURRENTLY
                # instead of serially. Each sub-question's internal sources are
                # also gathered concurrently inside _gather_evidence_concurrent.
                async def _gather_one(sq: str) -> tuple[str, list[EvidenceItem]]:
                    ev = await self._gather_evidence_concurrent(sq)
                    return sq, ev

                _gather_tasks = [_gather_one(sq) for sq in sub_questions]
                _gather_results = await asyncio.wait_for(
                    asyncio.gather(*_gather_tasks, return_exceptions=True),
                    timeout=_EVIDENCE_GATHER_TIMEOUT,
                )
                for _gr in _gather_results:
                    if isinstance(_gr, Exception):
                        logger.debug("[grounded_reasoner] gather task failed: %s", _gr)
                        continue
                    sq, ev = _gr
                    evidence_map[sq] = ev

                steps[-1].result = f"Gathered evidence for {len(evidence_map)} sub-questions"

                # ── Phase 4: Reason ──────────────────────────────────────
                steps.append(ReasoningStep(phase="reason", detail="Reasoning over verified evidence"))
                claims = await self._reason_over_evidence(message, sub_questions, evidence_map)
                steps[-1].result = f"Produced {len(claims)} claims"

                # ── Phase 5: Verify inline ───────────────────────────────
                steps.append(ReasoningStep(phase="verify", detail="Verifying claims inline"))
                claims = await self._verify_claims_inline(claims)
                grounded_count = sum(1 for c in claims if c.grounded)
                steps[-1].result = f"{grounded_count}/{len(claims)} claims grounded"

                # ── Phase 6: Ground-or-abstain ───────────────────────────
                steps.append(ReasoningStep(phase="ground_or_abstain", detail="Building final answer"))
                answer, abstained = self._build_answer(claims)
                steps[-1].result = "Abstained" if abstained else "Answered"
                return answer, abstained, claims

            answer, abstained, claims = await asyncio.wait_for(
                _pipeline(),
                timeout=_TOTAL_PIPELINE_TIMEOUT,
            )

            # ── Phase 7: Absorb ──────────────────────────────────────────
            await self._absorb_result(message, answer, claims, steps)

        except asyncio.TimeoutError:
            logger.warning("[grounded_reasoner] pipeline timed out after %ss — fast fallthrough",
                           _TOTAL_PIPELINE_TIMEOUT)
            steps.append(ReasoningStep(phase="timeout", detail=f"Exceeded {_TOTAL_PIPELINE_TIMEOUT}s budget"))
            answer = ""
            abstained = True
        except Exception as exc:
            logger.exception("[grounded_reasoner] pipeline failed")
            steps.append(ReasoningStep(phase="error", detail=str(exc)))
            answer = "I encountered an error while reasoning. Please try again."
            abstained = True

        duration = (time.monotonic() - start) * 1000
        return ReasonResult(
            answer=answer,
            claims=claims,
            steps=steps,
            abstained=abstained,
            duration_ms=duration,
        )

    # ── Phase implementations ───────────────────────────────────────────────

    async def _extract_premises(self, message: str) -> list[str]:
        """Extract factual premises from the user message.

        Uses the premise verifier for deterministic extraction, then the LLM
        for any additional premises the verifier missed.
        """
        premises: list[str] = []

        # 1. Deterministic premise verifier
        pv = await self._get_premise_verifier()
        try:
            # R-F4138 (C-170) — verify_premises runs an O(corpus) scan
            # (2.28s mean / 570k facts) since a search_fact_records call was
            # added under it. Off the loop, like deep_researcher's four sites.
            report = await asyncio.to_thread(pv.verify_premises, message)
            if report and hasattr(report, "premises"):
                premises.extend(report.premises)
        except Exception as exc:
            logger.debug("[grounded_reasoner] premise_verifier failed: %s", exc)

        # 2. LLM extraction for complex premises
        llm = await self._get_llm()
        if llm:
            try:
                prompt = (
                    "Extract factual premises from this message. A premise is a "
                    "statement that asserts something as true. Return a JSON list of "
                    "strings. If no factual premises, return [].\n\n"
                    f"Message: {message}"
                )
                from ..llm.structured import call_structured
                result = await call_structured(
                    "Extract factual premises. Return ONLY a JSON list of strings.",
                    prompt,
                    schema={"type": "list", "items": "str"},
                    llm=llm, caller="grounded_reasoner._extract_premises",
                    max_tokens=500,
                )
                # R-F3110 — `ok` is the ONLY branch that yields data; a rejected or
                # unavailable reply leaves `premises` as the deterministic set, and
                # the gateway has already reported it. It is never read as "none found".
                if result.ok:
                    for p in result.data:
                        if isinstance(p, str) and p not in premises:
                            premises.append(p)
            except Exception as exc:
                logger.debug("[grounded_reasoner] LLM premise extraction failed: %s", exc)

        return premises

    async def _decompose(self, message: str, premises: list[str]) -> list[str]:
        """Decompose the message into independently verifiable sub-questions."""
        sub_questions: list[str] = []

        # Each premise becomes a verification question
        for p in premises:
            sub_questions.append(f"Is this true: {p}")

        # Also generate sub-questions from the main query
        llm = await self._get_llm()
        if llm:
            try:
                prompt = (
                    "Break this question into independently verifiable sub-questions. "
                    "Each sub-question must be answerable from a single source. "
                    "Return a JSON list of strings.\n\n"
                    f"Question: {message}\n"
                    f"Premises: {json.dumps(premises)}"
                )
                from ..llm.structured import call_structured
                result = await call_structured(
                    "Decompose questions. Return ONLY a JSON list of strings.",
                    prompt,
                    schema={"type": "list", "items": "str"},
                    llm=llm, caller="grounded_reasoner._decompose",
                    max_tokens=500,
                )
                if result.ok:
                    for sq in result.data:
                        if isinstance(sq, str) and sq not in sub_questions:
                            sub_questions.append(sq)
            except Exception as exc:
                logger.debug("[grounded_reasoner] decomposition failed: %s", exc)

        return sub_questions or [f"Answer this: {message}"]

    async def _gather_evidence(self, sub_question: str) -> list[EvidenceItem]:
        """Gather evidence for a sub-question from all available sources.

        Searches in order of cost (cheapest first):
          1. Reasoning library (past Q→A)
          2. Knowledge store (verified facts)
          3. Neural memory (associative)
          4. RAG store (semantic search)
          5. Premise verifier (canonical data)
          6. Live tools (last resort)
        """
        evidence: list[EvidenceItem] = []

        # 1. Reasoning library
        rl = await self._get_reasoning_library()
        if rl:
            try:
                match = await rl.search(sub_question, threshold=0.85)
                if match and match.get("response"):
                    evidence.append(EvidenceItem(
                        source=f"reasoning_library:{match.get('id', 'unknown')}",
                        kind="reasoning_library",
                        confidence=match.get("confidence_score", 0.8),
                        content=match["response"][:500],
                    ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] reasoning_library failed: %s", exc)

        # 2. Knowledge store
        kn = await self._get_knowledge()
        if kn:
            try:
                facts = await kn.query(sub_question, limit=3)
                for fact in (facts or []):
                    if isinstance(fact, dict):
                        evidence.append(EvidenceItem(
                            source=fact.get("source", "knowledge_store"),
                            kind="knowledge",
                            confidence=fact.get("confidence", 0.7),
                            content=str(fact.get("fact", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] knowledge query failed: %s", exc)

        # 3. Neural memory
        nm = await self._get_neural_memory()
        if nm:
            try:
                neurons = await nm.recall(sub_question, limit=3)
                for n in (neurons or []):
                    if isinstance(n, dict):
                        evidence.append(EvidenceItem(
                            source=f"neural:{n.get('id', 'unknown')}",
                            kind="neural",
                            confidence=n.get("confidence", 0.6),
                            content=str(n.get("summary", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] neural recall failed: %s", exc)

        # 4. RAG store
        rag = await self._get_rag()
        if rag:
            try:
                docs = await rag.search(sub_question, limit=3)
                for doc in (docs or []):
                    if isinstance(doc, dict):
                        evidence.append(EvidenceItem(
                            source=doc.get("source", doc.get("url", "rag_store")),
                            kind="rag",
                            confidence=doc.get("score", 0.5),
                            content=str(doc.get("content", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] RAG search failed: %s", exc)

        # 5. Premise verifier (canonical data)
        pv = await self._get_premise_verifier()
        if pv:
            try:
                # R-F4138 (C-170) — O(corpus) scan; must not block the loop.
                report = await asyncio.to_thread(pv.verify_premises, sub_question)
                if report and hasattr(report, "verdicts"):
                    for v in report.verdicts:
                        evidence.append(EvidenceItem(
                            source=f"canonical:{v.get('source', 'premise_verifier')}",
                            kind="canonical",
                            confidence={"CONFIRMED": 0.95, "REFUTED": 0.95}.get(
                                v.get("verdict", ""), 0.5),
                            content=str(v.get("detail", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] premise_verifier failed: %s", exc)

        return evidence

    async def _gather_evidence_concurrent(self, sub_question: str) -> list[EvidenceItem]:
        """Gather evidence for a sub-question from ALL sources CONCURRENTLY.

        R-F1057 — runs all source queries in parallel instead of serially,
        with a per-source timeout so one slow source doesn't block the rest.
        """
        evidence: list[EvidenceItem] = []
        _tasks: list[asyncio.Task] = []

        async def _from_reasoning_library() -> None:
            rl = await self._get_reasoning_library()
            if not rl:
                return
            try:
                match = await rl.search(sub_question, threshold=0.85)
                if match and match.get("response"):
                    evidence.append(EvidenceItem(
                        source=f"reasoning_library:{match.get('id', 'unknown')}",
                        kind="reasoning_library",
                        confidence=match.get("confidence_score", 0.8),
                        content=match["response"][:500],
                    ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] reasoning_library failed: %s", exc)

        async def _from_knowledge() -> None:
            kn = await self._get_knowledge()
            if not kn:
                return
            try:
                facts = await kn.query(sub_question, limit=3)
                for fact in (facts or []):
                    if isinstance(fact, dict):
                        evidence.append(EvidenceItem(
                            source=fact.get("source", "knowledge_store"),
                            kind="knowledge",
                            confidence=fact.get("confidence", 0.7),
                            content=str(fact.get("fact", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] knowledge query failed: %s", exc)

        async def _from_neural() -> None:
            nm = await self._get_neural_memory()
            if not nm:
                return
            try:
                neurons = await nm.recall(sub_question, limit=3)
                for n in (neurons or []):
                    if isinstance(n, dict):
                        evidence.append(EvidenceItem(
                            source=f"neural:{n.get('id', 'unknown')}",
                            kind="neural",
                            confidence=n.get("confidence", 0.6),
                            content=str(n.get("summary", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] neural recall failed: %s", exc)

        async def _from_rag() -> None:
            rag = await self._get_rag()
            if not rag:
                return
            try:
                docs = await rag.search(sub_question, limit=3)
                for doc in (docs or []):
                    if isinstance(doc, dict):
                        evidence.append(EvidenceItem(
                            source=doc.get("source", doc.get("url", "rag_store")),
                            kind="rag",
                            confidence=doc.get("score", 0.5),
                            content=str(doc.get("content", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] RAG search failed: %s", exc)

        async def _from_premise_verifier() -> None:
            pv = await self._get_premise_verifier()
            if not pv:
                return
            try:
                # R-F4138 (C-170) — O(corpus) scan; must not block the loop.
                # This one runs inside a gather, so blocking here stalls every
                # sibling evidence source too.
                report = await asyncio.to_thread(pv.verify_premises, sub_question)
                if report and hasattr(report, "verdicts"):
                    for v in report.verdicts:
                        evidence.append(EvidenceItem(
                            source=f"canonical:{v.get('source', 'premise_verifier')}",
                            kind="canonical",
                            confidence={"CONFIRMED": 0.95, "REFUTED": 0.95}.get(
                                v.get("verdict", ""), 0.5),
                            content=str(v.get("detail", ""))[:500],
                        ))
            except Exception as exc:
                logger.debug("[grounded_reasoner] premise_verifier failed: %s", exc)

        # Run all source queries concurrently with individual timeouts
        _source_tasks = [
            _from_reasoning_library(),
            _from_knowledge(),
            _from_neural(),
            _from_rag(),
            _from_premise_verifier(),
        ]
        await asyncio.gather(*_source_tasks, return_exceptions=True)

        return evidence

    async def _reason_over_evidence(
        self,
        message: str,
        sub_questions: list[str],
        evidence_map: dict[str, list[EvidenceItem]],
    ) -> list[Claim]:
        """Reason over verified evidence to produce claims."""
        claims: list[Claim] = []

        # For each sub-question with evidence, produce a claim
        for sq in sub_questions:
            ev = evidence_map.get(sq, [])
            if ev:
                # Use the best evidence
                best = max(ev, key=lambda e: e.confidence)
                claims.append(Claim(
                    text=sq,
                    evidence=ev,
                    grounded=True,
                    confidence=best.confidence,
                ))
            else:
                # No evidence — mark as ungrounded
                claims.append(Claim(
                    text=sq,
                    grounded=False,
                    confidence=0.0,
                ))

        # Use LLM to synthesise claims from evidence
        llm = await self._get_llm()
        if llm and any(c.grounded for c in claims):
            try:
                evidence_text = "\n".join(
                    f"- [{e.kind}] {e.content[:200]}"
                    for c in claims for e in c.evidence
                )
                prompt = (
                    "Based ONLY on the evidence below, answer the question. "
                    "If the evidence does not support an answer, say so. "
                    "Return a JSON object with:\n"
                    "  - claims: list of {text, confidence}\n\n"
                    "Do NOT start your response with 'UNDERSTOOD AS:' or any "
                    "other preamble — return ONLY the JSON object.\n\n"
                    f"Question: {message}\n\n"
                    f"Evidence:\n{evidence_text}"
                )
                from ..llm.structured import OUTCOME_INVALID_OUTPUT, call_structured
                result = await call_structured(
                    "Answer ONLY from the supplied evidence. Return ONLY a JSON "
                    "object with a 'claims' list of {text, confidence}.",
                    prompt,
                    schema={"type": "dict", "required": ["claims"]},
                    llm=llm, caller="grounded_reasoner._reason_over_evidence",
                    max_tokens=1000, timeout=_LLM_SYNTHESIS_TIMEOUT,
                )
                # R-F3110 — `grounded` is EARNED FROM EVIDENCE, never asserted at
                # construction. These claims are model prose and carry no
                # EvidenceItem, so _verify_claims_inline (phase 5, :601) forces them
                # to grounded=False/confidence=0.0 anyway — constructing them as
                # True was a statement that only survived because something
                # downstream corrected it. That is the shape §21/the blueprint call
                # "true by construction vs true by luck": if the honesty judge were
                # ever unavailable, `_verify_claims_inline` returns claims UNTOUCHED
                # (:596) and the lie would ship. Observably identical today,
                # fail-safe if phase 5 ever goes quiet.
                if result.ok:
                    for lc in (result.data.get("claims") or []):
                        if isinstance(lc, dict):
                            claims.append(Claim(
                                text=lc.get("text", ""),
                                grounded=False,
                                confidence=float(lc.get("confidence", 0.5) or 0.0),
                            ))
                elif result.outcome == OUTCOME_INVALID_OUTPUT and result.raw_text:
                    # The model answered in prose rather than JSON. Keep it as an
                    # explicitly UNGROUNDED claim so the evidence trail shows what
                    # was said; it cannot become the answer without phase 6.
                    # R-F1057 — strip meta-preamble from LLM output.
                    _clean = self._strip_meta_preamble(result.raw_text[:500])
                    if _clean:
                        claims.append(Claim(
                            text=_clean,
                            grounded=False,
                            confidence=0.0,
                        ))
            except asyncio.TimeoutError:
                logger.debug("[grounded_reasoner] LLM synthesis timed out after %ss",
                             _LLM_SYNTHESIS_TIMEOUT)
            except Exception as exc:
                logger.debug("[grounded_reasoner] LLM reasoning failed: %s", exc)

        return claims

    async def _verify_claims_inline(self, claims: list[Claim]) -> list[Claim]:
        """Verify each claim inline — downgrade ungrounded claims."""
        hj = await self._get_honesty_judge()
        if not hj:
            return claims

        verified_claims: list[Claim] = []
        for claim in claims:
            if not claim.evidence:
                # No evidence — mark as ungrounded
                claim.grounded = False
                claim.confidence = 0.0
                verified_claims.append(claim)
                continue

            try:
                evidence_text = "\n".join(
                    f"[{e.kind}] {e.content[:300]}" for e in claim.evidence
                )
                verdict = await hj.judge(
                    claim=claim.text,
                    evidence=evidence_text,
                )
                if verdict and isinstance(verdict, dict):
                    is_supported = verdict.get("supported", False)
                    claim.grounded = is_supported
                    claim.confidence = verdict.get("confidence", claim.confidence)
            except Exception as exc:
                logger.debug("[grounded_reasoner] honesty_judge failed: %s", exc)

            verified_claims.append(claim)

        return verified_claims

    @staticmethod
    def _strip_meta_preamble(text: str) -> str:
        """Remove meta-preamble markers from LLM-generated text.

        R-F1057 — the LLM sometimes starts responses with "UNDERSTOOD AS:"
        (from the comprehension prefix pattern in the main chat path) or
        other meta markers. These are useful in the reasoning trace but
        must not leak into the user-facing answer.

        Strips:
          - "*UNDERSTOOD AS:* ..." (markdown italic variant)
          - "UNDERSTOOD AS: ..." (plain text variant)
          - "[COMPREHENSION PASS ...]" blocks
        """
        import re as _re
        # Markdown italic: *UNDERSTOOD AS:* or _UNDERSTOOD AS:_
        text = _re.sub(
            r'\*?UNDERSTOOD AS:\*?\s*',
            '',
            text,
            count=1,
            flags=_re.IGNORECASE,
        )
        # Plain text: UNDERSTOOD AS:
        text = _re.sub(
            r'^UNDERSTOOD AS:\s*',
            '',
            text,
            count=1,
            flags=_re.IGNORECASE,
        )
        # [COMPREHENSION PASS ...] blocks
        text = _re.sub(
            r'\[COMPREHENSION PASS[^\]]*\]\s*',
            '',
            text,
            flags=_re.IGNORECASE,
        )
        return text.strip()

    def _build_answer(self, claims: list[Claim]) -> tuple[str, bool]:
        """Build the final answer from verified claims.

        Returns (answer_text, abstained).
        """
        grounded = [c for c in claims if c.grounded]
        ungrounded = [c for c in claims if not c.grounded]

        if not grounded:
            return (
                "I cannot verify this with the evidence available to me. "
                "The information I have is insufficient to provide a confident answer.",
                True,
            )

        parts: list[str] = []
        for c in grounded:
            # R-F1057 — strip meta-preamble from claim text before presenting
            clean_text = self._strip_meta_preamble(c.text)
            sources = ", ".join(e.source[:60] for e in c.evidence[:3])
            parts.append(f"{clean_text} (confidence: {c.confidence:.0%}, sources: {sources})")

        if ungrounded:
            parts.append(
                "\nNote: The following aspects could not be verified: "
                + "; ".join(self._strip_meta_preamble(c.text[:100]) for c in ungrounded)
            )

        return "\n".join(parts), bool(ungrounded)

    async def _absorb_result(
        self,
        message: str,
        answer: str,
        claims: list[Claim],
        steps: list[ReasoningStep],
    ) -> None:
        """Absorb the result into brain_hook and reasoning_library."""
        try:
            from .engine_wiring import wire_success, wire_failure

            wire_success(
                module="grounded_reasoner",
                summary=f"Grounded reasoning: {len(claims)} claims, "
                        f"{sum(1 for c in claims if c.grounded)} grounded",
                detail=json.dumps({
                    "message": message[:200],
                    "claim_count": len(claims),
                    "grounded_count": sum(1 for c in claims if c.grounded),
                    "abstained": not any(c.grounded for c in claims),
                }),
                source_id="grounded_reasoner",
            )
        except Exception as exc:
            logger.debug("[grounded_reasoner] absorb failed: %s", exc)

        # Store in reasoning library for future use
        try:
            rl = await self._get_reasoning_library()
            if rl:
                await rl.record_response(
                    question=message,
                    response=answer,
                    confidence_tag="CONFIRMED" if not any(not c.grounded for c in claims) else "ASSESSED",
                    confidence_score=sum(c.confidence for c in claims) / max(len(claims), 1),
                )
        except Exception as exc:
            logger.debug("[grounded_reasoner] library record failed: %s", exc)

    # ── Lazy loader helpers ─────────────────────────────────────────────────

    async def _get_llm(self) -> Any:
        """Resolve the live application provider chain.

        R-F3110 — this returned None on EVERY call. It constructed
        `llm_pipeline.LLMPipeline`, a class that has never existed in this tree
        (llm_pipeline.py defines `LLMTrainingPipeline`), so the import raised
        ImportError straight into the `except` below and logged at DEBUG. Measured
        before the fix: `_get_llm()` -> None, `_extract_premises()` -> [] on a
        message with obvious premises, `_decompose()` -> a single stub question.
        Every LLM branch of this reasoner — a LIVE stage, called by
        reasoning_router.py:383 — was unreachable, and nothing said so.

        company_investigator.py hit the identical import and R-F2535 fixed it
        THERE, recording "never existed" in a comment. Two other sites kept the
        bug. Resolution now goes through the one gateway (R-F3109) so there is no
        third private way to obtain a provider.
        """
        if self._llm is None:
            from ..llm.structured import resolve_provider
            self._llm = resolve_provider()
            if self._llm is None:
                logger.debug("[grounded_reasoner] no LLM provider resolvable")
        return self._llm

    async def _get_rag(self) -> Any:
        if self._rag is None:
            try:
                from . import rag_store as rs
                self._rag = rs
            except Exception as exc:
                logger.debug("[grounded_reasoner] RAG not available: %s", exc)
        return self._rag

    async def _get_knowledge(self) -> Any:
        if self._knowledge is None:
            try:
                from . import knowledge as kn
                self._knowledge = kn
            except Exception as exc:
                logger.debug("[grounded_reasoner] knowledge not available: %s", exc)
        return self._knowledge

    async def _get_neural_memory(self) -> Any:
        if self._neural is None:
            try:
                from . import neural_memory as nm
                self._neural = nm
            except Exception as exc:
                logger.debug("[grounded_reasoner] neural_memory not available: %s", exc)
        return self._neural

    async def _get_premise_verifier(self) -> Any:
        if self._premise_verifier is None:
            try:
                from . import premise_verifier as pv
                self._premise_verifier = pv
            except Exception as exc:
                logger.debug("[grounded_reasoner] premise_verifier not available: %s", exc)
        return self._premise_verifier

    async def _get_reasoning_library(self) -> Any:
        if self._reasoning_library is None:
            try:
                from . import reasoning_library as rl
                self._reasoning_library = rl
            except Exception as exc:
                logger.debug("[grounded_reasoner] reasoning_library not available: %s", exc)
        return self._reasoning_library

    async def _get_honesty_judge(self) -> Any:
        if self._honesty_judge is None:
            try:
                from . import honesty_judge as hj
                self._honesty_judge = hj
            except Exception as exc:
                logger.debug("[grounded_reasoner] honesty_judge not available: %s", exc)
        return self._honesty_judge


# ── Convenience function ────────────────────────────────────────────────────

_reasoner_instance: GroundedReasoner | None = None


@fail_wire(module="grounded_reasoner", gap_type="engine_failure")
async def reason(message: str, context: Optional[dict] = None) -> ReasonResult:
    """Convenience function — uses a module-level singleton."""
    global _reasoner_instance
    if _reasoner_instance is None:
        _reasoner_instance = GroundedReasoner()
    return await _reasoner_instance.reason(message, context=context)


# ── Wire to brain ───────────────────────────────────────────────────────────

from .engine_wiring import wire_failure, wire_success  # noqa: E402

wire_success(
    module="grounded_reasoner",
    summary="Grounded Reasoner Engine active",
    detail="Gate: ARIA_GROUNDED_REASONER=1. Pipeline: understand → decompose → "
           "gather → reason → verify → ground-or-abstain → absorb",
    source_id="grounded_reasoner:R-F1044",
)

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
