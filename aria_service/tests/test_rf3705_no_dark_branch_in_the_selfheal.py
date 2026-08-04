"""R-F3705 — the last two dark branches in the self-heal, found by auditing my own work.

R-F3693 wired the recovery probe's three OUTCOMES (recovered / still_locked_out /
inconclusive). A mechanical §21a audit afterwards showed the two functions that
WRAP it were still dark:

  _probe_recovery_quietly   an exception escaping the probe was swallowed at
                            `logger.debug(...)`. Debug is not emitted at the
                            service's log level, so a probe that CRASHES rather
                            than merely failing is invisible — the self-heal dies
                            and the 24h lockout it exists to lift simply stands.

  _schedule_recovery_probes when probes were DUE but no event loop was available,
                            it returned silently. Nothing is ever scheduled, so
                            nothing recovers, and there is no signal at all.

Both are the R-F3687 failure class exactly: the recovery mechanism silently
no-ops and only a human driving it by hand finds out. R-F3687 was the probe
failing for an unrelated reason; these are the probe never running. The
distinction matters downstream, so they get their own outcome names rather than
being folded into `inconclusive` (which means "the probe RAN and could not
determine credit state").

§21d — a dark path is its own R-number, wired with a test that asserts the signal
lands rather than that a log line was written.
"""
import asyncio

import pytest

from aria_service.llm import fallback as fb
from aria_service.llm.provider import LLMProvider, LLMResult


def _run(coro):
    return asyncio.run(coro)


class _Provider(LLMProvider):
    def __init__(self, name):
        self.name = name

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt="", user_message="", **k):
        return LLMResult(text="ok", model=self.name)

    async def stream(self, *a, **k):
        yield "ok"


@pytest.fixture
def sink(monkeypatch):
    got = {"success": [], "failure": []}
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success", lambda **kw: got["success"].append(kw), raising=True)
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: got["failure"].append(kw), raising=True)
    return got


def _hard_cool(chain, name):
    now = fb.time.time()
    chain._stats[name] = {
        "calls": 1, "failures": 1, "last_failure": now - 10_000,
        "cooldown_until": now + 74_000, "last_kind": "billing",
        "cooldown_hard": True, "cooldown_since": now - 10_000,
    }


def test_capability_a_CRASHING_probe_is_reported_not_swallowed(sink, monkeypatch):
    """FAILS BEFORE: the exception vanished into logger.debug and the self-heal
    died silently — a funded provider would stay locked out for the full 24h with
    nothing recorded anywhere."""
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")

    async def _boom(_provider):
        raise RuntimeError("probe internals blew up")

    monkeypatch.setattr(chain, "_probe_recovery", _boom)

    _run(chain._probe_recovery_quietly(chain.providers[1]))

    assert sink["failure"], "a probe CRASH must reach the brain"
    detail = str(sink["failure"][-1]).lower()
    assert "crash" in detail, (
        f"a crash must be distinguishable from 'inconclusive' (which means the "
        f"probe RAN and could not determine credit state): {detail}"
    )
    assert "anthropic" in detail


def test_a_crash_never_escapes_into_the_caller(sink, monkeypatch):
    """It is a fire-and-forget background task — it must stay quiet upward while
    being loud toward the brain."""
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")

    async def _boom(_provider):
        raise RuntimeError("boom")

    monkeypatch.setattr(chain, "_probe_recovery", _boom)
    _run(chain._probe_recovery_quietly(chain.providers[1]))   # must not raise


def test_capability_probes_DUE_but_unschedulable_are_reported(sink):
    """FAILS BEFORE: returned silently, so the entire self-heal was inert with no
    signal. Called from a SYNC context (no running loop) with a probe due."""
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")
    assert [p.name for p in chain._providers_due_for_recovery_probe()] == ["anthropic"]

    chain._schedule_recovery_probes()      # no running loop here

    assert sink["failure"], (
        "probes were DUE and could not be scheduled — the self-heal is inert and "
        "that must not be silent"
    )
    assert "schedul" in str(sink["failure"][-1]).lower()


def test_nothing_due_stays_silent(sink):
    """Cry-wolf guard: no due probes is the healthy state, not an incident."""
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    chain._schedule_recovery_probes()
    assert not sink["failure"], "a no-op must not page"


def test_the_wrappers_are_no_longer_dark():
    """Mechanical §21a pin — the audit that found this must stay green."""
    import inspect
    import re
    sinks = re.compile(r"wire_failure|_wire_probe_outcome|_wire_selfheal_fault")
    for name in ("_probe_recovery_quietly", "_schedule_recovery_probes"):
        src = inspect.getsource(getattr(fb.FallbackProvider, name))
        assert sinks.search(src), f"{name} still has no brain sink"
        assert "logger.debug" not in src or sinks.search(src), (
            f"{name} must not rely on logger.debug as its only failure record"
        )
