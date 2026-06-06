"""R-F1363 — aria_llm provider must clamp max_tokens to the model window.

Bug: the self-coder requested max_tokens=8192 against a vLLM served with
--max-model-len 8192; prompt(39)+completion(8192)=8231 > 8192 → vLLM HTTP 400
("maximum context length is 8192 … you requested 8231"), which soft-cooled the
aria_llm provider and failed every self-coding fix at the plan step.

Capability test: drive the real complete() with a monkeypatched httpx, capture
the body actually POSTed, and assert max_tokens was clamped so
prompt_estimate + max_tokens < the window.
"""
import os

import pytest

from aria_service.llm import aria_llm_provider as P


class _FakeResp:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"content": "ok"}}],
            "model": "aria-llm-v0.2",
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }


class _FakeClient:
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *, headers=None, json=None):
        _FakeClient.captured = json
        return _FakeResp()


@pytest.mark.asyncio
async def test_max_tokens_clamped_to_window(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "8192")  # the live bug window
    # httpx is imported lazily inside complete(); patch the real module
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient)

    out = await P.complete(
        "Write a function." * 50,          # ~ small prompt
        system="You are a coder.",
        max_tokens=8192,                   # would overflow 8192 window
    )
    assert out["ok"] is True
    sent = _FakeClient.captured["max_tokens"]
    # must be strictly less than the window, leaving room for the prompt
    assert sent < 8192
    assert sent >= 256


@pytest.mark.asyncio
async def test_no_clamp_when_room_available(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")  # the new 14B window
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient)

    out = await P.complete("short prompt", system="", max_tokens=8192)
    assert out["ok"] is True
    # 8192 fits easily in a 32768 window → unchanged
    assert _FakeClient.captured["max_tokens"] == 8192


def test_max_model_len_default_and_override(monkeypatch):
    monkeypatch.delenv("ARIA_LLM_MAX_MODEL_LEN", raising=False)
    assert P._max_model_len() == 32768
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "8192")
    assert P._max_model_len() == 8192
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "garbage")
    assert P._max_model_len() == 32768  # safe fallback
