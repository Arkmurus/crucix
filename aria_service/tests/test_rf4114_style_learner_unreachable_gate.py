"""R-F4114 (C-147) — CAPABILITY: an unreachable input gate must reach the brain.

Live on aria-intel 2026-08-17, the only WARNING in the first half of the window:

    [style_learner] 200 entries scanned but 0 kept — filtered:
      {'total_entries': 200, 'not_grounded': 200, 'too_short': 0,
       'quarantined': 0, 'before_window': 0, 'kept': 0}

`_collect_gold_replies` requires `verification_status == "grounded"`. Sampling
120 live audit entries via `/api/aria/chat-audit/recent`:

    no_claims 78   unverified 34   well_formed 8   grounded 0

So `STYLE-LEARN-HOURLY` fires every hour, scans 200 entries, keeps zero, logs a
warning, and **nothing acts on it** — the module has no brain wiring at all
(§21a: no `wire_success`, no `wire_failure`, no `record_gap`), so a learning
loop that cannot learn is invisible to the self-heal loop that exists to notice
exactly this.

WHAT THIS FIX DELIBERATELY DOES NOT DO: widen the gate to accept
`well_formed`. That would change what ARIA learns style from — a quality
decision that belongs to the operator, not to a session tidying a warning. §1:
the band-aid is to make the symptom stop; the root cause is that the traffic
mix produces no grounded replies, and that is what has to become visible.

Run: python -m pytest aria_service/tests/test_rf4103_style_learner_unreachable_gate.py -v
"""
from __future__ import annotations

import asyncio

import pytest


def _entry(status, n=400):
    return {
        "response": "x" * n,
        "verification_status": status,
        "timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "user_message": "q",
    }


def _install(monkeypatch, entries, sink):
    from aria_service.intel import chat_audit_log as cal
    from aria_service.learning import style_learner as sl

    async def _recent(limit=200):
        return entries

    monkeypatch.setattr(cal, "get_recent", _recent)
    monkeypatch.setattr(sl, "_gate_gap_announced", False, raising=False)

    def _record(**kw):
        sink.append(kw)

    # Wire whichever sink the implementation reaches for.
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", _record, raising=False)
    return sl


# ══════════════════════════════════════════════════════════════════════
# THE DEFECT — 200 in, 0 out, and nothing downstream knows
# ══════════════════════════════════════════════════════════════════════

def test_a_totally_rejected_sample_reaches_the_brain(monkeypatch):
    sink: list = []
    sl = _install(monkeypatch, [_entry("no_claims") for _ in range(200)], sink)

    asyncio.run(sl._collect_gold_replies(lookback_hours=24))

    assert sink, (
        "200 entries were scanned, 0 were kept, and no brain sink heard about "
        "it. §21a: a path that only logs is DARK — and this one is the input "
        "gate of a learning loop that has been unable to learn for weeks."
    )
    blob = str(sink).lower()
    assert "not_grounded" in blob or "grounded" in blob, (
        "the signal must name the DOMINANT rejection reason — 'kept 0' alone "
        "does not tell anyone which gate is unreachable"
    )


def test_the_dominant_reason_is_named_not_just_the_total(monkeypatch):
    sink: list = []
    entries = [_entry("no_claims") for _ in range(150)]
    entries += [{"response": "short", "verification_status": "grounded"}] * 50
    sl = _install(monkeypatch, entries, sink)

    asyncio.run(sl._collect_gold_replies(lookback_hours=24))

    assert sink
    assert "200" in str(sink) or "150" in str(sink), (
        "the signal should carry the counts, so the reader can tell an "
        "unreachable gate from a quiet hour"
    )


# ══════════════════════════════════════════════════════════════════════
# THE GUARDS
# ══════════════════════════════════════════════════════════════════════

def test_an_empty_sample_does_not_cry_wolf(monkeypatch):
    """Nothing to scan is not a defect. A signal that fires on silence is one
    nobody reads — the C-96 reasoning."""
    sink: list = []
    sl = _install(monkeypatch, [], sink)

    asyncio.run(sl._collect_gold_replies(lookback_hours=24))

    assert not sink, "an empty audit log is a quiet hour, not an unreachable gate"


def test_a_healthy_sample_does_not_signal(monkeypatch):
    sink: list = []
    sl = _install(monkeypatch, [_entry("grounded") for _ in range(20)], sink)

    kept = asyncio.run(sl._collect_gold_replies(lookback_hours=24))

    assert kept, "precondition: grounded replies are keepable"
    assert not sink, "a working gate must stay silent"


def test_it_announces_once_not_every_run(monkeypatch):
    """STYLE-LEARN-HOURLY fires hourly and the condition is persistent; a
    per-run gap is the sanctions_coverage_degraded flood shape."""
    sink: list = []
    sl = _install(monkeypatch, [_entry("no_claims") for _ in range(200)], sink)

    asyncio.run(sl._collect_gold_replies(lookback_hours=24))
    asyncio.run(sl._collect_gold_replies(lookback_hours=24))
    asyncio.run(sl._collect_gold_replies(lookback_hours=24))

    assert len(sink) == 1, f"announced {len(sink)} times — must be once per process"


def test_the_gate_is_not_silently_widened():
    """The band-aid would be to accept `well_formed` and make the warning stop.
    That changes what ARIA learns style from — an operator decision."""
    from ._source_probe import function_source
    from aria_service.learning import style_learner as sl

    src = function_source(sl, "_collect_gold_replies")
    assert 'verdict != "grounded"' in src, (
        "the grounded-only bar was relaxed. If that is intended it is an "
        "operator quality decision, not a fix for a noisy log line."
    )
