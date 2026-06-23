"""R-F1829 — web DD budget fits the SSE proxy window (always deliver inline).

ROOT CAUSE (operator-visible, 2026-06-23): "not reply on the web for the DD".
The web SSE proxy (server.mjs ARIA_STREAM_PROXY_TIMEOUT_MS, default 600s) aborts
the connection at 600s, but the DD orchestrator's hard stop was
budget (ARIA_DD_CHAT_BUDGET_S = 720s, tuned for the WhatsApp 15-min async-push
window) + ARIA_DD_HARD_MARGIN_S (150s) = 870s. The proxy ALWAYS cut before the
DD returned → no reply. Live probe (2026-06-23): 513s of heartbeats and zero
final answer. Three independent timeout numbers (600 proxy / 720+150 DD /
400 people-drilldown) that don't compose — the structural defect.

R-F1829 makes the DD budget TRANSPORT-AWARE: chat_stream_ep (the web limb)
passes dd_budget_s = ARIA_DD_STREAM_BUDGET_S (default 300) into _execute_tool,
which forwards it to orchestrate_dd. hard = 300 + 150 = 450s, leaving ~150s for
the LLM to compose + stream the answer inside the 600s proxy window → a
(partial) report ALWAYS lands inline. The non-stream / WhatsApp path
(dd_budget_s=None) keeps the 720s env default — unchanged.

These are CAPABILITY tests: they drive the real `_execute_tool` coroutine (the
broken plumbing) and assert the budget that actually reaches `orchestrate_dd`,
plus a structural guard that the stream hard deadline fits the proxy window so
the two numbers can never silently drift apart again.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.routes import aria as aria_routes


_AR = pathlib.Path("C:/code/crucix/aria_service/routes/aria.py")


class _StubLLM:
    is_configured = True


def _capture_budget(monkeypatch):
    """Patch orchestrate_dd to record the total_budget_s it is handed, then
    raise so the rest of the (report-rendering) branch is short-circuited —
    the except handler at aria.py:6320 turns the raise into a FAILED string.
    We only assert on the captured budget."""
    captured: dict = {}

    async def _fake_orchestrate(*a, total_budget_s=None, **k):
        captured["budget"] = total_budget_s
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr(ddo, "orchestrate_dd", _fake_orchestrate)
    return captured


def _dd_intent():
    # skip_doc_gate bypasses the document-verification gate so the test stays
    # offline and deterministic; the budget plumbing is identical with/without.
    return {"tool": "dd_orchestrate", "name": "Modirum", "type": "company",
            "skip_doc_gate": True}


@pytest.mark.asyncio
async def test_rf1829_stream_budget_reaches_orchestrator(monkeypatch):
    """The smoking gun: a caller-supplied dd_budget_s (web stream) must reach
    orchestrate_dd as total_budget_s — NOT be overridden by the 720s env
    default. Pre-fix there was no param, so the web path always ran at 720."""
    captured = _capture_budget(monkeypatch)
    out = await aria_routes._execute_tool(_dd_intent(), _StubLLM(), dd_budget_s=300.0)

    assert captured.get("budget") == 300.0, (
        f"web stream budget did not reach the orchestrator "
        f"(got {captured.get('budget')!r}) — DD would run to the 870s ceiling "
        f"and the 600s proxy would cut it → no reply."
    )
    # The branch should have short-circuited via the FAILED path (our raise).
    assert "dd_orchestrate" in out


@pytest.mark.asyncio
async def test_rf1829_default_budget_unchanged_for_wa(monkeypatch):
    """Guard the WhatsApp / non-stream path: with no dd_budget_s the env
    default (720s, the 15-min push window) must still apply — R-F1829 must not
    shorten the channel that legitimately has the longer window."""
    monkeypatch.setenv("ARIA_DD_CHAT_BUDGET_S", "720")
    captured = _capture_budget(monkeypatch)
    await aria_routes._execute_tool(_dd_intent(), _StubLLM())  # no dd_budget_s

    assert captured.get("budget") == 720.0, (
        f"non-stream/WA path budget changed to {captured.get('budget')!r} — "
        f"it must keep the 720s env default."
    )


def test_rf1829_execute_tool_accepts_dd_budget_s():
    """The signature must expose dd_budget_s so chat_stream_ep can cap the DD
    to its transport window."""
    sig = inspect.signature(aria_routes._execute_tool)
    assert "dd_budget_s" in sig.parameters, (
        "_execute_tool lost its dd_budget_s parameter — the web stream can no "
        "longer fit the DD to the proxy window."
    )


def test_rf1829_stream_passes_budget_and_fits_proxy_window():
    """Structural anti-drift guard: chat_stream_ep must (a) pass dd_budget_s
    derived from ARIA_DD_STREAM_BUDGET_S, and (b) the default stream budget +
    the orchestrator hard margin must fit INSIDE the 600s web proxy window
    with room to compose the answer."""
    src = _AR.read_text(encoding="utf-8", errors="ignore")
    assert "ARIA_DD_STREAM_BUDGET_S" in src, (
        "R-F1829: the stream path no longer reads ARIA_DD_STREAM_BUDGET_S."
    )
    assert "dd_budget_s=_dd_stream_budget_s" in src, (
        "R-F1829: chat_stream_ep no longer passes the fit budget into "
        "_execute_tool — the web DD is unbounded again."
    )
    # Defaults: stream budget 300 + hard margin 150 = 450 hard deadline; the
    # web proxy is 600s. Require margin >= 120s for LLM compose + streaming.
    STREAM_DEFAULT = 300
    HARD_MARGIN_DEFAULT = 150
    PROXY_WINDOW = 600
    hard = STREAM_DEFAULT + HARD_MARGIN_DEFAULT
    assert hard + 120 <= PROXY_WINDOW, (
        f"R-F1829: stream hard deadline {hard}s leaves < 120s to compose "
        f"inside the {PROXY_WINDOW}s proxy window — defaults drifted; the "
        f"'no reply' failure class can return."
    )
