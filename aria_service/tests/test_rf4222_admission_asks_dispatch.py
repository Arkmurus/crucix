"""R-F4222 / C-202: admission control refused requests dispatch would have served.

THE LIVE SYMPTOM. Four times on 2026-08-21, while verifying other fixes, a chat
request returned:

    503 {"error": "llm_unavailable",
         "cooling_providers": [{"name": "deepseek", "reason": "timeout",
                                "seconds_remaining": 51}]}

This is the operator's recurring "⚠️ I hit a snag" on WhatsApp.

TWO LAYERS, TWO ANSWERS TO THE SAME QUESTION.

* DISPATCH asks *servability*. `_should_skip(stats, alternative_exists=...)` is
  R-F3680's rule: "a cooldown is a routing instruction — go somewhere else for a
  while. It is only meaningful if there IS somewhere else." With a single-provider
  chain there is nowhere else, so a SOFT cooldown is deliberately last-resorted
  once `_LAST_RESORT_BREATHER_S` (5s) has passed. Its docstring promises she
  "never goes silent just because her primary LLM had a transient blip".

* ADMISSION asks *redundancy*. `_llm_serving_state` refuses whenever
  `health["resilient"] is not True`, and `resilient` is
  `len(active) > 0 and no recent exhaustion`, where `active` means only that a
  provider's cooldown TIMESTAMP has passed.

So the moment the sole provider soft-cools, `active` empties, `resilient` goes
False, and the gate refuses — **before dispatch ever gets to apply the
last-resort rule built for exactly this case.** R-F3680's mechanism is
unreachable through the chat endpoint. `general_vendor_depth` is 1 (operator
removed `deepseek_backup`, §17), so this fires on every transient DeepSeek
timeout, and 11 of 60 dispatches were erroring when measured.

WHAT IS NOT CHANGED. R-F2814's purpose — never enter a pipeline that cannot
serve, because it HANGS for the client's whole budget — is preserved exactly. A
HARD cooldown (billing/auth) is still refused: `_should_skip` returns True for it
even with no alternative, because "dialling is failing slower". Warmup is still
refused. `resilient` keeps its R-F3477 meaning as a redundancy/outcome signal and
is still published; it is simply no longer the thing admission asks.
"""

from __future__ import annotations

import time

import pytest


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.is_configured = True


def _chain(names=("deepseek",)):
    from aria_service.llm import fallback as fb
    chain = fb.FallbackProvider.__new__(fb.FallbackProvider)
    chain.providers = [_FakeProvider(n) for n in names]
    chain._stats = {n: {"calls": 0, "failures": 0, "cooldown_until": 0} for n in names}
    if hasattr(fb.FallbackProvider, "_reset_chain_outcome"):
        chain._reset_chain_outcome()
    return chain


def _soft_cool(chain, name, *, age_s: float, remaining_s: float = 60.0):
    """Put a provider in a SOFT cooldown that started `age_s` ago."""
    now = time.time()
    chain._stats[name].update({
        "cooldown_until": now + remaining_s,
        "cooldown_since": now - age_s,
        "last_kind": "timeout",
    })


def _hard_cool(chain, name, kind="billing"):
    now = time.time()
    chain._stats[name].update({
        "cooldown_until": now + 3600,
        "cooldown_since": now, "cooldown_hard": True,
        "last_kind": kind,
    })


# ── the predicate ────────────────────────────────────────────────────────────

def test_a_healthy_sole_provider_can_dispatch():
    assert _chain().can_dispatch_now() is True


def test_a_soft_cooled_sole_provider_can_still_dispatch():
    """THE DEFECT. Dispatch would dial it; admission was refusing."""
    chain = _chain()
    _soft_cool(chain, "deepseek", age_s=30.0)
    assert chain.get_health()["resilient"] is False, "precondition: not redundant"
    assert chain.can_dispatch_now() is True, (
        "R-F3680 last-resorts a soft-cooled provider when nothing else is "
        "reachable — admission must not refuse what dispatch would serve")


def test_the_breather_is_respected():
    """The deliberate 5s pause after a blip is NOT removed."""
    chain = _chain()
    _soft_cool(chain, "deepseek", age_s=1.0)
    assert chain.can_dispatch_now() is False


def test_a_hard_cooled_sole_provider_cannot_dispatch():
    """R-F2814's purpose survives: no credit / bad key must still fail fast."""
    chain = _chain()
    _hard_cool(chain, "deepseek", kind="billing")
    assert chain.can_dispatch_now() is False, (
        "a hard cooldown must still refuse — dialling is failing slower")


def test_with_a_real_alternative_the_cooldown_keeps_full_force():
    """Depth>1 behaviour is untouched: cool one, serve from the other."""
    chain = _chain(names=("deepseek", "deepseek_backup"))
    _soft_cool(chain, "deepseek", age_s=30.0)
    assert chain.can_dispatch_now() is True
    _soft_cool(chain, "deepseek_backup", age_s=30.0)
    assert chain.can_dispatch_now() is True, (
        "both soft-cooled and nothing else reachable — still last-resortable")


# ── it must be published, not just computed (§21a) ───────────────────────────

def test_health_publishes_the_dispatch_answer():
    chain = _chain()
    _soft_cool(chain, "deepseek", age_s=30.0)
    health = chain.get_health()
    assert health.get("can_dispatch_now") is True
    assert health.get("resilient") is False, (
        "the two fields answer DIFFERENT questions and must not be collapsed: "
        "resilient is redundancy (R-F3477), can_dispatch_now is servability")


# ── admission uses it ────────────────────────────────────────────────────────

class _StubProvider:
    def __init__(self, health):
        self._health = health
    def get_health(self):
        return self._health


class _Req:
    def __init__(self, provider):
        from types import SimpleNamespace
        self.app = SimpleNamespace(state=SimpleNamespace(llm_provider=provider))


def test_admission_allows_when_dispatch_would_serve():
    from aria_service.routes.aria import _llm_serving_state
    state = _llm_serving_state(_Req(_StubProvider({
        "resilient": False, "can_dispatch_now": True, "cooling_providers": [
            {"name": "deepseek", "reason": "timeout", "seconds_remaining": 51}]})))
    assert state["ready"] is True, (
        "refused a request the dispatcher would have served — the live "
        "'⚠️ I hit a snag'")


def test_admission_still_refuses_when_nothing_can_dial():
    from aria_service.routes.aria import _llm_serving_state
    state = _llm_serving_state(_Req(_StubProvider({
        "resilient": False, "can_dispatch_now": False, "cooling_providers": [
            {"name": "deepseek", "reason": "billing", "seconds_remaining": 3600}]})))
    assert state["ready"] is False
    assert state["reason"] == "llm_unavailable"


def test_admission_fails_closed_when_the_field_is_absent():
    """An older provider object must not be admitted by an absent field."""
    from aria_service.routes.aria import _llm_serving_state
    state = _llm_serving_state(_Req(_StubProvider({"resilient": False})))
    assert state["ready"] is False
