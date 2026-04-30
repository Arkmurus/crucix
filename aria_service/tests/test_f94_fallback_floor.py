"""F94 (2026-04-30) regression: when the primary LLM provider
fails, the fallback must still get a usable per-call budget.

Live trigger: 08:30 UTC autonomous DAILY-PROC-UK fire — anthropic
fast-failed and the chain logged "Fallback budget exhausted (0.0s
remaining); skipping deepseek". Per-call budget arithmetic was
spending the caller's whole timeout on the primary, leaving the
secondary with nothing.

These tests assert the new shape: each non-cooling provider gets a
full per-call budget, capped at _MAX_FALLBACK_ATTEMPTS attempts.
"""
import asyncio
import pytest

from aria_service.llm.fallback import FallbackProvider
from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError


class _FakeProvider(LLMProvider):
    """Records the timeout it was called with so tests can assert on it."""

    def __init__(self, name, *, behaviour="ok", text="ok"):
        self.name = name
        self._behaviour = behaviour
        self._text = text
        self.calls = []  # list of (system, user, timeout)

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt, user_message, *,
                       max_tokens=4096, timeout=60.0):
        self.calls.append({"timeout": timeout, "max_tokens": max_tokens})
        if self._behaviour == "fast_timeout":
            raise ProviderError(self.name, "timeout", kind="timeout",
                                retryable=True)
        if self._behaviour == "rate_limit":
            raise ProviderError(self.name, "rate limited", kind="rate_limit",
                                retryable=True, status=429)
        if self._behaviour == "billing":
            raise ProviderError(self.name, "no credit", kind="billing",
                                retryable=False, status=402)
        return LLMResult(text=self._text, input_tokens=10, output_tokens=20,
                         model=self.name)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_secondary_gets_full_budget_when_primary_fast_fails():
    """The bug we shipped against: primary fast-fails, secondary
    skipped with '0.0s remaining'. Now secondary must get a full
    per-call timeout."""
    primary = _FakeProvider("primary", behaviour="fast_timeout")
    secondary = _FakeProvider("secondary", behaviour="ok", text="hi")
    chain = FallbackProvider([primary, secondary])

    result = _run(chain.complete("sys", "msg", timeout=100.0))

    assert result.text == "hi"
    assert len(primary.calls) == 1
    assert len(secondary.calls) == 1, (
        "secondary must be tried after primary fails — no skip"
    )
    # Each provider gets the full caller-supplied timeout, not a slice.
    assert primary.calls[0]["timeout"] == 100.0
    assert secondary.calls[0]["timeout"] == 100.0


def test_floor_raises_unreasonably_small_caller_timeout():
    """If a caller passes a sub-floor timeout (e.g. 1s left in their
    own budget), each provider still gets at least the floor — anything
    less isn't worth dialling out for."""
    primary = _FakeProvider("primary", behaviour="ok", text="hi")
    chain = FallbackProvider([primary])

    _run(chain.complete("sys", "msg", timeout=1.0))

    assert primary.calls[0]["timeout"] >= FallbackProvider._PROVIDER_MIN_BUDGET


def test_max_fallback_attempts_bounds_chain_walltime():
    """Cap on attempts so a pathological all-timeout cascade across
    five providers can't run for 5 * caller_timeout seconds."""
    providers = [
        _FakeProvider(f"p{i}", behaviour="fast_timeout") for i in range(5)
    ]
    chain = FallbackProvider(providers)

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "msg", timeout=30.0))

    # Only _MAX_FALLBACK_ATTEMPTS providers should have been tried.
    attempted = sum(1 for p in providers if p.calls)
    assert attempted == FallbackProvider._MAX_FALLBACK_ATTEMPTS


def test_failed_provider_falls_through_to_next():
    """When one provider hard-fails, the next is tried in the same call.
    This is the basic fallback contract; F94 must preserve it."""
    p_billing = _FakeProvider("billing_provider", behaviour="billing")
    p_ok = _FakeProvider("ok_provider", behaviour="ok", text="hi")
    chain = FallbackProvider([p_billing, p_ok])

    result = _run(chain.complete("sys", "msg", timeout=30.0))

    assert result.text == "hi"
    assert len(p_billing.calls) == 1
    assert len(p_ok.calls) == 1


def test_cooling_provider_skipped_on_subsequent_call():
    """After a HARD cooldown trip, a second call must skip the cooling
    provider entirely — no extra .complete() invocation against it."""
    p_billing = _FakeProvider("billing_provider", behaviour="billing")
    p_ok = _FakeProvider("ok_provider", behaviour="ok", text="hi")
    chain = FallbackProvider([p_billing, p_ok])

    # First call trips the cooldown.
    _run(chain.complete("sys", "msg", timeout=30.0))
    assert len(p_billing.calls) == 1

    # Second call: cooling provider is skipped, OK provider serves directly.
    _run(chain.complete("sys", "msg", timeout=30.0))
    assert len(p_billing.calls) == 1, "cooling provider must not be re-tried"
    assert len(p_ok.calls) == 2
