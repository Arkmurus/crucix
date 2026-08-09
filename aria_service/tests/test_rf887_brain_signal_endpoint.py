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

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._app_probe import mounted_paths
from ._source_probe import function_source


def test_endpoint_registered_under_api_aria():
    # R-F3791 — NOT a flat walk of `m.app.routes`: include_router appends a lazy
    # wrapper rather than copying the child's routes up, so that walk returned only
    # the four FastAPI built-ins and this read as a 404 black hole reopening.
    assert "/api/aria/brain/signal" in mounted_paths(m.app)


def test_endpoint_routes_failure_vs_content():
    """R-F3811 — assert the ROUTING, not which function holds the sink call.

    This read `capability_gaps`/`record_gap(`/`brain_hook`/`absorb(` out of
    `brain_signal_ep`'s own source. Those calls were later extracted into
    `_route_one_signal` (its docstring says so) and dispatched as a background task,
    so the endpoint stopped containing the literals while the wiring stayed entirely
    intact — a §21a test failing on a refactor that changed nothing it cares about.

    The endpoint still owns the DECISION (`_is_failure` → which sink), so that is
    asserted here; the sinks are asserted where they now live.
    """
    src = function_source(a, "brain_signal_ep")
    assert "_is_failure" in src
    # the failure-type detection covers the WA failure signal_types
    assert "fail" in src and "timeout" in src
    # the endpoint reports which way it routed — the contract aria-wa depends on
    assert '"capability_gap" if _is_failure else "brain_absorb"' in src

    # Both sinks, in the helper the dispatch now targets.
    routed = function_source(a, "_route_one_signal")
    assert "capability_gaps" in routed and "record_gap(" in routed
    assert "brain_hook" in routed and "absorb(" in routed


def test_a_failure_signal_reaches_capability_gaps_and_content_reaches_the_brain():
    """CAPABILITY (§3c) — drive the real routing helper and observe the sink.

    Source text proves a call is written; this proves it FIRES, and it is immune to
    the next relocation. R-F3811 added it because the source-text version above had
    been red for a refactor while the behaviour was correct the whole time.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from aria_service.intel import brain_hook, capability_gaps

    with patch.object(capability_gaps, "record_gap", AsyncMock()) as gap, \
         patch.object(brain_hook, "absorb", AsyncMock()) as absorb:
        asyncio.run(a._route_one_signal(
            "the WA send timed out", "aria-wa", "wa_chat_failed", {}))
        assert gap.await_count >= 1, "a failure signal must reach capability_gaps"

        gap.reset_mock(); absorb.reset_mock()
        asyncio.run(a._route_one_signal(
            "a customer said the report was useful", "aria-web",
            "whatsapp_group_message", {}))
        assert absorb.await_count >= 1, "a content signal must reach brain_hook.absorb"


def test_wa_listener_repointed_and_emits_failure_signal():
    wa = (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8")
    # actual call sites use the correct /api/aria/brain/signal path
    assert "brainPost('/api/aria/brain/signal'" in wa

    # R-F3811 — the URL is COMPOSED, not written out. This asserted the literal
    # "BRAIN_URL}/api/aria/brain/signal", i.e. the pre-refactor
    # `${BRAIN_URL}/api/aria/brain/signal` template at each call site. Those sites
    # now go through `brainFetch`/`brainPost`, which build `${BRAIN_URL}${path}`
    # once (aria_wa_listener.mjs:242) — strictly better, and it removed the literal.
    # Measured 2026-08-09: 13 `brainPost('/api/aria/brain/signal'` call sites and
    # zero on the old 404 path, so the wiring was never broken.
    #
    # Assert the composition rule instead of a spelling that a refactor can delete.
    assert "${BRAIN_URL}${path}" in wa, (
        "the brain helper must compose its URL from BRAIN_URL + the caller's path"
    )
    # no live call still points at the 404 path (comments may reference it)
    assert "brainPost('/api/brain/signal'" not in wa
    # read-document failure now emits a coder-visible cross-tier signal
    assert "wa_read_document_failed" in wa
