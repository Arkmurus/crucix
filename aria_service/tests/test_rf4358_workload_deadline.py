"""R-F4358 (C-304) — a sovereign deadline must be derived from the output it
asks for, not set as a constant beside it.

OPERATOR: *"aria cannot not have outage neither go dark."* R-F4357 removed the
OUTAGE half — the sovereign can no longer be refused or clamped into silence on
a depth-1 chain. This is the other half: a call whose deadline is arithmetically
too short for the tokens it requested is lost work, every time, forever.

MEASURED on the live pod 2026-08-26, immediately after R-F4357 deployed:

    max_tokens=128    completion=128     7.5s  -> 17.1 tok/s
    max_tokens=512    completion=512    47.4s  -> 10.8 tok/s
    max_tokens=1024   completion=1024   54.9s  -> 18.7 tok/s

and in the same window, 8 breaches — now at **60.0s**, proving R-F4357 let the
caller's real deadline through, and proving 60s is still not enough:

    4 x aria.intel.adversarial_challenge
    4 x aria.llm.fallback

`adversarial_challenge` asks for `max_tokens=800, timeout=60.0`
(adversarial_challenge.py:1393-1397). At the slowest measured 10.8 tok/s that
output needs **~74s of generation** before any queue or prompt-eval overhead.
**The deadline cannot be met by arithmetic** — no amount of retrying, warming or
capacity changes that, because the two numbers were chosen independently of each
other.

THE RULE: a caller declares WHAT it wants (`max_tokens`); the deadline is a
CONSEQUENCE of that, not a second free parameter. So the floor is derived and
the declared value is honoured whenever it is already larger.

WHY NOT JUST RAISE THE CONSTANT: because it is the same defect one notch along.
A bigger constant is still independent of the request, so it is too long for a
128-token call (wasting the caller's time on a hang) and too short for a
4096-token one. This is the C-182 lesson — one deadline serving two classes —
and the C-302 note already records that a global bump makes every SHORT call
hang proportionally longer before failing.
"""
from __future__ import annotations

import pytest

from aria_service.llm import provider as prov_mod
from aria_service.llm import resilience as res


@pytest.fixture
def sole():
    """The sovereign as the whole chain — the live production shape."""
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(True)
    yield
    prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


@pytest.fixture
def has_alternative():
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(False)
    yield
    prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


# ── the derivation ─────────────────────────────────────────────────────────

def test_the_live_adversarial_budget_becomes_achievable(sole) -> None:
    """THE DEFECT, with the exact live numbers. 800 tokens at the measured rate
    cannot be produced in 60s; the deadline must be raised to something the
    request can actually meet."""
    got = res._effective_call_timeout(60.0, max_tokens=800)
    assert got > 60.0, "the impossible 60s deadline was left impossible"
    assert got >= 800 / res._SOVEREIGN_TOKENS_PER_S, (
        "the deadline is still shorter than the generation time it requires")


def test_deadline_scales_with_the_requested_output() -> None:
    """A deadline that is a CONSEQUENCE of max_tokens, not a constant beside it:
    ask for more, get proportionally more time."""
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(True)
    try:
        small = res._effective_call_timeout(1.0, max_tokens=128)
        large = res._effective_call_timeout(1.0, max_tokens=4096)
        assert large > small * 2, "the deadline does not track the workload"
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_a_generous_caller_is_never_shortened(sole) -> None:
    """LOAD-BEARING. This may only ever RAISE a floor. A caller that already
    declared plenty keeps exactly what it declared — silently shortening a
    deadline is the defect this fixes, pointed the other way."""
    assert res._effective_call_timeout(300.0, max_tokens=128) == pytest.approx(300.0)


def test_the_derived_extension_is_capped(sole) -> None:
    """A runaway max_tokens must not buy an unbounded hang."""
    huge = res._effective_call_timeout(1.0, max_tokens=10_000_000)
    assert huge <= res._SOVEREIGN_DEADLINE_CAP_S


def test_the_cap_never_shortens_a_caller_who_asked_for_more(sole) -> None:
    """The cap bounds the DERIVED extension only. A caller that explicitly asked
    for longer than the cap still gets what it asked for — the cap exists to
    stop us inventing time, not to overrule an explicit budget."""
    beyond = res._SOVEREIGN_DEADLINE_CAP_S + 120.0
    assert res._effective_call_timeout(beyond, max_tokens=64) == pytest.approx(beyond)


# ── scope: only where there is nothing to fall back to ─────────────────────

def test_no_derivation_when_an_alternative_exists(has_alternative) -> None:
    """UNCHANGED PATH. With a real fallback the clamp is correct: fail fast and
    let the alternative serve. Deriving a longer deadline there would delay the
    very handover the clamp exists to make."""
    assert res._effective_call_timeout(60.0, max_tokens=4096) == pytest.approx(
        res._ARIA_LLM_CALL_TIMEOUT)


def test_missing_max_tokens_falls_back_to_the_declared_deadline(sole) -> None:
    """A caller that declares no size gets exactly its declared deadline — we
    derive from evidence, never from a guessed default size."""
    assert res._effective_call_timeout(45.0, max_tokens=0) == pytest.approx(45.0)


@pytest.mark.parametrize("bad", [None, -1, "many"])
def test_an_unusable_max_tokens_never_shortens_or_raises(sole, bad) -> None:
    """Fail SAFE: an unparseable size must leave the caller's deadline intact
    rather than throw inside the LLM path or invent a number."""
    assert res._effective_call_timeout(45.0, max_tokens=bad) == pytest.approx(45.0)


# ── §21a — the extension must be observable ────────────────────────────────

def test_extensions_are_counted_for_health(sole) -> None:
    """A silently-stretched deadline hides a mis-specified caller. The count is
    what tells the operator WHICH budgets to fix at the source; the derivation
    is a safety net, not a licence to leave them wrong."""
    before = res.deadline_extension_count()
    res._effective_call_timeout(60.0, max_tokens=800)
    assert res.deadline_extension_count() > before


def test_no_count_when_nothing_was_extended(sole) -> None:
    """The counter must mean something: a caller that needed no help must not
    inflate it."""
    before = res.deadline_extension_count()
    res._effective_call_timeout(600.0, max_tokens=128)
    assert res.deadline_extension_count() == before
