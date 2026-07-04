"""R-F1949 — ARIA_LLM_SHADOW places ARIA-LLM BELOW the primary (safe canary).

R-F2410 UPDATE: the DEFAULT when ARIA_LLM_URL is set is now TWO-TRACK — the
sovereign is NOT the global chain primary; it serves grounded synthesis only via
model_router, and DeepSeek stays the chain primary for coverage/fallback. The
legacy R-F93 "sovereign primary for ALL turns" is preserved behind
ARIA_LLM_PRIMARY_ALL=1."""
from __future__ import annotations

import pytest

from aria_service.llm import fallback


class _Stub:
    def __init__(self, name):
        self.name = name
        self.is_configured = True


_CLEAR = ("ARIA_LLM_URL", "ARIA_LLM_SHADOW", "ARIA_LLM_MODEL", "ARIA_LLM_KEY",
          "GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")


_EXTRA = ("ARIA_LLM_PRIMARY_ALL",)


def _names(monkeypatch, shadow: bool = False, primary_all: bool = False):
    for v in _CLEAR + _EXTRA:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ARIA_LLM_URL", "http://aria-llm/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-test")
    # a second real provider so the chain wraps into a FallbackChain (else a
    # single provider is returned bare) — makes ordering observable.
    monkeypatch.setenv("GROQ_API_KEY", "gkey")
    if shadow:
        monkeypatch.setenv("ARIA_LLM_SHADOW", "1")
    if primary_all:
        monkeypatch.setenv("ARIA_LLM_PRIMARY_ALL", "1")
    monkeypatch.setattr(fallback, "create_llm_provider",
                        lambda ptype, key, model="", base_url="": _Stub(model or ptype))
    chain = fallback.create_fallback_chain("deepseek", "dskey", "ds-model", "")
    provs = getattr(chain, "providers", None) or getattr(chain, "_providers", None)
    if provs is None:  # single provider returned bare
        return [getattr(chain, "name", "?")]
    return [getattr(p, "name", "?") for p in provs]


def test_default_is_two_track_deepseek_primary(monkeypatch):
    # R-F2410 — default when URL set: sovereign NOT in the global chain (two-track);
    # DeepSeek stays primary. Sovereign serves grounded synthesis via model_router.
    names = _names(monkeypatch, shadow=False)
    assert names, "no providers built"
    assert names[0] == "ds-model", names
    assert "aria_llm" not in names, names


def test_primary_all_restores_r_f93_primary(monkeypatch):
    # Legacy escape hatch — sovereign primary for ALL turns.
    names = _names(monkeypatch, primary_all=True)
    assert names[0] == "aria_llm", names


def test_shadow_aria_is_fallback_below_primary(monkeypatch):
    names = _names(monkeypatch, shadow=True)
    assert names[0] == "ds-model", names         # DeepSeek stays primary
    assert "aria_llm" in names, names            # ARIA-LLM still in the chain
    assert names.index("aria_llm") > 0, names    # ...but below the primary (canary)
