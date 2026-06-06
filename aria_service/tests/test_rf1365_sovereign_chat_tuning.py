"""R-F1365 — 14B chat viability: trim context + fast DeepSeek failover.

The sovereign 14B is chain-primary (ARIA_LLM_URL set) but slow for live chat:
it must read every char of the 20000-char intelligence context before
generating, and a slow/stuck 14B burned the full 120s before failing over.
This caused 120s timeouts + lock contention (state_store lock 20s, brain_hook
circuit tripped) → degraded chat. Fix: when sovereign is active, trim the
context budget (default 6000) and cap the per-provider timeout (default 40s) so
a slow 14B fails over to the funded DeepSeek fallback fast.

These are gated on _compact_prompt_active(); the full cloud chain is unchanged.
"""
import importlib
import os

import pytest


def _fresh_engine():
    import aria_service.aria_engine as e
    return e


def test_compact_active_gates_on_aria_llm_url(monkeypatch):
    e = _fresh_engine()
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    monkeypatch.delenv("ARIA_LLM_COMPACT_PROMPT", raising=False)
    assert e._compact_prompt_active() is True
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    assert e._compact_prompt_active() is False


def test_sovereign_caps_completion_tokens(monkeypatch):
    e = _fresh_engine()
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    # R-F1360 sovereign output cap stays in force
    assert e._completion_max_tokens("hello") == 800
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    assert e._completion_max_tokens("hello") == 4000


def test_explicit_compact_flag_overrides(monkeypatch):
    e = _fresh_engine()
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    monkeypatch.setenv("ARIA_LLM_COMPACT_PROMPT", "1")
    assert e._compact_prompt_active() is True
    monkeypatch.setenv("ARIA_LLM_COMPACT_PROMPT", "0")
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    # explicit 0 wins even when the URL is set
    assert e._compact_prompt_active() is False
