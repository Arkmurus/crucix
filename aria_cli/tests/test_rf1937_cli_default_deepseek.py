"""R-F1937 — the aria_cli coder must default to DIRECT deepseek, not the slow
brain chat path.

The old default provider `aria` builds base_url {ARIA_SERVICE_URL}/api/aria and
chat() appends /chat/completions -> the heavy full-chat brain pipeline, which
times out (~122s measured). Direct deepseek answers in ~1.7s. So when a
DEEPSEEK_API_KEY is present and no provider is explicitly set, the CLI defaults
to deepseek. `aria` remains an explicit opt-in.
"""
from __future__ import annotations

import pytest

from aria_cli.llm import LLMConfig

_PROVIDER_ENVS = (
    "ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER", "DEEPSEEK_API_KEY",
    "ARIA_CODER_LLM_API_KEY", "LLM_API_KEY", "ARIA_CODER_LLM_BASE_URL",
    "OPENAI_BASE_URL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for v in _PROVIDER_ENVS:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def test_defaults_to_deepseek_when_key_present(clean_env):
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "deepseek"
    assert "deepseek.com" in cfg.base_url           # direct, not /api/aria
    assert "/api/aria" not in cfg.base_url
    assert cfg.api_key == "sk-test-deepseek"


def test_falls_back_to_aria_without_deepseek_key(clean_env):
    # no DEEPSEEK_API_KEY, no explicit provider -> aria (unchanged prior behaviour)
    cfg = LLMConfig.from_env()
    assert cfg.provider == "aria"
    assert "/api/aria" in cfg.base_url


def test_explicit_aria_opt_in_wins_over_deepseek_key(clean_env):
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    clean_env.setenv("LLM_PROVIDER", "aria")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "aria"                   # explicit opt-in respected
    assert "/api/aria" in cfg.base_url


def test_explicit_deepseek_override(clean_env):
    clean_env.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "deepseek"
    assert "deepseek.com" in cfg.base_url
