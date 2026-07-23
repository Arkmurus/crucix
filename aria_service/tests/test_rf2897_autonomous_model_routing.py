"""R-F2897 — the high-volume autonomous loops route to the CHEAP Claude model.

R-F2768 built claude_model_for_intent() but attached it to exactly ONE call site
(researcher._analyse_article). Every other autonomous loop would therefore land
on full-price Sonnet after the Claude flip — and those loops, not DD, are where
ARIA's token volume actually lives.

Each test drives the REAL extraction function (§3c) with a fake provider that
captures the kwargs, and asserts the `model` that reaches llm.complete(). A test
that only checked claude_model_for_intent() would pass even if nothing were
wired — which is exactly the bug this R-number fixes.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.llm import tier_router as tr


CHEAP = tr.claude_model_for_intent("entity_extraction")   # claude-haiku-4-5
STANDARD = tr._model_standard()                           # claude-sonnet-5


def _run(coro):
    return asyncio.run(coro)


class _CapturingLLM:
    """Stands in for the provider chain and records the model it was asked for."""

    name = "anthropic"
    is_configured = True

    def __init__(self, text: str = "{}"):
        self.calls: list[dict] = []
        self._text = text

    async def complete(self, system_prompt, user_message, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {
            "text": self._text, "model": kwargs.get("model") or "",
            "input_tokens": 10, "output_tokens": 10,
        })()

    @property
    def model_used(self) -> str:
        assert self.calls, "llm.complete() was never called — wrong path driven"
        return self.calls[-1].get("model") or ""


# ──────────────────────────────────────────────────────────────────────────
# The policy itself
# ──────────────────────────────────────────────────────────────────────────
class TestPolicy:
    def test_cheap_and_standard_are_distinct(self):
        """If these ever collapse, every assertion below silently passes."""
        assert CHEAP != STANDARD
        assert "haiku" in CHEAP

    def test_unknown_intent_never_downgrades(self):
        """A mislabelled customer-facing call must not silently become cheap."""
        assert tr.claude_model_for_intent("not-a-real-intent") == STANDARD

    def test_every_intent_this_batch_uses_is_known(self):
        for intent in ("entity_extraction", "classification",
                       "research_extraction", "summary_short"):
            assert intent in tr.INTENTS, f"{intent} is not a declared intent"
            assert tr.claude_model_for_intent(intent) == CHEAP


# ──────────────────────────────────────────────────────────────────────────
# The wired call sites — drive the REAL functions
# ──────────────────────────────────────────────────────────────────────────
class TestWiredCallSites:
    def test_neural_memory_entity_extraction_uses_cheap(self):
        """Highest-volume site in the tree: 20-30 per sweep + one per article."""
        from aria_service.intel import neural_memory

        llm = _CapturingLLM('{"entities": []}')
        # >=50 chars: _extract_concepts_llm skips short text rather than pay
        # for an LLM call on a fragment.
        _run(neural_memory._extract_concepts_llm(
            "Rolls-Royce signed a submarine reactor deal with BAE Systems "
            "and the UK Ministry of Defence in Barrow-in-Furness.", llm))
        assert llm.model_used == CHEAP

    def test_corpus_url_classification_uses_cheap(self):
        from aria_service.intel import corpus_manager

        llm = _CapturingLLM(
            '{"proposed_tier":"B","confidence":0.9,"rationale":"x","risk_level":"LOW"}')
        _run(corpus_manager._classify_url_llm("https://example.com", "ctx", llm))
        assert llm.model_used == CHEAP

    def test_correction_fact_extraction_uses_cheap(self):
        from aria_service.intel import correction_learner

        llm = _CapturingLLM('{"facts": []}')
        # >=30 chars after strip, else the extractor short-circuits.
        _run(correction_learner._extract_facts_via_llm(
            "No, the Type 26 contract was signed in 2024, not 2023.", llm))
        assert llm.model_used == CHEAP

    def test_link_investigator_extraction_uses_cheap(self):
        from aria_service.intel import link_investigator

        llm = _CapturingLLM('{"facts": [], "links": [], "summary": ""}')
        # >=100 chars of page text, else extraction is skipped.
        _run(link_investigator._extract_facts_llm(
            "BAE Systems announced a contract award covering Type 26 frigates "
            "for the Royal Navy, with work carried out on the Clyde over the "
            "next decade under an MoD agreement.",
            "query", "https://example.com", llm))
        assert llm.model_used == CHEAP

    def test_mem0_summariser_uses_cheap(self):
        from aria_service.intel import mem0

        llm = _CapturingLLM("a one line summary")
        # mem0 only summarises SUBSTANTIVE replies (>=200 chars, non-refusal).
        reply = (
            "The Type 26 programme covers eight anti-submarine frigates for the "
            "Royal Navy, built by BAE Systems on the Clyde. The first batch was "
            "contracted in 2017 and the second in 2022, with in-service dates "
            "running through the late 2020s and export variants sold to "
            "Australia and Canada under separate agreements."
        )
        assert len(reply) >= 200
        _run(mem0.summarise_and_store("tell me about Type 26", reply, "sess-1", llm))
        assert llm.model_used == CHEAP


# ──────────────────────────────────────────────────────────────────────────
# What must NOT be down-modelled. These are the guard rails: a future pass
# that "optimises" a verdict or a code-writing path breaks these on purpose.
# ──────────────────────────────────────────────────────────────────────────
class TestQualityCriticalPathsUntouched:
    def test_verdict_and_judgement_intents_are_not_cheap(self):
        """The moat is honest verdicts (see the DD/USP work). Judgement-grade
        intents must stay at standard or premium — never Haiku."""
        for intent in ("audit_grade_dd", "compliance_opinion",
                       "constitutional_decision"):
            assert tr.claude_model_for_intent(intent) != CHEAP, intent

    def test_chat_is_not_cheap(self):
        for intent in ("chat", "chat_stream"):
            assert tr.claude_model_for_intent(intent) != CHEAP, intent

    def test_hypothesis_validation_site_left_on_default(self):
        """R-F2897 deliberately did NOT wire researcher.validate_hypothesis:
        it emits a SUPPORTS/CHALLENGES/REFUTED verdict, which is judgement, not
        extraction — and it is low-volume, so there is nothing to win. If a
        later pass wires it, that must be a conscious decision, not a drift."""
        import inspect
        from aria_service.intel import researcher

        src = inspect.getsource(researcher.validate_hypothesis)
        assert "claude_model_for_intent" not in src, (
            "validate_hypothesis was wired to model routing — it returns a "
            "verdict; confirm that is intended before changing this test"
        )
