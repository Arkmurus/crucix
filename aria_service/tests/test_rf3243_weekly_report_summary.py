"""R-F3243 — the weekly report's executive summary never worked.

Found in production logs by an independent review (2026-07-27):

    LLM summary generation failed: 'LLMResponseCache' object is not callable

`_generate_summary` called `await llm(prompt)`. Everything handed to it is an
LLMProvider (llm/provider.py:89) — LLMResponseCache wraps one — and the
interface is `complete(system_prompt, user_message) -> LLMResult`. There is no
`__call__`, so this raised TypeError on every single invocation.

It went unnoticed because the whole body sits inside a broad `except Exception`
that logs a WARNING and returns None. Every weekly report has shipped with no
executive summary since the function was written, and the report still looked
well-formed — a missing section reads as "nothing to say this week", not as a
crash. That is the real lesson: a broad except around an entire feature
converts a hard, immediate TypeError into a silent absence nobody chases.

These drive `_generate_summary` directly with a provider shaped exactly like
the real one, because the failure was in how the provider was CALLED (§3c).
"""

from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import weekly_report


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class _ProviderShapedLikeTheRealOne:
    """Mirrors LLMProvider: `complete(...)`, and deliberately NOT callable —
    which is precisely what LLMResponseCache is."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_message: str, **kwargs):
        self.calls.append((system_prompt, user_message))
        return _FakeResult("- Mastery rose.\n- Two capability gaps closed.")


class _NotAProvider:
    """A wiring error: something with no `complete`."""


def _report() -> dict:
    return {
        "generated_at": "2026-07-27T00:00:00Z",
        "knowledge": {"new_facts": 12},
        "mastery": {"composite": 0.62},
        "capability_gaps": {"open": 3},
    }


def test_summary_is_generated_through_the_provider_interface():
    """THE regression. Before this, every call raised TypeError and the caller
    swallowed it into None."""
    provider = _ProviderShapedLikeTheRealOne()
    out = asyncio.run(weekly_report._generate_summary(provider, _report()))

    assert out, "the weekly report still produces no executive summary"
    assert "Mastery rose" in out
    assert provider.calls, "the provider was never called"

    system_prompt, user_message = provider.calls[0]
    assert "ARIA" in system_prompt, "the role prompt was not sent as the system prompt"
    assert "DATA:" in user_message, "the report data never reached the model"


def test_a_non_callable_provider_no_longer_raises():
    """LLMResponseCache is not callable. Calling the object rather than its
    interface is the exact production failure."""
    provider = _ProviderShapedLikeTheRealOne()
    with pytest.raises(TypeError):
        provider("a prompt")          # premise: the real shape is not callable
    # ...and the summary path must not depend on it being callable.
    assert asyncio.run(weekly_report._generate_summary(provider, _report()))


def test_a_wiring_error_is_reported_not_silently_none(caplog):
    """Handing in something that is not a provider must SAY so. Returning a
    bare None is how this hid for so long."""
    import logging

    with caplog.at_level(logging.WARNING, logger="aria.intel.weekly_report"):
        out = asyncio.run(weekly_report._generate_summary(_NotAProvider(), _report()))
    assert out is None
    assert any("not an LLMProvider" in record.getMessage()
               for record in caplog.records), (
        "a non-provider was rejected silently — the wiring error is invisible")


def test_an_empty_model_response_yields_none_not_an_empty_summary():
    """A blank summary section is worse than none: it reads as 'nothing
    happened this week' rather than 'the model returned nothing'."""

    class _Blank(_ProviderShapedLikeTheRealOne):
        async def complete(self, system_prompt, user_message, **kwargs):
            return _FakeResult("   ")

    assert asyncio.run(weekly_report._generate_summary(_Blank(), _report())) is None
