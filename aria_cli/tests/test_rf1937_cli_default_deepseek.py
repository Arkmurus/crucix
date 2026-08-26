"""R-F1937 — the aria_cli coder must never default to the slow brain chat path.

R-F4370 (C-315) REVERSED HALF OF THIS FILE, and the half it kept is the half
that was actually load-bearing.

R-F1937 measured that the old default provider ``aria`` builds base_url
``{ARIA_SERVICE_URL}/api/aria`` and ``chat()`` appends ``/chat/completions`` —
the heavy full-chat brain pipeline, which times out (~122s measured) and cannot
do tool-calling at all (R-F2166). **That finding stands and is still pinned
below.** Its REMEDY — default to DeepSeek whenever a DEEPSEEK_API_KEY exists —
was the only tool-capable, fast option at the time.

It is not any more, and the operator has withdrawn it (2026-08-26: "remove
deepseek from cli, aria must use her own reasoning now"). ``aria-llm`` is a
DIFFERENT endpoint from ``aria``: the sovereign vLLM, answering in ~2-8s with
native tool-calling. So the default moves there, and the vendor is gone.

Do NOT "restore" a DeepSeek default to fix a failure here — see
``test_rf4370_deepseek_removed.py``, which pins the removal and the four routes
by which the vendor could otherwise still be reached.
"""
from __future__ import annotations

import pytest

from aria_cli.llm import LLMConfig, LLMError

_PROVIDER_ENVS = (
    "ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER", "DEEPSEEK_API_KEY",
    "ARIA_CODER_LLM_API_KEY", "LLM_API_KEY", "ARIA_CODER_LLM_BASE_URL",
    "OPENAI_BASE_URL", "ARIA_LLM_URL", "ARIA_LLM_MODEL", "ARIA_LLM_KEY",
)


@pytest.fixture
def clean_env(monkeypatch):
    for v in _PROVIDER_ENVS:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def _sovereign(env):
    env.setenv("ARIA_LLM_URL", "https://pod.example/v1")
    env.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")


# -- the surviving finding: never default to the heavy brain path -------------

def test_the_default_is_never_the_slow_brain_chat_path(clean_env):
    """R-F1937'S ACTUAL FINDING, unchanged. Whatever the default is, it must
    not be the ~122s tool-less /api/aria pipeline."""
    _sovereign(clean_env)
    cfg = LLMConfig.from_env()

    assert "/api/aria" not in cfg.base_url
    assert cfg.provider != "aria"


def test_explicit_aria_opt_in_is_still_respected(clean_env):
    """`aria` stays reachable ON PURPOSE — it is only wrong as a DEFAULT."""
    _sovereign(clean_env)
    clean_env.setenv("LLM_PROVIDER", "aria")
    clean_env.setenv("ARIA_INTERNAL_TOKEN", "tok")
    cfg = LLMConfig.from_env()

    assert cfg.provider == "aria"
    assert "/api/aria" in cfg.base_url


# -- the new default: ARIA's own model (R-F4370) ------------------------------

def test_the_default_is_arias_own_sovereign_model(clean_env):
    _sovereign(clean_env)
    cfg = LLMConfig.from_env()

    assert cfg.provider == "aria-llm"
    assert cfg.base_url == "https://pod.example/v1"


def test_a_deepseek_key_in_the_environment_no_longer_selects_anything(clean_env):
    """THE REVERSAL. Under R-F1937 the mere PRESENCE of this key was the
    selection; the operator's .env has one, which is why the coder was running
    on a vendor rather than on ARIA."""
    _sovereign(clean_env)
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    cfg = LLMConfig.from_env()

    assert cfg.provider == "aria-llm"
    assert "deepseek" not in cfg.base_url


def test_an_unconfigured_coder_refuses_instead_of_choosing_for_you(clean_env):
    """With no sovereign endpoint there is nothing correct to pick. Refuse —
    silently answering from some other model is the failure R-F4303 named."""
    with pytest.raises(LLMError):
        LLMConfig.from_env()
