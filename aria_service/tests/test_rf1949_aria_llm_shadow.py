"""R-F1949 — ARIA_LLM_SHADOW must place ARIA-LLM BELOW the primary (safe canary),
while the default (no shadow) keeps the R-F93 behaviour of ARIA-LLM as primary."""
from __future__ import annotations

import pytest

from aria_service.llm import fallback


class _Stub:
    def __init__(self, name):
        self.name = name
        self.is_configured = True


_CLEAR = ("ARIA_LLM_URL", "ARIA_LLM_SHADOW", "ARIA_LLM_MODEL", "ARIA_LLM_KEY",
          "GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")


def _names(monkeypatch, shadow: bool):
    for v in _CLEAR:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ARIA_LLM_URL", "http://aria-llm/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-test")
    if shadow:
        monkeypatch.setenv("ARIA_LLM_SHADOW", "1")
    monkeypatch.setattr(fallback, "create_llm_provider",
                        lambda ptype, key, model="", base_url="": _Stub(model or ptype))
    chain = fallback.create_fallback_chain("deepseek", "dskey", "ds-model", "")
    provs = getattr(chain, "providers", None) or getattr(chain, "_providers", [])
    return [getattr(p, "name", "?") for p in provs]


def test_default_aria_is_primary(monkeypatch):
    names = _names(monkeypatch, shadow=False)
    assert names, "no providers built"
    assert names[0] == "aria_llm", names   # R-F93 default: primary


def test_shadow_aria_is_fallback_below_primary(monkeypatch):
    names = _names(monkeypatch, shadow=True)
    assert names[0] == "ds-model", names         # DeepSeek stays primary
    assert "aria_llm" in names, names            # ARIA-LLM still in the chain
    assert names.index("aria_llm") > 0, names    # ...but below the primary (canary)
