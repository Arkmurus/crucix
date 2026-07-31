"""R-F3591 — ARIA sent her raw chain of thought to a user, and never answered.

Live 2026-07-31 21:55. Operator: "What is the time in Portugal?"
ARIA replied with her internal deliberation verbatim — "The user asks... I need
to answer from the snippets only. Let me look at what the snippets actually
contain... However, I also have CURRENT CONTEXT... But wait — can I use that?" —
truncated mid-word at "I can mark the timezone fact as CONFIRMED (W".

TWO DEFECTS, and they compounded.

1. THE LEAK. R-F3033 correctly found that an empty `content` on an HTTP 200 was
   being booked as a silent success, and fixed it by SERVING `reasoning_content`
   instead. That trades a silent empty answer for a leaked one. When the token
   budget is consumed by reasoning, the chain of thought BECOMES the answer:
   prompt internals (ANSWER SCOPE, snippet numbering, the grounding rules) go
   straight to the user, and the question goes unanswered.

2. THE CONTRADICTION, introduced by R-F3588. The tool-grounded ANSWER SCOPE says
   "Build your answer SOLELY from [Current message] and the tool output". The
   clock lives in the SYSTEM prompt, so that scope excluded it — and she was
   right to hesitate. The deadlock is visible in the leaked text.

The scope exists to stop conversation-history bleed and to stop training
knowledge being asserted as source-backed. Neither applies to a value this server
computed for this request.
"""

from __future__ import annotations

import pathlib

import pytest

from aria_service.llm.openai_compat import ProviderError


_ROOT = pathlib.Path(__file__).resolve().parents[2]
_COMPAT = _ROOT / "aria_service" / "llm" / "openai_compat.py"
_ENGINE = _ROOT / "aria_service" / "aria_engine.py"
_ROUTES = _ROOT / "aria_service" / "routes" / "aria.py"


def _joined(src: str) -> str:
    """Join adjacent Python string literals before matching.

    A prompt built from implicitly-concatenated literals holds
    `"... is NOT outside "` on one line and `"knowledge: ..."` on the next, so
    the phrase exists in the RENDERED prompt but not in the raw source.
    Asserting on raw source therefore tests the code's FORMATTING, not its
    content — the same wording-vs-property trap this session kept hitting.
    Normalise first.
    """
    import re as _re
    return _re.sub(r'"\s*\n\s*"', "", src)


def test_reasoning_content_is_never_returned_as_the_answer():
    src = _COMPAT.read_text(encoding="utf-8")
    code = "\n".join(
        l for l in src.splitlines()
        if not l.strip().startswith("#")
    )
    assert '_text = (_msg.get("reasoning_content") or "").strip()' not in code, (
        "reasoning_content is being assigned to the answer again — that is the "
        "leak: the model's deliberation is published as prose."
    )
    assert "_reasoning = " in code, "the reasoning must still be READ, for diagnosis"


def test_an_answerless_reasoning_response_fails_over_instead_of_leaking():
    """R-F3033's real finding stands: empty content must not book a success. What
    changes is HOW it fails — retryable, so the chain tries again or falls to the
    next provider, rather than emitting the chain of thought."""
    src = _COMPAT.read_text(encoding="utf-8")
    # Anchor on the LOGIC, not the first "R-F3591" in the file (which is now
    # the threshold constant near the top) — slicing from a marker that moved
    # is how a guard silently starts inspecting the wrong region.
    block = src[src.index("spent its budget reasoning") - 2000:
                src.index("spent its budget reasoning") + 1800]
    assert "raise ProviderError" in block
    assert "retryable=True" in block, (
        "a budget-exhausted reasoning turn is transient — it must be retryable, "
        "or one tight-budget call permanently kills the provider"
    )


def test_the_reasoning_is_logged_for_diagnosis_but_truncated():
    """It is still needed to debug 'why did the model produce no answer', but a
    full chain of thought in the logs is both noise and a privacy risk."""
    src = _COMPAT.read_text(encoding="utf-8")
    # Anchor on the LOGIC, not the first "R-F3591" in the file (which is now
    # the threshold constant near the top) — slicing from a marker that moved
    # is how a guard silently starts inspecting the wrong region.
    block = src[src.index("spent its budget reasoning") - 2000:
                src.index("spent its budget reasoning") + 1800]
    assert "logger.warning" in block
    assert "%.300s" in block, "the logged reasoning must be length-bounded"


