"""R-F780 — deal-outcome predictor + calibration post-mortem ingest.

Tests the predictor's evidence retrieval + LLM scoring contract + the
record_outcome post-mortem feedback to mastery + brain_hook.
"""
from __future__ import annotations

import asyncio
from unittest import mock


def _sample_deal() -> dict:
    return {
        "type": "procurement_tender",
        "name": "FAA artillery procurement Q3 2026",
        "counterparty": "Some Defence Corp Ltd",
        "country": "Angola",
        "region": "lusophone_africa",
        "value_usd": 12_000_000,
    }


# ── predict_deal_outcome ────────────────────────────────────────────────────

def test_rf780_predict_returns_canonical_shape_without_llm():
    """Even without an LLM, the predictor returns the canonical
    structure so frontend code never sees a missing key."""
    from aria_service.intel import deal_predictor

    async def fake_gather(_):
        return {"nearest_case": None, "intel_signals": [], "sanctions_hits": []}

    async def run():
        with mock.patch.object(deal_predictor, "_gather_evidence", side_effect=fake_gather):
            return await deal_predictor.predict_deal_outcome(_sample_deal(), llm=None)

    result = asyncio.run(run())
    assert "deal_id" in result
    assert "evidence" in result
    assert "prediction" in result
    assert "topic_tags" in result
    pred = result["prediction"]
    assert pred["needs_llm_scoring"] is True
    assert pred["win_prob"] is None
    assert pred["top_risks"] == []


def test_rf780_predict_with_llm_returns_parsed_forecast():
    """When an LLM is provided, the predictor calls it + parses JSON."""
    from aria_service.intel import deal_predictor

    class FakeLLM:
        async def chat(self, system, messages):
            return (
                '{"win_prob": 0.62, '
                '"top_risks": ["counterparty PEP exposure", "FX volatility", "delivery delay"], '
                '"time_to_close_p50_days": 90, '
                '"what_would_change_my_mind": ["fresh sanctions hit", "competitor announcement"]}'
            )

    async def fake_gather(_):
        return {
            "nearest_case": {"score": 0.83, "method": "semantic",
                             "question": "prior Angola tender", "answer": "...",
                             "outcome": "won", "topic_tags": ["procurement", "angola"]},
            "intel_signals": [{"id": "s1"}, {"id": "s2"}],
            "sanctions_hits": [],
        }

    async def run():
        with mock.patch.object(deal_predictor, "_gather_evidence", side_effect=fake_gather):
            return await deal_predictor.predict_deal_outcome(_sample_deal(), llm=FakeLLM())

    result = asyncio.run(run())
    pred = result["prediction"]
    assert pred["win_prob"] == 0.62
    assert "counterparty PEP exposure" in pred["top_risks"]
    assert pred["time_to_close_p50_days"] == 90
    assert pred["needs_llm_scoring"] is False
    # Evidence is still in the response
    assert result["evidence"]["nearest_case"]["outcome"] == "won"


def test_rf780_predict_llm_failure_falls_back_to_default_shape():
    """If the LLM raises, the predictor returns the default shape with
    an llm_error field — never a 500."""
    from aria_service.intel import deal_predictor

    class BrokenLLM:
        async def chat(self, system, messages):
            raise RuntimeError("LLM provider timeout")

    async def fake_gather(_):
        return {"nearest_case": None, "intel_signals": [], "sanctions_hits": []}

    async def run():
        with mock.patch.object(deal_predictor, "_gather_evidence", side_effect=fake_gather):
            return await deal_predictor.predict_deal_outcome(_sample_deal(), llm=BrokenLLM())

    result = asyncio.run(run())
    pred = result["prediction"]
    assert pred["needs_llm_scoring"] is True
    assert "LLM provider timeout" in pred.get("llm_error", "")


def test_rf780_stable_deal_id_is_deterministic():
    """The same deal produces the same id every time — guarantees that
    a second predict on the same deal overwrites cleanly."""
    from aria_service.intel import deal_predictor
    d = _sample_deal()
    id_1 = deal_predictor._stable_deal_id(d)
    id_2 = deal_predictor._stable_deal_id(dict(d))
    assert id_1 == id_2
    # Different deal → different id
    d2 = dict(d, counterparty="Different Corp")
    assert deal_predictor._stable_deal_id(d2) != id_1


