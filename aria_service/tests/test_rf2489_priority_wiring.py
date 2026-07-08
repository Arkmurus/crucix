"""R-F2489 — capability test for §21a priority-module brain wiring.

Proves that the newly-wired modules reach ARIA's brain on BOTH branches:
a driven failure path emits ``wire_failure`` and a driven success path emits
``wire_success``. Per CLAUDE.md §21d a wiring change is only real when a test
emits the signal and asserts it lands — so this drives the ACTUAL entry points
(not a helper) and captures the signal at each module's own imported name.

Covers 3+ wired modules across the reasoning/ingestion/DeepSeek surfaces:
  - llm.factory        (sync factory: unknown provider -> wire_failure;
                        known provider -> wire_success)
  - llm.local_llm      (async: OLLAMA_URL unset soft-failure -> wire_failure)
  - learning.deepseek_clients (async: no API key -> wire_failure)

The modules do ``from ..intel.engine_wiring import wire_success, wire_failure``
at load, binding the names into their OWN namespace — so we monkeypatch the
name ON EACH MODULE, not on engine_wiring, to intercept the call.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.llm import factory as _factory
from aria_service.llm import local_llm as _local_llm
from aria_service.learning import deepseek_clients as _dsc


def _spy(sink: list):
    def _capture(**kwargs):
        sink.append(kwargs)
    return _capture


def test_factory_unknown_provider_wires_failure(monkeypatch):
    calls: list = []
    monkeypatch.setattr(_factory, "wire_failure", _spy(calls))
    # Also silence success so an unrelated success can't confuse the assert.
    monkeypatch.setattr(_factory, "wire_success", _spy([]))

    prov = _factory.create_llm_provider("totally_unknown_provider_xyz")

    assert prov is None
    assert calls, "unknown-provider (soft None return) must emit wire_failure"
    assert any(c.get("module") == "llm_factory" for c in calls)


def test_factory_known_provider_wires_success(monkeypatch):
    succ: list = []
    monkeypatch.setattr(_factory, "wire_success", _spy(succ))
    monkeypatch.setattr(_factory, "wire_failure", _spy([]))

    prov = _factory.create_llm_provider("deepseek", api_key="test-key")

    assert prov is not None
    assert succ, "a created provider must emit wire_success"
    assert any(c.get("module") == "llm_factory" for c in succ)


def test_local_llm_unconfigured_wires_failure(monkeypatch):
    # OLLAMA_URL unset -> _complete_impl returns {"ok": False, ...} (no raise);
    # the wired wrapper must surface that soft-failure to the brain.
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    calls: list = []
    monkeypatch.setattr(_local_llm, "wire_failure", _spy(calls))
    monkeypatch.setattr(_local_llm, "wire_success", _spy([]))

    result = asyncio.run(_local_llm.complete("hello"))

    assert result["ok"] is False
    assert calls, "local_llm soft-failure must emit wire_failure"
    assert any(c.get("module") == "local_llm" for c in calls)


def test_deepseek_clients_no_key_wires_failure(monkeypatch):
    # No API key -> _chat raises RuntimeError before any network call; the
    # generator swallows it to [] but must now first reach the brain.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ARIA_DEEPSEEK_API_KEY", raising=False)
    calls: list = []
    monkeypatch.setattr(_dsc, "wire_failure", _spy(calls))
    monkeypatch.setattr(_dsc, "wire_success", _spy([]))

    gen = _dsc.DeepSeekQuestionGenerator()
    out = asyncio.run(gen.generate_questions("sanctions evasion", 3))

    assert out == []
    assert calls, "deepseek question-gen failure must emit wire_failure"
    assert any(c.get("module") == "deepseek_clients" for c in calls)