def test_the_error_names_the_actual_cause():
    """'empty response' sent someone hunting a network fault last time. The
    message must say the budget went on reasoning."""
    src = _COMPAT.read_text(encoding="utf-8")
    assert "reasoning consumed the token budget" in src
    assert "Raise max_tokens" in src, "the message must state the fix"


def test_provider_error_is_importable_and_retryable_is_a_real_field():
    """Verify the instrument: the guard above asserts on source text, so confirm
    the API it assumes actually exists."""
    err = ProviderError("test", "boom", kind="other", retryable=True)
    assert getattr(err, "retryable", None) is True


# ── The contradiction R-F3588 introduced ────────────────────────────────────


def test_the_tool_answer_scope_exempts_the_ambient_context():
    src = _joined(_ENGINE.read_text(encoding="utf-8"))
    scope_idx = src.index("[ANSWER SCOPE — BINDING]")
    block = src[scope_idx:scope_idx + 2400]
    assert "CURRENT CONTEXT" in block, (
        "the tool scope still excludes the clock — asked the time with a tool "
        "running, ARIA deadlocks between 'SOLELY from the tool output' and "
        "'you DO have a clock'"
    )
    assert "NOT outside knowledge" in block


def test_the_scope_still_blocks_what_it_was_built_to_block():
    """The carve-out must not become a hole. History bleed and single-subject
    focus are the reasons this scope exists (a deltaguard DD answered a prior
    gap-analysis, 2026-06-15)."""
    src = _joined(_ENGINE.read_text(encoding="utf-8"))
    scope_idx = src.index("[ANSWER SCOPE — BINDING]")
    block = src[scope_idx:scope_idx + 2400]
    assert "NO conversation history" in block
    assert "must be about THAT subject only" in block
    assert "SOLELY from [Current message]" in block


def test_the_grounding_repair_note_exempts_it_too():
    """The repair pass says 'Do not add outside knowledge' — which would push her
    straight back into refusing her own clock on any re-answer."""
    src = _joined(_ROUTES.read_text(encoding="utf-8"))
    idx = src.index("_GROUNDING_REPAIR_NOTE")
    block = src[idx:idx + 1400]
    assert "CURRENT CONTEXT" in block
    assert "NOT outside knowledge" in block
    # …while still forbidding the thing it exists to forbid
    assert "not supported by the provided sources" in block


# ── Behavioural: drive the parser, do not merely read the source ─────────────


@pytest.mark.asyncio
async def test_truncated_deliberation_raises_instead_of_being_served(monkeypatch):
    """THE LIVE FAILURE, reproduced. finish_reason='length' means the reasoning
    was cut off mid-thought — exactly the "…marked CONFIRMED (W" the operator
    received. It must fail over, never be published."""
    import aria_service.llm.openai_compat as oc
    from aria_service.tests.test_rf3032_3036_llm_chain_resilience import _fake_post

    deliberation = (
        "The user asks: \"What is the time in Portugal?\" I need to answer from "
        "the snippets only. Let me look at what the snippets actually contain. "
        "However, I also have CURRENT CONTEXT. But wait — can I use that? I can "
        "mark the timezone fact as CONFIRMED (W"
    )
    monkeypatch.setattr(oc.httpx, "AsyncClient", _fake_post({
        "choices": [{"message": {"content": "", "reasoning_content": deliberation},
                     "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 900},
        "model": "deepseek-v4-flash",
    }))
    p = oc.OpenAICompatProvider(name="deepseek", api_key="k",
                                model="deepseek-v4-flash",
                                base_url="https://api.deepseek.com")
    with pytest.raises(ProviderError) as exc:
        await p.complete("sys", "user")
    assert "reasoning consumed the token budget" in str(exc.value)
    assert exc.value.retryable is True
    # and the deliberation itself is NOT in the surfaced text
    assert "But wait" not in str(exc.value)


@pytest.mark.asyncio
async def test_a_long_untruncated_ramble_is_also_refused(monkeypatch):
    """finish_reason='stop' but 2K of monologue is still deliberation, not an
    answer. Length alone must catch it."""
    import aria_service.llm.openai_compat as oc
    from aria_service.tests.test_rf3032_3036_llm_chain_resilience import _fake_post

    monkeypatch.setattr(oc.httpx, "AsyncClient", _fake_post({
        "choices": [{"message": {"content": "", "reasoning_content": "Let me think. " * 200},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 900},
        "model": "deepseek-v4-flash",
    }))
    p = oc.OpenAICompatProvider(name="deepseek", api_key="k",
                                model="deepseek-v4-flash",
                                base_url="https://api.deepseek.com")
    with pytest.raises(ProviderError):
        await p.complete("sys", "user")
