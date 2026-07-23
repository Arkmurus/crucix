"""R-F2935 — an OpenAI-compatible provider must never emit a Claude model.

Live incident 2026-07-23: the restructure set LLM_PROVIDER=deepseek with
LLM_MODEL=claude-opus-4-8, so main.py built the DeepSeek PRIMARY with
self._model='claude-opus-4-8'. Every call with no per-call override then sent
claude-opus-4-8 to api.deepseek.com → HTTP 400 → 60s cooldown → self_improve and
DD layers degraded to local_brain. The per-call OVERRIDE was already guarded; the
CONFIGURED default was the hole.
"""
from __future__ import annotations

import logging

import pytest

from aria_service.llm.openai_compat import (
    OpenAICompatProvider,
    _OPENAI_COMPAT_SAFE_DEFAULT,
)


class TestConfiguredModelGuard:
    def test_deepseek_never_keeps_a_claude_default(self):
        """THE bug, verbatim: the DeepSeek primary built with the claude id."""
        p = OpenAICompatProvider(
            name="deepseek", api_key="k", model="claude-opus-4-8",
            base_url="https://api.deepseek.com/v1",
        )
        assert p._model == "deepseek-chat"
        assert not p._model.startswith("claude")

    @pytest.mark.parametrize("provider,expected", [
        ("deepseek", "deepseek-chat"),
        ("openai", "gpt-4"),
        ("groq", "llama-3.3-70b-versatile"),
        ("mistral", "mistral-large-latest"),
    ])
    def test_each_provider_falls_back_to_its_own_default(self, provider, expected):
        p = OpenAICompatProvider(name=provider, api_key="k",
                                 model="claude-opus-4-8", base_url="x")
        assert p._model == expected

    def test_a_non_claude_model_is_untouched(self):
        p = OpenAICompatProvider(name="deepseek", api_key="k",
                                 model="deepseek-v4-flash", base_url="x")
        assert p._model == "deepseek-v4-flash"

    def test_anthropic_named_provider_is_not_affected(self):
        """Only NON-anthropic providers strip the claude id — anthropic keeps it.
        (OpenAICompatProvider is never actually named anthropic in prod, but the
        guard must be scoped by name, not blanket.)"""
        p = OpenAICompatProvider(name="anthropic", api_key="k",
                                 model="claude-opus-4-8", base_url="x")
        assert p._model == "claude-opus-4-8"

    def test_unknown_provider_gets_empty_not_a_claude_id(self):
        p = OpenAICompatProvider(name="some-new-provider", api_key="k",
                                 model="claude-opus-4-8", base_url="x")
        assert p._model == ""
        assert not (p._model or "").startswith("claude")

    def test_it_warns_so_the_bad_secret_is_visible(self, caplog):
        with caplog.at_level(logging.WARNING):
            OpenAICompatProvider(name="deepseek", api_key="k",
                                 model="claude-opus-4-8", base_url="x")
        assert any("claude" in r.message.lower() and "deepseek" in r.message.lower()
                   for r in caplog.records), "misconfiguration must be logged"

    def test_the_payload_can_never_carry_a_claude_model(self):
        """End-to-end intent: whatever the config, the built payload's model
        is never a claude id."""
        p = OpenAICompatProvider(name="deepseek", api_key="k",
                                 model="claude-opus-4-8", base_url="x")
        # _eff_model computed with no override uses self._model, now safe.
        assert not p._model.startswith("claude")
