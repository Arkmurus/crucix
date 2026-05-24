"""R-F852 — operator chat `/code` command → ARIA-Coder bridge.

Gap (360, 2026-05-24): /api/aria/coder/request worked end-to-end but no chat
surface called it — "a user can ask ARIA to build/fix X" was unwired. R-F852
routes an operator `/code <desc>` (or `/coder ...`) chat message to the coder.

Security contract (operator direction):
  - OPERATOR-GATED, FAIL-CLOSED: only req.user_id == ARIA_CODER_OPERATOR_USER_ID
    may trigger; empty env var ⇒ bridge OFF for everyone.
  - ALWAYS STAGED: force_stage=True flows to operator_fix_request → fix_gap →
    _stage_or_deploy, so a chat-triggered diff is never auto-deployed (and the
    constitution stays blocked by R-F851 regardless).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aria_service.routes import aria as aria_routes
from aria_service.routes.aria import ChatRequest


def _request_with_coder(coder):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(aria_coder=coder)))


# ── operator gate (fail-closed) ───────────────────────────────────────────

def test_gate_fail_closed_when_env_unset(monkeypatch):
    monkeypatch.delenv("ARIA_CODER_OPERATOR_USER_ID", raising=False)
    req = ChatRequest(message="/code add a retry to the fetch", user_id="op123")
    assert aria_routes._is_coder_operator(req) is False


def test_gate_matches_operator_only(monkeypatch):
    monkeypatch.setenv("ARIA_CODER_OPERATOR_USER_ID", "op123")
    assert aria_routes._is_coder_operator(ChatRequest(message="x", user_id="op123")) is True
    assert aria_routes._is_coder_operator(ChatRequest(message="x", user_id="intruder")) is False
    assert aria_routes._is_coder_operator(ChatRequest(message="x", user_id="")) is False


# ── command parsing / dispatch ─────────────────────────────────────────────

def test_non_code_message_passes_through(monkeypatch):
    async def body():
        req = ChatRequest(message="what are the latest Gulf tenders?", user_id="op123")
        out = await aria_routes._maybe_handle_coder_command(req, _request_with_coder(MagicMock()))
        assert out is None  # normal chat proceeds
    asyncio.run(body())


def test_code_from_non_operator_denied(monkeypatch):
    monkeypatch.setenv("ARIA_CODER_OPERATOR_USER_ID", "op123")

    async def body():
        coder = MagicMock()
        coder.operator_fix_request = AsyncMock()
        req = ChatRequest(message="/code rewrite the auth module", user_id="intruder")
        out = await aria_routes._maybe_handle_coder_command(req, _request_with_coder(coder))
        assert out is not None and out.get("coder_denied") is True
        coder.operator_fix_request.assert_not_called()  # never triggered the coder

    asyncio.run(body())


def test_code_too_short_returns_help(monkeypatch):
    monkeypatch.setenv("ARIA_CODER_OPERATOR_USER_ID", "op123")

    async def body():
        req = ChatRequest(message="/code go", user_id="op123")
        out = await aria_routes._maybe_handle_coder_command(req, _request_with_coder(MagicMock()))
        assert out.get("coder_help") is True
    asyncio.run(body())


def test_code_when_coder_offline(monkeypatch):
    monkeypatch.setenv("ARIA_CODER_OPERATOR_USER_ID", "op123")

    async def body():
        req = ChatRequest(message="/code add a real fix to researcher.py", user_id="op123")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(aria_coder=None)))
        out = await aria_routes._maybe_handle_coder_command(req, request)
        assert out.get("coder_unavailable") is True
    asyncio.run(body())


# ── the capability: operator /code force-stages a queued fix ───────────────

def test_operator_code_queues_force_staged_fix(monkeypatch):
    """CAPABILITY: an operator /code command queues a coder fix AND passes
    force_stage=True so it can never auto-deploy."""
    monkeypatch.setenv("ARIA_CODER_OPERATOR_USER_ID", "op123")

    async def body():
        coder = MagicMock()
        coder.redis = None  # skip the queued-event publish path
        coder.operator_fix_request = AsyncMock(return_value=None)
        req = ChatRequest(
            message="/code add a 3-try backoff to the Brave fetch in researcher.py",
            user_id="op123",
        )
        out = await aria_routes._maybe_handle_coder_command(req, _request_with_coder(coder))
        assert out.get("coder_queued") is True
        assert out.get("fix_id")
        assert "staged for your review" in out.get("response", "").lower()
        # let the fire-and-forget background fix task run
        await asyncio.sleep(0.05)
        coder.operator_fix_request.assert_awaited_once()
        _, kwargs = coder.operator_fix_request.call_args
        assert kwargs.get("force_stage") is True, (
            "R-F852: chat-triggered coder requests MUST force_stage=True "
            "(never auto-deploy)"
        )

    asyncio.run(body())


# ── source guard: both chat paths call the bridge (§13 mirror) ─────────────

def test_both_chat_paths_call_bridge():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    # The bridge must be invoked in BOTH /chat and /chat/stream.
    assert src.count("_maybe_handle_coder_command(req, request)") >= 2, (
        "R-F852/§13 regression: the /code bridge must be mirrored into BOTH "
        "/chat and /chat/stream."
    )
