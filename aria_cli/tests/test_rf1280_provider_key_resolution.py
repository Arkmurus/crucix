"""R-F1280 — Capability test: api-key resolution is provider-aware.

Regression: ``LLMConfig.from_env`` resolved the api key as
``ARIA_CODER_LLM_API_KEY or LLM_API_KEY or ARIA_INTERNAL_TOKEN or
DEEPSEEK_API_KEY or ...``. With provider=deepseek and BOTH ARIA_INTERNAL_TOKEN
and DEEPSEEK_API_KEY set (the operator's real .env), the internal token won —
so the CLI sent ARIA's internal token to DeepSeek's API and every call 401'd
("api key ...9c2a is invalid"). ARIA "did not respond".

The fix: pick the credential that belongs to the selected provider.
ARIA_INTERNAL_TOKEN is only correct for the in-house ``aria`` provider.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_cli.llm import LLMConfig

# Every provider/credential env var this test touches — cleared before each case
# so the operator's real environment can't leak in and mask the assertion.
_VARS = [
    "ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER",
    "ARIA_CODER_LLM_API_KEY", "LLM_API_KEY",
    "ARIA_INTERNAL_TOKEN", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
    "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
    "ARIA_SERVICE_URL", "ARIA_CODER_LLM_MODEL", "LLM_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_deepseek_provider_uses_deepseek_key_not_internal_token(monkeypatch):
    """The exact failing combo: provider=deepseek with both keys present."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-token-ENDS9c2a")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-ENDS4559")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "deepseek"
    assert cfg.api_key == "sk-deepseek-ENDS4559", (
        "deepseek must use DEEPSEEK_API_KEY, not ARIA_INTERNAL_TOKEN"
    )


def test_aria_provider_uses_internal_token(monkeypatch):
    """The aria provider is the ONLY one that should use the internal token."""
    monkeypatch.setenv("LLM_PROVIDER", "aria")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-token-ENDS9c2a")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-ENDS4559")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "aria"
    assert cfg.api_key == "internal-token-ENDS9c2a"


def test_explicit_override_wins(monkeypatch):
    """An explicit ARIA_CODER_LLM_API_KEY beats the provider default."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-ENDS4559")
    monkeypatch.setenv("ARIA_CODER_LLM_API_KEY", "explicit-override")
    cfg = LLMConfig.from_env()
    assert cfg.api_key == "explicit-override"


def test_groq_provider_uses_groq_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-groq-key")
    cfg = LLMConfig.from_env()
    assert cfg.api_key == "gsk-groq-key"
