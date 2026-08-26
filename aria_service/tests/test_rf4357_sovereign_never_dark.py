"""R-F4357 (C-303) — the sovereign must never be clamped or cooled out of a
chain that has nowhere else to go.

OPERATOR, 2026-08-26: *"aria cannot be on cooldown, she is running on her own
reasoning and everything else is running on it as well … we cannot have a cool
down period."* With `ARIA_LLM_PRIMARY_ALL=1` and `chain_order: ["aria_llm"]`,
cooling the sovereign is not a routing decision — it is a total reasoning
outage for the whole operating system.

MEASURED LIVE on aria-intel, 2026-08-26, across two builds:

    145 x "attempt exceeded its 15.0s wall-clock ceiling"  (one value, uniformly)
    [circuit_breaker] aria_llm: CLOSED -> OPEN (3 consecutive failures,
                                reason=server, cooldown=300s)   x4 in one boot
    [aria_llm] ARIA-LLM unavailable (cold/unproven or breaker OPEN)
                                — skipping to fallback     <- to NOTHING

THE LOOP, and every step of it was verified in the tree:

  1. `resilience._HealthCheckedProvider.complete` clamps every sovereign call:
     `eff_timeout = min(timeout, _ARIA_LLM_CALL_TIMEOUT)` (12s). Its stated
     purpose, at resilience.py:67, is "clamp the per-call deadline so a hang
     fast-fails **to DeepSeek**".
  2. DeepSeek was removed from the chain by operator directive. **The premise
     is dead and the code kept obeying it** — the same shape as R-F4028 (C-98),
     where a backup tier outlived the reason it existed.
  3. `openai_compat` then floors attempt 0 at `_MIN_RETRY_SECONDS` (15.0), which
     is why every breach reports 15.0 regardless of what the caller asked for.
     `adversarial_challenge` asks for 60s and is given 15.
  4. Long-form work — article analysis, codegen, adversarial challenge — crosses
     15s BY ARITHMETIC on a 7B emitting 500-1500 tokens. The provider is NOT
     slow: measured 0.83s for /v1/models and 1.1s for a real completion.
  5. Each timeout calls `record_user_failure()`; three consecutive open the
     breaker for 300s.
  6. `_admission()` then refuses EVERY sovereign call for those 300s — and the
     chain has no alternative, so ARIA has no reasoning at all.
  7. Each failure logs ERROR on an `aria.*` logger, which resets the Phase A
     gate #3 streak (C-302).

**The doctrine already exists and the gate bypasses it.** R-F3680 (fallback.py)
dials a provider *"DESPITE its cooldown — it is the only reachable provider
left; going silent is worse"*, and R-F4330 (C-278) already exempts self-hosted
providers from soft cooldown because "a cooldown protects a VENDOR relationship
… no billing domain, no quota, no lockout to deepen". Both rulings are correct
and neither reaches this wrapper, which refuses BEFORE the chain ever dials.

So the fix is not a new policy — it is letting the existing one apply. The chain
already computes the predicate (`_has_reachable_alternative`, used on BOTH the
complete and stream paths); it simply never told the wrapper.

WHAT MUST NOT CHANGE, and each is pinned below:
  * when an alternative EXISTS, clamp and fail-closed exactly as before —
    fast-failing to a real fallback is right, and this must not become a
    blanket removal of the gate;
  * the health checker keeps recording, so a genuinely dead pod is still
    visible and still fails fast on its own connection error;
  * removing the cooldown does NOT remove the fallback (R-F4330's crux): the
    failing request still falls through on that same turn.
"""
from __future__ import annotations

import pytest

from aria_service.llm import provider as prov_mod
from aria_service.llm import resilience as res


# ── 1. the shared predicate ────────────────────────────────────────────────

def test_sole_provider_flag_exists_and_defaults_off() -> None:
    """The chain must be able to tell the wrapper 'there is nowhere else'.
    Default OFF so every existing caller keeps today's behaviour."""
    assert hasattr(prov_mod, "SOLE_PROVIDER_DIAL"), (
        "no channel for the chain to declare a last-resort dial")
    assert prov_mod.SOLE_PROVIDER_DIAL.get() is False


# ── 2. the clamp ───────────────────────────────────────────────────────────

def test_clamp_applies_when_an_alternative_exists() -> None:
    """UNCHANGED PATH. With somewhere to fall back to, clamping is correct:
    fast-fail and let the alternative serve."""
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(False)
    try:
        assert res._effective_call_timeout(60.0) == pytest.approx(
            res._ARIA_LLM_CALL_TIMEOUT)
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_clamp_is_lifted_when_the_sovereign_is_all_there_is() -> None:
    """THE DEFECT. Clamping preserves budget for a fallback. With none, it
    converts a slow SUCCESS into a hard failure and then silence."""
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(True)
    try:
        assert res._effective_call_timeout(60.0) == pytest.approx(60.0), (
            "the caller's deadline must be honoured when nothing else can serve")
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_lifting_the_clamp_never_shortens_a_caller() -> None:
    """A caller asking for LESS than the clamp still gets what it asked for —
    lifting a ceiling must never raise a floor."""
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(True)
    try:
        assert res._effective_call_timeout(5.0) == pytest.approx(5.0)
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


# ── 3. the admission gate ──────────────────────────────────────────────────

class _Checker:
    def __init__(self, available: bool) -> None:
        self._a = available

    def is_available(self) -> bool:
        return self._a


