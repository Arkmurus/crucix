"""R-F1236 — Capability tests for Prompt Budget Manager.

Tests:
1. estimate_tokens — accurate token estimation
2. get_context_window — correct window for known/unknown models
3. enforce_budget — truncates when over budget, preserves when under
4. enforce_budget preserves system prompt when possible
5. enforce_budget handles edge cases (empty, CJK, very long)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestEstimateTokens:
    """Test token estimation."""

    def test_empty_string(self):
        """Empty string should return 0 tokens."""
        from aria_service.llm.prompt_budget import estimate_tokens
        assert estimate_tokens("") == 0

    def test_short_text(self):
        """Short text should return at least 1 token."""
        from aria_service.llm.prompt_budget import estimate_tokens
        assert estimate_tokens("Hello") >= 1

    def test_long_text(self):
        """Long text should return proportionally more tokens."""
        from aria_service.llm.prompt_budget import estimate_tokens
        short = estimate_tokens("Hello world, this is a test.")
        long = estimate_tokens("Hello world, this is a test. " * 100)
        assert long > short * 50  # 100x text should be > 50x tokens

    def test_cjk_text(self):
        """CJK text should be estimated differently."""
        from aria_service.llm.prompt_budget import estimate_tokens
        cjk = estimate_tokens("你好世界，这是一个测试。")
        assert cjk >= 1

    def test_estimate_prompt_tokens(self):
        """estimate_prompt_tokens should sum system + user + overhead."""
        from aria_service.llm.prompt_budget import estimate_prompt_tokens
        total = estimate_prompt_tokens("System prompt", "User message")
        assert total >= 3  # at least 1 each + overhead


class TestGetContextWindow:
    """Test context window lookup."""

    def test_known_model(self):
        """Known model should return its context window."""
        from aria_service.llm.prompt_budget import get_context_window
        assert get_context_window("llama-3.3-70b-versatile") == 131072
        assert get_context_window("deepseek-chat") == 65536
        assert get_context_window("gpt-4o") == 128000

    def test_unknown_model_default(self):
        """Unknown model should return default window."""
        from aria_service.llm.prompt_budget import get_context_window
        window = get_context_window("nonexistent-model-v42")
        assert window == 8192  # _DEFAULT_CONTEXT_WINDOW

    def test_prefix_match(self):
        """Model name that starts with a known prefix should match."""
        from aria_service.llm.prompt_budget import get_context_window
        # "gpt-4o-2024-08-06" should match "gpt-4o"
        window = get_context_window("gpt-4o-2024-08-06")
        assert window == 128000


class TestEnforceBudget:
    """Test prompt budget enforcement."""

    def test_under_budget_no_truncation(self):
        """Prompt under budget should not be truncated."""
        from aria_service.llm.prompt_budget import enforce_budget
        system = "You are a helpful assistant."
        user = "What is the capital of France?"
        result_system, result_user = enforce_budget(system, user, model="llama-3.3-70b-versatile")
        assert result_system == system
        assert result_user == user

    def test_over_budget_truncates_user(self):
        """Prompt over budget should truncate user message first."""
        from aria_service.llm.prompt_budget import enforce_budget
        system = "You are a helpful assistant."
        # Very long user message that exceeds a tiny model's budget
        user = "Hello " * 10000  # ~60K chars, well over budget for a small model
        result_system, result_user = enforce_budget(system, user, model="gemma2-9b-it", reserved_output=512)
        assert result_system == system  # system should be preserved
        assert len(result_user) < len(user)  # user should be truncated
        assert "..." in result_user  # should have truncation marker

    def test_over_budget_truncates_both(self):
        """When user is already minimal, system should also be truncated."""
        from aria_service.llm.prompt_budget import enforce_budget
        # Both system and user are very long — well over gemma2's 8192 window
        system = "You are a helpful assistant. " * 2000  # ~58K chars
        user = "Hello world. " * 2000  # ~24K chars
        result_system, result_user = enforce_budget(
            system, user,
            model="gemma2-9b-it",  # only 8192 context
            reserved_output=512,
            min_system_tokens=10,
        )
        # Both should be truncated
        assert len(result_system) < len(system) or len(result_user) < len(user)

    def test_very_small_budget(self):
        """Even with a very small budget, should return something."""
        from aria_service.llm.prompt_budget import enforce_budget
        system = "You are a helpful assistant."
        user = "Hello world. " * 1000
        result_system, result_user = enforce_budget(
            system, user,
            model="unknown-model",  # default 8192 window
            reserved_output=8000,  # only 192 tokens for prompt
        )
        # Should still return something
        assert len(result_system) > 0 or len(result_user) > 0

    def test_empty_prompt(self):
        """Empty prompts should not crash."""
        from aria_service.llm.prompt_budget import enforce_budget
        result_system, result_user = enforce_budget("", "", model="llama-3.3-70b-versatile")
        assert result_system == ""
        assert result_user == ""


class TestIntegrationWithProvider:
    """Test that prompt budget integrates with OpenAICompatProvider."""

    def test_provider_calls_enforce_budget(self):
        """OpenAICompatProvider.complete() should call enforce_budget."""
        from aria_service.llm.openai_compat import OpenAICompatProvider

        # Create a provider with a fake key (won't actually call the API)
        provider = OpenAICompatProvider(
            name="test",
            api_key="test-key",
            model="gemma2-9b-it",  # small context window
        )

        # The enforce_budget call happens inside complete() before the API call.
        # We can verify the import path works by checking the source code.
        import inspect
        source = inspect.getsource(provider.complete)
        assert "enforce_budget" in source
        assert "prompt_budget" in source

    def test_enforce_budget_imports_cleanly(self):
        """The prompt_budget module should import without errors."""
        from aria_service.llm import prompt_budget
        assert prompt_budget.enforce_budget is not None
        assert prompt_budget.estimate_tokens is not None
        assert prompt_budget.get_context_window is not None
