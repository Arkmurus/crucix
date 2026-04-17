"""Tests for the resilient writer LLM fallback.

Covers:
  1. Happy path — Claude succeeds, no degradation flag.
  2. Fallback path — Claude raises, DeepSeek succeeds, degraded=True.
  3. Hard-fail path — both fail, RuntimeError raised.
  4. allow_fallback=False preserves the old strict behaviour.
  5. DEEPSEEK_API_KEY missing — clear error message.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic — .messages.create() only."""
    def __init__(self, behaviour="ok", text="{\"ok\": true}"):
        self.behaviour = behaviour
        self.text = text
        self.calls = 0

        # Tests dispatch through self.messages.create
        outer = self
        class _Messages:
            def create(self, **kwargs):
                outer.calls += 1
                if outer.behaviour == "raise_billing":
                    raise RuntimeError("Your credit balance is too low")
                if outer.behaviour == "raise_auth":
                    raise RuntimeError("401 authentication_error")
                # Happy path — shape mirrors the real client's return value
                return SimpleNamespace(
                    content=[SimpleNamespace(text=outer.text)]
                )
        self.messages = _Messages()


# ═══════════════════════════════════════════════════════════════════════
# 1. Happy path
# ═══════════════════════════════════════════════════════════════════════

def test_happy_path_returns_non_degraded():
    from aria_service.writers._resilient_llm import resilient_complete
    client = _FakeAnthropic(behaviour="ok", text="structured output")
    rr = resilient_complete(
        anthropic_client=client,
        anthropic_model="claude-opus-4-7",
        system_prompt="system",
        user_prompt="user",
        max_tokens=1000,
    )
    assert rr.degraded is False
    assert rr.model_used == "claude-opus-4-7"
    assert rr.text == "structured output"
    assert rr.reason == ""
    assert client.calls == 1


# ═══════════════════════════════════════════════════════════════════════
# 2. Fallback path
# ═══════════════════════════════════════════════════════════════════════

def test_fallback_path_sets_degraded_and_calls_deepseek(monkeypatch):
    from aria_service.writers import _resilient_llm

    # Simulate Anthropic billing failure
    client = _FakeAnthropic(behaviour="raise_billing")

    # Stub _call_deepseek to return a known result without network
    def _fake_deepseek(system_prompt, user_prompt, max_tokens, timeout):
        return "fallback output", "deepseek-chat"

    monkeypatch.setattr(_resilient_llm, "_call_deepseek", _fake_deepseek)

    rr = _resilient_llm.resilient_complete(
        anthropic_client=client,
        anthropic_model="claude-opus-4-7",
        system_prompt="sys",
        user_prompt="usr",
        max_tokens=1000,
    )
    assert rr.degraded is True
    assert rr.model_used == "deepseek-chat"
    assert rr.text == "fallback output"
    assert "Anthropic unavailable" in rr.reason
    assert "credit balance" in rr.primary_error.lower() or "low" in rr.primary_error.lower()


# ═══════════════════════════════════════════════════════════════════════
# 3. Hard-fail path
# ═══════════════════════════════════════════════════════════════════════

def test_both_providers_fail_raises_runtime_error(monkeypatch):
    from aria_service.writers import _resilient_llm
    client = _FakeAnthropic(behaviour="raise_billing")

    def _boom(system_prompt, user_prompt, max_tokens, timeout):
        raise RuntimeError("deepseek 500 upstream error")

    monkeypatch.setattr(_resilient_llm, "_call_deepseek", _boom)

    with pytest.raises(RuntimeError) as excinfo:
        _resilient_llm.resilient_complete(
            anthropic_client=client,
            anthropic_model="claude-opus-4-7",
            system_prompt="sys",
            user_prompt="usr",
            max_tokens=1000,
        )
    msg = str(excinfo.value)
    # Error message MUST name both providers so the operator can see
    # exactly what went wrong on each side.
    assert "Claude" in msg
    assert "DeepSeek" in msg


# ═══════════════════════════════════════════════════════════════════════
# 4. allow_fallback=False preserves the old strict behaviour
# ═══════════════════════════════════════════════════════════════════════

def test_allow_fallback_false_raises_original_error():
    from aria_service.writers._resilient_llm import resilient_complete
    client = _FakeAnthropic(behaviour="raise_auth")
    with pytest.raises(RuntimeError) as excinfo:
        resilient_complete(
            anthropic_client=client,
            anthropic_model="claude-opus-4-7",
            system_prompt="sys",
            user_prompt="usr",
            max_tokens=1000,
            allow_fallback=False,
        )
    assert "401" in str(excinfo.value) or "auth" in str(excinfo.value).lower()


# ═══════════════════════════════════════════════════════════════════════
# 5. Missing DEEPSEEK_API_KEY gives a clear error
# ═══════════════════════════════════════════════════════════════════════

def test_missing_deepseek_key_clear_error(monkeypatch):
    from aria_service.writers import _resilient_llm
    client = _FakeAnthropic(behaviour="raise_billing")

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        _resilient_llm.resilient_complete(
            anthropic_client=client,
            anthropic_model="claude-opus-4-7",
            system_prompt="sys",
            user_prompt="usr",
            max_tokens=1000,
        )
    # The inner RuntimeError ("DEEPSEEK_API_KEY is not set ...") gets
    # wrapped into the "Both writer LLM providers failed" outer error.
    assert "DeepSeek" in str(excinfo.value)


# ═══════════════════════════════════════════════════════════════════════
# 6. WriterResult carries the flags through the orchestrator
# ═══════════════════════════════════════════════════════════════════════

def test_writer_result_dataclass_has_degradation_fields():
    from aria_service.writers.writer_orchestrator import WriterResult
    r = WriterResult(
        writer_type="assessment",
        reference="TEST-001",
        document="doc",
        structured=None,
        output_hash="abc",
        produced_at="2026-04-17T21:00:00Z",
        word_count=1,
        success=True,
    )
    # Defaults — not degraded unless explicitly set
    assert r.degraded is False
    assert r.actual_model == ""
    assert r.degraded_reason == ""
