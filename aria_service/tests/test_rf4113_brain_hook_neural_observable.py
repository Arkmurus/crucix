"""R-F4113 (C-146) — CAPABILITY: the neural tier's outcome must be observable.

Measured live on aria-intel 2026-08-17: **131 of 131** brain_hook absorb lines
in a 23-minute window read

    brain_hook(<module>): absorbed [mastery=True knowledge=True neural=False]

Not one of those was a measurement. The log statement sits at
`brain_hook_bg.py:~180`; `result["neural_ok"]` is assigned ONLY inside the
neural lane, which R-F1665 deliberately moved to run LAST — at `:214`, AFTER
the log. The caller seeds the dict with `"neural_ok": False`
(`brain_hook.py:832`), so the log was printing that seed, every time.

The one operator-visible signal for the neural tier therefore COULD NOT report
success. A guard that cannot fire — the same shape §1 records for three Phase A
gates and C-96 for `/health`.

WHAT IS DELIBERATELY NOT CHANGED: `_record_signal`, `_record_latency` and the
breaker still run BEFORE the neural lane. R-F1665 put them there so the breaker
measures durable-core latency rather than the slow GIL-bound neural encode;
moving them would re-open the absorb-p95 wedge. Only the *reporting* moves.
`result["neural_ok"]` also keeps its exact True/False domain — several suites
pin it.

Run: python -m pytest aria_service/tests/test_rf4102_brain_hook_neural_observable.py -v
"""
from __future__ import annotations

import asyncio
import logging

from aria_service.intel import brain_hook


_LONG_TEXT = "Neural tier gate needs more than fifty characters of text here."


def _reset_breaker():
    brain_hook._breaker_state["open"] = False
    brain_hook._breaker_state["tripped_at"] = 0.0
    brain_hook._breaker_state["consecutive_high"] = 0
    brain_hook._breaker_state["ticket_filed_this_episode"] = False
    brain_hook._recent_latencies_ms.clear()


def _patch_tiers(monkeypatch, neural=None):
    from aria_service.intel import student, knowledge as kn, neural_memory, memory_wal

    async def _mastery(*a, **kw):
        return None

    async def _knowledge(*a, **kw):
        return {}

    async def _neural(*a, **kw):
        return {"neurons_activated": 3, "connections_formed": 2}

    async def _record_signal(module, success=True, sector="", skipped=False):
        return None

    monkeypatch.setattr(student, "update_mastery", _mastery)
    monkeypatch.setattr(kn, "store_fact", _knowledge)
    monkeypatch.setattr(neural_memory, "learn_from_text", neural or _neural)
    monkeypatch.setattr(brain_hook, "_record_signal", _record_signal)
    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)
    monkeypatch.setattr(memory_wal, "record_pending_fact", lambda *a, **kw: None)


async def _absorb_and_drain(**kwargs) -> dict:
    result = await brain_hook.absorb(**kwargs)
    for _ in range(200):
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task() and not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    return result


def _run(monkeypatch, caplog, neural=None):
    _reset_breaker()
    _patch_tiers(monkeypatch, neural=neural)
    caplog.set_level(logging.INFO, logger="aria.brain_hook_bg")
    result = asyncio.run(_absorb_and_drain(
        module="dd_orchestrator", summary=_LONG_TEXT, detail=_LONG_TEXT,
    ))
    return result, "\n".join(r.getMessage() for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
# THE DEFECT — a successful neural encode must be visible as one
# ══════════════════════════════════════════════════════════════════════

def test_a_successful_neural_encode_is_reported_as_success(monkeypatch, caplog):
    result, logged = _run(monkeypatch, caplog)

    assert result["neural_ok"] is True, "precondition: the neural tier ran and succeeded"
    assert "neural=False" not in logged, (
        "the logs claim neural=False for an encode that SUCCEEDED. That line is "
        "printed before the neural lane runs, so it reports the caller's seed "
        "value and can never say otherwise — 131/131 live absorbs read False."
    )
    assert "neural" in logged.lower(), "the neural tier produced no observable signal at all"


def test_a_failed_neural_encode_is_distinguishable_from_a_successful_one(monkeypatch, caplog):
    """The point of an instrument is that its readings DIFFER."""
    async def _boom(*a, **kw):
        raise RuntimeError("encoder exploded")

    ok_result, ok_logged = _run(monkeypatch, caplog)
    caplog.clear()
    bad_result, bad_logged = _run(monkeypatch, caplog, neural=_boom)

    assert ok_result["neural_ok"] is True
    assert bad_result["neural_ok"] is False
    assert ok_logged != bad_logged, (
        "a successful and a failed neural encode produced IDENTICAL operator "
        "output — the reading does not depend on what happened"
    )


# ══════════════════════════════════════════════════════════════════════
# THE GUARD — do not 'fix' this by moving the breaker (R-F1665)
# ══════════════════════════════════════════════════════════════════════

def test_the_durable_core_is_still_measured_before_the_neural_lane():
    """R-F1665 put _record_latency/_maybe_trip_breaker BEFORE neural on purpose:
    the breaker must measure the fast durable core, not the GIL-bound encode
    that drove the 22-44s absorb-p95 wedge. A fix that moves them re-opens it."""
    from ._source_probe import function_source
    from aria_service.intel import brain_hook_bg

    src = function_source(brain_hook_bg, "absorb_tiers_bg")
    latency_at = src.find("_record_latency")
    neural_at = src.find("learn_from_text")
    assert latency_at != -1 and neural_at != -1
    assert latency_at < neural_at, (
        "the breaker/latency record must stay AHEAD of the neural lane (R-F1665)"
    )
