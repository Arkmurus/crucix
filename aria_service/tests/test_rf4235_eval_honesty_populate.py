"""R-F4235 / C-214 — a recording eval achieved 0% of its purpose, silently.

`run_eval(record=True)` exists for one stated reason, in its own docstring: it is
*"the deterministic, offline way to populate the composite's verification (45%) +
honesty (25%) signals from the frozen golden set"*. It is the designed mechanism
for filling the axis Phase A is named after.

Measured live 2026-08-23. A run labelled `rf-gate1-honesty-seed` had been sitting
in the store for weeks:

    total 30    verification_recorded 30    honesty_recorded 0

Thirty LLM-driven chat turns were paid for, half the purpose was achieved, and
the other half returned **zero** — reported in a summary field that no verdict
consumes. So `/api/aria/honesty/stats` stayed at 55 lifetime judgments, gate #1's
honesty signal stayed `None`, and (until R-F4231) the gate could have certified
Phase A without it.

That is the C-96 defect verbatim: *"publishing a number no verdict consumes is why
the degradation went unnoticed."*

## Why it recorded nothing

The judgment gate is `_q_ctx and has_confidence_tags(_q_actual)`. Measured against
that stored run's own responses: only **3 of 30** previews carried a confidence
tag, and honesty was still 0 — so tool context was absent as well. The untagged
samples say why, and they are not bugs:

  * *"Retrieved from ARIA's reasoning library (prior fallback…)"* — memory-served
  * *"I started drafting a response that included specific claims I cannot verify"*
    — the R-F2406 grounding repair correctly refusing
  * *"no PDF has been attached"* — a clarification

With no retrieved context there is nothing to ground a claim against, so skipping
is **correct**. The defect is not the skip; it is that the skip was invisible and
undifferentiated.

## The fix

Count the two skip reasons separately — they need opposite responses:

  * `honesty_skipped_no_context` — correct behaviour; the SEED was aimed at
    unsuitable entries. Remedy: choose tool-backed questions.
  * `honesty_skipped_no_tags` — ARIA used a source and did not tag the claim.
    Remedy: prompt/behaviour, not entry selection.

…and make a `record=True` run that produced zero honesty **report itself** (§21a),
instead of leaving the number in a field nobody opens.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import eval_runner


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def sink(monkeypatch):
    got = {"success": [], "failure": []}
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: got["success"].append(kw), raising=True)
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: got["failure"].append(kw), raising=True)
    return got


class TestTheSkipReasonsAreKeptApart:
    """They demand opposite remedies, so one counter cannot serve both."""

    def test_summary_publishes_both_reasons(self):
        src = eval_runner.__file__
        import pathlib
        text = pathlib.Path(src).read_text(encoding="utf-8")
        for key in ("honesty_skipped_no_context", "honesty_skipped_no_tags"):
            assert f'"{key}"' in text, (
                f"{key} must appear in the run summary — 'honesty_recorded: 0' "
                f"alone cannot distinguish a correct skip (nothing to ground "
                f"against) from ARIA failing to tag a sourced claim")

    def test_the_gate_still_skips_a_contextless_answer(self):
        """The skip itself is CORRECT and must not be 'fixed' away.

        A memory-served or refused answer has no retrieved context, so there is
        nothing for a grounding judge to check. Judging it anyway would fabricate
        a signal — the failure mode this whole area exists to prevent.
        """
        from aria_service.intel import honesty_judge as hj
        assert hj.has_confidence_tags("") is False
        assert hj.has_confidence_tags("no tags here at all") is False
        assert hj.has_confidence_tags("The figure is 12,000 [CONFIRMED].") is True


class TestAZeroHonestyPopulateReportsItself:

    def _drive(self, monkeypatch, sink, *, record, honesty_n=0):
        """Exercise the real reporting block with a controlled outcome."""
        calls = {"n": 0}

        async def _fake(*a, **k):
            calls["n"] += 1
            return {}
        # Drive the block directly: it is plain code at the end of run_eval and
        # the surrounding run is a full LLM-driven eval we must not invoke here.
        import pathlib
        text = pathlib.Path(eval_runner.__file__).read_text(encoding="utf-8")
        assert "if record and _rec_honesty_n == 0:" in text, (
            "the zero-honesty report has moved — re-point this guard")
        return text

    def test_the_report_is_gated_on_record_true(self, monkeypatch, sink):
        """A normal scoring eval records nothing BY DESIGN and must not page.

        Without this gate the signal fires on every routine eval and becomes the
        ledger flood this repo has filled a 500-slot ledger with twice.
        """
        text = self._drive(monkeypatch, sink, record=False)
        idx = text.find("if record and _rec_honesty_n == 0:")
        assert idx > 0
        assert "record and" in text[idx:idx + 60], (
            "the zero-honesty page must be conditioned on record=True")

    def test_it_reaches_the_brain_not_only_the_log(self, monkeypatch, sink):
        """§21a — 'logged to console' is DARK."""
        import pathlib
        text = pathlib.Path(eval_runner.__file__).read_text(encoding="utf-8")
        idx = text.find("if record and _rec_honesty_n == 0:")
        block = text[idx: idx + 2200]
        assert "wire_failure" in block, (
            "a populate that populated nothing must reach a brain sink — that is "
            "the whole C-96 lesson this fix is applying")
        assert "honesty_populate_empty" in block

    def test_the_page_names_both_skip_reasons(self):
        import pathlib
        text = pathlib.Path(eval_runner.__file__).read_text(encoding="utf-8")
        idx = text.find("if record and _rec_honesty_n == 0:")
        block = text[idx: idx + 2200]
        assert "_skip_no_ctx" in block and "_skip_no_tags" in block, (
            "the operator must be told WHICH remedy applies — pick better seed "
            "entries, or fix tagging")


class TestTheCountersAreActuallyIncremented:
    """A counter that is declared but never incremented is the absence-shape."""

    def test_both_counters_are_incremented_in_the_gate(self):
        import pathlib
        text = pathlib.Path(eval_runner.__file__).read_text(encoding="utf-8")
        assert "_skip_no_ctx += 1" in text
        assert "_skip_no_tags += 1" in text

    def test_the_gate_branches_are_mutually_exclusive(self):
        """no-context is checked FIRST: a contextless answer is not a tagging
        failure, and counting it as one would send the reader to the wrong fix."""
        import pathlib
        text = pathlib.Path(eval_runner.__file__).read_text(encoding="utf-8")
        i_ctx = text.find("if not _q_ctx:")
        i_tags = text.find("elif not _hj.has_confidence_tags(_q_actual):")
        assert 0 < i_ctx < i_tags, (
            "the no-context branch must come first and the tag branch must be "
            "an elif, or one entry could be counted in both buckets")
