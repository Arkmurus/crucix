"""R-F2912/R-F2913/R-F2914 — close the three findings from the live cost review.

Live evidence 2026-07-23 (GET /api/aria/cost/monthly, read from inside the box):
    total $71.66 / 1894 calls, 74% of it in the "uncategorized" bucket
    self_improve = top ATTRIBUTABLE Claude cost, top_calls showing identical
    token counts (8806 x3, 8312 x2) — the same prompt billed again and again
and in the logs:
    [generative_redteam] Failed to stage defense: stage_improvement() got an
    unexpected keyword argument 'motivation'
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import cost_tracker as ct
from aria_service.intel import self_improve as si

# R-F3757/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────
# R-F2912 — the same diagnosis must not be re-bought every cycle
# ──────────────────────────────────────────────────────────────────────────
class TestDiagnosisDedupe:
    def test_same_file_same_errors_is_one_signature(self):
        e = [{"message": "KeyError: x"}, {"message": "TypeError: y"}]
        assert si._diagnosis_signature("a.py", e) == si._diagnosis_signature("a.py", e)

    def test_error_order_does_not_change_the_signature(self):
        """Ledger ordering is incidental; it must not defeat the dedupe."""
        a = [{"message": "KeyError: x"}, {"message": "TypeError: y"}]
        b = [{"message": "TypeError: y"}, {"message": "KeyError: x"}]
        assert si._diagnosis_signature("a.py", a) == si._diagnosis_signature("a.py", b)

    def test_a_new_error_re_opens_diagnosis(self):
        """THE property that keeps this a cost fix and not a capability loss:
        if the situation genuinely changed, we diagnose again."""
        a = [{"message": "KeyError: x"}]
        b = [{"message": "KeyError: x"}, {"message": "NEW: z"}]
        assert si._diagnosis_signature("a.py", a) != si._diagnosis_signature("a.py", b)

    def test_different_files_never_collide(self):
        e = [{"message": "KeyError: x"}]
        assert si._diagnosis_signature("a.py", e) != si._diagnosis_signature("b.py", e)

    def test_marker_round_trip_suppresses_a_repeat(self, monkeypatch):
        store: dict[str, str] = {}

        async def _get(key):
            return store.get(key)

        async def _set(key, value, ex=None, **kw):
            store[key] = value

        monkeypatch.setattr(si.rs, "get", _get)
        monkeypatch.setattr(si.rs, "set", _set)

        sig = si._diagnosis_signature("a.py", [{"message": "boom"}])

        async def _drive():
            first = await si._recently_diagnosed(sig)
            await si._mark_diagnosed(sig)
            second = await si._recently_diagnosed(sig)
            return first, second

        first, second = _run(_drive())
        assert first is False, "should not be suppressed before it has ever run"
        assert second is True, "the identical diagnosis was bought twice"

    def test_fails_OPEN_when_the_store_is_down(self, monkeypatch):
        """A cost optimisation must never be why a real bug goes undiagnosed."""
        async def _boom(*a, **k):
            raise RuntimeError("store down")

        monkeypatch.setattr(si.rs, "get", _boom)
        sig = si._diagnosis_signature("a.py", [{"message": "boom"}])
        assert _run(si._recently_diagnosed(sig)) is False


# ──────────────────────────────────────────────────────────────────────────
# R-F2913 — the red-team defense path actually records something
# ──────────────────────────────────────────────────────────────────────────
class TestStageDefense:
    def test_it_reaches_the_operator_queue(self, monkeypatch):
        """Before the fix this raised TypeError on EVERY call (4 nonexistent
        kwargs, 2 missing required args) and was swallowed — so a bypassed
        attack variant left no record anywhere."""
        from aria_service.intel import generative_redteam as grt

        calls: list[dict] = []

        async def _rec(**kw):
            calls.append(kw)
            return {}

        from aria_service.intel import pending_actions as _pa
        monkeypatch.setattr(_pa, "record", _rec)

        ok = _run(grt.stage_defense(
            {"variant_id": "abcdef123456", "category": "prompt_injection",
             "source_pattern": "roleplay_bypass"},
            "Amendment: reject roleplay framing that requests policy override.",
        ))
        assert ok is True
        assert calls, "the defense amendment reached nothing"
        assert calls[0]["severity"] == "HIGH"
        assert calls[0]["source"] == "generative_redteam"
        assert calls[0]["metadata"]["variant_id"] == "abcdef123456"

    def test_it_does_not_call_stage_improvement(self, monkeypatch):
        """It must NOT synthesise a constitution file edit — that is the path
        R-F851 guards. An amendment with no code change is a human decision."""
        from aria_service.intel import generative_redteam as grt

        async def _rec(**kw):
            return {}

        async def _must_not_run(*a, **k):
            raise AssertionError("stage_improvement must not be used here")

        from aria_service.intel import pending_actions as _pa
        monkeypatch.setattr(_pa, "record", _rec)
        monkeypatch.setattr(si, "stage_improvement", _must_not_run)

        assert _run(grt.stage_defense(
            {"variant_id": "x", "category": "c", "source_pattern": "p"}, "amend",
        )) is True

    def test_a_broken_queue_returns_False_not_raise(self, monkeypatch):
        from aria_service.intel import generative_redteam as grt

        async def _boom(**kw):
            raise RuntimeError("queue down")

        from aria_service.intel import pending_actions as _pa
        monkeypatch.setattr(_pa, "record", _boom)
        assert _run(grt.stage_defense({"variant_id": "x"}, "a")) is False


# ──────────────────────────────────────────────────────────────────────────
# R-F2914 — the highest-volume LLM sites are attributed
# ──────────────────────────────────────────────────────────────────────────
class _CapturingLLM:
    name = "anthropic"
    is_configured = True

    def __init__(self):
        self.feature_seen: str | None = None

    async def complete(self, system_prompt, user_message, **kw):
        # Read the contextvar exactly where MeteredProvider would.
        self.feature_seen = ct.get_current_feature()
        return type("R", (), {"text": '{"entities": []}', "model": "m",
                              "input_tokens": 1, "output_tokens": 1})()


class TestFeatureAttribution:
    def test_neural_memory_extraction_is_attributed(self):
        """The single highest-volume LLM site in the tree had NO feature scope,
        so its spend fell into the 74% uncategorized bucket."""
        from aria_service.intel import neural_memory

        llm = _CapturingLLM()
        _run(neural_memory._extract_concepts_llm(
            "Rolls-Royce signed a submarine reactor deal with BAE Systems and "
            "the UK Ministry of Defence in Barrow-in-Furness.", llm))
        assert llm.feature_seen == "neural_memory", llm.feature_seen

    def test_attribution_is_restored_afterwards(self):
        """feature() must not leak — a leaked label would mis-bill later calls."""
        from aria_service.intel import neural_memory

        before = ct.get_current_feature()
        llm = _CapturingLLM()
        _run(neural_memory._extract_concepts_llm(
            "Rolls-Royce signed a submarine reactor deal with BAE Systems and "
            "the UK Ministry of Defence in Barrow-in-Furness.", llm))
        assert ct.get_current_feature() == before

    def test_researcher_extraction_declares_its_feature(self):
        """researcher._analyse_article is the article-extraction bulk."""
        import inspect
        from aria_service.intel import researcher

        src = function_source(researcher, "_analyse_article")
        assert 'feature("research_extraction")' in src, (
            "the article-extraction call lost its cost attribution"
        )


# ──────────────────────────────────────────────────────────────────────────
# R-F2916 — cached tokens are billable and must be counted
# ──────────────────────────────────────────────────────────────────────────
class TestCachedTokensAreCounted:
    """Anthropic's usage.input_tokens is the UNCACHED REMAINDER only. R-F2760
    caches ARIA's large stable prefix, so the better the cache worked, the more
    spend vanished from the meter — and from both caps. Live 2026-07-23: the
    ledger showed $8.56 of Anthropic spend against roughly double that on the
    provider side."""

    def test_plain_usage_is_unchanged(self):
        from aria_service.llm.anthropic import _billable_input_tokens as b
        assert b({"input_tokens": 1000}) == 1000

    def test_cache_reads_are_counted_at_one_tenth(self):
        from aria_service.llm.anthropic import _billable_input_tokens as b
        # old code counted 200 and ignored 8000 cached tokens entirely
        assert b({"input_tokens": 200, "cache_read_input_tokens": 8000}) == 1000

    def test_cache_writes_are_counted_at_1_25x(self):
        from aria_service.llm.anthropic import _billable_input_tokens as b
        assert b({"input_tokens": 200, "cache_creation_input_tokens": 8000}) == 10200

    def test_all_three_components_combine(self):
        from aria_service.llm.anthropic import _billable_input_tokens as b
        assert b({
            "input_tokens": 100,
            "cache_creation_input_tokens": 1000,   # *1.25 = 1250
            "cache_read_input_tokens": 2000,       # *0.10 =  200
        }) == 1550

    def test_malformed_usage_never_raises(self):
        """A completed, already-billed call must not be lost to a parse error."""
        from aria_service.llm.anthropic import _billable_input_tokens as b
        assert b({"input_tokens": "x"}) == 0
        assert b({}) == 0
        assert b({"cache_read_input_tokens": None}) == 0

    def test_negative_counts_cannot_reduce_the_bill(self):
        from aria_service.llm.anthropic import _billable_input_tokens as b
        assert b({"input_tokens": -5, "cache_read_input_tokens": -100}) == 0