def test_rf780_topic_tags_inferred_from_deal_type_and_country():
    """The post-mortem ingest credits the right mastery cells based on
    the deal type + country."""
    from aria_service.intel import deal_predictor
    tags = deal_predictor._topic_tags_for_deal(_sample_deal())
    assert "procurement" in tags
    assert "country:angola" in tags
    assert "region:lusophone_africa" in tags


# ── record_outcome ─────────────────────────────────────────────────────────

def test_rf780_record_outcome_computes_brier_and_feeds_brain_hook():
    """A resolved deal must produce a Brier score + an absorb call."""
    from aria_service.intel import deal_predictor

    absorbs: list = []

    async def fake_absorb(**kw):
        absorbs.append(kw)
        return {"absorbed": True}

    async def run():
        with mock.patch("aria_service.intel.brain_hook.absorb",
                        side_effect=fake_absorb):
            return await deal_predictor.record_outcome(
                deal_id="deal_test_001",
                actual={"outcome": "won", "predicted_win_prob": 0.7},
                inferred_topic_tags=[],
            )

    out = asyncio.run(run())
    assert out["ok"] is True
    assert out["actual_label"] == 1.0
    # Brier = (0.7 - 1.0)^2 = 0.09
    assert out["brier_score"] == 0.09
    assert len(absorbs) == 1
    assert "deal_test_001" in absorbs[0]["summary"]
    assert absorbs[0]["module"] == "deal_predictor"


def test_rf780_record_outcome_lost_marks_success_false():
    """A 'lost' outcome flips success=False on the brain_hook absorb so
    the mistake_ledger picks it up as a learning signal."""
    from aria_service.intel import deal_predictor

    absorbs: list = []

    async def fake_absorb(**kw):
        absorbs.append(kw)

    async def run():
        with mock.patch("aria_service.intel.brain_hook.absorb",
                        side_effect=fake_absorb):
            return await deal_predictor.record_outcome(
                deal_id="deal_test_002",
                actual={"outcome": "lost", "predicted_win_prob": 0.85},
                inferred_topic_tags=[],
            )

    out = asyncio.run(run())
    assert out["ok"] is True
    # Brier = (0.85 - 0.0)^2 = 0.7225
    assert out["brier_score"] == 0.7225
    assert absorbs[0]["success"] is False


def test_rf780_record_outcome_rejects_unknown_outcome():
    """Invalid outcome strings degrade to ok=False with a reason."""
    from aria_service.intel import deal_predictor

    async def run():
        return await deal_predictor.record_outcome(
            deal_id="deal_test_003",
            actual={"outcome": "maybe", "predicted_win_prob": 0.5},
        )

    out = asyncio.run(run())
    assert out["ok"] is False
    assert "outcome must be won/lost/push" in out["reason"]


def test_rf780_record_outcome_with_topic_tags_attempts_mastery_feed():
    """When inferred_topic_tags is non-empty, the post-mortem attempts
    to call student.record_quiz_result/record_sample on each tag."""
    from aria_service.intel import deal_predictor

    calls: list = []

    def fake_record_sample(tag, correct):
        calls.append((tag, correct))

    async def fake_absorb(**kw):
        pass

    async def run():
        with mock.patch("aria_service.intel.brain_hook.absorb",
                        side_effect=fake_absorb), \
             mock.patch("aria_service.intel.student.record_quiz_result",
                        side_effect=fake_record_sample, create=True):
            return await deal_predictor.record_outcome(
                deal_id="deal_test_004",
                actual={"outcome": "won", "predicted_win_prob": 0.8},
                inferred_topic_tags=["procurement", "country:angola"],
            )

    out = asyncio.run(run())
    assert out["ok"] is True
    assert out["feeds"]["mastery"] == "fed"
    tag_set = {tag for tag, _ in calls}
    assert "procurement" in tag_set
    assert "country:angola" in tag_set
