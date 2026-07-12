"""R-F2567 — /api/aria/coder/llm must accept the INTERNAL token.

Bug: coder/llm (a COMPUTE endpoint the autonomous coder calls for reproduce/plan/
code/heal) was matched by _OPERATOR_ONLY_RE and required the OPERATOR token. The coder
holds only ARIA_INTERNAL_TOKEN → 403 on every LLM call → self-coding loop fixed=0 (§21c P0).

Capability test drives the REAL require_aria_token dependency:
  - internal token → /coder/llm: NOT blocked (the fix)
  - internal token → control/destructive paths: STILL 403 (guardrail intact)
  - internal token → coder/llm-sensitive: STILL 403 (precision — not over-exempted)
"""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from aria_service.routes import aria as A


def _req(path: str, token: str, method: str = "POST"):
    return types.SimpleNamespace(
        url=types.SimpleNamespace(path=path),
        headers={"Authorization": f"Bearer {token}"},
        method=method,
    )


def _auth(monkeypatch, path: str, token: str, method: str = "POST"):
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-tok")
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", "operator-tok")
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)   # default = scoping ON
    return A.require_aria_token(_req(path, token, method))


# ── The fix: coder/llm is reachable with the internal token ──────────────────
def test_coder_llm_accepts_internal_token(monkeypatch):
    _auth(monkeypatch, "/api/aria/coder/llm", "internal-tok")   # must NOT raise


def test_coder_rag_still_accepts_internal_token(monkeypatch):
    _auth(monkeypatch, "/api/aria/coder/rag/query", "internal-tok")  # existing exemption


# ── The guardrail still blocks control/destructive paths with the internal token ─
@pytest.mark.parametrize("path", [
    "/api/aria/autonomous/pause",
    "/api/aria/self/deploy",
    "/api/aria/coder/request",        # a non-llm coder control path stays gated
    "/api/aria/dd/admin/reset",
    "/api/aria/coder/llm-sensitive",  # PRECISION: must NOT exempt this
    "/api/aria/coder/llm/steal",      # PRECISION (security Pass-2): sub-paths stay gated
])
def test_control_paths_still_operator_only(monkeypatch, path):
    with pytest.raises(HTTPException) as ei:
        _auth(monkeypatch, path, "internal-tok")
    assert ei.value.status_code == 403


def test_coder_llm_also_accepts_operator_token(monkeypatch):
    _auth(monkeypatch, "/api/aria/coder/llm", "operator-tok")   # operator still works


# ── Direct regex assertion (the surface the fix changed) ─────────────────────
def test_operator_only_regex_exempts_coder_llm_only():
    R = A._OPERATOR_ONLY_RE
    assert R.search("/api/aria/coder/llm") is None            # exempted
    assert R.search("/api/aria/coder/rag/query") is None      # exempted
    assert R.search("/api/aria/coder/request") is not None    # gated
    assert R.search("/api/aria/coder/scoreboard") is not None # gated (coder reads via redis)
    assert R.search("/api/aria/coder/gaps") is not None       # gated
    assert R.search("/api/aria/coder/llm-sensitive") is not None  # gated (precision)
    assert R.search("/api/aria/coder/llmx") is not None       # gated (precision)
    assert R.search("/api/aria/coder/llm/steal") is not None  # gated — sub-paths NOT exempt


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
