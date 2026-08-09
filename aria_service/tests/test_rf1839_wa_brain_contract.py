"""R-F1839 — cross-service contract test: WhatsApp listener → brain.

Each service was tested in isolation, so the SEAM between them could (and did)
break silently: the WA listener once POSTed to `/api/brain/signal` and got a
404 (live 2026-05-25), so WA-tier chat failures were invisible to the coder —
exactly the kind of cross-service drift unit tests miss.

This test pins BOTH sides of the seam in one place:
  * Node side  — the WA listener source must POST to `/api/aria/brain/signal`
                 (the path the brain actually serves), never the old 404 path.
  * Python side — the brain endpoint must accept the listener's exact payload
                 shape {content, source, signal_type, metadata} AND route a
                 failure-type signal (wa_chat_failed) to capability_gaps, so the
                 coder can SEE WA failures (the §21/§25 promise).

If either side drifts, this fails — which is the point of a contract test.
Runs against the real brain via FastAPI TestClient (no live infra, CI-safe);
no lifespan context manager (avoids the known boot-time hang — see conftest).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from aria_service.main import app
from aria_service.intel import capability_gaps

client = TestClient(app)
AUTH_TOKEN = "rf1839-contract-token"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WA_LISTENER = REPO_ROOT / "services" / "wa-listener" / "aria_wa_listener.mjs"

# The exact payload signalChatFailure() sends (aria_wa_listener.mjs:861).
WA_CHAT_FAILED_PAYLOAD = {
    "content": 'WA chat failed (async: boom). User asked: "screen Acme Corp"',
    "source": "aria-wa",
    "signal_type": "wa_chat_failed",
    "metadata": {"sender": "27820000000@s.whatsapp.net", "error": "async: boom"},
}


# ── Node side of the seam ─────────────────────────────────────────────────────

def test_wa_listener_targets_the_path_the_brain_serves():
    src = WA_LISTENER.read_text(encoding="utf-8")
    assert "/api/aria/brain/signal" in src, "listener must POST the path the brain serves"
    # The old 404 path must never come back (the live 2026-05-25 regression).
    assert not re.search(r"['\"`]/api/brain/signal['\"`]", src), \
        "listener must NOT use the old /api/brain/signal (404)"


# ── Python side of the seam ───────────────────────────────────────────────────

def test_brain_accepts_wa_failure_payload_and_routes_to_capability_gap(monkeypatch):
    """The listener's wa_chat_failed payload must be accepted AND routed to
    capability_gaps so the coder sees it (not silently absorbed/dropped)."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", AUTH_TOKEN)
    spy = AsyncMock()
    with patch.object(capability_gaps, "record_gap", spy):
        resp = client.post(
            "/api/aria/brain/signal",
            json=WA_CHAT_FAILED_PAYLOAD,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["routed"] == "capability_gap", "WA failure must route to a coder-visible gap"
    # Background task ran → the gap was actually recorded with the failure shape.
    assert spy.await_count >= 1, "record_gap must be invoked for a WA failure signal"

    # R-F3811 — search the call LIST, don't read `await_args`.
    #
    # `record_gap` is patched on the module, so the spy catches EVERY caller in the
    # request, not just this one. `await_args` is the LAST await, and an unrelated
    # background writer (observed: gap_type="data_integrity") lands after the WA gap,
    # so the assertion compared this contract against somebody else's call and failed
    # with a confusing "data_integrity != operational:output_rejection".
    #
    # This is order-dependent by construction, which is why it passed in the
    # 2026-08-08 full-suite ordering and fails standalone. Asserting "one of the
    # recorded gaps has this shape" is the property actually being claimed, and it
    # cannot be broken by an unrelated caller arriving later.
    matches = [
        c.kwargs for c in spy.await_args_list
        if c.kwargs.get("gap_type") == "operational:output_rejection"
        and "aria-wa" in (c.kwargs.get("source") or "")
    ]
    assert matches, (
        "no record_gap call carried the WA failure shape; got "
        f"{[c.kwargs.get('gap_type') for c in spy.await_args_list]}"
    )
    assert any("wa_chat_failed" in (kw.get("detail") or "") for kw in matches), (
        "the WA gap must name the originating signal type in its detail"
    )


def test_brain_routes_non_failure_signal_to_absorb_not_gap(monkeypatch):
    """A normal (non-failure) signal must NOT be misrouted to a capability gap."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", AUTH_TOKEN)
    spy = AsyncMock()
    with patch.object(capability_gaps, "record_gap", spy), \
         patch("aria_service.intel.brain_hook.absorb", AsyncMock()):
        resp = client.post(
            "/api/aria/brain/signal",
            json={
                "content": "routine heartbeat from wa",
                "source": "aria-wa",
                "signal_type": "wa_status",
                "metadata": {},
            },
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["routed"] == "brain_absorb"
    # A non-failure signal must not be recorded as a gap.
    assert spy.await_count == 0


def test_brain_signal_skips_empty_content_without_false_acceptance(monkeypatch):
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", AUTH_TOKEN)
    resp = client.post(
        "/api/aria/brain/signal",
        json={"source": "aria-wa", "signal_type": "wa_chat_failed", "metadata": {}},
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "accepted": False,
        "skipped": "empty_content",
        "source": "aria-wa",
    }


@pytest.mark.parametrize("sig_type", ["wa_chat_failed", "send_failed", "ocr_error", "dd_timeout"])
def test_failure_taxonomy_all_route_to_gap(sig_type, monkeypatch):
    """Every failure-ish signal_type the surfaces emit must reach the coder."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", AUTH_TOKEN)
    spy = AsyncMock()
    with patch.object(capability_gaps, "record_gap", spy):
        resp = client.post(
            "/api/aria/brain/signal",
            json={
                "content": f"test {sig_type}", "source": "aria-wa",
                "signal_type": sig_type, "metadata": {},
            },
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["routed"] == "capability_gap", f"{sig_type} must be coder-visible"
