"""R-F3693 — the self-healing paths shipped DARK, and it cost a whole fix.

§21a is a definition, not a vibe: a code path is WIRED iff it emits, on BOTH the
success and the failure branch, at least one of brain_hook.absorb /
capability_gaps.record_gap / mistake_ledger.record / a metric / a brain signal.
"Logged to console" is DARK.

R-F3685's recovery probe — the thing that decides whether a provider ARIA has
locked out for 24h comes back — reached the brain on NEITHER branch. It only
called `logger`. Consequences, all silent:

  * R-F3687 is the proof. The probe sent an empty system prompt, Anthropic
    rejected it (`cache_control cannot be set for empty text blocks`), the probe
    scored that as INCONCLUSIVE and left the lockout standing — every 15 minutes,
    forever. Nothing recorded it. It was found only because I drove the live
    chain by hand and noticed anthropic had not come back while deepseek_backup
    had. A wired probe reports "inconclusive" and the gap surfaces itself.
  * A provider RECOVERING is a significant, rare state change (§25a
    proprioception: she must know her own limb came back) and nothing observed it.
  * The R-F3680 last-resort dial — ARIA deliberately dialling a COOLING provider
    because nothing else was reachable — is the chain's most degraded serving
    state, and it too only logged.

§21d: spotting a dark path is itself an R-number — wire it, with a capability
test that emits the signal and asserts it LANDS. That is this file.
"""
import asyncio

import pytest

from aria_service.llm import fallback as fb
from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


class _Provider(LLMProvider):
    def __init__(self, name, *, fail=None):
        self.name = name
        self._fail = fail

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt="", user_message="", **k):
        if self._fail:
            raise self._fail
        return LLMResult(text="ok", model=self.name)

    async def stream(self, *a, **k):
        if self._fail:
            raise self._fail
        yield "ok"


@pytest.fixture
def sink(monkeypatch):
    """Capture what actually reaches the brain-wiring layer."""
    got = {"success": [], "failure": []}
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(
        ew, "wire_success",
        lambda **kw: got["success"].append(kw), raising=True)
    monkeypatch.setattr(
        ew, "wire_failure",
        lambda **kw: got["failure"].append(kw), raising=True)
    return got


def _hard_cool(chain, name, kind="billing"):
    now = fb.time.time()
    chain._stats[name] = {
        "calls": 1, "failures": 1, "last_failure": now - 10_000,
        "cooldown_until": now + 74_000, "last_kind": kind,
        "cooldown_hard": True, "cooldown_since": now - 10_000,
    }


# ── the probe's three outcomes must ALL reach the brain ──────────────────────


def test_capability_a_recovery_reaches_the_brain(sink):
    """A locked-out provider coming back is the §25a signal that matters most."""
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")

    assert _run(chain._probe_recovery(chain.providers[1])) is True

    assert sink["success"], "a RECOVERY must be recorded, not just logged"
    kw = sink["success"][-1]
    assert "anthropic" in str(kw), f"the signal must name the provider: {kw}"


def test_capability_a_confirmed_lockout_reaches_the_brain(sink):
    """Still no credit after a probe is operator-actionable — it must be visible
    somewhere the operator's surfaces read, not only in a log line."""
    chain = fb.FallbackProvider([
        _Provider("deepseek"),
        _Provider("anthropic", fail=ProviderError(
            "anthropic", "credit balance too low", kind="billing", retryable=False)),
    ])
    _hard_cool(chain, "anthropic")

    assert _run(chain._probe_recovery(chain.providers[1])) is False

    assert sink["failure"], "a confirmed lockout must be recorded"
    assert "anthropic" in str(sink["failure"][-1])


def test_capability_an_INCONCLUSIVE_probe_reaches_the_brain(sink):
    """THE R-F3687 CASE. The probe ran, failed for a reason unrelated to credit,
    and left the lockout standing — every 15 minutes, indefinitely. This is the
    branch whose silence hid a broken self-heal, so it is the branch that most
    needs to be observed."""
    chain = fb.FallbackProvider([
        _Provider("deepseek"),
        _Provider("anthropic", fail=ProviderError(
            "anthropic",
            'HTTP 400: {"message":"system.0: cache_control cannot be set for '
            'empty text blocks"}', status=400, kind="other", retryable=True)),
    ])
    _hard_cool(chain, "anthropic")

    assert _run(chain._probe_recovery(chain.providers[1])) is False

    assert sink["failure"], (
        "an inconclusive probe is a BROKEN SELF-HEAL until proven otherwise — "
        "it must not be the one branch that stays dark"
    )
    detail = str(sink["failure"][-1])
    assert "inconclusive" in detail.lower(), (
        f"the signal must distinguish inconclusive from a confirmed lockout, "
        f"or the two failure modes are indistinguishable downstream: {detail}"
    )


def test_the_probe_never_raises_through_its_own_wiring(monkeypatch):
    """A wiring bug must not break the self-heal it observes."""
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success", lambda **kw: 1 / 0, raising=True)
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: 1 / 0, raising=True)
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")

    assert _run(chain._probe_recovery(chain.providers[1])) is True


# ── the last-resort dial is the most degraded serving state ─────────────────


def test_capability_the_last_resort_dial_reaches_the_brain(sink):
    """R-F3680: ARIA dialling a COOLING provider because nothing else is
    reachable. §14 keeps calling that 'operational' to the USER — but the brain
    must still know it happened, or the chain's worst serving state is invisible."""
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")            # dead — no alternative
    now = fb.time.time()
    chain._stats["deepseek"] = {              # soft-cooling, breather spent
        "calls": 2, "failures": 2, "last_failure": now - 30,
        "cooldown_until": now + 30, "last_kind": "timeout",
        "cooldown_hard": False, "cooldown_since": now - 30,
        "last_recovery_probe": now,
    }

    result = _run(chain.complete("sys", "usr"))
    assert result.text == "ok", "she must still serve — that is R-F3680"

    assert sink["failure"], (
        "dialling a cooling provider as the last resort must be observable"
    )
    assert "last_resort" in str(sink["failure"][-1]).lower()


def test_stream_wires_it_too_per_clause_13(sink):
    chain = fb.FallbackProvider([_Provider("deepseek"), _Provider("anthropic")])
    _hard_cool(chain, "anthropic")
    now = fb.time.time()
    chain._stats["deepseek"] = {
        "calls": 2, "failures": 2, "last_failure": now - 30,
        "cooldown_until": now + 30, "last_kind": "timeout",
        "cooldown_hard": False, "cooldown_since": now - 30,
        "last_recovery_probe": now,
    }

    async def _drain():
        return [c async for c in chain.stream("sys", "usr")]

    assert _run(_drain()) == ["ok"]
    assert sink["failure"], "§13 — the stream fork must wire it as well"


def test_no_dark_branch_remains_in_the_probe():
    """Structural pin against the exact regression: every outcome branch of the
    probe must reach the brain, so a future edit cannot quietly re-darken one."""
    import inspect
    src = function_source(fb.FallbackProvider, "_probe_recovery")
    assert src.count("_wire_probe_outcome") >= 3, (
        "recovered / confirmed-down / inconclusive are three distinct outcomes "
        "and all three must be observed"
    )
