"""R-F4253 / C-220 — the judge scored against a TRUNCATED source and never said so.

`_build_judge_user_prompt` cuts the source at `JUDGE_SOURCE_LIMIT` (8000 chars) —
*"aggressive truncation ... we want it cheap. 8000 chars is plenty for typical
research outputs."* Correct for the case it was written for, and silent about the
case it was not.

**A claim whose supporting passage sits past the cut is judged `supported: false`,
and that verdict is indistinguishable from a claim that was genuinely
unsupported.** The resulting `honesty_score` feeds **25% of Phase A gate #1** — the
phase named *Honesty foundation* — so a truncation artefact lands directly on the
gate as evidence of dishonesty.

## It bites today, not hypothetically

`routes/aria.py::_maybe_frame_grounding` deliberately SKIPS `dd_orchestrate`
output ("so the two marker doctrines don't collide"), so a chat turn that ran a
due-diligence tool hands the judge an enormous context which is then cut to its
first 8000 characters. Everything past that is invisible to the judge but fully
present in the answer it is grading.

This is also why **wiring DD into the honesty judge would be a mistake right now**:
`dd_orchestrator` reaches `honesty_judge` zero times, and gate #1's honesty axis is
starved partly because of it — but connecting them before this bound is understood
would manufacture false-negative honesty scores at scale.

## Recorded, deliberately NOT corrected

The fields added here are **additive**: no verdict changes, no score changes.
Excluding truncated judgments from `avg_honesty_score` is the arguable next step —
an unseen passage was UNMEASURED, and §1 is emphatic that *"could not measure"* is
not *"measured and failed"* — but that alters a Phase A gate input, and **nothing
in the tree has ever recorded how often truncation actually happens.** Measure
first, then decide on evidence. C-220 carries that reasoning forward.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aria_service.intel import honesty_judge as hj


def _run(coro):
    return asyncio.run(coro)


class _LLM:
    is_configured = True

    def __init__(self, verdicts):
        self._v = verdicts
        self.seen_prompt = ""

    async def complete(self, system_prompt, user_message, **kw):
        self.seen_prompt = user_message
        import json
        return types.SimpleNamespace(
            text=json.dumps({"verdicts": self._v}), model="fake")


_RESP = ("The registration number is 11668244 [CONFIRMED]. "
         "The company has four officers [CONFIRMED].")


def _judge(llm, source):
    return _run(hj.judge_response(llm, _RESP, source))


class TestTruncationIsRecorded:

    def test_a_truncated_source_is_declared(self):
        big = "x" * (hj.JUDGE_SOURCE_LIMIT + 5000)
        llm = _LLM([{"claim_index": 1, "supported": True},
                    {"claim_index": 2, "supported": False}])
        r = _judge(llm, big)

        assert r["status"] == "ok"
        assert r["source_truncated"] is True
        assert r["source_chars"] == len(big)
        assert r["source_chars_used"] == hj.JUDGE_SOURCE_LIMIT
        assert 0 < r["source_coverage"] < 1

    def test_a_whole_source_is_not_flagged(self):
        small = "y" * 500
        llm = _LLM([{"claim_index": 1, "supported": True},
                    {"claim_index": 2, "supported": True}])
        r = _judge(llm, small)

        assert r["source_truncated"] is False
        assert r["source_coverage"] == 1.0
        assert r["source_chars_used"] == r["source_chars"] == 500

    def test_the_flag_agrees_with_what_the_prompt_actually_cut(self):
        """One constant, or the record could claim coverage the prompt denied.

        Two definitions of "8000" is the divergence class §17 records for the
        pricing table — the record would say full coverage while the prompt cut
        the source.
        """
        big = "z" * (hj.JUDGE_SOURCE_LIMIT + 1234)
        llm = _LLM([{"claim_index": 1, "supported": True},
                    {"claim_index": 2, "supported": True}])
        r = _judge(llm, big)

        body = llm.seen_prompt.split("─────────")[1]
        assert body.count("z") == r["source_chars_used"], (
            "the recorded `source_chars_used` must equal the characters the "
            "prompt actually carried")


class TestTheMisleadingCombinationIsSignalled:
    """Narrow on purpose — see the flood shape this repo has hit twice."""

    def _sink(self, monkeypatch):
        got = {"failure": []}
        import aria_service.intel.engine_wiring as ew
        monkeypatch.setattr(ew, "wire_failure",
                            lambda **kw: got["failure"].append(kw), raising=True)
        monkeypatch.setattr(ew, "wire_success", lambda **kw: None, raising=True)
        return got

    def _truncated(self):
        return "q" * (hj.JUDGE_SOURCE_LIMIT + 100)

    def test_truncated_plus_unsupported_is_reported(self, monkeypatch):
        got = self._sink(monkeypatch)
        llm = _LLM([{"claim_index": 1, "supported": True},
                    {"claim_index": 2, "supported": False}])
        _judge(llm, self._truncated())

        hits = [f for f in got["failure"]
                if f.get("source") == "honesty_judge:truncated_source"]
        assert hits, (
            "a score depressed while the judge could not see the whole source "
            "must be visible as such — it feeds 25% of Phase A gate #1")
        assert "gate #1" in hits[0]["detail"]

    def test_truncated_but_all_supported_is_silent(self, monkeypatch):
        """Every claim was supported by the part it DID see — nothing to say."""
        got = self._sink(monkeypatch)
        llm = _LLM([{"claim_index": 1, "supported": True},
                    {"claim_index": 2, "supported": True}])
        _judge(llm, self._truncated())

        assert not [f for f in got["failure"]
                    if f.get("source") == "honesty_judge:truncated_source"]

    def test_untruncated_low_score_is_not_blamed_on_truncation(self, monkeypatch):
        """A REAL honesty finding must not be excused as an artefact."""
        got = self._sink(monkeypatch)
        llm = _LLM([{"claim_index": 1, "supported": False},
                    {"claim_index": 2, "supported": False}])
        _judge(llm, "short but complete source")

        assert not [f for f in got["failure"]
                    if f.get("source") == "honesty_judge:truncated_source"]


class TestNothingBehaviouralChanged:
    """Additive only — this measures, it does not correct."""

    def test_the_score_is_untouched_by_truncation(self):
        llm_a = _LLM([{"claim_index": 1, "supported": True},
                      {"claim_index": 2, "supported": False}])
        llm_b = _LLM([{"claim_index": 1, "supported": True},
                      {"claim_index": 2, "supported": False}])
        trunc = _judge(llm_a, "w" * (hj.JUDGE_SOURCE_LIMIT + 900))
        whole = _judge(llm_b, "w" * 100)

        assert trunc["honesty_score"] == whole["honesty_score"] == 0.5, (
            "R-F4253 records truncation; it must not silently re-score. "
            "Excluding truncated judgments from the average is a separate, "
            "evidence-led decision (C-220)")

    def test_the_fields_survive_persistence(self, monkeypatch):
        """record_judgment spreads **judgment, so the flags must land."""
        # Capture EVERY store write, not just the first. The first cut used
        # `setdefault("rec", ...)` and so depended on record_judgment's internal
        # call ORDER (record before index) — it passed alone and failed inside
        # the suite. An order-dependent guard is the flake shape §16 records a
        # known-flaky set for; assert on the SET of writes instead.
        writes: list = []

        async def _set_json(key, val, **kw):
            writes.append(val)
            return True

        async def _get_json(key, **kw):
            return []
        monkeypatch.setattr(hj.rs, "set_json", _set_json, raising=False)
        monkeypatch.setattr(hj.rs, "get_json", _get_json, raising=False)

        llm = _LLM([{"claim_index": 1, "supported": True},
                    {"claim_index": 2, "supported": False}])
        j = _judge(llm, "v" * (hj.JUDGE_SOURCE_LIMIT + 10))
        _run(hj.record_judgment(j, trace_id="t", session_id="s"))

        recs = [w for w in writes if isinstance(w, dict) and "honesty_score" in w]
        assert recs, f"the judgment record was not persisted (writes={len(writes)})"
        assert any(r.get("source_truncated") is True for r in recs), (
            "a stored judgment must carry whether the judge saw the whole "
            "source — otherwise the history cannot be re-read for this")
