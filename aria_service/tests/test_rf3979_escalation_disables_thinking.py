"""R-F3979 / C-68 — the reasoning-truncation escalation fed the disease.

When DeepSeek spends its whole budget thinking and returns no answer, R-F3627
retries the SAME provider with DOUBLE the token headroom. That is the wrong
correction, and it is why the live gap ledger still carries the failure with
`attempts=1`: the retry either never ran, or would not have helped if it had.

**Measured against the live production key, 2026-08-13/14, same prompt, same
`max_tokens=1024`:**

    baseline                       finish=length  reasoning=5334 chars  answer=0     -> NO ANSWER
    thinking:{"type":"disabled"}   finish=length  reasoning=0    chars  answer=4743  -> ANSWER
    baseline, max_tokens=8192      finish=stop    reasoning=20826 chars answer=10481 -> ANSWER

The middle row is the cure applied to the exact disease. The third row is why
enlarging the budget is not: given more room the model reasons MORE (20,826
chars), so a doubled budget buys thinking, not answering. It can succeed — but
only by paying for the deliberation the error was complaining about, and it costs
79.2s against the thinking-disabled retry's 13.9s. That speed matters directly,
because R-F3629 refuses the retry when under `_MIN_RETRY_SECONDS` (15s) of the
caller's clock remains — the guard that made `attempts=1` permanent.

**The silent-ignore trap, which is the reason this needed live probing rather
than reading.** Every candidate parameter returned HTTP 200:

    reasoning_effort=low        -> accepted, reasoning STILL 113 chars
    reasoning_effort=minimal    -> accepted, reasoning STILL 121 chars
    enable_thinking=False       -> accepted, reasoning STILL  30 chars
    chat_template_kwargs        -> accepted, reasoning STILL  41 chars
    reasoning.max_tokens        -> accepted, reasoning STILL  30 chars
    thinking.type=disabled      -> accepted, reasoning       0 chars   <- the only one

The API accepts unknown keys and ignores them. A fix built on `reasoning_effort`
would have passed review, deployed green, and changed nothing.

The doubled budget is KEPT alongside, because with thinking disabled that
headroom is now pure ANSWER room rather than an invitation to deliberate.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.llm import openai_compat as OC
from aria_service.llm.openai_compat import KIND_REASONING_TRUNCATED
from aria_service.llm.provider import ProviderError


def _provider():
    return OC.OpenAICompatProvider(
        name="deepseek", api_key="k", model="deepseek-v4-flash",
        base_url="https://example.invalid/v1",
    )


# ── the payload contract ─────────────────────────────────────────────────────

def test_thinking_is_not_disabled_on_the_first_attempt():
    """Attempt 0 must reason normally — that is what the model is for."""
    p = _provider()
    payload = p._completion_payload(
        "sys", "usr", eff_model="deepseek-v4-flash", max_tokens=1024,
        disable_thinking=False,
    )
    assert "thinking" not in payload


def test_the_escalation_payload_disables_thinking():
    p = _provider()
    payload = p._completion_payload(
        "sys", "usr", eff_model="deepseek-v4-flash", max_tokens=2048,
        disable_thinking=True,
    )
    assert payload.get("thinking") == {"type": "disabled"}, (
        "the retry does not disable thinking — a doubled budget invites MORE "
        "reasoning (measured: 20,826 chars when given room), so it feeds the "
        "failure it is meant to cure"
    )


def test_the_rest_of_the_payload_is_unchanged():
    p = _provider()
    payload = p._completion_payload(
        "sys", "usr", eff_model="deepseek-v4-flash", max_tokens=2048,
        disable_thinking=True,
    )
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["max_tokens"] == 2048
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["messages"][1] == {"role": "user", "content": "usr"}


def test_a_non_reasoning_model_never_gets_the_parameter():
    """gpt-4o-mini / llama / gemini share this class. Only send what applies."""
    p = _provider()
    payload = p._completion_payload(
        "sys", "usr", eff_model="gpt-4o-mini", max_tokens=512,
        disable_thinking=True,
    )
    assert "thinking" not in payload, (
        "a non-reasoning model was sent a reasoning-control parameter"
    )


# ── the escalation must actually use it ──────────────────────────────────────

def test_the_retry_disables_thinking_and_the_first_attempt_does_not():
    """The capability test: drive the real two-attempt loop."""
    p = _provider()
    seen: list[bool] = []

    async def _fake_one(system_prompt, user_message, *, eff_model,
                        max_tokens, timeout, disable_thinking=False):
        seen.append(disable_thinking)
        if len(seen) == 1:
            raise ProviderError("deepseek", "reasoning consumed the budget",
                                kind=KIND_REASONING_TRUNCATED)
        from aria_service.llm.provider import LLMResult
        return LLMResult(text="the answer", model=eff_model,
                         routed_via="deepseek", input_tokens=1, output_tokens=1)

    p._one_completion = _fake_one          # type: ignore[assignment]
    out = asyncio.run(p.complete("sys", "usr", max_tokens=1024, timeout=120.0))

    assert out.text == "the answer"
    assert seen == [False, True], (
        f"expected attempt 0 to reason and attempt 1 to disable thinking, got {seen}"
    )


def test_a_non_truncation_error_still_raises_without_a_retry():
    """The escalation is for ONE failure only; do not widen it."""
    p = _provider()
    calls = []

    async def _fake_one(system_prompt, user_message, *, eff_model,
                        max_tokens, timeout, disable_thinking=False):
        calls.append(disable_thinking)
        raise ProviderError("deepseek", "boom", kind="other")

    p._one_completion = _fake_one          # type: ignore[assignment]
    with pytest.raises(ProviderError):
        asyncio.run(p.complete("sys", "usr", max_tokens=1024, timeout=120.0))
    assert calls == [False], "a non-curable error must not be retried"


def test_a_successful_first_attempt_never_retries():
    p = _provider()
    calls = []

    async def _fake_one(system_prompt, user_message, *, eff_model,
                        max_tokens, timeout, disable_thinking=False):
        calls.append(disable_thinking)
        from aria_service.llm.provider import LLMResult
        return LLMResult(text="fine", model=eff_model, routed_via="deepseek",
                         input_tokens=1, output_tokens=1)

    p._one_completion = _fake_one          # type: ignore[assignment]
    asyncio.run(p.complete("sys", "usr", max_tokens=1024, timeout=120.0))
    assert calls == [False]


def test_the_clock_guard_still_refuses_a_retry_it_cannot_finish():
    """R-F3629's contract survives: `timeout` is a promise to the caller."""
    p = _provider()
    calls = []

    async def _fake_one(system_prompt, user_message, *, eff_model,
                        max_tokens, timeout, disable_thinking=False):
        calls.append(disable_thinking)
        raise ProviderError("deepseek", "reasoning consumed the budget",
                            kind=KIND_REASONING_TRUNCATED)

    p._one_completion = _fake_one          # type: ignore[assignment]
    with pytest.raises(ProviderError):
        # A timeout below _MIN_RETRY_SECONDS leaves nothing to retry into.
        asyncio.run(p.complete("sys", "usr", max_tokens=1024, timeout=1.0))
    assert calls == [False], "a retry was started with no clock left to finish it"


# ── the budget escalation is retained, not replaced ──────────────────────────

def test_the_doubled_budget_is_kept_as_answer_room():
    """With thinking off the extra headroom is pure ANSWER space, which is
    exactly what the failure lacked."""
    p = _provider()
    b0 = p._resolve_completion_budget("deepseek-v4-flash", 1024, attempt=0)
    b1 = p._resolve_completion_budget("deepseek-v4-flash", 1024, attempt=1)
    assert b1 > b0
