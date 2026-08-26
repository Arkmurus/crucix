"""R-F1280 — Capability test: api-key resolution is provider-aware.

Regression: ``LLMConfig.from_env`` resolved the api key as
``ARIA_CODER_LLM_API_KEY or LLM_API_KEY or ARIA_INTERNAL_TOKEN or
DEEPSEEK_API_KEY or ...``. With provider=deepseek and BOTH ARIA_INTERNAL_TOKEN
and DEEPSEEK_API_KEY set (the operator's real .env), the internal token won —
so the CLI sent ARIA's internal token to DeepSeek's API and every call 401'd
("api key ...9c2a is invalid"). ARIA "did not respond".

The fix: pick the credential that belongs to the selected provider.
ARIA_INTERNAL_TOKEN is only correct for the in-house ``aria`` provider.

R-F4370 (C-315) changed the VEHICLE, not the contract. Two cases below used
``deepseek`` as the example external provider; DeepSeek has since been removed
from the CLI entirely (operator directive: "aria must use her own reasoning
now"), so selecting it now raises. They are re-expressed against ``mistral`` —
another external provider with its own key var — because what R-F1280 protects
is "the RIGHT key goes to the SELECTED provider", which is provider-agnostic
and still exactly as load-bearing.
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


def test_external_provider_uses_its_own_key_not_the_internal_token(monkeypatch):
    """The exact failing SHAPE: an external provider with both keys present.
    (Was provider=deepseek; see the module docstring on why the vehicle moved.)"""
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-token-ENDS9c2a")
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral-ENDS4559")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "mistral"
    assert cfg.api_key == "sk-mistral-ENDS4559", (
        "an external provider must use its OWN key, not ARIA_INTERNAL_TOKEN"
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
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral-ENDS4559")
    monkeypatch.setenv("ARIA_CODER_LLM_API_KEY", "explicit-override")
    cfg = LLMConfig.from_env()
    assert cfg.api_key == "explicit-override"


def test_groq_provider_uses_groq_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-groq-key")
    cfg = LLMConfig.from_env()
    assert cfg.api_key == "gsk-groq-key"
