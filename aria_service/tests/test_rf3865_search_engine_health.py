"""R-F3865 — ARIA measures whether her own search sources are lying.

For 52 days SearXNG served query-independent junk while every consumer saw
`ok: True`. It was caught by a human running a bake-off by hand. That instinct
should not live in a chat session, because the engine list rots continuously:
R-F1659's "datacenter-tolerant" set was blocked two months later, R-F3849's
replacement was blocked the same week, and `yep` answered 20/20 and then began
returning 403 within the HOUR. Any design whose correctness depends on a
hand-maintained engine list is already failing; the only question is whether
anyone has noticed.

These tests pin the three properties that keep an automated judge honest, because
each one is a way this module could become worse than the problem:

  1. A MINIMUM SAMPLE — one unrelated result set proves nothing. A genuinely
     obscure query legitimately returns nothing related, and quarantining on that
     punishes an engine for the CALLER's query.
  2. QUARANTINE EXPIRES — every block is a TTL'd hypothesis. A permanent ban would
     make this module the next stale hand-maintained list, i.e. the thing it
     exists to replace.
  3. IT FAILS OPEN — an unreadable store must not blind ARIA's search. "Could not
     measure" is never "measured and failed" (§22).
"""
from __future__ import annotations

import pytest

from aria_service.intel import search_engine_health as seh
from aria_service.intel import search_searxng as sx


# ── property 1: a minimum sample ────────────────────────────────────────────────

def test_a_single_bad_observation_never_quarantines():
    """The caller's obscure query is not the engine's fault."""
    assert seh._should_quarantine({"total": 1, "independent": 1}) is False


def test_below_the_minimum_sample_nothing_is_judged():
    assert seh._should_quarantine(
        {"total": seh._MIN_SAMPLE - 1, "independent": seh._MIN_SAMPLE - 1}) is False


def test_at_the_minimum_sample_a_wholly_unrelated_engine_is_quarantined():
    assert seh._should_quarantine(
        {"total": seh._MIN_SAMPLE, "independent": seh._MIN_SAMPLE}) is True


def test_a_mixed_engine_is_left_alone():
    """bing measured 0/10 related on niche queries and 9/10 on popular ones. An
    engine that is merely UNEVEN must survive — the per-query R-F3853 filter is the
    precise instrument, this is only for sources that have stopped answering."""
    assert seh._should_quarantine({"total": 100, "independent": 50}) is False


def test_the_threshold_is_high_on_purpose():
    below = {"total": 100, "independent": int(100 * seh._QUARANTINE_RATIO) - 1}
    assert seh._should_quarantine(below) is False


# ── property 2: quarantine expires ──────────────────────────────────────────────

def test_quarantine_is_written_with_a_ttl():
    """A block must be a hypothesis, not a death sentence: blocked IPs get
    unblocked and rate limits reset."""
    from aria_service.tests._source_probe import function_source

    src = function_source(seh, "_quarantine")
    assert "_QUARANTINE_TTL_S" in src and "ex=" in src
    assert seh._QUARANTINE_TTL_S > 0


def test_counters_decay_rather_than_reset():
    """Halving keeps recent history meaningful; a hard reset would let a rotten
    engine wash its record clean, and a never-decaying counter would hold a
    recovered one hostage forever."""
    from aria_service.tests._source_probe import function_source

    src = function_source(seh, "record_observation")
    assert "_DECAY_AT" in src and "// 2" in src


# ── property 3: it fails open ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreadable_store_does_not_quarantine(monkeypatch):
    """Blinding search because the health bookkeeping broke is worse than the noise
    this module exists to catch."""
    async def _boom(*a, **k):
        raise RuntimeError("state_store: no connection")

    monkeypatch.setattr(seh.rs, "get", _boom)
    assert await seh.is_quarantined("bing") is False


@pytest.mark.asyncio
async def test_recording_never_raises_into_the_search_path(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(seh.rs, "get_json", _boom)
    monkeypatch.setattr(seh.rs, "set_json", _boom)
    out = await seh.record_observation("bing", query_independent=True)
    assert out["engine"] == "bing"      # returned a result rather than exploding


# ── the judgement cannot drift from the filter ─────────────────────────────────

def test_health_and_filter_share_one_definition_of_unrelated():
    """Quarantining on one definition of 'unrelated' while filtering on another
    would be a slow, silent contradiction."""
    rows = [
        {"engine": "bing", "title": "Why Am I Dizzy", "url": "", "snippet": ""},
        {"engine": "yep", "title": "Rosoboronexport sanctions", "url": "", "snippet": ""},
    ]
    verdicts = sx._per_engine_verdicts("Rosoboronexport sanctions", rows)

    assert verdicts == {"bing": True, "yep": False}
    _, dropped = sx._drop_query_independent_engines("Rosoboronexport sanctions", rows)
    assert set(dropped) == {e for e, bad in verdicts.items() if bad}


# ── acting on it is bounded by the R-F3857 lesson ──────────────────────────────

def test_quarantine_can_never_blank_the_result_set():
    """The R-F3857 lesson, generalised. Honouring every quarantine when that would
    leave NOTHING would hand the gates an empty set, which reads as 'nothing found'
    — a false clean. A health system that can blank an answer set is a worse
    failure than the degraded source it was policing."""
    from aria_service.tests._source_probe import function_source

    src = function_source(sx, "search")
    assert "if _live:" in src, (
        "quarantine filtering must keep the results when honouring it would leave "
        "none — see R-F3857")


def test_the_quarantine_is_wired_to_the_brain():
    """§21a — a source that stopped answering is exactly what ran unnoticed for 52
    days. This signal is what makes the engine list self-maintaining."""
    from aria_service.tests._source_probe import function_source

    src = function_source(seh, "_quarantine")
    assert "wire_failure" in src and "search_backend_failure" in src


def test_a_repeat_quarantine_does_not_re_alert():
    """Otherwise every query from a dead engine files another gap and the ledger
    fills with the system working correctly."""
    from aria_service.tests._source_probe import function_source

    src = function_source(seh, "_quarantine")
    assert "if already:" in src


@pytest.mark.asyncio
async def test_health_report_is_a_queryable_proprioception_surface(monkeypatch):
    """§25.3 — ARIA must be able to answer 'which of my senses are lying?'"""
    async def _get_json(key):
        return {"engine": "bing", "total": 40, "independent": 38, "ratio": 0.95}

    async def _get(key):
        return "1" if "quarantine" in key and "bing" in key else None

    monkeypatch.setattr(seh.rs, "get_json", _get_json)
    monkeypatch.setattr(seh.rs, "get", _get)

    rep = await seh.health_report(["bing"])
    assert rep["engines"]["bing"]["quarantined"] is True
    assert rep["engines"]["bing"]["judged"] is True
    assert rep["quarantined"] == ["bing"]