def test_open_breaker_still_refuses_when_an_alternative_exists(monkeypatch) -> None:
    """UNCHANGED PATH. An OPEN breaker with somewhere to go must still skip —
    this fix must not become a blanket removal of the gate."""
    monkeypatch.setattr(res, "_health_checker_instance", _Checker(False), raising=False)
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(False)
    try:
        ok, why = res._sovereign_admission()
        assert ok is False and "breaker" in why.lower()
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_open_breaker_admits_when_going_silent_is_the_alternative(monkeypatch) -> None:
    """THE DEFECT, and R-F3680's ruling applied where it was being bypassed:
    a refusal that routes to nothing is an outage, not a routing decision."""
    monkeypatch.setattr(res, "_health_checker_instance", _Checker(False), raising=False)
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(True)
    try:
        ok, _why = res._sovereign_admission()
        assert ok is True, (
            "refusing the only provider there is does not route around a fault, "
            "it takes ARIA's whole reasoning dark")
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_a_missing_health_checker_still_admits_a_sole_sovereign(monkeypatch) -> None:
    """'Unproven' is not 'dead'. With no checker AND no alternative, trying is
    strictly better than guaranteed silence — the pod answers or it does not,
    and either way we learn something the refusal never would."""
    monkeypatch.setattr(res, "_health_checker_instance", None, raising=False)
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(True)
    try:
        ok, _ = res._sovereign_admission()
        assert ok is True
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_a_missing_health_checker_still_refuses_when_alternatives_exist(monkeypatch) -> None:
    """R-F2686's fail-closed rule is preserved wherever it can actually route."""
    monkeypatch.setattr(res, "_health_checker_instance", None, raising=False)
    tok = prov_mod.SOLE_PROVIDER_DIAL.set(False)
    try:
        ok, why = res._sovereign_admission()
        assert ok is False and "checker" in why.lower()
    finally:
        prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


def test_healthy_sovereign_admits_either_way(monkeypatch) -> None:
    """The normal path is untouched by any of this."""
    monkeypatch.setattr(res, "_health_checker_instance", _Checker(True), raising=False)
    for flag in (False, True):
        tok = prov_mod.SOLE_PROVIDER_DIAL.set(flag)
        try:
            assert res._sovereign_admission()[0] is True
        finally:
            prov_mod.SOLE_PROVIDER_DIAL.reset(tok)


# ── 4. §13 — the chain must declare it on BOTH transports ──────────────────

class _Recorder(prov_mod.LLMProvider):
    """A provider that records what the chain declared while dialling it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[bool] = []

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, system_prompt, user_message, *, max_tokens=4096,
                       timeout=60.0, model=None):
        self.seen.append(prov_mod.SOLE_PROVIDER_DIAL.get())
        return prov_mod.LLMResult(text="ok", model=self.name)

    async def stream(self, system_prompt, user_message, *, max_tokens=4096,
                     timeout=120.0, on_done=None, model=None):
        self.seen.append(prov_mod.SOLE_PROVIDER_DIAL.get())
        yield "ok"


@pytest.mark.asyncio
async def test_chain_declares_sole_TRUE_when_it_is_the_only_provider() -> None:
    """BEHAVIOURAL, not a source scan. An earlier version of this test asserted
    only that the string 'SOLE_PROVIDER_DIAL' appeared in the method — which a
    hardcoded `set(False)` satisfies while leaving the wrapper just as blind.
    Assert the VALUE the provider actually observes."""
    from aria_service.llm.fallback import FallbackProvider

    only = _Recorder("aria_llm")
    await FallbackProvider([only]).complete("sys", "hi")
    assert only.seen == [True], (
        "a one-provider chain must declare the dial as last-resort")


@pytest.mark.asyncio
async def test_chain_declares_sole_FALSE_when_an_alternative_exists() -> None:
    """The counter-guard: with a second provider behind it, the dial is NOT
    last-resort and the clamp/fail-closed behaviour must stay in force."""
    from aria_service.llm.fallback import FallbackProvider

    head, spare = _Recorder("aria_llm"), _Recorder("deepseek")
    await FallbackProvider([head, spare]).complete("sys", "hi")
    assert head.seen == [False], (
        "with a reachable alternative the dial must not claim last-resort")


@pytest.mark.asyncio
async def test_stream_transport_declares_it_too() -> None:
    """§13 stream-bypass: a guard on one transport and not the other is this
    repo's repeat failure, and web chat streams."""
    from aria_service.llm.fallback import FallbackProvider

    only = _Recorder("aria_llm")
    async for _ in FallbackProvider([only]).stream("sys", "hi"):
        pass
    assert only.seen == [True], "the stream fork never declared the dial"


@pytest.mark.asyncio
async def test_the_flag_does_not_leak_past_the_dial() -> None:
    """A contextvar set and never reset would lift the clamp for every later
    call on the same task. It must be back to the default once the chain
    returns."""
    from aria_service.llm.fallback import FallbackProvider

    await FallbackProvider([_Recorder("aria_llm")]).complete("sys", "hi")
    assert prov_mod.SOLE_PROVIDER_DIAL.get() is False, "the dial flag leaked"


def test_an_unreadable_flag_never_widens_the_gate(monkeypatch) -> None:
    """FAIL-SAFE DIRECTION. If the contextvar cannot be read, the honest answer
    is 'not last-resort' — that keeps the existing fail-closed behaviour.
    Guessing True would lift the clamp and the breaker gate on evidence we do
    not have, which is the opposite of what an unknown should buy."""
    class _Boom:
        def get(self):
            raise RuntimeError("contextvar unreadable")

    monkeypatch.setattr(prov_mod, "SOLE_PROVIDER_DIAL", _Boom(), raising=False)
    assert res._sole_dial() is False
