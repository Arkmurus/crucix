"""R-F2243 (CLI side) — a reachable-but-refusing brain must NOT trigger the slow
in-process RAG fallback.

When HTTP is CONFIGURED (base+token) but the brain returns non-200 (or errors),
_query_coding_rag_http now returns "" (skip RAG) rather than None. None triggers the
in-process chromadb path, which cold-loads the sentence-transformer for 100s+ on the
operator's Windows machine — the "aria not responding" startup hang. None is returned
ONLY when HTTP is not configured (server-side, where in-process actually has data).
"""
from __future__ import annotations

import httpx  # patched at module level; prompt.py does a local `import httpx` bound to this same module

import aria_cli.prompt as prompt


class _Resp:
    def __init__(self, status): self.status_code = status
    def json(self): return {}


def test_non200_returns_empty_not_none(monkeypatch):
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "svc-token")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(403))
    # 403 (the exact launch bug) → skip, NOT the slow fallback
    assert prompt._query_coding_rag_http("some task") == ""


def test_network_error_returns_empty_not_none(monkeypatch):
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "svc-token")
    def _boom(*a, **k): raise RuntimeError("connection reset")
    monkeypatch.setattr(httpx, "post", _boom)
    assert prompt._query_coding_rag_http("t") == ""


def test_not_configured_returns_none(monkeypatch):
    # no base/token → None so the SERVER-SIDE caller can try in-process (has data)
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_CODER_LLM_API_KEY", raising=False)
    assert prompt._query_coding_rag_http("t") is None
