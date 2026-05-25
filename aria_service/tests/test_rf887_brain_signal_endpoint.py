"""R-F887 — /api/aria/brain/signal cross-tier sink (was a 404 black hole).

The only router is /api/aria, but the WA listener (aria_wa_listener.mjs:187,521)
POSTed to /api/brain/signal — 404 (live 2026-05-25 10:10:18). So WA group
messages + user feedback never reached the brain, and tier failures were
invisible to the coder. R-F887 adds POST /api/aria/brain/signal (failures →
capability_gaps, coder-visible via R-F884; content/feedback → brain_hook.absorb)
and repoints the WA listener.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import aria_service.main as m
from aria_service.routes import aria as a


def test_endpoint_registered_under_api_aria():
    paths = [getattr(r, "path", "") for r in m.app.routes]
    assert "/api/aria/brain/signal" in paths


def test_endpoint_routes_failure_vs_content():
    src = inspect.getsource(a.brain_signal_ep)
    # failure-type → capability_gaps (coder-visible); else → brain_hook.absorb
    assert "_is_failure" in src
    assert "capability_gaps" in src and "record_gap(" in src
    assert "brain_hook" in src and "absorb(" in src
    # the failure-type detection covers the WA failure signal_types
    assert "fail" in src and "timeout" in src


def test_wa_listener_repointed_and_emits_failure_signal():
    wa = (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8")
    # actual call sites use the correct /api/aria/brain/signal path
    assert "brainPost('/api/aria/brain/signal'" in wa
    assert "BRAIN_URL}/api/aria/brain/signal" in wa
    # no live call still points at the 404 path (comments may reference it)
    assert "brainPost('/api/brain/signal'" not in wa
    # read-document failure now emits a coder-visible cross-tier signal
    assert "wa_read_document_failed" in wa
