"""R-F576 (2026-05-16) — composite-score honesty fallback tests.

Live evidence: dashboard at intel.arkmurus.com/aria-brain showed
`verification (35%): 50%* (default — no data yet)` while the
verification panel underneath showed "Verification rate 39%
(9 verified / 14 unverified / 5 blocking)". The composite was
using the 0.5 neutral prior even when 23 real verdicts existed.

Root cause: source_verifier.get_verification_stats().avg_grounded_rate
is None when entries lack a numeric grounded_rate field (the
post-RAG grounding pass doesn't always populate it). autonomy_scorer
read only that field and missed the verdict-ratio that was right
there in `by_verdict`.

R-F576 adds a verdict-ratio proxy fallback: if avg_grounded_rate is
None but verdicts exist, compute verified / (verified+unverified+
contradicted). Same shape for honesty signal #4 via status-ratio
fallback. Dashboard "no data yet" only fires when there are no
verdicts at all.

Tests pin the behaviour:
  - With grounded_rate populated → use it (back-compat)
  - With grounded_rate None but verdicts present → verdict-ratio
  - With nothing at all → neutral 0.5 + honest source label
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


def _patch_inputs(monkeypatch, *,
                  verification_stats: dict | None = None,
                  honesty_stats: dict | None = None,
                  mastery_report: dict | None = None,
                  blocks_24h: int = 0):
    """Stub the four data sources autonomy_scorer reads."""
    from aria_service.intel import source_verifier, honesty_judge, student, redis_store as rs

    async def fake_v_stats():
        return verification_stats or {}

    async def fake_h_stats():
        return honesty_stats or {}

    async def fake_mastery():
        return mastery_report or {"headline_mastery": 0.8, "overall_mastery": 0.8, "topics": {}, "weak_topics": []}

    async def fake_rs_get(k):
        if "predictor:blocks" in k:
            return str(blocks_24h)
        return None

    async def fake_set_json(k, v, ex=None):
        return None

    monkeypatch.setattr(source_verifier, "get_verification_stats", fake_v_stats)
    monkeypatch.setattr(honesty_judge, "get_honesty_stats", fake_h_stats)
    monkeypatch.setattr(student, "get_mastery_report", fake_mastery)
    monkeypatch.setattr(rs, "get", fake_rs_get)
    monkeypatch.setattr(rs, "set_json", fake_set_json)


# ── Back-compat: when grounded_rate is present, use it ────────────────


def test_uses_grounded_rate_when_present(monkeypatch):
    _patch_inputs(monkeypatch, verification_stats={
        "avg_grounded_rate": 0.75,
        "rate_sample_size": 20,
        "by_verdict": {"verified": 15, "unverified": 5},
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    assert result["signals"]["verification"] == 0.75
    assert result["details"]["verification_source"] == "avg_grounded_rate"
    assert result["details"]["verification_samples"] == 20


# ── R-F576 fix: verdict-ratio fallback when grounded_rate is None ─────


def test_verdict_ratio_fallback_when_grounded_rate_none(monkeypatch):
    """The exact dashboard symptom: 23 verdicts (9 verified, 14
    unverified, 0 contradicted) but avg_grounded_rate is None.
    Composite must use 9/23 = 0.391, NOT the 0.5 neutral prior."""
    _patch_inputs(monkeypatch, verification_stats={
        "avg_grounded_rate": None,
        "rate_sample_size": 0,
        "by_verdict": {"verified": 9, "unverified": 14},
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    assert result["signals"]["verification"] is not None
    assert abs(result["signals"]["verification"] - (9/23)) < 0.001
    assert result["details"]["verification_source"] == "verdict_ratio_rf576"
    assert result["details"]["verification_samples"] == 23


def test_verdict_ratio_counts_contradicted_in_denominator(monkeypatch):
    """verified + unverified + contradicted go in the denominator;
    the score is verified / total."""
    _patch_inputs(monkeypatch, verification_stats={
        "avg_grounded_rate": None,
        "by_verdict": {"verified": 6, "unverified": 3, "contradicted": 1},
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    assert abs(result["signals"]["verification"] - 0.6) < 0.001  # 6/10
    assert result["details"]["verification_samples"] == 10


def test_no_data_falls_back_to_neutral_with_honest_source_label(monkeypatch):
    """Capability: when there are no verdicts AT ALL, fall back to
    neutral 0.5 but label the source honestly so the dashboard's
    asterisk is still warranted."""
    _patch_inputs(monkeypatch, verification_stats={
        "avg_grounded_rate": None,
        "by_verdict": {},
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    # Signal is None — composite math defaults to 0.5 internally
    assert result["signals"]["verification"] is None
    assert result["details"]["verification_source"] == "no_data_neutral_prior"


# ── Same shape for honesty signal #4 ──────────────────────────────────


def test_honesty_rate_uses_avg_honesty_score_when_present(monkeypatch):
    # R-F906: signal renamed grounded_rate -> honesty_rate (it reads the
    # honesty judge, not source grounding).
    _patch_inputs(monkeypatch, honesty_stats={
        "avg_honesty_score": 0.82,
        "scored_sample_size": 15,
        "by_status_24h": {"ok": 12, "suspicious": 3},
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    assert result["signals"]["honesty_rate"] == 0.82
    assert result["details"]["honesty_rate_source"] == "avg_honesty_score"


def test_honesty_rate_status_ratio_fallback_uses_24h_window(monkeypatch):
    """R-F906: when avg_honesty_score is None the status-ratio proxy must
    use the RECENT-WINDOW breakdown (by_status_24h), NOT the all-time
    by_status that pinned the signal at a permanently depressed value."""
    _patch_inputs(monkeypatch, honesty_stats={
        "avg_honesty_score": None,
        "by_status": {"ok": 1, "suspicious": 99},   # all-time: must be IGNORED
        "by_status_24h": {"ok": 8, "suspicious": 2, "failed": 0},
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    assert abs(result["signals"]["honesty_rate"] - 0.8) < 0.001  # 8/10 from 24h
    assert result["details"]["honesty_rate_source"] == "status_ratio_24h_rf906"
    assert result["details"]["honesty_rate_samples"] == 10


def test_honesty_rate_no_recent_data_uses_neutral_not_depressed_alltime(monkeypatch):
    """R-F906 capability: the exact bug being fixed. With no recent honesty
    data, the signal must be None (-> neutral 0.5 in composite math), NOT a
    depressed all-time ok/total ratio. Pre-R-F906 this returned 0.17
    (17/100) and dragged the composite into a permanent DEGRADED read."""
    _patch_inputs(monkeypatch, honesty_stats={
        "avg_honesty_score": None,
        "by_status": {"ok": 17, "suspicious": 83},  # all-time 17% — the old drag
        # no by_status_24h key at all (no recent judgments)
    })
    from aria_service.intel import autonomy_scorer
    result = asyncio.run(autonomy_scorer.compute_composite())
    assert result["signals"]["honesty_rate"] is None
    assert result["details"]["honesty_rate_source"] == "no_data_neutral_prior"


# ── Composite math sanity: real verdicts move the score ───────────────


def test_composite_score_moves_with_verdict_ratio(monkeypatch):
    """Capability proof: when 39% verdicts exist, composite must
    differ from a system with 0 verdicts (no longer identical at 50%).
    This is the user-visible bug fix — score honesty restored."""
    from aria_service.intel import autonomy_scorer

    # Scenario A: no verdicts (pre-R-F576 behaviour both before AND after)
    _patch_inputs(monkeypatch, verification_stats={
        "avg_grounded_rate": None, "by_verdict": {}
    }, honesty_stats={"avg_honesty_score": None, "by_status": {}})
    a = asyncio.run(autonomy_scorer.compute_composite())

    # Scenario B: 39% verdict ratio (the dashboard's real number)
    _patch_inputs(monkeypatch, verification_stats={
        "avg_grounded_rate": None,
        "by_verdict": {"verified": 9, "unverified": 14},
    }, honesty_stats={"avg_honesty_score": None, "by_status": {}})
    b = asyncio.run(autonomy_scorer.compute_composite())

    # Pre-R-F576: a == b (both 50% defaults).
    # Post-R-F576: b is LOWER than a because 39% < 50%.
    assert b["composite_score"] < a["composite_score"], (
        f"R-F576: composite must differ when verdicts exist. "
        f"a (no verdicts)={a['composite_score']}, "
        f"b (39% verdicts)={b['composite_score']}"
    )
