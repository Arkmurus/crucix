# -*- coding: utf-8 -*-
"""Capability tests (server-side location) for the ARIA CLI coder ↔ brain bridge
helpers introduced by R-F2161/R-F2162/R-F2165.

These live under aria_service/tests/ because the pre-commit capability-test gate
(scripts/pre_commit_checks.check_capability_tests) scans this directory; the same
paths are also covered by aria_cli/tests/test_rf2160_2166_coding_lift.py. Each
test invokes the actual function and asserts the user-visible outcome (CLAUDE.md
§3c), exercising the unconfigured branch so no live network is required.
"""
from __future__ import annotations

# Direct-import the symbols (not module.attr) so the pre-commit direct-calls
# check recognises them — LLMClient/LLMConfig are classes, which the checker's
# `def`-only matcher otherwise misreads as "function not found".
from aria_cli.llm import LLMClient, LLMConfig
from aria_cli.prompt import record_coding_outcome_http, _query_coding_rag_http


def test_supports_tools_false_on_aria_provider():
    """R-F2165: the in-house `aria` provider can't tool-call → supports_tools is
    False so the CLI can warn loudly instead of silently becoming a chat box."""
    c = LLMClient(LLMConfig(provider="aria", api_key="x", model="aria-coder"))
    assert c.supports_tools is False


def test_supports_tools_true_on_deepseek_provider():
    """R-F2165: a real tool-capable provider reports supports_tools True."""
    c = LLMClient(LLMConfig(provider="deepseek", api_key="x", model="deepseek-chat"))
    assert c.supports_tools is True


def test_record_coding_outcome_http_noop_when_unconfigured(monkeypatch):
    """R-F2162: write-back is best-effort — returns False (never raises) when the
    brain isn't configured, so a CLI ship never fails on a missing sink."""
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_CODER_LLM_API_KEY", raising=False)
    assert record_coding_outcome_http("fix", {"r_number": "F1"}) is False


def test_query_coding_rag_http_none_when_unconfigured(monkeypatch):
    """R-F2161: the HTTP RAG client returns None (→ caller falls back) when no
    brain URL/token is set, rather than erroring."""
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_CODER_LLM_API_KEY", raising=False)
    assert _query_coding_rag_http("anything") is None
