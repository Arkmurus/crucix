"""R-F4056 (C-120) — a deliberate DISABLE must not be wired as an engine failure.

R-F4052 (C-108) wired `learning_controller.run_cycle` to the brain on both
branches. But `run_cycle` returns `ok=False` for a case that is not a failure at
all — the controller being switched off:

    if not is_enabled():
        out["ok"] = False
        out["error"] = "controller disabled — set ARIA_LEARNING_CONTROLLER_ENABLED=1 ..."

so every tick while the flag is off would `wire_failure(...)` → a
`capability_gaps` entry claiming the learning engine is broken.

THIS IS THE R-F3703 DEFECT, REINTRODUCED BY MY OWN FIX. That entry records the
same mistake made against the coder scoreboard, where 4,007 `coder_disabled`
refusals — "we turned the lane off for a month" — were counted as failed
attempts and permanently shut an evidence gate. Its conclusion is the rule here:
**administrative outcomes are not quality outcomes.**

The consequence is worse than noise. `wire_failure` writes to BOTH
`capability_gaps.record_gap` (the coder's "something to fix" queue) and
`brain_hook.record_signal(success=False)` (the health metric). A disabled module
would therefore report `success_rate: 0.0` and invite the autonomous coder to
"fix" a flag the operator set on purpose.

A disable is reported as a THROTTLED SUCCESS-side note rather than dropped
silently: the module still needs to show it is alive and reachable, because
saying nothing is how C-104's modules became indistinguishable from dead.
"""
from __future__ import annotations

import asyncio

from aria_service.learning import learning_controller as lc


def _capture(monkeypatch):
    fails: list[dict] = []
    succs: list[dict] = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: fails.append(kw))
    monkeypatch.setattr(
        ew, "wire_success_throttled",
        lambda module, summary, **kw: succs.append({"module": module, "summary": summary}) or True,
    )
    return fails, succs


def test_disabled_controller_is_not_an_engine_failure(monkeypatch):
    """The defect: an operator switch reported as a broken engine."""
    fails, succs = _capture(monkeypatch)
    monkeypatch.setattr(lc, "is_enabled", lambda: False)

    out = asyncio.run(lc.run_cycle(max_topics=1, time_budget_s=1.0))

    assert out["ok"] is False          # the CONTRACT of run_cycle is unchanged
    assert not fails, (
        "a deliberately disabled controller was wired as an engine_failure — "
        "that is the R-F3703 defect: an administrative outcome counted as a "
        "quality outcome, which also invites the coder to 'fix' a flag the "
        f"operator set on purpose. Got: {fails}"
    )


def test_disabled_controller_still_reports_it_is_alive(monkeypatch):
    """Silence is not the fix — that is how C-104's modules looked dead."""
    fails, succs = _capture(monkeypatch)
    monkeypatch.setattr(lc, "is_enabled", lambda: False)

    asyncio.run(lc.run_cycle(max_topics=1, time_budget_s=1.0))

    assert succs, "a disabled controller emitted nothing at all"
    assert "disabled" in succs[0]["summary"].lower(), (
        f"the signal must say WHY it did no work: {succs[0]}"
    )
    assert succs[0]["module"] == "learning_controller"


def test_a_real_failure_is_still_wired_as_a_failure(monkeypatch):
    """The guard must not swallow genuine breakage — that would be worse."""
    fails, succs = _capture(monkeypatch)
    monkeypatch.setattr(lc, "is_enabled", lambda: True)

    async def _boom(_n):
        raise RuntimeError("collect exploded")

    monkeypatch.setattr(lc, "_collect_candidate_topics", _boom)

    out = asyncio.run(lc.run_cycle(max_topics=1, time_budget_s=1.0))

    assert out["ok"] is False
    assert fails, "a genuine cycle failure must still reach the brain (§21a)"
    assert "collect exploded" in fails[0].get("detail", "")
